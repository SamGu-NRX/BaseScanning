"""Recorded public responses exercise the complete judge without a server dependency."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from segmented_wall import segmented_wall_scene

from hsverify import e2e
from hsverify.resultcheck import MAX_RESULT_CHECKS, RuleSet, WorkMeter, ingestion_problems

ROOT = Path(__file__).parents[2]
RULES = RuleSet.from_yaml((ROOT / "server/rules.yaml").read_text())
SCHEMAS = {
    kind: json.loads((ROOT / f"server/schemas/{kind}.schema.json").read_text())
    for kind in ("scene", "result")
}


def workload(monkeypatch, segments):
    responses = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-workload.json").read_text()
    )
    calls = []

    def post(url, endpoint, item):
        calls.append(item.name)
        answer = copy.deepcopy(responses[item.name])
        answer["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
        return 200, json.dumps(answer).encode(), 0

    monkeypatch.setattr(e2e, "post", post)
    scene = segmented_wall_scene(segments)
    item = e2e.SceneInput("surveyed wall", scene, json.dumps(scene).encode())
    meter = WorkMeter()
    record = e2e.judge(
        "unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES, meter=meter
    )
    assert record["status"] == "pass", record["problems"]
    assert calls.count("surveyed wall") == 2
    assert "surveyed wall~mirrored" in calls
    return meter.samples


@pytest.mark.parametrize("segments", [13, 20])
def test_full_judge_accepts_equivalent_segmented_walls(monkeypatch, segments):
    baseline = workload(monkeypatch, 1)
    actual = workload(monkeypatch, segments)
    assert actual < baseline * 1.2


def test_identical_resend_counts_are_checked_before_comparison(monkeypatch):
    responses = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-workload.json").read_text()
    )
    original = copy.deepcopy(responses["surveyed wall"])
    oversized = original | {"checks": [None] * (MAX_RESULT_CHECKS + 1)}
    replies = iter((original, oversized))
    scene = segmented_wall_scene(1)
    item = e2e.SceneInput("surveyed wall", scene, json.dumps(scene).encode())
    original["stats"]["input_sha256"] = hashlib.sha256(item.raw).hexdigest()
    monkeypatch.setattr(e2e, "post", lambda *args: (200, json.dumps(next(replies)).encode(), 0))
    # Other transforms are immaterial to the identical-resend admission control.
    monkeypatch.setattr(e2e, "mirror_scene", lambda value: value)
    monkeypatch.setattr(e2e, "answer", lambda *args: (None, "transform omitted by test"))
    record = e2e.judge("unused", e2e.Endpoint("/place", "application/json"), item, SCHEMAS, RULES)
    assert any(
        "identical resend: result ingestion limit exceeded: checks" in problem
        for problem in record["problems"]
    )
    assert ingestion_problems(oversized, "result")
