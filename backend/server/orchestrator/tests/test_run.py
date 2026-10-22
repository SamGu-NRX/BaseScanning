"""State machine, sweeper, retries and events on both backends."""

from __future__ import annotations

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from server.common.backends.storage_local import LocalFsStorage
from server.contracts.api import HintData
from server.contracts.capture_check import HintCode
from server.contracts.jobs import StageName
from server.orchestrator.api import start_run
from server.orchestrator.artifacts import ReadThrough
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.services import bind, packet_sha256, run_id_for
from server.orchestrator.stateio import get_json, put_json
from server.orchestrator.stubs._common import json_bytes, write_files
from server.orchestrator.tests.conftest import build_services

_SYNTHETIC_DOOR = {
    "id": "synthetic-door",
    "class": "door",
    "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.0, 2.0], "bottom_m": 0.0, "top_m": 2.1},
    "confidence": 1,
    "plus_minus_m": 0.02,
    "source": {"kind": "reconstruction"},
    "evidence": "observed",
}


def _events(services, capture_id="cap_test"):
    return services.state.events_after(capture_id, 0, limit=500)


def _stage(services, name: StageName, run_id: str):
    return services.state.get_stage(run_id, name)


def test_stub_run_partial_order_and_artifacts(services) -> None:
    built, uri, digest = services
    run = start_run("cap_test", digest, uri)
    assert run.status == "done"
    names = [(event.data.get("stage"), event.data.get("status")) for event in _events(built) if event.type == "stage"]
    validate_done = next(i for i, item in enumerate(names) if item == ("validate", "done"))
    parallel = [i for i, item in enumerate(names) if item[0] in {"poses", "objects", "reads"} and item[1] == "running"]
    assert validate_done < min(parallel)

    def done_at(stage: str) -> int:
        return next(i for i, item in enumerate(names) if item == (stage, "done"))

    order = ["scale", "dense", "coverage", "scene", "objects_3d", "criteria", "result"]
    indexes = [done_at(stage) for stage in order]
    assert indexes == sorted(indexes)
    finals = [event for event in _events(built) if event.type in {"verdict_ready", "retake_request", "failed"}]
    assert len(finals) == 1
    assert finals[0].type == "verdict_ready"
    capture = built.state.get_capture("cap_test")
    assert capture.status in {"complete", "needs_views", "manual_review"}
    for key in (
        "packet/validation.json",
        "poses/cameras.json",
        "scale/scale.json",
        "scene/scene.json",
        "verdicts/verdict.C.json",
        "result/result.json",
        "run/timings.json",
        "run/cost.json",
        "run/stages.json",
    ):
        assert key in run.artifacts
        assert built.storage.exists(run.artifacts[key])
    timings = json.loads(built.storage.get_bytes(run.artifacts["run/timings.json"]))
    assert "enter_s" in timings["validate"]
    internal = json.loads(built.storage.get_bytes(run.artifacts["result/result.internal.json"]))
    verdict = built.storage.get_bytes(run.artifacts["verdicts/verdict.C.json"])
    assert internal["verdict_c_sha256"] == __import__("hashlib").sha256(verdict).hexdigest()


