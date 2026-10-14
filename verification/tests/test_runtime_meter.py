import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_resultcheck import RULES, SCENE, check, result

import hsverify.resultcheck as checks
from hsverify.resultcheck import (
    GroundPlan,
    Need,
    RuleSet,
    WorkLimitExceeded,
    WorkMeter,
    finite,
    geometry_admission_problems,
    invariant_problems,
    missing_evidence_problems,
    reach_gaps,
    schema_errors,
)


def test_oversized_integer_fails_admission_before_geometry():
    answer = result(
        spot=False, sweep=[{"wall_id": "w1", "start_ft": [0, 10**400], "outcome": "fail"}]
    )
    assert not finite(10**400)
    meter = WorkMeter(0)
    problems = invariant_problems(SCENE, answer, rules=RULES, meter=meter)
    assert any("start_ft must be finite and ordered" in problem for problem in problems)
    assert meter.samples == 0


def test_unknown_nearest_wall_is_rejected_before_geometry():
    answer = result()
    answer["nearest_considered"] = answer["spot"] | {"wall_id": "missing"}
    meter = WorkMeter(0)
    assert any(
        "nearest_considered names unknown wall_id 'missing'" in problem
        for problem in invariant_problems(SCENE, answer, rules=RULES, meter=meter)
    )
    assert meter.samples == 0


@pytest.mark.parametrize("has_unobserved_reason", [False, True])
def test_identical_unsure_checks_do_not_repeat_geometry(has_unobserved_reason):
    unsure = check(outcome="unsure", measured=None, cause="unobserved") | {"id": "pool_clearance"}
    answer = result(checks=[unsure], complete=False)
    answer["spot"]["outcome"] = "unsure"
    answer["reasons"] = [{"code": "unobserved_area"}] if has_unobserved_reason else []
    answer["missing_evidence"] = [{"kind": "band", "band": "ground", "checks": ["pool_clearance"]}]
    rules = replace(RULES, needs={"pool_clearance": (Need("ground", 10),)})
    meter = WorkMeter(100_000)
    once = missing_evidence_problems(SCENE, answer, rules, meter=meter)
    many = copy.deepcopy(answer)
    many["checks"] *= 10_000
    duplicate_meter = WorkMeter(100_000)
    assert missing_evidence_problems(SCENE, many, rules, meter=duplicate_meter) == once
    assert duplicate_meter.samples == meter.samples
    assert 0 < meter.samples < 100_000


def test_reach_cache_includes_geometry_and_parameters():
    meter = WorkMeter(10_000)
    first = reach_gaps(SCENE, RULES, "gas_clearance", "w1", 1, 3, meter=meter)
    count = meter.samples
    assert reach_gaps(SCENE, RULES, "gas_clearance", "w1", 1, 3, meter=meter) == first
    assert meter.samples == count
    reach_gaps(SCENE, RULES, "gas_clearance", "w1", 1, 4, meter=meter)
    assert meter.samples > count
    count = meter.samples
    changed = copy.deepcopy(SCENE)
    changed["coverage"]["observed"][1]["out_ft"] = 0.1
    reach_gaps(changed, RULES, "gas_clearance", "w1", 1, 3, meter=meter)
    assert meter.samples > count


def test_ground_grid_stops_at_shared_runtime_limit():
    meter = WorkMeter()
    plan = GroundPlan(SCENE, RULES, meter=meter)
    setup = meter.samples
    meter.limit = setup + 30
    with pytest.raises(WorkLimitExceeded) as caught:
        plan.covers(2, 1, 3, 0, lambda _: 2)
    assert caught.value.samples == meter.samples == setup + 31
    assert str(caught.value) == f"verification work limit exceeded ({setup + 31} samples)"


