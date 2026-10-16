"""The real CPU criteria stage over published capture artifacts.

Five claims, each against real bytes through the real seam:

- Upstream publication. Each scene's genuine nonempty laser PLY, coverage grid and
  declared assets are published under the same ``captures/<capture>/runs/<run>/<key>``
  map an upstream scene/coverage stage supplies (``graph.STAGE_INPUTS["criteria"]``
  keys), and the stage work dir is hydrated by ``Services.stage_context`` — the test
  never pre-seeds it.
- Real stage. ``runner.run_stage(ctx, stubs=False)`` executes the §3.9 criteria stage
  entry (``server/criteria/__init__.py::run``), which evaluates the scene and publishes
  fresh verdicts for every profile plus ``criteria_report.json`` — never
  ``evaluate_scene`` alone, never a stub.
- Declared bytes. The published PLY and grid hash to the scene's declared asset
  ``sha256`` stamps, and each verdict carries the hash of the scene it consumed.
- Honest C2. The report names the sweep's actual bounded status; a skipped or
  bounded-out sweep downgrades C1 passes instead of standing in for agreement, and a
  zero-comparison sweep reports no agreement (the #255 contract, regression pinned in
  ``server/criteria/tests``).
- Explicit failure. A missing, empty or truncated declared PLY fails the stage with
  the typed, non-retryable ``occluders_missing`` code — never a clean verdict.

Synthetic geometry only: ``provenance.data`` is ``"synthetic"``. The placement bridge
(``scene_bridge.convert_scene``) refuses these grid-bearing, keyframe-carrying scenes
by contract; the diagnostic below records that refusal on the generator's unchanged
geometry instead of weakening the bridge. Two distinct capture/run identities run the
stage and never share files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from server.common.backends.state_memory import MemoryState
from server.common.backends.storage_local import LocalFsStorage
from server.common.config import Settings
from server.contracts.jobs import CaptureRecord, RunRecord, StageRecord
from server.contracts.verdict import Verdict
from server.criteria.occluders import ply_vertex_count
from server.eval.sceneio import verdict_contract_errors
from server.orchestrator.clock import Clock
from server.orchestrator.config import OrchConfig
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.graph import STAGE_INPUTS
from server.orchestrator.runner import error_parts, is_retryable, run_stage
from server.orchestrator.services import Services, bind

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cpu_downstream"

VERDICT_KEYS = (
    "verdicts/verdict.A.json",
    "verdicts/verdict.B.json",
    "verdicts/verdict.C.json",
    "verdicts/verdict.S.json",
    "verdicts/criteria_report.json",
    "verdicts/placement.C.json",
    "verdicts/c2.sweep.json",
    "verdicts/c3.facts.json",
)


def _load_helper():
    spec = importlib.util.spec_from_file_location("cpu_downstream_scenes", FIXTURES / "scenes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stage_env(monkeypatch):
    """Offline, conservative criteria settings. C2 runs for real with a workable budget."""
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    monkeypatch.setenv("HSS_CRITERIA_C3_JUDGE", "off")
    monkeypatch.setenv("HSS_CRITERIA_ABSENT_FACTS", "review")
    monkeypatch.setenv("HSS_CRITERIA_C2_BUDGET_S", "120")
    # Explicit static test auth and HMAC test secret. Nothing external is contacted.
    monkeypatch.setenv("CAPTURE_API_TOKENS", "static-test-token")
    monkeypatch.setenv("UPLOAD_URL_HMAC_SECRET", "static-test-hmac-secret")
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", "static-test-webhook-hmac")


PACKET = {
    "keyframes": [{"id": "k0"}],
    "stills": [],
    "taps": [{"id": "meter", "element": "meter"}],
    "scaleReference": {"ext": {"doorOnMeterSide": False, "doorViews": []}},
}

READS = {
    "format": "base-reads/v0",
    "capture_id": "",
    "facts": [
        {
            "id": "panel_recall",
            "scope": "site",
            "outcome": "unsure",
            "unsure_cause": "not_evaluated",
            "reason": "static test fact",
            "measured": None,
            "threshold": None,
        }
    ],
}


def _services(tmp_path: Path) -> Services:
    """Memory state, local volume storage, no prewarm, no stubs — the real path."""
    settings = Settings(
        STATE_BACKEND="memory",
        STORAGE_BACKEND="local",
        VISION_EXTERNAL_CALLS="off",
        HARD_STOP_MIN=20,
        VOLUME_ROOT=str(tmp_path / "volume"),
        SMP_ENVIRONMENT="dev",
    )
    orch = OrchConfig(stage_stubs=False, stale_s=180, max_respawns=2, prewarm=False)
    return Services(
        settings=settings,
        orch=orch,
        state=MemoryState(),
        storage=LocalFsStorage(tmp_path / "store"),
        clock=Clock(),
        executor=InlineExecutor(config=orch, stubs=False),
        work_root=tmp_path / "work",
    )


def _bind_capture(services: Services, capture_id: str) -> bytes:
    packet = json.dumps(PACKET).encode()
    services.storage.put_bytes(f"captures/{capture_id}/packet.json", packet, "application/json")
    services.state.put_capture(
        CaptureRecord(
            capture_id=capture_id,
            packet_id=f"packet-{capture_id}",
            status="processing",
            tier="arkit",
            flow="photo",
            device_model="iPhone",
            owner_principal="test",
            created_at=services.clock.iso(),
            finalize_by=services.clock.iso(),
            packet_sha256=hashlib.sha256(packet).hexdigest(),
        )
    )
    return packet


def _publish_inputs(services: Services, capture_id: str, run_id: str, files: dict[str, bytes]) -> dict[str, str]:
    """The same storage/artifact map runner._publish uses for upstream stages."""
    prefix = f"captures/{capture_id}/runs/{run_id}"
    artifacts: dict[str, str] = {}
    for key, blob in files.items():
        storage_key = f"{prefix}/{key}"
        ctype = "application/json" if key.endswith(".json") else "application/octet-stream"
        services.storage.put_bytes(storage_key, blob, ctype)
        artifacts[key] = storage_key
    return artifacts


def _hydrated_context(services: Services, capture_id: str, run_id: str, packet: bytes, artifacts: dict[str, str]):
    run = RunRecord(
        run_id=run_id,
        capture_id=capture_id,
        packet_sha256=hashlib.sha256(packet).hexdigest(),
        status="running",
        branch="image",
        flow="photo",
        tier="arkit",
        profile_default="C",
        created_at=services.clock.iso(),
        deadline_at=services.clock.iso(),
        artifacts=artifacts,
    )
    services.state.put_run(run)
    rec = StageRecord(run_id=run_id, stage="criteria", status="running", attempt=1, heartbeat=services.clock.iso())
    services.state.upsert_stage(rec)
    ctx = services.stage_context(run, rec, "call-burst9")
    assert set(ctx.inputs) == {"packet_dir", "run_control"}  # inputs ride the work dir, not ctx.inputs
    return ctx


def _scene_files(scene_dir: Path, capture_id: str, laser: bytes | None) -> dict[str, bytes]:
    scene_bytes = (scene_dir / "scene.json").read_bytes()
    scene = json.loads(scene_bytes)
    meter = next(o for o in scene["objects"] if o["class"] == "meter")
    files = {
        "scene/scene.json": scene_bytes,
        "coverage/coverage.npz": (scene_dir / "coverage.npz").read_bytes(),
        "coverage/bands.json": (json.dumps({"bands": scene["coverage"]["bands"]}) + "\n").encode(),
        "coverage/coverage_report.json": b'{"views_needed": []}\n',
        "objects/objects_3d.json": (json.dumps({"format": "hss-objects-3d/v0", "objects": [meter]}) + "\n").encode(),
        "reads/reads.json": (json.dumps({**READS, "capture_id": capture_id}) + "\n").encode(),
        "scale/scale.json": b'{"rel_bound_95": 0.01}\n',
    }
    if laser is not None:
        files["scene/laser.ply"] = laser
    return files


def _assert_c2_honest(report: dict, sweep: dict, verdict_c: dict) -> None:
    """A skipped or bounded-out sweep is a downgrade, never a pass."""
    status = report["c2"]["status"]
    assert status == sweep["status"]
    if status == "ok":
        compared = report["c2"]["positions_compared"]
        assert isinstance(compared, int) and compared >= 0
        if compared == 0:
            assert report["c2"]["agreement_with_c1"] is None  # #255: no comparison is not perfect agreement
        assert sweep["by_profile"]["C"]["status"] == "ok"
        return
    rows = verdict_c["criteria"]
    assert not any(row["outcome"] == "pass" for row in rows), status
    assert verdict_c["site"]["decision"] != "pass"


def test_published_inputs_feed_the_real_criteria_stage(stage_env, tmp_path):
    scenes = _load_helper().supported_wall_variants(tmp_path)
    services = _services(tmp_path)
    runs: dict[str, tuple] = {}
    with bind(services):
        for variant, scene_dir in scenes.items():
            capture_id, run_id = f"cap-criteria-{variant}", f"run-criteria-{variant}"
            packet = _bind_capture(services, capture_id)
            files = _scene_files(scene_dir, capture_id, laser=(scene_dir / "laser.ply").read_bytes())
            for key in files:
                assert STAGE_INPUTS["criteria"] and any(
                    (key.startswith(p.rstrip("/")) if p.endswith("/") else key == p) for p in STAGE_INPUTS["criteria"]
                ), key
            artifacts = _publish_inputs(services, capture_id, run_id, files)
            ctx = _hydrated_context(services, capture_id, run_id, packet, artifacts)
            work = Path(ctx.work_dir)
            # Hydrated from storage by the service path, never pre-seeded by the test.
            assert (work / "scene" / "scene.json").is_file()
            assert (work / "scene" / "laser.ply").is_file()
            assert (work / "coverage" / "coverage.npz").is_file()
            assert not (work / "verdicts").exists()
            result = run_stage(ctx, stubs=False)
            assert result.metrics["verdicts"] == 4.0
            runs[variant] = (capture_id, run_id, ctx, result)

    storage = services.storage
    docs: dict[str, dict] = {}
    for variant, (capture_id, run_id, ctx, result) in runs.items():
        prefix = f"captures/{capture_id}/runs/{run_id}"
        for key in VERDICT_KEYS:
            assert key in result.artifacts, key
            blob = storage.get_bytes(result.artifacts[key])
            assert blob, key
            json.loads(blob)
        for key, storage_key in result.artifacts.items():
            assert storage_key.startswith(prefix + "/"), (key, storage_key)

        report = json.loads(storage.get_bytes(result.artifacts["verdicts/criteria_report.json"]))
        assert report["profiles"] == ["A", "B", "C", "S"]
        assert report["provisional"] is False

        verdict_c = json.loads(storage.get_bytes(result.artifacts["verdicts/verdict.C.json"]))
        assert Verdict.model_validate(verdict_c)
        assert verdict_contract_errors(verdict_c) == []

        # Published bytes hash to the scene's declared asset stamps. Inputs are read
        # from the run's own storage prefix — result.artifacts carries stage outputs.
        scene = json.loads(storage.get_bytes(f"{prefix}/scene/scene.json"))
        for asset in scene["assets"]:
            artifact_key = f"{prefix}/" + ("scene/" if asset["id"] == "laser" else "coverage/") + asset["path"]
            blob = storage.get_bytes(artifact_key)
            assert hashlib.sha256(blob).hexdigest() == asset["sha256"], asset["id"]
        assert ply_vertex_count(Path(ctx.work_dir) / "scene" / "laser.ply") > 0

        # The verdict carries exactly the scene bytes the stage consumed.
        consumed = json.dumps(scene) + "\n"
        assert verdict_c["scene"]["sha256"] == hashlib.sha256(consumed.encode()).hexdigest()
        assert verdict_c["scene"]["data"] == "synthetic"
        assert verdict_c["scene"]["scene_id"] == f"synthetic-flatwall-{variant}"

        sweep = json.loads(storage.get_bytes(result.artifacts["verdicts/c2.sweep.json"]))
        _assert_c2_honest(report, sweep, verdict_c)
        docs[variant] = {
            "report": report,
            "verdict": verdict_c,
            "raw": json.loads(storage.get_bytes(result.artifacts["verdicts/c1.C.raw.json"])),
            "sweep": sweep,
        }

    # Scene-dependent outcomes. The hazard is real to the engine: candidate runs near
    # its known span fail or go unsure on gas_clearance in one contiguous block, the
    # evaluated check names the hazard as its subject, and the sweep compares more
    # ground positions. The chosen candidate routes around the hazard, so the final
    # criteria rows still pass gas clearance — the effect lives in the runs and sweep.
    clean, hazard = docs["clean"], docs["hazard"]
    assert json.dumps(clean["verdict"]["criteria"], sort_keys=True) != json.dumps(
        hazard["verdict"]["criteria"], sort_keys=True
    )
    clean_fail = [r for r in clean["raw"]["runs"] if "gas_clearance" in r.get("failing", [])]
    hazard_fail = [r for r in hazard["raw"]["runs"] if "gas_clearance" in r.get("failing", [])]
    hazard_unsure = [r for r in hazard["raw"]["runs"] if "gas_clearance" in r.get("unsure", [])]
    assert not clean_fail
    assert len(hazard_fail) >= 20
    fail_u0 = [r["u"][0] for r in hazard_fail]
    assert max(fail_u0) - min(fail_u0) < 10.0  # one clearance block, not scattered noise
    unsure_u0 = [r["u"][0] for r in hazard_unsure]
    assert min(unsure_u0) < min(fail_u0) and max(fail_u0) < max(unsure_u0)  # unsure ring wraps the block
    best_gas = next(c for c in hazard["raw"]["best"]["checks"] if c["id"] == "gas_clearance")
    assert best_gas["subject"] == "gas-hazard"
    assert best_gas["outcome"] == "pass" and best_gas["measured"] >= best_gas["threshold"]
    assert hazard["sweep"]["by_profile"]["C"]["n_spots"] >= 0
    assert hazard["report"]["c2"]["positions_compared"] > clean["report"]["c2"]["positions_compared"]

    # Two capture/run identities never share files.
    clean_blob = storage.get_bytes(runs["clean"][3].artifacts["verdicts/verdict.C.json"])
    hazard_blob = storage.get_bytes(runs["hazard"][3].artifacts["verdicts/verdict.C.json"])
    assert clean_blob != hazard_blob
    clean_prefix = "captures/cap-criteria-clean/runs/run-criteria-clean"
    for name in storage.list_prefix(clean_prefix + "/"):
        assert "cap-criteria-hazard" not in str(name)
    clean_work, hazard_work = Path(runs["clean"][2].work_dir), Path(runs["hazard"][2].work_dir)
    assert clean_work != hazard_work
    assert "run-criteria-clean" in str(clean_work) and "run-criteria-hazard" in str(hazard_work)


@pytest.mark.parametrize("mode", ["missing", "empty", "truncated"])
def test_broken_declared_ply_fails_explicitly(stage_env, tmp_path, mode):
    scene_dir = _load_helper().supported_wall_variants(tmp_path)["clean"]
    services = _services(tmp_path)
    capture_id, run_id = "cap-broken-ply", "run-broken-ply"
    with bind(services):
        packet = _bind_capture(services, capture_id)
        laser = (scene_dir / "laser.ply").read_bytes()
        broken = {
            "missing": None,
            "empty": b"",
            "truncated": laser[:64],  # header cut before end_header: zero declared vertices
        }[mode]
        files = _scene_files(scene_dir, capture_id, laser=broken)
        artifacts = _publish_inputs(services, capture_id, run_id, files)
        ctx = _hydrated_context(services, capture_id, run_id, packet, artifacts)
        with pytest.raises(Exception) as excinfo:
            run_stage(ctx, stubs=False)
    code, text = error_parts(excinfo.value)
    assert code == "occluders_missing", (code, text)
    assert not is_retryable(excinfo.value)
    prefix = "captures/cap-broken-ply/runs/run-broken-ply/verdicts"
    assert services.storage.list_prefix(prefix + "/") == []


def _run_stage_once(monkeypatch, services, scene_dir, capture_id, run_id, env, files=None):
    """One real publication → hydration → run_stage(stubs=False) pass over committed inputs."""
    packet = _bind_capture(services, capture_id)
    if files is None:
        files = _scene_files(scene_dir, capture_id, laser=(scene_dir / "laser.ply").read_bytes())
    artifacts = _publish_inputs(services, capture_id, run_id, files)
    ctx = _hydrated_context(services, capture_id, run_id, packet, artifacts)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = run_stage(ctx, stubs=False)
    storage = services.storage
    report = json.loads(storage.get_bytes(result.artifacts["verdicts/criteria_report.json"]))
    sweep = json.loads(storage.get_bytes(result.artifacts["verdicts/c2.sweep.json"]))
    return report, sweep, result


OFF_DOCUMENT = {"status": "off", "agreement_with_c1": None, "positions_compared": 0, "vetoes": 0}


def test_c2_report_evidence_follows_the_default_profile_not_the_iteration_order(stage_env, tmp_path, monkeypatch):
    """Regression for the two review findings (Greptile r4290120811, Codex r4290131173).

    With HSS_CRITERIA_C2_PROFILES=all the old code overwrote the top-level C2 report on
    every profile iteration and corrected only the status afterwards, so the report's count
    and agreement described the last profile (S) while the status described the default (C).
    The real stage must keep every profile's comparison evidence and select the WHOLE
    top-level document from the configured default — for C and for a non-C default,
    independent of profile iteration order.
    """
    scenes = _load_helper().supported_wall_variants(tmp_path)
    services = _services(tmp_path)
    docs: dict[tuple[str, str], tuple[dict, dict, object]] = {}
    with bind(services):
        for variant, scene_dir in scenes.items():
            for label, profiles, default in (
                ("s_last", "A,B,C,S", "C"),  # S runs last: where the old overwrite bit
                ("c_last", "S,B,A,C", "S"),  # reordered iteration and a non-C default
            ):
                docs[(variant, label)] = _run_stage_once(
                    monkeypatch,
                    services,
                    scene_dir,
                    f"cap-c2sel-{variant}-{label}",
                    f"run-c2sel-{variant}-{label}",
                    env={
                        "HSS_CRITERIA_C2_PROFILES": "all",
                        "HSS_CRITERIA_PROFILES": profiles,
                        "RULES_PROFILE_DEFAULT": default,
                    },
                )

    for variant in scenes:
        for label, default in (("s_last", "C"), ("c_last", "S")):
            report, sweep, _result = docs[(variant, label)]
            by_profile = sweep["by_profile"]
            assert set(by_profile) == {"A", "B", "C", "S"}, by_profile  # evidence kept, not overwritten
            entry = by_profile[default]
            assert report["c2"]["status"] == entry["status"] == sweep["status"]
            assert report["c2"]["agreement_with_c1"] == entry["agreement_with_c1"]
            assert report["c2"]["positions_compared"] == entry["positions_compared"]
            assert report["c2"]["vetoes"] == entry["vetoes"]
            assert sweep["n_pass"] == entry["n_pass"]
            assert sweep["n_spots"] == entry["n_spots"]
            for record in by_profile.values():
                if record["status"] == "ok" and record["positions_compared"] == 0:
                    assert record["agreement_with_c1"] is None  # #255: no comparison is not perfect agreement

        # The reviewers' scenario, measured on the hazard variant: C and S sweep the same
        # 16 positions, but S's comparison agrees 0.938 with one veto where C's agrees
        # 1.0 with none. With profiles ordered A,B,C,S (S last) and default C, the old
        # last-profile overwrite published S's agreement and vetoes under C's status; the
        # report must carry the default's own comparison values.
        s_last_report, s_last_sweep, _ = docs[("hazard", "s_last")]
        assert s_last_sweep["by_profile"]["C"] == {
            "status": "ok",
            "agreement_with_c1": 1.0,
            "positions_compared": 16,
            "vetoes": 0,
            "n_pass": 26,
            "n_spots": 120,
        }
        assert s_last_sweep["by_profile"]["S"]["agreement_with_c1"] == 0.938
        assert s_last_sweep["by_profile"]["S"]["vetoes"] == 1
        assert s_last_report["c2"]["agreement_with_c1"] == 1.0
        assert s_last_report["c2"]["vetoes"] == 0
        # Order independence: the same scene+profile yields the same evidence in both runs.
        kept = docs[(variant, "s_last")][1]["by_profile"]
        assert docs[(variant, "c_last")][1]["by_profile"]["C"] == kept["C"]


def test_c2_report_names_off_absent_default_skipped_and_no_coverage_without_fabricated_agreement(
    stage_env, tmp_path, monkeypatch
):
    """Named statuses through the real stage: an off, an unavailable default, a skipped
    sweep and a grid-less scene each keep agreement None and never borrow numbers."""
    scenes = _load_helper().supported_wall_variants(tmp_path)
    clean = scenes["clean"]
    services = _services(tmp_path)

    # (a) C2 disabled: no profile runs it; the report keeps the named off document.
    report, sweep, _ = _run_stage_once(
        monkeypatch,
        services,
        clean,
        "cap-c2-off",
        "run-c2-off",
        env={
            "HSS_CRITERIA_C2": "off",
            "HSS_CRITERIA_C2_PROFILES": "all",
            "HSS_CRITERIA_PROFILES": "A,B,C,S",
            "RULES_PROFILE_DEFAULT": "C",
            "HSS_CRITERIA_C2_BUDGET_S": "120",
        },
    )
    assert report["c2"] == OFF_DOCUMENT
    assert sweep["status"] == "off" and sweep["n_pass"] == 0 and sweep["n_spots"] == 0
    assert sweep["by_profile"] == {}

    # (b) Other profiles really ran C2 but the default has no entry: no substitution.
    report, sweep, _ = _run_stage_once(
        monkeypatch,
        services,
        clean,
        "cap-c2-absent",
        "run-c2-absent",
        env={
            "HSS_CRITERIA_C2": "on",
            "HSS_CRITERIA_C2_PROFILES": "all",
            "HSS_CRITERIA_PROFILES": "A,S",
            "RULES_PROFILE_DEFAULT": "C",
            "HSS_CRITERIA_C2_BUDGET_S": "120",
        },
    )
    assert set(sweep["by_profile"]) == {"A", "S"}
    assert all(record["n_spots"] > 0 for record in sweep["by_profile"].values())  # their evidence is real
    assert report["c2"] == OFF_DOCUMENT
    assert sweep["status"] == "off" and sweep["n_pass"] == 0 and sweep["n_spots"] == 0

    # (c) A budget that cannot finish the first sweep names every profile skipped.
    report, sweep, result = _run_stage_once(
        monkeypatch,
        services,
        clean,
        "cap-c2-budget",
        "run-c2-budget",
        env={
            "HSS_CRITERIA_C2": "on",
            "HSS_CRITERIA_C2_PROFILES": "all",
            "HSS_CRITERIA_PROFILES": "A,B,C,S",
            "RULES_PROFILE_DEFAULT": "C",
            "HSS_CRITERIA_C2_BUDGET_S": "0.5",
        },
    )
    assert report["c2"] == {"status": "skipped", "agreement_with_c1": None, "positions_compared": 0, "vetoes": 0}
    assert set(sweep["by_profile"]) == {"A", "B", "C", "S"}
    assert all(
        record["status"] == "skipped" and record["agreement_with_c1"] is None and record["positions_compared"] == 0
        for record in sweep["by_profile"].values()
    )
    verdict_c = json.loads(services.storage.get_bytes(result.artifacts["verdicts/verdict.C.json"]))
    assert verdict_c["site"]["decision"] != "pass"  # a skipped sweep never yields a pass

    # (d) A scene that declares no coverage grid names no_coverage_grid and downgrades.
    # The standard committed file set, with only the scene's inline coverage grid removed.
    files = _scene_files(clean, "cap-c2-nogrid", laser=(clean / "laser.ply").read_bytes())
    scene = json.loads(files["scene/scene.json"])
    scene["coverage"]["grid"] = None
    files["scene/scene.json"] = json.dumps(scene).encode() + b"\n"
    report, sweep, result = _run_stage_once(
        monkeypatch,
        services,
        clean,
        "cap-c2-nogrid",
        "run-c2-nogrid",
        env={
            "HSS_CRITERIA_C2": "on",
            "HSS_CRITERIA_C2_PROFILES": "all",
            "HSS_CRITERIA_PROFILES": "A,B,C,S",
            "RULES_PROFILE_DEFAULT": "C",
            "HSS_CRITERIA_C2_BUDGET_S": "120",
        },
        files=files,
    )
    assert report["c2"] == {
        "status": "no_coverage_grid",
        "agreement_with_c1": None,
        "positions_compared": 0,
        "vetoes": 0,
    }
    assert set(sweep["by_profile"]) == {"A", "B", "C", "S"}
    assert all(
        record["status"] == "no_coverage_grid" and record["agreement_with_c1"] is None
        for record in sweep["by_profile"].values()
    )
    assert "no_coverage_grid" in report["degraded"]
    verdict_c = json.loads(services.storage.get_bytes(result.artifacts["verdicts/verdict.C.json"]))
    assert not any(row["outcome"] == "pass" for row in verdict_c["criteria"])
    assert verdict_c["site"]["decision"] != "pass"


def test_bridge_boundary_diagnostic_on_unchanged_generator_geometry(tmp_path):
    from server.criteria.tests.data.synthetic_flatwall.make import scene_dict
    from server.placement.scene_bridge import UNSUPPORTED, SceneBridgeError, convert_scene

    scenes = _load_helper()
    original = scenes.original_scene()
    assert original == scene_dict()  # the generator's geometry, byte-for-byte unchanged
    with pytest.raises(SceneBridgeError) as first:
        convert_scene(original)
    assert first.value.code == UNSUPPORTED
    # The documented refusals, in the bridge's own order: keyframes, then the grid.
    without_keyframes = dict(original, keyframes=[])
    with pytest.raises(SceneBridgeError) as second:
        convert_scene(without_keyframes)
    assert second.value.code == UNSUPPORTED
    assert second.value.pointer == "/coverage/grid"
    for scene_dir in scenes.supported_wall_variants(tmp_path).values():
        document = json.loads((scene_dir / "scene.json").read_text())
        with pytest.raises(SceneBridgeError) as variant_refusal:
            convert_scene(document)
        assert variant_refusal.value.code == UNSUPPORTED
