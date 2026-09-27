"""A PASS holds in every world the declared errors allow.

A scene is what the phone measured, each position to within its declared error. A check that
PASSes must stay clear of its rule wherever the true positions lie within those errors, so its
error has to include the error of everything its measurement depends on, not only the candidate
wall's. The property moves every input within its declared error and re-evaluates the same
battery:

- The battery stays where the answer puts it: at its offset from the meter, where the AR view
  anchors it (AGENTS.md), flush against its own wall.
- A position moves in its own coordinates: wall ends and corners, the meter, and object and
  ground outlines anywhere within their error in plan; an object's span along s by its error;
  a measured height or depth by its error. The contract defines an error as a distance ("the true
  value lies within this distance"), so the extremes lie on a circle of that radius (eight
  directions), not at the corners of a box, which are sqrt(2) times further. A corner shared by two
  walls moves as one point, within both walls' errors.
- Coverage spans have no declared error and keep their s.
- Moves that contradict each other (the meter past the end of its own wall) describe no real
  house and are discarded.

Then, for every check that PASSed: in the perturbed scene with its declared errors it is not
FAIL, and in the perturbed scene read as exact (every error zero, so the check reports whether the
rule truly holds there) it is not FAIL either.
"""

import copy
import math

import pytest
from helpers import parsed, rect, shared_fixture
from hypothesis import assume, given, reject, settings, target
from hypothesis import strategies as st
from test_final_review import cornered
from test_s4_round import PUBLIC

from scene import SceneError
from solver import FAIL, PASS, evaluate_start, solve

W = PUBLIC.rules.battery.width_ft.value
DIRECTIONS = [(math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)) for k in range(8)]
OBJECT_TYPES = ["gas_meter", "ac", "pool", "window", "door", "elec_box", "vent", "battery"]
ERROR = st.sampled_from([0.0, 0.05, 0.1, 0.2, 0.3])


def error_of(item: dict) -> float:
    return item.get("plus_minus_ft", 0.0)


@st.composite
def uncertain_corners(draw: st.DrawFn) -> dict:
    """The fixed-point test's corner scenes with the meter anywhere up to the corner, declared
    errors on the walls, the meter and every object, objects with and without plan outlines, a
    second ground patch, measured overheads and facing gaps, and, half the time, everything
    observed so checks can pass."""
    raw = draw(cornered())
    # Anywhere along its wall up to the corner, so a battery round the corner can come near the
    # meter's working space.
    corner = raw["walls"][0]["baseline"][1][0]
    raw["meter"]["pos"][0] = draw(st.floats(-3.0, corner))
    scene = parsed(raw, PUBLIC)
    # Two walls a join treats as one straight wall have no corner to move.
    assume(sum(1 for p in scene.walls) == 2)
    for wall in raw["walls"]:
        wall["plus_minus_ft"] = draw(ERROR)
    raw["meter"]["plus_minus_ft"] = draw(ERROR)
    lo, hi = scene.walls[0].s0, scene.walls[-1].s1
    if draw(st.booleans()):
        raw["coverage"]["observed"] = [
            {"band": b, "span_ft": [lo, hi], **({"out_ft": 30.0} if b == "ground" else {})}
            for b in ("wall", "ground", "overhead", "facing")
        ]

    def wall_at(s: float):
        return next(p for p in scene.walls if p.s0 - 1e-9 <= s <= p.s1 + 1e-9)

    objects = []
    for _ in range(draw(st.integers(0, 3))):
        kind = draw(st.sampled_from(OBJECT_TYPES))
        a = draw(st.floats(lo, hi - 1.0))
        length = draw(st.floats(0.5, 4.0))
        b = min(a + length, hi)
        wall = wall_at(a)
        obj = {
            "type": kind,
            "wall_id": wall.wall_id,
            "span_ft": [a, b],
            "bottom_ft": draw(st.sampled_from([0.0, 1.0, 3.0])),
            "top_ft": 7.0,
            "source": "tape",
            "plus_minus_ft": draw(ERROR),
        }
        if kind in ("gas_meter", "ac", "pool", "battery") and draw(st.booleans()):
            out = draw(st.floats(0.0, 8.0))
            depth = draw(st.floats(0.5, 3.0))
            # A rectangle along the wall its span starts on (one crossing the corner keeps going
            # straight rather than folding into a self-intersecting outline).
            corners = [(a, out), (b, out), (b, out + depth), (a, out + depth)]
            obj["footprint"] = [list(wall.point(s, o)) for s, o in corners]
        if kind == "window":
            obj["attrs"] = {"operable": draw(st.booleans())}
        objects.append(obj)
    raw["objects"] = objects
    if draw(st.booleans()):
        kind = draw(st.sampled_from(["drive", "deck", "concrete"]))
        x0, z0 = draw(st.floats(-20, 20)), draw(st.floats(0.5, 12))
        raw["ground"].append(
            {"type": kind, "polygon": rect(x0, x0 + 6, z0, z0 + 4), "plus_minus_ft": draw(ERROR)}
        )
    for key, value_key in (("overheads", "clearance_ft"), ("facing", "depth_ft")):
        raw[key] = []
        for _ in range(draw(st.integers(0, 2))):
            a = draw(st.floats(lo, hi - 1.0))
            raw[key].append(
                {
                    "wall_id": wall_at(a).wall_id,
                    "span_ft": [a, min(a + draw(st.floats(0.5, 6.0)), hi)],
                    value_key: draw(st.floats(1.0, 12.0)),
                    "plus_minus_ft": draw(ERROR),
                }
            )
    return raw


