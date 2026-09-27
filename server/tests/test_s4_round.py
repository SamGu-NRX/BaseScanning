"""Regression tests for S4's end-to-end run at 739fb6f; each failed before its fix."""

import copy

import pytest
from helpers import at_start, observed_band, parsed, shared_fixture
from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import Point

import solver
from rules import LoadedRules, public_rules_dict, rules_from_dict
from solver import FAIL, PASS, UNSURE, at_least, evaluate_start, solve

PUBLIC = rules_from_dict(public_rules_dict())
# Ground captured in answer to a request is seen this far out: past every clearance's reach.
FAR_FT = 40.0


def answer(raw: dict, rules: LoadedRules = PUBLIC) -> dict:
    return solve(parsed(raw, rules), rules)


def captured(raw: dict, result: dict) -> dict:
    """The scene after the homeowner shows exactly the band views the answer asked for."""
    out = copy.deepcopy(raw)
    observed = out.setdefault("coverage", {}).setdefault("observed", [])
    for item in result["missing_evidence"]:
        if item["kind"] == "band":
            # Exactly what the request names, depth included.
            depth = {"out_ft": item["out_ft"]} if "out_ft" in item else {}
            observed.append({"band": item["band"], "span_ft": item["span_ft"], **depth})
    return out


def repeated_requests(raw: dict, result: dict) -> list[dict]:
    """Band requests the scene already satisfies: the same request coming back."""
    seen: dict[str, list[tuple[float, float, float]]] = {}
    for o in raw.get("coverage", {}).get("observed", []):
        seen.setdefault(o["band"], []).append((*o["span_ft"], o.get("out_ft", float("inf"))))
    return [
        item
        for item in result["missing_evidence"]
        if item["kind"] == "band"
        and any(
            a <= item["span_ft"][0] and item["span_ft"][1] <= b and out >= item.get("out_ft", 0)
            for a, b, out in seen.get(item["band"], [])
        )
    ]


# --- 1. every request can be satisfied by what it asks for --------------------------------------


def no_coverage() -> dict:
    """S4 case c5-no-coverage: a 16 ft wall, nothing observed, both ends unexplored."""
    return {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0},
        "walls": [{"id": "w1", "baseline": [[-6, 0.0], [10, 0.0]], "plus_minus_ft": 0.0}],
        "objects": [],
        "ground": [
            {
                "type": "concrete",
                "polygon": [[-6, 0], [10, 0], [10, 10], [-6, 10]],
                "plus_minus_ft": 0,
            }
        ],
        "facing": [{"wall_id": "w1", "span_ft": [-6.0, 10.0], "depth_ft": 9.0, "plus_minus_ft": 0}],
    }


def test_requests_past_an_unexplored_end_do_not_come_back() -> None:
    # Before: ground [-13.9, -6] and wall [-6.9, -6], past the unexplored left end, were asked
    # for again after being shown; only walking past the end (a past_end request) settles them.
    raw = no_coverage()
    for _ in range(3):
        result = answer(raw)
        assert repeated_requests(raw, result) == []
        raw = captured(raw, result)
    assert any(m["kind"] == "past_end" for m in result["missing_evidence"])


