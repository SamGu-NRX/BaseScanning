"""Regressions from the solver review. Synthetic scenes built on the shared fixture in helpers.py.

Thresholds are the golden test settings (GOLDEN_RULES), not Base policy: facing 5 ft from the
wall, headroom 6.5, clearances 3, confident reach 15, maximum 20, every default error 0.
"""

import math
import time

import pytest
from helpers import (
    W,
    at_start,
    check,
    everything_observed,
    golden_rules,
    observed_band,
    pads_ground,
    parsed,
    rect,
    run,
    shared_fixture,
)

from scene import SceneError
from solver import evaluate_start

# --- 1. unexplored end ----------------------------------------------------------------------------


def test_unexplored_end_next_to_spot_is_not_a_pass() -> None:
    # Before: the area past an unexplored end counted as seen, so a spot 0.02 ft from it passed.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [10, 0]]
    raw["ground"] = pads_ground([(7.4, 30)], hi=30)
    raw["coverage"] = everything_observed(-40, 30)
    raw["coverage"]["ends"]["right"] = {"kind": "unexplored"}
    result = run(raw)
    assert result["decision"] != "pass"
    assert any(
        c["outcome"] == "unsure" and c.get("unsure_cause") == "unobserved" for c in result["checks"]
    )


def test_limit_end_with_wall_beyond_spot_still_passes() -> None:
    # Control for the test above: the same spot on a wall that continues and ends at a real limit.
    raw = shared_fixture()
    raw["ground"] = pads_ground([(7.4, 10)])
    assert run(raw)["decision"] == "pass"


# --- 2. doors with a footprint ------------------------------------------------------------------


def test_garage_door_with_footprint_still_blocks_route() -> None:
    # Before: a door given a footprint 0.35 ft off the wall was treated as standing off it.
    raw = shared_fixture()
    raw["ground"] = pads_ground([(10, 13)])
    raw["objects"].append(
        {
            "type": "garage_door",
            "wall_id": "w1",
            "span_ft": [3, 7],
            "bottom_ft": 0,
            "top_ft": 7,
            "source": "tap",
            "plus_minus_ft": 0.3,
            "footprint": [[3, 0.35], [7, 0.35]],
        }
    )
    assert run(raw)["decision"] == "reject"
    assert at_start(raw, 10.0, "route_path").outcome == "fail"


# --- 3. measurement span edges carry their error ------------------------------------------------


def test_headroom_span_edge_error_counts() -> None:
    # Before: a 5 ft overhead ending at s = 9 was ignored for a battery starting at 9, though
    # where a stretch sits along the wall is only known to the wall's error (± 0.3 here).
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.3
    raw["ground"] = pads_ground([(8.5, 12.5)])
    raw["overheads"] = [
        {"wall_id": "w1", "span_ft": [-40, 9], "clearance_ft": 5, "plus_minus_ft": 0.5},
        {"wall_id": "w1", "span_ft": [9, 40], "clearance_ft": 9, "plus_minus_ft": 0.5},
    ]
    assert at_start(raw, 9.0, "headroom").outcome == "unsure"
    assert at_start(raw, 9.4, "headroom").outcome == "pass"
    # A stretch that lies under the battery wherever the error puts it still fails outright.
    assert at_start(raw, 6.0, "headroom").outcome == "fail"


def test_exact_touching_measurement_does_not_count() -> None:
    # With exact geometry a stretch ending where the battery starts is beside it, not over it.
    raw = shared_fixture()
    raw["ground"] = pads_ground([(9, 12)])
    raw["overheads"] = [
        {"wall_id": "w1", "span_ft": [-40, 9], "clearance_ft": 5, "plus_minus_ft": 0.5},
        {"wall_id": "w1", "span_ft": [9, 40], "clearance_ft": 9, "plus_minus_ft": 0.5},
    ]
    assert at_start(raw, 9.0, "headroom").outcome == "pass"


