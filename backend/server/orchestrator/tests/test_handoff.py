"""Remote hand-off: pass keys, do not copy files, and do not spawn past the deadline."""

from __future__ import annotations

from datetime import timedelta

from server.contracts.jobs import RunRecord, StageRecord, StageResult
from server.orchestrator.coordinator import RunControl, RunCoordinator
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.services import Services
from server.orchestrator.stateio import get_json, put_json
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.conftest import build_services


def _remote_ok(monkeypatch, calls: list[tuple[str, str | None]]) -> None:
    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)

    def remote(run_id: str, stage: str, attempt: int, gpu: str | None) -> str:
        del run_id, attempt
        calls.append((stage, gpu))
        return f"fc-{stage}"

    monkeypatch.setattr("server.orchestrator.modal_spawn._remote_spawn", remote)


def _run(services, digest: str, *, deadline: str, status: str = "running") -> RunRecord:
    now = services.clock.iso()
    run = RunRecord(
        run_id="run_handoff",
        capture_id="cap_test",
        packet_sha256=digest,
        status=status,
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
    return run


def test_remote_spawn_looks_up_the_deployed_app(monkeypatch) -> None:
    import modal

    from server.orchestrator.modal_spawn import _remote_spawn

    seen: list[tuple[object, ...]] = []

    class _Call:
        object_id = "fc-looked-up"

    class _Method:
        def spawn(self, *args: object) -> _Call:
            seen.append(("spawn", args))
            return _Call()

    class _Handle:
        run_stage = _Method()

    class _Cls:
        def with_options(self, **kwargs: object) -> _Cls:
            seen.append(("options", tuple(sorted(kwargs.items()))))
            return self

        def __call__(self) -> _Handle:
            return _Handle()

    def cls_from_name(app_name: str, name: str) -> _Cls:
        seen.append(("cls", app_name, name))
        return _Cls()

    def fn_from_name(app_name: str, name: str) -> _Method:
        seen.append(("fn", app_name, name))
        return _Method()

    monkeypatch.setenv("MODAL_APP_NAME", "hss-probe")
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(cls_from_name))
    monkeypatch.setattr(modal.Function, "from_name", staticmethod(fn_from_name))
    assert _remote_spawn("run_1", "validate", 1, None) == "fc-looked-up"
    assert _remote_spawn("run_1", "poses", 2, "L4") == "fc-looked-up"
    assert ("fn", "hss-probe", "run_cpu_stage") in seen
    assert ("cls", "hss-probe", "Pipeline") in seen
    assert ("options", (("gpu", "L4"),)) in seen
    assert ("spawn", ("run_1", "validate", 1)) in seen
    assert ("spawn", ("run_1", "poses", 2)) in seen


def test_remote_spawn_passes_ids_and_does_not_hydrate(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _remote_ok(monkeypatch, calls)
    hydrated: list[str] = []

    def spy(self, run, rec, call_id):
        del self, run, call_id
        hydrated.append(rec.stage)
        raise AssertionError("remote spawn hydrated a work dir")

    monkeypatch.setattr(Services, "stage_context", spy)
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True, coordinator_loop_s=0)
    deadline = (services.clock.now() + timedelta(hours=1)).isoformat()
    run = _run(services, digest, deadline=deadline)
    for name in STAGE_NAMES:
        services.state.upsert_stage(
            StageRecord(run_id=run.run_id, stage=name, status="pending", attempt=1, heartbeat=services.clock.iso())
        )
    RunCoordinator(services)._spawn_ready(run, RunControl())
    assert calls[0] == ("validate", None)
    assert hydrated == []
    record = services.state.get_stage(run.run_id, "validate")
    assert record.worker_call_id == "fc-validate"


def test_objects_remote_spawn_uses_the_gpu_image(monkeypatch) -> None:
    import modal

    seen: dict[str, object] = {}

    class _Call:
        object_id = "fc-objects"

    class _Method:
        def spawn(self, run_id: str, stage: str, attempt: int) -> _Call:
            seen["spawn"] = (run_id, stage, attempt)
            return _Call()

    class _Handle:
        run_stage = _Method()

    class _Cls:
        def with_options(self, **kwargs: object) -> _Cls:
            seen["gpu"] = kwargs.get("gpu")
            return self

        def __call__(self) -> _Handle:
            return _Handle()

    def cls_from_name(app_name: str, name: str) -> _Cls:
        seen["cls"] = (app_name, name)
        return _Cls()

    def fn_from_name(app_name: str, name: str) -> None:
        raise AssertionError((app_name, name))

    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(cls_from_name))
    monkeypatch.setattr(modal.Function, "from_name", staticmethod(fn_from_name))
    from server.orchestrator.modal_spawn import _remote_spawn

    assert _remote_spawn("run_facade", "objects", 1, "L4") == "fc-objects"
    assert seen["cls"] == ("house-scanning", "Pipeline")
    assert seen["gpu"] == "L4"
    assert seen["spawn"] == ("run_facade", "objects", 1)


