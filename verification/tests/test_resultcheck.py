import copy
import hashlib
import json
from dataclasses import replace

import pytest

from hsverify.resultcheck import (
    GroundPlan,
    Need,
    RuleSet,
    assumption_mismatches,
    battery_error,
    chain_ends_s,
    comparable,
    coverage_problems,
    declared_height_problems,
    expectation_problems,
    expected_checks,
    footprint_problems,
    gaps_over,
    invariant_problems,
    less_coverage_problems,
    margin_problem,
    mirror_scene,
    missing_evidence_problems,
    more_error_problems,
    observed,
    outcome_at,
    outcome_lengths,
    reach_gaps,
    run_starts,
    straight_pieces,
    tape_to_tap,
    with_ground_short_of,
    with_less_coverage,
    with_more_error,
    with_requests_captured,
)

# Small radii keep the geometry checkable by hand: a battery 31 x 22 in, gas and openings 1 ft.
RULES = RuleSet(
    width_ft=31 / 12,
    depth_ft=22 / 12,
    needs={
        "ground_surface": (Need("ground", 0.0),),
        "gas_clearance": (Need("ground", 1.0),),
        "opening_clearance": (Need("wall", 1.0, height=6.5),),
    },
    errors={
        "tap": 0.3,
        "vlm": 1.5,
        "tape": 0.05,
        "wall": 0.3,
        "mesh": 0.5,
        "plane": 0.75,
        "meter": 0.3,
        "drift_per_ft": 0.16,
    },
    step_ft=1 / 6,  # the server's 2 in
    wall_join_ft=0.6,
    battery_height_ft=3.25,
    headroom_ft=6.5,
)

SCENE = {
    "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0},
    # An explicit zero error keeps the reach arithmetic in these tests to the radii alone.
    "walls": [{"id": "w1", "baseline": [[-12.0, 0.0], [12.0, 0.0]], "plus_minus_ft": 0.0}],
    "objects": [
        {
            "type": "gas_meter",
            "wall_id": "w1",
            "span_ft": [-6.0, -5.0],
            "source": "tap",
            "footprint": [[-6.0, 0.5], [-5.0, 1.0]],
        },
    ],
    "coverage": {
        "ends": {"left": {"kind": "limit"}, "right": {"kind": "unexplored"}},
        "observed": [
            {"band": "wall", "span_ft": [-12.0, 10.0]},
            {"band": "ground", "span_ft": [-12.0, 10.0], "out_ft": 3.0},
            {"band": "ground", "span_ft": [10.0, 12.0], "out_ft": 1.0},
        ],
    },
}


def check(outcome="pass", measured=5.0, error=0.3, threshold=3.0, cmp="at_least", cause=None):
    c = {
        "id": "gas_clearance",
        "label": "Gas",
        "outcome": outcome,
        "reason": "",
        "measured_ft": measured,
        "plus_minus_ft": error,
        "threshold_ft": threshold,
        "comparison": cmp,
        "rule": {"key": "gas_clearance_ft", "source": "", "placeholder": False},
    }
    if cause:
        c["unsure_cause"] = cause
    return c


# Every check a 3baa338 result lists (battery_clearance only when the scene or rules call for it).
SERVER_CHECKS = (
    "wall_backing",
    "ground_surface",
    "meter_working_space",
    "gas_clearance",
    "ac_clearance",
    "drive_clearance",
    "pool_clearance",
    "opening_clearance",
    "wall_equipment_above",
    "facing_gap",
    "headroom",
    "route_path",
    "route_length",
)


def result(decision="manual_review", spot=True, checks=None, sweep=None, sent=b"{}", complete=True):
    """A result listing `checks` (default: gas passing); with `complete`, every other check a
    server lists is added as a pass with no numbers, so a test is about the checks it names."""
    checks = [check()] if checks is None else list(checks)
    if complete:
        named = {c["id"] for c in checks}
        checks += [passing(i) for i in SERVER_CHECKS if i not in named]
    s = None
    if spot:
        s = {
            "outcome": "pass",
            "wall_id": "w1",
            "segment": 0,
            "span_ft": [1.0, 1.0 + 31 / 12],
            "width_ft": 31 / 12,
            "depth_ft": 22 / 12,
            "height_ft": 3.3,
            "footprint": [[1, 0], [3.58, 0], [3.58, 1.83], [1, 1.83]],
            "center": [2.29, 0.92],
            "along": [1, 0],
            "outward": [0, 1],
            "meter_offset_ft": [2.29, 0.92],
            "route_length_ft": 1.0,
        }
    return {
        "schema_version": "1.0",
        "decision": decision,
        "summary": "",
        "reasons": [] if decision == "pass" else [{"code": "policy_not_approved", "message": ""}],
        "policy": {
            "id": None,
            "version": None,
            "auto_approve": decision != "manual_review",
            "sources": ["public"],
            "rules_sha256": "0" * 64,
        },
        "spot": s,
        "route": None if s is None else {"outcome": "pass"},
        "checks": checks,
        "missing_evidence": [],
        "ends": {},
        "sweep": sweep
        if sweep is not None
        else [
            {
                "wall_id": "w1",
                "start_ft": [1.0, 1.0],
                "outcome": "pass",
                "failing": [],
                "unsure": [],
            },
        ],
        "stats": {
            "candidates": 1,
            "pass": 1,
            "unsure": 0,
            "fail": 0,
            "elapsed_ms": 3.0,
            "input_sha256": hashlib.sha256(sent).hexdigest(),
        },
    }


def test_consistent_result_has_no_problems():
    assert invariant_problems(SCENE, result(), sent=b"{}", rules=RULES) == []


@pytest.mark.parametrize(
    ("cmp", "measured", "error", "threshold", "expected"),
    [
        ("at_least", 3.31, 0.3, 3.0, "pass"),
        ("at_least", 3.3, 0.3, 3.0, "unsure"),  # margin equals the error
        ("at_least", 2.7, 0.3, 3.0, "unsure"),
        ("at_least", 2.69, 0.3, 3.0, "fail"),
        ("at_most", 19.69, 0.3, 20.0, "pass"),
        ("at_most", 19.7, 0.3, 20.0, "unsure"),
        ("at_most", 20.31, 0.3, 20.0, "fail"),
    ],
)
def test_margin_rule(cmp, measured, error, threshold, expected):
    for outcome in ("pass", "fail", "unsure"):
        problem = margin_problem(check(outcome, measured, error, threshold, cmp))
        assert (problem is None) == (outcome == expected), (outcome, problem)


def test_unsure_for_another_cause_may_sit_on_passing_numbers_but_not_failing_ones():
    assert margin_problem(check("unsure", 5.9, 0.6, 3.0, cause="unobserved")) is None
    assert margin_problem(check("unsure", 3.1, 0.6, 3.0, cause="unknown_attribute")) is None
    problem = margin_problem(check("unsure", 1.0, 0.6, 3.0, cause="unobserved"))
    assert problem is not None and "should be fail" in problem
    # A margin-caused unsure must really be inside the band.
    assert margin_problem(check("unsure", 5.9, 0.6, 3.0, cause="margin")) is not None


def route(outcome, measured, error=0.0, cause=None):
    c = check(outcome, measured, error, 20.0, "at_most", cause)
    c["review_threshold_ft"] = 15.0
    return c


def test_review_band_blocks_a_pass_and_is_not_a_margin():
    assert margin_problem(route("pass", 14.0)) is None
    assert "should be unsure" in margin_problem(route("pass", 16.0))
    assert margin_problem(route("unsure", 16.0, cause="rule_requires_review")) is None
    assert "not within its error of any line" in margin_problem(
        route("unsure", 16.0, cause="margin")
    )
    # Within error of either line, margin is the right cause.
    assert margin_problem(route("unsure", 15.2, 0.3, cause="margin")) is None
    assert margin_problem(route("unsure", 19.9, 0.3, cause="margin")) is None
    assert margin_problem(route("unsure", 15.0, cause="margin")) is None  # on the line
    assert margin_problem(route("fail", 20.4, 0.3)) is None


def test_margin_rule_skips_checks_without_numbers():
    assert margin_problem(check("unsure", measured=None, cause="unobserved")) is None


def test_decision_consistency():
    bad_pass = result("pass", checks=[check("unsure", 3.2, cause="margin")])
    msgs = invariant_problems(SCENE, bad_pass)
    assert any("checks not passing" in m for m in msgs)
    reject_with_spot = result("reject")
    assert any("reject but a spot" in m for m in invariant_problems(SCENE, reject_with_spot))
    auto_off = result("pass")
    auto_off["policy"]["auto_approve"] = False
    assert any("auto_approve" in m for m in invariant_problems(SCENE, auto_off))


def test_unsure_cause_bookkeeping():
    msgs = invariant_problems(SCENE, result(checks=[check("unsure", 3.2)]))
    assert any("unsure without unsure_cause" in m for m in msgs)
    msgs = invariant_problems(SCENE, result(checks=[check("pass", cause="margin")]))
    assert any("unsure_cause on a pass check" in m for m in msgs)


def test_counts_hash_and_offset():
    r = result()
    r["stats"]["fail"] = 2
    r["spot"]["meter_offset_ft"] = [2.0, 0.92]
    msgs = invariant_problems(SCENE, r, sent=b"other bytes")
    assert any(m.startswith("stats: pass") for m in msgs)
    assert any("input_sha256" in m for m in msgs)
    assert any("meter_offset_ft" in m for m in msgs)