def test_facing_span_edge_error_counts() -> None:
    # Before: a 1 ft facing gap ending at s = 9 was ignored for a battery starting at 9 on a wall
    # known to ± 0.3.
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.3
    raw["ground"] = pads_ground([(8.5, 12.5)])
    raw["facing"] = [
        {"wall_id": "w1", "span_ft": [-40, 9], "depth_ft": 1, "plus_minus_ft": 0.5},
        {"wall_id": "w1", "span_ft": [9, 40], "depth_ft": 9, "plus_minus_ft": 0.5},
    ]
    assert at_start(raw, 9.0, "facing_gap").outcome == "unsure"
    assert at_start(raw, 9.4, "facing_gap").outcome == "pass"


# --- 4. meter past its wall's end ----------------------------------------------------------------


def test_meter_past_wall_end_is_refused() -> None:
    # Before: the meter was clamped to the wall end, silently shortening every route by 2.5 ft.
    raw = shared_fixture()
    raw["meter"]["pos"] = [42.5, 5, 0]
    with pytest.raises(SceneError, match="past"):
        parsed(raw)


def test_meter_past_wall_end_within_error_is_accepted() -> None:
    raw = shared_fixture()
    raw["meter"]["pos"] = [40.2, 5, 0]
    raw["meter"]["plus_minus_ft"] = 0.3
    parsed(raw)


# --- 5. coverage radius -------------------------------------------------------------------------


def test_coverage_radius_includes_wall_error() -> None:
    # Before: coverage was checked to exactly 3 ft, though the wall itself is only known to ± 0.3.
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.3
    edge = 6 + W + 3.05
    observed_band(raw, "wall", [(-40, edge)])
    observed_band(raw, "ground", [(-40, edge)])
    for check_id in ("gas_clearance", "ac_clearance", "opening_clearance"):
        c = at_start(raw, 6.0, check_id)
        assert (c.outcome, c.unsure_cause) == ("unsure", "unobserved"), check_id


# --- 6. detour error ----------------------------------------------------------------------------


def test_detour_error_flows_into_route_error() -> None:
    # Before: a detour around a ± 1.5 ft photo detection added length but no error to the route.
    rules = golden_rules(errors={"vlm_ft": {"value": 1.5, "source": "t"}})
    raw = shared_fixture()
    raw["ground"] = pads_ground([(13, 16)])
    raw["objects"].append(
        {
            "type": "elec_box",
            "wall_id": "w1",
            "span_ft": [2, 3],
            "bottom_ft": 0.5,
            "top_ft": 2,
            "source": "vlm",
        }
    )
    result = run(raw, rules)
    assert result["route"]["plus_minus_ft"] >= 2 * 1.5
    assert result["decision"] == "manual_review"


# --- 7. gaps between walls ----------------------------------------------------------------------


def two_wall_scene(w2_start, w2_end, pad):
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-40, 0], [3, 0]], "plus_minus_ft": 0.5},
        {"id": "w2", "baseline": [[w2_start, 0], [w2_end, 0]], "plus_minus_ft": 0.5},
    ]
    raw["ground"] = pads_ground([pad], hi=w2_end)
    raw["facing"] = [
        {"wall_id": "w1", "span_ft": [-40, 3], "depth_ft": 9, "plus_minus_ft": 0},
        {"wall_id": "w2", "span_ft": [w2_start, w2_end], "depth_ft": 9, "plus_minus_ft": 0},
    ]
    raw["overheads"] = [
        {"wall_id": "w1", "span_ft": [-40, 3], "clearance_ft": 9, "plus_minus_ft": 0},
        {"wall_id": "w2", "span_ft": [w2_start, w2_end], "clearance_ft": 9, "plus_minus_ft": 0},
    ]
    raw["coverage"] = everything_observed(-40, w2_end)
    return raw