def test_reads_remote_spawn_stays_on_the_cpu_image(monkeypatch) -> None:
    import modal

    seen: dict[str, object] = {}

    class _Call:
        object_id = "fc-reads"

    class _Method:
        def spawn(self, run_id: str, stage: str, attempt: int) -> _Call:
            seen["cpu"] = (run_id, stage, attempt)
            return _Call()

    def cls_from_name(app_name: str, name: str) -> None:
        raise AssertionError((app_name, name))

    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(cls_from_name))
    monkeypatch.setattr(modal.Function, "from_name", staticmethod(lambda app_name, name: _Method()))
    from server.orchestrator.modal_spawn import _remote_spawn

    assert _remote_spawn("run_facade", "reads", 1, None) == "fc-reads"
    assert seen["cpu"] == ("run_facade", "reads", 1)


def test_spawn_refuses_after_the_deadline(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _remote_ok(monkeypatch, calls)
    answers = {"n": 0}

    def past(self, run) -> bool:
        del self, run
        answers["n"] += 1
        return answers["n"] >= 2

    monkeypatch.setattr(RunCoordinator, "_past_deadline", past)
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True)
    run = _run(services, digest, deadline=services.clock.iso())
    for name in STAGE_NAMES:
        services.state.upsert_stage(
            StageRecord(
                run_id=run.run_id,
                stage=name,
                status="done" if name == "validate" else "pending",
                attempt=1,
                heartbeat=services.clock.iso(),
            )
        )
    RunCoordinator(services)._spawn_ready(run, RunControl())
    assert calls == []
    stored = services.state.get_run(run.run_id)
    assert stored.failure_code == "hard_stop"
    assert stored.status == "failed"


def test_coordinator_reloads_the_volume_only_to_merge(tmp_path, monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr("server.orchestrator.coordinator.sync_volume", lambda action: actions.append(action))
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True)
    run = _run(services, digest, deadline=services.clock.iso())
    now = services.clock.iso()
    for name in STAGE_NAMES:
        services.state.upsert_stage(
            StageRecord(run_id=run.run_id, stage=name, status="pending", attempt=1, heartbeat=now)
        )
    coordinator = RunCoordinator(services)
    coordinator._merge_if_ready(run, RunControl())
    assert actions == []
    for name, status in (("scene", "done"), ("objects_3d", "done")):
        services.state.upsert_stage(StageRecord(run_id=run.run_id, stage=name, status=status, attempt=1, heartbeat=now))
    coordinator._merge_if_ready(run, RunControl())
    assert actions[0] == "reload"


def test_parallel_stage_done_keeps_both_artifact_maps(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("server.orchestrator.coordinator.sync_volume", lambda action: None)
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True)
    deadline = (services.clock.now() + timedelta(hours=1)).isoformat()
    run = _run(services, digest, deadline=deadline)
    now = services.clock.iso()
    for name in STAGE_NAMES:
        services.state.upsert_stage(
            StageRecord(run_id=run.run_id, stage=name, status="running", attempt=1, heartbeat=now)
        )
    coordinator = RunCoordinator(services)
    stale_scene = services.state.get_run(run.run_id)
    stale_objects = services.state.get_run(run.run_id)
    coordinator._mark_done(
        stale_scene,
        services.state.get_stage(run.run_id, "scene"),
        StageResult(
            artifacts={"scene/scene.json": "captures/x/scene/scene.json"},
            metrics={"duration_s": 0.01},
            warnings=[],
        ),
    )
    coordinator._mark_done(
        stale_objects,
        services.state.get_stage(run.run_id, "objects_3d"),
        StageResult(
            artifacts={"objects/objects_3d.json": "captures/x/objects/objects_3d.json"},
            metrics={"duration_s": 0.01},
            warnings=[],
        ),
    )
    stored = services.state.get_run(run.run_id)
    assert "scene/scene.json" not in stored.artifacts
    overlaid = coordinator._overlay_artifacts(stored)
    assert overlaid.artifacts["scene/scene.json"] == "captures/x/scene/scene.json"
    assert overlaid.artifacts["objects/objects_3d.json"] == "captures/x/objects/objects_3d.json"


def test_sweeper_respawns_the_remote_coordinator(tmp_path, monkeypatch) -> None:
    spawned: list[str] = []

    def fail_drive(self, run_id: str, **kwargs: object) -> str:
        del self, kwargs
        raise AssertionError(f"in-process drive {run_id}")

    monkeypatch.setattr("server.orchestrator.sweeper.RunCoordinator.drive", fail_drive)
    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)

    class _Call:
        @staticmethod
        def spawn(run_id: str) -> None:
            spawned.append(run_id)

    monkeypatch.setattr("server.orchestrator.modal_functions.coordinate_run", _Call)
    services, _uri, digest = build_services(tmp_path, "memory", modal_remote=True)
    deadline = (services.clock.now() + timedelta(hours=1)).isoformat()
    run = _run(services, digest, deadline=deadline, status="running")
    services.state.put_run(run)
    put_json(
        services.state,
        f"run_control:{run.run_id}",
        {"coordinator_heartbeat_at": services.clock.iso(), "coordinator_respawns": 0},
    )
    put_json(services.state, f"run_hooks:{run.run_id}", {"modal_remote": True})
    services.clock.advance(181)
    assert sweep(services) >= 1
    assert spawned == [run.run_id]
    control = get_json(services.state, f"run_control:{run.run_id}")
    assert control["coordinator_respawns"] == 1
    assert services.state.stages(run.run_id) == []