RULE_OF = {
    "gas_meter": "gas_ft",
    "ac": "ac_ft",
    "pool": "pool_ft",
    "battery": "battery_ft",
    "window": "opening_ft",
    "door": "opening_ft",
    "elec_box": "wall_equipment_ft",
    "vent": "wall_equipment_ft",
}


def near_misses(raw: dict, piece, s0: float, data) -> dict:
    """Up to two objects just past their rule from this battery, by a fraction of the errors
    (up to twice the largest), beside it along the wall or in front of it: margins where an error
    left out of a check shows."""
    out = copy.deepcopy(raw)
    errors = [error_of(out["meter"]), *(error_of(w) for w in out["walls"])]
    for i in range(data.draw(st.integers(0, 2), label="near misses")):
        kind = data.draw(st.sampled_from(sorted(RULE_OF)), label=f"near {i} type")
        rule = getattr(PUBLIC.rules.clearances, RULE_OF[kind]).value
        gap = rule + data.draw(st.floats(0.0, 2.0), label=f"near {i} margin") * max(errors)
        e = data.draw(ERROR, label=f"near {i} error")
        obj = {
            "type": kind,
            "wall_id": piece.wall_id,
            "bottom_ft": 3.0,
            "top_ft": 7.0,
            "source": "tape",
            "plus_minus_ft": e,
        }
        where = data.draw(st.sampled_from(["left", "right", "front"]), label=f"near {i} where")
        if where == "front" and kind in ("gas_meter", "ac", "pool", "battery"):
            a = s0 + data.draw(st.floats(-1.0, W), label=f"near {i} along")
            o = PUBLIC.rules.battery.depth_ft.value + gap + e
            corners = [(a, o), (a + 1, o), (a + 1, o + 1), (a, o + 1)]
            obj["span_ft"] = [a, a + 1]
            obj["footprint"] = [list(piece.point(s, v)) for s, v in corners]
        elif where == "left":
            obj["span_ft"] = [s0 - gap - e - 1, s0 - gap - e]
        else:
            obj["span_ft"] = [s0 + W + gap + e, s0 + W + gap + e + 1]
        if kind == "window":
            obj["attrs"] = {"operable": True}
        out["objects"].append(obj)
    return out


def moved(point: list[float], radius: float, k: int | None) -> list[float]:
    if k is None:
        return list(point)
    dx, dz = DIRECTIONS[k]
    return [point[0] + radius * dx, point[1] + radius * dz]