def test_gap_within_wall_error_is_manual_review() -> None:
    # Before: a 0.7 ft gap between walls each ± 0.5 rejected, though the walls may meet.
    raw = two_wall_scene(3.7, 40.7, (9.5, 13.5))
    result = run(raw)
    assert result["decision"] == "manual_review"
    assert check(result, "route_path")["outcome"] == "unsure"


def test_gap_beyond_wall_error_rejects() -> None:
    raw = two_wall_scene(5, 40, (11, 15))
    assert run(raw)["decision"] == "reject"
    assert at_start(raw, 11.0, "route_path").outcome == "fail"


# --- 8. collinear baseline points ---------------------------------------------------------------


@pytest.mark.parametrize(
    "baseline",
    [
        [[-40, 0], [7.5, 0], [40, 0]],
        [[x, 0] for x in range(-40, 41, 2)],
    ],
    ids=["one-mid-point", "every-2-ft"],
)
def test_collinear_baseline_points_are_not_corners(baseline) -> None:
    # Before: a point in the middle of a straight wall split it, failing backing across the point.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = baseline
    result = run(raw)
    assert result["decision"] == "pass"
    assert result["spot"]["span_ft"] == pytest.approx([6, 6 + W], abs=1e-6)


# --- 9. starts from object edges ----------------------------------------------------------------


def test_start_window_between_slanted_pipes_is_found() -> None:
    # Before: only grid starts were tried, so a 0.025 ft pass window between two pipes was missed.
    rules = golden_rules(
        route={
            "confident_reach_ft": {"value": 30, "source": "t"},
            "max_ft": {"value": 40, "source": "t"},
        }
    )
    lo, hi = 14.005, 14.03
    c_right = hi + W + 3 * math.sqrt(2)
    c_left = lo - 3 * math.sqrt(2)
    raw = shared_fixture()
    raw["ground"] = pads_ground([(8, 17)])
    for c, pipe in (
        (c_right, [[c_right - 10, -10], [c_right + 30, 30]]),
        (c_left, [[c_left + 10, -10], [c_left - 30, 30]]),
    ):
        raw["objects"].append(
            {
                "type": "gas_meter",
                "wall_id": "w1",
                "span_ft": [c, c],
                "bottom_ft": 3,
                "top_ft": 4,
                "source": "tap",
                "plus_minus_ft": 0,
                "footprint": pipe,
            }
        )
    assert evaluate_start(parsed(raw, rules), rules, (lo + hi) / 2).outcome == "pass"
    result = run(raw, rules)
    assert result["decision"] == "pass"
    assert lo < result["spot"]["span_ft"][0] < hi


# --- 10. sweep runs at corners ------------------------------------------------------------------


def test_sweep_runs_split_at_corners() -> None:
    # Before: pass runs on both segments of one wall merged into a run spanning the corner.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [8, 0], [8, -32]]
    raw["ground"] = [
        {"type": "lawn", "polygon": rect(-40, 8, 0, 30), "plus_minus_ft": 0},
        {"type": "lawn", "polygon": rect(8, 40, -40, 30), "plus_minus_ft": 0},
    ]
    result = run(raw)
    passing = [r for r in result["sweep"] if r["outcome"] == "pass"]
    assert passing
    assert all("segment" in r for r in passing)
    assert not [r for r in passing if r["start_ft"][0] < 8 - W and r["start_ft"][1] > 8 - W + 1e-6]
    assert at_start(raw, 7.0, "wall_backing").outcome == "fail"


# --- 11. non-finite and absurd numbers ----------------------------------------------------------


def _set_meter_nan(raw):
    raw["meter"]["pos"][0] = float("nan")


def _set_baseline_inf(raw):
    raw["walls"][0]["baseline"][1] = [float("inf"), 0]


def _set_coverage_nan(raw):
    raw["coverage"]["observed"][0]["span_ft"] = [-40, float("nan")]


def _set_baseline_huge(raw):
    raw["walls"][0]["baseline"][1] = [2e5, 0]


