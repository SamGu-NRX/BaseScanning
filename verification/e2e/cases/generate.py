"""Write the black-box placement cases in this folder, one JSON file per case.

Run from verification/: `uv run python e2e/cases/generate.py`. README.md gives the arithmetic
behind every expected number. The scenes follow server/schemas/scene.schema.json at
origin/t3/server; they were written from that schema and the lane C review, not from the
server's code or fixtures.
"""

import json
import math
from pathlib import Path

HERE = Path(__file__).parent
RESEARCH_SHA = "a7d91f1"
SERVER_SHA = "69c6c3c"

# Battery footprint from the lane C review's parameter table: 31 in wide, 22 in deep.
W = 31 / 12
D = 11 / 6
# Public server/rules.yaml route.corner_allowance_ft, a day-1 placeholder, not measured.
CORNER_ALLOWANCE = 0.5
METER_Y = 4.0
# Walls placed at z = -D put a flush battery's front edge at z = 0 exactly in floating point,
# so a gas strip at z = d is measured as exactly d. The margin cases need that: 3.3 - 0.3 and
# 2.7 + 0.3 both evaluate to exactly 3.0, while 11/6 + 3.3 - 11/6 does not return 3.3.
FRONT0 = -D
# rules.yaml meter_working_space: 2.5 ft wide centred on the meter (NEC 110.26). A battery
# overlapping s = (-1.25, 1.25) fails, i.e. starts in (-1.25 - W, 1.25) = (-3.833, 1.25).
WS = "meter_working_space"


def golden(n):
    return f"t3-lane-c-review.md golden {n} at origin/t3/research {RESEARCH_SHA}"


def straight(wall_id, x0, x1, z=0.0, pm=0.0):
    return {"id": wall_id, "baseline": [[x0, z], [x1, z]], "plus_minus_ft": pm}


