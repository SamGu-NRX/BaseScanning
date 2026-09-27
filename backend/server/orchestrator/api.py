"""SD-23 entry points. WS-01's hooks bind to these functions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from server.contracts.api import ConfirmationRequest, StageData
from server.contracts.jobs import (
    CommittedFile,
    RunRecord,
    StageRecord,
)
from server.contracts.packet import Tier
from server.orchestrator.clock import parse_iso
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.early.capture_check import final_check, flush_pending
from server.orchestrator.early.dispatch import dispatch
from server.orchestrator.events import emit_stage
from server.orchestrator.executor import CoordinatorDied
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.prewarm import request_prewarm
from server.orchestrator.services import branch_of, current, deadline_from, run_id_for
from server.orchestrator.services import packet_sha256 as digest_of
from server.orchestrator.stateio import get_json, put_json
from server.orchestrator.sweeper import sweep as sweep_runs

_READ_PURPOSES = {"meter_close", "meter_oblique", "panel_close", "panel_open", "main_disconnect"}


def start_run(
    capture_id: str,
    packet_sha256: str,
    packet_uri: str,
    *,
    parent_run_id: str | None = None,
) -> RunRecord:
    services = current()
    data = services.storage.get_bytes(packet_uri)  # type: ignore[attr-defined]
    digest = digest_of(data)
    if packet_sha256 and packet_sha256 != digest:
        raise ValueError("packet_sha256 does not match packet bytes")
    run_id = run_id_for(data)
    existing = _same_run(capture_id, digest)
    if existing is not None:
        _resume_if_stale(existing)
        return services.state.get_run(existing.run_id)  # type: ignore[attr-defined]
    packet = json.loads(data)
    capture = services.state.get_capture(capture_id)  # type: ignore[attr-defined]
    if capture is None:
        raise KeyError(capture_id)
    now = services.clock.iso()
    run = RunRecord(
        run_id=run_id,
        capture_id=capture_id,
        packet_sha256=digest,
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
    _add_early_vlm(run)
    services.state.put_run(run)  # type: ignore[attr-defined]
    put_json(
        services.state,
        f"run_control:{run_id}",
        {"coordinator_respawns": 0, "cancel_requested": False, "merge_done": False},
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
    except ImportError:
        return
    confirm(
        capture_id,
        still_id=confirmation.imageId,
        choice=confirmation.value if confirmation.kind == "confirmed" else None,
        typed=confirmation.value if confirmation.kind == "corrected" else None,
        by=by,
        field_id=confirmation.fieldId,
    )


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
    return sweep_runs(current())


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


def _same_run(capture_id: str, digest: str) -> RunRecord | None:
    services = current()
    for run in services.state.runs_for_capture(capture_id):  # type: ignore[attr-defined]
        if run.packet_sha256 == digest:
            return run
    return None


def _resume_if_stale(run: RunRecord) -> None:
    services = current()
    raw = get_json(services.state, f"run_control:{run.run_id}") or {}  # type: ignore[attr-defined]
    beat = raw.get("coordinator_heartbeat_at")
    if run.status not in {"pending", "running"}:
        return
    if beat is not None:
        age = (services.clock.now() - parse_iso(str(beat))).total_seconds()
        if age <= services.orch.stale_s:
            return
    try:
        RunCoordinator(services).drive(run.run_id)
    except CoordinatorDied:
        pass


def _add_early_vlm(run: RunRecord) -> None:
    services = current()
    total = 0.0
    for key in services.state.list_blobs(f"early:{run.capture_id}:"):  # type: ignore[attr-defined]
        blob = get_json(services.state, key) or {}  # type: ignore[attr-defined]
        total += float(blob.get("cost_usd") or 0.0)
    if total:
        run.spend_usd["vlm_reads"] = total


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
