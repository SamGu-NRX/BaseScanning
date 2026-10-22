"""SD-23 entry points. WS-01's hooks bind to these functions."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from server.common.lease import LeaseGuard
from server.common.logging import log_event
from server.contracts.api import ConfirmationRequest, StageData
from server.contracts.jobs import (
    CommittedFile,
    RunRecord,
    StageRecord,
)
from server.contracts.packet import Tier
from server.orchestrator.clock import parse_iso
from server.orchestrator.coordinator import (
    RunCoordinator,
    pending_correction_key,
    reset_criteria_and_result,
    with_correction_lease,
)
from server.orchestrator.early.capture_check import final_check, flush_pending
from server.orchestrator.early.dispatch import dispatch
from server.orchestrator.early.index import early_keys
from server.orchestrator.events import emit_stage
from server.orchestrator.executor import CoordinatorDied
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.prewarm import request_prewarm
from server.orchestrator.services import branch_of, current, deadline_from, run_id_for
from server.orchestrator.services import packet_sha256 as digest_of
from server.orchestrator.stateio import get_json, put_json
from server.orchestrator.sweeper import coordinator_is_dead
from server.orchestrator.sweeper import sweep as sweep_runs

_READ_PURPOSES = {"meter_close", "meter_oblique", "panel_close", "panel_open", "main_disconnect"}
# A claim with no run record is abandoned. A later finalize or the sweeper takes it.
CLAIM_LEASE_S = 30.0


def start_run(
    capture_id: str,
    packet_sha256: str,
    packet_uri: str,
    *,
    parent_run_id: str | None = None,
) -> RunRecord:
    services = current()
    data = services.reads_for(capture_id).get_bytes(packet_uri)
    digest = digest_of(data)
    if packet_sha256 and packet_sha256 != digest:
        raise ValueError("packet_sha256 does not match packet bytes")
    run_id = run_id_for(data, capture_id)
    packet = json.loads(data)
    capture = services.state.get_capture(capture_id)  # type: ignore[attr-defined]
    if capture is None:
        raise KeyError(capture_id)
    if not _acquire_claim(services, run_id, capture_id, digest, packet_uri):
        joined = _join_run(run_id)
        if joined is not None:
            return joined
        if not _acquire_claim(services, run_id, capture_id, digest, packet_uri):
            joined = _join_run(run_id)
            if joined is None:
                raise KeyError(run_id)
            return joined
    existing = services.state.get_run(run_id)  # type: ignore[attr-defined]
    if existing is not None and existing.capture_id == capture_id and existing.packet_sha256 == digest:
        _resume_if_stale(existing)
        stored = services.state.get_run(run_id)  # type: ignore[attr-defined]
        if stored is None:
            raise KeyError(run_id)
        return stored
    now = services.clock.iso()
    run = RunRecord(
        run_id=run_id,
        capture_id=capture_id,
        packet_sha256=digest,
        packet_uri=packet_uri,
        parent_run_id=parent_run_id,
        status="pending",
        branch=branch_of(packet),  # type: ignore[arg-type]
        flow=capture.flow,
        tier=capture.tier,
        profile_default=services.settings.RULES_PROFILE_DEFAULT,
        created_at=now,
        deadline_at=deadline_from(services.clock, services.settings.HARD_STOP_MIN),
        artifacts={},
        spend_usd={"gpu": 0.0, "cpu": 0.0, "vlm_reads": 0.0, "vlm_objects": 0.0, "vlm_c3": 0.0},
    )
    # The run record lands with the claim, before any slow read, so a dead
    # caller cannot leave a claim that blocks the capture forever.
    services.state.put_run(run)  # type: ignore[attr-defined]
    put_json(
        services.state,
        f"run_control:{run_id}",
        {
            "coordinator_heartbeat_at": now,
            "coordinator_respawns": 0,
            "cancel_requested": False,
            "merge_done": False,
        },
    )  # type: ignore[attr-defined]
    put_json(
        services.state,
        f"run_hooks:{run_id}",
        {
            "modal_remote": services.orch.modal_remote,
            "stage_stubs": services.orch.stage_stubs,
            "test_hang_stage": services.orch.test_hang_stage,
            "test_fail_stage": services.orch.test_fail_stage,
            "test_kill_coordinator_after": services.orch.test_kill_coordinator_after,
        },
    )  # type: ignore[attr-defined]
    for name in STAGE_NAMES:
        services.state.upsert_stage(  # type: ignore[attr-defined]
            StageRecord(run_id=run_id, stage=name, status="pending", attempt=1, heartbeat=now)
        )
    capture.packet_sha256 = digest
    capture.run_id = run_id
    services.state.put_capture(capture)  # type: ignore[attr-defined]
    services.state.set_status(capture_id, "processing")  # type: ignore[attr-defined]
    try:
        _add_early_vlm(run)
    except Exception as exc:
        log_event("early_vlm.skip", capture_id=capture_id, error=type(exc).__name__)
    services.state.put_run(run)  # type: ignore[attr-defined]
    emit_stage(
        services.state,
        capture_id,
        StageData(stage="validate", status="pending", attempt=0, runId=run_id),
        clock=services.clock,
    )
    flush_pending(services, capture_id)
    _final_capture_check(capture_id, packet)
    try:
        RunCoordinator(services).drive(run_id)
    except CoordinatorDied:
        pass
    stored = services.state.get_run(run_id)  # type: ignore[attr-defined]
    if stored is None:
        raise KeyError(run_id)
    return stored


def prewarm(capture_id: str, *, tier: Tier, expected_keyframes: int | None = None) -> None:
    request_prewarm(current(), capture_id, tier=tier, expected_keyframes=expected_keyframes)


def dispatch_early(capture_id: str, files: list[CommittedFile]) -> None:
    dispatch(current(), capture_id, files)


def _recompose_after_correction(capture_id: str, run: RunRecord) -> None:
    """Re-run criteria and result on the finished run so the served result reflects a late correction.

    A confirmation accepted after reads reached the result cannot retroactively
    enter the composed payload. ``start_run`` cannot stage a fresh run here — it
    derives the run id from the packet digest, so the same packet joins the
    existing run instead of creating one. Instead the finished run's criteria and
    result stages go back to pending, the run re-enters the normal coordinator
    lifecycle, and the re-executed stages overwrite the same artifact keys: the
    recomposed payload is what ``GET /result`` serves next. The acceptance event
    and the recomposition's ``verdict_ready`` land on ``GET /events``. A replayed
    confirmation still short-circuits upstream, so one correction recomposes once.

    A finished run without a record for every stage predates per-stage records.
    Its upstream stages really finished — the run is done — so the rebuild
    preserves that work instead of skipping it: every stage history never wrote
    gets a terminal done record (idempotent, the same recovery shape the
    coordinator's missing-record materialization uses), and only the criteria and
    result records reset. No succeeded stage reopens, so the re-run reads stage
    that would overwrite the just-persisted correction never executes. If the
    stored upstream artifacts cannot support the rebuild — no scene bytes for the
    coordinator-owned merge, say — the coordinator closes the run with its normal
    artifact_invalid failure instead of leaving the stale result served as
    success.

    A live run is left alone — its result stage has not consumed the corrected
    reads yet, so it recomposes by simply running.
    """

    services = current()
    if run.status != "done":
        return
    prepared = [False]

    def prepare(guard: LeaseGuard) -> None:
        nonlocal run
        guard.assert_held()
        stored = services.state.get_run(run.run_id)  # type: ignore[attr-defined]
        if stored is None or stored.status != "done":
            return
        run = stored
        present = {rec.stage for rec in services.state.stages(run.run_id)}  # type: ignore[attr-defined]
        missing = [name for name in STAGE_NAMES if name not in present]
        preserved = 0
        if missing:
            now = services.clock.iso()
            for name in missing:
                if name in ("criteria", "result"):
                    continue
                services.state.create_stage_if_absent(  # type: ignore[attr-defined]
                    StageRecord(
                        run_id=run.run_id,
                        stage=name,
                        status="done",
                        attempt=1,
                        heartbeat=now,
                        finished_at=now,
                    )
                )
                preserved += 1
            log_event(
                "confirmation.recompose_legacy",
                capture_id=capture_id,
                run_id=run.run_id,
                preserved_stages=preserved,
            )
        reset_criteria_and_result(services, run)
        # The marker is cleared only after the reset is durable. A failed reset
        # therefore remains recoverable by the coordinator.
        guard.assert_held()
        for marker in services.state.list_blobs(pending_correction_key(run.run_id)):  # type: ignore[attr-defined]
            services.state.delete_blob(marker)  # type: ignore[attr-defined]
        prepared[0] = True

    with_correction_lease(services, run.run_id, prepare)
    if not prepared[0]:
        return
    try:
        RunCoordinator(services).drive(run.run_id)
    except CoordinatorDied:
        pass
    log_event("confirmation.recompose", capture_id=capture_id, run_id=run.run_id)


def on_confirmation(
    capture_id: str,
    confirmation: ConfirmationRequest,
    confirmation_id: str,
    *,
    by: str,
) -> None:
    services = current()
    put_json(
        services.state,  # type: ignore[attr-defined]
        f"confirmation:{capture_id}:{confirmation_id}",
        {"by": by, **confirmation.model_dump(mode="json")},
    )
    try:
        from server.reads.policy import confirm
        from server.reads.types import Reads
    except ImportError:
        return
    runs = services.state.runs_for_capture(capture_id)  # type: ignore[attr-defined]
    if not runs:
        return
    newest = max(runs, key=lambda item: item.created_at)
    key = _latest_reads_key(services, capture_id)
    if key is None:
        return
    # The marker is durable before the reads read-modify-write. A crash at
    # either boundary leaves work for recomposition, and each confirmation gets
    # its own marker so concurrent fields cannot overwrite each other.
    _record_pending_correction(services, newest, capture_id, confirmation, confirmation_id, by)
    recompose = [False]

    def apply_correction(guard: LeaseGuard) -> None:
        guard.assert_held()
        try:
            reads = Reads.model_validate(json.loads(services.storage.get_bytes(key)))  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            raise RuntimeError("confirmation could not load the reads artifact") from exc
        updated = confirm(
            reads,
            still_id=confirmation.imageId,
            choice=confirmation.value if confirmation.kind == "confirmed" else None,
            typed=confirmation.value if confirmation.kind == "corrected" else None,
            by=by,  # type: ignore[arg-type]
            field_id=confirmation.fieldId,
        )
        guard.assert_held()
        services.storage.put_bytes(key, updated.model_dump_json().encode(), "application/json")  # type: ignore[attr-defined]
        guard.assert_held()
        marker_key = pending_correction_key(newest.run_id, confirmation_id)
        marker = get_json(services.state, marker_key) or {}  # type: ignore[attr-defined]
        marker["readsApplied"] = True
        put_json(services.state, marker_key, marker)  # type: ignore[attr-defined]
        guard.assert_held()
        fresh = services.state.get_run(newest.run_id)  # type: ignore[attr-defined]
        if fresh is not None and fresh.status == "done":
            recompose[0] = True

    with_correction_lease(services, newest.run_id, apply_correction)
    if recompose[0]:
        fresh = services.state.get_run(newest.run_id)  # type: ignore[attr-defined]
        if fresh is not None:
            _recompose_after_correction(capture_id, fresh)


def _record_pending_correction(
    services: object,
    run: RunRecord,
    capture_id: str,
    confirmation: ConfirmationRequest | None = None,
    confirmation_id: str | None = None,
    by: str | None = None,
) -> None:
    """Persist one correction before changing the shared reads artifact.

    The per-confirmation marker survives a crash between the marker and reads
    writes, while the shared correction lease serializes field merges.
    """
    marker = {"captureId": capture_id, "recordedAt": services.clock.iso(), "readsApplied": False}  # type: ignore[attr-defined]
    if confirmation is not None:
        marker.update({"confirmationId": confirmation_id, "by": by, **confirmation.model_dump(mode="json")})
    put_json(
        services.state,  # type: ignore[attr-defined]
        pending_correction_key(run.run_id, confirmation_id),
        marker,
    )
    log_event("confirmation.pending_correction", capture_id=capture_id, run_id=run.run_id)



def _latest_reads_key(services: object, capture_id: str) -> str | None:
    """The reads artifact of the capture's newest run, or None before reads exist."""

    runs = services.state.runs_for_capture(capture_id)  # type: ignore[attr-defined]
    if not runs:
        return None
    run = max(runs, key=lambda item: item.created_at)
    from server.orchestrator.artifacts import overlay_stage_artifacts

    key = overlay_stage_artifacts(services.state, run).artifacts.get("reads/reads.json")  # type: ignore[attr-defined]
    return key or None


def cancel_capture(capture_id: str) -> None:
    services = current()
    for run in services.state.runs_for_capture(capture_id):  # type: ignore[attr-defined]
        if run.status not in {"pending", "running"}:
            continue
        raw = get_json(services.state, f"run_control:{run.run_id}") or {}  # type: ignore[attr-defined]
        raw["cancel_requested"] = True
        put_json(services.state, f"run_control:{run.run_id}", raw)  # type: ignore[attr-defined]
        RunCoordinator(services).tick(run.run_id)


def sweep() -> int:
    services = current()
    reclaimed = _reclaim_orphan_claims(services)
    return sweep_runs(services) + reclaimed


def purge_capture(capture_id: str) -> None:
    """Erase one capture's runs, stages, blobs, spend and work folders.

    ``DELETE /v1/captures/{id}`` calls this. A second call is a no-op aside
    from refreshing the tombstone. Storage is listed from the backend root,
    not from the process-local index.
    """

    services = current()
    state = services.state
    run_ids = [run.run_id for run in state.runs_for_capture(capture_id)]  # type: ignore[attr-defined]
    needles = [capture_id, *run_ids]
    _drop_memory_records(state, capture_id, run_ids)
    for key in list(state.list_blobs("")):  # type: ignore[attr-defined]
        if _mentions(key, needles):
            state.delete_blob(key)  # type: ignore[attr-defined]
    _drop_mapping(getattr(state, "_kv", None), needles)
    _drop_mapping(getattr(state, "_spend", None), needles)
    kv = getattr(state, "kv", None)
    _drop_mapping(getattr(kv, "_items", None), needles)
    for run_id in run_ids:
        _remove_tree(services.work_root / run_id)
    _remove_tree(services.work_root / capture_id)
    _purge_storage_tree(services.storage, capture_id)
    if hasattr(state, "delete_capture"):
        state.delete_capture(capture_id)


def _join_run(run_id: str) -> RunRecord | None:
    """Another container owns this run. Do not create stages or drive unless it is stale.

    None means the claim expired with no run record, so the caller may take it.
    """

    services = current()
    for _ in range(40):
        existing = services.state.get_run(run_id)  # type: ignore[attr-defined]
        if existing is not None:
            _resume_if_stale(existing)
            stored = services.state.get_run(run_id)  # type: ignore[attr-defined]
            if stored is None:
                raise KeyError(run_id)
            return stored
        time.sleep(0.05)
    claim = services.state.get_kv(f"run_claim:{run_id}") or {}  # type: ignore[attr-defined]
    if services.state.get_run(run_id) is None and _claim_expired(services, claim.get("at")):  # type: ignore[attr-defined]
        return None
    raise KeyError(run_id)


def _resume_if_stale(run: RunRecord) -> None:
    services = current()
    raw = get_json(services.state, f"run_control:{run.run_id}") or {}  # type: ignore[attr-defined]
    if run.status not in {"pending", "running"}:
        return
    if not coordinator_is_dead(run, raw, now=services.clock.now(), stale_s=services.orch.stale_s):
        return
    try:
        RunCoordinator(services).drive(run.run_id)
    except CoordinatorDied:
        pass


def _add_early_vlm(run: RunRecord) -> None:
    services = current()
    total = 0.0
    for key in early_keys(services.state, run.capture_id):
        blob = get_json(services.state, key) or {}  # type: ignore[attr-defined]
        if isinstance(blob, dict):
            total += float(blob.get("cost_usd") or 0.0)
    if total:
        run.spend_usd["vlm_reads"] = total


def _acquire_claim(services: object, run_id: str, capture_id: str, digest: str, packet_uri: str) -> bool:
    state = services.state  # type: ignore[attr-defined]
    key = f"run_claim:{run_id}"
    payload = {
        "capture_id": capture_id,
        "packet_sha256": digest,
        "packet_uri": packet_uri,
        "at": services.clock.iso(),  # type: ignore[attr-defined]
    }
    if state.put_if_absent(key, payload):
        return True
    if state.get_run(run_id) is not None:
        return False
    existing = state.get_kv(key) or {}
    if not _claim_expired(services, existing.get("at")):
        return False
    state.delete_kv(key)
    return bool(state.put_if_absent(key, payload))


def _claim_expired(services: object, at: object) -> bool:
    if not isinstance(at, str) or not at:
        return True
    try:
        age = (services.clock.now() - parse_iso(at)).total_seconds()  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        return True
    return age >= CLAIM_LEASE_S


def _reclaim_orphan_claims(services: object) -> int:
    state = services.state  # type: ignore[attr-defined]
    claims = state.iter_run_claims() if hasattr(state, "iter_run_claims") else []
    taken = 0
    for run_id, claim in claims:
        if state.get_run(run_id) is not None:
            continue
        if not _claim_expired(services, claim.get("at")):
            continue
        state.delete_kv(f"run_claim:{run_id}")
        capture_id = str(claim.get("capture_id") or "")
        if not capture_id:
            continue
        packet_uri = str(claim.get("packet_uri") or f"captures/{capture_id}/packet.json")
        digest = str(claim.get("packet_sha256") or "")
        try:
            start_run(capture_id, digest, packet_uri)
        except Exception as exc:
            log_event("run_claim.reclaim_failed", capture_id=capture_id, error=type(exc).__name__)
            continue
        taken += 1
    return taken


def _final_capture_check(capture_id: str, packet: dict) -> None:
    final_check(current(), capture_id, packet)


def _drop_memory_records(state: object, capture_id: str, run_ids: list[str]) -> None:
    runs = getattr(state, "_runs", None)
    if isinstance(runs, dict):
        for run_id in list(runs):
            rec = runs[run_id]
            if getattr(rec, "capture_id", None) == capture_id or run_id in run_ids:
                runs.pop(run_id, None)
    stages = getattr(state, "_stages", None)
    if isinstance(stages, dict):
        for key in list(stages):
            if key[0] in run_ids:
                del stages[key]
    events = getattr(state, "_events", None)
    if isinstance(events, dict):
        events.pop(capture_id, None)
    files = getattr(state, "_files", None)
    if isinstance(files, dict):
        for key in list(files):
            if key[0] == capture_id:
                del files[key]


def _drop_mapping(mapping: object, needles: list[str]) -> None:
    if not isinstance(mapping, dict):
        return
    for key in list(mapping):
        if isinstance(key, str) and _mentions(key, needles) and not key.startswith("tomb:"):
            mapping.pop(key, None)


def _mentions(key: str, needles: list[str]) -> bool:
    return any(needle and needle in key for needle in needles)


def _purge_storage_tree(storage: object, capture_id: str) -> None:
    root = getattr(storage, "root", None)
    if root is None:
        return
    base = Path(root)
    prefixes = (f"captures/{capture_id}", f"uploads/{capture_id}", f"work/{capture_id}")
    for prefix in prefixes:
        _remove_tree(base / prefix)
        # A file can sit beside the index. Walk the backend, not ``_meta``.
        folder = base / prefix.split("/", 1)[0]
        if folder.is_dir():
            for path in folder.rglob("*"):
                if capture_id in path.parts and path.is_file():
                    path.unlink(missing_ok=True)
    meta = getattr(storage, "_meta", None)
    if isinstance(meta, dict):
        for key in list(meta):
            if any(key == prefix or key.startswith(f"{prefix}/") for prefix in prefixes):
                meta.pop(key, None)
        save = getattr(storage, "_save", None)
        if callable(save):
            save()


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file() or path.is_symlink():
        path.unlink()
