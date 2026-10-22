"""Round-2 validation fixes: clock, replay, frusta, outcomes, heartbeats, prewarm, Modal."""

from __future__ import annotations

import ast
import json
import tarfile
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

import server.orchestrator.executor as executor
from server.common.errors import SceneError
from server.contracts.api import HintData
from server.contracts.jobs import CommittedFile, RunRecord, StageContext, StageRecord, StageResult
from server.coverage.run import run as coverage_stage
from server.eval.geomscore import load_cameras
from server.orchestrator.api import dispatch_early, prewarm, start_run
from server.orchestrator.cli import main
from server.orchestrator.clock import Clock
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.early.capture_check import _axes, _in_frame, _publish
from server.orchestrator.heartbeat import Heartbeat
from server.orchestrator.modal_spawn import ModalPending, collect_modal
from server.orchestrator.services import bind, packet_sha256
from server.orchestrator.stateio import get_json
from server.orchestrator.stubs import coverage as coverage_stub
from server.orchestrator.stubs import criteria as criteria_stub
from server.orchestrator.stubs import objects as objects_stub
from server.orchestrator.stubs import poses as poses_stub
from server.orchestrator.stubs import result as result_stub
from server.orchestrator.stubs import scene as scene_stub
from server.orchestrator.stubs._common import json_bytes, ply, tracks, write_files
from server.orchestrator.tests.conftest import build_services
from server.reads.cache import put_json as put_storage_json
from server.reads.cache import still_key
from server.reads.types import StillResult

_FINALS = {"verdict_ready", "retake_request", "failed"}


def _events(services, capture_id="cap_test"):
    return services.state.events_after(capture_id, 0, limit=500)


def test_production_clock_advances_until_a_test_pins_it() -> None:
    clock = Clock()
    first = clock.now()
    time.sleep(0.02)
    assert clock.now() > first
    clock.advance(0)
    pinned = clock.now()
    time.sleep(0.02)
    assert clock.now() == pinned
    clock.advance(5)
    assert clock.now() == pinned + timedelta(seconds=5)


def test_point_in_front_of_the_camera_is_in_frame() -> None:
    pose = [0.0] * 16
    pose[0] = pose[5] = pose[10] = 1.0
    _origin, forward, right, up = _axes(pose)
    frame = {"intrinsics": [100.0, 100.0, 50.0, 50.0], "w": 100, "h": 100}
    assert _in_frame((0.0, 0.0, -3.0), right, up, forward, frame) is True
    assert _in_frame((0.0, 0.0, 3.0), right, up, forward, frame) is False


def _posed(path: str) -> CommittedFile:
    pose = [0.0] * 16
    pose[0] = pose[5] = pose[10] = 1.0
    return CommittedFile(
        path=path,
        role="keyframe",
        sha256="ab" * 32,
        bytes=8,
        generation="1",
        key=f"uploads/{path}",
        meta={"purpose": "meter_close", "pose": pose, "w": 100, "h": 100, "intrinsics": [100, 100, 50, 50]},
    )


