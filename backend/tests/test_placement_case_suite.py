"""Focused tests for the offline placement case suite and its manifest.

These tests exercise the suite machinery, not solver geometry twice: the
manifest's pinned expectations are evaluated once per full-suite run and the
individual tests assert on the resulting report plus the failure paths of the
loader. Expectation mutations are checked against copies of the manifest.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from server.placement import case_suite as cs

MANIFEST = cs.DEFAULT_MANIFEST


@pytest.fixture(scope="module")
def report() -> dict:
    return cs.run_suite(MANIFEST)


def test_load_cases_interface() -> None:
    records = cs.load_cases(MANIFEST)
    ids = [record.id for record in records]
    assert ids == [
        "hazard-right-ground-gap",
        "hazard-left-ground-gap",
        "multiwall-source-unsupported",
        "generated-hazard-unsupported",
    ]
    by_id = {record.id: record for record in records}
    assert all(record.source_path.is_file() for record in records)
    for positive in ("hazard-right-ground-gap", "hazard-left-ground-gap"):
        assert by_id[positive].kind == cs.KIND_POSITIVE
        assert by_id[positive].wire_path is not None and by_id[positive].wire_path.is_file()
        assert by_id[positive].expectations["decision"] == "manual_review"
        assert by_id[positive].expectations["spot"]["span_ft"]
    for negative in ("multiwall-source-unsupported", "generated-hazard-unsupported"):
        assert by_id[negative].kind == cs.KIND_NEGATIVE
        assert by_id[negative].wire_path is None
        assert by_id[negative].expected_error == {
            "code": "placement_scene_unsupported",
            "pointer": "/walls" if negative.startswith("multiwall") else "/objects/1/evidence",
        }


def test_positive_cases_match_report(report: dict) -> None:
    assert report["schema_version"] == cs.REPORT_SCHEMA_VERSION
    assert report["summary"]["awaiting_conversion"] == 2
    assert report["summary"]["matched"] == 2
    assert report["summary"]["mismatched"] == 0
    for case in report["cases"]:
        if case["kind"] != cs.KIND_POSITIVE:
            continue
        assert case["status"] == cs.STATUS_MATCHED, case
        assert case["executed"] is True
        assert case["executions_agree"] is True
        assert case["input_sha_matches_file"] is True
        assert case["decision"] == "manual_review"
        assert case["policy"]["rules_sha256"]
        # The wire scene's own hash is echoed back by the solver.
        wire_bytes = Path(cs.DEFAULT_MANIFEST).parent / case["wire_path"]
        assert case["input_sha256"] == cs._file_sha256(wire_bytes)


def test_negative_cases_never_claim_conversion(report: dict) -> None:
    for case in report["cases"]:
        if case["kind"] != cs.KIND_NEGATIVE:
            continue
        assert case["status"] == cs.STATUS_AWAITING_CONVERSION
        assert case["executed"] is False
        assert "awaiting conversion" in (case["note"] or "").lower()
        assert case["expected_error"]["code"] == "placement_scene_unsupported"
    statuses = {case["status"] for case in report["cases"]}
    assert "converted" not in statuses


def _absolutized_payload() -> dict:
    """Manifest payload with fixture paths made absolute, so a copy of the
    manifest living anywhere still resolves the committed fixtures."""
    payload = json.loads(MANIFEST.read_text())
    cases_dir = MANIFEST.parent
    for case in payload["cases"]:
        case["source"]["path"] = str(cases_dir / case["source"]["path"])
        if case.get("wire"):
            case["wire"]["path"] = str(cases_dir / case["wire"]["path"])
    return payload


def test_missing_fixture_fails_with_path(tmp_path: Path) -> None:
    payload = _absolutized_payload()
    missing = tmp_path / "does-not-exist.wire.json"
    payload["cases"][0]["wire"]["path"] = str(missing)
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert str(missing) in str(excinfo.value)


def test_drifted_fixture_fails_with_path(tmp_path: Path) -> None:
    payload = _absolutized_payload()
    fixture = tmp_path / "hazard-right-ground-gap.wire.json"
    fixture.write_bytes(b"{}")  # same path name, different bytes, wrong hash
    payload["cases"][0]["wire"]["sha256"] = "0" * 64
    payload["cases"][0]["wire"]["path"] = str(fixture)
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert str(fixture) in str(excinfo.value)


def _run_mutated(tmp_path: Path, mutate) -> dict:
    """Run the suite against a mutated manifest copy placed in tmp."""
    payload = _absolutized_payload()
    mutate(payload)
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    return cs.run_suite(drift)


def test_changed_hazard_expectation_fails(tmp_path: Path) -> None:
    report = _run_mutated(
        tmp_path,
        lambda payload: payload["cases"][0]["expectations"]["checks"].update(
            {"gas_clearance": {"outcome": "fail", "unsure_cause": "margin"}}
        ),
    )
    case = report["cases"][0]
    assert case["status"] == cs.STATUS_MISMATCHED
    assert any("gas_clearance" in mismatch for mismatch in case["mismatches"])
    assert report["ok"] is False


def test_changed_coverage_expectation_fails(tmp_path: Path) -> None:
    report = _run_mutated(
        tmp_path,
        lambda payload: payload["cases"][0]["expectations"]["missing_evidence"][0].update({"out_ft": 9.0}),
    )
    case = report["cases"][0]
    assert case["status"] == cs.STATUS_MISMATCHED
    assert any("missing_evidence" in mismatch for mismatch in case["mismatches"])
    assert report["ok"] is False


def test_cli_writes_report_from_clean_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "report.json"
    exit_code = cs.main(["--output", str(out)])
    assert exit_code == 0
    assert out.is_file()
    written = json.loads(out.read_text())
    assert written["schema_version"] == cs.REPORT_SCHEMA_VERSION
    assert written["summary"]["matched"] == 2
    assert "matched=2" in capsys.readouterr().out


def test_two_executions_agree_only_elapsed_differs() -> None:
    """Direct in-process check: same scene twice differs only in elapsed_ms."""
    modules = cs.adapter.load_api()
    from fastapi.testclient import TestClient

    wire = MANIFEST.parent / "hazard-left-ground-gap.wire.json"
    with TestClient(modules["api"].app) as client:
        bodies = [
            client.post(
                "/v1/placements", content=wire.read_bytes(), headers={"Content-Type": "application/json"}
            ).json()
            for _ in range(2)
        ]
    assert bodies[0]["stats"]["elapsed_ms"] is not None
    for body in bodies:
        body["stats"] = {k: v for k, v in body["stats"].items() if k != "elapsed_ms"}
    assert json.dumps(bodies[0], sort_keys=True) == json.dumps(bodies[1], sort_keys=True)


def test_manifest_copy_is_untouched_by_mutations() -> None:
    payload = json.loads(MANIFEST.read_text())
    original = copy.deepcopy(payload)
    payload["cases"][0]["expectations"]["decision"] = "reject"
    assert json.loads(MANIFEST.read_text()) == original
