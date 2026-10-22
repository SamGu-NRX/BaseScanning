"""Runtime regression tests for the capture-placement runner's real-downstream mode.

The mode runs the same capture front door as the W8 journey, but selects the real
criteria and result implementations while the unavailable upstream boundaries are
injected from the criteria producer's flatwall fixture. These tests pin the
receipt: blockers empty, the recorded real callables, the served criteria-to-result
policy, the two supported cases' distinct consequences, the missing-occluder typed
refusal, and the faults that must never pass silently (stub substitution, scene
swap, dropped publication). The mode itself is also exercised through the real
argv/exit-code boundary from a temporary working directory.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.capture_placement_cases as runner
import server.orchestrator.runner as orchestrator_runner
import server.orchestrator.stubs.reads as reads_stub

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def receipt(tmp_path_factory):
    """The full three-case real-downstream run, shared by the receipt tests."""
    return runner.run_real_downstream(tmp_path_factory.mktemp("real-downstream"))


def test_the_real_downstream_receipt_is_ok(receipt):
    """Every case closes cleanly: no journey errors, no mismatches, replay
    idempotent."""
    assert receipt["ok"] is True, receipt["blockers"]
    assert receipt["schemaVersion"] == runner.REAL_DOWNSTREAM_RECEIPT_SCHEMA
    assert receipt["summary"] == {
        "cases": 3,
        "solved": 2,
        "refused": 1,
        "refusedDeclared": 1,
        "mismatched": 0,
        "errored": 0,
    }
    assert runner.TOKEN not in json.dumps(receipt)


def test_the_real_criteria_and_result_actually_ran(receipt):
    """The executed-callable records name the real server.criteria and
    server.result implementations for the supported captures, and the real
    criteria's prepared input kept the same-id merge unique."""
    real = [item for item in receipt["executedCallables"] if item["stage"] in ("criteria", "result")]
    solved_ids = {case["captureId"] for case in receipt["cases"] if case["status"] == "solved"}
    for item in real:
        if item["captureId"] in solved_ids:
            assert item["callable"].startswith(f"server.{item['stage']}:"), item
    for case in receipt["cases"]:
        if case["status"] != "solved":
            continue
        assert case["criteriaCallable"] == "server.criteria:run", case["criteriaCallable"]
        assert case["resultCallable"] == "server.result:run", case["resultCallable"]
        assert case["criteriaInputVentCount"] == 1
        assert case["criteriaInputIdsUnique"] is True


def test_the_served_outcome_follows_the_criteria_policy(receipt):
    """Per supported case: HTTP 200, a schema-valid ResultResponse on the
    journey's own run id, and the outcome kind the recomputed policy demands."""
    for case in receipt["cases"]:
        if case["status"] != "solved":
            continue
        assert case["servedResultHttpStatus"] == 200
        assert case["servedResultRunId"] == case["runId"]
        assert case["servedResultKind"] == case["expectedDeviceKind"]
        assert case["captureStatus"] == case["servedResultStatus"]
        assert case["terminalEventCount"] == 1


def test_the_solver_decision_is_recorded_separately(receipt):
    """The real solver's decision is on the receipt next to the served outcome —
    recorded, never forced into agreement with it."""
    for case in receipt["cases"]:
        if case["status"] != "solved":
            continue
        assert case["solverDecision"], case
        assert case["artifactSha256"]["placement/placement.json"]
        assert case["artifactSha256"]["result/result.json"]
        assert case["artifactSha256"]["verdicts/verdict.C.json"]


def test_the_two_supported_cases_differ_in_relevant_consequences(receipt):
    """Same fixture, different geometry: only the blocked case binds the
    gas_clearance criterion to its injected gas meter with a measured margin —
    the outcome itself may honestly tie at unsure under the documented
    no-keyframe degradations."""
    cases = {case["id"]: case for case in receipt["cases"]}
    clear, blocked = cases["flatwall-clear"], cases["flatwall-gas-blocked"]
    assert clear["gasClearanceSubject"] is None
    assert blocked["gasClearanceSubject"] == "obj-gas"
    assert blocked["gasClearanceMeasuredFt"] is not None
    assert blocked["gasClearanceOutcome"] == "unsure"
    assert clear["mergedGeometrySha256"] != blocked["mergedGeometrySha256"]
    assert clear["resultSha256"] != blocked["resultSha256"]


