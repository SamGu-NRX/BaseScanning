"""Regression tests for the main-track review of #11 at 9edcd4b; each failed before its fix."""

import json
import math

from fastapi.testclient import TestClient
from helpers import at_start, observed_band, pads_ground, parsed, rect, shared_fixture
from hypothesis import given, settings
from hypothesis import strategies as st
from test_s4_round import PUBLIC, answer

import api
from rules import deep_merge, public_rules_dict, rules_from_dict
from solver import PASS, UNSURE, evaluate_start, solve

W = 31 / 12


# --- 1. wall equipment carries the battery's position error -----------------------------------


def test_equipment_within_the_walls_error_is_not_a_pass() -> None:
    # Before: a box 0.204 ft from the battery's edge passed, though the wall is known to ±0.5.
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.5
    raw["ground"] = pads_ground([(1, 30)])
    raw["objects"] = [
        {
            "type": "elec_box",
            "wall_id": "w1",
            "span_ft": [4.6, 5.6],
            "bottom_ft": 4,
            "top_ft": 5,
            "source": "tape",
            "plus_minus_ft": 0,
        }
    ]
    check = at_start(raw, 1.8125, "wall_equipment_above", PUBLIC)
    assert check.outcome == UNSURE
    assert check.plus_minus >= 0.5


# --- 2. a door that may or may not cross the route ---------------------------------------------


def door(span: list[float], wall_error: float = 0.0) -> dict:
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = wall_error
    raw["objects"] = [
        {
            "type": "door",
            "wall_id": "w1",
            "span_ft": span,
            "bottom_ft": 0,
            "top_ft": 7,
            "source": "tape",
            "plus_minus_ft": 0.5,
        }
    ]
    return raw


def test_a_door_that_may_reach_the_route_is_not_a_pass() -> None:
    # Before: its right edge at -0.4 ± 0.5 can reach past the meter at 0, yet the route passed.
    assert at_start(door([-3.4, -0.4]), 6.0, "route_path", PUBLIC).outcome == UNSURE


def test_a_door_that_may_miss_the_route_is_not_a_reject() -> None:
    # Before: its right edge at 0.1 ± 0.5 (wall ±0.5) can end left of the meter, yet every spot
    # right of the meter failed and the scene was rejected.
    raw = door([-2.9, 0.1], wall_error=0.5)
    assert at_start(raw, 6.0, "route_path", PUBLIC).outcome == UNSURE
    assert answer(raw)["decision"] != "reject"


def test_a_door_surely_across_the_route_still_fails() -> None:
    assert at_start(door([1.0, 4.0]), 6.0, "route_path", PUBLIC).outcome == "fail"


# --- 3. the battery clearance reads the wall too -----------------------------------------------


def six_ft_battery_rules():
    return rules_from_dict(
        deep_merge(
            public_rules_dict(),
            {"clearances": {"battery_ft": {"value": 6.0, "source": "test"}}},
        )
    )


def test_a_larger_battery_clearance_needs_the_wall_seen() -> None:
    # Before: only ground was required, so a wall stretch unseen within 6 ft passed.
    raw = shared_fixture()
    observed_band(raw, "wall", [(-40, 9.5), (13, 40)])
    check = at_start(raw, 6.0, "battery_clearance", six_ft_battery_rules())
    assert (check.outcome, check.unsure_cause) == (UNSURE, "unobserved")
    assert any(v.band == "wall" for v in check.all_missing())


def test_the_battery_check_is_left_out_only_when_the_gas_check_covers_it() -> None:
    ids = {c.id for c in evaluate_start(parsed(shared_fixture(), PUBLIC), PUBLIC, 6.0).checks}
    assert "battery_clearance" not in ids  # battery_ft 3 <= gas_ft 3: gas reads both bands
    loaded = six_ft_battery_rules()
    ids = {c.id for c in evaluate_start(parsed(shared_fixture(), loaded), loaded, 6.0).checks}
    assert "battery_clearance" in ids


# --- 4. start positions where the error grows with drift ---------------------------------------