def perturbed(raw: dict, data) -> tuple[dict, tuple[float, float]]:
    """The scene with every input moved within its declared error (the docstring's model), and
    the meter's plan displacement."""
    out = copy.deepcopy(raw)
    direction = st.none() | st.integers(0, 7)
    sign = st.sampled_from([-1, 0, 1])
    walls, given = out["walls"], raw["walls"]

    def joined(i: int) -> bool:
        """Whether wall i starts exactly where wall i - 1 ends: one corner, moved once."""
        return i > 0 and given[i - 1]["baseline"][-1] == given[i]["baseline"][0]

    for i, wall in enumerate(walls):
        pts = wall["baseline"]
        for j in range(len(pts)):
            if j == 0 and joined(i):
                pts[0] = list(walls[i - 1]["baseline"][-1])
                continue
            radius = error_of(wall)
            if j == len(pts) - 1 and i + 1 < len(walls) and joined(i + 1):
                radius = min(radius, error_of(walls[i + 1]))
            pts[j] = moved(pts[j], radius, data.draw(direction, label=f"wall {i} point {j}"))
    m = out["meter"]
    k = data.draw(direction, label="meter")
    x, _, z = m["pos"]
    mx, mz = moved([x, z], error_of(m), k)
    m["pos"] = [mx, m["pos"][1], mz]
    for i, obj in enumerate(out["objects"]):
        e = error_of(obj)
        shift = data.draw(sign, label=f"object {i} span") * e
        obj["span_ft"] = [obj["span_ft"][0] + shift, obj["span_ft"][1] + shift]
        if "footprint" in obj:
            k = data.draw(direction, label=f"object {i} outline")
            if k is not None:
                d = moved([0.0, 0.0], e, k)
                obj["footprint"] = [[p[0] + d[0], p[1] + d[1]] for p in obj["footprint"]]
    for i, patch in enumerate(out["ground"]):
        k = data.draw(direction, label=f"ground {i}")
        if k is not None:
            d = moved([0.0, 0.0], error_of(patch), k)
            patch["polygon"] = [[p[0] + d[0], p[1] + d[1]] for p in patch["polygon"]]
    for key, value_key in (("overheads", "clearance_ft"), ("facing", "depth_ft")):
        for i, entry in enumerate(out[key]):
            change = data.draw(sign, label=f"{key} {i}") * error_of(entry)
            entry[value_key] = max(0.0, entry[value_key] + change)
    return out, (mx - x, mz - z)


def exact(raw: dict) -> dict:
    """The same scene read as exact: every declared error zero."""
    out = copy.deepcopy(raw)
    for item in [out["meter"], *out["walls"], *out["objects"], *out["ground"]]:
        item["plus_minus_ft"] = 0.0
    for item in [*out["overheads"], *out["facing"]]:
        item["plus_minus_ft"] = 0.0
    return out


def same_battery(raw: dict, s0: float, wall_id: str, moved_raw: dict, meter_shift) -> dict:
    """The battery at its offset from the meter in the perturbed scene: the point of the wall
    behind its middle moves with the meter, and it stands flush against the moved wall there.
    Returns its checks by id."""
    scene = parsed(raw, PUBLIC)
    candidate = evaluate_start(scene, PUBLIC, s0, wall_id)
    middle = candidate.piece.point(s0 + W / 2)
    target = (middle[0] + meter_shift[0], middle[1] + meter_shift[1])
    other = parsed(moved_raw, PUBLIC)
    # A move within the errors can make two nearly collinear walls one straight wall, which keeps
    # the first wall's id.
    pieces = [p for p in other.walls if p.wall_id == candidate.piece.wall_id] or other.walls
    piece = min(pieces, key=lambda p: abs(p.local(target)[1]))
    s_mid = piece.local(target)[0]
    # The round trip through plan coordinates adds float noise (1e-16 ft) that can cross the
    # solver's 1e-9 ft thresholds; a start that didn't move keeps its value. Every nonzero error
    # here is 0.05 ft or more.
    start = s0 if abs(s_mid - W / 2 - s0) < 1e-6 else s_mid - W / 2
    result = evaluate_start(other, PUBLIC, start, piece.wall_id)
    return {c.id: c for c in result.checks}


