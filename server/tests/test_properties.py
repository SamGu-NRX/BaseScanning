"""Properties the placement solver must keep for every scene, not only the golden ones.

All scenes are synthetic: one straight wall w1 along z = 0 from x = -16 to 16 (s = x, outward +z),
the meter at s = 0, lawn over one pad and deck (not an allowed surface) everywhere else, so every
spot that can pass lies on the pad.

The rules are the golden test rules with a 0.5 ft filler grid instead of 2 in. That keeps each
solve in the tens of milliseconds, and it thins only the filler: the solver also starts a candidate
at every position where some check can change outcome, which is what these properties exercise.
"""

import copy
from typing import Any

import pytest
from helpers import (
    everything_observed,
    golden_rules,
    pads_ground,
    parsed,
    rect,
    run,
    shared_fixture,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from solver import FAIL, PASS, UNSURE, at_least, evaluate_start, reach_outcome

LO, HI = -16.0, 16.0
GRID = 0.5
STEP = {"sweep": {"step_ft": {"value": GRID, "source": "test: coarse filler grid"}}}
FAST = golden_rules(**STEP)
W = FAST.rules.battery.width_ft.value
D = FAST.rules.battery.depth_ft.value
_C = FAST.rules.clearances
OPENING = _C.opening_ft.value
# The widest plan clearance measured over the ground: how far out the ground must have been seen.
GROUND_REACH = max(_C.gas_ft.value, _C.ac_ft.value, _C.drive_ft.value, _C.pool_ft.value)
SEVERITY = {PASS: 0, UNSURE: 1, FAIL: 2}
# A cut in coverage must overlap the stretch it is meant to hide by at least this much, so the
# unseen area is real geometry and not a floating point sliver.
OVERLAP = 0.05

# No example database: a failure is reproduced from the example pytest prints, so every run and CI
# behave the same whatever an earlier local run found.
PROPERTY = settings(max_examples=40, deadline=None, database=None)


def ft(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(lo, hi, allow_nan=False).map(lambda x: round(x, 3))


# --- scene builders


def gas(g: float, err: float = 0.0) -> dict[str, Any]:
    # top_ft is below the cable height, so the cable passes over it without a detour.
    return {
        "type": "gas_meter",
        "wall_id": "w1",
        "span_ft": [g, g + 1],
        "bottom_ft": 0,
        "top_ft": 0.8,
        "source": "tap",
        "plus_minus_ft": err,
        "footprint": rect(g, g + 1, 0, 1),
    }


def window(w: float, err: float = 0.0) -> dict[str, Any]:
    return {
        "type": "window",
        "wall_id": "w1",
        "span_ft": [w, w + 3],
        "bottom_ft": 3,
        "top_ft": 7,
        "attrs": {"operable": True, "well": False},
        "source": "tap",
        "plus_minus_ft": err,
    }


def ac(a: float, err: float = 0.0) -> dict[str, Any]:
    return {
        "type": "ac",
        "wall_id": "w1",
        "span_ft": [a, a + 3],
        "source": "tap",
        "plus_minus_ft": err,
        "footprint": rect(a, a + 3, 0.5, 3.5),
    }


def elec_box(e: float, err: float = 0.0) -> dict[str, Any]:
    return {
        "type": "elec_box",
        "wall_id": "w1",
        "span_ft": [e, e + 1],
        "bottom_ft": 4,
        "top_ft": 5,
        "source": "tap",
        "plus_minus_ft": err,
    }


def pool(q: float, err: float = 0.0) -> dict[str, Any]:
    return {
        "type": "pool",
        "wall_id": "w1",
        "span_ft": [q, q + 8],
        "source": "tap",
        "plus_minus_ft": err,
        "footprint": rect(q, q + 8, 6, 14),
    }


def scene(
    pad: tuple[float, float],
    objects: list[dict[str, Any]] | None = None,
    facing: float = 9.0,
    headroom: float = 9.0,
    measured_err: float = 0.0,
    wall_err: float = 0.0,
    drive: float | None = None,
) -> dict[str, Any]:
    # Deck past each end too, where a view past a limit end sees both sides: seen ground with
    # no recorded surface may be a driveway (drive_clearance), so a fully observed scene records
    # it.
    ground = [
        *pads_ground([pad], LO, HI),
        {"type": "deck", "polygon": rect(LO - 40, LO, -40, 40)},
        {"type": "deck", "polygon": rect(HI, HI + 40, -40, 40)},
    ]
    if drive is not None:
        ground.append({"type": "drive", "polygon": rect(drive, drive + 4, 0, 30)})
    return {
        "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w1", "plus_minus_ft": 0},
        "walls": [
            {"id": "w1", "baseline": [[LO, 0], [HI, 0]], "height_ft": 9, "plus_minus_ft": wall_err}
        ],
        "objects": objects or [],
        "ground": ground,
        "overheads": [
            {
                "wall_id": "w1",
                "span_ft": [LO, HI],
                "clearance_ft": headroom,
                "plus_minus_ft": measured_err,
            }
        ],
        "facing": [
            {
                "wall_id": "w1",
                "span_ft": [LO, HI],
                "depth_ft": facing,
                "plus_minus_ft": measured_err,
            }
        ],
        # Seen well past both wall ends, so no spot on the pad is unsure for lack of coverage.
        "coverage": everything_observed(-60, 60, 30),
    }


def _mirror_span(span: list[float]) -> list[float]:
    return [-span[1], -span[0]]


def _mirror_points(points: list[list[float]]) -> list[list[float]]:
    return [[-x, z] for x, z in reversed(points)]


def mirror(raw: dict[str, Any]) -> dict[str, Any]:
    """The same scene reflected left for right (x -> -x, so s -> -s). Baselines are reversed so
    they still run left to right as seen from outside, which keeps outward pointing out."""
    assert "keyframes" not in raw, "mirroring camera poses is not implemented"
    m = copy.deepcopy(raw)
    x, y, z = m["meter"]["pos"]
    m["meter"]["pos"] = [-x, y, z]
    m["walls"] = [{**w, "baseline": _mirror_points(w["baseline"])} for w in reversed(m["walls"])]
    for o in m.get("objects", []):
        o["span_ft"] = _mirror_span(o["span_ft"])
        if "footprint" in o:
            o["footprint"] = _mirror_points(o["footprint"])
    for g in m.get("ground", []):
        g["polygon"] = _mirror_points(g["polygon"])
    for item in m.get("overheads", []) + m.get("facing", []):
        item["span_ft"] = _mirror_span(item["span_ft"])
    coverage = m.get("coverage", {})
    for obs in coverage.get("observed", []):
        obs["span_ft"] = _mirror_span(obs["span_ft"])
    ends = coverage.get("ends", {})
    if ends:
        coverage["ends"] = {
            side: ends[other]
            for side, other in (("left", "right"), ("right", "left"))
            if other in ends
        }
    return m


def outcomes(result: dict[str, Any]) -> dict[str, str]:
    return {c["id"]: c["outcome"] for c in result["checks"]}


# --- (b) equality is unsure: truth tables


@pytest.mark.parametrize(
    ("value", "error", "threshold", "expected"),
    [
        (4.0, 0.0, 3.0, PASS),
        (2.0, 0.0, 3.0, FAIL),
        (3.0, 0.0, 3.0, UNSURE),  # exactly on the line
        (3.6, 0.5, 3.0, PASS),
        (3.5, 0.5, 3.0, UNSURE),  # value - error on the line
        (2.5, 0.5, 3.0, UNSURE),  # value + error on the line
        (2.4, 0.5, 3.0, FAIL),
        (3.0, 1.0, 3.0, UNSURE),
        (0.1 + 0.2, 0.0, 0.3, UNSURE),  # 0.30000000000000004: float noise must not pass it
        (0.0, 0.0, 0.0, UNSURE),  # touching: the meter working space check
        (-0.1, 0.0, 0.0, FAIL),
    ],
)
def test_at_least_truth_table(value: float, error: float, threshold: float, expected: str) -> None:
    assert at_least(value, error, threshold) == expected


@pytest.mark.parametrize(
    ("length", "error", "expected"),
    [
        (10.0, 0.0, PASS),
        (15.0, 0.0, UNSURE),  # exactly the confident reach
        (17.0, 0.0, UNSURE),  # between confident reach and maximum
        (20.0, 0.0, UNSURE),  # exactly the maximum
        (21.0, 0.0, FAIL),
        (14.0, 0.5, PASS),
        (14.5, 0.5, UNSURE),  # length + error on the confident line
        (20.5, 0.5, UNSURE),  # length - error on the maximum
        (21.0, 0.5, FAIL),
        (19.9, 0.5, UNSURE),
    ],
)
def test_reach_outcome_truth_table(length: float, error: float, expected: str) -> None:
    assert reach_outcome(length, error, 15.0, 20.0) == expected


@PROPERTY
@given(t=ft(0, 100), e=ft(0, 10))
def test_at_least_on_either_error_edge_is_unsure(t: float, e: float) -> None:
    assert at_least(t, 0.0, t) == UNSURE
    assert at_least(t + e, e, t) == UNSURE
    assert at_least(t - e, e, t) == UNSURE


@PROPERTY
@given(confident=ft(0, 50), extra=ft(0, 50), e=ft(0, 5))
def test_reach_on_either_line_is_unsure(confident: float, extra: float, e: float) -> None:
    maximum = confident + extra
    assert reach_outcome(confident, 0.0, confident, maximum) == UNSURE
    assert reach_outcome(maximum, 0.0, confident, maximum) == UNSURE
    assert reach_outcome(confident - e, e, confident, maximum) == UNSURE
    assert reach_outcome(maximum + e, e, confident, maximum) == UNSURE


# --- (a) missing coverage never passes

BANDS = ["wall", "ground", "overhead", "facing"]


def needed(band: str, p0: float, p1: float) -> tuple[float, float]:
    """The stretch of `band` every candidate on the pad [p0, p1] (right of the meter) depends on.

    A candidate starts at s0 in [p0, p1 - W]. The wall band carries its cable route (0 to s0), its
    backing (s0 to s0 + W) and the opening clearance around it (to s0 + W + opening); the ground
    band everything within the widest ground clearance of the footprint; overhead and facing only
    the footprint's own stretch. The intersection over all candidates is what is returned.
    """
    if band == "wall":
        return (0.0, p0 + W + OPENING)
    if band == "ground":
        return (p1 - W - GROUND_REACH, p0 + W + GROUND_REACH)
    return (p1 - W, p0 + W)


def _observed(raw: dict[str, Any], band: str) -> dict[str, Any]:
    return next(o for o in raw["coverage"]["observed"] if o["band"] == band)


@st.composite
def pads(draw: st.DrawFn) -> tuple[float, float]:
    p0 = draw(ft(2.0, 9.0))
    return (p0, round(p0 + draw(ft(W + 0.2, W + 2.0)), 3))


@st.composite
def coverage_cuts(draw: st.DrawFn) -> tuple[tuple[float, float], str, str, float, float]:
    pad = draw(pads())
    band = draw(st.sampled_from(BANDS))
    modes = ["remove", "hole", "cut_left", "cut_right"] + (["shallow"] * (band == "ground"))
    mode = draw(st.sampled_from(modes))
    lo, hi = needed(band, *pad)
    assert hi - lo > 2 * OVERLAP
    at = draw(ft(lo + OVERLAP, hi - OVERLAP))
    size = draw(ft(2 * OVERLAP, 4.0))
    return pad, band, mode, at, size


def cut(raw: dict[str, Any], band: str, mode: str, at: float, size: float) -> dict[str, Any]:
    raw = copy.deepcopy(raw)
    observed = raw["coverage"]["observed"]
    obs = _observed(raw, band)
    a, b = obs["span_ft"]
    if mode == "remove":
        observed.remove(obs)
    elif mode == "hole":
        observed.remove(obs)
        for span in ([a, at - size / 2], [at + size / 2, b]):
            observed.append({**obs, "span_ft": span})
    elif mode == "cut_left":
        obs["span_ft"] = [at, b]
    elif mode == "cut_right":
        obs["span_ft"] = [a, at]
    else:  # shallow: the ground was seen, but not as far out as the clearances reach
        obs["out_ft"] = round(max(0.0, min(at, D + GROUND_REACH - OVERLAP)), 3)
    return raw


def test_the_uncut_pad_scene_passes() -> None:
    # The coverage property below means something only because the same scene, fully observed,
    # passes. Checked at the generator's extremes.
    for pad in [(2.0, 2.0 + W + 0.2), (9.0, 9.0 + W + 2.0), (6.0, 9.0)]:
        for raw in (scene(pad), mirror(scene(pad))):
            result = run(raw, FAST)
            assert result["decision"] == PASS, (pad, result["summary"])


@PROPERTY
@given(case=coverage_cuts(), flip=st.booleans())
def test_missing_coverage_never_passes(case, flip: bool) -> None:
    pad, band, mode, at, size = case
    raw = cut(scene(pad), band, mode, at, size)
    if flip:
        raw = mirror(raw)
    result = run(raw, FAST)
    assert result["stats"]["pass"] == 0, result["summary"]
    assert result["decision"] != PASS
    assert result["spot"] is None or result["spot"]["outcome"] != PASS


# --- (c) left and right mirror


@st.composite
def layouts(draw: st.DrawFn) -> dict[str, Any]:
    p0, p1 = draw(pads())
    if draw(st.booleans()):
        p0, p1 = -p1, -p0
    objects = []
    # Nonzero errors put some best spots inside an error band, so manual_review is reached too.
    g = draw(st.one_of(st.none(), ft(-14.0, 13.0)))
    if g is not None:
        objects.append(gas(g, draw(ft(0.0, 0.5))))
    w = draw(st.one_of(st.none(), ft(-14.0, 11.0)))
    if w is not None:
        objects.append(window(w, draw(ft(0.0, 0.5))))
    return scene((p0, p1), objects)


def assert_mirrored(a: dict[str, Any], b: dict[str, Any], tol: float) -> None:
    """`b` is the answer for the mirror of `a`'s scene, positions equal within `tol`."""
    assert a["decision"] == b["decision"]
    assert (a["spot"] is None) == (b["spot"] is None)
    if a["spot"] is None:
        near_a, near_b = a["nearest_considered"], b["nearest_considered"]
        assert near_a["route_length_ft"] == pytest.approx(near_b["route_length_ft"], abs=tol)
        return
    assert outcomes(a) == outcomes(b)
    assert a["route"]["length_ft"] == pytest.approx(b["route"]["length_ft"], abs=tol)
    s0, s1 = a["spot"]["span_ft"]
    assert b["spot"]["span_ft"] == pytest.approx([-s1, -s0], abs=tol)


# Start positions are anchored at the meter in left-edge and right-edge form, so a scene and its
# mirror try mirrored starts and must choose mirrored spots, to rounding.
EXACT = 1e-6


@PROPERTY
@given(raw=layouts())
def test_mirror_gives_the_mirrored_answer(raw: dict[str, Any]) -> None:
    assert_mirrored(run(raw, FAST), run(mirror(raw), FAST), EXACT)


def test_mirror_of_a_scene_whose_pad_ends_at_its_own_edges() -> None:
    # The pad's own edges bound the passing stretch here (standing next to deck is allowed), so
    # the best spot is exact and the mirror must match to rounding.
    raw = scene((6.0, 9.0), [gas(-5.0), window(12.0)])
    a, b = run(raw, FAST), run(mirror(raw), FAST)
    assert a["decision"] == PASS
    assert_mirrored(a, b, EXACT)


# Regression: the grid used to be anchored at the wall's left end, so with a clearance line
# bounding the pad the chosen start was 6.316667 and the mirrored one ended at -6.358333.
def test_mirror_is_exact_when_a_clearance_bounds_the_pad() -> None:
    raw = shared_fixture()
    raw["objects"] = [gas(2.3)]
    a, b = run(raw), run(mirror(raw))
    assert a["decision"] == PASS
    assert_mirrored(a, b, EXACT)


# --- (d) monotonicity: more room never hurts, a stricter rule never helps

MOVERS = {"gas": gas, "ac": ac, "pool": pool, "window": window, "elec_box": elec_box}


@st.composite
def cluttered(draw: st.DrawFn) -> dict[str, Any]:
    """Every kind of clearance subject at a random place, with random errors."""
    err = ft(0.0, 0.5)
    positions = {name: draw(ft(-14.0, 12.0)) for name in MOVERS}
    errors = {name: draw(err) for name in MOVERS}
    return {
        "pad": draw(pads()),
        "positions": positions,
        "errors": errors,
        "facing": draw(ft(3.0, 9.0)),
        "headroom": draw(ft(5.0, 9.0)),
        "measured_err": draw(err),
        "wall_err": draw(ft(0.0, 0.3)),
        "drive": draw(st.one_of(st.none(), ft(-15.0, 11.0))),
        "s0": draw(ft(-16.0, 16.0)),
    }


def build(c: dict[str, Any], **changes: Any) -> dict[str, Any]:
    c = {**c, **changes}
    objects = [MOVERS[n](c["positions"][n], c["errors"][n]) for n in MOVERS]
    return scene(
        c["pad"],
        objects,
        facing=c["facing"],
        headroom=c["headroom"],
        measured_err=c["measured_err"],
        wall_err=c["wall_err"],
        drive=c["drive"],
    )


def evaluate(raw: dict[str, Any], s0: float, loaded=FAST) -> dict[str, str]:
    return {c.id: c.outcome for c in evaluate_start(parsed(raw, loaded), loaded, s0).checks}


def never_more_severe(a: dict[str, str], b: dict[str, str]) -> None:
    """No check is more severe in `a` than in `b` (so no pass in b is a fail in a)."""
    worse = {k: (b[k], a[k]) for k in b if SEVERITY[a[k]] > SEVERITY[b[k]]}
    assert not worse, worse


@PROPERTY
@given(c=cluttered(), mover=st.sampled_from(sorted(MOVERS)), delta=ft(0.01, 10.0))
def test_moving_an_object_away_never_hurts(c: dict[str, Any], mover: str, delta: float) -> None:
    s0 = c["s0"]
    width = {"gas": 1.0, "ac": 3.0, "pool": 8.0, "window": 3.0, "elec_box": 1.0}[mover]
    at = c["positions"][mover]
    away = 1.0 if at + width / 2 >= s0 + W / 2 else -1.0
    moved = {**c["positions"], mover: round(at + away * delta, 3)}
    never_more_severe(evaluate(build(c, positions=moved), s0), evaluate(build(c), s0))


@PROPERTY
@given(
    c=cluttered(),
    which=st.sampled_from(["facing", "headroom"]),
    delta=ft(0.01, 5.0),
)
def test_more_facing_depth_or_headroom_never_hurts(
    c: dict[str, Any], which: str, delta: float
) -> None:
    s0 = c["s0"]
    more = build(c, **{which: round(c[which] + delta, 3)})
    never_more_severe(evaluate(more, s0), evaluate(build(c), s0))


RULE_KEYS = [
    ("clearances", "gas_ft"),
    ("clearances", "ac_ft"),
    ("clearances", "opening_ft"),
    ("clearances", "drive_ft"),
    ("clearances", "pool_ft"),
    ("clearances", "wall_equipment_ft"),
    ("facing", "min_ft"),
    ("headroom", "min_ft"),
    ("meter_working_space", "width_ft"),
    ("meter_working_space", "depth_ft"),
]


@PROPERTY
@given(c=cluttered(), key=st.sampled_from(RULE_KEYS), delta=ft(0.01, 5.0))
def test_a_stricter_rule_never_helps(c: dict[str, Any], key: tuple[str, str], delta: float) -> None:
    section, name = key
    base = getattr(getattr(FAST.rules, section), name).value
    stricter = golden_rules(
        **STEP, **{section: {name: {"value": base + delta, "source": "test: stricter"}}}
    )
    raw, s0 = build(c), c["s0"]
    # Same candidate, same scene; the scene is parsed again because its modelled outdoor area
    # grows with the widest clearance.
    never_more_severe(evaluate(raw, s0, FAST), evaluate(raw, s0, stricter))
