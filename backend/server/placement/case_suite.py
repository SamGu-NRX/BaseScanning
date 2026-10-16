"""Offline batch evaluator for the placement boundary.

Runs the case manifest in ``server/placement/tests/data/cases/`` against the
unchanged top-level solver, driven through ``POST /v1/placements`` on the
placement adapter. It exists so placement work is judged on independently
authored geometry instead of repeated references to one scene.

What it does:

- Loads the manifest and verifies every fixture exists and matches its pinned
  sha256. Missing or drifted files fail with their paths.
- Validates every source fixture against the committed base-scene/v0 contract.
- Positive cases: posts the wire scene to the placement endpoint twice,
  verifies the two executions agree on everything except ``stats.elapsed_ms``,
  validates the response against the result schema, and evaluates the
  manifest's explicit expectations about the chosen spot, the per-check
  outcomes and unsure causes, the missing-evidence requests, the failing sweep
  runs and the chain ends. Decision codes are expectations too, but every
  pinned decision here is a manual review forced by geometry (no fully passing
  candidate exists), so it holds under the demo and strict policies alike.
- Negative cases are source-only: the W7 bridge that would convert sources
  does not exist in this packet, so they are reported as awaiting conversion
  with the error code and pointer the bridge must eventually produce. They are
  never executed and never reported as a successful conversion.

The solver's policy object is recorded verbatim in the report (id, version,
``rules_sha256``, notice) so any drift in rule sources is visible, while the
expectations stay geometry-only.

Usage::

    uv run --frozen python -m server.placement.case_suite --output=/tmp/report.json

Exit code 0 means every case matched or is legitimately awaiting conversion;
1 means a mismatch, an execution error, or a load problem (which is also
printed to stderr with the offending paths).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from server.placement import adapter

MANIFEST_SCHEMA_VERSION = "placement-case-manifest/v0"
REPORT_SCHEMA_VERSION = "hss-placement-case-report/v0"
ELAPSED_KEY = "elapsed_ms"
KIND_POSITIVE = "positive"
KIND_NEGATIVE = "negative_source"
STATUS_MATCHED = "matched"
STATUS_MISMATCHED = "mismatched"
STATUS_AWAITING_CONVERSION = "awaiting_conversion"
STATUS_ERRORED = "error"
DEFAULT_MANIFEST = Path(__file__).parent / "tests" / "data" / "cases" / "manifest.json"
_REPO_ROOT = Path(__file__).parents[3]
RESULT_SCHEMA = _REPO_ROOT / "server" / "schemas" / "result.schema.json"
SOURCE_SCHEMA = _REPO_ROOT / "backend" / "server" / "contracts" / "schemas" / "scene.v0.schema.json"
# Recognized expectation keys and the container shape each must have.
EXPECTATION_KEYS = ("decision", "spot", "checks", "missing_evidence", "sweep_failing_runs", "ends")
EXPECTATION_SHAPES = {
    "decision": str,
    "spot": dict,
    "checks": dict,
    "missing_evidence": list,
    "sweep_failing_runs": list,
    "ends": dict,
}
# A positive case's expectations are meaningful only when they pin the decision
# and at least one geometric expectation that can actually mismatch.
GEOMETRIC_EXPECTATION_KEYS = ("spot", "checks", "missing_evidence", "sweep_failing_runs", "ends")


class CaseSuiteError(RuntimeError):
    """Raised when the manifest or its fixtures are missing or drifted."""


@dataclass(frozen=True)
class CaseRecord:
    """One manifest case with its fixtures resolved to absolute paths."""

    id: str
    kind: str
    source_path: Path
    wire_path: Path | None
    expected_error: dict[str, str] | None
    expectations: dict[str, Any]
    note: str | None = None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_fixture(name: str, fixture: dict[str, Any], base: Path, problems: list[str]) -> Path:
    rel = fixture.get("path")
    expected_sha = fixture.get("sha256")
    if not isinstance(rel, str) or not rel:
        problems.append(f"case fixture has no usable path: {fixture!r}")
        raise CaseSuiteError(_problems_message(problems))
    path = base / rel
    if not path.is_file():
        problems.append(f"missing fixture file for {name!r}: {path}")
        raise CaseSuiteError(_problems_message(problems))
    if not isinstance(expected_sha, str) or not expected_sha:
        problems.append(f"fixture {path} has no pinned sha256")
        raise CaseSuiteError(_problems_message(problems))
    actual = _file_sha256(path)
    if actual != expected_sha:
        problems.append(f"drifted fixture for {name!r}: {path} sha256 {actual} != pinned {expected_sha}")
        raise CaseSuiteError(_problems_message(problems))
    return path


def _expectations_problem(expectations: Any) -> str | None:
    """Return a named problem when a positive case's expectations could not
    fail a solver regression, or ``None`` when they can."""
    if not isinstance(expectations, dict):
        return "must be an object of pinned expectations"
    for key in EXPECTATION_KEYS:
        if key in expectations and not isinstance(expectations[key], EXPECTATION_SHAPES[key]):
            shape = (
                "a string"
                if EXPECTATION_SHAPES[key] is str
                else ("an object" if EXPECTATION_SHAPES[key] is dict else "an array")
            )
            return f".{key} must be {shape} (got {type(expectations[key]).__name__})"
    if not isinstance(expectations.get("decision"), str) or not expectations.get("decision"):
        return "must pin a nonempty 'decision' string"
    geometric = [
        key
        for key in GEOMETRIC_EXPECTATION_KEYS
        if isinstance(expectations.get(key), EXPECTATION_SHAPES[key]) and expectations[key]
    ]
    if not geometric:
        return (
            "must pin at least one geometric expectation with content "
            f"(one of {', '.join(GEOMETRIC_EXPECTATION_KEYS)}), so a solver change can fail the case"
        )
    return None


def _problems_message(problems: list[str]) -> str:
    return "case suite fixtures failed to load:\n" + "\n".join(f"- {p}" for p in problems)


def load_cases(path: Path) -> list[CaseRecord]:
    """Load the manifest at ``path`` and verify its fixtures.

    The loader is the gate against a silently weakened manifest: it requires a
    nonempty ``cases`` list of unique nonempty ids with a recognized ``kind``
    (``positive`` or ``negative_source``), the fixture records each kind needs
    (positive: ``source`` plus ``wire``; negative: ``source``), meaningful
    positive expectations (a pinned ``decision`` plus at least one geometric
    expectation of the right shape) and a negative ``expected_error`` with
    nonempty ``code`` and ``pointer`` strings. Fixture existence and pinned
    sha256s are checked before any record is returned. Every rejection names
    the manifest path and the offending field (``cases[N].field``) instead of
    guessing a default.

    Raises :class:`CaseSuiteError` with the manifest path and field on any
    malformed entry, and naming every missing or drifted fixture path.
    """
    problems: list[str] = []
    if not path.is_file():
        raise CaseSuiteError(_problems_message([f"missing case manifest: {path}"]))
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise CaseSuiteError(f"case manifest {path}: top level must be a JSON object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(f"{path}: schema_version {manifest.get('schema_version')!r} != {MANIFEST_SCHEMA_VERSION!r}")
        raise CaseSuiteError(_problems_message(problems))
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CaseSuiteError(
            f"case manifest {path}: field 'cases' must be a nonempty list of case objects "
            f"(got {'nothing' if raw_cases is None else type(raw_cases).__name__})"
        )
    base = path.parent
    records: list[CaseRecord] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_cases):
        field = f"cases[{index}]"
        if not isinstance(raw, dict):
            raise CaseSuiteError(f"case manifest {path}: field {field} must be an object (got {type(raw).__name__})")
        name = raw.get("id")
        if not isinstance(name, str) or not name:
            raise CaseSuiteError(f"case manifest {path}: field {field}.id must be a nonempty string (got {name!r})")
        if name in seen_ids:
            raise CaseSuiteError(f"case manifest {path}: field {field}.id duplicates id {name!r} from an earlier case")
        seen_ids.add(name)
        kind = raw.get("kind")
        if kind not in (KIND_POSITIVE, KIND_NEGATIVE):
            raise CaseSuiteError(
                f"case manifest {path}: field {field}.kind {kind!r} is not a recognized case kind "
                f"(expected {KIND_POSITIVE!r} or {KIND_NEGATIVE!r})"
            )
        source = raw.get("source")
        if not isinstance(source, dict):
            raise CaseSuiteError(
                f"case manifest {path}: field {field}.source must be an object with a path and a pinned sha256"
            )
        source_path = _check_fixture(name, source, base, problems)
        expectations: dict[str, Any] = {}
        expected_error: dict[str, str] | None = None
        if kind == KIND_POSITIVE:
            wire = raw.get("wire")
            if not isinstance(wire, dict):
                raise CaseSuiteError(
                    f"case manifest {path}: field {field}.wire must be an object with a path and a pinned "
                    "sha256 (a positive case is executed, so its wire fixture is mandatory)"
                )
            wire_path = _check_fixture(name, wire, base, problems)
            expectations = raw.get("expectations")
            problem = _expectations_problem(expectations)
            if problem is not None:
                raise CaseSuiteError(f"case manifest {path}: field {field}.expectations {problem}")
            expectations = dict(expectations)
        else:
            wire_path = None
            expected_error = raw.get("expected_error")
            if (
                not isinstance(expected_error, dict)
                or not isinstance(expected_error.get("code"), str)
                or not expected_error.get("code")
                or not isinstance(expected_error.get("pointer"), str)
                or not expected_error.get("pointer")
            ):
                raise CaseSuiteError(
                    f"case manifest {path}: field {field}.expected_error must be an object with a nonempty "
                    "'code' and 'pointer' string (the error the future converter must produce)"
                )
        records.append(
            CaseRecord(
                id=name,
                kind=kind,
                source_path=source_path,
                wire_path=wire_path,
                expected_error=expected_error,
                expectations=expectations,
                note=raw.get("note"),
            )
        )
    return records


def _case_paths(record: CaseRecord, manifest_dir: Path) -> dict[str, Any]:
    """Fixture paths as authored in the manifest, made relative when they live
    beside it; copies of the manifest elsewhere keep their absolute paths."""

    def as_authored(path: Path) -> str:
        try:
            return path.relative_to(manifest_dir).as_posix()
        except ValueError:
            return path.as_posix()

    return {
        "source_path": as_authored(record.source_path),
        "wire_path": as_authored(record.wire_path) if record.wire_path else None,
    }


def _evaluate_expectations(expectations: dict[str, Any], body: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    expected_decision = expectations.get("decision")
    if expected_decision is not None and body.get("decision") != expected_decision:
        mismatches.append(f"decision {body.get('decision')!r} != expected {expected_decision!r}")
    expected_spot = expectations.get("spot")
    if expected_spot is not None:
        spot = body.get("spot")
        if spot is None:
            mismatches.append(f"spot is None but expected {expected_spot!r}")
        else:
            for key, value in expected_spot.items():
                if spot.get(key) != value:
                    mismatches.append(f"spot.{key} {spot.get(key)!r} != expected {value!r}")
    checks_by_id = {check["id"]: check for check in body.get("checks", [])}
    for check_id, expected in (expectations.get("checks") or {}).items():
        check = checks_by_id.get(check_id)
        if check is None:
            mismatches.append(f"check {check_id!r} missing from response")
            continue
        if check.get("outcome") != expected.get("outcome"):
            mismatches.append(
                f"check {check_id!r} outcome {check.get('outcome')!r} != expected {expected.get('outcome')!r}"
            )
        if "unsure_cause" in expected and check.get("unsure_cause") != expected["unsure_cause"]:
            mismatches.append(
                f"check {check_id!r} unsure_cause {check.get('unsure_cause')!r} != expected {expected['unsure_cause']!r}"
            )
    for expected in expectations.get("missing_evidence") or []:
        found = any(
            all(actual.get(key) == value for key, value in expected.items())
            for actual in body.get("missing_evidence", [])
        )
        if not found:
            mismatches.append(f"missing_evidence entry not found: {expected!r}")
    sweep = body.get("sweep", [])
    for expected in expectations.get("sweep_failing_runs") or []:
        found = any(
            run.get("start_ft") == expected.get("start_ft")
            and set(expected.get("failing") or []).issubset(run.get("failing") or [])
            for run in sweep
        )
        if not found:
            mismatches.append(f"failing sweep run not found: {expected!r}")
    expected_ends = expectations.get("ends") or {}
    for side, kind in expected_ends.items():
        actual_kind = (body.get("ends") or {}).get(side, {}).get("kind")
        if actual_kind != kind:
            mismatches.append(f"end {side!r} kind {actual_kind!r} != expected {kind!r}")
    return mismatches


def _record_check(check: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "outcome", "unsure_cause", "measured_ft", "plus_minus_ft", "threshold_ft", "subject")
    return {key: check.get(key) for key in keys if check.get(key) is not None}


def _schema_validator(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text()))


def _schema_errors(validator: Draft202012Validator, document: Any) -> list[str]:
    return [
        f"schema error at {list(error.absolute_path)}: {error.message}"
        for error in list(validator.iter_errors(document))[:10]
    ]


def _positive_case(
    record: CaseRecord,
    client: Any,
    result_validator: Draft202012Validator,
    manifest_dir: Path,
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "executed": False,
        "executions_agree": None,
        "mismatches": [],
        "errors": [],
        **_case_paths(record, manifest_dir),
    }
    if record.note:
        case["note"] = record.note
    wire_bytes = record.wire_path.read_bytes()
    responses = []
    for _ in range(2):
        response = client.post("/v1/placements", content=wire_bytes, headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            case["errors"].append(
                {
                    "code": "http_error",
                    "status_code": response.status_code,
                    "message": response.text,
                }
            )
            case["status"] = STATUS_ERRORED
            return case
        responses.append(response.json())
    first, second = responses
    case["executed"] = True
    case["executions_agree"] = _agree_without_elapsed(first, second)
    stats = first.get("stats", {})
    case["input_sha256"] = stats.get("input_sha256")
    posted_sha = _file_sha256(record.wire_path)
    case["input_sha_matches_file"] = case["input_sha256"] == posted_sha
    if not case["input_sha_matches_file"]:
        # A response tied to different bytes must not pass, no matter how well
        # its placement fields happen to satisfy the expectations.
        case["mismatches"].append(
            f"stats.input_sha256 {case['input_sha256']!r} does not match the sha256 of the "
            f"posted wire bytes ({posted_sha})"
        )
    case["policy"] = first.get("policy")
    case["decision"] = first.get("decision")
    case["spot"] = first.get("spot")
    case["checks"] = [_record_check(check) for check in first.get("checks", [])]
    case["missing_evidence"] = first.get("missing_evidence", [])
    case["ends"] = first.get("ends")
    case["sweep_failing"] = [
        {key: run.get(key) for key in ("start_ft", "outcome", "failing", "unsure")}
        for run in first.get("sweep", [])
        if run.get("outcome") != "pass"
    ]
    case["objects_not_used"] = first.get("objects_not_used", [])
    schema_errors = _schema_errors(result_validator, first)
    if schema_errors:
        case["errors"].append({"code": "result_schema_invalid", "message": "; ".join(schema_errors)})
    if not case["executions_agree"]:
        case["mismatches"].append("the two direct executions disagree beyond stats.elapsed_ms")
    case["mismatches"].extend(_evaluate_expectations(record.expectations, first))
    case["status"] = STATUS_MISMATCHED if case["mismatches"] or case["errors"] else STATUS_MATCHED
    return case


def _agree_without_elapsed(first: dict[str, Any], second: dict[str, Any]) -> bool:
    def without_elapsed(body: dict[str, Any]) -> dict[str, Any]:
        stats = dict(body.get("stats") or {})
        stats.pop(ELAPSED_KEY, None)
        return {**body, "stats": stats}

    return json.dumps(without_elapsed(first), sort_keys=True) == json.dumps(without_elapsed(second), sort_keys=True)


def _negative_case(record: CaseRecord, manifest_dir: Path) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "status": STATUS_AWAITING_CONVERSION,
        "executed": False,
        "expected_error": record.expected_error,
        "mismatches": [],
        "errors": [],
        **_case_paths(record, manifest_dir),
    }
    if record.note:
        case["note"] = record.note
    return case


def run_suite(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Run every case in the manifest and return the full report dict."""
    records = load_cases(manifest_path)
    manifest_dir = manifest_path.parent
    source_validator = _schema_validator(SOURCE_SCHEMA)
    result_validator = _schema_validator(RESULT_SCHEMA)
    cases: list[dict[str, Any]] = []
    for record in records:
        schema_errors = _schema_errors(source_validator, json.loads(record.source_path.read_text()))
        if record.kind == KIND_POSITIVE:
            case = _positive_case(record, _get_client(), result_validator, manifest_dir)
            if schema_errors:
                case["errors"].append({"code": "source_schema_invalid", "message": "; ".join(schema_errors)})
                case["status"] = STATUS_ERRORED
        elif record.kind == KIND_NEGATIVE:
            case = _negative_case(record, manifest_dir)
            if schema_errors:
                case["errors"].append({"code": "source_schema_invalid", "message": "; ".join(schema_errors)})
                case["status"] = STATUS_ERRORED
        else:
            case = {
                "id": record.id,
                "kind": record.kind,
                "status": STATUS_ERRORED,
                "executed": False,
                "errors": [{"code": "unknown_case_kind", "message": f"kind {record.kind!r} is not supported"}],
                "mismatches": [],
                **_case_paths(record, manifest_dir),
            }
        cases.append(case)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": {
            "path": manifest_path.as_posix(),
            "sha256": _file_sha256(manifest_path),
        },
        "cases": cases,
    }
    report["summary"] = _summary(cases)
    report["ok"] = report["summary"]["mismatched"] == 0 and report["summary"]["errored"] == 0
    return report


