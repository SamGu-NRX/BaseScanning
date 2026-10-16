"""A remote worker stays owned while its stage runs without progress callbacks."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from server.contracts.jobs import RunRecord, StageRecord, StageResult
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.tests.conftest import build_services


def test_remote_heartbeat_keeps_live_worker_but_sweeps_silent_worker(tmp_path, monkeypatch):
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True, heartbeat_s=0.01)
    now = services.clock.iso()
    run = RunRecord(
        run_id="run_remote_heartbeat",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=now,
        deadline_at=now,
        artifacts={},
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
                heartbeat=now,
            )
        )
    entered = threading.Event()
    release = threading.Event()
    advanced = threading.Event()
    fresh_beat = threading.Event()
    cancelled = []
    heartbeat = services.state.heartbeat

    def record_heartbeat(run_id, stage, call_id, substep, at):
        result = heartbeat(run_id, stage, call_id, substep, at)
        if stage == "objects" and substep == "beat" and advanced.is_set() and at == services.clock.iso():
            fresh_beat.set()
        return result

    def stage_body(_ctx):
        entered.set()
        assert release.wait(5)
        return StageResult(artifacts={}, metrics={}, warnings=[])

    monkeypatch.setattr(services.state, "heartbeat", record_heartbeat)
    monkeypatch.setattr("server.orchestrator.runner.Clock", lambda: services.clock)
    monkeypatch.setattr("server.orchestrator.runner.stage_callable", lambda *_args, **_kwargs: stage_body)
    monkeypatch.setattr("server.orchestrator.coordinator.current_call_id", lambda: "fc-objects")
    monkeypatch.setattr("server.orchestrator.coordinator.cancel_modal", cancelled.append)
    coordinator = RunCoordinator(services)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.execute_named_stage, run.run_id, "objects", 1, gpu="")
        try:
            assert entered.wait(2)
            services.clock.advance(services.orch.stale_s + 1)
            advanced.set()
            assert fresh_beat.wait(2), "remote worker did not send a recurring heartbeat"
            coordinator._sweep_stale(run)
            assert services.state.get_stage(run.run_id, "objects").status == "running"
            assert services.state.get_stage(run.run_id, "reads").status == "pending"
            assert cancelled == ["fc-reads"]
        finally:
            release.set()
            future.result(timeout=3)
    assert services.state.get_stage(run.run_id, "objects").status == "done"
