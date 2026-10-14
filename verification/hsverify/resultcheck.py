"""Judge a placement result (contract C2) against its scene (C1) and the decision rule (C5).

Everything here is pure: a scene and a result go in, a list of problems comes out. The
checks hold for any policy and any rule values, so they can run on every response:

- decision consistency: what `pass`, `reject` and `manual_review` each require of the rest
  of the record;
- the margin rule: a check's outcome must follow from its measurement, error and threshold
  (PASS only when the margin beats the error, and clears the review line when the check has
  one; FAIL only when past the threshold by more than the error; otherwise UNSURE);
- coverage: no battery position passes without the coverage each passing check needs, as the
  server's README ("What settles each check") states it: the wall seen high enough over the
  clearance's reach, the ground out to the battery's depth plus the reach, the facing and
  overhead bands unless measured, and the cable route. The reach includes the battery's
  position error, which depends on how the wall was found. Only points within reach whatever
  the wall's shape are required, so a correct server is not flagged;
- requests: every check left unsure for coverage is named for each band it lacks, and nothing
  is requested that was already seen as far as the request needs;
- bookkeeping: counts add up, the input hash matches what was sent, the spot's offset from
  the meter matches its centre.

Case expectations (a scene plus the outcomes geometry forces) and the mirror transform used
for the left-right symmetry check also live here.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import yaml
from jsonschema import Draft202012Validator

EPS = 1e-6
NUMBER_TOL_FT = 1e-5
# Bound verifier work even when a server span or rules step requests billions of samples.
MAX_SWEEP_SAMPLES = 10_000
# Spot centres and offsets are rounded by the server; a hundredth of a foot is 1/8 inch.
OFFSET_TOL_FT = 0.01


@dataclass(frozen=True)
class Need:
    """One piece of coverage a check needs around a battery over [lo, hi] (server/README.md at the
    tested ref, "What settles each check"):

    - `ground`: ground over [lo - r, hi + r], out to depth + r in front of the battery;
    - `wall`: the wall band over [lo - r, hi + r], seen higher than `height`;
    - `facing`, `overhead`: the band over [lo - r, hi + r]; where no measurement (`facing`,
      `overheads`) covers a stretch, the band's `out_ft` there must be beyond `height`.

    r is `radius`, plus the battery's position error when `widen` (battery_error: the server's
    checks see the wall's error at the battery's far edge, server/solver.py at 903d86f).
    """

    band: str
    radius: float = 0.0
    height: float = 0.0
    widen: bool = True
    # Widened by the battery's error along the walls only (along_error), not in plan: spans
    # measured in s move with the meter as the battery does (server 3baa338, along_err).
    along: bool = False


# Scene lists whose entries settle facing_gap and headroom where they cover the battery.
MEASUREMENTS = {"facing": "facing", "overhead": "overheads"}
# rules.yaml error keys for a wall's `source`; a wall without one is tapped.
WALL_ERROR = {"tap": "wall", "mesh": "mesh", "plane": "plane"}


@dataclass(frozen=True)
class RuleSet:
    """What the checks here need from the server's rules.yaml at the tested ref."""

    width_ft: float
    depth_ft: float
    # check id -> the coverage its pass needs
    needs: dict[str, tuple[Need, ...]]
    # default error by object source, for walls (tap, mesh, plane) and the meter, and drift_per_ft
    errors: dict[str, float]
    # the height up the wall the cable route must be seen, from the meter to the battery
    route_height_ft: float = 0.0
    # the rules' sweep.step_ft: the spacing at which a passing sweep run's starts are sampled
    step_ft: float | None = None
    # the rules' sweep.wall_join_ft: the most two walls' ends may be apart and still meet
    wall_join_ft: float | None = None
    # the rules' battery.height_ft: a wall that declares height_ft must be taller
    battery_height_ft: float | None = None
    # the rules' headroom.min_ft: how high a wall view without out_ft reached
    headroom_ft: float | None = None
    corner_allowance_ft: float | None = None

    @classmethod
    def from_yaml(cls, text: str) -> RuleSet:
        data = yaml.safe_load(text)

        def value(*path: str, default: float | None = None) -> float:
            node: Any = data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    if default is not None:
                        return default
                    raise ValueError(f"rules.yaml has no {'.'.join(path)}")
                node = node[key]
            if node is None and default is not None:
                return default
            return float(node["value"] if isinstance(node, dict) else node)

        depth = value("battery", "depth_ft")
        headroom = value("headroom", "min_ft")
        # Openings need the wall seen to headroom height, or to a lower exempt height the rules set.
        opening_height = min(
            headroom, value("openings", "exempt_bottom_above_ft", default=headroom)
        )

        def r(key: str) -> float:
            return value("clearances", key)

        def reach(key: str) -> Need:
            return Need("ground", r(key))

        needs = {
            "ground_surface": (Need("ground", 0.0),),
            "wall_backing": (Need("wall", 0.0, value("battery", "height_ft")),),
            "gas_clearance": (reach("gas_ft"), Need("wall", r("gas_ft"), headroom)),
            "battery_clearance": (reach("battery_ft"), Need("wall", r("battery_ft"), headroom)),
            "ac_clearance": (reach("ac_ft"),),
            "drive_clearance": (reach("drive_ft"),),
            "pool_clearance": (reach("pool_ft"),),
            "opening_clearance": (Need("wall", r("opening_ft"), opening_height),),
            # Widened by the battery's error from 3baa338 (server README, wall_equipment_above).
            "wall_equipment_above": (Need("wall", r("wall_equipment_ft"), headroom, along=True),),
            "facing_gap": (Need("facing", 0.0, depth + value("facing", "min_ft"), along=True),),
            "headroom": (Need("overhead", 0.0, headroom, along=True),),
        }
        errors = {
            name: value("errors", f"{name}_ft")
            for name in ("tap", "vlm", "tape", "wall", "mesh", "plane", "meter")
        }
        errors["drift_per_ft"] = value("errors", "drift_per_ft")
        return cls(
            value("battery", "width_ft"),
            depth,
            needs,
            errors,
            value("route", "height_ft"),
            value("sweep", "step_ft"),
            value("sweep", "wall_join_ft"),
            value("battery", "height_ft"),
            headroom,
            value("route", "corner_allowance_ft"),
        )


def schema_errors(instance: Any, schema: dict) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]


# --- Coverage --------------------------------------------------------------------------------


def reached(entry: dict) -> float:
    """How far a facing, overhead or ground entry's view reached: its `out_ft`, or without one,
    none for ground and all the way for facing (to whatever faces the wall) and overhead (clear
    to the sky). A wall entry without one is handled in `observed`."""
    default = 0.0 if entry["band"] == "ground" else math.inf
    return entry.get("out_ft", default)


# The server's float tolerance (server/scene.py EPS), for comparisons that must agree with it
# exactly: where walls meet, whether a declared wall is taller, and the wall view default.
SERVER_EPS = 1e-9


def observed(
    scene: dict,
    band: str,
    min_out_ft: float = 0.0,
    beyond: bool = False,
    wall_default_ft: float | None = None,
) -> list[tuple[float, float]]:
    """Merged observed s-intervals for a band, counting only entries whose view reached
    `min_out_ft` (out from the wall for ground and facing, up for wall and overhead). With
    `beyond`, the view must pass it strictly, as the server requires of a wall seen "higher
    than" a height (server/scene.py at 6c7ca23).

    A wall entry without `out_ft` reached headroom height (`wall_default_ft`, the rules'
    headroom.min_ft) and settles any height up to it inclusively, `beyond` or not; S2 chose
    this category default at 6200098 (server/scene.py `observed_intervals`). None keeps the
    old reading, all the way up."""
    spans = []
    for entry in scene.get("coverage", {}).get("observed", []):
        if entry["band"] != band:
            continue
        if band == "wall" and "out_ft" not in entry and wall_default_ft is not None:
            if min_out_ft > wall_default_ft + SERVER_EPS:
                continue
            spans.append(tuple(sorted(entry["span_ft"])))
            continue
        far = reached(entry)
        # Without `beyond` the view must reach min_out_ft itself: a request's out_ft is set just
        # past a rule's line (server _above, 1e-6 over it), so any looser slack than the
        # server's own would count a view at the line as reaching past it.
        if (far <= min_out_ft) if beyond else (far + SERVER_EPS < min_out_ft):
            continue
        spans.append(tuple(sorted(entry["span_ft"])))
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def covers(intervals: list[tuple[float, float]], a: float, b: float, slack: float = 0.0) -> bool:
    return any(lo - slack <= a + EPS and b - EPS <= hi + slack for lo, hi in intervals)


