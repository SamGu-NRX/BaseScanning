"""Immutable correction sequences and result versions.

Each slot contains its entire record, inserted with put_if_absent. There is no
reserved slot, mutable head pointer, or separately deletable pending marker.
Readers stop at the first empty slot. Competing writers retry that same slot,
so a crash cannot leave a hole hiding later records.

Capture records also cover answers accepted before a run exists. A run copies
that prefix into its own immutable sequence before acknowledging an answer or
composing. A crash during the copy is recovered by the next snapshot read.
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
            if prior != correction:
                raise ValueError("confirmation ID was reused with different correction data")
            return sequence
        sequence += 1


def record_correction(state: Any, capture_id: str, correction: dict, run: RunRecord | None = None) -> int:
    sequence = _append_correction(state, f"capture_correction:{capture_id}", correction)
    if run is not None:
        correction_snapshot(state, run)
    return sequence


def correction_snapshot(state: Any, run: RunRecord) -> list[dict]:
    rows = _records(state, f"capture_correction:{run.capture_id}")
    for sequence, row in enumerate(rows, 1):
        key = f"run_correction:{run.run_id}:{sequence}"
        if not state.put_if_absent(key, row) and state.get_kv(key) != row:
            raise RuntimeError(f"correction sequence mismatch for {run.run_id} at {sequence}")
    return rows


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
    path = work / "verdicts/correction-inputs.json"
    reads_path = work / "reads/reads.json"
    if stage == "criteria":
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
        if ids[: len(previous)] != previous or len(ids) <= len(previous):
            # Another writer may have appended beyond the slot just inspected.
            return latest_publication(state, run_id) or prior
        sequence += 1


def pending_corrections(state: Any, run: RunRecord) -> bool:
    corrections = correction_snapshot(state, run)
    published = latest_publication(state, run.run_id)
    covered = published["correctionIds"] if published else []
    return len(corrections) > len(covered)
