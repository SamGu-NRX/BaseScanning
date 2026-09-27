"""Round-2 validation fixes: clock, replay, frusta, outcomes, heartbeats, prewarm, Modal."""

from __future__ import annotations

import ast
import json
import tarfile
import time
from datetime import timedelta
from pathlib import Path

import server.orchestrator.executor as executor
from server.contracts.jobs import CommittedFile, StageContext
from server.eval.geomscore import load_cameras
from server.orchestrator.api import dispatch_early, prewarm, start_run
from server.orchestrator.cli import main
from server.orchestrator.clock import Clock
from server.orchestrator.early.capture_check import _axes, _in_frame
from server.orchestrator.heartbeat import Heartbeat
from server.orchestrator.services import bind, packet_sha256
from server.orchestrator.stateio import get_json
from server.orchestrator.stubs import criteria as criteria_stub
from server.orchestrator.stubs import poses as poses_stub
from server.orchestrator.stubs import scene as scene_stub
from server.orchestrator.stubs._common import json_bytes, ply, tracks, write_files
from server.orchestrator.tests.conftest import build_services
from server.reads.types import StillResult


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
    assert still.still_id == "meter.png"
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
    assert main(["run-modal", "--env", "dev"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["modal"] == "available"
    assert body["remote"] == "not_supported"
    assert body["reason"] == "remote execution isn't supported yet"
    assert "spawn" not in body
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


def test_coordinator_spawns_through_modal_when_configured(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def remote(ctx, gpu) -> None:
        calls.append((ctx.stage, gpu))

    monkeypatch.setattr("server.orchestrator.modal_functions.modal_available", lambda: True)
    monkeypatch.setattr("server.orchestrator.modal_spawn._remote_spawn", remote)
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=False)
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert calls[0] == ("validate", None)
    assert services.state.get_stage(run.run_id, "validate").status == "running"
