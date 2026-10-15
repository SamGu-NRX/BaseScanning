import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from segmented_wall import segmented_wall_scene
from test_resultcheck import RULES, SCENE

from hsverify.resultcheck import (
    RuleSet,
    WorkMeter,
    ground_plan,
    invariant_problems,
    schema_errors,
    segments_s,
    slide,
    wall_spans_s,
)


@pytest.mark.parametrize("segments_per_wall", [13, 20])
def test_public_pass_is_independent_of_redundant_baseline_vertices(segments_per_wall):
    # Public solve(parse_scene(...), rules_from_dict(public YAML)) produced this
    # 815-candidate, 26-run response. The geometry is unchanged by extra vertices.
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/segmented-public-pass.json").read_text()
    )
    rules = RuleSet.from_yaml((Path(__file__).parents[2] / "server/rules.yaml").read_text())
    answer = fixture["result"]
    baseline_meter = WorkMeter()
    assert invariant_problems(fixture["scene"], answer, rules=rules, meter=baseline_meter) == []
    scene = segmented_wall_scene(segments_per_wall)
    for kind, value in (("scene", scene), ("result", answer)):
        schema = json.loads(
            (Path(__file__).parents[2] / f"server/schemas/{kind}.schema.json").read_text()
        )
        assert schema_errors(value, schema) == []
    meter = WorkMeter()
    assert invariant_problems(scene, answer, rules=rules, meter=meter) == []
    # Metadata grows with the input, but actual geometry work must not multiply
    # with redundant baseline vertices. Leave room for the bounded input scan.
    assert meter.samples < baseline_meter.samples * 1.2
    assert all(key[0] != "ground_strips" for key in meter.cache)


def test_scene_derivatives_are_built_once_per_judgment():
    scene = segmented_wall_scene(20)
    meter = WorkMeter()
    segments = segments_s(scene, RULES, meter=meter)
    spans = wall_spans_s(scene, RULES, meter=meter)
    plan = ground_plan(scene, RULES, meter)
    once = meter.samples
    assert segments_s(scene, RULES, meter=meter) is segments
    assert wall_spans_s(scene, RULES, meter=meter) is spans
    assert ground_plan(scene, RULES, meter) is plan
    assert meter.samples == once
    assert len(segments) == 20
    assert len({segment[0] for segment in segments}) == 20
    assert all(key[0] != "ground_strips" for key in meter.cache)


def test_collinear_merge_keeps_actual_turns_and_wall_error_sources():
    scene = copy.deepcopy(SCENE)
    scene["walls"] = [
        {"id": "w1", "baseline": [[0, 0], [2, 0], [4, 0], [4, 4]], "plus_minus_ft": 0.1},
        {"id": "w2", "baseline": [[4, 4], [4, 6], [4, 8]], "plus_minus_ft": 0.2},
    ]
    scene["meter"].update(pos=[0, 4, 0], wall_id="w1", plus_minus_ft=0)
    rules = replace(RULES, wall_join_ft=0.1)
    meter = WorkMeter()
    segments = segments_s(scene, rules, meter=meter)
    assert [(wid, a, b) for wid, a, b, _, _ in segments] == [
        ("w1", 0, 4),
        ("w1", 4, 8),
        ("w2", 8, 12),
    ]
    assert slide(scene, rules, 9, 10, meter=meter) == pytest.approx(0.1 * 2**0.5)
    once = meter.samples
    assert slide(scene, rules, 9, 10, meter=meter) == pytest.approx(0.1 * 2**0.5)
    assert meter.samples == once