@pytest.mark.parametrize(
    "mutate",
    [_set_meter_nan, _set_baseline_inf, _set_coverage_nan, _set_baseline_huge],
    ids=["nan-meter", "inf-baseline", "nan-coverage", "huge-baseline"],
)
def test_non_finite_or_huge_numbers_are_refused(mutate) -> None:
    # Before: NaN and infinity passed the schema and crashed or poisoned the geometry.
    raw = shared_fixture()
    mutate(raw)
    with pytest.raises(SceneError):
        parsed(raw)


# --- 12. reach bound ----------------------------------------------------------------------------


def test_long_wall_is_bounded_by_reach() -> None:
    # Before: every start along a 2000 ft wall was fully evaluated, far past the cable's reach.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-1000, 0], [1000, 0]]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-1000, 1000, 0, 30), "plus_minus_ft": 0}]
    raw["overheads"][0]["span_ft"] = [-1000, 1000]
    raw["facing"][0]["span_ft"] = [-1000, 1000]
    raw["coverage"] = everything_observed(-1000, 1000)
    started = time.perf_counter()
    result = run(raw)
    assert time.perf_counter() - started < 1.5
    assert result["decision"] == "pass"
    far = [
        r for r in result["sweep"] if r["outcome"] == "fail" and r["failing"] == ["route_length"]
    ]
    assert any(r["start_ft"][0] >= 20 for r in far)
    assert any(r["start_ft"][1] <= -20 for r in far)


# --- 13. route length rule citation -------------------------------------------------------------


def test_route_length_cites_both_lines() -> None:
    # Before: the rule named only the maximum, hiding that a pass is decided by the confident
    # reach. threshold_ft stays the maximum, the at_most line past which the run fails.
    rules = golden_rules(
        route={"confident_reach_ft": {"value": 15, "source": "reach test", "placeholder": True}}
    )
    reach = check(run(shared_fixture(), rules), "route_length")
    assert reach["threshold_ft"] == 20
    assert reach["review_threshold_ft"] == 15
    assert "reach test" in reach["rule"]["source"]
    assert reach["rule"]["placeholder"] is True


def test_route_length_cites_maximum_on_reject() -> None:
    raw = shared_fixture()
    raw["ground"] = pads_ground([(21, 24)])
    assert run(raw)["decision"] == "reject"
    # The result's nearest considered spot is a deck spot by the meter (it also fails only one
    # check, with a shorter route), so check the length-rejected start on the pad directly.
    reach = at_start(raw, 21.0, "route_length")
    assert (reach.outcome, reach.rule_key) == ("fail", "route.max_ft")


# --- 14. gas clearance needs the wall ------------------------------------------------------------


def test_gas_clearance_needs_the_wall_observed() -> None:
    # Before: gas clearance checked only the ground band, missing an unseen wall-mounted meter.
    rules = golden_rules(clearances={"gas_ft": {"value": 5, "source": "t"}})
    raw = shared_fixture()
    observed_band(raw, "wall", [(-40, 5), (12, 40)])
    result = run(raw, rules)
    assert result["decision"] == "manual_review"
    gas = check(result, "gas_clearance")
    assert (gas["outcome"], gas.get("unsure_cause")) == ("unsure", "unobserved")


# --- second review round ----------------------------------------------------------------------


def test_side_edge_reaches_an_object_standing_off_the_wall() -> None:
    # Before: starts only came from where a footprint corner's track crosses a clearance circle,
    # so a gas meter 0.9 ft off the wall (reached by a side edge) left the pass window unfound.
    rules = golden_rules(
        route={
            "confident_reach_ft": {"value": 30, "source": "t"},
            "max_ft": {"value": 40, "source": "t"},
        }
    )
    raw = shared_fixture()
    raw["ground"] = pads_ground([(8, 17)])
    for u in (14.005 - 3, 14.03 + W + 3):
        raw["objects"].append(
            {
                "type": "gas_meter",
                "wall_id": "w1",
                "span_ft": [u, u],
                "bottom_ft": 3,
                "top_ft": 4,
                "source": "tap",
                "plus_minus_ft": 0,
                "footprint": [[u, 0.9]],
            }
        )
    result = run(raw, rules)
    assert result["decision"] == "pass"
    assert 14.005 < result["spot"]["span_ft"][0] < 14.03