@st.composite
def scenes_with_ends(draw: st.DrawFn) -> dict:
    """The shared wall shortened at random, each end limit or unexplored, coverage with gaps."""
    raw = shared_fixture()
    left = draw(st.floats(min_value=-12, max_value=-0.5))
    right = draw(st.floats(min_value=12, max_value=25))
    raw["walls"][0]["baseline"] = [[left, 0], [right, 0]]
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [left, right]
    raw["coverage"]["ends"] = {
        side: {"kind": draw(st.sampled_from(["limit", "unexplored"]))} for side in ("left", "right")
    }
    for band in ("wall", "ground", "overhead", "facing"):
        cuts = draw(st.lists(st.floats(min_value=left, max_value=right), max_size=4))
        edges = [left, *sorted(cuts[: len(cuts) // 2 * 2]), right]
        spans = [(a, b) for a, b in zip(edges[::2], edges[1::2], strict=True) if b > a]
        observed_band(raw, band, spans or [(left, left + 0.1)], draw(st.floats(1, 30)))
        if band in ("facing", "overhead"):
            # Seen clear only so far (a walked path, a tilt-up frame), or all the way.
            depth = draw(st.none() | st.floats(1, 12))
            for o in raw["coverage"]["observed"]:
                if o["band"] == band and depth is not None:
                    o["out_ft"] = depth
    return raw


@settings(max_examples=40, deadline=None)
@given(raw=scenes_with_ends())
def test_a_captured_request_never_comes_back(raw: dict) -> None:
    for _ in range(3):
        result = answer(raw)
        assert repeated_requests(raw, result) == [], result["missing_evidence"]
        raw = captured(raw, result)


def request_past_a_limit_end() -> dict:
    """One wall from s = -0.5 to 12, a limit at the left end and an unexplored right end whose
    disc (radius reach, about 12.8 ft) reaches past the left end."""
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-0.5, 0], [12.0, 0]]
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [-0.5, 12.0]
    raw["coverage"]["ends"] = {"left": {"kind": "limit"}, "right": {"kind": "unexplored"}}
    for band in ("wall", "ground", "overhead", "facing"):
        observed_band(raw, band, [(-0.5, 12.0)], 1.0)
    return raw


def test_a_request_past_a_limit_end_is_settled_by_capturing_it() -> None:
    # Before: the first answer asked for ground from s = -4.01, past the left limit end; after
    # that capture [-0.833, -0.5] 2.85 ft out (pool_clearance) was asked for on every round.
    # That ground is in the right end's disc, where ground seen past the limit end didn't count.
    raw = request_past_a_limit_end()
    for _ in range(3):
        result = answer(raw)
        assert repeated_requests(raw, result) == [], result["missing_evidence"]
        raw = captured(raw, result)


def test_ground_seen_past_a_limit_end_counts_in_an_unexplored_ends_disc() -> None:
    # Inside the right end's disc, past the left limit end: seen once a view there reaches it.
    raw = request_past_a_limit_end()
    probe = Point(-0.7, 1.5)
    assert parsed(raw, PUBLIC).unobserved_ground().contains(probe)
    raw["coverage"]["observed"].append({"band": "ground", "span_ft": [-1.0, -0.5], "out_ft": 2.0})
    assert not parsed(raw, PUBLIC).unobserved_ground().contains(probe)


def test_ground_seen_past_an_unexplored_end_still_counts_for_nothing() -> None:
    # Past the unexplored right end the extended line is a guess, so a view along it shows
    # nothing about where the walls went.
    raw = request_past_a_limit_end()
    probe = Point(13.0, 1.5)
    raw["coverage"]["observed"].append({"band": "ground", "span_ft": [12.0, 15.0], "out_ft": 5.0})
    assert parsed(raw, PUBLIC).unobserved_ground().contains(probe)


# --- 2. ground past a limit end is not clear until it is seen ------------------------------------


def limit_end_near_the_spot() -> dict:
    """A limit end at s = -1, 7 ft left of the only spot: the pool clearance reaches past it."""
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-1, 0], [40, 0]]
    raw["overheads"][0]["span_ft"] = raw["facing"][0]["span_ft"] = [-1, 40]
    for band in ("wall", "ground", "overhead", "facing"):
        observed_band(raw, band, [(-1, 40)])
    return raw


def test_pool_clearance_needs_the_ground_past_a_limit_end() -> None:
    # Before: past a limit end the ground was never required, so the pool check passed with
    # nothing seen beyond the fence.
    raw = limit_end_near_the_spot()
    result = answer(raw)
    pool = next(c for c in result["checks"] if c["id"] == "pool_clearance")
    assert (pool["outcome"], pool["unsure_cause"]) == (UNSURE, "unobserved")
    ground = next(m for m in result["missing_evidence"] if m.get("band") == "ground")
    assert ground["span_ft"][0] < -1  # past the end, shown by pointing the camera there


def test_showing_the_ground_past_a_limit_end_settles_it() -> None:
    raw = limit_end_near_the_spot()
    after = answer(captured(raw, answer(raw)))
    pool = next(c for c in after["checks"] if c["id"] == "pool_clearance")
    assert pool["outcome"] == PASS


