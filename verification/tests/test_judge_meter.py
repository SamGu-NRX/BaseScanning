"""Hostile answers must still produce a report, with bounded verification work."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from hsverify import e2e
from hsverify.resultcheck import RuleSet, WorkMeter

ROOT = Path(__file__).resolve().parents[2]
RULES = RuleSet.from_yaml((ROOT / "server/rules.yaml").read_text())
SCHEMAS = {
    "scene": {},
    "result": json.loads((ROOT / "server/schemas/result.schema.json").read_text()),
}
SCENE = {
    "meter": {"pos": [0, 4, 0], "wall_id": "w1"},
    "walls": [{"id": "w1", "baseline": [[-30, 0], [30, 0]]}],
}
ENDPOINT = e2e.Endpoint("/place", "application/json")
META = {
    "started_at": "2026-10-14",
    "server_ref": "public test fixture",
    "server_sha": "a" * 40,
    "endpoint": "/place",
    "load_average_1_5_15": [0, 0, 0],
    "peak_memory": {},
}


def probe():
    return e2e.SceneInput("hostile answer", SCENE, json.dumps(SCENE).encode())


def response():
    return {
        "schema_version": "1.0",
        "decision": "manual_review",
        "summary": "No spot chosen",
        "reasons": [],
        "policy": {
            "id": "demo",
            "version": "1",
            "auto_approve": False,
            "sources": ["public"],
            "rules_sha256": "a" * 64,
        },
        "spot": None,
        "route": None,
        "checks": [],
        "missing_evidence": [],
        "ends": {
            side: {"kind": "limit", "s_ft": s, "point": [s, 0]}
            for side, s in (("left", -30), ("right", 30))
        },
        "sweep": [],
        "stats": {
            "candidates": 0,
            "pass": 0,
            "unsure": 0,
            "fail": 0,
            "elapsed_ms": 1,
            "input_sha256": hashlib.sha256(probe().raw).hexdigest(),
        },
    }


def serve(monkeypatch, result):
    assert not e2e.schema_errors(result, SCHEMAS["result"])
    monkeypatch.setattr(e2e, "post", lambda *args: (200, json.dumps(result).encode(), 1))


def forbid_properties(*args, **kwargs):
    raise AssertionError("a rejected answer reached property comparisons")


@pytest.mark.parametrize("exception", [OverflowError, ValueError, RuntimeError])
def test_check_exception_is_written_to_report(monkeypatch, tmp_path, exception):
    serve(monkeypatch, response())

    def crash(*args, **kwargs):
        raise exception("hostile response")

    monkeypatch.setattr(e2e, "invariant_problems", crash)
    monkeypatch.setattr(e2e, "property_problems", forbid_properties)
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES)
    assert record["status"] == "fail"
    assert record["problems"] == [f"invariants: {exception.__name__}: hostile response"]
    report = e2e.write_report(tmp_path, META, [record])
    assert not report["contract_ok"]
    assert json.loads((tmp_path / "report.json").read_text())["records"][0] == record


def test_failed_invariants_skip_properties(monkeypatch):
    serve(monkeypatch, response())
    monkeypatch.setattr(e2e, "invariant_problems", lambda *args, **kwargs: ["bad invariant"])
    monkeypatch.setattr(e2e, "property_problems", forbid_properties)
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES)
    assert record["contract_problems"] == ["bad invariant"]


def test_original_and_properties_share_one_meter(monkeypatch):
    serve(monkeypatch, response())
    meters = []

    def invariants(*args, meter, **kwargs):
        meters.append(meter)
        meter.charge(4)
        return []

    def properties(*args, meter, **kwargs):
        meters.append(meter)
        meter.charge(5)
        return []

    monkeypatch.setattr(e2e, "invariant_problems", invariants)
    monkeypatch.setattr(e2e, "property_problems", properties)
    monkeypatch.setattr(e2e, "geometry_admission_problems", lambda *args, **kwargs: [])
    meter = WorkMeter(limit=9)
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES, meter=meter)
    assert record["status"] == "pass"
    assert record["verification_samples"] == 9
    assert meters == [meter, meter]


def test_meter_limit_is_one_specific_report_problem(monkeypatch, tmp_path):
    serve(monkeypatch, response())

    def invariants(*args, meter, **kwargs):
        meter.charge(4)
        return []

    monkeypatch.setattr(e2e, "invariant_problems", invariants)
    monkeypatch.setattr(e2e, "property_problems", forbid_properties)
    monkeypatch.setattr(e2e, "geometry_admission_problems", lambda *args, **kwargs: [])
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES, meter=WorkMeter(limit=3))
    assert record["problems"] == ["verification work limit exceeded (4 samples)"]
    assert record["verification_samples"] == 4
    assert not e2e.write_report(tmp_path, META, [record])["contract_ok"]


def test_oversized_integer_is_an_admission_problem(monkeypatch, tmp_path):
    result = response()
    result["sweep"] = [
        {"wall_id": "w1", "start_ft": [0, 10**400], "outcome": "fail", "failing": [], "unsure": []}
    ]
    serve(monkeypatch, result)
    monkeypatch.setattr(e2e, "property_problems", forbid_properties)
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES)
    assert record["status"] == "fail"
    assert any("finite" in p for p in record["problems"])
    assert not any("OverflowError" in p for p in record["problems"])
    assert record["verification_samples"] == 0
    assert not e2e.write_report(tmp_path, META, [record])["contract_ok"]


def spot(wall_id):
    return {
        "outcome": "fail",
        "wall_id": wall_id,
        "segment": 0,
        "span_ft": [1, 1 + RULES.width_ft],
        "width_ft": RULES.width_ft,
        "depth_ft": RULES.depth_ft,
        "height_ft": RULES.battery_height_ft,
        "footprint": [
            [1, 0],
            [1 + RULES.width_ft, 0],
            [1 + RULES.width_ft, RULES.depth_ft],
            [1, RULES.depth_ft],
        ],
        "center": [1 + RULES.width_ft / 2, RULES.depth_ft / 2],
        "along": [1, 0],
        "outward": [0, 1],
        "meter_offset_ft": [1 + RULES.width_ft / 2, RULES.depth_ft / 2],
        "route_length_ft": None,
    }


@pytest.mark.parametrize("field", ["sweep", "spot", "nearest_considered"])
def test_unknown_wall_is_rejected_before_geometry(monkeypatch, tmp_path, field):
    result = response()
    if field == "sweep":
        result[field] = [
            {
                "wall_id": "missing",
                "start_ft": [1, 2],
                "outcome": "fail",
                "failing": [],
                "unsure": [],
            }
        ]
    else:
        result[field] = spot("missing")
    serve(monkeypatch, result)
    monkeypatch.setattr(e2e, "property_problems", forbid_properties)
    record = e2e.judge("unused", ENDPOINT, probe(), SCHEMAS, RULES)
    assert record["status"] == "fail"
    assert any("unknown wall_id 'missing'" in p for p in record["problems"])
    assert record["verification_samples"] == 0
    assert not e2e.write_report(tmp_path, META, [record])["contract_ok"]


@pytest.mark.parametrize("failure", ["admission", "invariants"])
def test_invalid_variant_never_reaches_comparisons(monkeypatch, failure):
    original = response()
    other = copy.deepcopy(original)
    if failure == "admission":
        other["nearest_considered"] = spot("missing")
    serve(monkeypatch, original)
    monkeypatch.setattr(e2e, "answer", lambda *args: (other, None))
    monkeypatch.setattr(e2e, "with_ground_short_of", lambda *args: None)
    monkeypatch.setattr(e2e, "with_less_coverage", lambda *args: None)
    monkeypatch.setattr(e2e, "with_more_error", lambda *args: copy.deepcopy(SCENE))
    monkeypatch.setattr(e2e, "tape_to_tap", lambda *args: None)
    monkeypatch.setattr(e2e, "more_error_problems", forbid_properties)
    monkeypatch.setattr(e2e, "less_coverage_problems", forbid_properties)
    if failure == "admission":
        monkeypatch.setattr(e2e, "invariant_problems", forbid_properties)
    else:
        monkeypatch.setattr(e2e, "invariant_problems", lambda *args, **kwargs: ["bad variant hash"])
    problems = e2e.property_problems("unused", ENDPOINT, probe(), original, SCHEMAS, RULES)
    expected = "unknown wall_id" if failure == "admission" else "bad variant hash"
    assert any(p.startswith("more error:") and expected in p for p in problems)


def test_variants_and_comparisons_charge_the_judge_meter(monkeypatch):
    original = response()
    serve(monkeypatch, original)
    monkeypatch.setattr(e2e, "answer", lambda *args: (original, None))
    monkeypatch.setattr(e2e, "geometry_admission_problems", lambda *args, **kwargs: [])
    monkeypatch.setattr(e2e, "with_ground_short_of", lambda *args: None)
    monkeypatch.setattr(e2e, "with_less_coverage", lambda *args: None)
    monkeypatch.setattr(e2e, "with_more_error", lambda *args: copy.deepcopy(SCENE))
    monkeypatch.setattr(e2e, "tape_to_tap", lambda *args: None)
    received = []

    def invariants(*args, meter, **kwargs):
        received.append(meter)
        meter.charge()
        return []

    def compare(*args, meter):
        received.append(meter)
        meter.charge(2)
        return []

    monkeypatch.setattr(e2e, "invariant_problems", invariants)
    monkeypatch.setattr(e2e, "more_error_problems", compare)
    meter = WorkMeter(limit=4)
    problems = e2e.property_problems(
        "unused", ENDPOINT, probe(), original, SCHEMAS, RULES, meter=meter
    )
    assert not problems
    assert received == [meter, meter, meter]
    assert meter.samples == 4


def test_unknown_rules_keep_rule_independent_properties(monkeypatch):
    original = response()
    changed = copy.deepcopy(original)
    changed["summary"] = "Changed answer for the identical scene"
    serve(monkeypatch, changed)
    scene = copy.deepcopy(SCENE)
    scene["coverage"] = {"observed": [{"band": "wall", "span_ft": [-30, 30]}]}
    item = e2e.SceneInput("rule override", scene, json.dumps(scene).encode())
    variants = []
    checked_rules = []

    def answer(url, endpoint, transformed):
        variants.append(transformed.name.split("~")[-1])
        return original, None

    def invariants(*args, rules, **kwargs):
        checked_rules.append(rules)
        return []

    def public_rule_transform(*args):
        raise AssertionError("private override judged using a public-rule transform")

    monkeypatch.setattr(e2e, "answer", answer)
    monkeypatch.setattr(e2e, "invariant_problems", invariants)
    monkeypatch.setattr(e2e, "with_ground_short_of", public_rule_transform)
    monkeypatch.setattr(e2e, "with_more_error", public_rule_transform)
    monkeypatch.setattr(e2e, "tape_to_tap", public_rule_transform)
    problems = e2e.property_problems(
        "unused", ENDPOINT, item, original, SCHEMAS, RULES, rule_checks=False
    )
    assert problems == ["sending the same scene twice gave different results"]
    assert variants == ["mirrored", "no coverage", "less coverage"]
    assert checked_rules == [None, None, None]
