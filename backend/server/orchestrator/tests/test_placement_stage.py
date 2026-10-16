"""The placement stage: the repo's unchanged top-level solver behind the orchestrator.

Five claims, each tested against the real code and real bytes:

- The stage invokes the top-level `server/` solver — loaded by explicit absolute file
  path, never a flat import, which could resolve the eval vendor tree's same-named
  modules — on the run's own merged scene, converted by `scene_bridge.convert_scene`,
  and publishes a schema-valid artifact whose decision bytes are identical across runs.
  Only the solver's own `stats.elapsed_ms` may differ. The solved bytes are the
  converted capture scene, never a committed fixture.
- Dependency ordering: placement waits for `objects_3d` to be terminal as well as
  `scene` done, and `result` waits for placement to be terminal — the runtime graph and
  the `STAGE_DEPS` contract stay aligned.
- Refusals: a missing source scene or an objects_3d stage that did not finish (`done`
  with an explicit empty object list is valid) is a typed, non-retryable stage error —
  the stage degrades the run instead of implying no hazards — and writes no placement
  result.
- Object-merge precedence: the hazards in the merged source scene reach the wire
  geometry exactly once. The adapter treats `objects/objects_3d.json` as readiness
  evidence only; a same-id detector record still sitting in that artifact must not be
  merged a second time.
- `POST /v1/placements` still serves its existing contract from the same loaded code,
  and the backend's own `server` package is never shadowed by the flat names.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import server
from server.common.backends.storage_local import LocalFsStorage
from server.contracts.jobs import STAGE_DEPS, RunRecord, StageContext, StageRecord, StageResult
from server.orchestrator.cli import run_local
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.graph import STAGE_NAMES, STAGES, is_ready, ready_stages
from server.orchestrator.runner import error_parts, is_retryable
from server.orchestrator.services import bind, deadline_from
from server.orchestrator.stubs._common import scene_document
from server.orchestrator.tests.conftest import build_services
from server.placement import adapter
from server.placement.scene_bridge import convert_scene

REPO_ROOT = Path(__file__).resolve().parents[4]
TOP_SERVER = REPO_ROOT / "server"
RESULT_SCHEMA = json.loads((TOP_SERVER / "schemas/result.schema.json").read_text())
PACKET_DIR = REPO_ROOT / "backend/data/fixtures/packets/mini-arkit"

# The solver's own verdict for the committed fixtures, observed through the boundary.
EXPECTED_DECISION = "manual_review"


def _packet_tar(tmp_path: Path) -> Path:
    tar_path = tmp_path / "mini-arkit.tar"
    with tarfile.open(tar_path, "w") as archive:
        for path in sorted(PACKET_DIR.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(PACKET_DIR).as_posix())
    return tar_path


def _read_artifact(out: Path, run: dict, key: str) -> bytes:
    return LocalFsStorage(out / "store").get_bytes(run["artifacts"][key])


def _decision_bytes(payload: dict) -> bytes:
    """The decision projection: everything but the solver's wall-clock timing."""

    decision = json.loads(json.dumps(payload))
    decision["stats"].pop("elapsed_ms")
    return json.dumps(decision, sort_keys=True).encode()


def _hazard_document() -> dict:
    """A valid merged scene with one observed tap hazard beside the stub meter."""

    document = scene_document("cap_hazard")
    document["objects"].append(
        {
            "id": "h1",
            "class": "gas_meter",
            "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [2.0, 2.6], "bottom_m": 0.8, "top_m": 1.4},
            "confidence": 0.9,
            "plus_minus_m": 0.05,
            "source": {"kind": "tap"},
            "evidence": "observed",
        }
    )
    return document


class _StageRow:
    def __init__(self, status: str) -> None:
        self.status = status


class _State:
    """The one method the adapter reads: the run's objects_3d stage record."""

    def __init__(self, status: str | None = "done") -> None:
        self.status = status

    def get_stage(self, run_id: str, stage: str) -> _StageRow | None:
        return _StageRow(self.status) if self.status is not None else None


def _ctx(work_dir: Path, state: _State) -> types.SimpleNamespace:
    """The slice of StageContext the adapter body reads."""

    return types.SimpleNamespace(work_dir=work_dir, run_id="run_unit", state=state)