# A gap in a 1D band narrower than this is rounding between the capture's spans and the unrolled
# walls, not an unseen stretch (server/scene.py COVERAGE_TOLERANCE_FT and missing() at 3baa338).
COVERAGE_TOLERANCE_FT = 0.01


def gaps_over(
    intervals: list[tuple[float, float]], a: float, b: float
) -> list[tuple[float, float]]:
    """The parts of [a, b] no interval covers, as the server's missing() finds them: gaps
    narrower than COVERAGE_TOLERANCE_FT don't count."""
    gaps, at = [], a
    for lo, hi in sorted(intervals):
        if hi <= at:
            continue
        if lo > at:
            gaps.append((at, min(lo, b)))
        at = max(at, hi)
        if at >= b:
            break
    if at < b:
        gaps.append((at, b))
    return [(x, y) for x, y in gaps if y - x >= COVERAGE_TOLERANCE_FT]


# --- Invariants ------------------------------------------------------------------------------


def margin_problem(check: dict) -> str | None:
    """The C5 rule for one check, when its numbers are all present.

    PASS needs a clear pass and FAIL a clear fail. A check with a review band
    (`review_threshold_ft`) passes only when it also clears that line. UNSURE for another cause
    (unobserved area, unknown attribute, review rule) may sit on numbers that would pass, never
    on numbers that clearly fail. UNSURE labelled `margin` must lie within its error of a line:
    C2 defines that cause as "measured inside the error band".
    """
    m, e, t, cmp = (
        check.get(k) for k in ("measured_ft", "plus_minus_ft", "threshold_ft", "comparison")
    )
    if m is None or e is None or t is None or cmp is None:
        return None
    review = check.get("review_threshold_ft")
    lines = [t] if review is None else [t, review]
    if cmp == "at_least":
        clear_pass = all(m - e > x + EPS for x in lines)
        clear_fail = m + e < t - EPS
    else:
        clear_pass = all(m + e < x - EPS for x in lines)
        clear_fail = m - e > t + EPS
    expected = "pass" if clear_pass else "fail" if clear_fail else "unsure"
    outcome, cause = check["outcome"], check.get("unsure_cause")
    band = f" ({cmp} {t}" + (f", review {review})" if review is not None else ")")
    if outcome == "unsure" and cause == "margin" and expected == "unsure":
        if not any(abs(m - x) <= e + EPS for x in lines):
            return (
                f"check {check['id']}: unsure_cause margin, but {m} ± {e} is not within its "
                f"error of any line{band}; a review band is rule_requires_review"
            )
        return None
    other_cause = outcome == "unsure" and cause not in (None, "margin")
    if outcome == expected or (other_cause and not clear_fail):
        return None
    # The EPS above allows for float noise; a PASS may also clear its line by less. The server
    # rounds each number to 6 decimals and rule lines sit on that grid, and rounding keeps order:
    # a true margin of zero or less can't come out positive. So any positive reported margin
    # proves the pass (3baa338 places spots at the edge of a pass, a margin of 1e-6).
    if outcome == "pass" and expected == "unsure":
        if cmp == "at_least":
            proven = all(m - e - x > SERVER_EPS for x in lines)
        else:
            proven = all(x - (m + e) > SERVER_EPS for x in lines)
        if proven:
            return None
    because = f" ({cause})" if cause else ""
    return (
        f"check {check['id']}: measured {m} ± {e}{band} should be {expected}, "
        f"server says {outcome}{because}"
    )


def invariant_problems(
    scene: dict, result: dict, sent: bytes | None = None, rules: RuleSet | None = None
) -> list[str]:
    if rules is not None and (sampling := sweep_sampling_problems(scene, result, rules)):
        return sampling
    problems: list[str] = []
    decision = result["decision"]
    spot = result.get("spot")
    checks = result.get("checks", [])

    if decision == "pass":
        if spot is None or spot.get("outcome") != "pass":
            problems.append("decision pass without a passing spot")
        if not checks:
            problems.append("decision pass with no checks")
        not_passing = [c["id"] for c in checks if c["outcome"] != "pass"]
        if not_passing:
            problems.append(f"decision pass with checks not passing: {not_passing}")
        if result.get("missing_evidence"):
            problems.append("decision pass while asking for more evidence")
        if not result["policy"].get("auto_approve"):
            problems.append("decision pass although policy.auto_approve is false")
    if decision == "reject":
        if spot is not None:
            problems.append("decision reject but a spot is given")
        # A reject says no start can pass or be settled by a person: every start fails.
        for outcome in ("pass", "unsure"):
            runs = [r["start_ft"] for r in result.get("sweep", []) if r["outcome"] == outcome]
            if runs:
                problems.append(f"decision reject but the sweep has {outcome} starts {runs}")
        if result.get("missing_evidence"):
            problems.append("decision reject while asking for more evidence")
    if decision == "manual_review" and not result.get("reasons"):
        problems.append("decision manual_review without reasons")
    # A person reviews a spot that may pass; one that fails is a reject's, not theirs.
    if decision == "manual_review" and spot is not None and spot["outcome"] == "fail":
        problems.append("decision manual_review with a failing spot")
    # A spot's outcome is its checks': a pass has every check passing, an unsure spot none failing.
    if spot is not None and spot["outcome"] in ("pass", "unsure"):
        for c in checks:
            if c["outcome"] == "fail" or (spot["outcome"] == "pass" and c["outcome"] != "pass"):
                verb = "fails" if c["outcome"] == "fail" else f"is {c['outcome']}"
                problems.append(f"the spot is {spot['outcome']} but its check {c['id']} {verb}")
    if (spot is None) != (result.get("route") is None):
        problems.append("spot and route must be both null or both present")

    for check in checks:
        problem = margin_problem(check)
        if problem:
            problems.append(problem)
        cause = check.get("unsure_cause")
        if check["outcome"] == "unsure" and cause is None:
            problems.append(f"check {check['id']}: unsure without unsure_cause")
        if check["outcome"] != "unsure" and cause is not None:
            problems.append(f"check {check['id']}: unsure_cause on a {check['outcome']} check")

    stats = result["stats"]
    if stats["pass"] + stats["unsure"] + stats["fail"] != stats["candidates"]:
        problems.append(
            f"stats: pass {stats['pass']} + unsure {stats['unsure']} + fail {stats['fail']} "
            f"!= candidates {stats['candidates']}"
        )
    if sent is not None and stats["input_sha256"] != hashlib.sha256(sent).hexdigest():
        problems.append("stats.input_sha256 does not match the scene bytes sent")

    if spot is not None:
        mx, _, mz = scene["meter"]["pos"]
        want = [spot["center"][0] - mx, spot["center"][1] - mz]
        got = spot["meter_offset_ft"]
        if max(abs(want[0] - got[0]), abs(want[1] - got[1])) > OFFSET_TOL_FT:
            problems.append(f"spot.meter_offset_ft {got} != centre - meter {want}")

    problems += spot_sweep_problems(result)
    if rules is not None:
        problems += footprint_problems(scene, result, rules)
        problems += spot_position_problems(scene, result, rules)
    problems += missing_evidence_problems(scene, result, rules)
    if rules is not None:
        problems += coverage_problems(scene, result, rules)
        problems += declared_height_problems(scene, result, rules)
        problems += dropped_check_problems(scene, result, rules)
    return problems


# Seen ground grows by this in every direction before it is compared, closing gaps under the
# 0.01 ft coverage tolerance (server/scene.py SEEN_GROWTH_FT at 3baa338).
SEEN_GROWTH_FT = 0.005 - 1e-4

