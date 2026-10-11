"""Findings of the final review of 3baa338, each pinned by the reviewer's probe."""

import math
import time

import pytest
import shapely
from helpers import everything_observed, pads_ground, parsed, rect, shared_fixture
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from shapely import unary_union
from shapely.geometry import Point
from test_s4_round import PUBLIC

from scene import MAX_WALL_PIECES, SceneError, parse_scene
from solver import PASS, evaluate_start, solve


def side_passage(error: float = 0.05) -> dict:
    """The house wall, a 4 ft open passage to the side yard, then the garage wall, everything
    observed by views along the walls (probe r_gap2)."""
    return {
        "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "house", "plus_minus_ft": error},
        "walls": [
            {"id": "house", "baseline": [[-20, 0], [10, 0]], "plus_minus_ft": error},
            {"id": "garage", "baseline": [[14, 0], [40, 0]], "plus_minus_ft": error},
        ],
        "objects": [],
        "ground": pads_ground([(6.5, 10)], -40, 60),
        "overheads": [],
        "facing": [],
        "coverage": everything_observed(-60, 60),
    }


def test_ground_behind_a_gap_between_walls_is_yard_until_seen() -> None:
    # Before: the passage behind the gap's line was neither in front of a wall nor the house,
    # so it never counted as unseen, and [6.6, 9.18], 1.65 ft from it, passed gas, AC and pool
    # "and that whole area was seen" from views that only ran along the walls.
    raw = side_passage()
    scene = parsed(raw, PUBLIC)
    assert scene.unobserved_ground().intersects(Point(10.5, -1.0))
    result = solve(scene, PUBLIC)
    assert result["decision"] != PASS
    checks = {c["id"]: c for c in result["checks"]}
    for cid in ("gas_clearance", "ac_clearance", "pool_clearance"):
        assert (checks[cid]["outcome"], checks[cid]["unsure_cause"]) == ("unsure", "unobserved")


def test_a_view_into_the_passage_settles_it() -> None:
    # The request names ground over exactly the gap's stretch; captured, it shows both sides of
    # the gap's line (the camera pointed into the passage), and with the passage's surface
    # recorded too (seen ground with none may be a driveway) the spot passes.
    raw = side_passage()
    result = solve(parsed(raw, PUBLIC), PUBLIC)
    passage = [m for m in result["missing_evidence"] if m.get("span_ft") == [10.0, 14.0]]
    assert passage and passage[0]["band"] == "ground"
    raw["coverage"]["observed"].append(
        {"band": "ground", "span_ft": [10.0, 14.0], "out_ft": passage[0]["out_ft"]}
    )
    raw["ground"].append({"type": "concrete", "polygon": rect(10, 14, -30, 0), "plus_minus_ft": 0})
    assert solve(parsed(raw, PUBLIC), PUBLIC)["decision"] == PASS


def test_seen_ground_with_no_recorded_surface_may_be_a_driveway() -> None:
    # Before (probe r_drive): ground seen 30 ft out but only the pad's lawn recorded, and
    # drive_clearance passed "that whole area was seen", where ground_surface calls the same
    # ground an unknown attribute.
    raw = shared_fixture()
    raw["meter"].pop("plus_minus_ft")
    raw["ground"] = [{"type": "lawn", "polygon": rect(4, 11, 0, 4), "plus_minus_ft": 0.05}]
    result = solve(parsed(raw, PUBLIC), PUBLIC)
    drive = next(c for c in result["checks"] if c["id"] == "drive_clearance")
    assert (drive["outcome"], drive["unsure_cause"]) == ("unsure", "unknown_attribute")
    assert "Recording what that ground is" in drive["reason"]


