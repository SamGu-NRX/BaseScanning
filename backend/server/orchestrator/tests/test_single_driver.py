"""One finish thread owns a run. Stage time starts when the worker enters."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from server.contracts.jobs import RunRecord, StageRecord
from server.orchestrator.api import start_run
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.services import bind
from server.orchestrator.stateio import put_json
from server.orchestrator.sweeper import coordinator_is_dead, sweep
from server.orchestrator.tests.conftest import build_services


def test_second_start_does_not_reset_stages(tmp_path, monkeypatch) -> None:
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=True, coordinator_loop_s=0)
    calls: list[str] = []

    def drive(self, run_id: str, **kwargs: object) -> str:
        del kwargs
        calls.append(run_id)
        rec = self.s.state.get_stage(run_id, "validate")
        assert rec is not None
        rec.status = "done"
        self.s.state.upsert_stage(rec)
        time.sleep(0.3)
        return "running"

    monkeypatch.setattr(RunCoordinator, "drive", drive)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def go() -> None:
        try:
            barrier.wait(timeout=2)
            with bind(services):
                start_run("cap_test", digest, uri)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert calls == [calls[0]]
    assert len(calls) == 1
    assert services.state.get_stage(calls[0], "validate").status == "done"


def test_fresh_run_without_heartbeat_is_not_dead(tmp_path, monkeypatch) -> None:
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True)
    deadline = (services.clock.now() + timedelta(hours=1)).isoformat()
    now = services.clock.iso()
    run = RunRecord(
        run_id="run_fresh",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=now,
        deadline_at=deadline,
        artifacts={},
        spend_usd={},
    )
    services.state.put_run(run)
    put_json(services.state, f"run_hooks:{run.run_id}", {"modal_remote": True})
    assert coordinator_is_dead(run, {}, now=services.clock.now(), stale_s=services.orch.stale_s) is False
    monkeypatch.setattr(
        "server.orchestrator.sweeper.RunCoordinator.drive",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("drive")),
    )
    assert sweep(services) == 0


def test_scale_timeout_starts_when_the_worker_enters(tmp_path) -> None:
    services, _uri, digest = build_services(tmp_path, "memory")
    deadline = (services.clock.now() + timedelta(hours=1)).isoformat()
    started = services.clock.iso()
    run = RunRecord(
        run_id="run_scale",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=started,
        started_at=started,
        deadline_at=deadline,
        artifacts={},
        spend_usd={},
    )
    services.state.put_run(run)
    services.state.upsert_stage(
        StageRecord(
            run_id=run.run_id,
            stage="scale",
            status="running",
            attempt=1,
            heartbeat=started,
            started_at=started,
            worker_call_id="call-scale",
        )
    )
    services.clock.advance(90)
    coordinator = RunCoordinator(services)
    coordinator._sweep_timeouts(run)
    assert services.state.get_stage(run.run_id, "scale").status == "running"
    assert services.state.heartbeat(run.run_id, "scale", "call-scale", "start", services.clock.iso())
    coordinator._rows(run.run_id, refresh=True)
    coordinator._sweep_timeouts(run)
    assert services.state.get_stage(run.run_id, "scale").status == "running"
    services.clock.advance(61)
    coordinator._rows(run.run_id, refresh=True)
    coordinator._sweep_timeouts(run)
    rec = services.state.get_stage(run.run_id, "scale")
    assert rec is not None
    assert rec.status == "pending"
    assert rec.failures == 1