def test_curved_taps_are_not_merged_into_one_false_wall() -> None:
    # Before: dropped points were never rechecked, so a gentle curve became one straight wall
    # up to 1.6 ft off the taps.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[x, 0.004 * x * x] for x in range(-40, 41)]
    scene = parsed(raw)
    for p in scene.walls:
        for x in range(-40, 41):
            s, out = p.local((x, 0.004 * x * x))
            if p.s0 <= s <= p.s1:
                assert abs(out) <= 0.05 + 1e-9


def test_observed_ground_near_an_unexplored_end_still_counts() -> None:
    # Before: everything within reach of an unexplored end was unseen, even ground the scan saw
    # in front of the wall, so a fully observed pad asked for photos it already had.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [30, 0]]
    raw["coverage"]["ends"]["right"] = {"kind": "unexplored"}
    result = run(raw)
    assert result["decision"] == "pass"
    assert result["missing_evidence"] == []


def test_walls_meeting_in_a_straight_line_are_one_stretch() -> None:
    # Before: two collinear walls with different ids made a corner at the joint.
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-40, 0], [7.5, 0]], "plus_minus_ft": 0},
        {"id": "w2", "baseline": [[7.5, 0], [40, 0]], "plus_minus_ft": 0},
    ]
    result = run(raw)
    assert result["decision"] == "pass"
    assert result["spot"]["span_ft"] == pytest.approx([6, 6 + W])
    assert result["spot"]["wall_id"] in {"w1", "w2"}


def test_oversized_scene_is_refused() -> None:
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[x / 10, (x % 2) / 10] for x in range(-400, 400)]
    with pytest.raises(SceneError, match="is too long"):
        parsed(raw)


@pytest.mark.parametrize(
    ("start", "cause"), [(15.0, "margin"), (16.0, "rule_requires_review"), (20.0, "margin")]
)
def test_route_length_unsure_cause(start, cause) -> None:
    # On a line the run is too close to call; clearly between the lines the policy sends it to a
    # person, which more photos can't settle either.
    raw = shared_fixture()
    raw["ground"] = pads_ground([(start, start + 3)])
    reach = check(run(raw), "route_length")
    assert (reach["outcome"], reach["unsure_cause"]) == ("unsure", cause)


def test_sweep_runs_only_claim_evaluated_starts_on_one_piece() -> None:
    # Before (S4, case g03 at 44607e0): runs merged across pieces, so one pass run covered
    # 15 ft of corner-crossing starts between two pads on a wall bent into short chords.
    raw = shared_fixture()
    arc = [[x, -0.02 * x * x] for x in (-9, -6, -4, -2, 0, 2, 4, 6, 7, 10)]
    raw["walls"] = [{"id": "w1", "baseline": arc, "plus_minus_ft": 0}]
    raw["ground"] = pads_ground([(-9.5, -5.5), (6.5, 10.5)], lo=-12, hi=12)
    raw["coverage"] = everything_observed(-12, 12)
    raw["facing"], raw["overheads"] = [], []
    scene = parsed(raw)
    result = run(raw)
    for r in result["sweep"]:
        # The run's segment is the uploaded one; the piece it was evaluated on holds its start.
        a, b = r["start_ft"]
        piece = next(p for p in scene.walls if p.s0 - 1e-6 <= a < p.s1 - 1e-6)
        assert piece.s0 - 1e-6 <= a <= b <= max(piece.s1 - W, piece.s0) + 1e-6, r
        if r["outcome"] == "pass":
            for s0 in (a, (a + b) / 2, b):
                assert evaluate_start(scene, golden_rules(), s0).outcome == "pass", (r, s0)