def test_the_local_limitations_are_recorded(receipt):
    """The receipt names the reductions the bridge's supported scope forces, the
    injected boundaries, and the stubbed stages — nothing silent."""
    reductions = "\n".join(receipt["inputProvenance"]["reductions"])
    assert "/keyframes" in reductions and "no_keyframes" in reductions
    assert "/coverage/grid" in reductions
    assert set(receipt["stubbedStages"]) == {"validate", "poses", "objects", "dense", "coverage"}
    assert set(receipt["injectedBoundaries"]) == {"scene", "objects_3d", "reads", "scale"}
    for case in receipt["cases"]:
        if case["status"] == "solved":
            assert "no_keyframes" in (case["criteriaReport"]["degraded"] or [])
            assert case["criteriaReport"]["provisional"] is False


def test_a_declared_missing_occluder_refuses_with_the_typed_error(receipt):
    """The declared-missing occluder is the real engine's typed refusal: criteria
    failed with its own code, no verdicts, and the coordinator's conservative
    manual-review fallback is what the device gets — never an evaluated claim."""
    case = next(item for item in receipt["cases"] if item["id"] == "flatwall-missing-occluder")
    assert case["criteriaStage"] == {"status": "failed", "errorCode": "occluders_missing"}
    # The orchestrator wraps the typed refusal with its stage-exhaustion prefix.
    assert case["failureCode"] == "stage_exhausted:criteria:occluders_missing"
    assert case["servedResultHttpStatus"] == 200
    assert case["servedResultKind"] == "manual_review"
    assert case["captureStatus"] in ("manual_review", "needs_views")
    assert not any(key.startswith("verdicts/") for key in (case.get("artifactSha256") or {}))


def test_substituting_the_criteria_stub_fails_the_mode(tmp_path, monkeypatch):
    """The stub must never stand in for the real criteria silently: with the
    selection forced back onto the stub, the receipt's recorded-callable check
    fails the mode."""
    monkeypatch.setattr(runner, "_FORCE_STUB_STAGES", frozenset({"criteria"}))
    receipt = runner.run_real_downstream(tmp_path / "run", case_ids=["flatwall-clear"])
    assert receipt["ok"] is False
    blockers = "\n".join(receipt["blockers"])
    assert "criteria ran" in blockers and "not the real server.criteria implementation" in blockers


def test_replacing_the_case_scene_fails_the_mode(tmp_path, monkeypatch):
    """A substituted scene — another case's document behind this case's id — is
    caught by the case-identity and hazard checks."""
    blocked = runner._real_case_document({"id": "flatwall-gas-blocked", "objects": (runner.REAL_DOWNSTREAM_GAS,)})
    monkeypatch.setattr(runner, "_real_case_document", lambda case: blocked)
    receipt = runner.run_real_downstream(tmp_path / "run", case_ids=["flatwall-clear"])
    assert receipt["ok"] is False
    blockers = "\n".join(receipt["blockers"])
    assert "gas_meter hazard" in blockers or "identity stamp" in blockers


def test_dropping_the_published_result_fails_the_mode(tmp_path, monkeypatch):
    """Reject a missing result before committing an immutable publication."""
    monkeypatch.setattr(runner, "_DROP_RESULT_ARTIFACT", True)
    receipt = runner.run_real_downstream(tmp_path / "run", case_ids=["flatwall-clear"])
    assert receipt["ok"] is False
    assert any("artifact_missing" in blocker for blocker in receipt["blockers"]), receipt["blockers"]


def test_the_selection_override_and_boundaries_are_restored(tmp_path):
    """The mode-scoped patches never outlive the run: the orchestrator's stage
    selection, the reads stub and the registry all return to their originals."""
    original_stage_callable = orchestrator_runner.stage_callable
    original_reads_run = reads_stub.run
    runner.run_real_downstream(tmp_path / "run", case_ids=["flatwall-clear"])
    assert orchestrator_runner.stage_callable is original_stage_callable
    assert reads_stub.run is original_reads_run
    assert runner._REGISTRY == {}


def test_the_real_downstream_cli_runs_from_a_temporary_cwd(tmp_path):
    """The one-command entry point: `--real-downstream` from a fresh subprocess
    and a temporary working directory writes the receipt and exits 0."""
    output = tmp_path / "receipt.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts" / "capture_placement_cases.py"),
            "--real-downstream",
            "--output",
            str(output),
            "--workdir",
            str(tmp_path / "run"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    written = json.loads(output.read_text())
    assert written["ok"] is True
    assert written["summary"]["solved"] == 2 and written["summary"]["refused"] == 1
    assert runner.TOKEN not in output.read_text()