@settings(max_examples=40, deadline=None)
@given(raw=uncertain_corners(), data=st.data())
def test_a_pass_holds_wherever_the_declared_errors_put_things(raw: dict, data) -> None:
    scene = parsed(raw, PUBLIC)
    piece = data.draw(st.sampled_from(scene.walls), label="wall")
    assume(piece.s1 - piece.s0 >= W)
    s0 = data.draw(st.floats(piece.s0, piece.s1 - W), label="s0")
    raw = near_misses(raw, piece, s0, data)
    scene = parsed(raw, PUBLIC)
    before = {c.id: c for c in evaluate_start(scene, PUBLIC, s0, piece.wall_id).checks}
    passing = [cid for cid, c in before.items() if c.outcome == PASS]
    assume(passing)
    moved_raw, shift = perturbed(raw, data)
    try:
        declared = same_battery(raw, s0, piece.wall_id, moved_raw, shift)
        truth = same_battery(raw, s0, piece.wall_id, exact(moved_raw), shift)
    except SceneError:
        # Moves that contradict each other (the meter past the end of its own wall) describe no
        # real house.
        reject()
    # Steer the search toward near misses: the least slack any passing check keeps in the exact
    # scene.
    slack = [slack_of(truth[cid]) for cid in passing if slack_of(truth[cid]) is not None]
    if slack:
        target(-min(slack), label="closest to breaking")
    for cid in passing:
        for name, after in (("declared errors", declared), ("exact", truth)):
            c = after[cid]
            assert c.outcome != FAIL, (cid, name, c.measured, c.plus_minus, c.reason)


def slack_of(c) -> float | None:
    """How far a check's measurement is from its rule, on the passing side."""
    if c.measured is None or c.threshold is None:
        return None
    return c.measured - c.threshold if c.comparison == "at_least" else c.threshold - c.measured


def test_a_scene_whose_overlays_meet_at_a_far_corner_is_answered() -> None:
    # Found by the property: these exact walls (w1's ends moved within 0.3 ft of a corner scene)
    # made GEOS raise "side location conflict" while subtracting the seen ground, a 500 for a
    # valid scene.
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = 0.0
    corner = [8.382847167508826, -0.035355339059327376]
    raw["walls"] = [
        {
            "id": "w1",
            "baseline": [[-30.212132034355964, 0.21213203435596426], corner],
            "height_ft": 9,
            "plus_minus_ft": 0.0,
        },
        {
            "id": "w2",
            "baseline": [corner, [23.96112173570757, -12.586407820996747]],
            "height_ft": 9,
            "plus_minus_ft": 0.0,
        },
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(-60, 60, -60, 60), "plus_minus_ft": 0}]
    raw["overheads"], raw["facing"] = [], []
    end = 28.418202506568154
    raw["coverage"] = {
        "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
        "observed": [
            {"band": b, "span_ft": [-30.0, end], **({"out_ft": 1.0} if b == "ground" else {})}
            for b in ("wall", "ground", "overhead", "facing")
        ],
    }
    assert solve(parsed(raw, PUBLIC), PUBLIC)["decision"] in ("pass", "reject", "manual_review")