def zigzag(walls: int, points: int) -> dict:
    """Probe r_time2: walls end to end along x, each tap bowing 0.1 ft, so every tap is a
    corner (more than the 0.05 ft a straight wall's taps may stray)."""
    out, x = [], 0.0
    for k in range(walls):
        pts = []
        for j in range(points):
            pts.append([x, 0.1 * (j % 2)])
            x += 1.0
        x -= 1.0
        out.append({"id": f"w{k}", "baseline": pts})
    return {
        "schema_version": "1.0",
        "meter": {"pos": [5.0, 5, 0.0], "wall_id": "w0"},
        "walls": out,
        "coverage": {"ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}}},
    }


def test_a_scene_with_more_wall_pieces_than_the_bound_is_refused_at_once() -> None:
    # Before: an 11 KB body of 4 zigzag walls (798 straight pieces) took 28.6 s to solve.
    started = time.perf_counter()
    with pytest.raises(SceneError, match="straight wall segments"):
        parse_scene(zigzag(4, 200), PUBLIC.rules)
    assert time.perf_counter() - started < 1.0
    # A scene at the bound still parses.
    parse_scene(zigzag(1, MAX_WALL_PIECES + 1), PUBLIC.rules)


RULE_OF = {"gas_clearance": "gas_ft", "ac_clearance": "ac_ft", "pool_clearance": "pool_ft"}


@st.composite
def passages(draw: st.DrawFn) -> dict:
    """Two walls with a gap wider than the join tolerance between them, the second at any angle,
    at any errors, every band seen by views along the walls (none pointed into the passage)."""
    c = draw(st.floats(1.0, 15.0))  # w1 reaches the meter at x = 0
    g = c + draw(st.floats(1.0, 8.0))
    angle = math.radians(draw(st.floats(-80.0, 80.0)))
    end = [g + 20 * math.cos(angle), 20 * math.sin(angle)]
    e1, e2 = draw(st.floats(0.0, 0.3)), draw(st.floats(0.0, 0.3))
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = draw(st.floats(0.0, 0.3))
    raw["walls"] = [
        {"id": "w1", "baseline": [[-20, 0], [c, 0]], "height_ft": 9, "plus_minus_ft": e1},
        {"id": "w2", "baseline": [[g, 0], end], "height_ft": 9, "plus_minus_ft": e2},
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-80, 80, -80, 80), "plus_minus_ft": 0}]
    raw["overheads"], raw["facing"] = [], []
    raw["coverage"] = everything_observed(-60, 60)
    return raw


@settings(max_examples=40, deadline=None)
@given(raw=passages(), data=st.data())
def test_views_along_the_walls_never_show_a_side_passage(raw: dict, data) -> None:
    """Ground behind a gap that no wall faces is shown only by a view pointed into the passage,
    so views along the walls leave any clearance that reaches it unsettled."""
    scene = parsed(raw, PUBLIC)
    assume(scene.gaps)
    # What no wall faces: behind the gap's line, not the house, and not in front of a wall
    # (a turned second wall faces part of that strip, and a view along it does show that part).
    faced = scene.band_polygon(scene.pieces[0].s0, scene.pieces[-1].s1, scene.reach_ft)
    behind = unary_union(scene.passages(scene.reach_ft))
    passage = shapely.difference(shapely.difference(behind, scene.house()), faced)
    piece = data.draw(st.sampled_from(scene.walls), label="wall")
    assume(piece.s1 - piece.s0 >= PUBLIC.rules.battery.width_ft.value)
    s0 = data.draw(st.floats(piece.s0, piece.s1 - PUBLIC.rules.battery.width_ft.value))
    candidate = evaluate_start(scene, PUBLIC, s0, piece.wall_id)
    for c in candidate.checks:
        rule = RULE_OF.get(c.id)
        if rule is None:
            continue
        reach = getattr(PUBLIC.rules.clearances, rule).value
        if candidate.footprint.distance(passage) < reach:
            assert c.outcome != PASS, (c.id, s0, c.reason)


def test_a_subnormal_coordinate_does_not_hide_a_passage() -> None:
    # Found by the passage property: w2's far end at z = 7.8e-314 ft made the ground behind the
    # gap vanish from the unseen area, and a battery 0.73 ft from the passage passed gas.
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = 0.0
    raw["walls"] = [
        {"id": "w1", "baseline": [[-20, 0], [3.3096, 0]], "height_ft": 9, "plus_minus_ft": 0.0},
        {
            "id": "w2",
            "baseline": [[4.7683, 0], [24.7683, 7.7669729873e-314]],
            "height_ft": 9,
            "plus_minus_ft": 0.0,
        },
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-80, 80, -80, 80), "plus_minus_ft": 0}]
    raw["overheads"], raw["facing"] = [], []
    raw["coverage"] = everything_observed(-60, 60)
    scene = parsed(raw, PUBLIC)
    assert scene.unobserved_ground().contains(Point(4.0, -0.5))
    gas = next(
        c for c in evaluate_start(scene, PUBLIC, 0.0, "w1").checks if c.id == "gas_clearance"
    )
    assert gas.outcome != PASS