# Walls and baseline points within this of one straight line are one straight stretch
# (server/scene.py COLLINEAR_FT and _join_straight_walls at a726199). Also the slack on a
# footprint's ends, since s here and the server's s can differ by a merged point's offset.
COLLINEAR_FT = 0.05


def straight_pieces(scene: dict, rules: RuleSet) -> list[tuple[float, float]]:
    """The s-extent of each straight stretch of scanned wall a battery can back onto: baseline
    segments joined where the next one starts at the previous one's end and continues its line,
    within a wall or across two walls that meet."""
    pieces: list[list] = []  # [s0, s1, start point, end point]
    for _, s0, s1, a, b in segments_s(scene, rules) or []:
        if pieces:
            prev = pieces[-1]
            length = math.dist(prev[2], prev[3])
            if length > EPS and math.dist(prev[3], a) <= COLLINEAR_FT:
                ux, uz = (prev[3][0] - prev[2][0]) / length, (prev[3][1] - prev[2][1]) / length
                off = abs((b[0] - prev[2][0]) * uz - (b[1] - prev[2][1]) * ux)
                ahead = (b[0] - prev[3][0]) * ux + (b[1] - prev[3][1]) * uz > 0
                if off <= COLLINEAR_FT and ahead and abs(s0 - prev[1]) <= COLLINEAR_FT:
                    prev[1], prev[3] = s1, b
                    continue
        pieces.append([s0, s1, a, b])
    return [(p[0], p[1]) for p in pieces]


def footprint_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    """A battery that passes or is left to a person backs onto one straight stretch of scanned
    wall along its whole width; a footprint past the stretch's end (a corner, the end of the
    wall, a gap) can't sit flush, so the server fails it (server/solver.py check_backing)."""
    if sampling := sweep_sampling_problems(scene, result, rules):
        return sampling
    pieces = straight_pieces(scene, rules)
    if not pieces:
        return []

    def off_wall(lo: float, hi: float, outcome: str, wall_id: str) -> str | None:
        # The stretch it starts on, or else ends on: the one it would back onto. A start on a
        # boundary belongs to the stretch it runs onto, an end to the one it runs off.
        piece = next((p for p in pieces if p[0] - EPS <= lo < p[1] - EPS), None) or next(
            (p for p in pieces if p[0] + EPS < hi <= p[1] + EPS), None
        )
        if piece is None:
            return "is not on any scanned wall"
        if lo < piece[0] - COLLINEAR_FT or hi > piece[1] + COLLINEAR_FT:
            return f"runs past its straight wall [{piece[0]:.2f}, {piece[1]:.2f}]"
        if outcome != "pass":
            return None
        # A pass also keeps its position error clear of the stretch's ends: within it the
        # battery may not sit flush, and the server leaves it unsure (check_backing).
        e = battery_error(scene, rules, wall_id, lo, hi)
        for end, clear in ((piece[0], lo - e - piece[0]), (piece[1], piece[1] - hi - e)):
            if clear < -COLLINEAR_FT:
                return f"ends within its error ({e:.2f} ft) of its straight wall's end at {end:.2f}"
        return None

    problems = []
    spot = result.get("spot")
    if spot is not None and spot["outcome"] in ("pass", "unsure"):
        lo, hi = spot["span_ft"]
        if (why := off_wall(lo, hi, spot["outcome"], spot["wall_id"])) is not None:
            problems.append(f"spot [{lo:.2f}, {hi:.2f}] {why}")
    for run in result.get("sweep", []):
        if run["outcome"] not in ("pass", "unsure"):
            continue
        for start in run_starts(run["start_ft"], rules.step_ft):
            battery = (start, start + rules.width_ft)
            if (why := off_wall(*battery, run["outcome"], run["wall_id"])) is not None:
                problems.append(f"sweep {run['outcome']} start {start:.2f} {why}")
                break
    return problems


def spot_position_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    """Where the app places the battery must be where the result says it stands: spot.center is
    its footprint's centre, and that is the middle of span_ft on the wall, half the battery's
    depth out. meter_offset_ft is checked against center elsewhere, so a centre moved together
    with its offset is caught here."""
    spot = result.get("spot")
    if spot is None:
        return []
    problems = []
    cx, cz = spot["center"]
    corners = spot.get("footprint") or []
    if corners:
        fx = sum(pt[0] for pt in corners) / len(corners)
        fz = sum(pt[1] for pt in corners) / len(corners)
        if math.dist((cx, cz), (fx, fz)) > OFFSET_TOL_FT:
            problems.append(
                f"spot.center [{cx:.2f}, {cz:.2f}] is not its footprint's centre "
                f"[{fx:.2f}, {fz:.2f}]"
            )
    lo, hi = spot["span_ft"]
    plan = GroundPlan(scene, rules)
    want = plan.point((lo + hi) / 2, (lo + hi) / 2, rules.depth_ft / 2)
    if want is not None and math.dist((cx, cz), want) > COLLINEAR_FT:
        problems.append(
            f"spot.center [{cx:.2f}, {cz:.2f}] is not the centre of span [{lo:.2f}, {hi:.2f}] "
            f"on its wall, [{want[0]:.2f}, {want[1]:.2f}]"
        )
    return problems


def spot_sweep_problems(result: dict) -> list[str]:
    """The chosen spot is one of the evaluated starts, so the sweep must give its start the
    spot's own outcome, and a passing spot must lie in a passing run. A result could otherwise
    report a pass at a start its own sweep says fails, with stats that agree with the sweep."""
    spot = result.get("spot")
    if spot is None:
        return []
    start = spot["span_ft"][0]
    got = outcome_at(result, spot["wall_id"], start)
    if got == spot["outcome"]:
        return []
    verb = {"pass": "passes", "fail": "fails"}.get(spot["outcome"], "is unsure")
    where = f"start {start:.2f} on {spot['wall_id']}"
    if got is None:
        return [f"spot {verb} but no sweep run has {where}"]
    return [f"spot {verb} but the sweep has {where} as {got}"]


def required_span(lo: float, hi: float, radius: float) -> tuple[float, float]:
    """Along-wall stretch within `radius` of a battery covering [lo, hi].

    Along the wall chain, distance is never shorter than straight-line distance, so every point
    in this stretch lies within the radius even where the chain bends: observing it is necessary
    (not sufficient) for a PASS, and demanding it cannot wrongly flag a correct server.
    """
    return lo - radius, hi + radius


def battery_error(scene: dict, rules: RuleSet, wall_id: str, lo: float, hi: float) -> float:
    """Position error of a battery over [lo, hi] on `wall_id`, as server 3baa338 takes it for
    its checks (solver.py _errors): its wall's error at its far edge from the meter, plus the
    larger of the meter's error in plan and the battery's slide along the walls (slide)."""
    wall = next((w for w in scene["walls"] if w["id"] == wall_id), None)
    if wall is None:
        raise ValueError(f"the result names wall {wall_id!r}, which the scene does not have")
    return wall_error_at(wall, rules, max(abs(lo), abs(hi))) + max(
        meter_error(scene, rules), slide(scene, rules, lo, hi)
    )


def along_error(scene: dict, rules: RuleSet, wall_id: str, lo: float, hi: float) -> float:
    """The battery's error against spans measured along the walls: its wall's at its far edge
    plus its slide, without the meter's plan error (server 3baa338 _errors, along_err)."""
    wall = next(w for w in scene["walls"] if w["id"] == wall_id)
    return wall_error_at(wall, rules, max(abs(lo), abs(hi))) + slide(scene, rules, lo, hi)


def wall_error_at(wall: dict, rules: RuleSet, s: float) -> float:
    """A wall's error at distance |s| along the walls from the meter: its explicit one, or its
    source's default plus drift."""
    if "plus_minus_ft" in wall:
        return wall["plus_minus_ft"]
    return wall_error(wall, rules) + rules.errors["drift_per_ft"] * abs(s)


def meter_error(scene: dict, rules: RuleSet) -> float:
    """The meter's position error: its explicit one or rules.yaml's errors.meter_ft (no drift)."""
    return scene.get("meter", {}).get("plus_minus_ft", rules.errors["meter"])