def test_observed_merges_and_filters_ground_depth():
    assert observed(SCENE, "wall") == [(-12.0, 10.0)]
    assert observed(SCENE, "ground", min_out_ft=2.0) == [(-12.0, 10.0)]
    assert observed(SCENE, "ground", min_out_ft=0.5) == [(-12.0, 12.0)]
    assert observed({"meter": {}}, "wall") == []


def with_ground(out_ft: float, span: list[float]) -> dict:
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"][1:] = [{"band": "ground", "span_ft": span, "out_ft": out_ft}]
    return scene


def test_a_pass_needs_ground_out_to_depth_plus_each_radius():
    # Start 1 covers [1, 3.58]. Gas (radius 1) needs ground out to 1.83 + 1 = 2.83 ft, in front
    # and just past its ends; the first stretch short of it is [0, 1].
    msgs = invariant_problems(with_ground(2.5, [-12.0, 10.0]), result(), rules=RULES)
    needs = "gas_clearance needs ground [0.00, 1.00] observed out to 2.83 ft, seen 2.50 ft"
    assert any(needs in m for m in msgs)
    assert invariant_problems(with_ground(2.84, [-12.0, 10.0]), result(), rules=RULES) == []


def test_past_the_battery_the_ground_needed_shrinks_with_distance():
    # d past the battery's left end at 1, gas (radius 1) needs ground out to D + sqrt(1 - d^2):
    # 1.83 + 0.87 = 2.70 for [0, 0.5], and D + 1 = 2.83 for [0.5, 1].
    scene = with_ground(2.84, [1.0, 10.0])
    scene["coverage"]["observed"] += [
        {"band": "ground", "span_ft": [0.0, 0.5], "out_ft": 2.71},
        {"band": "ground", "span_ft": [0.5, 1.0], "out_ft": 2.84},
    ]
    assert invariant_problems(scene, result(), rules=RULES) == []
    scene["coverage"]["observed"][-1]["out_ft"] = 2.8
    msgs = invariant_problems(scene, result(), rules=RULES)
    assert any("ground [0.50, 1.00] observed out to 2.83 ft, seen 2.80 ft" in m for m in msgs)


def test_a_pass_needs_ground_along_the_wall_to_each_radius_without_slack():
    # Ground from 0.1: the 1 ft gas radius around [1, 3.58] reaches back to 0.
    msgs = invariant_problems(with_ground(3.0, [0.1, 10.0]), result(), rules=RULES)
    assert any("gas_clearance needs ground [0.00, 0.10] observed, none seen" in m for m in msgs)


def test_a_passing_opening_check_needs_the_wall_to_its_radius():
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"][0]["span_ft"] = [0.0, 4.0]  # radius needs [0, 4.58]
    r = result(
        checks=[
            check(),
            {**check(), "id": "opening_clearance", "measured_ft": None, "plus_minus_ft": None},
        ]
    )
    msgs = invariant_problems(scene, r, rules=RULES)
    needs = "check opening_clearance passes but needs wall [0.00, 4.58] observed"
    assert any(needs in m for m in msgs)


def test_pass_whose_route_crosses_unobserved_wall_is_flagged():
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"][0]["span_ft"] = [0.5, 10.0]  # the wall at s = [0, 0.5] unseen
    msgs = invariant_problems(scene, result(), rules=RULES)
    assert any("wall and cable route" in m for m in msgs)


def test_no_coverage_never_passes():
    scene = {k: v for k, v in SCENE.items() if k != "coverage"}
    msgs = invariant_problems(scene, result("pass"), rules=RULES)
    assert any("no coverage at all" in m for m in msgs)


def test_photo_request_for_an_observed_area_is_flagged():
    # The wall was seen with no out_ft, so to headroom height: a request up to it is redundant.
    r = result()
    r["missing_evidence"] = [
        {"kind": "band", "band": "wall", "span_ft": [-3.0, -1.0], "out_ft": 6.5, "message": ""}
    ]
    assert any("lists as observed" in m for m in invariant_problems(SCENE, r, rules=RULES))


def test_every_unobserved_check_needs_a_request_naming_it():
    unseen = check("unsure", None, cause="unobserved")
    r = result(checks=[unseen])
    r["reasons"] = [{"code": "unobserved_area", "message": ""}]
    assert missing_evidence_problems(SCENE, r) == [
        "reason unobserved_area but missing_evidence is empty"
    ]
    r["missing_evidence"] = [
        {"kind": "band", "band": "wall", "span_ft": [11, 12], "checks": ["x"], "message": ""}
    ]
    assert missing_evidence_problems(SCENE, r) == [
        "check gas_clearance is unsure (unobserved) but no missing_evidence entry names it"
    ]
    r["missing_evidence"] = [
        {
            "kind": "band",
            "band": "ground",
            "span_ft": [11, 12],
            "checks": ["gas_clearance"],
            "message": "",
        }
    ]
    assert missing_evidence_problems(SCENE, r) == []


def test_the_reach_widens_by_the_battery_position_error():
    # Default wall error at the far edge 3.58: 0.3 + 0.16 x 3.58 = 0.87; gas reach 1.87 ft, so
    # ground out to 1.83 + 1.87 = 3.71 ft in front.
    scene = with_ground(3.6, [-12.0, 10.0])
    del scene["walls"][0]["plus_minus_ft"]
    msgs = invariant_problems(scene, result(), rules=RULES)
    assert any("gas_clearance needs ground" in m and "out to 3.71 ft" in m for m in msgs)
    scene["coverage"]["observed"][1]["out_ft"] = 3.72
    assert invariant_problems(scene, result(), rules=RULES) == []


def test_a_clearance_is_named_under_each_band_it_lacks():
    two_band = replace(
        RULES,
        needs=RULES.needs | {"gas_clearance": (Need("ground", 1.0), Need("wall", 1.0, height=6.5))},
    )
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"] = []  # nothing seen: both bands missing around the spot
    r = result(checks=[check("unsure", None, cause="unobserved")])
    r["reasons"] = [{"code": "unobserved_area", "message": ""}]
    ground_only = {"kind": "band", "band": "ground", "span_ft": [0, 5], "checks": ["gas_clearance"]}
    r["missing_evidence"] = [ground_only | {"message": ""}]
    assert missing_evidence_problems(scene, r, two_band) == [
        "check gas_clearance is unsure and needs wall [0.00, 4.58] observed higher than 6.5 ft, "
        "but no wall request names it"
    ]
    r["missing_evidence"].append(ground_only | {"band": "wall", "message": ""})
    assert missing_evidence_problems(scene, r, two_band) == []


def test_rules_come_from_rules_yaml_and_a_missing_one_is_loud():
    text = """
battery: {width_ft: {value: 2.5}, depth_ft: {value: 1.5}, height_ft: {value: 3.25}}
errors: {tap_ft: {value: 0.3}, vlm_ft: {value: 1.5}, tape_ft: {value: 0.05},
         wall_ft: {value: 0.3}, mesh_ft: {value: 0.5}, plane_ft: {value: 0.75},
         meter_ft: {value: 0.3}, drift_per_ft: {value: 0.16}}
clearances: {gas_ft: {value: 3}, ac_ft: {value: 3}, battery_ft: {value: 3}, drive_ft: {value: 5},
             pool_ft: {value: 10}, opening_ft: {value: 3}, wall_equipment_ft: {value: 0}}
openings: {exempt_bottom_above_ft: null}
facing: {min_ft: {value: 3}}
headroom: {min_ft: {value: 6.5}}
route: {height_ft: {value: 1}, corner_allowance_ft: {value: 0.5}}
sweep: {step_ft: {value: 0.166666666667}, wall_join_ft: {value: 0.6}}
"""
    rules = RuleSet.from_yaml(text)
    assert rules.needs["pool_clearance"] == (Need("ground", 10.0),)
    assert rules.needs["gas_clearance"] == (Need("ground", 3.0), Need("wall", 3.0, 6.5))
    assert rules.needs["battery_clearance"] == (Need("ground", 3.0), Need("wall", 3.0, 6.5))
    assert rules.needs["opening_clearance"] == (Need("wall", 3.0, 6.5),)
    assert rules.needs["wall_backing"] == (Need("wall", 0.0, 3.25),)
    assert rules.needs["facing_gap"] == (Need("facing", 0.0, 4.5, along=True),)
    assert rules.needs["headroom"] == (Need("overhead", 0.0, 6.5, along=True),)
    assert (rules.wall_join_ft, rules.battery_height_ft, rules.headroom_ft) == (0.6, 3.25, 6.5)
    assert (rules.width_ft, rules.errors["plane"], rules.route_height_ft) == (2.5, 0.75, 1.0)
    lower = RuleSet.from_yaml(
        text.replace("exempt_bottom_above_ft: null", "exempt_bottom_above_ft: 2")
    )
    assert lower.needs["opening_clearance"] == (Need("wall", 3.0, 2.0),)
    with pytest.raises(ValueError, match=r"rules\.yaml has no clearances\.pool_ft"):
        RuleSet.from_yaml(text.replace("pool_ft: {value: 10}, ", ""))