def rect(x0, x1, z0, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def concrete(polygon):
    return {"type": "concrete", "polygon": polygon, "plus_minus_ft": 0.0}


def facing(wall_id, span, depth=9.0):
    return {"wall_id": wall_id, "span_ft": list(span), "depth_ft": depth, "plus_minus_ft": 0.0}


# How far "fully observed" coverage reaches past the chain's ends and out from the wall.
# rules.yaml (origin/t3/server) has pool_ft 10 and drive_ft 5, and an unseen pool or driveway
# within that distance of the footprint makes the check UNSURE and asks for a photo. The
# footprint's front is 11/6 ft out, so the ground must be seen to 10 + 11/6 ft out and 10 ft
# along the wall past any footprint. 15 ft clears both.
PAST = 15.0


def coverage(
    span,
    ground=None,
    left="limit",
    right="limit",
    bands=("wall", "overhead", "facing"),
    past=(0.0, 0.0),
    out=10.0,
):
    """Observed bands over `span` widened by `past` (left, right); ground over `ground`."""
    wide = [span[0] - past[0], span[1] + past[1]] if any(past) else list(span)
    observed = [{"band": b, "span_ft": wide} for b in bands]
    for g in [wide] if ground is None else ground:
        observed.append({"band": "ground", "span_ft": list(g), "out_ft": out})
    return {"ends": {"left": {"kind": left}, "right": {"kind": right}}, "observed": observed}


def full_coverage(span):
    return coverage(span, past=(PAST, PAST), out=PAST)


def gas(wall_id, span, footprint, pm=0.0, conf=None):
    obj = {
        "type": "gas_meter",
        "wall_id": wall_id,
        "span_ft": list(span),
        "source": "tape",
        "plus_minus_ft": pm,
        "footprint": footprint,
    }
    if conf is not None:
        obj["conf"] = conf
    return obj


def opening(kind, wall_id, span, bottom, top):
    return {
        "type": kind,
        "wall_id": wall_id,
        "span_ft": list(span),
        "bottom_ft": bottom,
        "top_ft": top,
        "attrs": {"operable": True},
        "source": "tape",
        "plus_minus_ft": 0.0,
    }


def meter(x, z, wall_id="w1"):
    return {"pos": [x, METER_Y, z], "wall_id": wall_id, "plus_minus_ft": 0.0}


def polyline_wall(wall_id, s_start, segments):
    """A wall of straight segments given as (length, heading in degrees), heading 0 = +x.

    The first point sits at s = s_start. The baseline is shifted so the point at s = 0 (the
    meter) is the origin. Headings fall left to right, so every corner is an outside corner.
    """
    pts = [(0.0, 0.0)]
    s_marks = [s_start]
    for length, heading in segments:
        a = math.radians(heading)
        x, z = pts[-1]
        pts.append((x + length * math.cos(a), z + length * math.sin(a)))
        s_marks.append(s_marks[-1] + length)
    for i in range(len(segments)):
        if s_marks[i] <= 0 <= s_marks[i + 1]:
            t = (0 - s_marks[i]) / (s_marks[i + 1] - s_marks[i])
            ox = pts[i][0] + t * (pts[i + 1][0] - pts[i][0])
            oz = pts[i][1] + t * (pts[i + 1][1] - pts[i][1])
            break
    shifted = [[x - ox, z - oz] for x, z in pts]
    return {"id": wall_id, "baseline": shifted, "plus_minus_ft": 0.0}


def offset_ground(baseline, out=12.0):
    """Ground polygon in front of a convex wall: the baseline, then the baseline pushed out."""
    front = []
    n = len(baseline)
    for i, (x, z) in enumerate(baseline):
        normals = []
        for j in (i - 1, i):
            if 0 <= j < n - 1:
                dx = baseline[j + 1][0] - baseline[j][0]
                dz = baseline[j + 1][1] - baseline[j][1]
                length = math.hypot(dx, dz)
                normals.append((-dz / length, dx / length))
        nx = sum(v[0] for v in normals) / len(normals)
        nz = sum(v[1] for v in normals) / len(normals)
        norm = math.hypot(nx, nz)
        front.append([x + out * nx / norm, z + out * nz / norm])
    return concrete([list(p) for p in baseline] + front[::-1])


CASES = []


def case(cid, source, description, scene, expect, rules_assumed=None):
    CASES.append(
        {
            "id": cid,
            "source": source,
            "description": description,
            "scene": scene,
            # The fixed fixture spans and sweep ranges were derived with this battery width.
            "rules_assumed": {"battery_width": W} | (rules_assumed or {}),
            "expect": expect,
        }
    )


def straight_scene(x0, x1, z=0.0, meter_x=0.0, objects=(), ground=None, cov=None, wall_pm=0.0):
    scene = {
        "schema_version": "1.0",
        "meter": meter(meter_x, z),
        "walls": [straight("w1", x0, x1, z, wall_pm)],
        "objects": list(objects),
        "ground": [concrete(rect(x0, x1, z, z + 10))] if ground is None else ground,
        "facing": [facing("w1", (x0 - meter_x, x1 - meter_x))],
    }
    scene["coverage"] = full_coverage((x0 - meter_x, x1 - meter_x)) if cov is None else cov
    return scene


# g01: an unseen stretch behind a garage door needs no photo, since no cable can reach it.
# The garage ends at s = -1, inside the meter's working space, so no battery can sit between
# garage and meter; the unseen stretch starts at s = -9, more than pool_ft (10) left of the
# first start right of the meter (1.25; 2 once garage doors count as openings, f2705dd).
case(
    "g01-unseen-beyond-garage",
    golden("01"),
    "The wall beyond a garage door left of the meter was never observed. No spot there can be "
    "reached by a cable, so the result asks for no photos, and every start beyond the garage "
    "fails its route.",
    straight_scene(
        -20,
        10,
        objects=[opening("garage_door", "w1", (-5.5, -1), 0.0, 7.0)],
        cov=coverage((-9, 10), past=(0.0, PAST), out=PAST),
    ),
    {
        "missing_evidence_empty": True,
        "sweep_runs": [
            {
                "wall_id": "w1",
                "start_ft": [-19.9, -8.2],
                "outcome": "fail",
                "failing_match": "route",
            }
        ],
    },
)

# g03: pads at s = [-9, -6] and [7, 10]; the stretch between is short segments.
seg = 13 / 7
g03_wall = polyline_wall("w1", -9, [(3, 32)] + [(seg, 24 - 8 * k) for k in range(7)] + [(3, -32)])
case(
    "g03-negative-s-near-edge",
    golden("03"),
    "Two straight 3 ft pads, s = [-9, -6] and [7, 10], joined by segments 13/7 ft long that no "
    "battery fits on. The left pad's route runs to its near edge (6 ft), so it beats the right "
    "pad (7 ft); measuring to the far edge (103/12 ft) would pick the right pad.",
    {
        "schema_version": "1.0",
        "meter": meter(0.0, 0.0),
        "walls": [g03_wall],
        "objects": [],
        "ground": [offset_ground(g03_wall["baseline"])],
        "facing": [facing("w1", (-9, 10))],
        "coverage": full_coverage((-9, 10)),
    },
    {
        "spot": {"wall_id": "w1", "span_within": [-9, -6]},
        "sweep_runs": [{"wall_id": "w1", "start_ft": [-8.4, 6.9], "outcome": "fail"}],
        "missing_evidence_empty": True,
    },
    {"corner_allowance": CORNER_ALLOWANCE},
)

# g05: inside corner, gas meter on the return wall at [4, 4].
g05 = {
    "schema_version": "1.0",
    "meter": meter(-6.0, 0.0),
    "walls": [
        straight("w1", -7, 4),
        {"id": "w2", "baseline": [[4, 0], [4, 12]], "plus_minus_ft": 0.0},
    ],
    "objects": [gas("w2", (14, 14), [[4, 4]])],
    "ground": [concrete(rect(-7, 4, 0, 12))],
    "facing": [facing("w1", (-1, 10)), facing("w2", (10, 22))],
    "coverage": coverage((-1, 22)),
}
case(
    "g05-inside-corner-gas",
    golden("05"),
    "A gas meter on the return wall of an inside corner is 65/12 ft away along the unrolled "
    "wall but about 2.589 ft away in plan from a battery at s = [6, 6 + 31/12]. Gas clearance "
    "is measured in plan, so those starts fail.",
    g05,
    {
        "decision_not": ["pass"],
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [6, 7.4], "outcome": "fail", "failing_match": "gas"}
        ],
    },
    {"gas": 3.0, "corner_allowance": CORNER_ALLOWANCE},
)