def slide(scene: dict, rules: RuleSet, lo: float, hi: float) -> float:
    """How far a battery over [lo, hi] can move along the walls against spans measured in s when
    the meter or a corner between them moves within its error (server 3baa338 _slide): the
    meter's error times the difference between its wall's direction and the battery's, plus each
    other wall between the meter and the battery's near edge, at its far end, times the same
    difference for that wall. 0 on the meter's own wall."""
    segments = segments_s(scene, rules)
    if not segments:
        return 0.0
    walls = {w["id"]: w for w in scene["walls"]}

    def along(seg) -> Point:
        _, _, _, p, q = seg
        n = math.dist(p, q)
        return ((q[0] - p[0]) / n, (q[1] - p[1]) / n)

    def under(s: float):
        return min(segments, key=lambda g: max(g[1] - s, s - g[2], 0.0))

    battery = along(under((lo + hi) / 2))

    def turn(seg) -> float:
        a = along(seg)
        return math.hypot(battery[0] - a[0], battery[1] - a[1])

    meter_wall = scene["meter"]["wall_id"]
    meter_seg = min(
        (g for g in segments if g[0] == meter_wall), key=lambda g: max(g[1], -g[2], 0.0)
    )
    near = lo if lo > 0 else min(hi, 0.0)
    a0, b0 = min(0.0, near), max(0.0, near)
    total = meter_error(scene, rules) * turn(meter_seg)
    for seg in segments:
        a, b = max(seg[1], a0), min(seg[2], b0)
        if b - a <= EPS:
            continue
        total += wall_error_at(walls[seg[0]], rules, max(abs(a), abs(b))) * turn(seg)
    return total


def wall_error(wall: dict, rules: RuleSet) -> float:
    """A wall's own position error, without drift: its explicit one or its source's default."""
    return wall.get("plus_minus_ft", rules.errors[WALL_ERROR[wall.get("source", "tap")]])


def reach_gaps(
    scene: dict, rules: RuleSet, check: str, wall_id: str, lo: float, hi: float
) -> list[tuple[str, str]]:
    """(band, what is missing) for each Need of `check` a battery over [lo, hi] does not have.

    Only points within the check's radius R of the battery whatever the wall's shape are
    required, so a correct server is never flagged. R is widened by the battery's position
    error e where the server widens it (C5: a pass needs the margin to exceed the error, and an
    unseen hazard just past the seen area is a margin of zero).

    - wall band: [lo - R - e, hi + R + e] along the wall, since distance along the wall is
      never shorter than straight-line distance, seen up to the check's height;
    - ground band: out to depth + R + e in front of the battery (a battery sits flush on one
      straight segment), and at distance d past either end out to depth + sqrt((R + e)^2 - d^2),
      the straight-line reach from the footprint's corner;
    - facing and overhead bands: over [lo - e, hi + e], every place the battery may sit, beyond
      the check's distance unless measured.
    """
    gaps = []
    for need in rules.needs[check]:
        if not need.widen:
            error = 0.0
        elif need.along:
            error = along_error(scene, rules, wall_id, lo, hi)
        else:
            error = battery_error(scene, rules, wall_id, lo, hi)
        reach = need.radius + error
        if need.band == "ground":
            plan = GroundPlan(scene, rules) if rules.wall_join_ft is not None else None
            gap = ground_gap(scene, lo, hi, rules.depth_ft, reach, plan)
        elif need.band == "wall":
            a, b = required_span(lo, hi, reach)
            wall = observed(
                scene, "wall", need.height, beyond=True, wall_default_ft=rules.headroom_ft
            )
            seen = not gaps_over(wall, a, b)
            gap = None if seen else f"wall [{a:.2f}, {b:.2f}] observed higher than {need.height} ft"
        else:
            a, b = required_span(lo, hi, reach)
            gap = measured_band_gap(scene, need.band, wall_id, a, b, need.height)
        if gap:
            gaps.append((need.band, gap))
    return gaps


def measured_band_gap(
    scene: dict, band: str, wall_id: str, lo: float, hi: float, need: float
) -> str | None:
    """The first stretch of [lo, hi] the facing or overhead band leaves unsettled: not observed,
    or observed short of `need` where no measurement covers it."""
    entries = [
        (*sorted(e["span_ft"]), reached(e))
        for e in scene.get("coverage", {}).get("observed", [])
        if e["band"] == band
    ]
    measured = [
        tuple(sorted(m["span_ft"]))
        for m in scene.get(MEASUREMENTS[band], [])
        if m.get("wall_id", wall_id) == wall_id
    ]
    edges = {x for e in entries for x in e[:2]} | {x for m in measured for x in m}
    cuts = sorted({lo, hi} | {x for x in edges if lo < x < hi})
    for p, q in itertools.pairwise(cuts):
        if q - p <= EPS:
            continue
        seen = [out for x, y, out in entries if x <= p + EPS and q - EPS <= y]
        where = f"{band} [{p:.2f}, {q:.2f}] observed"
        if not seen:
            if q - p < COVERAGE_TOLERANCE_FT:
                continue
            return f"{where}, none seen"
        settled = any(x <= p + EPS and q - EPS <= y for x, y in measured)
        # Strictly beyond, as the server requires (server/solver.py at 6c7ca23).
        if not settled and max(seen) <= need:
            return f"{where} out to {need:.2f} ft or measured, seen {max(seen):.2f} ft"
    return None


def run_starts(span: list[float], step_ft: float | None) -> Iterator[float]:
    """Validate eagerly, then sample a run lazily: first, every step after it, and last.

    A sample, not the server's own list: the server also evaluates boundaries and midpoints
    that a run reports only as its two ends. Oversized runs fail rather than lose coverage
    through truncation."""
    if step_ft is None or not math.isfinite(step_ft) or step_ft <= 0:
        raise ValueError("checking a sweep run needs finite positive rules' sweep.step_ft")
    a, b = span
    if not math.isfinite(a) or not math.isfinite(b) or a > b:
        raise ValueError("sweep start_ft must be finite and ordered")
    steps = (b - a) / step_ft
    if not math.isfinite(steps) or steps > MAX_SWEEP_SAMPLES:
        raise ValueError(f"sweep sampling exceeds the {MAX_SWEEP_SAMPLES} sample limit")
    # Every step strictly before the last start, the first included however short the run.
    count = max(int(a < b), math.ceil(steps - EPS))
    if count + 1 > MAX_SWEEP_SAMPLES:
        raise ValueError(f"sweep sampling exceeds the {MAX_SWEEP_SAMPLES} sample limit")
    return itertools.chain((a + k * step_ft for k in range(count)), (b,))


def sweep_sampling_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    """Reject invalid runs before footprint, coverage or height checks sample them."""
    ends = chain_ends_s(scene, rules)
    problems = []
    for run in result.get("sweep", []):
        if run["outcome"] not in ("pass", "unsure"):
            continue
        a, b = run["start_ft"]
        prefix = f"sweep {run['outcome']} starts {run['start_ft']}: "
        if ends is not None and (a < ends[0] - COLLINEAR_FT or b > ends[1] + COLLINEAR_FT):
            problems.append(prefix + f"start_ft span exceeds scanned range [{ends[0]}, {ends[1]}]")
            continue
        try:
            run_starts(run["start_ft"], rules.step_ft)
        except ValueError as exc:
            problems.append(prefix + str(exc))
    return problems


# Checks every result lists besides those with coverage needs (server README, "What settles
# each check", at 3baa338).
ALWAYS_CHECKS = frozenset({"meter_working_space", "route_path", "route_length"})


def expected_checks(scene: dict, rules: RuleSet) -> set[str]:
    """The checks the rules define for this scene. battery_clearance only when the scene marks
    an existing battery or the rules set its clearance above gas's (server 3baa338)."""
    ids = set(rules.needs)
    battery = rules.needs.get("battery_clearance")
    gas = rules.needs.get("gas_clearance")
    marked = any(o.get("type") == "battery" for o in scene.get("objects", []))
    if battery and not marked and not (gas and battery[0].radius > gas[0].radius):
        ids.discard("battery_clearance")
    return ids