def test_expectations():
    r = result(
        complete=False,  # no route check, which `bad` below expects
        sweep=[
            {
                "wall_id": "w1",
                "start_ft": [-9.0, -4.0],
                "outcome": "fail",
                "failing": ["gas_clearance"],
                "unsure": [],
            },
            {
                "wall_id": "w1",
                "start_ft": [1.0, 1.0],
                "outcome": "pass",
                "failing": [],
                "unsure": [],
            },
        ],
    )
    ok = {
        "decision_not": ["pass"],
        "spot": {"wall_id": "w1", "span_within": [0.0, 4.0]},
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-8, -5], "outcome": "fail", "failing_match": "gas"}
        ],
        "checks": [{"match": "gas", "outcome": "pass"}],
        "missing_evidence_empty": True,
    }
    assert expectation_problems(ok, r) == []
    bad = {
        "decision_in": ["reject"],
        "spot": None,
        "sweep_runs": [{"wall_id": "w1", "start_ft": [0, 2], "outcome": "fail"}],
        "checks": [{"match": "route", "outcome": "pass"}],
    }
    msgs = expectation_problems(bad, r)
    assert len(msgs) == 4, msgs


def test_outcome_not_rules_out_one_outcome_and_tolerates_no_runs():
    r = result(
        sweep=[
            {
                "wall_id": "w1",
                "start_ft": [0.0, 2.0],
                "outcome": "unsure",
                "failing": [],
                "unsure": [],
            },
            {
                "wall_id": "w1",
                "start_ft": [2.1, 3.0],
                "outcome": "pass",
                "failing": [],
                "unsure": [],
            },
        ]
    )
    ok = {
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [0.5, 1.5], "outcome_not": "pass"},
            {"wall_id": "w9", "start_ft": [0, 9], "outcome_not": "pass"},
        ]
    }
    assert expectation_problems(ok, r) == []
    bad = {
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [0.5, 2.5], "outcome_not": "pass", "reason": "window"}
        ]
    }
    assert expectation_problems(bad, r) == [
        "sweep w1 starts [2.1, 3.0] is pass, which this case rules out (window)"
    ]


def test_pinned_check_numbers():
    r = result()
    assert expectation_problems({"checks": [{"match": "gas", "measured_ft": 5.0000004}]}, r) == []
    msgs = expectation_problems({"checks": [{"match": "gas", "measured_ft": 4.5}]}, r)
    assert msgs == ["check gas_clearance: measured_ft 5.0, expected 4.5"]


def test_assumption_mismatch():
    assert assumption_mismatches({"gas": 3.0}, result()) == []
    assert assumption_mismatches({"gas": 4.0}, result())


def test_mirror_negates_s_and_keeps_baselines_left_to_right():
    m = mirror_scene(SCENE)
    assert m["walls"][0]["baseline"] == [[-12.0, 0.0], [12.0, 0.0]]
    assert m["objects"][0]["span_ft"] == [5.0, 6.0]
    assert m["objects"][0]["footprint"] == [[6.0, 0.5], [5.0, 1.0]]
    assert m["coverage"]["observed"][0]["span_ft"] == [-10.0, 12.0]
    assert m["coverage"]["ends"] == {"left": {"kind": "unexplored"}, "right": {"kind": "limit"}}
    assert mirror_scene(m) == json.loads(json.dumps(SCENE))  # an involution
    assert SCENE["objects"][0]["span_ft"] == [-6.0, -5.0]  # input untouched


def test_mirror_reverses_a_wall_chain():
    scene = {
        "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "w1"},
        "walls": [
            {"id": "w1", "baseline": [[-4.0, 0.0], [4.0, 0.0]]},
            {"id": "w2", "baseline": [[4.0, 0.0], [4.0, 12.0]]},
        ],
    }
    m = mirror_scene(scene)
    assert [w["id"] for w in m["walls"]] == ["w2", "w1"]
    assert m["walls"][0]["baseline"] == [[-4.0, 12.0], [-4.0, 0.0]]
    assert m["walls"][1]["baseline"] == [[-4.0, 0.0], [4.0, 0.0]]
    # Chain stays connected: w2 ends where w1 starts.
    assert m["walls"][0]["baseline"][-1] == m["walls"][1]["baseline"][0]


def test_outcome_lengths_and_comparable():
    r = result(
        sweep=[
            {
                "wall_id": "w1",
                "start_ft": [0.0, 2.0],
                "outcome": "pass",
                "failing": [],
                "unsure": [],
            },
            {
                "wall_id": "w1",
                "start_ft": [2.0, 3.0],
                "outcome": "fail",
                "failing": [],
                "unsure": [],
            },
        ]
    )
    assert outcome_lengths(r) == {"pass": 2.0, "unsure": 0.0, "fail": 1.0}
    other = copy.deepcopy(r)
    other["stats"]["elapsed_ms"] = 99.0
    assert comparable(r) == comparable(other)


def run(a, b, outcome, wall="w1"):
    return {"wall_id": wall, "start_ft": [a, b], "outcome": outcome, "failing": [], "unsure": []}


def swept(*runs, decision="manual_review"):
    r = result(decision=decision, sweep=list(runs))
    return r


def test_start_outcomes_expectation():
    r = swept(run(0.0, 2.0, "fail"), run(2.1667, 5.0, "unsure"))
    assert outcome_at(r, "w1", 1.0) == "fail" and outcome_at(r, "w1", 2.1) is None
    ok = {"start_outcomes": [{"wall_id": "w1", "start_ft": 3.0, "outcome": "unsure"}]}
    assert expectation_problems(ok, r) == []
    bad = {
        "start_outcomes": [
            {"wall_id": "w1", "start_ft": 1.0, "outcome": "unsure", "why": "inside the error band"}
        ]
    }
    assert expectation_problems(bad, r) == [
        "start w1 1.0 is fail, expected unsure (inside the error band)"
    ]


@pytest.mark.parametrize(
    ("before", "after", "allowed"),
    [
        ("pass", "unsure", True),
        ("fail", "unsure", True),
        ("unsure", "unsure", True),
        ("unsure", "fail", False),
        ("unsure", "pass", False),
        ("fail", "pass", False),
        ("pass", "fail", False),
    ],
)
def test_more_error_only_moves_toward_unsure(before, after, allowed):
    msgs = more_error_problems(swept(run(0, 4, before)), swept(run(0, 4, after)), "more error")
    assert (msgs == []) == allowed, msgs


def test_less_coverage_never_creates_a_pass():
    assert less_coverage_problems(swept(run(0, 4, "pass")), swept(run(0, 4, "unsure")), "x") == []
    msgs = less_coverage_problems(swept(run(0, 4, "unsure")), swept(run(0, 4, "pass")), "x")
    assert msgs and all("went from unsure to pass" in m for m in msgs)


def split():
    """An original sweep: fail up to 2.0, then pass from 2.1, nothing evaluated between."""
    return swept(run(0.0, 2.0, "fail"), run(2.1, 5.0, "pass"))


def test_less_coverage_may_move_a_boundary_into_an_unswept_gap():
    """A coverage edge that moved adds evaluated starts; one inside the original's gap between
    a fail run and a pass run may pass, because the original allows either outcome there."""
    after = swept(run(0.0, 2.04, "fail"), run(2.05, 5.0, "pass"))
    assert less_coverage_problems(split(), after, "x") == []


def test_less_coverage_cannot_pass_a_gap_between_runs_that_did_not():
    before = swept(run(0.0, 2.0, "fail"), run(2.1, 5.0, "unsure"))
    after = swept(run(0.0, 2.0, "fail"), run(2.05, 2.05, "pass"), run(2.1, 5.0, "unsure"))
    assert less_coverage_problems(before, after, "x") == [
        "x: start w1 2.050 went from unswept beside fail and unsure to pass"
    ]


def test_less_coverage_cannot_pass_where_the_original_swept_nothing():
    """The decision guard does not catch this when the original already passed elsewhere."""
    before = swept(run(0.0, 4.0, "pass"), decision="pass")
    after = swept(run(0.0, 4.0, "pass"), run(10.0, 12.0, "pass", wall="w2"), decision="pass")
    assert less_coverage_problems(before, after, "x") == [
        f"x: start w2 {s:.3f} went from no run on its wall to pass" for s in (10.0, 11.0, 12.0)
    ]
    past_end = swept(run(0.0, 4.0, "pass"), run(4.1, 6.0, "fail"), decision="pass")
    longer = swept(run(0.0, 4.0, "pass"), run(4.1, 6.0, "fail"), run(6.1, 6.1, "pass"))
    assert less_coverage_problems(past_end, longer, "x") == [
        "x: start w1 6.100 went from past the original sweep to pass"
    ]


def test_a_new_pass_past_the_original_sweep_is_not_read_from_its_last_run():
    """The original ends on a pass run; nothing it evaluated brackets 4.1 to 6.0."""
    before = swept(run(0.0, 4.0, "pass"), decision="pass")
    after = swept(run(0.0, 4.0, "pass"), run(4.1, 6.0, "pass"), decision="pass")
    expected = [
        f"x: start w1 {s:.3f} went from past the original sweep to pass" for s in (4.1, 5.05, 6.0)
    ]
    assert less_coverage_problems(before, after, "x") == expected
    assert more_error_problems(before, after, "x") == expected
    # More error widens the reach limit, so a longer sweep may fail or turn unsure out there.
    wider = swept(run(0.0, 4.0, "unsure"), run(4.1, 5.0, "fail"), run(5.1, 6.0, "unsure"))
    assert more_error_problems(before, wider, "x") == []