# g06: gas meter body 10 ft out, regulator reaching [7, 23/6].
spike = [[6.5, 10.5], [6.5, 9.5], [6.9, 9.5], [7.0, 23 / 6], [7.1, 9.5], [7.5, 9.5], [7.5, 10.5]]
case(
    "g06-regulator-footprint",
    golden("06"),
    "The gas meter body stands 10 ft from the wall but its regulator reaches [7, 23/6], 2 ft "
    "from the front edge of any battery covering x = 7. Clearance is measured to every part of "
    "the footprint.",
    straight_scene(-3, 16, objects=[gas("w1", (6.5, 7.5), spike)])
    | {"facing": [facing("w1", (-3, 16), depth=12.0)]},
    {
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [3, 9], "outcome": "fail", "failing_match": "gas"}
        ],
    },
    {"gas": 3.0},
)

# g08a: garage door right of the meter, nothing usable before it.
case(
    "g08a-garage-blocks-route",
    golden("08"),
    "A garage door spans s = [1, 5] and the wall ends at s = -0.5, so every start either "
    "overlaps the door or lies beyond it, where the cable would have to cross the door.",
    straight_scene(-0.5, 12, objects=[opening("garage_door", "w1", (1, 5), 0.0, 7.0)]),
    {
        "decision_not": ["pass"],
        "spot": None,
        "missing_evidence_empty": True,
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [5, 9.4], "outcome": "fail", "failing_match": "route"}
        ],
    },
)

# g08b: a 1 ft stretch with no wall between the meter's short wall and the rest.
g08b = {
    "schema_version": "1.0",
    "meter": meter(0.0, 0.0),
    "walls": [straight("w1", -0.5, 1), straight("w2", 2, 12)],
    "objects": [],
    "ground": [concrete(rect(-0.5, 12, 0, 10))],
    "facing": [facing("w1", (-0.5, 1)), facing("w2", (2, 12))],
    "coverage": coverage((-0.5, 12)),
}
case(
    "g08b-missing-wall-blocks-route",
    golden("08"),
    "The meter's wall w1 is 1.5 ft long, too short for a battery; after a 1 ft stretch with no "
    "wall, w2 runs from s = 2 to 12. No cable can cross the gap, so every start on w2 fails "
    "its route and no spot exists.",
    g08b,
    {
        "decision_not": ["pass"],
        "spot": None,
        "missing_evidence_empty": True,
        "sweep_runs": [
            {"wall_id": "w2", "start_ft": [2, 9.4], "outcome": "fail", "failing_match": "route"}
        ],
    },
)

# g10: uniform gas gap d with error e. The strip spans past both wall ends, so every start
# on the wall sees the same perpendicular gap.
T = 3.0


def g10_scene(d, e, wall_pm=0.0):
    strip = gas("w1", (-5, 15), rect(-10, 20, d, d + 0.5), pm=e, conf=0.6)
    return straight_scene(-5, 15, z=FRONT0, objects=[strip], wall_pm=wall_pm) | {
        "ground": [concrete(rect(-5, 15, FRONT0, FRONT0 + 10))]
    }


ROWS = [
    ("pass", lambda e: round(T + e + 0.01, 2), "pass", None),
    ("upper-edge", lambda e: round(T + e, 2), "unsure", "margin"),
    ("lower-edge", lambda e: round(T - e, 2), "unsure", "margin"),
    ("fail", lambda e: round(T - e - 0.01, 2), "fail", None),
]
for e in (0.3, 0.5, 1.5):
    for name, dfun, outcome, cause in ROWS:
        d = dfun(e)
        check = {"match": "gas", "outcome": outcome, "measured_ft": d, "plus_minus_ft": e}
        if cause:
            check["unsure_cause"] = cause
        expect = {"checks": [check], "missing_evidence_empty": True}
        if outcome != "pass":
            expect["decision_not"] = ["pass"]
        if outcome == "fail":
            expect["spot"] = None
            expect["sweep_runs"] = [
                {"wall_id": "w1", "start_ft": [-5, 12.4], "outcome": "fail", "failing_match": "gas"}
            ]
        case(
            f"g10-e{round(e * 10):02d}-{name}",
            golden("10"),
            f"Every start has gas gap d = {d} ft with error {e} ft (gas meter +/- {e}, wall "
            f"exact), against a {T} ft threshold. The gas object's recognition conf of 0.6 "
            "must not change the error.",
            g10_scene(d, e),
            expect,
            {"gas": T},
        )

case(
    "g10-derived-compound-error",
    golden("10"),
    "Gas gap 3.5 ft between a gas meter at +/- 0.3 and a battery on a wall at +/- 0.3. The gap "
    "carries +/- 0.6 (linear sum), so 3.5 - 0.6 = 2.9 is not above 3: unsure by margin. A "
    "root-sum-square error of 0.424 would wrongly pass it.",
    g10_scene(3.5, 0.3, wall_pm=0.3),
    {
        "decision_not": ["pass"],
        "checks": [
            {
                "match": "gas",
                "outcome": "unsure",
                "unsure_cause": "margin",
                "measured_ft": 3.5,
                "plus_minus_ft": 0.6,
            }
        ],
        "missing_evidence_empty": True,
    },
    {"gas": T},
)