def _summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    statuses = [case["status"] for case in cases]
    return {
        "cases": len(cases),
        "positive": sum(1 for case in cases if case["kind"] == KIND_POSITIVE),
        "negative_source": sum(1 for case in cases if case["kind"] == KIND_NEGATIVE),
        "matched": statuses.count(STATUS_MATCHED),
        "mismatched": statuses.count(STATUS_MISMATCHED),
        "awaiting_conversion": statuses.count(STATUS_AWAITING_CONVERSION),
        "errored": statuses.count(STATUS_ERRORED),
    }


def _build_client() -> Any:
    modules = adapter.load_api()
    return TestClient(modules["api"].app)


_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline placement case suite.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to the case manifest.")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the JSON report.")
    args = parser.parse_args(argv)
    try:
        report = run_suite(args.manifest)
    except CaseSuiteError as error:
        print(error, file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    summary = report["summary"]
    print(
        f"cases={summary['cases']} matched={summary['matched']} "
        f"awaiting_conversion={summary['awaiting_conversion']} "
        f"mismatched={summary['mismatched']} errored={summary['errored']}"
    )
    for case in report["cases"]:
        if case["status"] not in (STATUS_MATCHED, STATUS_AWAITING_CONVERSION):
            print(f"case {case['id']}: status={case['status']}", file=sys.stderr)
            for mismatch in case["mismatches"]:
                print(f"  mismatch: {mismatch}", file=sys.stderr)
            for error in case["errors"]:
                print(f"  error: {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