def dropped_check_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    listed = {c["id"] for c in result.get("checks", [])}
    wanted = expected_checks(scene, rules) | ALWAYS_CHECKS
    return [
        f"the result leaves out check {c}, which the rules define" for c in sorted(wanted - listed)
    ]


def coverage_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    """Missing coverage is never a pass (C5), to the reach each check's rule looks out to (see
    reach_gaps). A sweep run that passes needs, at each start sampled from it (run_starts), every
    check's area around that start's battery with that battery's own position error, and the
    wall and cable route back to the meter over the whole run. Checking the whole run as one
    battery at its farthest start's error would ask for more than any single start needs."""
    if sampling := sweep_sampling_problems(scene, result, rules):
        return sampling
    problems: list[str] = []
    route_wall = observed(
        scene, "wall", rules.route_height_ft, beyond=True, wall_default_ft=rules.headroom_ft
    )
    # Coverage is needed for every check the rules define for this scene, whether or not the
    # result lists it: a result that drops a check must not skip its coverage.
    evaluated = expected_checks(scene, rules)
    if "coverage" not in scene and result["decision"] == "pass":
        problems.append("decision pass for a scene with no coverage at all")

    for run in result.get("sweep", []):
        if run["outcome"] != "pass":
            continue
        lo, hi = run["start_ft"][0], run["start_ft"][1] + rules.width_ft
        # The route starts at the meter, which may stand its error either side of s = 0
        # (server README, route_path), within the scanned chain.
        m = meter_error(scene, rules)
        ends = chain_ends_s(scene, rules) or (-math.inf, math.inf)
        route_lo = max(min(lo, -m), ends[0])
        route_hi = min(max(hi, m), ends[1])
        if gaps_over(route_wall, route_lo, route_hi):
            problems.append(
                f"sweep pass for starts {run['start_ft']} but the wall and cable route "
                f"[{route_lo:.2f}, {route_hi:.2f}] were not all observed higher than "
                f"{rules.route_height_ft} ft"
            )
        for check in sorted(set(rules.needs) & evaluated):
            gaps = {}  # each gap once, at the first start that needs it
            for start in run_starts(run["start_ft"], rules.step_ft):
                battery = (start, start + rules.width_ft)
                for _, gap in reach_gaps(scene, rules, check, run["wall_id"], *battery):
                    gaps.setdefault(gap, start)
            problems += [
                f"sweep pass for starts {run['start_ft']} but at start {start:.2f} {check} "
                f"needs {gap}"
                for gap, start in gaps.items()
            ]

    spot = result.get("spot")
    if spot is not None:
        lo, hi = spot["span_ft"]
        for check in result.get("checks", []):
            if check["outcome"] != "pass" or check["id"] not in rules.needs:
                continue
            for _, gap in reach_gaps(scene, rules, check["id"], spot["wall_id"], lo, hi):
                problems.append(f"check {check['id']} passes but needs {gap}")

    for request in result.get("missing_evidence", []):
        if request["kind"] != "band" or "span_ft" not in request or "band" not in request:
            continue
        a, b = sorted(request["span_ft"])
        # A request may want a farther or higher view over a span already seen: it is redundant
        # only where the band was seen as far as it asks (`out_ft`, on every band since S2
        # 55cb4b0). Without one, a ground request is redundant past GROUND_FAR_FT, and any other
        # only where the band was seen all the way (no `out_ft`).
        default = GROUND_FAR_FT if request["band"] == "ground" else math.inf
        far = request.get("out_ft", default)
        seen = observed(scene, request["band"], far, wall_default_ft=rules.headroom_ft)
        if b - a > EPS and covers(seen, a, b):
            problems.append(
                f"missing_evidence asks for {request['band']} {request['span_ft']}, "
                "which the scene lists as observed"
            )
    return problems


def ground_gap(
    scene: dict,
    lo: float,
    hi: float,
    depth: float,
    radius: float,
    plan: GroundPlan | None = None,
) -> str | None:
    """The first stretch of ground seen less far out than a battery over [lo, hi] needs (see
    coverage_problems), or None. With `plan`, ground not seen in the battery's own wall's band
    still counts where it lies in another wall's observed band in plan (GroundPlan)."""
    entries = [
        (*sorted(e["span_ft"]), e.get("out_ft", 0.0))
        for e in scene.get("coverage", {}).get("observed", [])
        if e["band"] == "ground"
    ]
    a, b = lo - radius, hi + radius
    cuts = sorted({a, lo, hi, b} | {x for e in entries for x in e[:2] if a < x < b})
    for p, q in itertools.pairwise(cuts):
        if q - p <= EPS:
            continue

        # Past an end, a point d along the wall from the battery is within the radius of its
        # footprint (depth D out from the wall) out to D + sqrt(R^2 - d^2).
        def need_at(s: float) -> float:
            d = max(lo - s, s - hi, 0.0)
            return depth + math.sqrt(max(0.0, radius**2 - d**2))

        need = need_at(q if q <= lo else p if p >= hi else (lo + hi) / 2)
        seen = max((out for x, y, out in entries if x <= p + EPS and q - EPS <= y), default=None)
        if seen is None or seen + SEEN_GROWTH_FT < need:
            if plan is not None and plan.covers((lo + hi) / 2, p, q, seen or 0.0, need_at):
                continue
            where = f"ground [{p:.2f}, {q:.2f}] observed"
            if seen is None:
                return f"{where}, none seen"
            return f"{where} out to {need:.2f} ft, seen {seen:.2f} ft"
    return None


def missing_evidence_problems(scene: dict, result: dict, rules: RuleSet | None = None) -> list[str]:
    """When coverage is what stands in the way (reason unobserved_area), every check left unsure
    for an unobserved area is named by a request, and a clearance check by a request of each
    band it lacks around the chosen spot: a check that forgets one band leaves the next capture
    still unsure.

    Without that reason, a chosen spot may still have checks unsure for an unobserved area only
    where what they lack lies past an unexplored end the result asks to walk past (reason
    unexplored_end, a past_end request, which names no checks). Anything else they lack is owed
    the reason and its requests; leaving both out would never ask the homeowner for the view
    that settles the check. Without a spot and without the reason, every position fails anyway,
    no photo would change the answer, and C2 lets missing_evidence stay empty."""
    codes = {r["code"] for r in result.get("reasons", [])}
    spot = result.get("spot")
    unseen = unobserved_checks(result)
    requests = result.get("missing_evidence", [])
    if "unobserved_area" not in codes:
        if spot is None or not unseen:
            return []
        if rules is None:
            if requests:
                return []
            return [
                f"the chosen spot has checks unsure for an unobserved area ({', '.join(unseen)}) "
                "but no reason unobserved_area and no missing_evidence"
            ]
        lo, hi = spot["span_ft"]
        asked = with_past_ends_asked(scene, requests, rules)
        return [
            f"check {c} is unsure and needs {gap}, not past an end the result asks to walk "
            f"past, but there is no reason unobserved_area and no {band} request"
            for c in unseen
            if c in rules.needs
            for band, gap in reach_gaps(asked, rules, c, spot["wall_id"], lo, hi)
        ]
    if not requests:
        return ["reason unobserved_area but missing_evidence is empty"]
    problems: list[str] = []
    named = {(r.get("band"), c) for r in requests for c in r.get("checks", [])}
    problems += [
        f"check {c} is unsure (unobserved) but no missing_evidence entry names it"
        for c in unseen
        if not any(check == c for _, check in named)
    ]
    if rules is None or spot is None:
        return problems
    lo, hi = spot["span_ft"]
    asked = with_past_ends_asked(scene, requests, rules)
    for c in unobserved_checks(result):
        if c not in rules.needs:
            continue
        for band, gap in reach_gaps(asked, rules, c, spot["wall_id"], lo, hi):
            if (band, c) not in named:
                problems.append(
                    f"check {c} is unsure and needs {gap}, but no {band} request names it"
                )
    return problems


# How far a past_end request may sit from the chain's actual end and still name it.
END_TOL_FT = 0.1


