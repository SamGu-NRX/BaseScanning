"""Regression tests for the Codex review of #11 at 9176125, and the scoreboard's S2-3 properties."""

import copy

from helpers import at_start, parsed, rect, shared_fixture
from hypothesis import given, settings
from hypothesis import strategies as st
from test_s4_round import PUBLIC, answer

from solver import FAIL, PASS, UNSURE, at_least, evaluate_start

W = 31 / 12


# --- 4114314891: a spot within the wall's error of the wall's end ------------------------------


def wall_ending_at_9() -> dict:
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [9, 0]]
    raw["walls"][0]["plus_minus_ft"] = 0.5
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [-40, 9]
    raw["coverage"]["ends"]["right"] = {"kind": "limit"}
    return raw


def test_a_spot_within_error_of_the_wall_end_is_not_a_pass() -> None:
    # Before: [6.1, 8.683] passed although its edge may be at 9.18, past the wall's end at 9.
    backing = at_start(wall_ending_at_9(), 6.1, "wall_backing", PUBLIC)
    assert (backing.outcome, backing.unsure_cause) == (UNSURE, "margin")
    assert answer(wall_ending_at_9())["decision"] != "pass"


def test_a_spot_clear_of_the_end_by_its_error_still_passes_backing() -> None:
    assert at_start(wall_ending_at_9(), 9 - W - 0.6, "wall_backing", PUBLIC).outcome == PASS


# --- 4114314918: the meter's offset from the wall ------------------------------------------------


def meter_off_the_wall() -> dict:
    raw = shared_fixture()
    raw["meter"]["pos"] = [0.0, 5.0, 2.0]  # 2 ft in front of the wall at z = 0
    raw["ground"] = [
        {"type": "lawn", "polygon": rect(14, 17, 0, 30), "plus_minus_ft": 0},
        {"type": "deck", "polygon": rect(-40, 14, 0, 30), "plus_minus_ft": 0},
        {"type": "deck", "polygon": rect(17, 40, 0, 30), "plus_minus_ft": 0},
    ]
    return raw


def test_the_route_starts_at_the_meter_not_its_projection() -> None:
    # Before: 14 ± 0 from [0, 0], a pass; the real run from the meter is 16, past 15 confident.
    reach = at_start(meter_off_the_wall(), 14.0, "route_length", PUBLIC)
    assert reach.measured == 16.0
    assert reach.outcome == UNSURE
    result = answer(meter_off_the_wall())
    assert result["decision"] != "pass"
    assert result["route"] is None or result["route"]["polyline"][0] == [0.0, 2.0]


# --- scoreboard S2-3 -----------------------------------------------------------------------------


@given(threshold=st.floats(0.1, 20), error=st.floats(0, 5))
def test_equality_is_unsure(threshold: float, error: float) -> None:
    # A margin exactly equal to the error decides nothing, either way.
    assert at_least(threshold + error, error, threshold) == UNSURE
    assert at_least(threshold - error, error, threshold) == UNSURE


RANK = {PASS: 0, UNSURE: 1, FAIL: 2}


@settings(max_examples=40, deadline=None)
@given(near=st.floats(0.0, 6.0), extra=st.floats(0.0, 4.0), error=st.floats(0.0, 0.5))
def test_moving_an_obstacle_away_never_turns_pass_into_fail(
    near: float, extra: float, error: float
) -> None:
    """Monotonicity over one obstacle distance: an AC unit further from the battery never makes
    the AC check, or the spot, worse."""

    def spot_with_ac(gap: float):
        raw = shared_fixture()
        x = 6.0 + W + gap
        raw["objects"] = [
            {
                "type": "ac",
                "wall_id": "w1",
                "span_ft": [x, x + 2],
                "source": "tape",
                "plus_minus_ft": error,
                "footprint": rect(x, x + 2, 0, 2),
            }
        ]
        return evaluate_start(parsed(copy.deepcopy(raw), PUBLIC), PUBLIC, 6.0)

    closer, further = spot_with_ac(near), spot_with_ac(near + extra)
    ac = {c.id: c.outcome for c in closer.checks}["ac_clearance"]
    ac_far = {c.id: c.outcome for c in further.checks}["ac_clearance"]
    assert RANK[ac_far] <= RANK[ac]
    assert RANK[further.outcome] <= RANK[closer.outcome]


def test_the_wall_behind_every_possible_position_must_be_seen() -> None:
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.5
    raw["coverage"]["observed"] = [o for o in raw["coverage"]["observed"] if o["band"] != "wall"]
    raw["coverage"]["observed"].append({"band": "wall", "span_ft": [-40, 6 + W]})
    backing = at_start(raw, 6.0, "wall_backing", PUBLIC)
    assert (backing.outcome, backing.unsure_cause) == (UNSURE, "unobserved")