def test_capture_check_reruns_after_debounce_with_every_commit(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    with bind(services):
        dispatch_early("cap_test", [_posed("a")])
        first = [event for event in _events(services) if event.type == "capture_check"]
        dispatch_early("cap_test", [_posed("b")])
        held = [event for event in _events(services) if event.type == "capture_check"]
        services.clock.advance(2)
        dispatch_early("cap_test", [_posed("c")])
        third = [event for event in _events(services) if event.type == "capture_check"]
    assert len(first) == 1
    assert len(held) == 1
    assert len(third) == 2
    blob = get_json(services.state, "early:cap_test:capture_check")
    assert blob["frames"] == 3


def test_replay_interval_commits_one_file_at_a_time(tmp_path, monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("server.orchestrator.cli.time.sleep", lambda seconds: sleeps.append(seconds))
    packet = {
        "captureId": "cap_replay",
        "flow": "guided",
        "keyframes": [{"id": "k0"}, {"id": "k1"}, {"id": "k2"}],
        "stills": [],
        "taps": [{"id": "meter", "element": "meter"}],
        "scaleReference": {"ext": {"doorOnMeterSide": False, "doorViews": []}},
    }
    root = tmp_path / "src"
    root.mkdir()
    (root / "packet.json").write_text(json.dumps(packet))
    tar_path = tmp_path / "mini.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(root / "packet.json", arcname="packet.json")
    out = tmp_path / "out"
    code = main(
        ["run-local", "--tar", str(tar_path), "--variant", "arkit", "--out", str(out), "--replay-interval", "0.5"]
    )
    assert code == 0
    assert sleeps == [0.5, 0.5]
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    checks = [event for event in events if event["type"] == "capture_check"]
    assert len(checks) >= 2


def test_reads_dispatch_uses_still_ref(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(still, storage, cfg):
        seen["still"] = still
        seen["storage"] = storage
        seen["cfg"] = cfg
        return StillResult(still=still, cost_usd=0.2), []

    monkeypatch.setattr("server.orchestrator.early.reads.read_still", fake)
    services, _uri, _digest = build_services(tmp_path, "memory")
    services.settings.VISION_EXTERNAL_CALLS = "on"
    item = CommittedFile(
        path="meter.png",
        role="still",
        sha256="cd" * 32,
        bytes=8,
        generation="1",
        key="uploads/meter.png",
        meta={"purpose": "meter_close"},
    )
    with bind(services):
        dispatch_early("cap_test", [item])
    still = seen["still"]
    assert still.capture_id == "cap_test"
    assert still.still_id == "meter"
    assert still.path == "uploads/meter.png"
    assert still.purpose == "meter_close"
    assert seen["storage"] is services.storage
    assert seen["cfg"].external_vision is True
    blob = get_json(services.state, "early:cap_test:reads:meter.png")
    assert blob["meter_number"] is None
    assert blob["cost_usd"] == 0.2


def test_validate_failure_with_usable_images_is_packet_invalid(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_fail_stage="validate:packet_invalid:2")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert run.failure_code == "packet_invalid"
    assert run.outcome_kind == "manual_review"
    assert services.state.get_capture("cap_test").status == "manual_review"


def test_validate_failure_with_unusable_images_fails_the_capture(tmp_path) -> None:
    services, uri, _digest = build_services(tmp_path, "memory", test_fail_stage="validate:packet_invalid:2")
    packet = json.loads(services.storage.get_bytes(uri))
    packet["imagesUsable"] = False
    raw = json.dumps(packet).encode()
    services.storage.put_bytes(uri, raw, "application/json")
    with bind(services):
        run = start_run("cap_test", packet_sha256(raw), uri)
    assert run.failure_code == "packet_invalid"
    assert run.outcome_kind is None
    assert services.state.get_capture("cap_test").status == "failed"


def test_missing_meter_tap_closes_manual_review_on_the_first_attempt(tmp_path, monkeypatch) -> None:
    """SD-97: orchestrated coverage with models=None and no meter tap does not retry."""
    monkeypatch.delenv("HSS_COVERAGE_METER_FRAMES", raising=False)
    monkeypatch.delenv("HSS_COVERAGE_ANCHOR_FROM_TAP", raising=False)

    def run(ctx: StageContext):
        assert ctx.models is None
        dense = ctx.work_dir / "dense"
        dense.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dense / "cloud.npz", points=np.zeros((1, 3), np.float32), src=np.zeros(1, np.int16))
        if not (dense / "dense_report.json").is_file():
            (dense / "dense_report.json").write_text("{}\n")
        return coverage_stage(ctx)

    monkeypatch.setattr(coverage_stub, "run", run)
    services, uri, _digest = build_services(tmp_path, "memory")
    packet = json.loads(services.storage.get_bytes(uri))
    packet["taps"] = [{"label": "reference_point", "keyframe": "k0", "pixel": [10, 20]}]
    raw = json.dumps(packet).encode()
    services.storage.put_bytes(uri, raw, "application/json")
    with bind(services):
        finished = start_run("cap_test", packet_sha256(raw), uri)
    assert finished.failure_code == "meter_tap_missing"
    assert finished.outcome_kind == "manual_review"
    assert services.state.get_capture("cap_test").status == "manual_review"
    record = services.state.get_stage(finished.run_id, "coverage")
    assert record is not None
    assert record.attempt == 1
    assert record.failures == 1
    assert record.status == "failed"
    assert record.error_code == "no_meter_anchor"
    coverage_events = [
        event for event in _events(services) if event.type == "stage" and event.data.get("stage") == "coverage"
    ]
    assert [event.data.get("status") for event in coverage_events] == ["running", "failed"]
    assert coverage_events[0].data.get("attempt") == 1


def test_dev_fixture_meter_reaches_coverage(tmp_path, monkeypatch) -> None:
    """A capture-level override is on the coverage context only in dev."""
    monkeypatch.delenv("HSS_COVERAGE_METER_FRAMES", raising=False)
    monkeypatch.delenv("HSS_COVERAGE_ANCHOR_FROM_TAP", raising=False)
    seen: dict = {}

    def run(ctx: StageContext):
        seen["models"] = ctx.models
        raise SceneError("no_meter_anchor", "stop after observing models")

    monkeypatch.setattr(coverage_stub, "run", run)
    services, uri, digest = build_services(tmp_path, "memory")
    services.state.put_kv(
        "capturex:cap_test",
        {"fixture_meter": {"meter_frames": "k0:10,20", "anchor_label": "reference_point"}},
    )
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert seen["models"] == {"meter_frames": "k0:10,20", "anchor_label": "reference_point"}
    assert finished.failure_code == "meter_tap_missing"
    record = services.state.get_stage(finished.run_id, "coverage")
    assert record is not None
    assert record.attempt == 1


def test_prod_ignores_fixture_meter_on_the_coverage_context(tmp_path, monkeypatch) -> None:
    """SD-97: the same stored override is not applied when the server is prod."""
    monkeypatch.delenv("HSS_COVERAGE_METER_FRAMES", raising=False)
    monkeypatch.delenv("HSS_COVERAGE_ANCHOR_FROM_TAP", raising=False)
    seen: dict = {}

    def run(ctx: StageContext):
        seen["models"] = ctx.models
        raise SceneError("no_meter_anchor", "stop after observing models")

    monkeypatch.setattr(coverage_stub, "run", run)
    services, uri, digest = build_services(tmp_path, "memory")
    services.settings = services.settings.model_copy(update={"SMP_ENVIRONMENT": "prod"})
    services.state.put_kv(
        "capturex:cap_test",
        {"fixture_meter": {"meter_frames": "k0:10,20", "anchor_label": "reference_point"}},
    )
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert seen["models"] is None
    assert finished.failure_code == "meter_tap_missing"
    record = services.state.get_stage(finished.run_id, "coverage")
    assert record is not None
    assert record.attempt == 1


def test_no_meter_model_finishes_as_no_meter_anchor(tmp_path, monkeypatch) -> None:
    def run(ctx: StageContext):
        return write_files(
            ctx,
            {
                "poses/cameras.json": poses_stub.cameras_bytes(),
                "poses/sparse.ply": ply(),
                "poses/tracks.npz": tracks(),
                "poses/registration_report.json": json_bytes(
                    {"status": "no_meter_model", "registered_fraction": 1.0, "actions": []}
                ),
                "poses/sparse/0/": b"stub",
            },
        )

    monkeypatch.setattr(poses_stub, "run", run)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert finished.status == "done"
    assert finished.failure_code == "no_meter_anchor"
    assert finished.outcome_kind == "manual_review"
    assert services.state.get_capture("cap_test").status == "manual_review"
    finals = [event for event in services.state.events_after("cap_test", 0, limit=500) if event.type in _FINALS]
    assert [event.type for event in finals] == ["verdict_ready"]


def _views_result(ctx: StageContext, *, both: bool) -> StageResult:
    events = [["retake_request", {"runId": ctx.run_id, "viewsNeeded": ["ground"], "memberActions": []}]]
    if both:
        events.insert(0, ["verdict_ready", {"runId": ctx.run_id, "kind": "needs_more_photos"}])
    body = {"status": "needs_views", "outcome": {"kind": "needs_more_photos"}}
    intent = {"captureId": ctx.capture_id, "events": events, "status": "needs_views", "deliverWebhook": False}
    (ctx.work_dir / "result").mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "result/publication.json").write_bytes(json_bytes(intent))
    return write_files(ctx, {"result/result.json": json_bytes(body)})


def _no_meter_poses(ctx: StageContext) -> StageResult:
    return write_files(
        ctx,
        {
            "poses/cameras.json": poses_stub.cameras_bytes(),
            "poses/sparse.ply": ply(),
            "poses/tracks.npz": tracks(),
            "poses/registration_report.json": json_bytes(
                {"status": "no_meter_model", "registered_fraction": 1.0, "actions": []}
            ),
            "poses/sparse/0/": b"stub",
        },
    )


def test_no_meter_with_views_stays_needs_views(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(poses_stub, "run", _no_meter_poses)
    monkeypatch.setattr(result_stub, "run", lambda ctx: _views_result(ctx, both=False))
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert finished.failure_code == "no_meter_anchor"
    assert finished.outcome_kind == "needs_more_photos"
    assert services.state.get_capture("cap_test").status == "needs_views"
    finals = [event.type for event in services.state.events_after("cap_test", 0, limit=500) if event.type in _FINALS]
    assert finals == ["retake_request"]


def test_no_meter_branch_rejects_two_final_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(poses_stub, "run", _no_meter_poses)
    monkeypatch.setattr(result_stub, "run", lambda ctx: _views_result(ctx, both=True))
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        start_run("cap_test", digest, uri)
    events = services.state.events_after("cap_test", 0, limit=500)
    finals = [event.type for event in events if event.type in _FINALS]
    # Reject the invalid intent before either final event can be emitted.
    assert finals == ["failed"]
    failed = next(event for event in events if event.type == "failed")
    assert failed.data["code"] == "stage_exhausted:result:result_no_event"
    assert services.state.get_capture("cap_test").status == "manual_review"


def test_degraded_views_keep_needs_views_and_one_final(tmp_path, monkeypatch) -> None:
    def objects(_ctx: StageContext) -> StageResult:
        raise ValueError("objects unavailable")

    monkeypatch.setattr(objects_stub, "run", objects)
    monkeypatch.setattr(result_stub, "run", lambda ctx: _views_result(ctx, both=False))
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert finished.failure_code == "degraded:objects"
    assert finished.outcome_kind == "needs_more_photos"
    assert services.state.get_capture("cap_test").status == "needs_views"
    finals = [event.type for event in services.state.events_after("cap_test", 0, limit=500) if event.type in _FINALS]
    assert finals == ["retake_request"]


def test_invalid_scene_is_not_skipped(tmp_path, monkeypatch) -> None:
    def run(ctx: StageContext):
        return write_files(
            ctx,
            {
                "scene/scene.json": json_bytes({"format": "not-a-scene"}),
                "scene/build_report.json": json_bytes({"status": "bad"}),
            },
        )

    monkeypatch.setattr(scene_stub, "run", run)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert finished.failure_code == "artifact_invalid:scene"
    assert services.state.get_capture("cap_test").status == "manual_review"


def test_invalid_verdict_is_rejected(tmp_path, monkeypatch) -> None:
    def run(ctx: StageContext):
        bad = json_bytes({"format": "not-a-verdict"})
        return write_files(
            ctx,
            {
                "verdicts/verdict.A.json": bad,
                "verdicts/verdict.B.json": bad,
                "verdicts/verdict.C.json": bad,
                "verdicts/verdict.S.json": bad,
                "verdicts/criteria_report.json": json_bytes({"status": "bad"}),
                "verdicts/placement.C.json": json_bytes({"profile": "C"}),
            },
        )

    monkeypatch.setattr(criteria_stub, "run", run)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        finished = start_run("cap_test", digest, uri)
    assert finished.failure_code == "artifact_invalid:criteria"


def test_heartbeat_repeats_while_the_stage_runs() -> None:
    class _State:
        def __init__(self) -> None:
            self.beats: list[str] = []

        def heartbeat(self, run_id: str, stage: str, call_id: str, substep: str, at: str) -> bool:
            self.beats.append(substep)
            return True

    state = _State()
    with Heartbeat(state, "run", "poses", "call", 0.05, Clock()):
        time.sleep(0.2)
    assert state.beats[0] == "start"
    assert state.beats.count("beat") >= 2


def test_executor_uses_configured_heartbeat_period(tmp_path, monkeypatch) -> None:
    seen: list[float] = []
    real = executor.run_stage

    def wrapped(*args, **kwargs):
        seen.append(kwargs["heartbeat_s"])
        return real(*args, **kwargs)

    monkeypatch.setattr(executor, "run_stage", wrapped)
    services, uri, digest = build_services(tmp_path, "memory", heartbeat_s=7)
    with bind(services):
        start_run("cap_test", digest, uri)
    assert seen
    assert set(seen) == {7}


def test_prewarm_uses_async_spawn_inside_a_running_loop(tmp_path, monkeypatch) -> None:
    import asyncio

    log: list[str] = []

    class _Spawn:
        def __call__(self) -> str:
            log.append("sync")
            return "fc-sync"

        async def aio(self) -> str:
            log.append("aio")
            return "fc-aio"

    def factory(name: str):
        class _Handle:
            warm = type("Warm", (), {"spawn": _Spawn()})()

        class _Cls:
            def __call__(self):
                return _Handle()

        del name
        return _Cls()

    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)
    monkeypatch.setattr("server.orchestrator.modal_functions.Pipeline", factory("pipeline"))
    monkeypatch.setattr("server.orchestrator.modal_functions.EarlyQa", factory("early_qa"))
    services, _uri, _digest = build_services(tmp_path, "memory")

    async def drive() -> None:
        with bind(services):
            prewarm("cap_async", tier="arkit")
        await asyncio.sleep(0)

    asyncio.run(drive())
    assert log == ["aio", "aio"]
    assert get_json(services.state, "prewarm:cap_async")["spawned"] == {"pipeline": True, "early_qa": True}


def test_prewarm_looks_up_the_deployed_app_inside_a_container(tmp_path, monkeypatch) -> None:
    import modal

    seen: list[tuple[str, str]] = []

    class _Spawn:
        def __call__(self) -> str:
            return "fc-sync"

        async def aio(self) -> str:
            return "fc-aio"

    class _Handle:
        warm = type("Warm", (), {"spawn": _Spawn()})()

    class _Cls:
        def __call__(self):
            return _Handle()

    def from_name(app_name: str, name: str):
        seen.append((app_name, name))
        return _Cls()

    monkeypatch.setenv("MODAL_TASK_ID", "ta-probe")
    monkeypatch.setenv("MODAL_APP_NAME", "hss-probe")
    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(from_name))
    services, _uri, _digest = build_services(tmp_path, "memory")
    with bind(services):
        prewarm("cap_lookup", tier="arkit")
    assert seen == [("hss-probe", "Pipeline"), ("hss-probe", "EarlyQa")]