class _CountingStorage:
    def __init__(self, inner: LocalFsStorage) -> None:
        self._inner = inner
        self.gets: list[str] = []

    def get_bytes(self, key: str) -> bytes:
        self.gets.append(key)
        return self._inner.get_bytes(key)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def test_run_id_changes_with_one_byte(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    raw = services.storage.get_bytes(uri)
    assert run_id_for(raw, "cap_test") != "run_" + digest[:16]
    assert run_id_for(raw, "cap_a") != run_id_for(raw, "cap_b")
    assert run_id_for(raw, "cap_a") != run_id_for(raw + b" ", "cap_a")
    assert packet_sha256(raw) != packet_sha256(raw + b" ")


def test_identical_packets_keep_separate_runs(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    raw = services.storage.get_bytes(uri)
    other_id = "cap_other"
    other_uri = f"captures/{other_id}/packet.json"
    services.storage.put_bytes(other_uri, raw, "application/json")
    original = services.state.get_capture("cap_test")
    services.state.put_capture(original.model_copy(update={"capture_id": other_id, "packet_id": "packet-other"}))
    with bind(services):
        left = start_run("cap_test", digest, uri)
        right = start_run(other_id, digest, other_uri)

    assert left.run_id != right.run_id
    assert services.state.get_run(left.run_id).capture_id == "cap_test"
    assert services.state.get_run(right.run_id).capture_id == other_id
    assert [run.run_id for run in services.state.runs_for_capture("cap_test")] == [left.run_id]
    assert [run.run_id for run in services.state.runs_for_capture(other_id)] == [right.run_id]

    left_stage = services.state.get_stage(left.run_id, "result")
    right_stage = services.state.get_stage(right.run_id, "result")
    assert left_stage is not None and right_stage is not None
    assert left_stage.status == "done" and right_stage.status == "done"
    services.state.upsert_stage(left_stage.model_copy(update={"status": "failed", "error_code": "probe"}))
    assert services.state.get_stage(left.run_id, "result").status == "failed"
    assert services.state.get_stage(right.run_id, "result").status == "done"

    left_control = get_json(services.state, f"run_control:{left.run_id}")
    right_control = get_json(services.state, f"run_control:{right.run_id}")
    assert left_control["merge_done"] is True
    assert right_control["merge_done"] is True
    left_control["cancel_requested"] = True
    put_json(services.state, f"run_control:{left.run_id}", left_control)
    assert get_json(services.state, f"run_control:{right.run_id}")["cancel_requested"] is False

    left_key = left.artifacts["result/result.json"]
    right_key = right.artifacts["result/result.json"]
    assert left_key != right_key
    assert json.loads(services.storage.get_bytes(left_key))["runId"] == left.run_id
    assert json.loads(services.storage.get_bytes(right_key))["runId"] == right.run_id
    services.storage.put_bytes(left_key, b'{"runId":"tampered"}', "application/json")
    assert json.loads(services.storage.get_bytes(right_key))["runId"] == right.run_id

    left_final = [event.run_id for event in _events(services) if event.type == "verdict_ready"]
    right_final = [event.run_id for event in _events(services, other_id) if event.type == "verdict_ready"]
    assert left_final == [left.run_id]
    assert right_final == [right.run_id]


def _object_ids(data: bytes) -> list[str]:
    return [obj["id"] for obj in json.loads(data)["objects"]]


@pytest.mark.parametrize("backend", ["memory", "modal_dict"])
def test_merged_scene_reaches_later_stages(tmp_path, monkeypatch, backend: str) -> None:
    def publish_door(ctx):
        body = {"format": "hss-objects-3d/v0", "objects": [_SYNTHETIC_DOOR]}
        return write_files(ctx, {"objects/objects_3d.json": json_bytes(body)})

    monkeypatch.setattr("server.orchestrator.stubs.objects_3d.run", publish_door)
    services, uri, digest = build_services(tmp_path, backend)
    with bind(services):
        run = start_run("cap_test", digest, uri)
    stored = _object_ids(services.storage.get_bytes(run.artifacts["scene/scene.json"]))
    assert stored == ["meter", "synthetic-door"]
    for stage in ("criteria", "result"):
        attempt = services.work_root / run.run_id / stage / "attempt-1"
        paths = list(attempt.glob("*/scene/scene.json"))
        assert len(paths) == 1
        assert _object_ids(paths[0].read_bytes()) == stored


def test_inline_stages_read_the_packet_once(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    counter = _CountingStorage(services.storage)
    services.storage = counter
    with bind(services):
        start_run("cap_test", digest, uri)
    assert counter.gets.count(uri) == 1


def test_read_through_does_not_cache_a_miss(tmp_path) -> None:
    storage = LocalFsStorage(tmp_path)
    reads = ReadThrough(storage)
    assert reads.exists("missing") is False
    storage.put_bytes("missing", b"now", "text/plain")
    assert reads.exists("missing") is True
    assert reads.get_bytes("missing") == b"now"
    storage.put_bytes("missing", b"later", "text/plain")
    assert reads.get_bytes("missing") == b"now"


def test_second_start_is_idempotent(services) -> None:
    built, uri, digest = services
    first = start_run("cap_test", digest, uri)
    before = len(_events(built))
    second = start_run("cap_test", digest, uri)
    assert second.run_id == first.run_id
    assert len(_events(built)) == before
    assert len(built.state.runs_for_capture("cap_test")) == 1


def test_staleness_boundary_and_respawn(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_hang_stage="poses:1")
    with bind(services):
        run = start_run("cap_test", digest, uri)
        assert _stage(services, "poses", run.run_id).status == "running"
        services.clock.advance(179)
        RunCoordinator(services).tick(run.run_id)
        assert _stage(services, "poses", run.run_id).respawns == 0
        services.clock.advance(2)
        RunCoordinator(services).drive(run.run_id)
        poses = _stage(services, "poses", run.run_id)
        assert poses.status == "done"
        assert poses.respawns == 1
        assert poses.gpu == "L4"
        finals = [event for event in _events(services) if event.type == "verdict_ready"]
        assert len(finals) == 1


def test_escalation_then_exhausted(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_fail_stage="poses:gpu_oom:2")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    poses = _stage(services, "poses", run.run_id)
    assert poses.status == "done"
    assert poses.gpu == "A100-40GB"
    assert poses.failures == 2
    failed = build_services(tmp_path / "again", "memory", test_fail_stage="poses:gpu_oom:3")
    with bind(failed[0]):
        stopped = start_run("cap_test", failed[2], failed[1])
    assert stopped.failure_code == "stage_exhausted:poses:gpu_oom"
    assert failed[0].state.get_capture("cap_test").status == "manual_review"
    kinds = [event.type for event in _events(failed[0]) if event.type == "failed"]
    assert kinds == ["failed"]


def test_hard_stop_cancels_worker(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_hang_stage="dense:1,2,3", hard_stop_min=4)
    with bind(services):
        run = start_run("cap_test", digest, uri)
        assert _stage(services, "dense", run.run_id).status == "running"
        services.clock.advance(4 * 60 + 1)
        RunCoordinator(services).tick(run.run_id)
    stored = services.state.get_run(run.run_id)
    assert stored.failure_code == "hard_stop"
    assert services.state.get_capture("cap_test").status == "manual_review"
    assert services.executor.alive(_stage(services, "dense", run.run_id).worker_call_id or "") is False


def test_sweeper_reattaches_after_coordinator_death(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_kill_coordinator_after="poses")
    with bind(services):
        run = start_run("cap_test", digest, uri)
        call_id = _stage(services, "poses", run.run_id).worker_call_id
        assert _stage(services, "poses", run.run_id).status == "running"
        services.clock.advance(181)
        from server.orchestrator.api import sweep

        assert sweep() >= 1
    poses = _stage(services, "poses", run.run_id)
    assert poses.worker_call_id == call_id
    control = get_json(services.state, f"run_control:{run.run_id}")
    assert control["coordinator_respawns"] == 1
    finished = services.state.get_run(run.run_id)
    assert finished.status == "done"


def test_hint_code_is_closed() -> None:
    HintData(code="frames_blurry", message="hold still")
    with pytest.raises(ValidationError):
        HintData(code="too_few_keyframes", message="nope")
    codes = get_args(HintCode)
    assert "wall_left_of_meter" in codes
    assert "ground_downward_w1" not in codes


def test_failed_attempt_does_not_upload(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory", test_fail_stage="poses:gpu_oom:1")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    poses = _stage(services, "poses", run.run_id)
    assert poses.failures == 1
    assert poses.status == "done"
    body = services.storage.get_bytes(run.artifacts["poses/cameras.json"])
    rows = json.loads(body)
    assert isinstance(rows, list) and rows
