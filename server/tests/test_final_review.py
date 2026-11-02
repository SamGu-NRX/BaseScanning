"""Regression tests for the Codex review of #11 at 9176125, and the scoreboard's S2-3 properties."""

import copy
import math

from helpers import at_start, everything_observed, pads_ground, parsed, rect, shared_fixture
from hypothesis import example, given, settings
from hypothesis import strategies as st
from test_s4_round import PUBLIC, answer

from solver import FAIL, PASS, SOLVE_BUDGET_S, UNSURE, Solver, at_least, evaluate_start

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


# --- S4 on 4a9fa44: a request is a fixed point ---------------------------------------------------


def supplied_exactly(raw: dict, result: dict) -> dict:
    out = copy.deepcopy(raw)
    for item in result["missing_evidence"]:
        if item["kind"] == "band":
            depth = {"out_ft": item["out_ft"]} if "out_ft" in item else {}
            out["coverage"]["observed"].append(
                {"band": item["band"], "span_ft": item["span_ft"], **depth}
            )
    return out


def unseen_at(raw: dict, s0: float, wall_id: str) -> list[str]:
    candidate = evaluate_start(parsed(raw, PUBLIC), PUBLIC, s0, wall_id)
    return [c.id for c in candidate.checks if c.unsure_cause == "unobserved"]


def exact_start(raw: dict, result: dict) -> float:
    scene = parsed(raw, PUBLIC)
    target = result["spot"]["span_ft"][0]
    return min(
        (c.s0 for c in Solver(scene, PUBLIC).candidates(SOLVE_BUDGET_S)),
        key=lambda s: abs(s - target),
    )


@st.composite
def cornered(draw: st.DrawFn) -> dict:
    """A wall that turns at x = c by `angle` degrees (positive: an inside corner, turning
    toward the yard), with gaps in every band's coverage and random view depths."""
    c = draw(st.floats(4.0, 14.0))
    angle = math.radians(draw(st.floats(-80.0, 80.0)))
    end = [c + 20 * math.cos(angle), 20 * math.sin(angle)]
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-30, 0], [c, 0]], "height_ft": 9, "plus_minus_ft": 0.1},
        {"id": "w2", "baseline": [[c, 0], end], "height_ft": 9, "plus_minus_ft": 0.1},
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-60, 60, -60, 60), "plus_minus_ft": 0}]
    raw["overheads"], raw["facing"] = [], []
    raw["coverage"] = {
        "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
        "observed": [],
    }
    for band in ("wall", "ground", "overhead", "facing"):
        cuts = draw(st.lists(st.floats(-10.0, c + 20.0), max_size=4))
        edges = [-30.0, *sorted(cuts[: len(cuts) // 2 * 2]), c + 20.0]
        depth = draw(st.floats(1.0, 12.0))
        for a, b in zip(edges[::2], edges[1::2], strict=True):
            if b > a:
                item = {"band": band, "span_ft": [a, b]}
                if band == "ground" or draw(st.booleans()):
                    item["out_ft"] = depth
                raw["coverage"]["observed"].append(item)
    return raw


def corner_108() -> dict:
    """The example the final review saved (finding 6): a 108 degree corner, walls +/- 0.1 ft,
    ground seen 1 ft out, overheads unseen over s [-2, 0]."""
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-30, 0], [6.1875, 0]], "height_ft": 9, "plus_minus_ft": 0.1},
        {
            "id": "w2",
            "baseline": [[6.1875, 0], [12.367839887498949, 19.02113032590307]],
            "height_ft": 9,
            "plus_minus_ft": 0.1,
        },
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-60, 60, -60, 60), "plus_minus_ft": 0}]
    raw["overheads"], raw["facing"] = [], []
    raw["coverage"] = {
        "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
        "observed": [
            {"band": "wall", "span_ft": [-30.0, 26.1875]},
            {"band": "ground", "span_ft": [-30.0, 26.1875], "out_ft": 1.0},
            {"band": "overhead", "span_ft": [-30.0, -2.0]},
            {"band": "overhead", "span_ft": [0.0, 26.1875]},
            {"band": "facing", "span_ft": [-30.0, 26.1875]},
        ],
    }
    return raw


# The saved example runs on every run, CI's fixed examples included.
@settings(max_examples=30, deadline=None)
@given(raw=cornered())
@example(raw=corner_108())
def test_capturing_exactly_what_is_requested_settles_it_in_one_round(raw: dict) -> None:
    # Before: near a corner the ground depth was the distance from the chain line, less than
    # the strip in front of the wall needs, so each capture fell short and the next answer
    # asked for 0.005 ft more over the same stretch.
    result = answer(raw)
    if result["decision"] != "manual_review" or result["spot"] is None:
        return
    assert unseen_without_a_reason(result) == []
    s0 = exact_start(raw, result)
    assert unseen_at(supplied_exactly(raw, result), s0, result["spot"]["wall_id"]) == []


def unseen_without_a_reason(result: dict) -> list[str]:
    """Unseen checks at the spot that the answer's unobserved_area reason doesn't name: the
    result contract has every UNSURE check carry a request or a reason."""
    named = {i for r in result["reasons"] if r["code"] == "unobserved_area" for i in r["checks"]}
    return [
        c["id"]
        for c in result["checks"]
        if c.get("unsure_cause") == "unobserved" and c["id"] not in named
    ]


def test_a_request_leaves_no_lens_where_a_clearance_circle_meets_a_view() -> None:
    # Before: the ground request's depth was accepted once under 1e-9 sq ft stayed unseen, and a
    # 9.5e-10 sq ft lens where the pool circle meets the view's edge, 1.7e-5 ft inside the
    # radius, left pool_clearance unseen after the exact capture.
    raw = corner_108()
    result = answer(raw)
    s0 = exact_start(raw, result)
    assert unseen_at(supplied_exactly(raw, result), s0, result["spot"]["wall_id"]) == []


def test_ground_past_an_end_beyond_reach_is_asked_for() -> None:
    # The manager's ETH3D case, rebuilt: the pool circle at the only allowed pad reaches past an
    # unexplored end that no spot past it could reach. Before, pool_clearance was UNSURE with no
    # request and no reason (only walking past the end shows that ground, and no past_end
    # request was made for an end beyond reach).
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-20, 0], [30, 0]], "height_ft": 9, "plus_minus_ft": 0.0}
    ]
    raw["ground"] = pads_ground([(20.0, 23.0)], -40, 60)
    raw["overheads"], raw["facing"] = [], []
    raw["coverage"] = everything_observed(-20, 30)
    raw["coverage"]["ends"]["right"] = {"kind": "unexplored"}
    result = answer(raw)
    assert unseen_without_a_reason(result) == []
    walk = [m for m in result["missing_evidence"] if m["kind"] == "past_end"]
    assert [m["side"] for m in walk] == ["right"]