def test_more_error_reads_an_unswept_start_from_its_neighbours():
    moved = swept(run(0.0, 2.04, "fail"), run(2.05, 5.0, "pass"))
    assert more_error_problems(split(), moved, "x") == []
    flipped = swept(run(0.0, 2.0, "fail"), run(2.05, 2.05, "pass"), run(2.1, 5.0, "fail"))
    before = swept(run(0.0, 2.0, "fail"), run(2.1, 5.0, "fail"))
    assert more_error_problems(before, flipped, "x") == [
        "x: start w1 2.050 went from unswept beside fail and fail to pass"
    ]


def test_transforms():
    scene = copy.deepcopy(SCENE)
    scene["objects"].append({"type": "ac", "wall_id": "w1", "span_ft": [5, 6], "source": "tape"})
    more = with_more_error(scene, RULES)
    # A 24 ft chain: a tap default can drift to 0.3 + 0.16 * 24 = 4.14; tape does not drift.
    assert [o["plus_minus_ft"] for o in more["objects"]] == pytest.approx([4.64, 0.55])
    assert more["walls"][0]["plus_minus_ft"] == 0.5
    default_wall = copy.deepcopy(scene)
    del default_wall["walls"][0]["plus_minus_ft"]
    assert with_more_error(default_wall, RULES)["walls"][0]["plus_minus_ft"] == pytest.approx(4.64)
    assert more["meter"]["plus_minus_ft"] == 0.5  # SCENE's meter is exact
    assert [o["source"] for o in tape_to_tap(scene)["objects"]] == ["tap", "tap"]
    assert tape_to_tap(SCENE) is None
    less = with_less_coverage(scene)
    assert less["coverage"]["observed"][0]["span_ft"] == [-11.5, 9.5]
    assert less["coverage"]["observed"][1]["out_ft"] == 2.5
    short = with_ground_short_of(scene, RULES)  # largest ground radius 1: out to 2.73
    assert [e.get("out_ft") for e in short["coverage"]["observed"]] == [
        None,
        pytest.approx(22 / 12 + 1 - 0.1),
        1.0,
    ]
    assert with_ground_short_of(short, RULES) is None  # nothing left beyond the radius
    r = result()
    r["missing_evidence"] = [
        {"kind": "band", "band": "ground", "span_ft": [12, 11], "message": ""},
        {"kind": "past_end", "side": "left", "message": ""},
    ]
    captured = with_requests_captured(scene, r)
    assert captured["coverage"]["observed"][-1] == {
        "band": "ground",
        "span_ft": [11, 12],
        "out_ft": 40.0,
    }
    assert with_requests_captured(scene, result()) is None


def test_a_ground_request_is_redundant_only_as_far_out_as_it_asks():
    r = result()
    ask = {"kind": "band", "band": "ground", "span_ft": [-3.0, -1.0], "message": ""}
    r["missing_evidence"] = [ask | {"out_ft": 2.5}]  # SCENE saw this ground 3 ft out
    assert any("lists as observed" in m for m in invariant_problems(SCENE, r, rules=RULES))
    r["missing_evidence"] = [ask | {"out_ft": 3.5}]
    assert not any("lists as observed" in m for m in invariant_problems(SCENE, r, rules=RULES))


# --- Every band's reach, and the wall's source ---------------------------------------------------

PASS_RUN = {"wall_id": "w1", "start_ft": [1.0, 1.0], "outcome": "pass", "failing": [], "unsure": []}


def passing(check_id: str) -> dict:
    return {**check(), "id": check_id, "measured_ft": None, "plus_minus_ft": None}


def with_wall_seen(out_ft: float | None) -> dict:
    scene = copy.deepcopy(SCENE)
    wall = scene["coverage"]["observed"][0]
    wall.pop("out_ft", None)
    if out_ft is not None:
        wall["out_ft"] = out_ft
    return scene


def test_a_wall_seen_too_low_does_not_settle_an_opening():
    r = result(checks=[check(), passing("opening_clearance")])
    msgs = invariant_problems(with_wall_seen(1.0), r, rules=RULES)
    needs = "check opening_clearance passes but needs wall [0.00, 4.58] observed higher than 6.5 ft"
    assert any(needs in m for m in msgs)
    assert invariant_problems(with_wall_seen(7.0), r, rules=RULES) == []
    assert invariant_problems(with_wall_seen(None), r, rules=RULES) == []  # seen to headroom


def test_a_request_to_see_higher_than_the_view_reached_is_not_redundant():
    r = result()
    r["missing_evidence"] = [
        {"kind": "band", "band": "wall", "span_ft": [-3.0, -1.0], "out_ft": 6.6, "message": ""}
    ]
    low = invariant_problems(with_wall_seen(1.0), r, rules=RULES)
    assert not any("lists as observed" in m for m in low)
    # A view with no out_ft reached headroom (6.5), not 6.6.
    default = invariant_problems(with_wall_seen(None), r, rules=RULES)
    assert not any("lists as observed" in m for m in default)
    full = invariant_problems(with_wall_seen(7.0), r, rules=RULES)
    assert any("lists as observed" in m for m in full)


BAND_RULES = replace(
    RULES,
    needs=RULES.needs
    | {
        "facing_gap": (Need("facing", 0.0, 22 / 12 + 3.0),),
        "headroom": (Need("overhead", 0.0, 6.5),),
    },
)
FACING_RULES = replace(RULES, needs=RULES.needs | {"facing_gap": BAND_RULES.needs["facing_gap"]})
HEADROOM_RULES = replace(RULES, needs=RULES.needs | {"headroom": BAND_RULES.needs["headroom"]})


def test_facing_and_headroom_passes_need_their_bands():
    r = result(checks=[check(), passing("facing_gap"), passing("headroom")])
    msgs = invariant_problems(SCENE, r, rules=BAND_RULES)
    assert any(
        "facing_gap passes but needs facing [1.00, 3.58] observed, none seen" in m for m in msgs
    )
    assert any(
        "headroom passes but needs overhead [1.00, 3.58] observed, none seen" in m for m in msgs
    )


def test_a_short_facing_view_needs_a_measurement():
    r = result(checks=[check(), passing("facing_gap")])
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"].append({"band": "facing", "span_ft": [0.0, 5.0], "out_ft": 3.0})
    msgs = invariant_problems(scene, r, rules=FACING_RULES)
    assert any("out to 4.83 ft or measured, seen 3.00 ft" in m for m in msgs)
    scene["facing"] = [{"wall_id": "w1", "span_ft": [0.0, 5.0], "depth_ft": 8.0}]
    assert invariant_problems(scene, r, rules=FACING_RULES) == []


def test_an_overhead_seen_clear_all_the_way_up_settles_headroom():
    r = result(checks=[check(), passing("headroom")])
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"].append({"band": "overhead", "span_ft": [0.0, 5.0]})
    assert invariant_problems(scene, r, rules=HEADROOM_RULES) == []
    scene["coverage"]["observed"][-1]["out_ft"] = 5.0  # clear only to 5 ft, under 6.5
    assert any(
        "overhead [1.00, 3.58]" in m for m in invariant_problems(scene, r, rules=HEADROOM_RULES)
    )


def test_battery_clearance_is_needed_when_its_rule_reaches_past_gas_or_a_battery_is_marked():
    # BAND_RULES set battery_clearance's radius (100) above gas's (1): the server evaluates it,
    # so a result that leaves it out is flagged and its coverage is needed anyway.
    wide = replace(RULES, needs=RULES.needs | {"battery_clearance": (Need("ground", 100.0),)})
    r = result(checks=[check()], sweep=[PASS_RUN])
    msgs = invariant_problems(SCENE, r, rules=wide)
    assert "the result leaves out check battery_clearance, which the rules define" in msgs
    assert any("battery_clearance needs ground" in m for m in msgs)
    # With its radius at gas's and no battery marked, the server doesn't evaluate it.
    level = replace(RULES, needs=RULES.needs | {"battery_clearance": (Need("ground", 1.0),)})
    assert expected_checks(SCENE, level) == set(level.needs) - {"battery_clearance"}
    marked = copy.deepcopy(SCENE)
    marked["objects"].append({"type": "battery", "wall_id": "w1", "span_ft": [8.0, 9.0]})
    assert "battery_clearance" in expected_checks(marked, level)