# g11a: every start fails gas, but the right end is an untapped corner.
case(
    "g11a-unexplored-corner",
    golden("11 (a)"),
    "Every start on w1 fails gas by 2 ft (gap 1 ft). The walk stopped at the corner at s = 8 "
    "without seeing the next wall, so the result cannot reject and must ask for the wall past "
    "the corner.",
    straight_scene(
        -3,
        8,
        z=FRONT0,
        objects=[gas("w1", (-3, 8), rect(-10, 20, 1.0, 1.5))],
        ground=[concrete(rect(-3, 8, FRONT0, FRONT0 + 10))],
        cov=coverage((-3, 8), right="unexplored"),
    ),
    {
        "decision_in": ["manual_review"],
        "spot": None,
        "missing_evidence_empty": False,
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-3, 5.4], "outcome": "fail", "failing_match": "gas"}
        ],
    },
    {"gas": 3.0},
)

# g11b: both walls walked to real ends, every start fails gas.
strip_out = D + 1.0
g11b = {
    "schema_version": "1.0",
    "meter": meter(0.0, 0.0),
    "walls": [
        straight("w1", -3, 8),
        {"id": "w2", "baseline": [[8, 0], [8, -6]], "plus_minus_ft": 0.0},
    ],
    "objects": [
        gas("w1", (-3, 8), rect(-10, 12, strip_out, strip_out + 0.5)),
        gas("w2", (8, 14), rect(8 + strip_out, 8 + strip_out + 0.5, -10, 3)),
    ],
    "ground": [concrete([[-3, 0], [8, 0], [8, -6], [18, -6], [18, 10], [-3, 10]])],
    "facing": [facing("w1", (-3, 8)), facing("w2", (8, 14))],
    "coverage": coverage((-3, 14)),
}
case(
    "g11b-both-ends-real-all-fail",
    golden("11 (b)"),
    "w1 turns an outside corner at s = 8 onto w2, walked to its real end at s = 14. Gas strips "
    "1 ft in front of each wall fail every start. Nothing is unobserved, so there is no spot "
    "and no photo request.",
    g11b,
    {
        "decision_in": ["manual_review", "reject"],
        "spot": None,
        "missing_evidence_empty": True,
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-3, 5.4], "outcome": "fail", "failing_match": "gas"},
            {"wall_id": "w2", "start_ft": [8, 11.4], "outcome": "fail", "failing_match": "gas"},
        ],
    },
    {"gas": 3.0, "corner_allowance": CORNER_ALLOWANCE},
)

# g11c: observed starts fail gas; the ground right of s = 1 was never seen (closed gate).
case(
    "g11c-unobserved-ground",
    golden("11 (c)"),
    "Starts below s = 2*sqrt(2) - 0.5 fail gas. Starts beyond it pass gas but stand on ground "
    "nobody saw (observed only up to s = 1), so they are unsure, never pass, and the result "
    "asks for that ground.",
    straight_scene(
        -10,
        10,
        z=FRONT0,
        objects=[gas("w1", (-10, -0.5), rect(-12, -0.5, 1.0, 1.5))],
        ground=[concrete(rect(-10, 1, FRONT0, FRONT0 + 10))],
        cov=coverage((-10, 10), ground=[(-10, 1)]),
    ),
    {
        "decision_in": ["manual_review"],
        "spot": {"wall_id": "w1", "span_within": [2 * math.sqrt(2) - 0.5, 10]},
        "missing_evidence_empty": False,
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-9.9, 2.2], "outcome": "fail", "failing_match": "gas"},
            {"wall_id": "w1", "start_ft": [2.5, 7.3], "outcome": "unsure"},
        ],
    },
    {"gas": 3.0},
)

# g12a: legal starts only in the open interval (7, 7.1), narrower than a 2 in grid step.
win2 = 10.1 + W
case(
    "g12a-subgrid-start",
    golden("12"),
    "Windows at s = [3, 4] and [10.1 + 31/12, 11.1 + 31/12] with 3 ft opening clearance leave "
    "legal starts only in (7, 7.1). A 2 in grid from s = 0 samples 7 and 43/6 and misses them.",
    straight_scene(
        -1,
        16,
        objects=[
            opening("window", "w1", (3, 4), 4.0, 7.0),
            opening("window", "w1", (win2, win2 + 1), 4.0, 7.0),
        ],
    ),
    {
        "spot": {"wall_id": "w1", "span_within": [7, 7.1 + W]},
        "sweep_runs": [
            {
                "wall_id": "w1",
                "start_ft": [-1, 6.95],
                "outcome": "fail",
                "failing_match": "opening",
            },
            {
                "wall_id": "w1",
                "start_ft": [7.2, 13.4],
                "outcome": "fail",
                "failing_match": "opening",
            },
        ],
    },
    {"opening": 3.0},
)

# g12b: one straight segment exactly one battery wide; its only start is its last start.
g12b_wall = polyline_wall("w1", -3, [(1.8, 40 - 8 * k) for k in range(5)] + [(W, 0)])
case(
    "g12b-exact-fit-last-start",
    golden("12"),
    "Five 1.8 ft segments, then one straight segment exactly 31/12 ft long at s = [6, 6 + 31/12] "
    "where the wall ends. Its single start, s = 6, is also the last start and must be "
    "enumerated.",
    {
        "schema_version": "1.0",
        "meter": meter(0.0, 0.0),
        "walls": [g12b_wall],
        "objects": [],
        "ground": [offset_ground(g12b_wall["baseline"])],
        "facing": [facing("w1", (-3, 6 + W))],
        "coverage": full_coverage((-3, 6 + W)),
    },
    {"spot": {"wall_id": "w1", "span_within": [6, 6 + W]}, "missing_evidence_empty": True},
    {"corner_allowance": CORNER_ALLOWANCE},
)


