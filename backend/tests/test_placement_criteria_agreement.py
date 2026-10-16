"""Pins of the placement/criteria agreement measurement on two small cases.

The two cases are the smallest committed ones, and the expectations below are
hand-checked against the manifest's own declared facts (the case notes derive
them from the authored dimensions) plus the engines' measured behavior:

- ``capture_cases_v1/hazard-right-ground-gap`` — the placement path solves the
  positive to ``manual_review`` with the manifest's hand-derived spot and check
  outcomes; the criteria engine refuses the same source at its meter-wall
  precondition (the committed meter is frame-anchored and carries no
  ``wall_id``), so the case lands on ``criteria_refused``.
- ``capture_cases_v1/multiwall-source-unsupported`` — the bridge refuses the
  two-wall source at the manifest's declared code and pointer, the criteria
  engine refuses the same source, so no engine evaluates it.

Any engine exception (as opposed to a declared refusal) must fail loudly: the
last test injects a crashing engine and pins the error record and the failing
exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import placement_criteria_agreement as agreement  # noqa: E402


TWO_CASES = (
    ("capture_cases_v1", "hazard-right-ground-gap"),
    ("capture_cases_v1", "multiwall-source-unsupported"),
)


def _run_two_cases(**kwargs) -> list[dict]:
    cases = [c for c in agreement.load_cases() if (c["manifest"], c["id"]) in TWO_CASES]
    assert [(c["manifest"], c["id"]) for c in cases] == list(TWO_CASES)
    return agreement.collect_results(cases, **kwargs)


def test_positive_case_placement_matches_the_manifest_and_criteria_refuses() -> None:
    (record,) = [r for r in _run_two_cases() if r["id"] == "hazard-right-ground-gap"]

    # The manifest derives these by hand from the authored dimensions.
    placement = record["placement"]
    assert placement["status"] == "solved"
    assert placement["decision"] == "manual_review"
    assert placement["spot"]["wall_id"] == "side"
    assert placement["spot"]["segment"] == 0
    assert placement["spot"]["span_ft"] == [7.0, 9.583333]
    assert placement["spot"]["outcome"] == "unsure"
    checks = {check["id"]: check for check in placement["checks"]}
    assert checks["gas_clearance"]["outcome"] == "unsure"
    assert checks["gas_clearance"]["unsure_cause"] == "margin"
    assert checks["ground_surface"]["outcome"] == "unsure"
    assert checks["ground_surface"]["unsure_cause"] == "unobserved"
    assert checks["meter_working_space"]["outcome"] == "pass"

    # The criteria engine's own declared refusal on this input: its meter-wall
    # precondition needs meter.wall_id, and the committed meter_anchored/v0
    # scene anchors the meter by frame.anchor instead.
    criteria = record["criteria"]
    assert criteria["status"] == "refused"
    assert criteria["code"] == "meter_wall_missing"

    assert record["agreement"]["verdict"] == "criteria_refused"
    assert record["agreement"]["pairs"] == []


def test_negative_case_is_refused_by_both_engines_and_named_as_such() -> None:
    (record,) = [r for r in _run_two_cases() if r["id"] == "multiwall-source-unsupported"]

    placement = record["placement"]
    assert placement["status"] == "refused"
    assert placement["code"] == "placement_scene_unsupported"
    assert placement["pointer"] == "/walls"
    assert record["manifest_expected_error"] == {"code": "placement_scene_unsupported", "pointer": "/walls"}
    assert record["placement_matches_manifest"] is True

    assert record["criteria"]["status"] == "refused"
    assert record["criteria"]["code"] == "meter_wall_missing"

    assert record["agreement"]["verdict"] == "neither_engine_evaluated"


def test_an_engine_crash_is_recorded_and_fails_the_run_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    def crashing_placement(source_bytes: bytes) -> dict:
        raise RuntimeError("boom")

    def canned_criteria(source_bytes: bytes, work_dir: Path) -> dict:
        return {"status": "evaluated", "provisional": False, "degraded": [], "profiles": {}}

    table = _run_two_cases(placement_fn=crashing_placement, criteria_fn=canned_criteria)
    assert agreement.has_errors(table)
    for record in table:
        assert record["placement"]["status"] == "error"
        assert record["placement"]["exception"] == "RuntimeError"
        assert record["placement"]["message"] == "boom"
        assert record["agreement"]["verdict"] == "engine_error"

    output = agreement.build_output(table, BACKEND.parent)
    assert [error["engine"] for error in output["summary"]["errors"]] == ["placement", "placement"]

    # The CLI surfaces the same failure as a non-zero exit.
    monkeypatch.setattr(agreement, "collect_results", lambda *a, **kw: table)
    assert agreement.main(["--case", "hazard-right-ground-gap", "--case", "multiwall-source-unsupported"]) == 1