def meter_corner(meter_wall_z: float = 0.0, meter_wall_error: float = 0.5) -> dict:
    """The caretaker's witness: the meter on w1 [[-4, 0], [2, 0]] (+/- 0.5 ft), the battery on the
    exact wall w2 [[2, 0], [2, 10]] over lawn, everything seen."""
    raw = shared_fixture()
    raw["meter"] = {"pos": [0.0, 5.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0.0}
    raw["walls"] = [
        {
            "id": "w1",
            "baseline": [[-4, meter_wall_z], [2, meter_wall_z]],
            "height_ft": 9,
            "plus_minus_ft": meter_wall_error,
        },
        {"id": "w2", "baseline": [[2, 0], [2, 10]], "height_ft": 9, "plus_minus_ft": 0.0},
    ]
    raw["ground"] = [{"type": "lawn", "polygon": rect(0.1, 2.1, 3.2, 6), "plus_minus_ft": 0.0}]
    raw["objects"], raw["overheads"], raw["facing"] = [], [], []
    raw["coverage"] = {
        "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
        "observed": [
            {"band": b, "span_ft": [-4, 12], **({"out_ft": 20} if b == "ground" else {})}
            for b in ("wall", "ground", "overhead", "facing")
        ],
    }
    return raw


def working_space(raw: dict, s0: float = 5.2):
    checks = evaluate_start(parsed(raw, PUBLIC), PUBLIC, s0, "w2").checks
    return next(c for c in checks if c.id == "meter_working_space")


def test_the_meter_walls_error_counts_toward_its_working_space() -> None:
    # Before: 0.2 ft clear (+/- 0) was a PASS, though the working space is drawn in front of w1,
    # which may lie 0.5 ft closer to the battery.
    c = working_space(meter_corner())
    assert (c.measured, c.outcome) == (pytest.approx(0.2), "unsure")
    assert c.plus_minus >= 0.5


def test_a_battery_in_the_working_space_from_another_wall_fails() -> None:
    # w1 moved 0.3 ft toward the battery, read as exact: the battery on w2 still starts at
    # z = 3.2 (s = 5.5, as w2 now follows a 0.3 ft gap) and stands 0.1 ft inside the working
    # space. Before, the overlap was measured along s as 0, a tie, never a FAIL.
    c = working_space(meter_corner(meter_wall_z=0.3, meter_wall_error=0.0), s0=5.5)
    assert c.measured == pytest.approx(-0.1)
    assert c.outcome == "fail"


def test_the_meters_error_counts_toward_where_the_battery_stands() -> None:
    # Found by the property: on the exact wall w2 the battery ended 0.042 ft short of the wall's
    # end, a PASS, but it is placed by its offset from the meter, and the meter (+/- 0.05 ft) may
    # stand 0.05 ft further along: the battery then runs past the end.
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = 0.05
    end = [25.121953903127825, 0.34904812874567026]
    raw["walls"] = [
        {"id": "w1", "baseline": [[-30, 0], [5.125, 0]], "height_ft": 9, "plus_minus_ft": 0.0},
        {"id": "w2", "baseline": [[5.125, 0], end], "height_ft": 9, "plus_minus_ft": 0.0},
    ]
    checks = evaluate_start(parsed(raw, PUBLIC), PUBLIC, 22.5, "w2").checks
    backing = next(c for c in checks if c.id == "wall_backing")
    assert backing.outcome == "unsure"


def test_a_meter_move_slides_a_battery_round_the_corner_along_its_wall() -> None:
    # The meter (+/- 0.3 ft) on w1 and the battery on the exact wall w2 at a right angle: a box
    # on w2 cleared the battery along the wall by 0.35 ft, a PASS with 0.3 ft of error. Moving
    # the meter 0.3 ft at 135 degrees moves s = 0 back along w1 and the battery up w2, 0.42 ft in
    # s together (0.3 * sqrt(2)), and the box then sits over the battery.
    t = PUBLIC.rules.clearances.wall_equipment_ft.value
    raw = shared_fixture()
    raw["meter"]["plus_minus_ft"] = 0.3
    raw["walls"] = [
        {"id": "w1", "baseline": [[-30, 0], [4.0, 0]], "height_ft": 9, "plus_minus_ft": 0.0},
        {"id": "w2", "baseline": [[4.0, 0], [4.0, 20]], "height_ft": 9, "plus_minus_ft": 0.0},
    ]
    box = 10.0 + W + t + 0.35
    raw["objects"] = [
        {
            "type": "elec_box",
            "wall_id": "w2",
            "span_ft": [box, box + 1],
            "bottom_ft": 3,
            "top_ft": 5,
            "source": "tape",
            "plus_minus_ft": 0.0,
        }
    ]
    checks = evaluate_start(parsed(raw, PUBLIC), PUBLIC, 10.0, "w2").checks
    above = next(c for c in checks if c.id == "wall_equipment_above")
    assert (above.measured, above.outcome) == (pytest.approx(0.35), "unsure")


def test_ground_requests_ignore_lines_left_where_edges_touch() -> None:
    # Reduced from a simulator capture: with the snapped overlays, clipping its unseen ground to
    # a check's radius left a line beside the area, and the next overlay raised "mixed-dimension".
    raw = {
        "meter": {"pos": [5.3468, 4.9213, 15.6219], "wall_id": "wall", "plus_minus_ft": 0.9843},
        "walls": [{"id": "wall", "baseline": [[2.7079, 4.9432], [8.9454, 30.1839]]}],
        "objects": [
            {
                "type": "window",
                "wall_id": "wall",
                "span_ft": [6.7257, 9.6785],
                "bottom_ft": 3.1168,
                "top_ft": 6.0696,
                "source": "tap",
                "plus_minus_ft": 0.9843,
                "attrs": {"operable": True},
            }
        ],
        "ground": [],
        "overheads": [],
        "facing": [],
        "coverage": {
            "ends": {"left": {"kind": "unexplored"}, "right": {"kind": "unexplored"}},
            "observed": [{"band": "ground", "span_ft": [-1.5, 0], "out_ft": 3.5}],
        },
    }
    assert solve(parsed(raw, PUBLIC), PUBLIC)["decision"] == "manual_review"