# g13: F = right of the meter, gas gap 3.2 +/- 0.3 (unsure); S = left, passes.
def g13_scene(x0):
    strip = gas("w1", (0.3, 8), rect(0.3, 12, 3.2, 3.7), pm=0.3)
    return straight_scene(x0, 8, z=FRONT0, objects=[strip]) | {
        "ground": [concrete(rect(x0, 8, FRONT0, FRONT0 + 10))]
    }


# Passing spots need their right edge left of both the gas edge 0.3 - sqrt(0.65) ~ -0.506 and
# the meter working space's left edge, -1.25 (rules.yaml meter_working_space width 2.5).
s_edge = min(0.3 - math.sqrt(3.3**2 - 3.2**2), -1.25)
case(
    "g13a-unsure-never-outranks-pass",
    golden("13"),
    "Starts right of the meter have gas gap 3.2 +/- 0.3 (unsure) and the shortest routes. "
    "Batteries whose right edge is left of s = 0.3 - sqrt(0.65) clear gas and pass. The chosen "
    "spot must be a passing one on the left.",
    g13_scene(-8),
    {
        "spot": {"wall_id": "w1", "span_within": [-8, s_edge]},
        "checks": [{"match": "gas", "outcome": "pass"}],
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-3.8, 1.2], "outcome": "fail", "failing_match": WS},
            {"wall_id": "w1", "start_ft": [1.3, 5], "outcome": "unsure"},
        ],
        "missing_evidence_empty": True,
    },
    {"gas": 3.0},
)
case(
    "g13b-unsure-alone",
    golden("13"),
    "The wall ends at s = -0.5, so every start has gas gap 3.2 +/- 0.3. The best spot is "
    "unsure by margin and fully observed: manual review with no photo request.",
    g13_scene(-0.5),
    {
        "decision_in": ["manual_review"],
        "spot": {"wall_id": "w1", "span_within": [-0.5, 8]},
        "checks": [
            {
                "match": "gas",
                "outcome": "unsure",
                "unsure_cause": "margin",
                "measured_ft": 3.2,
                "plus_minus_ft": 0.3,
            }
        ],
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-0.4, 1.2], "outcome": "fail", "failing_match": WS},
            {"wall_id": "w1", "start_ft": [1.3, 5.3], "outcome": "unsure"},
        ],
        "missing_evidence_empty": True,
    },
    {"gas": 3.0},
)

# No coverage field: nothing counts as observed and both ends default to unexplored.
nocov = straight_scene(-6, 10)
del nocov["coverage"]
case(
    "c5-no-coverage",
    f"scene.schema.json coverage and end descriptions at origin/t3/server {SERVER_SHA}; C5",
    "A clean straight wall with ground and facing measurements but no coverage field. Nothing is "
    "known to be observed, so no start may pass, the result must ask for views, and it cannot "
    "reject because both ends default to unexplored.",
    nocov,
    {
        "decision_in": ["manual_review"],
        "missing_evidence_empty": False,
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-6, -3.9], "outcome": "unsure"},
            {"wall_id": "w1", "start_ft": [-3.8, 1.2], "outcome": "fail", "failing_match": WS},
            {"wall_id": "w1", "start_ft": [1.3, 7.4], "outcome": "unsure"},
        ],
    },
)

# g07: headroom. rules.yaml headroom.min_ft = 6.5 (at_least). The schema's overheads carry only
# a stretch of wall (span_ft) and a clear height, not a depth out from the wall, so "covers the
# whole footprint" is tested along the wall: a partial overhead over part of the battery's width
# must still limit it.
HEADROOM = 6.5


def overhead(span, clearance, pm=0.5):
    return {"wall_id": "w1", "span_ft": list(span), "clearance_ft": clearance, "plus_minus_ft": pm}


G07_ROWS = [
    # name, clearance, outcome, unsure cause
    ("fail", HEADROOM - 1, "fail", None),
    ("pass", HEADROOM + 0.7, "pass", None),
    ("margin", HEADROOM + 0.3, "unsure", "margin"),
]
for name, clearance, outcome, cause in G07_ROWS:
    check = {
        "match": "headroom",
        "outcome": outcome,
        "measured_ft": clearance,
        "plus_minus_ft": 0.5,
    }
    if cause:
        check["unsure_cause"] = cause
    if outcome == "fail":
        runs = [
            {
                "wall_id": "w1",
                "start_ft": [-5, 12.4],
                "outcome": "fail",
                "failing_match": "headroom",
            }
        ]
    else:
        runs = [
            {"wall_id": "w1", "start_ft": [-5, -3.9], "outcome": outcome},
            {"wall_id": "w1", "start_ft": [1.3, 12.4], "outcome": outcome},
        ]
    expect = {"checks": [check], "sweep_runs": runs, "missing_evidence_empty": True}
    if outcome != "pass":
        expect["decision_not"] = ["pass"]
    if outcome == "fail":
        expect["spot"] = None
    case(
        f"g07-headroom-{name}",
        golden("07"),
        f"An overhead over the whole wall has clear height {clearance} +/- 0.5 ft against "
        f"headroom {HEADROOM} ft, so every start's headroom check is {outcome}.",
        straight_scene(-5, 15) | {"overheads": [overhead((-5, 15), clearance)]},
        expect,
        {"headroom": HEADROOM},
    )

