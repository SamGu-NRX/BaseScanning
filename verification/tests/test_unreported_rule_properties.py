"""Unknown rule overrides do not hide nondeterminism in identical requests."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from test_judge_meter import response

from hsverify import e2e
from hsverify.resultcheck import RuleSet, WorkMeter, expected_checks, schema_errors

ROOT = Path(__file__).parents[2]
RULES = RuleSet.from_yaml((ROOT / "server/rules.yaml").read_text())
SCENE = {
    "schema_version": "1.0",
    "meter": {"pos": [0, 4, 0], "wall_id": "w1"},
    "walls": [{"id": "w1", "baseline": [[-30, 0], [30, 0]]}],
}
SCHEMAS = {
    kind: json.loads((ROOT / f"server/schemas/{kind}.schema.json").read_text())
    for kind in ("scene", "result")
}


def test_unreported_width_keeps_identical_input_comparison(monkeypatch):
    raw = json.dumps(SCENE).encode()
    answer = {
        "schema_version": "1.0",
        "decision": "manual_review",
        "summary": "Review required",
        "reasons": [{"code": "policy_not_approved", "message": "Review required"}],
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
            "input_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    assert schema_errors(answer, SCHEMAS["result"]) == []
    changed = copy.deepcopy(answer) | {"summary": "Different answer to identical input"}
    calls = []

    def post(url, endpoint, item):
        calls.append(item.raw)
        value = answer if len(calls) == 1 else changed
        value = copy.deepcopy(value)
        value["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(value).encode(), 1

    monkeypatch.setattr(e2e, "post", post)
    item = e2e.SceneInput(
        "unknown rules", SCENE, raw, rules_assumed={"battery_width": RULES.width_ft}
    )
    record = e2e.judge(
        "unused",
        e2e.Endpoint("/place", "application/json"),
        item,
        SCHEMAS,
        replace(RULES, width_ft=4),
    )
    assert record["rule_checks_unverified"]
    assert record["status"] == "fail"
    assert "sending the same scene twice gave different results" in record["contract_problems"]
    assert calls[:2] == [raw, raw]


def test_reported_width_uses_runtime_meter_without_predicting_unused_grid(monkeypatch):
    answer = response()
    answer["decision"] = "reject"
    answer["sweep"] = [
        {
            "wall_id": "w1",
            "start_ft": [1, 1],
            "outcome": "fail",
            "failing": ["route_length"],
            "unsure": [],
        }
    ]
    answer["stats"].update(candidates=1, fail=1)
    answer["checks"] = [
        {
            "id": key,
            "label": key,
            "outcome": "fail",
            "reason": "No fitting footprint",
            "measured_ft": None,
            "plus_minus_ft": None,
            "threshold_ft": None,
            "comparison": None,
            "subject": None,
            "rule": {"key": key, "source": "public fixture", "placeholder": False},
        }
        for key in sorted(
            expected_checks(SCENE, RULES) | {"meter_working_space", "route_path", "route_length"}
        )
    ]
    answer["checks"].append(
        {
            "id": "width",
            "label": "width",
            "outcome": "fail",
            "reason": "No fitting footprint",
            "measured_ft": None,
            "plus_minus_ft": None,
            "threshold_ft": 1e9,
            "comparison": None,
            "rule": {"key": "battery.width_ft", "source": "public fixture", "placeholder": False},
        }
    )
    assert schema_errors(answer, SCHEMAS["result"]) == []

    def post(url, endpoint, item):
        value = copy.deepcopy(answer)
        value["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(value).encode(), 1

    monkeypatch.setattr(e2e, "post", post)
    meter = WorkMeter(2000)
    item = e2e.SceneInput("large reported width", SCENE, json.dumps(SCENE).encode())
    record = e2e.judge(
        "unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES, meter=meter
    )
    assert record["status"] == "pass", record["problems"]
    assert 0 < record["verification_samples"] == meter.samples < 2000
