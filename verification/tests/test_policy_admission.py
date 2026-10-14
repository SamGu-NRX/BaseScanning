import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from segmented_wall import segmented_wall_scene
from test_judge_meter import META

from hsverify import e2e
from hsverify.resultcheck import RuleSet, public_dimension_problems, reported_rules

ROOT = Path(__file__).parents[2]
RULES = RuleSet.from_yaml((ROOT / "server/rules.yaml").read_text())
SCHEMAS = {
    kind: json.loads((ROOT / f"server/schemas/{kind}.schema.json").read_text())
    for kind in ("scene", "result")
}


def replies():
    return json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-workload.json").read_text()
    )


@pytest.mark.parametrize("field", ["width_ft", "depth_ft", "height_ft"])
def test_matching_public_policy_keeps_loaded_dimensions(field):
    answer = replies()["surveyed wall"]
    answer["spot"].pop("footprint")
    answer["spot"].pop("span_ft")
    answer["spot"][field] = 1
    assert reported_rules(answer, RULES) is RULES


@pytest.mark.parametrize("wrong", [False, True])
def test_public_policy_width_is_checked_without_case_assumptions(monkeypatch, wrong):
    answers = replies()
    if wrong:
        spot = answers["surveyed wall"]["spot"]
        lo = spot["span_ft"][0]
        spot.update(
            width_ft=1,
            span_ft=[lo, lo + 1],
            footprint=[[lo, 0], [lo + 1, 0], [lo + 1, RULES.depth_ft], [lo, RULES.depth_ft]],
            center=[lo + 0.5, RULES.depth_ft / 2],
            meter_offset_ft=[lo + 0.5, RULES.depth_ft / 2],
        )
    seen_rules = []
    original = e2e.invariant_problems

    def invariant(*args, rules=None, **kwargs):
        seen_rules.append(rules)
        return original(*args, rules=rules, **kwargs)

    def post(url, endpoint, item):
        answer = copy.deepcopy(answers[item.name])
        answer["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(answer).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    monkeypatch.setattr(e2e, "invariant_problems", invariant)
    scene = segmented_wall_scene(1)
    item = e2e.SceneInput("surveyed wall", scene, json.dumps(scene).encode())
    record = e2e.judge("unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES)
    assert record["status"] == ("fail" if wrong else "pass"), record["problems"]
    assert seen_rules[0] is RULES
    if wrong:
        assert any("width_ft" in p and "public rules" in p for p in record["contract_problems"])


@pytest.mark.parametrize("sources", [["public", "private"], ["public"]])
def test_different_policy_keeps_validated_reported_width(sources):
    answer = replies()["surveyed wall"]
    answer["policy"].update(sources=sources, rules_sha256="f" * 64)
    loaded = replace(RULES, width_ft=4)
    assert reported_rules(answer, loaded).width_ft == answer["spot"]["width_ft"]


def test_malformed_cases_are_reported_and_next_case_runs(monkeypatch, tmp_path):
    scene = segmented_wall_scene(1)
    cases = []
    for name, metadata in (
        ("null-assumptions", {"rules_assumed": None}),
        ("null-checks", {"expect": {"checks": None}}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"id": name, "scene": scene, "expect": {}, **metadata}))
        cases.append(e2e.load_input(path))
    cases.append(e2e.SceneInput("surveyed wall", scene, json.dumps(scene).encode()))
    answers = replies()

    def post(url, endpoint, item):
        assert item.name.startswith("surveyed wall")
        answer = copy.deepcopy(answers[item.name])
        answer["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(answer).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    records = [
        e2e.judge("unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES)
        for item in cases
    ]
    assert [r["status"] for r in records] == ["bad input", "bad input", "pass"]
    assert "rules_assumed" in records[0]["problems"][0]
    assert "expect.checks" in records[1]["problems"][0]
    (tmp_path / "report").mkdir()
    e2e.write_report(tmp_path / "report", META, records)
    saved = json.loads((tmp_path / "report/report.json").read_text())
    assert len(saved["records"]) == 3
    assert not saved["all_passed"]


@pytest.mark.parametrize("name", ["spot", "nearest_considered"])
@pytest.mark.parametrize("field", ["width_ft", "depth_ft", "height_ft"])
def test_every_public_spot_dimension_is_compared(name, field):
    answer = {"policy": {"rules_sha256": RULES.rules_sha256}, name: {field: 1}}
    assert any(f"{name}.{field}" in p for p in public_dimension_problems(answer, RULES))


def test_public_corner_allowance_and_route_height_are_compared():
    answer = {
        "policy": {"rules_sha256": RULES.rules_sha256},
        "route": {"height_ft": 100},
        "checks": [
            {"id": "corner", "rule": {"key": "route.corner_allowance_ft"}, "threshold_ft": 100}
        ],
    }
    problems = public_dimension_problems(answer, RULES)
    assert any("route.height_ft" in p for p in problems)
    assert any("corner" in p for p in problems)


def test_private_marker_cannot_override_matching_fingerprint():
    answer = replies()["surveyed wall"]
    answer["policy"]["sources"].append("private")
    assert reported_rules(answer, RULES) is RULES


def test_missing_policy_cannot_override_loaded_dimensions():
    answer = replies()["surveyed wall"]
    answer.pop("policy")
    assert reported_rules(answer, RULES) is RULES


def test_transformed_public_response_uses_original_loaded_rules(monkeypatch):
    answers = replies()
    loaded = replace(RULES, width_ft=4)

    def post(url, endpoint, item):
        answer = copy.deepcopy(answers[item.name])
        answer["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        if not item.name.endswith("mirrored"):
            answer["policy"].update(sources=["public", "private"], rules_sha256="f" * 64)
        return 200, json.dumps(answer).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    scene = segmented_wall_scene(1)
    item = e2e.SceneInput("surveyed wall", scene, json.dumps(scene).encode())
    record = e2e.judge("unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, loaded)
    assert record["status"] == "fail"
    assert any("mirrored" in p and "public rules" in p for p in record["contract_problems"])