def test_ground_behind_the_line_past_a_limit_end_counts() -> None:
    # Ground seen only in front of the continued wall line leaves the other side unseen.
    raw = limit_end_near_the_spot()
    observed_band(raw, "ground", [(-20, 40)], out=FAR_FT)
    s0 = answer(raw)["spot"]["span_ft"][0]
    pool = at_start(raw, s0, "pool_clearance", PUBLIC)
    assert pool.outcome == PASS  # a span past the end covers both sides of the line


# --- 3. the reported error is the one the decision used -----------------------------------------


def uncertain_obstruction() -> dict:
    """An obstruction in front of the wall ends 0.3 ft before the battery, and the wall is only
    known to ± 0.6 ft, so it may or may not be in front of the battery."""
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.6
    raw["facing"] = [
        {"wall_id": "w1", "span_ft": [-40, 5.7], "depth_ft": 9, "plus_minus_ft": 0.1},
        {"wall_id": "w1", "span_ft": [5.7, 6.7], "depth_ft": 1.0, "plus_minus_ft": 0.1},
        {"wall_id": "w1", "span_ft": [6.7, 40], "depth_ft": 9, "plus_minus_ft": 0.1},
    ]
    return raw


def test_a_band_check_reports_numbers_that_give_its_outcome() -> None:
    # Before: "-0 ft 10 in (± 0 ft 1 in) against a 3 ft rule: too close to call", which the
    # C5 rule makes a clear fail; the position error that made it UNSURE went unreported.
    gap = evaluate_start(parsed(uncertain_obstruction(), PUBLIC), PUBLIC, 7.0)
    facing = next(c for c in gap.checks if c.id == "facing_gap")
    assert facing.outcome == UNSURE
    assert at_least(facing.measured, facing.plus_minus, facing.threshold) == UNSURE
    assert "facing[1]" in facing.reason


@settings(max_examples=40, deadline=None)
@given(s0=st.floats(min_value=4.0, max_value=9.0))
def test_band_check_numbers_agree_with_c5(s0: float) -> None:
    candidate = evaluate_start(parsed(uncertain_obstruction(), PUBLIC), PUBLIC, s0)
    for c in candidate.checks:
        decided_by_numbers = c.outcome in (PASS, FAIL) or c.unsure_cause == "margin"
        if c.id in ("facing_gap", "headroom") and c.measured is not None and decided_by_numbers:
            assert at_least(c.measured, c.plus_minus, c.threshold) == c.outcome, c


# --- 4. a scene too slow to place is refused early ------------------------------------------------


def test_a_scene_that_would_overrun_is_refused_early(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before: the solver spent the whole budget before refusing. Now the pace after the first
    # PROJECT_AFTER positions decides. A clock that advances 1 ms per reading makes the pace
    # exact: the shared wall's positions project past a budget not yet spent at that point.
    scene = parsed(shared_fixture(), PUBLIC)
    positions = sum(len(solver.Solver(scene, PUBLIC).starts(p)) for p in scene.walls)
    readings = iter(range(10**6))

    class Clock:
        @staticmethod
        def perf_counter() -> float:
            return next(readings) / 1000

    monkeypatch.setattr(solver, "time", Clock)
    budget = (solver.PROJECT_AFTER + positions) / 2 / 1000
    with pytest.raises(solver.SceneTooComplex, match="would take about"):
        solver.solve(scene, PUBLIC, budget_s=budget)
    assert next(readings) < solver.PROJECT_AFTER + 10


def test_unseen_ground_under_an_exact_footprint_is_not_clear() -> None:
    # The coverage test at radius 0 (a wall with no error) must still see unseen ground under
    # the footprint itself; a distance of 0 there is overlap, not touching.
    raw = shared_fixture()
    observed_band(raw, "ground", [(-40, 5), (10, 40)])
    ground = at_start(raw, 6.0, "ground_surface", PUBLIC)
    assert (ground.outcome, ground.unsure_cause) == (UNSURE, "unobserved")


def test_ground_seen_right_up_to_the_footprint_is_clear() -> None:
    raw = shared_fixture()
    observed_band(raw, "ground", [(-40, 6), (6, 40)])
    assert at_start(raw, 6.0, "ground_surface", PUBLIC).outcome == PASS