def test_prewarm_spawns_pipeline_and_early_qa(tmp_path, monkeypatch) -> None:
    log: list[str] = []

    def factory(name: str):
        class _Method:
            def spawn(self) -> str:
                log.append(name)
                return "fc-1"

        class _Handle:
            warm = _Method()

        class _Cls:
            def __call__(self):
                return _Handle()

        return _Cls()

    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)
    monkeypatch.setattr("server.orchestrator.modal_functions.Pipeline", factory("pipeline"))
    monkeypatch.setattr("server.orchestrator.modal_functions.EarlyQa", factory("early_qa"))
    services, _uri, _digest = build_services(tmp_path, "memory")
    with bind(services):
        prewarm("cap_test", tier="arkit")
    blob = get_json(services.state, "prewarm:cap_test")
    assert blob["spawned"] == {"pipeline": True, "early_qa": True}
    assert log == ["pipeline", "early_qa"]


def test_run_modal_reports_available_when_the_sdk_is_present(monkeypatch, capsys) -> None:
    monkeypatch.setattr("server.orchestrator.cli.modal_available", lambda: True)
    assert main(["run-modal", "--env", "dev"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["modal"] == "available"
    assert body["remote"] == "supported"
    assert body["spawned"] is False
    assert body["reason"] == "a packet tar is required"
    assert body["env"] == "dev"


def test_a_failed_read_does_not_abort_the_early_fanout(tmp_path, monkeypatch) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    services.settings.VISION_EXTERNAL_CALLS = "on"
    services.clock.advance(0)
    started = services.clock.iso()

    def fake(still, storage, cfg):
        del storage, cfg
        services.clock.advance(41)
        if "zxing" in still.still_id:
            raise RuntimeError("zxingcpp is not installed")
        raise FileNotFoundError(still.path)

    monkeypatch.setattr("server.orchestrator.early.reads.read_still", fake)
    missing = CommittedFile(
        path="missing.png",
        role="still",
        sha256="ab" * 32,
        bytes=8,
        generation="1",
        key="uploads/missing.png",
        meta={"purpose": "meter_close"},
    )
    zxing = CommittedFile(
        path="zxing.png",
        role="still",
        sha256="cd" * 32,
        bytes=8,
        generation="1",
        key="uploads/zxing.png",
        meta={"purpose": "panel_close"},
    )
    with bind(services):
        dispatch_early("cap_test", [missing, zxing])
    missing_blob = get_json(services.state, "early:cap_test:reads:missing.png")
    zxing_blob = get_json(services.state, "early:cap_test:reads:zxing.png")
    assert missing_blob["started_at"] == started
    assert missing_blob["error"].startswith("FileNotFoundError")
    assert zxing_blob["started_at"] != started
    assert "zxingcpp is not installed" in zxing_blob["error"]
    assert get_json(services.state, "early:cap_test:qa:missing.png")["frame_id"] == "missing.png"
    assert get_json(services.state, "early:cap_test:qa:zxing.png")["frame_id"] == "zxing.png"
    checks = [event for event in _events(services) if event.type == "capture_check"]
    assert checks


def test_run_record_is_running_while_a_stage_runs(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}
    real = poses_stub.run

    def wrapped(ctx: StageContext):
        run = ctx.state.get_run(ctx.run_id)
        seen["status"] = run.status
        seen["started_at"] = run.started_at
        return real(ctx)

    monkeypatch.setattr(poses_stub, "run", wrapped)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        start_run("cap_test", digest, uri)
    assert seen["status"] == "running"
    assert seen["started_at"]


def test_timings_include_merge_objects_s(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    timings = json.loads(services.storage.get_bytes(run.artifacts["run/timings.json"]))
    assert isinstance(timings["merge_objects_s"], float)


def test_start_run_flushes_a_debounced_capture_check(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    services.clock.advance(0)
    with bind(services):
        dispatch_early("cap_test", [_posed("a")])
        dispatch_early("cap_test", [_posed("b")])
        before = [event for event in _events(services) if event.type == "capture_check"]
        start_run("cap_test", digest, uri)
        after = [event for event in _events(services) if event.type == "capture_check"]
    assert len(before) == 1
    assert len(after) >= len(before) + 2


def test_wall_gap_and_ground_hints_are_published_once(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    services.clock.advance(0)
    missing = ["wall_gap_left", "ground_not_seen_left", "ground_not_seen_right"]
    with bind(services):
        _publish(services, "cap_test", missing, meter_read="absent", frames=1)
        services.clock.advance(3)
        _publish(services, "cap_test", missing, meter_read="absent", frames=2)
    for code in missing:
        hints = [
            event for event in _events(services) if event.type == "hint" and (event.data or {}).get("code") == code
        ]
        assert len(hints) == 1


def test_read_hints_and_early_blobs_are_stored(tmp_path, monkeypatch) -> None:
    hint = HintData(code="meter_plate_unreadable", message="Take another close-up of the nameplate.")

    def fake(still, storage, cfg):
        del cfg
        result = StillResult(still=still, cost_usd=0.1)
        put_storage_json(storage, still_key(still.capture_id, still.still_id), result.model_dump(mode="json"))
        return result, [hint]

    monkeypatch.setattr("server.orchestrator.early.reads.read_still", fake)
    services, _uri, _digest = build_services(tmp_path, "memory")
    services.settings.VISION_EXTERNAL_CALLS = "on"
    item = CommittedFile(
        path="meter.png",
        role="still",
        sha256="cd" * 32,
        bytes=8,
        generation="1",
        key="uploads/meter.png",
        meta={"id": "s00001", "purpose": "meter_close"},
    )
    nested = CommittedFile(
        path="stills/s00002.jpg",
        role="still",
        sha256="ab" * 32,
        bytes=8,
        generation="1",
        key="captures/cap_test/stills/s00002.jpg",
        meta={"id": "s00002", "purpose": "panel_close"},
    )
    with bind(services):
        dispatch_early("cap_test", [item, nested])
    hints = [event for event in _events(services) if event.type == "hint"]
    assert hints[0].data["code"] == "meter_plate_unreadable"
    stored = json.loads(services.storage.get_bytes("captures/cap_test/early/reads/s00001.json"))
    assert StillResult.model_validate(stored).still.still_id == "s00001"
    summary = get_json(services.state, "early:cap_test:reads:meter.png")
    assert summary["hints"][0]["code"] == "meter_plate_unreadable"
    assert services.storage.exists("captures/cap_test/early/reads/s00002.json")
    assert not services.storage.exists("captures/cap_test/early/reads/stills/s00002.jpg.json")
    assert services.storage.exists("captures/cap_test/early/qa/meter.png.json")
    assert services.storage.exists("captures/cap_test/early/capture_check.json")


def test_poses_stub_cameras_json_is_the_ws04_list(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    raw = services.storage.get_bytes(run.artifacts["poses/cameras.json"])
    rows = json.loads(raw)
    assert isinstance(rows, list)
    assert rows[0]["pose_source"] == "arkit"
    path = tmp_path / "cameras.json"
    path.write_bytes(raw)
    loaded = load_cameras(path)
    assert set(loaded) == {"k0"}
    assert loaded["k0"]["w"] == 100


def test_pipeline_exposes_modal_methods() -> None:
    source = Path(__file__).resolve().parents[1] / "modal_functions.py"
    tree = ast.parse(source.read_text())
    found: dict[tuple[str, str], list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in {"Pipeline", "EarlyQa"}:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in {"warm", "run_stage"}:
                    found[(node.name, item.name)] = [ast.unparse(dec) for dec in item.decorator_list]
    assert "modal.method()" in found[("Pipeline", "warm")]
    assert "modal.method()" in found[("Pipeline", "run_stage")]
    assert "modal.method()" in found[("EarlyQa", "warm")]


def test_collect_modal_treats_a_poll_timeout_as_still_running(monkeypatch) -> None:
    class _Call:
        def get(self, timeout: float = 0) -> object:
            del timeout
            raise TimeoutError()

    monkeypatch.setattr("modal.FunctionCall.from_id", lambda call_id: _Call())
    with pytest.raises(ModalPending):
        collect_modal("fc-poll")


def test_coordinator_records_the_modal_call_and_collects_it(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    collected: list[str] = []

    def remote(run_id: str, stage: str, attempt: int, gpu: str | None) -> str:
        del run_id
        calls.append((stage, gpu))
        return f"fc-{stage}-{attempt}"

    def pending(call_id: str) -> object:
        collected.append(call_id)
        raise ModalPending(call_id)

    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)
    monkeypatch.setattr("server.orchestrator.modal_spawn._remote_spawn", remote)
    monkeypatch.setattr("server.orchestrator.coordinator.collect_modal", pending)
    services, uri, digest = build_services(
        tmp_path, "memory", stage_stubs=True, modal_remote=True, coordinator_loop_s=0
    )
    with bind(services):
        run = start_run("cap_test", digest, uri)
    record = services.state.get_stage(run.run_id, "validate")
    assert calls[0] == ("validate", None)
    assert record.worker_call_id == "fc-validate-1"
    assert record.status == "running"
    assert "fc-validate-1" in collected

    def finished(call_id: str) -> StageResult:
        collected.append(call_id)
        return StageResult(artifacts={"packet/validation.json": "kept"}, metrics={"duration_s": 0.01}, warnings=[])

    monkeypatch.setattr("server.orchestrator.coordinator.collect_modal", finished)
    with bind(services):
        RunCoordinator(services).tick(run.run_id)
    done = services.state.get_stage(run.run_id, "validate")
    assert done.status == "done"
    assert done.worker_call_id == "fc-validate-1"
    assert "fc-validate-1" in collected


def test_remote_stage_marks_itself_done(tmp_path) -> None:
    services, _uri, digest = build_services(tmp_path, "memory")
    now = services.clock.iso()
    run = RunRecord(
        run_id="run_remote",
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
    services.state.upsert_stage(
        StageRecord(run_id=run.run_id, stage="validate", status="pending", attempt=1, heartbeat=now)
    )
    with bind(services):
        artifacts = RunCoordinator(services).execute_named_stage(run.run_id, "validate", 1, gpu="")
    record = services.state.get_stage(run.run_id, "validate")
    assert record.status == "done"
    assert "packet/validation.json" in artifacts