g07u = straight_scene(
    -5, 15, cov=coverage((-5, 15), bands=("wall", "facing"), past=(PAST, PAST), out=PAST)
)
case(
    "g07-headroom-unobserved",
    golden("07"),
    "Nothing overhead was recorded and the overhead band was never observed, so every start's "
    "headroom check is unsure for lack of a view, and the result asks for that view.",
    g07u,
    {
        "decision_not": ["pass"],
        "checks": [{"match": "headroom", "outcome": "unsure", "unsure_cause": "unobserved"}],
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [-5, -3.9], "outcome": "unsure"},
            {"wall_id": "w1", "start_ft": [1.3, 12.4], "outcome": "unsure"},
        ],
        "missing_evidence_empty": False,
    },
    {"headroom": HEADROOM},
)
case(
    "g07-headroom-partial-span",
    golden("07"),
    "A low overhead (5.5 +/- 0.5 ft) covers only s = [8, 8.5]. Every battery whose 31 in "
    "stretch overlaps it fails headroom, even when the overhead covers a few inches of it.",
    straight_scene(-5, 15) | {"overheads": [overhead((8, 8.5), HEADROOM - 1)]},
    {
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [1.3, 5.3], "outcome": "pass"},
            {
                "wall_id": "w1",
                "start_ft": [5.5, 8.4],
                "outcome": "fail",
                "failing_match": "headroom",
            },
            {"wall_id": "w1", "start_ft": [8.6, 12.4], "outcome": "pass"},
        ],
        "missing_evidence_empty": True,
    },
    {"headroom": HEADROOM},
)

# g09: reach uses the routed length. rules.yaml route.confident_reach_ft = 15 (beyond it the
# route is unsure), route.max_ft = 20 (route_length check, at_most; beyond it the route fails).
# Every start before L sits on deck, which rules.yaml ground.allowed excludes, so it fails
# ground_surface; the wall ends at L + W, so L is the last start and the only one on concrete.
# Starts left of the meter overlap its working space (the wall begins at -1).
CONFIDENT, MAX_ROUTE = 15.0, 20.0
# The battery width as rules.yaml writes it. The last start is end - W_RULES in floating point.
# The wall's end is the nearest float to L + W_RULES for which that start is exactly L. For
# L = 14, 15 and 20.2 no such float exists (end has a coarser ulp than L), so the end is the
# smallest float whose last start is at or past L: 14 and 15 land 1.8e-15 past, 20.2 lands 3.6e-15
# past. None of those flips an outcome except that L = 15 tests "past the confident reach", not
# the equality at 15 itself.
W_RULES = 2.583333333333


def exact_end(L):
    base = L + W_RULES
    near = [base]
    for direction in (-math.inf, math.inf):
        x = base
        for _ in range(4):
            x = math.nextafter(x, direction)
            near.append(x)
    exact = [x for x in near if x - W_RULES == L]
    end = (
        min(exact, key=lambda x: abs(x - base))
        if exact
        else min(x for x in near if x - W_RULES >= L)
    )
    assert (end + 1) - 1 == end
    return end


def reach_scene(L, meter_pm=0.0, objects=()):
    end = exact_end(L)
    scene = straight_scene(-1, end, objects=objects)
    scene["meter"]["plus_minus_ft"] = meter_pm
    scene["ground"] = [
        {"type": "deck", "polygon": rect(-1, L, 0, 10), "plus_minus_ft": 0.0},
        concrete(rect(L, end, 0, 10)),
    ]
    return scene


def reach_case(
    cid,
    L,
    route_ft,
    e,
    outcome,
    description,
    objects=(),
    extra_checks=(),
    meter_pm=None,
    spot_start=None,
    at_L_outcome=None,
):
    """`e` is the route check's expected error; the meter's error is `e` unless `meter_pm` says
    otherwise. The spot starts at L unless `spot_start` says otherwise, and the start at L has
    the spot's outcome unless `at_L_outcome` says otherwise."""
    meter_pm = e if meter_pm is None else meter_pm
    spot_start = L if spot_start is None else spot_start
    at_L_outcome = outcome if at_L_outcome is None else at_L_outcome
    at_L = [round(L - 0.001, 3), round(L + 0.001, 3)]
    check = {
        "match": "route_length",
        "outcome": outcome,
        "measured_ft": route_ft,
        "plus_minus_ft": e,
    }
    expect = {"missing_evidence_empty": True}
    if outcome == "fail":
        expect["spot"] = None
        expect["sweep_runs"] = [
            {"wall_id": "w1", "start_ft": at_L, "outcome": "fail", "failing_match": "route_length"}
        ]
    else:
        expect["spot"] = {"wall_id": "w1", "span_within": [spot_start, spot_start + W]}
        expect["checks"] = [check, *extra_checks]
        expect["sweep_runs"] = [{"wall_id": "w1", "start_ft": at_L, "outcome": at_L_outcome}]
    if outcome != "pass":
        expect["decision_not"] = ["pass"]
    case(
        cid,
        golden("09"),
        description,
        reach_scene(L, meter_pm, objects),
        expect,
        {"route_length": MAX_ROUTE},
    )