def test_valid_wide_direct_coverage_uses_only_visited_work():
    rules = RuleSet.from_yaml((Path(__file__).parents[2] / "server" / "rules.yaml").read_text())
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["baseline"] = [[-30, 0], [30, 0]]
    scene["objects"] = []
    scene["ground"] = [{"type": "concrete", "polygon": [[-30, 0], [30, 0], [30, 20], [-30, 20]]}]
    scene["coverage"]["observed"] = [
        {"band": band, "span_ft": [-30, 30], "out_ft": 20}
        for band in ("ground", "wall", "facing", "overhead")
    ]
    answer = result(decision="pass")
    answer["summary"] = "All public-rule checks pass with coverage beyond every required reach."
    lo, hi = 2, 2 + rules.width_ft
    answer["spot"].update(
        span_ft=[lo, hi],
        center=[(lo + hi) / 2, rules.depth_ft / 2],
        meter_offset_ft=[(lo + hi) / 2, rules.depth_ft / 2],
        footprint=[[lo, 0], [hi, 0], [hi, rules.depth_ft], [lo, rules.depth_ft]],
        route_length_ft=2,
    )
    answer["sweep"][0]["start_ft"] = [2, 12]
    answer["route"] = {
        "outcome": "pass",
        "length_ft": 2,
        "plus_minus_ft": 0,
        "height_ft": rules.route_height_ft,
        "polyline": [[0, 0], [2, 0]],
        "detours": [],
        "crossings": [],
    }
    answer["ends"] = {
        "left": {"kind": "limit", "s_ft": -30, "point": [-30, 0]},
        "right": {"kind": "limit", "s_ft": 30, "point": [30, 0]},
    }
    for kind, value in (("scene", scene), ("result", answer)):
        schema = json.loads(
            (Path(__file__).parents[2] / "server" / "schemas" / f"{kind}.schema.json").read_text()
        )
        assert schema_errors(value, schema) == []
    assert len(json.dumps(answer).encode()) == 4420
    meter = WorkMeter(100_000)
    assert geometry_admission_problems(scene, answer, rules) == []
    assert invariant_problems(scene, answer, rules=rules, meter=meter) == []
    assert 0 < meter.samples < 100_000


def test_public_solver_80_foot_wall_uses_direct_coverage(monkeypatch):
    # Recorded from public server/rules.yaml with solve(parse_scene(...)) on 2026-10-14.
    # Elapsed time is normalized to zero. Keep the response here so verification
    # needs no solver or Shapely dependency.
    fixture = json.loads((Path(__file__).parent / "fixtures/wide-public-pass.json").read_text())
    scene, answer = fixture["scene"], fixture["result"]
    rules = RuleSet.from_yaml((Path(__file__).parents[2] / "server/rules.yaml").read_text())
    for kind, value in (("scene", scene), ("result", answer)):
        schema = json.loads(
            (Path(__file__).parents[2] / "server/schemas" / f"{kind}.schema.json").read_text()
        )
        assert schema_errors(value, schema) == []
    assert answer["decision"] == "pass"
    assert answer["stats"]["candidates"] == 683

    def unexpected_grid(*args, **kwargs):
        pytest.fail("complete direct coverage must not sample the ground grid")

    monkeypatch.setattr(GroundPlan, "_samples", unexpected_grid)
    meter = WorkMeter()
    assert invariant_problems(scene, answer, rules=rules, meter=meter) == []
    # Actual footprint, interval and metadata visits are below 1% of the shared limit.
    assert 0 < meter.samples < checks.MAX_WORK_SAMPLES // 100


def test_invariant_geometry_exception_names_its_check(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("damaged geometry")

    monkeypatch.setattr("hsverify.resultcheck.coverage_problems", broken)
    problems = invariant_problems(SCENE, result(), rules=RULES)
    assert "coverage: RuntimeError: damaged geometry" in problems


def test_each_candidate_wall_piece_charges_the_shared_meter(monkeypatch):
    monkeypatch.setattr(checks, "geometry_admission_problems", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        checks,
        "straight_pieces",
        lambda *args, **kwargs: [(i * 10, i * 10 + 5) for i in range(1000)],
    )
    answer = result(
        spot=False, sweep=[{"wall_id": "w1", "start_ft": [9991, 9991], "outcome": "unsure"}]
    )
    meter = WorkMeter(100)
    with pytest.raises(WorkLimitExceeded):
        checks.footprint_problems(SCENE, answer, RULES, meter=meter)
    assert meter.samples == 101


def test_finite_endpoints_with_overflowing_length_stop_admission():
    answer = result(
        spot=False, sweep=[{"wall_id": "w1", "start_ft": [-1e308, 1e308], "outcome": "fail"}]
    )
    meter = WorkMeter(0)
    problems = invariant_problems(SCENE, answer, rules=RULES, meter=meter)
    assert any("span length must be finite" in p for p in problems)
    assert meter.samples == 0


def test_equal_scene_copies_share_geometry_cache_keys():
    meter = WorkMeter()
    assert meter.input_key(SCENE) == meter.input_key(copy.deepcopy(SCENE))
    assert isinstance(meter.input_key(SCENE), int)
    before = meter.samples
    for _ in range(10_000):
        meter.input_key(SCENE)
    assert meter.samples == before


def test_ground_samples_do_not_overflow_finite_span():
    points = GroundPlan._samples(0, 1e306)
    assert all(finite(next(points)) for _ in range(1000))
