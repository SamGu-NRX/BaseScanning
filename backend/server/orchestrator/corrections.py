"""Immutable correction sequences and result versions.

Each slot contains its entire record, inserted with put_if_absent. There is no
reserved slot, mutable head pointer, or separately deletable pending marker.
Readers stop at the first empty slot. Competing writers retry that same slot,
so a crash cannot leave a hole hiding later records.

Capture records freeze each answer's target before acknowledgment. Run sequences
contain only answers for that run. Answers without a target are claimed by the
next run at creation, never by a historical run checking its pending work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.contracts.jobs import RunRecord
from server.reads.policy import confirm
from server.reads.types import Reads


def _records(state: Any, prefix: str) -> list[dict]:
    rows = []
    while (row := state.get_kv(f"{prefix}:{len(rows) + 1}")) is not None:
        rows.append(row)
    return rows


def _append_correction(state: Any, prefix: str, correction: dict) -> int:
    sequence = 1
    while True:
        key = f"{prefix}:{sequence}"
        prior = state.get_kv(key)
        if prior is None:
            if state.put_if_absent(key, correction):
                return sequence
            continue
        if prior["confirmationId"] == correction["confirmationId"]:
            # A retry must keep its original target even if a newer run exists.
            if {k: v for k, v in prior.items() if k not in {"targetRunId", "legacy"}} != {
                k: v for k, v in correction.items() if k not in {"targetRunId", "legacy"}
            }:
                raise ValueError("confirmation ID was reused with different correction data")
            return sequence
        sequence += 1


def newest_correction_run(state: Any, capture_id: str) -> RunRecord | None:
    runs = state.runs_for_capture(capture_id)
    newest = max(runs, key=lambda item: (item.created_at, item.run_id)) if runs else None
    # Do not fall back to an older delivered run when the newest one failed.
    return newest if newest is not None and newest.status in {"running", "done"} else None


def reserve_correction_target(state: Any, capture_id: str, identifier: str, run: RunRecord | None) -> dict:
    key = f"correction_target:{capture_id}:{identifier}"
    state.put_if_absent(key, {"runId": run.run_id if run else None})
    return state.get_kv(key)


def record_correction(state: Any, capture_id: str, correction: dict, run: RunRecord | None = None) -> int:
    target = reserve_correction_target(state, capture_id, correction["confirmationId"], run)
    sequence = _append_correction(
        state, f"capture_correction:{capture_id}", {**correction, "targetRunId": target["runId"]}
    )
    accepted = state.get_kv(f"capture_correction:{capture_id}:{sequence}")
    target = state.get_run(accepted["targetRunId"]) if accepted.get("targetRunId") else None
    if run is not None and accepted.get("targetRunId") == run.run_id:
        target = run
    if target is not None:
        correction_snapshot(state, target)
    return sequence


def seed_run_corrections(state: Any, run: RunRecord) -> None:
    """Freeze pre-run answers before publishing a newly created run record."""
    import_legacy_corrections(state, run.capture_id)
    rows = _records(state, f"capture_correction:{run.capture_id}")
    state.put_if_absent(
        f"run_correction_seed:{run.run_id}",
        {"confirmationIds": [row["confirmationId"] for row in rows if not row.get("targetRunId")]},
    )
    _claim_pre_run_answers(state, run)


def _claim_pre_run_answers(state: Any, run: RunRecord) -> None:
    seed = state.get_kv(f"run_correction_seed:{run.run_id}") or {}
    for identifier in seed.get("confirmationIds", []):
        state.put_if_absent(f"pre_run_owner:{run.capture_id}:{identifier}", {"runId": run.run_id})


def correction_snapshot(state: Any, run: RunRecord) -> list[dict]:
    # Finish an interrupted creation-time claim, but never adopt later answers.
    _claim_pre_run_answers(state, run)
    for row in _records(state, f"capture_correction:{run.capture_id}"):
        target = row.get("targetRunId")
        if target is None:
            owner = state.get_kv(f"pre_run_owner:{run.capture_id}:{row['confirmationId']}") or {}
            target = owner.get("runId")
        if target == run.run_id:
            answer = {key: value for key, value in row.items() if key not in {"targetRunId", "legacy"}}
            _append_correction(state, f"run_correction:{run.run_id}", answer)
    return _records(state, f"run_correction:{run.run_id}")


def import_legacy_corrections(state: Any, capture_id: str, run: RunRecord | None = None) -> None:
    """Preserve answers acknowledged by old API workers before reads existed."""
    from server.reads.pending import pending_slots

    for row in sorted(pending_slots(state, capture_id), key=lambda row: row.get("seq", 0)):
        required = ("confirmationId", "fieldId", "imageId", "kind", "by")
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"legacy confirmation is missing required fields: {', '.join(missing)}")
        answer = {key: row[key] for key in required}
        answer["value"] = row.get("value")
        target = state.get_kv(f"correction_target:{capture_id}:{row['confirmationId']}")
        metadata = {"targetRunId": target["runId"]} if target is not None else {"targetRunId": None, "legacy": True}
        _append_correction(state, f"capture_correction:{capture_id}", {**answer, **metadata})
    if run is None or run.status not in {"pending", "running"}:
        return
    runs = state.runs_for_capture(capture_id)
    newest = max(runs, key=lambda item: (item.created_at, item.run_id)) if runs else None
    if newest is None or newest.run_id != run.run_id:
        return
    # An old worker may write its slot after creation but before the reads stage.
    # Adopt that legacy answer into this active run, never a delivered predecessor.
    # The immutable marker also recovers an import interrupted before this claim.
    for row in _records(state, f"capture_correction:{capture_id}"):
        if row.get("legacy") and not row.get("targetRunId"):
            state.put_if_absent(f"pre_run_owner:{capture_id}:{row['confirmationId']}", {"runId": run.run_id})


def fold_reads(base: Reads, corrections: list[dict]) -> Reads:
    """Replay accepted answers in sequence; the latest answer wins per field.

    Authorization and device conflict checks belong to acceptance. Replaying an
    accepted answer must not conflict with locks left by an earlier answer or by
    a legacy reads artifact that already contains that answer.
    """
    reads = base.model_copy(deep=True)
    for row in corrections:
        field = row["fieldId"]
        if reads.confirm is not None:
            reads.confirm.locks.pop(field, None)
        reads = confirm(
            reads,
            still_id=row["imageId"],
            choice=row.get("value") if row["kind"] == "confirmed" else None,
            typed=row.get("value") if row["kind"] == "corrected" else None,
            by=row["by"],
            field_id=field,
        )
    return reads


def prepare_inputs(state: Any, run: RunRecord, stage: str, work: Path) -> Path:
    """Criteria and result must consume the same immutable reads snapshot."""
    path = work / "correction-inputs.json"
    reads_path = work / "reads/reads.json"
    hydrated = work / "verdicts/correction-inputs.json"
    if stage == "result" and hydrated.is_file():
        path.write_bytes(hydrated.read_bytes())
    if stage == "criteria":
        import_legacy_corrections(state, run.capture_id, run)
        rows = correction_snapshot(state, run)
        raw = json.loads(reads_path.read_text()) if reads_path.is_file() else None
        if rows:
            if raw is None:
                raise RuntimeError("correction composition has no base reads artifact")
            raw = fold_reads(Reads.model_validate(raw), rows).model_dump(mode="json")
        snapshot = {"correctionIds": [row["confirmationId"] for row in rows], "reads": raw}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot))
    elif path.is_file():
        snapshot = json.loads(path.read_text())
    else:
        # Legacy criteria did not record a snapshot. Publish coverage zero, then
        # the coordinator sees uncovered answers and rebuilds both stages.
        snapshot = {"correctionIds": [], "reads": None}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot))
    if snapshot["reads"] is not None:
        reads_path.parent.mkdir(parents=True, exist_ok=True)
        reads_path.write_text(json.dumps(snapshot["reads"]))
    return path


def latest_publication(state: Any, run_id: str) -> dict | None:
    versions = _records(state, f"result_version:{run_id}")
    return versions[-1] if versions else None


def publish_version(state: Any, run_id: str, publication: dict) -> dict:
    """Append only if this composition covers every previously published answer.

    All snapshots are prefixes of one immutable sequence. Comparing their IDs
    rejects a stale composer even if it deletes a successor's advisory lease.
    Artifact bytes must already exist at this composition's unique storage keys.
    """
    sequence = 1
    ids = publication["correctionIds"]
    while True:
        key = f"result_version:{run_id}:{sequence}"
        prior = state.get_kv(key)
        if prior is None:
            # Preserve historical final events, but identify an older emitter
            # whose append arrives after this publication. This watermark is
            # internal metadata, not an extra field in the device event.
            event_floor = 0
            if publication.get("captureId"):
                while page := state.events_after(publication["captureId"], event_floor):
                    event_floor = page[-1].seq
            candidate = {**publication, "version": sequence, "afterEventSeq": event_floor}
            if state.put_if_absent(key, candidate):
                return candidate
            continue
        previous = prior["correctionIds"]
        if ids[: len(previous)] != previous:
            # Another writer may have appended beyond the slot just inspected.
            return latest_publication(state, run_id) or prior
        if all(
            prior.get(key) == value for key, value in publication.items() if key not in {"version", "afterEventSeq"}
        ):
            return latest_publication(state, run_id) or prior
        # Equal coverage is a superset too. Upstream verdicts can change without
        # a new correction; their recomposition must still publish a new result.
        sequence += 1


def pending_corrections(state: Any, run: RunRecord) -> bool:
    corrections = correction_snapshot(state, run)
    published = latest_publication(state, run.run_id)
    covered = published["correctionIds"] if published else []
    return len(corrections) > len(covered)
