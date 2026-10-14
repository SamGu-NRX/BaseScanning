"""Metadata admission and lookup costs for large, schema-shaped responses."""

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest
from test_resultcheck import RULES, SCENE, check, result

import hsverify.resultcheck as checks


class ComparedName(str):
    comparisons = 0

    def __eq__(self, other):
        type(self).comparisons += 1
        if type(self).comparisons > 100_000:
            raise AssertionError("metadata lookup performed more than 100,000 comparisons")
        return super().__eq__(other)

    __hash__ = str.__hash__


def test_distinct_evidence_ids_use_linear_name_lookups():
    names = [ComparedName(f"clearance_{i}") for i in range(12_000)]
    answer = result(spot=False, checks=[], complete=False)
    answer["reasons"] = [{"code": "unobserved_area", "message": ""}]
    answer["checks"] = [
        check(outcome="unsure", measured=None, cause="unobserved") | {"id": name} for name in names
    ]
    answer["missing_evidence"] = [
        {"kind": "band", "band": "ground", "checks": [name], "message": ""} for name in names
    ]
    ComparedName.comparisons = 0
    assert checks.missing_evidence_problems(SCENE, answer) == []
    assert ComparedName.comparisons < 4 * len(names)


def test_invalid_width_message_deduplication_is_linear():
    # Instrument only the diagnostic buffer. The old list-membership search calls
    # ComparedName.__eq__ for every previous diagnostic; hash deduplication does not.
    class DiagnosticBuffer(list):
        def append(self, message):
            super().append(ComparedName(message))

    class InstrumentBuffer(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any(isinstance(t, ast.Name) and t.id == "problems" for t in node.targets):
                node.value = ast.Call(
                    func=ast.Name(id="DiagnosticBuffer", ctx=ast.Load()), args=[], keywords=[]
                )
            return node

    source = ast.parse(inspect.getsource(checks.reported_dimension_problems))
    source = ast.fix_missing_locations(InstrumentBuffer().visit(source))
    scope = checks.reported_dimension_problems.__globals__ | {"DiagnosticBuffer": DiagnosticBuffer}
    exec(compile(source, "<instrumented dimension validation>", "exec"), scope)
    answer = result(spot=False, checks=[], complete=False)
    answer["checks"] = [
        check(threshold=-1) | {"id": f"width_{i}", "rule": {"key": "battery.width_ft"}}
        for i in range(4_000)
    ]
    ComparedName.comparisons = 0
    problems = scope["reported_dimension_problems"](answer, RULES)
    assert len(problems) == 4_000
    assert problems[0] == "width_0 must be finite and positive"
    assert problems[-1] == "width_3999 must be finite and positive"
    assert ComparedName.comparisons < 4 * len(problems)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("sweep", checks.MAX_RESULT_RUNS),
        ("checks", checks.MAX_RESULT_CHECKS),
        ("missing_evidence", checks.MAX_EVIDENCE_REQUESTS),
    ],
)
def test_result_count_caps_accept_boundary_and_reject_next(field, limit):
    answer = {field: [None] * limit}
    assert checks.ingestion_problems(answer, "result") == []
    answer[field].append(None)
    assert checks.ingestion_problems(answer, "result") == [
        f"result ingestion limit exceeded: {field} exceeds {limit} entries"
    ]