def drift_window(lo: float = 8.005, hi: float = 8.030, z: float = 0.9) -> tuple[dict, float, float]:
    """Two exact gas-meter points placed so a start passes only inside (lo, hi): each is 3 ft
    plus the battery's error at that end away from the battery's nearest part. The error grows
    0.16 ft per foot from the meter (default wall error). `z` is how far out from the wall the
    points stand; past the battery's depth the nearest part of it is a front corner."""

    def e(s: float) -> float:
        return 0.3 + 0.16 * (s + W)

    def along(s: float) -> float:
        out = max(0.0, z - 22 / 12)
        return math.sqrt((3 + e(s)) ** 2 - out**2)

    raw = shared_fixture()
    del raw["walls"][0]["plus_minus_ft"]  # default error with drift
    raw["ground"] = pads_ground([(lo - e(lo) - 0.01, hi + W + e(hi) + 0.01)])
    raw["objects"] = [
        {
            "type": "gas_meter",
            "wall_id": "w1",
            "span_ft": [x, x],
            "bottom_ft": 0,
            "top_ft": 0.1,
            "source": "tape",
            "plus_minus_ft": 0,
            "footprint": [[x, z]],
        }
        for x in (lo - along(lo), hi + W + along(hi))
    ]
    return raw, lo, hi


def test_the_narrow_interval_is_found_with_the_meters_in_front() -> None:
    # The caretaker's follow-up: the same interval with the gas points 1 ft in front of the
    # battery, so the nearest part is a front corner and the distance runs diagonally.
    raw, lo, hi = drift_window(z=22 / 12 + 1)
    assert evaluate_start(parsed(raw, PUBLIC), PUBLIC, (lo + hi) / 2).outcome == PASS
    assert solve(parsed(raw, PUBLIC), PUBLIC)["stats"]["pass"] > 0


@settings(max_examples=25, deadline=None)
@given(
    lo=st.floats(min_value=3.0, max_value=12.0),
    width=st.floats(min_value=0.004, max_value=0.2),
    z=st.floats(min_value=0.1, max_value=4.0),
)
def test_a_start_that_passes_is_found_by_the_solve(lo: float, width: float, z: float) -> None:
    raw, lo, hi = drift_window(lo, lo + width, z)
    scene = parsed(raw, PUBLIC)
    if evaluate_start(scene, PUBLIC, (lo + hi) / 2).outcome == PASS:
        assert solve(scene, PUBLIC)["stats"]["pass"] > 0


def test_a_passing_interval_narrower_than_the_grid_is_found() -> None:
    # Before: the start positions used the segment's largest error, so the passing interval
    # (8.005, 8.030) had no start in it and the scene went to manual review.
    raw, lo, hi = drift_window()
    assert evaluate_start(parsed(raw, PUBLIC), PUBLIC, (lo + hi) / 2).outcome == PASS
    result = solve(parsed(raw, PUBLIC), PUBLIC)
    assert result["stats"]["pass"] > 0


# --- 5. the site plan draws an existing battery ------------------------------------------------


def test_the_site_plan_draws_an_existing_battery() -> None:
    raw = shared_fixture()
    raw["objects"] = [
        {
            "wall_id": "w1",
            "type": "battery",
            "span_ft": [12, 14.6],
            "source": "tape",
            "plus_minus_ft": 0.05,
            "footprint": rect(12, 14.6, 0, 1.83),
        }
    ]
    resp = TestClient(api.app).post(
        "/v1/placements/site-plan.svg",
        content=json.dumps(raw),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert "Battery" in resp.text


# --- 7. an exact-end span is labelled with its own side ----------------------------------------


def test_a_span_starting_at_the_right_end_is_labelled_right() -> None:
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [12, 0]]
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [-40, 12]
    raw["coverage"]["ends"]["right"] = {"kind": "unexplored"}
    raw["objects"] = [
        {
            "type": "window",
            "wall_id": "w1",
            "span_ft": [12, 20],
            "bottom_ft": 3,
            "top_ft": 7,
            "source": "tap",
            "plus_minus_ft": 0.1,
        }
    ]
    assert [side for _, _, side, _ in parsed(raw, PUBLIC).set_aside] == ["right"]