reach_case(
    "g09-reach-14",
    14.0,
    14.0,
    0.0,
    "pass",
    "Route 14 ft, 1 ft inside the 15 ft confident reach: the route passes.",
)
reach_case(
    "g09-reach-15",
    15.0,
    15.0,
    0.0,
    "unsure",
    "Route exactly at the 15 ft confident reach: not inside it, so unsure.",
)
reach_case(
    "g09-reach-16",
    16.0,
    16.0,
    0.0,
    "unsure",
    "Route 16 ft, between the 15 ft confident reach and the 20 ft maximum: unsure.",
)
reach_case(
    "g09-reach-20",
    20.0,
    20.0,
    0.0,
    "unsure",
    "Route exactly at the 20 ft maximum with no error: 20 + 0 is not below 20 and 20 - 0 "
    "is not above it, so unsure.",
)
reach_case(
    "g09-reach-21",
    21.0,
    21.0,
    0.0,
    "fail",
    "Route 21 ft, 1 ft past the 20 ft maximum: the only start on usable ground fails.",
)
# The meter's error can lengthen the run, since the meter may stand off the wall, but not
# shorten it while it stands on its wall's line (server README, route_length, at 3baa338): a
# start s routes over [s, s + 0.3]. The battery moves with the meter against the ground patches,
# so starts from L - 0.3 may stand on the concrete and are unsure; the nearest is the spot.
reach_case(
    "g09-reach-20p2-pm03",
    20.2,
    20.2 - 0.3 / 2,
    0.3 / 2,
    "unsure",
    "Meter at +/- 0.3 ft. The spot is the first start that may stand on the concrete, "
    "L - 0.3 = 19.9, whose route is 19.9 to 20.2: not above 20, so unsure. The start at 20.2 "
    "routes 20.2 to 20.5, above 20, so it fails.",
    meter_pm=0.3,
    spot_start=20.2 - 0.3,
    at_L_outcome="fail",
)
reach_case(
    "g09-reach-20p4-pm03",
    20.4,
    20.4,
    0.3,
    "fail",
    "Route 20.4 +/- 0.3 ft (meter tap error): 20.4 - 0.3 = 20.1 is above 20, so fail.",
)
# Vertical run: an electrical box standing on the ground at s = [4, 5], 3.5 ft tall, across the
# 1 ft cable run (rules.yaml route.height_ft). route.crossing.elec_box is detour; going under is
# impossible (bottom 0), so the cable climbs 3.5 - 1 = 2.5 ft and comes back down: 5 ft extra.
ebox = {
    "type": "elec_box",
    "wall_id": "w1",
    "span_ft": [4, 5],
    "bottom_ft": 0.0,
    "top_ft": 3.5,
    "source": "tape",
    "plus_minus_ft": 0.0,
}
reach_case(
    "g09-vertical-run",
    CONFIDENT - 4,
    CONFIDENT + 1,
    0.0,
    "unsure",
    "The pad starts 11 ft along the wall, but the cable must climb over a 3.5 ft box at "
    "the 1 ft run height: 11 + 2 x 2.5 = 16 ft routed, past the 15 ft confident reach.",
    objects=[ebox],
    extra_checks=[{"match": "route_path", "outcome": "pass"}],
)

# Drift cases. Walls, meter and objects carry no plus_minus_ft, so rules.yaml's defaults apply:
# errors.meter_ft 0.3 (no drift), errors.wall_ft 0.3 and errors.tap_ft 0.3, each plus
# errors.drift_per_ft 0.16 per foot walked along the walls from the meter. Ground and facing keep
# explicit errors so that only the checks under test move. README.md ("Drift cases") has the
# derivation; the model assumed is: a measured point's error is its default plus drift times its
# walked distance, the battery carries the drift of its edge further from the meter (S2 70ab0b0,
# "checks take it at the far edge"), and a gap or route sums the errors of its two ends.
TAP, WALL_E, METER_E, DRIFT = 0.3, 0.3, 0.3, 0.16
PT = 0.2  # sample points sit 0.2 ft inside a boundary: more than one 2 in sweep step


def drift_scene(x0, x1, objects=()):
    return {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, METER_Y, 0.0], "wall_id": "w1"},
        "walls": [{"id": "w1", "baseline": [[x0, 0.0], [x1, 0.0]]}],
        "objects": list(objects),
        "ground": [concrete(rect(x0, x1, 0, 10))],
        "facing": [facing("w1", (x0, x1), depth=20.0)],
        "coverage": full_coverage((x0, x1)),
    }


def starts(points):
    return [
        {"wall_id": "w1", "start_ft": round(s, 4), "outcome": o, "why": why} for s, o, why in points
    ]


