"""Scoreboard S2-3: more space never makes an answer worse.

The monotone statement the rules support is per check, at a fixed battery start (the chosen spot
itself may move, since another start can improve too). Rank PASS < UNSURE < FAIL. With
everything else fixed and the same start evaluated:

1. Moving one ground-standing obstacle (a gas meter, AC unit or pool with a plan footprint)
   straight out from the wall the battery backs onto, which can only increase its plan distance
   from the footprint, never raises any check's rank, nor the start's overall outcome.
2. Adding one more observed span to any band never raises any check's rank, nor the start's
   overall outcome.

And the strict rule holds on both sides: a check that PASSes with a measurement clears its
threshold by more than its error, so a FAIL can only become a PASS once its margin exceeds its
error.
"""

import copy

from helpers import parsed
from hypothesis import given, settings
from hypothesis import strategies as st
from test_final_review import cornered
from test_s4_round import PUBLIC

from solver import EPS, PASS, evaluate_start

RANK = {"pass": 0, "unsure": 1, "fail": 2}


def outcomes(raw: dict, s0: float) -> tuple[dict, str, list]:
    candidate = evaluate_start(parsed(copy.deepcopy(raw), PUBLIC), PUBLIC, s0, "w1")
    return {c.id: c.outcome for c in candidate.checks}, candidate.outcome, candidate.checks


def strict_rule_holds(checks: list) -> None:
    for c in checks:
        if c.outcome != PASS or c.measured is None or c.threshold is None:
            continue
        error = c.plus_minus or 0.0
        if c.comparison == "at_least":
            assert c.measured - error - c.threshold > -EPS, c
        elif c.comparison == "at_most":
            assert c.threshold - (c.measured + error) > -EPS, c


def never_worse(before: tuple, after: tuple) -> None:
    by_check, overall, checks = before
    by_check_after, overall_after, checks_after = after
    for check_id, outcome in by_check.items():
        assert RANK[by_check_after[check_id]] <= RANK[outcome], (check_id, outcome, by_check_after)
    assert RANK[overall_after] <= RANK[overall], (overall, overall_after)
    strict_rule_holds(checks)
    strict_rule_holds(checks_after)


def first_wall_end(raw: dict) -> float:
    return raw["walls"][0]["baseline"][1][0]


@settings(max_examples=40, deadline=None)
@given(raw=cornered(), data=st.data())
def test_moving_an_obstacle_away_never_makes_a_check_worse(raw: dict, data) -> None:
    end = first_wall_end(raw)
    s0 = data.draw(st.floats(-25.0, end - 3.5), label="s0")
    kind = data.draw(st.sampled_from(["gas_meter", "ac", "pool"]), label="kind")
    x = data.draw(st.floats(-28.0, end), label="x")
    z = data.draw(st.floats(0.0, 6.0), label="z")
    farther = data.draw(st.floats(0.05, 8.0), label="farther")
    error = data.draw(st.floats(0.0, 0.5), label="error")

    def with_obstacle(out: float) -> dict:
        scene = copy.deepcopy(raw)
        scene["objects"] = [
            {
                "type": kind,
                "wall_id": "w1",
                "span_ft": [x, x + 1],
                "bottom_ft": 0,
                "top_ft": 1,
                "source": "tape",
                "plus_minus_ft": error,
                "footprint": [[x, out], [x + 1, out], [x + 1, out + 1], [x, out + 1]],
            }
        ]
        return scene

    never_worse(outcomes(with_obstacle(z), s0), outcomes(with_obstacle(z + farther), s0))


@settings(max_examples=40, deadline=None)
@given(raw=cornered(), data=st.data())
def test_seeing_more_never_makes_a_check_worse(raw: dict, data) -> None:
    end = first_wall_end(raw)
    s0 = data.draw(st.floats(-25.0, end - 3.5), label="s0")
    band = data.draw(st.sampled_from(["wall", "ground", "overhead", "facing"]), label="band")
    a = data.draw(st.floats(-35.0, end + 20), label="a")
    b = data.draw(st.floats(a, end + 25), label="b")
    item = {"band": band, "span_ft": [a, b]}
    if band == "ground" or data.draw(st.booleans(), label="depth"):
        item["out_ft"] = data.draw(st.floats(0.5, 20.0), label="out")
    more = copy.deepcopy(raw)
    more["coverage"]["observed"].append(item)
    never_worse(outcomes(raw, s0), outcomes(more, s0))