def _seed_inputs(work_dir: Path, scene: dict, objects_3d: dict | None) -> None:
    (work_dir / "scene").mkdir(parents=True, exist_ok=True)
    (work_dir / "scene" / "scene.json").write_bytes(json.dumps(scene, sort_keys=True).encode())
    if objects_3d is not None:
        (work_dir / "objects").mkdir(parents=True, exist_ok=True)
        (work_dir / "objects" / "objects_3d.json").write_bytes(json.dumps(objects_3d, sort_keys=True).encode())


def test_boundary_loads_the_top_level_server_by_explicit_path(monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_MODULES", None)
    foreign = types.ModuleType("rules")  # what a vendor-tree import may have left behind
    monkeypatch.setitem(sys.modules, "rules", foreign)
    modules = adapter.load_solver()

    for name in adapter._FLAT_STAGE:
        assert Path(modules[name].__file__) == TOP_SERVER / f"{name}.py"
    assert sys.modules["rules"] is foreign  # restored: the vendor tree keeps its names
    assert adapter.load_solver() is modules  # one load per process
    # The backend's own `server` package is untouched by the flat-name swap.
    assert "backend" in Path(server.__file__).parts


def test_typed_codes_reach_the_runner_seam() -> None:
    scene_error = adapter.PlacementSceneError("not a scene")
    assert error_parts(scene_error) == ("placement_scene_invalid", "not a scene")
    assert not is_retryable(scene_error)  # the bytes do not heal on a retry
    boundary_error = adapter.PlacementBoundaryError("missing tree")
    assert error_parts(boundary_error) == ("placement_boundary_missing", "missing tree")
    assert not is_retryable(boundary_error)
    missing = adapter.PlacementInputError("scene/scene.json is missing")
    assert error_parts(missing) == ("placement_scene_input_missing", "scene/scene.json is missing")
    assert not is_retryable(missing)
    objects = adapter.PlacementObjectsError("objects_3d stage status is 'failed'")
    assert error_parts(objects) == ("placement_objects_3d_unavailable", "objects_3d stage status is 'failed'")
    assert not is_retryable(objects)


def test_missing_solver_tree_raises_the_typed_boundary_error(tmp_path, monkeypatch) -> None:
    """Modal images ship only the backend package, so there the loader refuses with the
    typed boundary error on the first attempt and the optional stage degrades instead
    of retrying."""

    monkeypatch.setattr(adapter, "_MODULES", None)
    monkeypatch.setattr(adapter, "_REPO_ROOT", tmp_path)
    with pytest.raises(adapter.PlacementBoundaryError, match="Modal images ship only the backend package"):
        adapter.load_solver()
    boundary = adapter.PlacementBoundaryError(str(tmp_path / "server"))
    assert error_parts(boundary)[0] == "placement_boundary_missing"
    assert not is_retryable(boundary)


def test_dependency_ordering_is_aligned_across_graph_and_contract() -> None:
    """Placement needs the merged scene (scene done, objects_3d terminal); result
    needs placement terminal. The runtime graph and STAGE_DEPS say the same thing."""

    assert STAGES["placement"].deps == (("scene", "done"), ("objects_3d", "terminal"))
    assert STAGE_DEPS["placement"] == ("scene", "objects_3d")
    assert STAGES["result"].deps == (("criteria", "done"), ("placement", "terminal"))
    assert STAGE_DEPS["result"] == ("criteria", "placement")

    status = {name: "pending" for name in STAGE_NAMES}
    status["scene"] = "done"
    status["objects_3d"] = "running"
    assert "placement" not in ready_stages(status), "objects_3d still running"
    status["objects_3d"] = "done"
    assert is_ready(STAGES["placement"], status)
    status["placement"] = "running"
    status["criteria"] = "done"
    assert not is_ready(STAGES["result"], status), "result waits for a terminal placement"
    status["placement"] = "failed"
    assert is_ready(STAGES["result"], status), "a failed placement is terminal and must not block the run"


def test_run_local_solves_the_captures_merged_scene_deterministically(tmp_path) -> None:
    tar_path = _packet_tar(tmp_path)
    outs: list[Path] = []
    for name in ("run-a", "run-b"):
        out = tmp_path / name
        assert run_local(tar_path, "arkit-nonlidar", out, 0.0) == 0
        outs.append(out)

    runs = [json.loads((out / "run.json").read_text()) for out in outs]
    placement = [_read_artifact(out, run, "placement/placement.json") for out, run in zip(outs, runs, strict=True)]
    scenes = [_read_artifact(out, run, "placement/scene.json") for out, run in zip(outs, runs, strict=True)]
    payloads = [json.loads(item) for item in placement]
    for out, run, payload, scene_bytes in zip(outs, runs, payloads, scenes, strict=True):
        Draft202012Validator(RESULT_SCHEMA).validate(payload)
        # The solved bytes are this capture's merged scene, converted — not a fixture.
        source = json.loads(_read_artifact(out, run, "scene/scene.json"))
        expected = json.dumps(convert_scene(source), sort_keys=True, allow_nan=False).encode()
        assert scene_bytes == expected
        assert payload["stats"]["input_sha256"] == hashlib.sha256(scene_bytes).hexdigest()
        assert payload["decision"] == EXPECTED_DECISION
        assert "placement" not in run["degraded"]
        assert run["status"] == "done"
        # The source scene artifact is untouched by the stage: still the merged
        # metres document, not the stage's converted feet bytes.
        assert json.loads(_read_artifact(out, run, "scene/scene.json")) == source
        assert scene_bytes != _read_artifact(out, run, "scene/scene.json")

    stages = {rec["stage"]: rec for rec in json.loads(_read_artifact(outs[0], runs[0], "run/stages.json"))}
    assert stages["placement"]["status"] == "done"
    assert stages["placement"]["error_code"] is None
    assert stages["placement"]["metrics"]["elapsed_ms"] > 0

    events = [json.loads(line) for line in (outs[0] / "events.jsonl").read_text().splitlines()]
    marks = [(event["data"].get("stage"), event["data"].get("status")) for event in events if event["type"] == "stage"]
    assert (
        marks.index(("objects_3d", "done")) < marks.index(("placement", "running")) < marks.index(("placement", "done"))
    )
    # Criteria and placement both follow the merge and run in parallel; the result
    # stage is the one that must wait for a terminal placement.
    assert marks.index(("scene", "done")) < marks.index(("placement", "done")) < marks.index(("result", "running"))
    finals = [event for event in events if event["type"] in {"verdict_ready", "retake_request", "failed"}]
    assert [event["type"] for event in finals] == ["verdict_ready"]

    # Determinism: identical decision bytes across runs; only elapsed_ms may differ.
    assert _decision_bytes(payloads[0]) == _decision_bytes(payloads[1])


def test_malformed_source_scene_refuses_at_the_adapter(tmp_path) -> None:
    """Malformed bytes on the hydrated input are a typed refusal with no output."""

    work = tmp_path / "work"
    work.mkdir()
    _seed_inputs(work, _hazard_document(), {"format": "hss-objects-3d/v0", "objects": []})
    (work / "scene" / "scene.json").write_bytes(b"{ not json")

    with pytest.raises(adapter.PlacementSceneError):
        adapter.run(_ctx(work, _State()))
    assert not (work / "placement" / "placement.json").exists()
    assert not (work / "placement" / "scene.json").exists()


def test_unsupported_source_scene_is_recorded_stage_data(tmp_path, monkeypatch) -> None:
    """A scene that validates as scene v0 but sits outside the bridge's bounded scope —
    a hazard with generated evidence — is a typed non-retryable placement error on the
    existing conservative path: the stage fails, the run degrades to manual_review, and
    no placement artifact is emitted."""

    from server.orchestrator.stubs import scene as scene_stub
    from server.orchestrator.stubs._common import json_bytes, scene_document, write_files

    def unsupported_scene(ctx: StageContext) -> StageResult:
        scene = scene_document(ctx.capture_id)
        scene["objects"].append(
            {
                "id": "h1",
                "class": "gas_meter",
                "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [2.0, 2.6], "bottom_m": 0.8, "top_m": 1.4},
                "confidence": 0.9,
                "plus_minus_m": 0.05,
                "source": {"kind": "vlm"},
                "evidence": "generated",
            }
        )
        return write_files(
            ctx,
            {
                "scene/scene.json": json_bytes(scene),
                "scene/build_report.json": json.dumps({"status": "stub"}).encode(),
            },
        )

    monkeypatch.setattr(scene_stub, "run", unsupported_scene)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = RunRecord(
            run_id="run_placefail",
            capture_id="cap_test",
            packet_sha256=digest,
            status="pending",
            branch="image",
            flow="guided",
            tier="arkit",
            profile_default=services.settings.RULES_PROFILE_DEFAULT,
            created_at=services.clock.iso(),
            deadline_at=deadline_from(services.clock, services.settings.HARD_STOP_MIN),
            artifacts={},
        )
        services.state.put_run(run)
        now = services.clock.iso()
        for name in STAGE_NAMES:
            services.state.upsert_stage(
                StageRecord(run_id=run.run_id, stage=name, status="pending", attempt=1, heartbeat=now)
            )
        RunCoordinator(services).drive(run.run_id)

        finished = services.state.get_run(run.run_id)
        record = services.state.get_stage(run.run_id, "placement")
        assert record.status == "failed"
        assert record.error_code == "placement_scene_unsupported"
        assert record.error.startswith("placement_scene_unsupported:")
        assert finished.degraded == ["placement"]
        assert finished.status == "done"
        assert finished.outcome_kind == "manual_review"
        assert finished.failure_code == "degraded:placement"
        assert "placement/placement.json" not in finished.artifacts
        assert "placement/scene.json" not in finished.artifacts
        assert services.state.get_capture("cap_test").status == "manual_review"

        events = services.state.events_after("cap_test", 0, limit=500)
        failed = [
            event
            for event in events
            if event.type == "stage" and event.data.get("stage") == "placement" and event.data.get("status") == "failed"
        ]
        assert [event.data.get("errorCode") for event in failed] == ["placement_scene_unsupported"]
        marks = [(event.data.get("stage"), event.data.get("status")) for event in events if event.type == "stage"]
        assert marks.index(("objects_3d", "done")) < marks.index(("placement", "failed"))
        finals = [event for event in events if event.type in {"verdict_ready", "retake_request", "failed"}]
        assert len(finals) == 1


def test_missing_source_scene_refuses_and_writes_no_result(tmp_path) -> None:
    """No merged scene in the run, no fallback: a typed refusal, no output bytes."""

    work = tmp_path / "work"
    work.mkdir()
    _seed_inputs(work, _hazard_document(), {"format": "hss-objects-3d/v0", "objects": []})
    (work / "scene" / "scene.json").unlink()

    with pytest.raises(adapter.PlacementInputError) as excinfo:
        adapter.run(_ctx(work, _State()))
    assert "scene/scene.json" in str(excinfo.value)
    assert not (work / "placement" / "placement.json").exists()
    assert not (work / "placement" / "scene.json").exists()


def test_objects_3d_not_done_refuses_instead_of_implying_no_hazards(tmp_path) -> None:
    """failed, skipped, missing records and absent artifacts all refuse. A successful
    explicit empty list is valid — that is the one shape that may read as no hazards."""

    work = tmp_path / "work"
    work.mkdir()
    _seed_inputs(work, _hazard_document(), {"format": "hss-objects-3d/v0", "objects": []})

    for status in ("failed", "skipped", "running"):
        with pytest.raises(adapter.PlacementObjectsError, match=status):
            adapter.run(_ctx(work, _State(status)))
    with pytest.raises(adapter.PlacementObjectsError):
        adapter.run(_ctx(work, _State(None)))  # no stage record at all

    assert not (work / "placement" / "placement.json").exists()

    # Readiness evidence must be the declared format.
    (work / "objects" / "objects_3d.json").write_bytes(json.dumps({"format": "hss-objects/v9", "objects": []}).encode())
    with pytest.raises(adapter.PlacementObjectsError, match="hss-objects-3d/v0"):
        adapter.run(_ctx(work, _State()))

    # The valid gate: done stage, declared format, explicit empty list.
    (work / "objects" / "objects_3d.json").write_bytes(
        json.dumps({"format": "hss-objects-3d/v0", "objects": []}).encode()
    )
    result = adapter.run(_ctx(work, _State()))
    assert (work / "placement" / "placement.json").is_file()
    assert result.metrics["elapsed_ms"] > 0


def test_merged_hazards_convert_once_and_never_merge_again(tmp_path) -> None:
    """The observed tap hazard appears once in the source scene and once in the
    converted geometry — even with a same-id detector record still sitting in the
    objects_3d artifact. A second merge would duplicate it."""

    scene = _hazard_document()
    assert [obj["id"] for obj in scene["objects"]].count("h1") == 1
    # What a detector replacement would have produced, still in the readiness artifact.
    objects_3d = {
        "format": "hss-objects-3d/v0",
        "objects": [
            {
                "id": "h1",
                "label": "gas_meter",
                "s_m": [2.0, 2.6],
                "bottom_m": 0.8,
                "top_m": 1.4,
                "confidence": 0.7,
                "source": {"kind": "vlm"},
            }
        ],
    }
    work = tmp_path / "work"
    work.mkdir()
    _seed_inputs(work, scene, objects_3d)

    adapter.run(_ctx(work, _State()))
    converted = json.loads((work / "placement" / "scene.json").read_bytes())
    hazards = converted.get("objects", [])
    assert [obj["type"] for obj in hazards] == ["gas_meter"]
    assert len(hazards) == 1, "the same-id detector object was merged a second time"
    span = hazards[0]["span_ft"]
    assert span == [pytest.approx(2.0 / 0.3048), pytest.approx(2.6 / 0.3048)]
    assert hazards[0]["conf"] == 0.9, "the merged tap evidence wins, not the detector record"


def test_distinct_source_scenes_distinct_bytes_and_same_input_is_deterministic(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    _seed_inputs(plain, scene_document("cap_plain"), {"format": "hss-objects-3d/v0", "objects": []})
    result_plain = adapter.run(_ctx(plain, _State()))
    payload_plain = json.loads((plain / "placement" / "placement.json").read_bytes())

    hazard = tmp_path / "hazard"
    hazard.mkdir()
    _seed_inputs(hazard, _hazard_document(), {"format": "hss-objects-3d/v0", "objects": []})
    result_hazard = adapter.run(_ctx(hazard, _State()))
    payload_hazard = json.loads((hazard / "placement" / "placement.json").read_bytes())

    # Distinct source scenes → distinct converted inputs, hashes and outputs.
    scene_plain = (plain / "placement" / "scene.json").read_bytes()
    scene_hazard = (hazard / "placement" / "scene.json").read_bytes()
    assert scene_plain != scene_hazard
    assert payload_plain["stats"]["input_sha256"] == hashlib.sha256(scene_plain).hexdigest()
    assert payload_hazard["stats"]["input_sha256"] == hashlib.sha256(scene_hazard).hexdigest()
    assert payload_plain["stats"]["input_sha256"] != payload_hazard["stats"]["input_sha256"]
    assert result_plain.artifacts.keys() == result_hazard.artifacts.keys()
    assert _decision_bytes(payload_plain) != _decision_bytes(payload_hazard), "the hazard changes the checks"

    # Same input again: identical decision bytes, only the solver's timing may move.
    again = adapter.run(_ctx(plain, _State()))
    payload_again = json.loads((plain / "placement" / "placement.json").read_bytes())
    assert again.artifacts == result_plain.artifacts
    assert _decision_bytes(payload_again) == _decision_bytes(payload_plain)


def test_v1_placements_contract_stays_live() -> None:
    modules = adapter.load_api()
    assert Path(modules["api"].__file__) == TOP_SERVER / "api.py"
    fixture = TOP_SERVER / "tests/fixtures/example-scene.json"
    with TestClient(modules["api"].app) as client:
        response = client.post(
            "/v1/placements",
            content=fixture.read_bytes(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(RESULT_SCHEMA).validate(body)
    assert body["decision"] == EXPECTED_DECISION
    assert body["stats"]["input_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
