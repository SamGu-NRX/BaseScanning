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



def test_missing_cases_key_fails_with_manifest_path(tmp_path: Path) -> None:
    """A manifest without a cases key cannot silently disable the gate."""
    payload = _absolutized_payload()
    del payload["cases"]
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    message = str(excinfo.value)
    assert str(drift) in message
    assert "cases" in message


def test_empty_cases_list_fails_at_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    payload = _absolutized_payload()
    payload["cases"] = []
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    out = tmp_path / "report.json"
    assert cs.main(["--manifest", str(drift), "--output", str(out)]) == 1
    assert "cases" in capsys.readouterr().err
    assert not out.exists()


def test_duplicate_case_ids_fail_with_field(tmp_path: Path) -> None:
    payload = _absolutized_payload()
    payload["cases"][1]["id"] = payload["cases"][0]["id"]
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    message = str(excinfo.value)
    assert "cases[1].id" in message
    assert payload["cases"][0]["id"] in message


@pytest.mark.parametrize("kind", ["positve", "", 3, None])
def test_malformed_case_kinds_fail_with_field(tmp_path: Path, kind: object) -> None:
    payload = _absolutized_payload()
    payload["cases"][0]["kind"] = kind
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert "cases[0].kind" in str(excinfo.value)


@pytest.mark.parametrize(
    ("mangle", "why"),
    [
        (lambda case: case.pop("expectations"), "absent"),
        (lambda case: case.update(expectations={}), "empty"),
        (lambda case: case.update(expectations={"decision": "manual_review"}), "decision only"),
        (lambda case: case["expectations"].update(spot="the good spot"), "spot wrong type"),
        (lambda case: case["expectations"].update(checks=["gas_clearance"]), "checks wrong type"),
    ],
    ids=["absent", "empty", "decision-only", "spot-wrong-type", "checks-wrong-type"],
)
def test_unusable_positive_expectations_fail_with_field(
    tmp_path: Path, mangle, why: str
) -> None:
    payload = _absolutized_payload()
    mangle(payload["cases"][0])
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert "cases[0].expectations" in str(excinfo.value), why


def test_negative_case_requires_declared_error(tmp_path: Path) -> None:
    payload = _absolutized_payload()
    payload["cases"][2].pop("expected_error")
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert "cases[2].expected_error" in str(excinfo.value)

    thin = _absolutized_payload()
    thin["cases"][2]["expected_error"] = {"code": "placement_scene_unsupported"}
    thin_manifest = tmp_path / "thin.json"
    thin_manifest.write_text(json.dumps(thin))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(thin_manifest)
    assert "cases[2].expected_error" in str(excinfo.value)


def _forging_client(monkeypatch: pytest.MonkeyPatch, mutate) -> None:
    """Swap the suite's client for one that mutates every 200 response body,
    simulating a solver/API regression in the echoed response."""
    inner = cs._build_client()

    class _ShimResponse:
        def __init__(self, status_code: int, body: dict) -> None:
            self.status_code = status_code
            self.text = ""
            self._body = body

        def json(self) -> dict:
            return self._body

    class _ForgingClient:
        def post(self, *args: object, **kwargs: object) -> _ShimResponse:
            response = inner.post(*args, **kwargs)
            assert response.status_code == 200
            body = response.json()
            mutate(body)
            return _ShimResponse(200, body)

    monkeypatch.setattr(cs, "_get_client", lambda: _ForgingClient())


def test_forged_input_sha256_fails_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response whose echoed hash points at other bytes must not match."""
    _forging_client(
        monkeypatch,
        lambda body: body.setdefault("stats", {}).update(input_sha256="0" * 64),
    )
    report = cs.run_suite(MANIFEST)
    case = next(case for case in report["cases"] if case["kind"] == cs.KIND_POSITIVE)
    assert case["status"] == cs.STATUS_MISMATCHED
    assert case["input_sha_matches_file"] is False
    assert any("input_sha256" in mismatch for mismatch in case["mismatches"])
    assert report["ok"] is False


def test_missing_input_sha256_fails_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _forging_client(
        monkeypatch,
        lambda body: body.setdefault("stats", {}).pop("input_sha256", None),
    )
    report = cs.run_suite(MANIFEST)
    case = next(case for case in report["cases"] if case["kind"] == cs.KIND_POSITIVE)
    assert case["status"] == cs.STATUS_MISMATCHED
    assert case["input_sha256"] is None


def test_forged_input_sha256_fails_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _forging_client(
        monkeypatch,
        lambda body: body["stats"].update(input_sha256="f" * 64),
    )
    out = tmp_path / "report.json"
    assert cs.main(["--output", str(out)]) == 1
    assert json.loads(out.read_text())["ok"] is False
    err = capsys.readouterr().err
    assert "status=mismatched" in err
    assert "input_sha256" in err