Point = tuple[float, float]


def segments_s(scene: dict, rules: RuleSet) -> list[tuple[str, float, float, Point, Point]] | None:
    """Each straight baseline segment as (wall id, s at its start, s at its end, start point,
    end point), with s measured from the meter's projection, or None without a meter wall.

    s runs along every wall. The space from one wall's end to the next one's start counts only
    when it is a gap: wider than both walls' own errors together (no drift), capped at
    sweep.wall_join_ft. Narrower, the walls meet and s skips the space (server/scene.py at
    7133283, `meets`)."""
    if rules.wall_join_ft is None:
        raise ValueError("laying out the walls needs the rules' sweep.wall_join_ft")
    walls = scene.get("walls", [])
    if not walls or "meter" not in scene:
        return None
    raw: list[tuple[str, float, float, Point, Point]] = []
    meter_s = None
    mx, _, mz = scene["meter"]["pos"]
    s, prev = 0.0, None
    for wall in walls:
        pts = [(float(pt[0]), float(pt[1])) for pt in wall["baseline"]]
        if prev is not None:
            space = math.dist(prev[0], pts[0])
            meets = min(rules.wall_join_ft, max(prev[1] + wall_error(wall, rules), SERVER_EPS))
            s += space if space > meets else 0.0
        for p, q in itertools.pairwise(pts):
            seg = math.dist(p, q)
            if wall["id"] == scene["meter"]["wall_id"] and seg > 0:
                t = ((mx - p[0]) * (q[0] - p[0]) + (mz - p[1]) * (q[1] - p[1])) / seg**2
                t = min(1.0, max(0.0, t))
                d = math.dist((mx, mz), (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
                if meter_s is None or d < meter_s[0]:
                    meter_s = (d, s + t * seg)
            raw.append((wall["id"], s, s + seg, p, q))
            s += seg
        prev = (pts[-1], wall_error(wall, rules))
    if meter_s is None:
        return None
    return [(wid, a - meter_s[1], b - meter_s[1], p, q) for wid, a, b, p, q in raw]


def wall_spans_s(scene: dict, rules: RuleSet) -> dict[str, tuple[float, float]] | None:
    """Each wall's stretch of s (see segments_s), or None without a meter wall."""
    segments = segments_s(scene, rules)
    if segments is None:
        return None
    spans: dict[str, tuple[float, float]] = {}
    for wid, a, b, _, _ in segments:
        lo, hi = spans.get(wid, (a, b))
        spans[wid] = (min(lo, a), max(hi, b))
    return spans


class GroundPlan:
    """The observed ground in plan: each entry's stretch of every segment it runs along, out to
    its `out_ft` on the segment's outward side (the baseline turned 90 degrees clockwise seen
    from above, scene.schema.json). A point in front of one wall can lie in a neighbouring
    wall's band, as in an inside corner, where the second wall faces back over the first."""

    # Sampling step for coverage tests. Samples are finite, so they cannot prove that every
    # point of a region was seen: a thin gap shaped to avoid them can still be missed.
    STEP_FT = 0.05

    def __init__(self, scene: dict, rules: RuleSet) -> None:
        self.segments = segments_s(scene, rules) or []
        self.strips = []  # (origin, along, outward, from s, to s, out)
        for e in scene.get("coverage", {}).get("observed", []):
            if e["band"] != "ground":
                continue
            a, b = sorted(e["span_ft"])
            for _, s0, s1, p, q in self.segments:
                if min(b, s1) - max(a, s0) > EPS and s1 - s0 > EPS:
                    along, outward = self._frame(p, q)
                    strip = (p, along, outward, max(a, s0) - s0, min(b, s1) - s0)
                    self.strips.append((*strip, e.get("out_ft", 0.0)))

    @staticmethod
    def _frame(p: Point, q: Point) -> tuple[Point, Point]:
        length = math.dist(p, q)
        along = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
        return along, (-along[1], along[0])

    def point(self, s_mid: float, s: float, out: float) -> Point | None:
        """The plan point at s along the line of the segment under s_mid, `out` in front."""
        seg = next((g for g in self.segments if g[1] - EPS <= s_mid <= g[2] + EPS), None)
        if seg is None:
            return None
        _, s0, _, p, q = seg
        along, outward = self._frame(p, q)
        t = s - s0
        return (p[0] + t * along[0] + out * outward[0], p[1] + t * along[1] + out * outward[1])

    def seen(self, x: Point) -> bool:
        for origin, along, outward, t0, t1, out in self.strips:
            dx, dz = x[0] - origin[0], x[1] - origin[1]
            t = dx * along[0] + dz * along[1]
            n = dx * outward[0] + dz * outward[1]
            grow = SEEN_GROWTH_FT
            if t0 - grow <= t <= t1 + grow and -grow <= n <= out + grow:
                return True
        return False

    def required(self, x: Point) -> bool:
        """Whether ground at x is yard a view must show (server 3baa338 unobserved_ground): in
        front of some wall segment, or past either end of the chain. Past a corner, behind the
        next wall, is neither: the house."""
        for i, (_, _, _, p, q) in enumerate(self.segments):
            along, outward = self._frame(p, q)
            dx, dz = x[0] - p[0], x[1] - p[1]
            t = dx * along[0] + dz * along[1]
            n = dx * outward[0] + dz * outward[1]
            length = math.dist(p, q)
            if -EPS <= t <= length + EPS and n >= -EPS:
                return True
            if (i == 0 and t < 0) or (i == len(self.segments) - 1 and t > length):
                return True
        return False

    def covers(
        self, s_mid: float, p: float, q: float, out_lo: float, need: Callable[[float], float]
    ) -> bool:
        """Every sampled point from s = p to q, deeper than out_lo (seen by the stretch's own
        band) and out to need(s) in front of the battery's segment, lies in some observed ground
        band.

        Samples are each cell's edges and its centre, in s and out. Edges alone missed a gap
        narrower than a cell between two bands (0.019 ft at 5a4b4f4): both its edges lie on a
        band, its middle on none."""
        if not self.strips:
            return False
        for s in self._samples(p, q):
            # Up to out_lo the stretch's own band already saw the ground.
            deeper = [o for o in self._samples(out_lo, need(s)) if o > out_lo + SERVER_EPS]
            for out in deeper:
                x = self.point(s_mid, s, out)
                if x is None or (self.required(x) and not self.seen(x)):
                    return False
        return True

    @classmethod
    def _samples(cls, a: float, b: float) -> list[float]:
        n = max(1, math.ceil((b - a) / cls.STEP_FT))
        edges = [a + (b - a) * i / n for i in range(n + 1)]
        centres = [a + (b - a) * (i + 0.5) / n for i in range(n)]
        return sorted(edges + centres)


def chain_ends_s(scene: dict, rules: RuleSet) -> tuple[float, float] | None:
    """s of the wall chain's left and right ends, or None without a meter wall."""
    spans = wall_spans_s(scene, rules)
    if spans is None:
        return None
    return min(a for a, _ in spans.values()), max(b for _, b in spans.values())


def declared_height_problems(scene: dict, result: dict, rules: RuleSet) -> list[str]:
    """A battery backs onto a wall that declares `height_ft` only if the wall is taller than the
    battery. The server fails wall_backing for a lower wall surely behind the battery and leaves
    it unsure for one within the battery's error of it (server/solver.py at 7133283,
    check_backing), so wall_backing passes only if every declared wall overlapping
    [s0 - e, s1 + e] is taller than battery.height_ft."""
    if sampling := sweep_sampling_problems(scene, result, rules):
        return sampling
    declared = {w["id"]: w["height_ft"] for w in scene.get("walls", []) if "height_ft" in w}
    if not declared or rules.battery_height_ft is None:
        return []
    spans = wall_spans_s(scene, rules) or {}
    height = rules.battery_height_ft

    def short_near(wall_id: str, lo: float, hi: float) -> str | None:
        e = battery_error(scene, rules, wall_id, lo, hi)
        for wid, h in declared.items():
            a, b = spans.get(wid, (math.inf, -math.inf))
            near = a < hi + e - SERVER_EPS and b > lo - e + SERVER_EPS
            if near and h - height <= SERVER_EPS:
                return f"wall {wid} ({h} ft) is within {e:.2f} ft and not taller than {height} ft"
        return None

    problems = []
    spot = result.get("spot")
    backing = next((c for c in result.get("checks", []) if c["id"] == "wall_backing"), None)
    if spot is not None and backing is not None and backing["outcome"] == "pass":
        short = short_near(spot["wall_id"], *spot["span_ft"])
        if short:
            problems.append(f"check wall_backing passes but {short}")
    for run in result.get("sweep", []):
        if run["outcome"] != "pass":
            continue
        for start in run_starts(run["start_ft"], rules.step_ft):
            short = short_near(run["wall_id"], start, start + rules.width_ft)
            if short:
                problems.append(
                    f"sweep pass for starts {run['start_ft']} but at {start:.2f} {short}"
                )
                break
    return problems


def with_past_ends_asked(scene: dict, requests: list[dict], rules: RuleSet) -> dict:
    """The scene as if everything past each end the result asks to walk past were observed.

    Past an unexplored end the wall may turn, so a view along the same line settles nothing; the
    server asks to keep walking (`past_end`) instead of for a band there, and that request covers
    every band beyond the end. Only a request at the chain's actual end, on a side not marked
    `limit`, earns that credit."""
    kinds = scene.get("coverage", {}).get("ends", {})
    past = [r for r in requests if r["kind"] == "past_end" and "span_ft" in r and "side" in r]
    ends = chain_ends_s(scene, rules) if past else None
    if not past or ends is None:
        return scene
    m = copy.deepcopy(scene)
    observed_ = m.setdefault("coverage", {}).setdefault("observed", [])
    for r in past:
        side = r["side"]
        end = ends[0] if side == "left" else ends[1]
        if kinds.get(side, {}).get("kind") == "limit" or abs(r["span_ft"][0] - end) > END_TOL_FT:
            continue
        span = [end - 1e6, end] if side == "left" else [end, end + 1e6]
        for band in ("wall", "facing", "overhead"):
            observed_.append({"band": band, "span_ft": span})
        observed_.append({"band": "ground", "span_ft": span, "out_ft": 1e6})
    return m


# --- Case expectations -----------------------------------------------------------------------


def assumption_mismatches(
    assumed: dict[str, float], result: dict, rules: RuleSet | None = None
) -> list[str]:
    out = []
    configured = (
        {"battery_width": rules.width_ft, "corner_allowance": rules.corner_allowance_ft}
        if rules
        else {}
    )
    for key, value in assumed.items():
        actual = configured.get(key)
        if actual is not None and abs(actual - value) > EPS:
            out.append(f"case assumes {key} {value}, rules use {actual}")
    for key, value in assumed.items():
        for check in result.get("checks", []):
            threshold = check.get("threshold_ft")
            if key in check["id"] and threshold is not None and abs(threshold - value) > EPS:
                out.append(
                    f"case assumes {key} threshold {value}, server's {check['id']} uses "
                    f"{check['threshold_ft']}"
                )
    return out


def expectation_problems(expect: dict, result: dict) -> list[str]:
    problems: list[str] = []
    decision = result["decision"]
    if "decision_in" in expect and decision not in expect["decision_in"]:
        problems.append(f"decision {decision}, expected one of {expect['decision_in']}")
    if decision in expect.get("decision_not", []):
        problems.append(f"decision {decision} is ruled out for this case")

    if "spot" in expect:
        want, spot = expect["spot"], result.get("spot")
        if want is None and spot is not None:
            problems.append(f"expected no spot, got {spot['wall_id']} {spot['span_ft']}")
        if want is not None:
            if spot is None:
                problems.append("expected a spot, got none")
            else:
                a, b = want["span_within"]
                s0, s1 = spot["span_ft"]
                if spot["wall_id"] != want["wall_id"] or s0 < a - 1 / 12 or s1 > b + 1 / 12:
                    problems.append(
                        f"spot {spot['wall_id']} {spot['span_ft']} outside expected "
                        f"{want['wall_id']} {want['span_within']}"
                    )

    for rule in expect.get("sweep_runs", []):
        a, b = rule["start_ft"]
        runs = [
            r
            for r in result.get("sweep", [])
            if r["wall_id"] == rule["wall_id"] and r["start_ft"][0] < b and r["start_ft"][1] > a
        ]
        if not runs and "outcome" in rule:
            problems.append(f"no sweep run overlaps {rule['wall_id']} starts ({a}, {b})")
        for run in runs:
            if "outcome" in rule and run["outcome"] != rule["outcome"]:
                problems.append(
                    f"sweep {run['wall_id']} starts {run['start_ft']} is {run['outcome']}, "
                    f"expected {rule['outcome']}"
                )
            if "outcome_not" in rule and run["outcome"] == rule["outcome_not"]:
                why = f" ({rule['reason']})" if "reason" in rule else ""
                problems.append(
                    f"sweep {run['wall_id']} starts {run['start_ft']} is {run['outcome']}, "
                    f"which this case rules out{why}"
                )
            for field, key in (("failing", "failing_match"), ("unsure", "unsure_match")):
                if key in rule and not any(rule[key] in c for c in run[field]):
                    problems.append(
                        f"sweep {run['wall_id']} starts {run['start_ft']}: no {field} check "
                        f"matching {rule[key]!r} (has {run[field]})"
                    )

    for rule in expect.get("checks", []):
        matching = [c for c in result.get("checks", []) if rule["match"] in c["id"]]
        if not matching:
            problems.append(f"no check matching {rule['match']!r}")
        for check in matching:
            for field in ("outcome", "unsure_cause"):
                if field in rule and check.get(field) != rule[field]:
                    problems.append(
                        f"check {check['id']}: {field} {check.get(field)}, expected {rule[field]}"
                    )
            # Pinned numbers catch a server that measures from somewhere else (for example
            # another battery depth) and lands on the right outcome by accident. The server
            # rounds to 6 decimals.
            for field in ("measured_ft", "plus_minus_ft"):
                got = check.get(field)
                if field in rule and (got is None or abs(got - rule[field]) > NUMBER_TOL_FT):
                    problems.append(f"check {check['id']}: {field} {got}, expected {rule[field]}")

    for rule in expect.get("start_outcomes", []):
        got = outcome_at(result, rule["wall_id"], rule["start_ft"])
        if got != rule["outcome"]:
            why = f" ({rule['why']})" if "why" in rule else ""
            problems.append(
                f"start {rule['wall_id']} {rule['start_ft']} is {got or 'not evaluated'}, "
                f"expected {rule['outcome']}{why}"
            )

    if "missing_evidence_empty" in expect:
        empty = not result.get("missing_evidence")
        if empty != expect["missing_evidence_empty"]:
            problems.append(
                "missing_evidence "
                + ("is empty" if empty else "is not empty")
                + f", expected {'empty' if expect['missing_evidence_empty'] else 'requests'}"
            )
    return problems


# --- Transforms for black-box properties -----------------------------------------------------


def _flip_span(span: list[float]) -> list[float]:
    return [-span[1], -span[0]]


def mirror_scene(scene: dict) -> dict:
    """Reflect the scene left to right (x -> -x) so s becomes -s.

    Baselines are reversed after reflecting so they still run left to right as seen from
    outside, which keeps every wall's outward side. Keyframes, stills and heading are dropped,
    since a reflected camera pose is not a rotation.
    """
    m = copy.deepcopy(scene)
    for key in ("keyframes", "stills", "heading"):
        m.pop(key, None)
    x, y, z = m["meter"]["pos"]
    m["meter"]["pos"] = [-x, y, z]
    m["walls"] = [
        wall | {"baseline": [[-px, pz] for px, pz in reversed(wall["baseline"])]}
        for wall in reversed(m["walls"])
    ]
    for obj in m.get("objects", []):
        obj["span_ft"] = _flip_span(obj["span_ft"])
        if "footprint" in obj:
            obj["footprint"] = [[-px, pz] for px, pz in obj["footprint"]]
    for area in m.get("ground", []):
        area["polygon"] = [[-px, pz] for px, pz in area["polygon"]]
    for key in ("overheads", "facing"):
        for item in m.get(key, []):
            item["span_ft"] = _flip_span(item["span_ft"])
    coverage = m.get("coverage")
    if coverage:
        for entry in coverage.get("observed", []):
            entry["span_ft"] = _flip_span(entry["span_ft"])
        ends = coverage.get("ends")
        if ends:
            coverage["ends"] = {
                side: ends[other]
                for side, other in (("left", "right"), ("right", "left"))
                if other in ends
            }
    return m


def outcome_lengths(result: dict) -> dict[str, float]:
    """Total length of battery start positions per outcome, from the sweep."""
    totals = {"pass": 0.0, "unsure": 0.0, "fail": 0.0}
    for run in result.get("sweep", []):
        totals[run["outcome"]] += run["start_ft"][1] - run["start_ft"][0]
    return totals


def comparable(result: dict) -> dict:
    """The result with the fields that may legitimately differ between identical runs removed."""
    r = copy.deepcopy(result)
    r["stats"].pop("elapsed_ms", None)
    return r


# --- Ordering properties ---------------------------------------------------------------------
#
# Comparisons between the answers to a scene and to a changed copy of it. They need no expected
# numbers: more measurement error or less coverage can only make the server less sure.


def outcome_at(result: dict, wall_id: str, start_ft: float) -> str | None:
    """Outcome of the sweep run containing this battery start, or None if none contains it."""
    for run in result.get("sweep", []):
        a, b = run["start_ft"]
        if run["wall_id"] == wall_id and a - EPS <= start_ft <= b + EPS:
            return run["outcome"]
    return None


def probe_starts(*results: dict) -> list[tuple[str, float]]:
    """Every run's ends and middle, from all results: where the outcomes can be compared."""
    points = set()
    for result in results:
        for run in result.get("sweep", []):
            a, b = run["start_ft"]
            points |= {(run["wall_id"], a), (run["wall_id"], b), (run["wall_id"], (a + b) / 2)}
    return sorted(points)


def more_error_problems(before: dict, after: dict, change: str) -> list[str]:
    """With more error, a start may only move toward UNSURE: UNSURE stays, PASS and FAIL stay
    or become UNSURE."""
    problems = []
    for wall_id, s in probe_starts(before, after):
        a, b = outcome_at(before, wall_id, s), outcome_at(after, wall_id, s)
        if a is None or b is None or a == b or b == "unsure":
            continue
        problems.append(f"{change}: start {wall_id} {s:.3f} went from {a} to {b}")
    if before["decision"] != "pass" and after["decision"] == "pass":
        problems.append(f"{change}: the decision became pass")
    return problems


def less_coverage_problems(before: dict, after: dict, change: str) -> list[str]:
    """With less observed, no start may newly PASS."""
    problems = [
        f"{change}: start {wall_id} {s:.3f} went from {outcome_at(before, wall_id, s)} to pass"
        for wall_id, s in probe_starts(before, after)
        if outcome_at(after, wall_id, s) == "pass"
        and outcome_at(before, wall_id, s) not in (None, "pass")
    ]
    if before["decision"] != "pass" and after["decision"] == "pass":
        problems.append(f"{change}: the decision became pass")
    return problems


def chain_length_ft(scene: dict) -> float:
    """Length of the wall chain, gaps between walls included: no point on it is further than
    this from the meter along the walls."""
    points = [tuple(p) for wall in scene["walls"] for p in wall["baseline"]]
    return sum(math.dist(p, q) for p, q in itertools.pairwise(points))


def with_more_error(scene: dict, rules: RuleSet, extra_ft: float = 0.5) -> dict:
    """Every object, wall and the meter measured `extra_ft` less precisely than before.

    An explicit error replaces the default, and the default for walls and for tap and vlm
    objects grows by drift_per_ft with distance from the meter. So an item left on its default
    gets that default at the far end of the chain, plus `extra_ft`: at least as much as before
    everywhere.
    """
    m = copy.deepcopy(scene)
    drift = rules.errors["drift_per_ft"] * chain_length_ft(scene)
    for obj in m.get("objects", []):
        if "plus_minus_ft" not in obj:
            grows = obj["source"] in ("tap", "vlm")
            obj["plus_minus_ft"] = rules.errors[obj["source"]] + (drift if grows else 0.0)
        obj["plus_minus_ft"] += extra_ft
    for wall in m["walls"]:
        wall["plus_minus_ft"] = wall.get("plus_minus_ft", rules.errors["wall"] + drift) + extra_ft
    meter = m["meter"]
    meter["plus_minus_ft"] = meter.get("plus_minus_ft", rules.errors["meter"]) + extra_ft
    return m


def tape_to_tap(scene: dict) -> dict | None:
    """Objects measured by tape (and not given their own error) re-measured by AR tap."""
    m = copy.deepcopy(scene)
    changed = False
    for obj in m.get("objects", []):
        if obj["source"] == "tape" and "plus_minus_ft" not in obj:
            obj["source"] = "tap"
            changed = True
    return m if changed else None


def with_less_coverage(scene: dict, trim_ft: float = 0.5) -> dict | None:
    """Every observed span shortened by `trim_ft` at each end and the ground seen less far out."""
    if not scene.get("coverage", {}).get("observed"):
        return None
    m = copy.deepcopy(scene)
    kept = []
    for entry in m["coverage"]["observed"]:
        a, b = sorted(entry["span_ft"])
        if b - a <= 2 * trim_ft:
            continue
        entry["span_ft"] = [a + trim_ft, b - trim_ft]
        if "out_ft" in entry:
            entry["out_ft"] = max(0.0, entry["out_ft"] - trim_ft)
        kept.append(entry)
    m["coverage"]["observed"] = kept
    return m


def with_ground_short_of(scene: dict, rules: RuleSet, margin_ft: float = 0.1) -> dict | None:
    """Ground seen out to just short of the largest clearance radius, where any correct server
    must stop passing the check that needs it."""
    radius = max(n.radius for ns in rules.needs.values() for n in ns if n.band == "ground")
    reach = rules.depth_ft + radius - margin_ft
    grounds = [e for e in scene.get("coverage", {}).get("observed", []) if e["band"] == "ground"]
    if not any(e.get("out_ft", 0.0) > reach for e in grounds):
        return None
    m = copy.deepcopy(scene)
    for entry in m["coverage"]["observed"]:
        if entry["band"] == "ground":
            entry["out_ft"] = min(entry.get("out_ft", 0.0), reach)
    return m


# Further out than any clearance looks: the largest in rules.yaml is 10 ft, plus a 1.8 ft
# battery and its position error. Requested ground is added out to here, and ground seen this
# far never needs asking for again.
GROUND_FAR_FT = 40.0


def with_requests_captured(scene: dict, result: dict) -> dict | None:
    """The scene as if the homeowner had shown every band the result asked for, exactly as far
    as each request asks (its `out_ft`, which the result schema says to report back as the
    observed entry's), never further. A ground request without one is read as far as the
    redundancy check reads it, GROUND_FAR_FT."""

    def reach(request: dict) -> dict:
        if "out_ft" in request:
            return {"out_ft": request["out_ft"]}
        return {"out_ft": GROUND_FAR_FT} if request["band"] == "ground" else {}

    added = [
        {"band": r["band"], "span_ft": sorted(r["span_ft"])} | reach(r)
        for r in result.get("missing_evidence", [])
        if r["kind"] == "band" and "band" in r and "span_ft" in r
    ]
    if not added:
        return None
    m = copy.deepcopy(scene)
    m.setdefault("coverage", {}).setdefault("observed", []).extend(added)
    return m


def coverage_blocks(result: dict) -> bool:
    """The result names an unobserved area as a reason, so photos could change it."""
    return any(r["code"] == "unobserved_area" for r in result.get("reasons", []))


def unobserved_checks(result: dict) -> list[str]:
    return [
        c["id"]
        for c in result.get("checks", [])
        if c["outcome"] == "unsure" and c.get("unsure_cause") == "unobserved"
    ]