@pytest.mark.parametrize("field", ["sweep", "checks", "missing_evidence"])
def test_named_result_caps_allow_schema_valid_responses(field):
    fixture = json.loads((Path(__file__).parent / "fixtures/wide-public-pass.json").read_text())
    answer = copy.deepcopy(fixture["result"])
    request = {"kind": "band", "message": "", "band": "ground", "checks": ["ground_surface"]}
    first_check = answer["checks"][0]
    first_check.update(label="", reason="", rule={"key": "", "source": "", "placeholder": False})
    entries = {
        "sweep": (answer["sweep"][0], checks.MAX_RESULT_RUNS),
        "checks": (first_check, checks.MAX_RESULT_CHECKS),
        "missing_evidence": (request, checks.MAX_EVIDENCE_REQUESTS),
    }
    entry, limit = entries[field]
    answer[field] = (
        answer[field] + [entry] * (limit - len(answer[field]))
        if field == "checks"
        else [entry] * limit
    )
    schema = json.loads(
        (Path(__file__).parents[2] / "server/schemas/result.schema.json").read_text()
    )
    assert checks.schema_errors(answer, schema) == []
    if field == "sweep":
        answer["spot"] = answer["route"] = None
        answer["decision"] = "reject"
    elif field == "missing_evidence":
        answer["decision"] = "manual_review"
    rules = checks.RuleSet.from_yaml((Path(__file__).parents[2] / "server/rules.yaml").read_text())
    assert checks.invariant_problems(fixture["scene"], answer, rules=rules) == []


def test_scene_geometry_counts_keep_existing_schema_caps():
    wall = {"id": "wall", "baseline": [[0, 0]] * 500}
    scene = {"walls": [wall] * 50}
    assert checks.ingestion_problems(scene, "scene") == []
    scene["walls"].append(wall)
    assert "walls exceeds 50 entries" in checks.ingestion_problems(scene, "scene")[0]
    assert (
        "baseline exceeds 500 vertices"
        in checks.ingestion_problems({"walls": [wall | {"baseline": [[0, 0]] * 501}]}, "scene")[0]
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([0] * checks.MAX_NUMERIC_LEAVES, "numeric leaves"),
        ([None] * (checks.MAX_METADATA_NODES - 1), "JSON nodes"),
        ("x" * checks.MAX_METADATA_CHARACTERS, "characters"),
    ],
)
def test_generic_metadata_caps_accept_boundary_and_reject_next(value, message):
    assert checks.ingestion_problems(value) == []
    over = value + ("x" if isinstance(value, str) else [value[0]])
    assert message in checks.ingestion_problems(over)[0]


def test_nesting_cap_precedes_recursive_schema_validation(monkeypatch):
    nested = 0
    for _ in range(checks.MAX_METADATA_DEPTH):
        nested = [nested]
    assert checks.ingestion_problems(nested) == []

    def unexpected_validator(*args):
        pytest.fail("oversized metadata reached recursive schema validation")

    monkeypatch.setattr(checks, "Draft202012Validator", unexpected_validator)
    assert checks.schema_errors([nested], {}) == [
        f"document ingestion limit exceeded: nesting exceeds {checks.MAX_METADATA_DEPTH}"
    ]


def test_ingestion_failure_precedes_geometry(monkeypatch):
    def unexpected_dimensions(*args):
        pytest.fail("oversized metadata reached dimension validation")

    monkeypatch.setattr(checks, "reported_dimension_problems", unexpected_dimensions)
    answer = {"checks": [None] * (checks.MAX_RESULT_CHECKS + 1)}
    assert checks.geometry_admission_problems(SCENE, answer, RULES) == [
        f"result ingestion limit exceeded: checks exceeds {checks.MAX_RESULT_CHECKS} entries"
    ]


def test_invalid_schema_stops_after_bounded_diagnostics():
    errors = checks.schema_errors([None] * 10_000, {"type": "array", "items": {"type": "number"}})
    assert len(errors) == checks.MAX_SCHEMA_PROBLEMS


def test_case_queries_have_a_fixed_scan_bound():
    at_cap = {f"rule_{i}": 0 for i in range(checks.MAX_CASE_RULE_QUERIES)}
    assert checks.case_metadata_problems(at_cap) == []
    at_cap["one_more"] = 0
    assert checks.case_metadata_problems(at_cap) == [
        "case ingestion limit exceeded: more than 64 rule queries"
    ]
    assert checks.expectation_problems(
        {"checks": [{"match": "gas"}] * (checks.MAX_CASE_RULE_QUERIES + 1)}, {}
    ) == ["case ingestion limit exceeded: more than 64 rule queries"]