# Cable reach, right of the meter. Route = a (near edge). The wall's error at the far edge,
# e_w = 0.3 + 0.16(a + W), moves it either way; the meter's 0.3 only lengthens it, since the
# meter stands on its wall's line (server README, route_length, at 3baa338). Pass:
# a + 0.3 + e_w < 15 (review line) -> a < (14.4 - 0.16W)/1.16. Fail: a - e_w > 20 ->
# a > (20.3 + 0.16W)/0.84.
R_PASS = (CONFIDENT - METER_E - WALL_E - DRIFT * W) / (1 + DRIFT)
R_FAIL = (MAX_ROUTE + WALL_E + DRIFT * W) / (1 - DRIFT)
reach_points = [
    (5.0, "pass", "route 5 + 1.81 = 6.81 < 15"),
    (R_PASS - PT, "pass", "just inside a + 0.6 + 0.16(a + W) < 15"),
    (R_PASS + PT, "unsure", "just past the 15 ft review line"),
    (18.0, "unsure", "18 + 3.89 > 15; 18 - 3.59 < 20"),
    (R_FAIL - PT, "unsure", "just inside a - 0.3 - 0.16(a + W) <= 20"),
    (R_FAIL + PT, "fail", "just past a - 0.3 - 0.16(a + W) > 20"),
    (25.5, "fail", "25.5 - 4.79 = 20.71 > 20"),
]
case(
    "d-reach-drift-right",
    "rules.yaml errors.drift_per_ft and route.* at origin/t3/server f2705dd; route range at "
    "3baa338; C5 margin rule",
    "Straight wall with default (drifting) errors. The route to a start a right of the meter is "
    "a - (0.3 + 0.16(a + W)) to a + 0.6 + 0.16(a + W): pass below a = 12.058, fail above "
    "a = 24.659, unsure between.",
    drift_scene(-1, 29),
    {"start_outcomes": starts(reach_points)},
    {"route_length": MAX_ROUTE},
)
# Mirror: left of the meter the near edge is the battery's right edge b = a + W, route = -b, and
# the far edge is a.
case(
    "d-reach-drift-left",
    "rules.yaml errors.drift_per_ft and route.* at origin/t3/server f2705dd; route range at "
    "3baa338; C5 margin rule",
    "Mirror of d-reach-drift-right. Left of the meter the route runs to the battery's right edge "
    "b = a + W, so the boundaries sit at a = -12.058 - W and a = -24.659 - W.",
    drift_scene(-29, 1),
    {
        "start_outcomes": starts([(-s - W, o, why + " (b = a + W)") for s, o, why in reach_points]),
    },
    {"route_length": MAX_ROUTE},
)

# Gas clearance. A tapped gas meter on the wall line at s = 4 (error 0.3 + 0.16 x 4 = 0.94). A
# battery right of it has gap d = a - 4 and error 0.94 + (0.3 + 0.16(a + W)) = 1.24 + 0.16(a + W).
# Fail: d + e < 3 -> 1.16a < 5.76 - 0.16W. Pass: d - e > 3 -> 0.84a > 8.24 + 0.16W.
G_AT = 4.0
G_E = TAP + DRIFT * G_AT
G_FAIL = (3.0 + G_AT - G_E - WALL_E - DRIFT * W) / (1 + DRIFT)
G_PASS = (3.0 + G_AT + G_E + WALL_E + DRIFT * W) / (1 - DRIFT)
# Heights above the 1 ft cable run, so the route passes under it and only the clearance is tested:
# with no heights the server (6c7ca23) calls the detour unknown and every route past it unsure.
tap_gas = {
    "type": "gas_meter",
    "wall_id": "w1",
    "span_ft": [G_AT, G_AT],
    "bottom_ft": 2.5,
    "top_ft": 4.0,
    "source": "tap",
}
case(
    "d-gas-drift",
    "rules.yaml errors.tap_ft, wall_ft, drift_per_ft and clearances.gas_ft at origin/t3/server "
    "f2705dd; C5 margin rule",
    "A tapped gas meter at s = 4 on a wall with default errors. For a start a right of it the gap "
    "is (a - 4) +/- (1.24 + 0.16(a + W)): fail below a = 4.609, pass above a = 10.302, unsure "
    "between.",
    drift_scene(-1, 20, objects=[tap_gas]),
    {
        "start_outcomes": starts(
            [
                (3.0, "fail", "battery [3, 5.58] covers the gas meter: gap 0 + 2.13 < 3"),
                (G_FAIL - PT, "fail", "just inside (a - 4) + 1.24 + 0.16(a + W) < 3"),
                (G_FAIL + PT, "unsure", "just past the fail line"),
                (7.5, "unsure", "gap 3.5 +/- 2.85"),
                (G_PASS - PT, "unsure", "just inside (a - 4) - 1.24 - 0.16(a + W) <= 3"),
                (G_PASS + PT, "pass", "just past (a - 4) - 1.24 - 0.16(a + W) > 3"),
                (11.0, "pass", "gap 7 - 3.41 = 3.59 > 3; route 11 + 2.77 < 15"),
            ]
        ),
    },
    {"gas": 3.0, "route_length": MAX_ROUTE},
)


# Marks the files this script owns. Regenerating replaces only those, so a regression case
# written by hand in this folder survives.
GENERATED_BY = "e2e/cases/generate.py"


def main(folder: Path = HERE) -> None:
    ids = {c["id"] for c in CASES}
    for old in folder.glob("*.json"):
        if json.loads(old.read_text()).get("generated_by") == GENERATED_BY:
            old.unlink()
        elif old.stem in ids:
            raise SystemExit(f"{old.name} was written by hand but a generated case has its id")
    for c in CASES:
        case = {"generated_by": GENERATED_BY, **c}
        (folder / f"{c['id']}.json").write_text(json.dumps(case, indent=2) + "\n")
    print(f"wrote {len(CASES)} cases")


if __name__ == "__main__":
    main()
