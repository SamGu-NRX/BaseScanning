"""The placement stage: the repo's unchanged top-level solver behind the orchestrator.

Three claims, each tested against the real code and real bytes:

- The stage invokes the top-level `server/` solver — loaded by explicit absolute file
  path, never a flat import, which could resolve the eval vendor tree's same-named
  modules — on a committed scene fixture and publishes a schema-valid artifact whose
  decision bytes are identical across runs. Only the solver's own `stats.elapsed_ms`
  may differ: the result schema requires that field, so determinism is asserted on the
  decision projection without it.
- A malformed scene is recorded stage data: the stage record carries the typed
  `placement_scene_invalid` code, the failed stage event is emitted, and the optional
  stage degrades the run (`degraded:placement`, capture `manual_review`) instead of
  failing it.
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
from server.contracts.jobs import RunRecord, StageRecord
from server.orchestrator.cli import run_local
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.runner import error_parts, is_retryable
from server.orchestrator.services import bind, deadline_from
from server.orchestrator.stateio import put_json
from server.orchestrator.tests.conftest import build_services
from server.placement import adapter

REPO_ROOT = Path(__file__).resolve().parents[4]
TOP_SERVER = REPO_ROOT / "server"
FIXTURE = TOP_SERVER / "tests/fixtures/example-scene.json"
REFERENCE = Path(adapter.__file__).resolve().parent / "reference_scene.json"
RESULT_SCHEMA = json.loads((TOP_SERVER / "schemas/result.schema.json").read_text())
PACKET_DIR = REPO_ROOT / "backend/data/fixtures/packets/mini-arkit"

# The solver's own verdict for the committed fixture, observed through the boundary.
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


def test_reference_scene_is_the_top_level_fixture() -> None:
    """The vendored input is a byte-identical copy of the fixture the task names."""

    assert REFERENCE.read_bytes() == FIXTURE.read_bytes()


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


def test_run_local_places_the_reference_scene_deterministically(tmp_path) -> None:
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
    fixture_sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    for run, payload, scene_bytes in zip(runs, payloads, scenes, strict=True):
        Draft202012Validator(RESULT_SCHEMA).validate(payload)
        assert payload["decision"] == EXPECTED_DECISION
        assert payload["stats"]["input_sha256"] == fixture_sha
        assert scene_bytes == FIXTURE.read_bytes()  # the stage solved exactly the committed fixture
        assert "placement" not in run["degraded"]
        assert run["status"] == "done"

    stages = {rec["stage"]: rec for rec in json.loads(_read_artifact(outs[0], runs[0], "run/stages.json"))}
    assert stages["placement"]["status"] == "done"
    assert stages["placement"]["error_code"] is None
    assert stages["placement"]["metrics"]["elapsed_ms"] > 0

    events = [json.loads(line) for line in (outs[0] / "events.jsonl").read_text().splitlines()]
    marks = [(event["data"].get("stage"), event["data"].get("status")) for event in events if event["type"] == "stage"]
    assert marks.index(("scene", "done")) < marks.index(("placement", "done")) < marks.index(("criteria", "done"))
    finals = [event for event in events if event["type"] in {"verdict_ready", "retake_request", "failed"}]
    assert [event["type"] for event in finals] == ["verdict_ready"]

    # Determinism: identical decision bytes across runs; only elapsed_ms may differ.
    assert _decision_bytes(payloads[0]) == _decision_bytes(payloads[1])


def test_malformed_scene_is_recorded_stage_data(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        key = "captures/cap_test/placement/scene.json"
        services.storage.put_bytes(key, b"{ not json", "application/json")
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
            artifacts={"placement/scene.json": key},
        )
        services.state.put_run(run)
        # Persist the input the way the orchestrator persists stage artifacts: the
        # per-stage map is what overlay_stage_artifacts recovers, because a parallel
        # stub stage's put_run would drop a key that lives only on the run record.
        put_json(services.state, f"stage_artifacts:{run.run_id}:placement", {"placement/scene.json": key})
        now = services.clock.iso()
        for name in STAGE_NAMES:
            services.state.upsert_stage(
                StageRecord(run_id=run.run_id, stage=name, status="pending", attempt=1, heartbeat=now)
            )
        RunCoordinator(services).drive(run.run_id)

        finished = services.state.get_run(run.run_id)
        record = services.state.get_stage(run.run_id, "placement")
        assert record.status == "failed"
        assert record.error_code == "placement_scene_invalid"
        assert record.error.startswith("placement_scene_invalid:")
        assert finished.degraded == ["placement"]
        assert finished.status == "done"
        assert finished.outcome_kind == "manual_review"
        assert finished.failure_code == "degraded:placement"
        assert "placement/placement.json" not in finished.artifacts
        assert services.state.get_capture("cap_test").status == "manual_review"

        events = services.state.events_after("cap_test", 0, limit=500)
        failed = [
            event
            for event in events
            if event.type == "stage" and event.data.get("stage") == "placement" and event.data.get("status") == "failed"
        ]
        assert [event.data.get("errorCode") for event in failed] == ["placement_scene_invalid"]
        finals = [event for event in events if event.type in {"verdict_ready", "retake_request", "failed"}]
        assert len(finals) == 1


def test_v1_placements_contract_stays_live() -> None:
    modules = adapter.load_api()
    assert Path(modules["api"].__file__) == TOP_SERVER / "api.py"
    with TestClient(modules["api"].app) as client:
        response = client.post(
            "/v1/placements",
            content=FIXTURE.read_bytes(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(RESULT_SCHEMA).validate(body)
    assert body["decision"] == EXPECTED_DECISION
    assert body["stats"]["input_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
