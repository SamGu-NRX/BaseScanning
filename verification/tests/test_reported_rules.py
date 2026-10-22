"""Reported geometry and policy identity determine whether rule assumptions are verified."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from segmented_wall import segmented_wall_scene
from test_judge_meter import META

from hsverify import e2e
from hsverify.resultcheck import RuleSet, public_assumption_warnings

ROOT = Path(__file__).parents[2]
RULES = RuleSet.from_yaml((ROOT / "server/rules.yaml").read_text())
SCHEMAS = {
    kind: json.loads((ROOT / f"server/schemas/{kind}.schema.json").read_text())
    for kind in ("scene", "result")
}


def test_public_fingerprint_matches_recorded_public_solver():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-pass.json").read_text()
    )
    assert RULES.rules_sha256 == fixture["result"]["policy"]["rules_sha256"]


@pytest.mark.parametrize(
    "assumed",
    [
        {"battery_width": RULES.width_ft},
        {"corner_allowance": RULES.corner_allowance_ft},
    ],
    ids=["battery_width", "corner_allowance"],
)
@pytest.mark.parametrize("identity", ["private source", "different fingerprint"])
def test_unreported_override_is_unverified_even_when_case_matches_public(assumed, identity):
    answer = {"policy": {"sources": ["public"], "rules_sha256": RULES.rules_sha256}}
    if identity == "private source":
        answer["policy"]["sources"].append("private")
    else:
        answer["policy"]["rules_sha256"] = "f" * 64
    warnings = public_assumption_warnings(assumed, answer, RULES)
    assert len(warnings) == 1
    assert "assumed public rules; server may override" in warnings[0]


@pytest.mark.parametrize("matches", [True, False])
def test_judge_compares_reported_width_with_case_assumption(monkeypatch, matches):
    answers = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-workload.json").read_text()
    )

    def post(url, endpoint, item):
        answer = copy.deepcopy(answers[item.name])
        answer["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        answer["policy"].update(sources=["public", "private"], rules_sha256="f" * 64)
        return 200, json.dumps(answer).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    scene = segmented_wall_scene(1)
    item = e2e.SceneInput(
        "surveyed wall",
        scene,
        json.dumps(scene).encode(),
        rules_assumed={"battery_width": RULES.width_ft if matches else 4},
    )
    record = e2e.judge(
        "unused",
        e2e.Endpoint("/place", "application/json"),
        item,
        SCHEMAS,
        replace(RULES, width_ft=4),
    )
    assert record["status"] == ("pass" if matches else "assumption mismatch")
    assert bool(record.get("assumption_mismatches")) is not matches


@pytest.mark.parametrize("identity", ["private source", "different fingerprint"])
def test_unreported_private_width_cannot_claim_contract(monkeypatch, tmp_path, identity):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-pass.json").read_text()
    )
    answer = copy.deepcopy(fixture["result"])
    answer.update(decision="reject", spot=None, route=None)
    answer["reasons"] = [{"code": "policy_not_approved", "message": "Private rules reject"}]
    answer["sweep"] = [
        {
            "wall_id": "w1",
            "start_ft": [1, 1],
            "outcome": "fail",
            "failing": ["route_length"],
            "unsure": [],
        }
    ]
    answer["stats"].update(candidates=1, fail=1, unsure=0, **{"pass": 0})
    for check in answer["checks"]:
        check.update(outcome="fail", measured_ft=None, plus_minus_ft=None)
    if identity == "private source":
        answer["policy"]["sources"].append("private")
    else:
        answer["policy"]["rules_sha256"] = "f" * 64

    def post(url, endpoint, item):
        value = copy.deepcopy(answer)
        value["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(value).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    scene = segmented_wall_scene(1)
    item = e2e.SceneInput(
        "private reject",
        scene,
        json.dumps(scene).encode(),
        rules_assumed={"battery_width": RULES.width_ft},
    )
    record = e2e.judge("unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES)
    assert record["status"] == "assumptions unverified", record["problems"]
    assert record["rule_checks_unverified"]
    assert not e2e.write_report(tmp_path, META, [record])["contract_ok"]
