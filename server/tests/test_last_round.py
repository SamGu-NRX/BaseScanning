"""Regression tests for the caretaker's three findings at 4a9fa44; each failed before its fix."""

import pytest
from helpers import at_start, observed_band, pads_ground, shared_fixture
from test_s4_round import PUBLIC, answer

from rules import deep_merge, public_rules_dict, rules_from_dict
from solver import PASS, UNSURE

# --- 4114443089: every wall the cable runs along carries its error ------------------------------


def three_walls(corner_x: float) -> dict:
    """w1 and w2 known to ±1 ft, the candidate wall w3 exact; their shared corner at x."""
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = 0
    raw["walls"] = [
        {"id": "w1", "baseline": [[-4, 0], [corner_x, 0]], "height_ft": 9, "plus_minus_ft": 1},
        {"id": "w2", "baseline": [[corner_x, 0], [4, -4]], "height_ft": 9, "plus_minus_ft": 1},
        {"id": "w3", "baseline": [[4, -4], [20, -4]], "height_ft": 9, "plus_minus_ft": 0},
    ]
    ground = pads_ground([(9.5, 12.5)])
    for g in ground:
        g["polygon"] = [[x, z - 4 if z == 0 else z] for x, z in g["polygon"]]
    raw["ground"] = ground
    raw["overheads"], raw["facing"] = [], []
    for band in ("wall", "ground", "overhead", "facing"):
        observed_band(raw, band, [(-40, 60)])
    return raw


def test_a_route_near_the_reach_line_through_uncertain_walls_is_not_a_pass() -> None:
    # Before: 14.5 ± 0 passed; the same battery with the shared corner moved 1 ft (within the
    # walls' ±1) is a 15.6 ft run and goes to review.
    result = answer(three_walls(4.0))
    reach = next(c for c in result["checks"] if c["id"] == "route_length")
    assert reach["outcome"] != PASS, reach
    assert reach["plus_minus_ft"] >= 2


def test_moving_the_corner_within_its_error_does_not_flip_the_decision() -> None:
    assert answer(three_walls(4.0))["decision"] == answer(three_walls(5.0))["decision"]


# --- 4114443091: unseen wall where the route may run ---------------------------------------------


def short_wall(door: bool) -> dict:
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-4, 0], [20, 0]]
    raw["meter"]["plus_minus_ft"] = 0.3
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [-4, 20]
    observed_band(raw, "wall", [(0, 40)])
    if door:
        raw["objects"] = [
            {
                "type": "door",
                "wall_id": "w1",
                "span_ft": [-0.3, -0.1],
                "bottom_ft": 0,
                "top_ft": 7,
                "source": "tape",
                "plus_minus_ft": 0,
            }
        ]
    return raw


def test_unseen_wall_within_the_meters_error_leaves_the_route_unsure() -> None:
    # Before: the route passed while the wall at s -0.3 to 0, where the meter may be, was unseen;
    # recording a door there made it UNSURE.
    route = at_start(short_wall(door=False), 6.0, "route_path", PUBLIC)
    assert (route.outcome, route.unsure_cause) == (UNSURE, "unobserved")
    assert at_start(short_wall(door=True), 6.0, "route_path", PUBLIC).outcome == UNSURE


# --- 4114443100: a window exemption above the height the opening check requires -----------------


def test_an_exemption_above_headroom_is_refused_when_the_rules_load() -> None:
    data = deep_merge(public_rules_dict(), {"openings": {"exempt_bottom_above_ft": 10.0}})
    with pytest.raises(ValueError, match=r"openings\.exempt_bottom_above_ft"):
        rules_from_dict(data)


def test_an_exemption_at_or_below_headroom_loads() -> None:
    data = deep_merge(public_rules_dict(), {"openings": {"exempt_bottom_above_ft": 6.5}})
    assert rules_from_dict(data).rules.openings.exempt_bottom_above_ft == 6.5