@pytest.mark.parametrize(
    ("wall", "error"),
    [
        ({}, 0.3),
        ({"source": "tap"}, 0.3),
        ({"source": "mesh"}, 0.5),
        ({"source": "plane"}, 0.75),
        ({"source": "mesh", "plus_minus_ft": 0.2}, 0.2),
    ],
)
def test_the_wall_source_sets_the_default_error(wall, error):
    meter = {"pos": [0.0, 4.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0}
    scene = {"meter": meter, "walls": [{"id": "w1", "baseline": [[0, 0], [9, 0]], **wall}]}
    drift = 0.0 if "plus_minus_ft" in wall else 0.16 * 1.0  # far edge 1 ft from the meter
    assert battery_error(scene, RULES, "w1", 0.0, 1.0) == pytest.approx(error + drift)


def test_a_request_to_walk_past_the_real_end_covers_what_lies_beyond_it():
    wide = replace(
        RULES,
        needs=RULES.needs | {"gas_clearance": (Need("ground", 1.0), Need("wall", 3.0, height=6.5))},
    )
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["baseline"] = [[-1.0, 0.0], [12.0, 0.0]]  # the chain's left end is s = -1
    scene["coverage"]["ends"]["left"] = {"kind": "unexplored"}
    scene["coverage"]["observed"][0]["span_ft"] = [-1.0, 10.0]
    r = result(checks=[check("unsure", None, cause="unobserved")])
    r["reasons"] = [{"code": "unobserved_area", "message": ""}]
    r["missing_evidence"] = [
        {
            "kind": "band",
            "band": "ground",
            "span_ft": [0, 5],
            "checks": ["gas_clearance"],
            "message": "",
        }
    ]
    # The spot [1, 3.58] needs the wall over [-2, 6.58]; [-2, -1] lies past the end.
    assert any("no wall request names it" in m for m in missing_evidence_problems(scene, r, wide))

    def past_end(at: float) -> dict:
        return {"kind": "past_end", "side": "left", "span_ft": [at, at], "message": ""}

    asked = r | {"missing_evidence": [*r["missing_evidence"], past_end(-1.0)]}
    assert missing_evidence_problems(scene, asked, wide) == []
    elsewhere = r | {"missing_evidence": [*r["missing_evidence"], past_end(1.0)]}
    assert missing_evidence_problems(scene, elsewhere, wide) != []  # not the chain's end
    scene["coverage"]["ends"]["left"] = {"kind": "limit"}
    assert missing_evidence_problems(scene, asked, wide) != []  # nothing to walk past


def test_chain_ends_are_measured_from_the_meter():
    scene = {
        "meter": {"pos": [2.0, 4.0, 0.3], "wall_id": "b"},
        "walls": [
            {"id": "a", "baseline": [[-5.0, 3.0], [-5.0, 0.0]]},
            {"id": "b", "baseline": [[-5.0, 0.0], [5.0, 0.0]]},
        ],
    }
    assert chain_ends_s(scene, RULES) == pytest.approx((-10.0, 3.0))


def test_a_view_that_only_reaches_the_needed_height_is_not_higher():
    r = result(checks=[check(), passing("opening_clearance")])
    msgs = invariant_problems(with_wall_seen(6.5), r, rules=RULES)
    assert any("observed higher than 6.5 ft" in m for m in msgs)
    assert invariant_problems(with_wall_seen(6.51), r, rules=RULES) == []


def test_a_facing_view_that_only_reaches_the_needed_depth_is_not_beyond():
    r = result(checks=[check(), passing("facing_gap")])
    scene = copy.deepcopy(SCENE)
    need = 22 / 12 + 3.0
    scene["coverage"]["observed"].append({"band": "facing", "span_ft": [0.0, 5.0], "out_ft": need})
    assert any("facing [1.00, 3.58]" in m for m in invariant_problems(scene, r, rules=FACING_RULES))
    scene["coverage"]["observed"][-1]["out_ft"] = need + 0.01
    assert invariant_problems(scene, r, rules=FACING_RULES) == []


def test_each_start_in_a_sweep_run_needs_only_its_own_reach():
    # A pool radius of 10 ft with the default wall error. Start 1's battery [1, 3.58] is 0.87 ft
    # uncertain, so it needs ground out to 1.83 + 10 + 0.87 = 12.71 ft; start 10's is 2.31 ft
    # uncertain. As one battery at start 10's error, the run would ask 14.15 ft in front of start 1.
    pool = replace(RULES, needs={"pool_clearance": (Need("ground", 10.0),)})
    scene = {
        "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0},
        "walls": [{"id": "w1", "baseline": [[-40.0, 0.0], [40.0, 0.0]]}],
        "objects": [],
        "coverage": {
            "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
            "observed": [
                {"band": "wall", "span_ft": [-40.0, 40.0]},
                {"band": "ground", "span_ft": [-40.0, 2.0], "out_ft": 13.5},
                {"band": "ground", "span_ft": [2.0, 40.0], "out_ft": 25.0},
            ],
        },
    }
    run = {"wall_id": "w1", "start_ft": [1.0, 10.0], "outcome": "pass", "failing": [], "unsure": []}
    r = result(spot=False, checks=[check() | {"id": "pool_clearance"}], sweep=[run])
    assert coverage_problems(scene, r, pool) == []
    scene["coverage"]["observed"][1]["out_ft"] = 12.5
    assert any(
        "at start 1.00 pool_clearance needs ground" in m and "out to 12.71 ft" in m
        for m in coverage_problems(scene, r, pool)
    )


def test_sweep_run_starts_are_sampled_at_the_rules_step_plus_the_ends():
    assert run_starts([1.0, 1.5], 1 / 6) == pytest.approx([1.0, 7 / 6, 8 / 6, 1.5])
    assert run_starts([2.0, 2.0], 1 / 6) == [2.0]
    with pytest.raises(ValueError, match=r"sweep\.step_ft"):
        run_starts([1.0, 2.0], None)


def test_facing_and_headroom_need_the_band_past_the_battery_by_the_wall_error():
    # The spot [1, 3.58] on a wall with 0.3 ft of error needs facing seen over [0.7, 3.88].
    r = result(checks=[passing("facing_gap")])
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["plus_minus_ft"] = 0.3
    scene["coverage"]["observed"][1]["out_ft"] = 3.2  # gas: 1.83 + 1.3 in front and past ends
    scene["coverage"]["observed"].append({"band": "facing", "span_ft": [1.0, 3.6]})
    assert any("facing [0.70, 1.00]" in m for m in invariant_problems(scene, r, rules=FACING_RULES))
    scene["coverage"]["observed"][-1]["span_ft"] = [0.7, 3.9]
    assert invariant_problems(scene, r, rules=FACING_RULES) == []


def test_facing_and_headroom_widen_by_the_default_error_with_drift():
    # The caretaker's repro: no explicit wall error, views over [5.7, 8.88] out 9 ft. At start 6
    # the battery [6, 8.58] is 0.3 + 0.16 x 8.58 = 1.67 ft uncertain, so both bands are needed
    # over [4.33, 10.25]; the error without drift (0.3) would ask only [5.7, 8.88].
    r = result(checks=[passing("facing_gap"), passing("headroom")], spot=False)
    r["sweep"][0]["start_ft"] = [6.0, 6.0]
    scene = copy.deepcopy(SCENE)
    del scene["walls"][0]["plus_minus_ft"]
    for band in ("facing", "overhead"):
        scene["coverage"]["observed"].append(
            {"band": band, "span_ft": [5.7, 6 + 31 / 12 + 0.3], "out_ft": 9.0}
        )
    problems = coverage_problems(scene, r, BAND_RULES)
    assert any("facing [4.33, 5.70] observed, none seen" in m for m in problems)
    assert any("overhead [4.33, 5.70] observed, none seen" in m for m in problems)


@pytest.mark.parametrize(
    ("errors", "right_end"),
    [
        ((None, None), 5.0),
        ((0.1, 0.1), 5.4),
        ((0.3, 0.2), 5.0),
        ((0.3, 0.0), 5.4),
        ((0.0, 0.0), 5.4),
    ],
)
def test_walls_meet_across_a_space_within_both_errors(errors, right_end):
    # 0.4 ft between the walls. Within both walls' own errors (0.3 + 0.3 by default, capped at
    # 0.6) they meet and s skips the space; wider, it is a gap that s counts. Exact walls keep
    # any gap (server/scene.py at 7133283).
    scene = {
        "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "a"},
        "walls": [
            {"id": "a", "baseline": [[-5.0, 0.0], [0.0, 0.0]]},
            {"id": "b", "baseline": [[0.4, 0.0], [5.4, 0.0]]},
        ],
    }
    for wall, error in zip(scene["walls"], errors, strict=True):
        if error is not None:
            wall["plus_minus_ft"] = error
    assert chain_ends_s(scene, RULES) == pytest.approx((-5.0, right_end))


def test_the_join_uses_the_walls_own_error_not_drift():
    # 0.8 ft apart, 20 ft from the meter: drift would make the errors exceed it, but the join
    # uses the default 0.3 + 0.3 capped at 0.6, so it is a gap.
    scene = {
        "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "a"},
        "walls": [
            {"id": "a", "baseline": [[0.0, 0.0], [20.0, 0.0]]},
            {"id": "b", "baseline": [[20.8, 0.0], [25.0, 0.0]]},
        ],
    }
    assert chain_ends_s(scene, RULES) == pytest.approx((0.0, 25.0))


def short_wall_scene(height: float, span: list[list[float]]) -> dict:
    scene = copy.deepcopy(SCENE)
    scene["walls"] = [
        {"id": "w1", "baseline": [[-12.0, 0.0], span[0]], "plus_minus_ft": 0.3},
        {"id": "low", "baseline": span, "plus_minus_ft": 0.3, "height_ft": height},
    ]
    return scene


@pytest.mark.parametrize(("height", "flagged"), [(3.0, True), (3.25, True), (3.26, False)])
def test_backing_passes_only_if_every_declared_wall_within_error_is_taller(height, flagged):
    # The spot [1, 3.58] on w1, which ends at 3.7; the low wall starts there, within 0.3 ft.
    scene = short_wall_scene(height, [[3.7, 0.0], [12.0, 0.0]])
    r = result(checks=[check(), passing("wall_backing")])
    msgs = declared_height_problems(scene, r, RULES)
    assert bool(msgs) == flagged
    if flagged:
        assert "check wall_backing passes but wall low" in msgs[0]


def test_a_short_wall_beyond_the_error_does_not_matter():
    scene = short_wall_scene(2.0, [[4.0, 0.0], [12.0, 0.0]])
    r = result(checks=[check(), passing("wall_backing")], sweep=[])
    assert declared_height_problems(scene, r, RULES) == []


@pytest.mark.parametrize(
    ("height", "out_ft", "settled"),
    [
        (6.5, None, True),  # no out_ft: seen to headroom, inclusively
        (6.5 + 1e-10, None, True),  # within the server's float tolerance
        (6.51, None, False),  # more than headroom needs an explicit view
        (6.5, 6.5, False),  # an explicit view must pass the height
        (6.5, 6.51, True),
        (1.0, None, True),
    ],
)
def test_a_wall_view_without_out_ft_reached_headroom_height(height, out_ft, settled):
    scene = copy.deepcopy(SCENE)
    entry = {"band": "wall", "span_ft": [-12.0, 10.0]}
    if out_ft is not None:
        entry["out_ft"] = out_ft
    scene["coverage"]["observed"][0] = entry
    seen = observed(scene, "wall", height, beyond=True, wall_default_ft=RULES.headroom_ft)
    assert (seen == [(-12.0, 10.0)]) == settled


def test_a_pass_needing_the_wall_above_headroom_is_flagged_without_out_ft():
    tall = replace(RULES, needs=RULES.needs | {"opening_clearance": (Need("wall", 1.0, 7.0),)})
    r = result(checks=[check(), passing("opening_clearance")])
    assert any("observed higher than 7.0 ft" in m for m in invariant_problems(SCENE, r, rules=tall))


def test_without_a_spot_or_the_reason_no_photo_is_owed():
    # Every position fails anyway: no spot, no unobserved_area reason, nothing to capture.
    unseen = check("unsure", None, cause="unobserved")
    r = result(decision="reject", spot=False, checks=[unseen])
    assert missing_evidence_problems(SCENE, r, RULES) == []


def test_a_spot_with_an_unobserved_check_needs_the_reason_and_the_request():
    # The server marks the spot's check unsure for lack of coverage but drops both the reason
    # and the request: the homeowner would never be asked for the view that settles it.
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"] = [scene["coverage"]["observed"][0]]  # no ground seen
    r = result(checks=[check("unsure", None, cause="unobserved")])
    msgs = missing_evidence_problems(scene, r, RULES)
    assert msgs and all("no reason unobserved_area and no ground request" in m for m in msgs)
    assert any("no reason unobserved_area" in m for m in invariant_problems(scene, r, rules=RULES))
    r["reasons"].append({"code": "unobserved_area", "message": ""})
    r["missing_evidence"] = [
        {
            "kind": "band",
            "band": "ground",
            "span_ft": [-1, 5],
            "out_ft": 3.0,
            "checks": ["gas_clearance"],
            "message": "",
        }
    ]
    assert missing_evidence_problems(scene, r, RULES) == []


def test_what_lies_past_an_unexplored_end_needs_only_the_walk_past_it():
    # As the server answers once every band view is in: reason unexplored_end, a past_end
    # request naming no checks, and checks unsure only for what lies beyond the end.
    wide = replace(
        RULES, needs=RULES.needs | {"gas_clearance": (Need("ground", 1.0), Need("wall", 3.0, 6.5))}
    )
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["baseline"] = [[-1.0, 0.0], [12.0, 0.0]]  # the chain's left end is s = -1
    scene["coverage"]["ends"]["left"] = {"kind": "unexplored"}
    scene["coverage"]["observed"][0]["span_ft"] = [-1.0, 10.0]
    r = result(checks=[check("unsure", None, cause="unobserved")])
    r["reasons"] = [{"code": "unexplored_end", "message": ""}]
    assert any("wall [-2.00, 6.58]" in m for m in missing_evidence_problems(scene, r, wide))
    r["missing_evidence"] = [
        {"kind": "past_end", "side": "left", "span_ft": [-1.0, -1.0], "message": ""}
    ]
    assert missing_evidence_problems(scene, r, wide) == []


def test_a_view_at_headroom_does_not_settle_a_request_just_past_it():
    # App f14947e reports the wall seen to exactly 6.5 ft; the server then asks for a view past
    # headroom, out_ft 6.500001. That request is not redundant.
    r = result()
    r["missing_evidence"] = [
        {"kind": "band", "band": "wall", "span_ft": [-3.0, -1.0], "out_ft": 6.500001, "message": ""}
    ]
    msgs = invariant_problems(with_wall_seen(6.5), r, rules=RULES)
    assert not any("lists as observed" in m for m in msgs)
    msgs = invariant_problems(with_wall_seen(6.500001), r, rules=RULES)
    assert any("lists as observed" in m for m in msgs)


def test_a_captured_request_claims_only_the_reach_it_asked_for():
    # Review 4114352249: a ground request out 0.5 ft was captured as seen 40 ft out.
    r = result()
    r["missing_evidence"] = [
        {"kind": "band", "band": "ground", "span_ft": [0, 5], "out_ft": 0.5, "message": ""},
        {"kind": "band", "band": "wall", "span_ft": [2, 3], "out_ft": 6.500001, "message": ""},
        {"kind": "band", "band": "facing", "span_ft": [2, 3], "message": ""},
    ]
    added = with_requests_captured(SCENE, r)["coverage"]["observed"][-3:]
    assert added == [
        {"band": "ground", "span_ft": [0, 5], "out_ft": 0.5},
        {"band": "wall", "span_ft": [2, 3], "out_ft": 6.500001},
        {"band": "facing", "span_ft": [2, 3]},
    ]


def test_a_sweep_run_shorter_than_a_step_keeps_its_first_start():
    # Review 4114352251: run_starts([0, 0.4], 1) returned only 0.4.
    assert run_starts([0.0, 0.4], 1.0) == [0.0, 0.4]


def test_a_passing_spot_must_pass_in_the_sweep_too():
    # Review 4114352261: the spot passes but its start is in a failing run, stats consistent.
    run = {
        "wall_id": "w1",
        "start_ft": [1.0, 1.0],
        "outcome": "fail",
        "failing": ["gas_clearance"],
        "unsure": [],
    }
    r = result(sweep=[run])
    r["stats"] |= {"pass": 0, "fail": 1}
    assert any(
        "spot passes but the sweep has start 1.00 on w1 as fail" in m
        for m in invariant_problems(SCENE, r, rules=RULES)
    )


def test_wall_backing_needs_the_wall_seen_past_the_battery_by_its_error():
    # Server 4a9fa44: the wall behind every place the battery may sit, [s0 - e, s1 + e].
    backing = replace(RULES, needs={"wall_backing": (Need("wall", 0.0, 3.25),)})
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["plus_minus_ft"] = 0.3
    scene["coverage"]["observed"][0] = {"band": "wall", "span_ft": [1.0, 3.6], "out_ft": 4.0}
    r = result(checks=[passing("wall_backing")])
    assert any("wall [0.70, 3.88]" in m for m in coverage_problems(scene, r, backing))
    scene["coverage"]["observed"][0]["span_ft"] = [0.0, 3.9]
    assert coverage_problems(scene, r, backing) == []


def inside_corner(w2_out: float) -> dict:
    # g05: w1 runs +x with its outside at +z; w2 turns up +z from its end, facing back over w1.
    return {
        "meter": {"pos": [-6.0, 4.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0},
        "walls": [
            {"id": "w1", "baseline": [[-7.0, 0.0], [4.0, 0.0]], "plus_minus_ft": 0.0},
            {"id": "w2", "baseline": [[4.0, 0.0], [4.0, 12.0]], "plus_minus_ft": 0.0},
        ],
        "objects": [],
        "coverage": {
            "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
            "observed": [
                {"band": "wall", "span_ft": [-1.0, 22.0]},
                {"band": "ground", "span_ft": [-7.0, 10.0], "out_ft": 10.5},
                {"band": "ground", "span_ft": [10.0, 22.0], "out_ft": w2_out},
            ],
        },
    }


def test_ground_in_front_of_one_wall_may_be_seen_from_the_next_in_an_inside_corner():
    # The battery at s 4.04 (x -1.96 to 0.62) needs a 10 ft pool radius, ground out to 11.83 in
    # front and within 10 ft of its corners; w1's band reaches 10.5. From x = 4, w2's band
    # reaching 12 covers the rest.
    pool = replace(RULES, needs={"pool_clearance": (Need("ground", 10.0),)})
    run = {
        "wall_id": "w1",
        "start_ft": [4.04, 4.04],
        "outcome": "pass",
        "failing": [],
        "unsure": [],
    }
    r = result(spot=False, checks=[check() | {"id": "pool_clearance"}], sweep=[run])
    assert coverage_problems(inside_corner(12.0), r, pool) == []
    short = coverage_problems(inside_corner(4.0), r, pool)  # w2's band stops at x = 0
    assert any("pool_clearance needs ground" in m and "out to 11.83" in m for m in short)


def test_a_ground_gap_narrower_than_the_sampling_step_is_not_counted_seen():
    # 5a4b4f4 passed a spot over ground seen [-40, 7.5] and [7.519, 40], out 30 ft: both edges
    # of the 0.019 ft gap lie on a band, but its middle, s = 7.5095, was never seen.
    rules = replace(RULES, needs={"gas_clearance": (Need("ground", 1.0),)})
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["baseline"] = [[-40.0, 0.0], [40.0, 0.0]]
    scene["coverage"]["observed"] = [
        {"band": "wall", "span_ft": [-40.0, 40.0]},
        {"band": "ground", "span_ft": [-40.0, 7.5], "out_ft": 30.0},
        {"band": "ground", "span_ft": [7.519, 40.0], "out_ft": 30.0},
    ]
    r = result(checks=[check()], sweep=[])
    r["spot"]["span_ft"] = [6.0, 6.0 + 31 / 12]
    msgs = coverage_problems(scene, r, rules)
    assert any("ground [7.50, 7.52] observed, none seen" in m for m in msgs)
    assert GroundPlan(scene, rules).seen((7.5095, 1.0)) is False
    scene["coverage"]["observed"][2]["span_ft"] = [7.5, 40.0]
    assert coverage_problems(scene, r, rules) == []


@pytest.mark.parametrize("outcome", ["pass", "unsure"])
def test_a_reject_must_have_every_start_failing(outcome):
    # Review 4114676165: decision reject, no spot, nothing asked, but the sweep has a start
    # that is not failing and stats that agree with it.
    run = {
        "wall_id": "w1",
        "start_ft": [1.0, 1.0],
        "outcome": outcome,
        "failing": [],
        "unsure": [] if outcome == "pass" else ["gas_clearance"],
    }
    r = result(decision="reject", spot=False, checks=[check("fail", 2.0)], sweep=[run])
    r["stats"] |= {
        "candidates": 1,
        "pass": int(outcome == "pass"),
        "unsure": int(outcome == "unsure"),
        "fail": 0,
    }
    assert any(
        f"decision reject but the sweep has {outcome} starts" in m
        for m in invariant_problems(SCENE, r, rules=RULES)
    )


def broadly_seen() -> dict:
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["ends"] = {"left": {"kind": "limit"}, "right": {"kind": "limit"}}
    scene["coverage"]["observed"] = [
        {"band": "wall", "span_ft": [-100.0, 100.0]},
        {"band": "ground", "span_ft": [-100.0, 100.0], "out_ft": 30.0},
    ]
    return scene


def test_a_passing_spot_must_stand_on_the_scanned_wall():
    # Review 4114676173: a pass at start 11, 31 in wide, on a wall that ends at s = 12.
    r = result(
        decision="pass",
        sweep=[
            {
                "wall_id": "w1",
                "start_ft": [11.0, 11.0],
                "outcome": "pass",
                "failing": [],
                "unsure": [],
            }
        ],
    )
    r["spot"]["span_ft"] = [11.0, 11.0 + 31 / 12]
    msgs = invariant_problems(broadly_seen(), r, rules=RULES)
    assert any("spot [11.00, 13.58] runs past its straight wall [-12.00, 12.00]" in m for m in msgs)
    assert any("sweep pass start 11.00 runs past its straight wall" in m for m in msgs)


def test_walls_meeting_in_a_straight_line_are_one_stretch_and_a_corner_is_not():
    scene = broadly_seen()
    scene["walls"] = [
        {"id": "w1", "baseline": [[-12.0, 0.0], [5.0, 0.0]], "plus_minus_ft": 0.0},
        {"id": "w2", "baseline": [[5.0, 0.0], [12.0, 0.0]], "plus_minus_ft": 0.0},
    ]
    assert straight_pieces(scene, RULES) == [(-12.0, 12.0)]
    r = result(sweep=[])
    r["spot"]["span_ft"] = [4.0, 4.0 + 31 / 12]  # across the join
    assert footprint_problems(scene, r, RULES) == []
    scene["walls"][1]["baseline"] = [[5.0, 0.0], [5.0, 7.0]]  # a corner at s = 5
    assert straight_pieces(scene, RULES) == [(-12.0, 5.0), (5.0, 12.0)]
    assert footprint_problems(scene, r, RULES) == [
        "spot [4.00, 6.58] runs past its straight wall [-12.00, 5.00]"
    ]
    r["spot"]["span_ft"] = [5.0, 5.0 + 31 / 12]  # starts exactly at the corner, on the next wall
    assert footprint_problems(scene, r, RULES) == []


def test_a_pass_by_the_smallest_reported_margin_is_a_pass():
    # 3baa338 puts a spot at the edge of its working-space pass: 1.444446 +/- 1.444445 against
    # 0. The server rounds to 6 decimals, which keeps order, so a reported margin above the
    # error proves the true one is; equal reported numbers do not.
    c = check("pass", 1.444446, 1.444445, 0.0) | {"id": "meter_working_space"}
    assert margin_problem(c) is None
    assert "should be unsure" in margin_problem(c | {"measured_ft": 1.444445})
    at_most = check("pass", 19.999999, 0.0, 20.0, cmp="at_most")
    assert margin_problem(at_most) is None
    assert "should be unsure" in margin_problem(at_most | {"measured_ft": 20.0})


# Final Opus review of 255bc84 (.local/final-opus-review-probes/pr13), findings 1 to 5.

D_FT = 22 / 12
POOL = replace(RULES, needs={"pool_clearance": (Need("ground", 10.0),)})


def open_wall(ground_out: float, meter_pm: float | None = None) -> dict:
    """The probes' scene: one 60 ft wall, error 0.3, everything but the ground seen."""
    meter = {"pos": [0.0, 4.0, 0.0], "wall_id": "w1"}
    if meter_pm is not None:
        meter["plus_minus_ft"] = meter_pm
    return {
        "meter": meter,
        "walls": [{"id": "w1", "baseline": [[-30.0, 0.0], [30.0, 0.0]], "plus_minus_ft": 0.3}],
        "objects": [],
        "coverage": {
            "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
            "observed": [
                {"band": "wall", "span_ft": [-30.0, 30.0]},
                {"band": "ground", "span_ft": [-30.0, 30.0], "out_ft": ground_out},
            ],
        },
    }


def pass_run(a: float, b: float, wall_id: str = "w1") -> dict:
    return {"wall_id": wall_id, "start_ft": [a, b], "outcome": "pass", "failing": [], "unsure": []}


def test_the_battery_error_includes_the_meters_error_in_plan():
    # meter_gap.py: ground seen to D + 10 + 0.3 + 0.15. With the meter's default 0.3 the pool
    # needs D + 10 + 0.6 (server 3baa338 _errors: wall at the far edge plus the meter's).
    scene = open_wall(D_FT + 10.45)
    r = result(spot=False, checks=[check() | {"id": "pool_clearance"}], sweep=[pass_run(2.0, 2.0)])
    assert any(
        "observed out to 12.43 ft, seen 12.28 ft" in m for m in coverage_problems(scene, r, POOL)
    )
    assert battery_error(scene, RULES, "w1", 2.0, 2.0 + 31 / 12) == pytest.approx(0.6)


def test_the_battery_error_includes_the_slide_round_a_corner():
    # Round a right angle from the meter, the meter's 0.3 slides the battery by 0.3 x sqrt(2),
    # more than its 0.3 in plan (server 3baa338 _slide).
    scene = open_wall(20.0, meter_pm=0.3)
    scene["walls"] = [
        {"id": "w1", "baseline": [[-30.0, 0.0], [10.0, 0.0]], "plus_minus_ft": 0.3},
        {"id": "w2", "baseline": [[10.0, 0.0], [10.0, -20.0]], "plus_minus_ft": 0.3},
    ]
    assert battery_error(scene, RULES, "w2", 15.0, 17.58) == pytest.approx(
        0.3 + 0.3 * 2**0.5 + 0.3 * 2**0.5
    )


def test_ground_just_past_the_battery_is_needed_out_to_the_straight_line_reach():
    # ends.py: ground in front seen to 12.5, just past the ends only to 10.4. A point d past an
    # end is within R of the footprint out to D + sqrt(R^2 - d^2), not R - d.
    lo, hi = 5.0, 5.0 + 31 / 12
    scene = open_wall(10.4, meter_pm=0.0)
    scene["coverage"]["observed"][1:] = [
        {"band": "ground", "span_ft": [-30.0, lo], "out_ft": 10.4},
        {"band": "ground", "span_ft": [lo, hi], "out_ft": 12.5},
        {"band": "ground", "span_ft": [hi, 30.0], "out_ft": 10.4},
    ]
    gaps = reach_gaps(scene, POOL, "pool_clearance", "w1", lo, hi)
    assert gaps and gaps[0][1].startswith("ground [-5.30, 5.00] observed out to 12.13 ft")


def test_a_passing_footprint_keeps_the_wall_error_clear_of_its_stretch_end():
    # corner.py: a run whose last footprint ends at the corner s = 10 is unsure at 3baa338
    # (check_backing: within e of the segment's end); relabelled pass, it must be flagged.
    scene = open_wall(20.0)
    scene["walls"] = [
        {"id": "w1", "baseline": [[-30.0, 0.0], [10.0, 0.0]], "plus_minus_ft": 0.3},
        {"id": "w2", "baseline": [[10.0, 0.0], [10.0, -20.0]], "plus_minus_ft": 0.3},
    ]
    r = result(spot=False, sweep=[pass_run(6.846, 10.0 - 31 / 12)])
    assert footprint_problems(scene, r, RULES) == [
        "sweep pass start 7.01 ends within its error (0.60 ft) of its straight wall's end at 10.00"
    ]
    r["sweep"][0]["outcome"] = "unsure"  # unsure may stand that close
    assert footprint_problems(scene, r, RULES) == []


def test_a_footprint_past_its_stretch_by_more_than_the_slack_is_flagged():
    # With no error, a pass ending 0.2 ft past its wall's end: beyond the 0.05 ft slack.
    scene = open_wall(20.0, meter_pm=0.0)
    scene["walls"][0]["plus_minus_ft"] = 0.0
    r = result(spot=False, sweep=[pass_run(30.2 - 31 / 12, 30.2 - 31 / 12)])
    assert footprint_problems(scene, r, RULES) == [
        "sweep pass start 27.62 runs past its straight wall [-30.00, 30.00]"
    ]


def test_a_result_that_drops_a_check_is_flagged_and_its_coverage_still_needed():
    # dropped.py: a PASS judged on a scene whose ground reaches drive's 5 ft but not pool's 10.
    rules = replace(
        RULES,
        needs={
            "pool_clearance": (Need("ground", 10.0),),
            "drive_clearance": (Need("ground", 5.0),),
        },
    )
    scene = open_wall(D_FT + 5 + 0.6 + 0.1)
    full = result(spot=False, sweep=[pass_run(2.0, 2.0)])
    dropped = full | {"checks": [c for c in full["checks"] if c["id"] != "pool_clearance"]}
    msgs = invariant_problems(scene, dropped, rules=rules)
    assert any("pool_clearance needs ground" in m for m in msgs)
    assert "the result leaves out check pool_clearance, which the rules define" in msgs


def test_the_route_wall_is_needed_past_the_meter_by_its_error():
    # route_end.py: the wall seen from s = 0 only. The route starts at the meter, which may
    # stand 0.3 ft either side, so the wall is needed from -0.3 (server README route_path).
    scene = open_wall(20.0)
    scene["coverage"]["observed"][0]["span_ft"] = [0.0, 30.0]
    r = result(spot=False, checks=[], sweep=[pass_run(3.7, 14.3875)])
    assert any(
        "the wall and cable route [-0.30, 16.97] were not all observed" in m
        for m in coverage_problems(scene, r, RULES)
    )


# Flags at server 3baa338 after the review fixes, each the checker's (see the commit message).


def test_spans_along_the_walls_widen_by_the_along_error_not_the_meters():
    # The app scan: headroom needs the overhead band over [s0 - e, s1 + e] with e the wall's
    # error plus the slide (0 on the meter's wall), not the meter's 0.3 in plan.
    headroom = replace(RULES, needs={"headroom": (Need("overhead", 0.0, 6.5, along=True),)})
    scene = open_wall(20.0)  # wall 0.3, meter default 0.3
    scene["coverage"]["observed"].append({"band": "overhead", "span_ft": [1.7, 4.9]})
    r = result(spot=False, sweep=[pass_run(2.0, 2.0)])
    assert coverage_problems(scene, r, headroom) == []  # needs [1.7, 4.88]
    scene["coverage"]["observed"][-1]["span_ft"] = [1.8, 4.9]
    assert any("overhead [1.70, 1.80]" in m for m in coverage_problems(scene, r, headroom))


def test_ground_short_of_the_reach_by_less_than_the_seen_growth_is_seen():
    # d-gas-drift: ground seen to 14.50 where 14.5007 is needed. The server grows what was seen
    # by 0.0049 ft before comparing (SEEN_GROWTH_FT), so a 0.0007 ft shortfall is seen.
    scene = open_wall(D_FT + 10.6 - 0.0007)
    r = result(spot=False, sweep=[pass_run(2.0, 2.0)])
    assert coverage_problems(scene, r, POOL) == []
    scene["coverage"]["observed"][1]["out_ft"] = D_FT + 10.6 - 0.01
    assert coverage_problems(scene, r, POOL) != []


def test_ground_past_an_inside_corner_behind_the_next_wall_is_the_house():
    # g05 after a capture: past the battery's right end, the straight-line reach runs past the
    # corner at x = 4 to ground behind w2, which the server's yard model leaves out.
    pool = replace(RULES, needs={"pool_clearance": (Need("ground", 10.0),)})
    scene = inside_corner(10.66)
    scene["coverage"]["observed"][1:] = [
        {"band": "ground", "span_ft": [-7.0, 10.0], "out_ft": 10.66},
        {"band": "ground", "span_ft": [10.0, 22.0], "out_ft": 12.0},
    ]
    run = {
        "wall_id": "w1",
        "start_ft": [4.04, 4.04],
        "outcome": "pass",
        "failing": [],
        "unsure": [],
    }
    r = result(spot=False, checks=[check() | {"id": "pool_clearance"}], sweep=[run])
    assert coverage_problems(scene, r, pool) == []
    plan = GroundPlan(scene, pool)
    assert not plan.required((6.0, 5.0))  # behind w2, in front of no wall
    assert plan.required((1.0, 11.5))  # in the corner's yard, in front of w1
    assert plan.required((-9.0, 3.0))  # past the chain's left end


# Codex on 255bc84, verified by the #11 caretaker: three more false certifications.


def test_a_pass_with_no_checks_is_flagged():
    # 4114865912: decision pass, a passing spot and sweep, and checks [].
    r = result(decision="pass", checks=[], complete=False)
    r["policy"]["auto_approve"] = True
    msgs = invariant_problems(broadly_seen(), r, rules=RULES)
    assert "decision pass with no checks" in msgs
    assert "the result leaves out check gas_clearance, which the rules define" in msgs


def test_the_spot_centre_must_sit_on_its_footprint_on_the_wall():
    # 4114865917: centre and meter_offset moved together by 5 ft, footprint unchanged.
    r = result()
    r["spot"]["center"] = [7.29, 0.92]
    r["spot"]["meter_offset_ft"] = [7.29, 0.92]
    msgs = invariant_problems(SCENE, r, rules=RULES)
    assert any(m.startswith("spot.center [7.29, 0.92] is not its footprint's centre") for m in msgs)
    assert any(m.startswith("spot.center [7.29, 0.92] is not the centre of span") for m in msgs)
    assert not any("spot.center" in m for m in invariant_problems(SCENE, result(), rules=RULES))


def test_manual_review_needs_a_spot_that_does_not_fail():
    # 4114865919: manual_review, a failing spot, a failing check and a matching failing run.
    run = {
        "wall_id": "w1",
        "start_ft": [1.0, 1.0],
        "outcome": "fail",
        "failing": ["gas_clearance"],
        "unsure": [],
    }
    r = result(checks=[check("fail", 2.0)], sweep=[run])
    r["spot"]["outcome"] = "fail"
    r["stats"] |= {"pass": 0, "fail": 1}
    msgs = invariant_problems(SCENE, r, rules=RULES)
    assert "decision manual_review with a failing spot" in msgs
    passing_spot = result(checks=[check("fail", 2.0)])
    assert "the spot is pass but its check gas_clearance fails" in invariant_problems(
        SCENE, passing_spot, rules=RULES
    )


def test_a_band_gap_under_the_coverage_tolerance_is_rounding():
    # The app scan at c820cf1, less coverage: wall seen to 18.0, needed to 18.0037 for
    # wall_equipment_above. The server's missing() drops gaps under 0.01 ft; 0.011 stays.
    assert gaps_over([(-6.5, 18.0)], 9.94, 18.0037) == []
    assert gaps_over([(-6.5, 18.0)], 9.94, 18.011) == [(18.0, 18.011)]
    assert gaps_over([(0.0, 1.0), (1.009, 3.0)], 0.5, 2.0) == []
    assert gaps_over([(0.0, 1.0), (1.011, 3.0)], 0.5, 2.0) == [(1.0, 1.011)]


@pytest.mark.parametrize("outcome", ["pass", "unsure"])
def test_interior_sweep_start_cannot_cross_a_corner(outcome):
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["baseline"] = [[-12.0, 0.0], [5.0, 0.0], [5.0, 7.0]]
    result = {"sweep": [{"wall_id": "w1", "start_ft": [1.0, 8.0], "outcome": outcome}]}
    assert footprint_problems(scene, result, RULES)


def test_ground_trimming_keeps_omitted_reach_at_zero():
    scene = copy.deepcopy(SCENE)
    scene["coverage"]["observed"] = [
        {"band": "ground", "span_ft": [-12, 0]},
        {"band": "ground", "span_ft": [0, 12], "out_ft": 100},
    ]
    trimmed = with_ground_short_of(scene, RULES)
    assert trimmed["coverage"]["observed"][0]["out_ft"] == 0


def test_geometric_assumptions_compare_active_rules():
    rules = replace(RULES, corner_allowance_ft=0.5)
    assert (
        len(assumption_mismatches({"battery_width": 100, "corner_allowance": 100}, {}, rules)) == 2
    )
    assert (
        assumption_mismatches({"battery_width": rules.width_ft, "corner_allowance": 0.5}, {}, rules)
        == []
    )
