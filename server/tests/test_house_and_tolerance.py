"""Regression tests for the caretaker's findings at 5a4b4f4; each failed before its fix."""

import math

import pytest
from helpers import observed_band, rect, shared_fixture
from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import Point
from test_s4_round import PUBLIC, answer, parsed

from solver import PASS, evaluate_start

GROUND_CHECKS = {
    "ground_surface",
    "gas_clearance",
    "ac_clearance",
    "drive_clearance",
    "pool_clearance",
    "battery_clearance",
}


def without_ground(raw: dict) -> dict:
    raw["coverage"]["observed"] = [o for o in raw["coverage"]["observed"] if o["band"] != "ground"]
    for o in raw["coverage"]["observed"]:
        o["span_ft"] = [-100, 100]
    return raw


def back_wall(error: float) -> dict:
    """The caretaker's witness: w3 runs back parallel 2 ft behind w1."""
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-20, 0], [20, 0]], "height_ft": 9, "plus_minus_ft": 0},
        {"id": "w2", "baseline": [[20, 0], [20, -2]], "height_ft": 9, "plus_minus_ft": 0},
        {"id": "w3", "baseline": [[20, -2], [-20, -2]], "height_ft": 9, "plus_minus_ft": error},
    ]
    return without_ground(raw)


@pytest.mark.parametrize("error", [0.0, 4.0])
def test_yard_in_front_of_a_wall_is_not_house(error: float) -> None:
    # Before: w3's strip behind it reached across w1 into its yard, so (7, 3) counted as house.
    assert not parsed(back_wall(error), PUBLIC).house().covers(Point(7, 3))
    assert parsed(back_wall(error), PUBLIC).house().covers(Point(7, -1))  # between the facades


@pytest.mark.parametrize("error", [0.0, 4.0])
def test_a_spot_with_no_ground_seen_is_not_a_pass(error: float) -> None:
    candidate = evaluate_start(parsed(back_wall(error), PUBLIC), PUBLIC, 6.0)
    assert candidate.outcome != PASS
    assert answer(back_wall(error))["decision"] != "pass"


# --- the seen-ground tolerance -------------------------------------------------------------------


@pytest.mark.parametrize(("gap", "closes"), [(0.009, True), (0.011, False), (0.019, False)])
def test_only_gaps_within_the_tolerance_close(gap: float, closes: bool) -> None:
    # Before: growing seen ground by 0.005 each side and removing seams by another 0.005 closed
    # gaps up to 0.02 ft, twice the 0.01 ft tolerance.
    raw = shared_fixture()
    observed_band(raw, "ground", [(-40, 7), (7 + gap, 40)], out=30)
    unseen = parsed(raw, PUBLIC).unobserved_ground()
    assert unseen.intersects(Point(7 + gap / 2, 5)) != closes


# --- no ground seen, nothing that needs ground passes -------------------------------------------


@st.composite
def facades(draw: st.DrawFn) -> dict:
    """A facade with a corner, a wall running back parallel behind the first, or a gap between
    two walls (a side passage), at any error; no ground observations at all."""
    raw = shared_fixture()
    c = draw(st.floats(10.0, 25.0))
    angle = math.radians(draw(st.floats(-100.0, 100.0)))
    e1, e2 = draw(st.floats(0.0, 1.0)), draw(st.floats(0.0, 5.0))
    walls = [{"id": "w1", "baseline": [[-20, 0], [c, 0]], "height_ft": 9, "plus_minus_ft": e1}]
    layout = draw(st.sampled_from(["back", "corner", "gap"]))
    if layout == "gap":
        # Wider than the join tolerance (sweep.wall_join_ft, 0.6), so it stays a gap.
        g = c + draw(st.floats(1.0, 8.0))
        end = [g + 15 * math.cos(angle), 15 * math.sin(angle)]
        walls.append({"id": "w2", "baseline": [[g, 0], end], "height_ft": 9, "plus_minus_ft": e2})
    elif layout == "back":
        depth = draw(st.floats(0.5, 20.0))
        walls += [
            {"id": "w2", "baseline": [[c, 0], [c, -depth]], "height_ft": 9, "plus_minus_ft": e1},
            {
                "id": "w3",
                "baseline": [[c, -depth], [-20, -depth]],
                "height_ft": 9,
                "plus_minus_ft": e2,
            },
        ]
    else:
        end = [c + 15 * math.cos(angle), 15 * math.sin(angle)]
        walls.append({"id": "w2", "baseline": [[c, 0], end], "height_ft": 9, "plus_minus_ft": e2})
    raw["walls"] = walls
    raw["ground"] = [{"type": "lawn", "polygon": rect(-80, 80, -80, 80), "plus_minus_ft": 0}]
    return without_ground(raw)


@settings(max_examples=40, deadline=None)
@given(raw=facades(), starts=st.lists(st.floats(-15.0, 8.0), min_size=1, max_size=3))
def test_with_no_ground_seen_no_check_that_needs_it_passes(raw: dict, starts: list[float]) -> None:
    scene = parsed(raw, PUBLIC)
    for s0 in starts:
        candidate = evaluate_start(scene, PUBLIC, s0)
        passed = {c.id for c in candidate.checks if c.outcome == PASS} & GROUND_CHECKS
        assert not passed, (s0, passed)
