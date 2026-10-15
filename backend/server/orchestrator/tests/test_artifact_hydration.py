"""Fresh workers consume outputs published independently of the shared run map."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from server.contracts.jobs import RunRecord, StageRecord, StageResult
from server.objects import run_3d
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.services import Services
from server.orchestrator.stateio import put_json
from server.orchestrator.tests.conftest import build_services

_SCENE = {"keyframes": [], "walls": [], "objects": []}
_OBJECTS = {"format": "hss-objects/v0", "objects": [{"id": "door-1", "class": "door", "views": []}]}
_READS = {"format": "base-reads/v0", "facts": []}


def _setup(tmp_path: Path, backend: str) -> tuple[Services, RunRecord]:
    services, _, digest = build_services(tmp_path, backend, modal_remote=True)
    scene_key = "captures/cap_test/runs/run_parallel/scene/scene.json"
    services.storage.put_bytes(scene_key, json.dumps(_SCENE).encode(), "application/json")
    run = RunRecord(
        run_id="run_parallel",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=services.clock.iso(),
        deadline_at=(services.clock.now() + timedelta(hours=1)).isoformat(),
        artifacts={"scene/scene.json": scene_key},
        spend_usd={},
    )
    services.state.put_run(run)
    for stage in ("objects", "reads"):
        services.state.upsert_stage(
            StageRecord(
                run_id=run.run_id,
                stage=stage,
                status="running",
                attempt=1,
                worker_call_id=f"fc-{stage}",
                heartbeat=services.clock.iso(),
            )
        )
    return services, run


def _context(services: Services, run: RunRecord):
    rec = StageRecord(
        run_id=run.run_id, stage="objects_3d", status="running", attempt=1, heartbeat=services.clock.iso()
    )
    return services.stage_context(run, rec, "fc-objects-3d")


def _stage_body(ctx) -> StageResult:
    body = _OBJECTS if ctx.stage == "objects" else _READS
    name = f"{ctx.stage}.json"
    (ctx.work_dir / name).write_text(json.dumps(body))
    return StageResult(artifacts={f"{ctx.stage}/{name}": name}, metrics={"duration_s": 1.0}, warnings=[])


def test_parallel_outputs_reach_a_fresh_worker(tmp_path, monkeypatch) -> None:
    services, run = _setup(tmp_path, "memory")
    both = threading.Barrier(2)
    first_done = threading.Event()

    def stage_body(ctx):
        both.wait(timeout=10)
        if ctx.stage != "objects":
            assert first_done.wait(timeout=10)
        return _stage_body(ctx)

    def worker(stage: str):
        try:
            return RunCoordinator(services).execute_named_stage(run.run_id, stage, 1, gpu="")
        finally:
            if stage == "objects":
                first_done.set()

    monkeypatch.setattr("server.orchestrator.coordinator.current_call_id", lambda: None)
    monkeypatch.setattr("server.orchestrator.runner.stage_callable", lambda *args, **kwargs: stage_body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, stage) for stage in ("objects", "reads")]
        for future in futures:
            future.result(timeout=20)
    fresh = replace(services, work_root=tmp_path / "fresh-worker")
    stored = services.state.get_run(run.run_id)
    assert stored is not None
    ctx = _context(fresh, stored)
    assert json.loads((ctx.work_dir / "objects" / "objects.json").read_text()) == _OBJECTS
    control = json.loads(Path(ctx.inputs["run_control"]).read_text())
    assert {"objects/objects.json", "reads/reads.json", "scene/scene.json"} <= set(control["run"]["artifacts"])
    result = run_3d(ctx)
    assert result.metrics["objects_emitted"] == 1.0


def test_output_is_hydratable_as_soon_as_stage_is_done(tmp_path, monkeypatch) -> None:
    services, run = _setup(tmp_path, "memory")
    put_stage = services.state.upsert_stage
    consumed: list[float] = []

    def publish_done(rec):
        put_stage(rec)
        if rec.stage == "objects" and rec.status == "done":
            fresh = replace(services, work_root=tmp_path / "ready-worker")
            ctx = _context(fresh, services.state.get_run(run.run_id))
            consumed.append(run_3d(ctx).metrics["objects_emitted"])

    monkeypatch.setattr(services.state, "upsert_stage", publish_done)
    monkeypatch.setattr("server.orchestrator.coordinator.current_call_id", lambda: None)
    monkeypatch.setattr("server.orchestrator.runner.stage_callable", lambda *args, **kwargs: _stage_body)
    RunCoordinator(services).execute_named_stage(run.run_id, "objects", 1, gpu="")
    assert consumed == [1.0]


def test_parent_outputs_are_recovered_under_parent(tmp_path) -> None:
    services, parent = _setup(tmp_path, "memory")
    key = "captures/cap_test/runs/run_parallel/objects/objects.json"
    services.storage.put_bytes(key, json.dumps(_OBJECTS).encode(), "application/json")
    put_json(services.state, f"stage_artifacts:{parent.run_id}:objects", {"objects/objects.json": key})
    child = parent.model_copy(update={"run_id": "run_child", "parent_run_id": parent.run_id, "artifacts": {}})
    services.state.put_run(child)
    ctx = _context(services, child)
    assert json.loads(Path(ctx.inputs["parent/objects/objects.json"]).read_text()) == _OBJECTS
    assert not (ctx.work_dir / "objects" / "objects.json").exists()
