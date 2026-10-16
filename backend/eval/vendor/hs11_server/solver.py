"""The placement solver: a pure function from a parsed scene and the rules to a result.

It slides the battery footprint along every straight wall segment, runs every check at each start
position, and decides. Every check follows the strict rule (plan contract C5, docs/02 Lane C):
PASS only when the margin is larger than the error, FAIL only when the value is past the threshold
by more than the error, otherwise UNSURE. An area nobody observed is never a pass.

Start positions are a 2 in grid plus every position where some check can change its outcome (an
opening's clearance edge, a coverage boundary, a ground patch edge), plus the midpoints between
those. A feasible stretch narrower than the grid step is therefore still found (golden test 12).
"""

import itertools
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from shapely import Geometry, LineString, Polygon, get_coordinates, unary_union

from rules import LoadedRules, Rules, Value
from scene import EPS, Measured, Piece, Scene, SceneObject, merge_intervals, subtract_intervals
from units import format_ft_in

PASS, FAIL, UNSURE = "pass", "fail", "unsure"
_SEVERITY = {PASS: 0, UNSURE: 1, FAIL: 2}
# Areas, lengths and distances below this are floating point noise, not geometry.
_MEASURE_EPS = 1e-6
SCHEMA_VERSION = "1.0"
# Operational bounds on one request: a realistic scan needs a few thousand positions and well
# under a second. Past these the input is refused rather than tying the server up. The budget is
# wall-clock on the hosted server, which ran about 4x slower than the development Mac (ETH3D scene:
# 1.6 s there against 0.4 s locally at 739fb6f), and sits far under Vercel's 300 s function limit.
MAX_STARTS = 50_000
SOLVE_BUDGET_S = 8.0
# Positions evaluated before the total time is projected; enough for a stable per-position cost.
PROJECT_AFTER = 256


class SceneTooComplex(ValueError):
    """The scene needs more positions checked, or more time, than one request may take."""


@dataclass(frozen=True)
class View:
    """A view that would settle part of a check: `band` over s from a to b, seen at least `out`
    far (ground and facing: out from the wall; overhead: up from the ground). None for the wall
    band, which has no depth."""

    band: str
    a: float
    b: float
    out: float | None = None


@dataclass
class Check:
    id: str
    label: str
    outcome: str
    reason: str
    rule_key: str
    rule: Value | None
    rule_source: str = ""
    rule_placeholder: bool = False
    measured: float | None = None
    plus_minus: float | None = None
    threshold: float | None = None
    review_threshold: float | None = None
    comparison: str | None = None
    subject: str | None = None
    unsure_cause: str | None = None
    missing: list[View] = field(default_factory=list)
    # Computing the exact unseen stretch is slow, so it is deferred until a result reports it.
    missing_later: Callable[[], list[View]] | None = None
    # Other rule keys whose values or citation the check uses; if the private file set any of
    # them, the citation is withheld.
    cites: tuple[str, ...] = ()

    def all_missing(self) -> list[View]:
        return self.missing + (self.missing_later() if self.missing_later else [])

    def rule_keys(self) -> tuple[str, ...]:
        return (self.rule_key, *self.cites)

    def to_json(self, private_keys: frozenset[str] = frozenset()) -> dict[str, Any]:
        source = self.rule.source if self.rule else self.rule_source
        if _is_private(self.rule_keys(), private_keys):
            # The private file's citations stay on the server; answers say only where the value
            # came from.
            source = "Private rules"
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "outcome": self.outcome,
            "reason": self.reason,
            "measured_ft": _round(self.measured),
            "plus_minus_ft": _round(self.plus_minus),
            "threshold_ft": _round(self.threshold),
            "comparison": self.comparison,
            "subject": self.subject,
            "rule": {
                "key": self.rule_key,
                "source": source,
                "placeholder": self.rule.placeholder if self.rule else self.rule_placeholder,
            },
        }
        if self.review_threshold is not None:
            out["review_threshold_ft"] = _round(self.review_threshold)
        if self.outcome == UNSURE:
            out["unsure_cause"] = self.unsure_cause or "margin"
        return out


def _is_private(keys: tuple[str, ...], private_keys: frozenset[str]) -> bool:
    """Whether the private file set any of these rules or anything under them."""
    return any(
        k == p or p.startswith(f"{k}.") or k.startswith(f"{p}.") for k in keys for p in private_keys
    )


# A buffer's round corners are polygons inscribed in the true circle (16 segments a quarter
# turn), so they fall short of the radius by up to 0.12%. Scaling by 1/cos of half a segment's
# angle makes them circumscribe it: the region reaches at least as far as the exact distance
# test in Solver._covered.
_BUFFER_SEGMENTS = 16
_CIRCUMSCRIBE = 1 / math.cos(math.pi / (4 * _BUFFER_SEGMENTS))


def _within(fp: Polygon, radius: float) -> Geometry:
    """Every point within `radius` of the footprint, and slightly more at the corners."""
    return fp.buffer(radius * _CIRCUMSCRIBE, quad_segs=_BUFFER_SEGMENTS) if radius > 0 else fp


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 6)


def _up(v: float) -> float:
    """v rounded up to 6 decimals: a depth to show, never less than what is needed."""
    return math.ceil(v * 1e6) / 1e6


def _above(v: float) -> float:
    """The smallest 6-decimal value strictly above v: a view must reach past the rule's line,
    since PASS needs the margin to exceed the error (here 0)."""
    return math.floor(v * 1e6) / 1e6 + 1e-6


def _outward(a: float, b: float) -> list[float]:
    """A requested view's span, rounded to 6 decimals away from its middle: rounding an end
    inward would leave a sliver that capturing exactly the listed span never covers."""
    return [math.floor(a * 1e6) / 1e6, math.ceil(b * 1e6) / 1e6]


def at_least(value: float, error: float, threshold: float) -> str:
    """A clearance: PASS when value - error > threshold, FAIL when value + error < threshold."""
    if value - error - threshold > EPS:
        return PASS
    if threshold - (value + error) > EPS:
        return FAIL
    return UNSURE


def reach_outcome(length: float, error: float, confident: float, maximum: float) -> str:
    """A route length: PASS when clearly under the confident reach, FAIL when clearly over the
    maximum, UNSURE in between or on either line."""
    if (length - error) - maximum > EPS:
        return FAIL
    if confident - (length + error) > EPS:
        return PASS
    return UNSURE


def worst(outcomes: list[str]) -> str:
    return max(outcomes, key=_SEVERITY.__getitem__, default=PASS)


def ft(v: float) -> str:
    return format_ft_in(abs(v)) if v >= 0 else "-" + format_ft_in(abs(v))


def _point(s: float) -> str:
    """where(s) as the end of a stretch: "from 3 ft left of the meter to the meter"."""
    return "the meter" if abs(s) < 1 / 24 else where(s)


def where(s: float) -> str:
    if abs(s) < 1 / 24:
        return "at the meter"
    return f"{format_ft_in(abs(s))} {'left' if s < 0 else 'right'} of the meter"


@dataclass
class Route:
    outcome: str
    length: float
    plus_minus: float
    polyline: list[tuple[float, float]]
    detours: list[dict[str, Any]]
    crossings: list[dict[str, Any]]


@dataclass
class Candidate:
    piece: Piece
    s0: float
    s1: float
    footprint: Polygon
    checks: list[Check]
    route: Route
    outcome: str

    def failing(self) -> list[str]:
        return [c.id for c in self.checks if c.outcome == FAIL]

    def unsure(self) -> list[str]:
        return [c.id for c in self.checks if c.outcome == UNSURE]


class Solver:
    def __init__(self, scene: Scene, loaded: LoadedRules) -> None:
        self.scene = scene
        r: Rules = loaded.rules
        self.r = r
        self.W = r.battery.width_ft.value
        self.D = r.battery.depth_ft.value
        self.H = r.battery.height_ft.value
        objs = scene.objects
        self.gas = [o for o in objs if o.type == "gas_meter"]
        self.ac = [o for o in objs if o.type == "ac"]
        self.pool = [o for o in objs if o.type == "pool"]
        self.openings = [o for o in objs if o.type in r.openings.types]
        self.equipment = [o for o in objs if o.type in r.wall_equipment.types]
        self.route_objects = [
            o
            for o in objs
            if r.route.crossing.get(o.type, "allow") != "allow"  # type: ignore[call-overload]
        ]
        self.good = [g for g in scene.ground if g.type in r.ground.allowed]
        self.bad = [g for g in scene.ground if g.type not in r.ground.allowed]
        self.drives = [g for g in scene.ground if g.type in r.ground.drivable]
        self.unobserved_ground = scene.unobserved_ground()
        self.unobserved_wall = scene.unobserved_wall()
        ws = r.meter_working_space
        self.ws_span = (-ws.width_ft.value / 2, ws.width_ft.value / 2)
        self.ws_poly = scene.band_polygon(self.ws_span[0], self.ws_span[1], ws.depth_ft.value)
        self._good_union = unary_union([g.polygon for g in self.good]).buffer(1e-7)
        recorded = unary_union([g.polygon for g in scene.ground])
        outdoor = scene.band_polygon(scene.pieces[0].s0, scene.pieces[-1].s1, scene.reach_ft)
        # Outdoor ground no patch describes; opened to drop floating point slivers.
        self._unclassified = outdoor.difference(recorded).buffer(-1e-6).buffer(1e-6)
        self._buffers: dict[tuple[int, float], Geometry] = {}
        self._ground_error = max((g.plus_minus for g in scene.ground), default=0.0)

    # --- geometry helpers ----------------------------------------------------------------------

    def footprint(self, piece: Piece, s0: float) -> Polygon:
        return piece.rect(s0, s0 + self.W, 0.0, self.D)

    def _buffered(self, geom: Geometry, distance: float) -> Geometry:
        """geom.buffer(distance), computed once per solve: ground patches and the unclassified
        area only depend on the wall error, not on the candidate."""
        key = (id(geom), distance)
        if key not in self._buffers:
            self._buffers[key] = geom.buffer(distance)
        return self._buffers[key]

    def _covered(self, fp: Polygon, unobserved: Geometry, radius: float) -> bool:
        """True when nothing unobserved lies strictly within `radius` of the footprint: an unseen
        area that only touches that distance (an exact shared edge) does not count. An exact
        distance, rather than intersecting a buffered footprint, is both cheaper (this is most
        of the solve time) and free of the buffer's polygonal approximation of a circle."""
        if unobserved.is_empty:
            return True
        if radius > _MEASURE_EPS:
            return fp.distance(unobserved) >= radius - _MEASURE_EPS
        # At radius 0 (a wall with no error) a distance of 0 is either overlap or touching; only
        # overlap, by area or by length along a line, leaves part of the footprint unseen.
        overlap = unobserved.intersection(fp)
        for part in getattr(overlap, "geoms", [overlap]):
            if part.geom_type.endswith("Polygon") and part.area > _MEASURE_EPS:
                return False
            if part.geom_type.endswith("LineString") and part.length > _MEASURE_EPS:
                return False
        return True

    # --- checks --------------------------------------------------------------------------------

    def check_backing(self, piece: Piece, s0: float, s1: float) -> Check:
        c = Check(
            "wall_backing",
            "Flush against one straight wall",
            PASS,
            "",
            "battery.width_ft",
            self.r.battery.width_ft,
        )
        if piece.kind != "wall" or s0 < piece.s0 - EPS or s1 > piece.s1 + EPS:
            c.outcome = FAIL
            c.reason = (
                "The footprint runs past the end of its straight wall segment (a corner, the end "
                "of the wall or a stretch with no wall), so the battery can't sit flush."
            )
            return c
        missing = self.scene.missing("wall", s0, s1)
        if missing:
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.missing = [View("wall", a, b) for a, b in missing]
            c.reason = "Part of the wall behind the battery was not seen."
            return c
        c.reason = "The whole footprint backs onto one straight, observed wall segment."
        return c

    def check_ground(self, piece: Piece, fp: Polygon) -> Check:
        c = Check(
            "ground_surface",
            "Ground under the battery",
            PASS,
            "",
            "ground.allowed",
            None,
            rule_source=self.r.ground.source,
            rule_placeholder=self.r.ground.placeholder,
            # The citation is ground.source, shared by the whole ground group.
            cites=("ground",),
        )
        ew = piece.plus_minus
        # "Clearly on a disallowed surface" erodes the patch by the error, rounded up to 0.1 ft
        # so the eroded patches are reused across candidates (at most 1.2 in more conservative).
        erosion = math.ceil(round(ew, 9) * 10) / 10
        for g in self.bad:
            core = self._buffered(g.polygon, -(g.plus_minus + erosion))
            if not core.is_empty and fp.intersection(core).area > _MEASURE_EPS:
                c.outcome, c.subject = FAIL, f"ground[{g.index}] {g.type}"
                c.reason = f"The footprint stands on {g.type}, which is not an allowed surface."
                return c
        if not self._covered(fp, self.unobserved_ground, ew):
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.missing_later = partial(self._missing, "ground", fp, ew)
            c.reason = "The ground under the footprint was not seen."
            return c
        # A patch edge's error matters only where an allowed surface meets a disallowed or
        # unrecorded one; an edge that runs along the house wall is not a boundary at all.
        # Containment is inclusive: touching a disallowed patch along an exact edge is not
        # standing on it (golden test 01).
        on_good = self._good_union.covers(fp)
        near_bad = any(fp.distance(g.polygon) < g.plus_minus + ew - _MEASURE_EPS for g in self.bad)
        near_unknown = (
            not self._unclassified.is_empty
            and fp.distance(self._unclassified) < self._ground_error + ew - _MEASURE_EPS
        )
        if on_good and not near_bad and not near_unknown:
            c.reason = "The whole footprint stands on an allowed surface."
            return c
        c.outcome = UNSURE
        if not on_good and not near_bad:
            c.unsure_cause = "unknown_attribute"
            c.reason = "The ground under the footprint was seen but its surface was not recorded."
        else:
            c.unsure_cause = "margin"
            c.reason = "The footprint sits within measurement error of a surface boundary."
        return c

    def _unobserved(self, band: str) -> Geometry:
        return self.unobserved_ground if band == "ground" else self.unobserved_wall

    def _missing(self, band: str, fp: Polygon, radius: float) -> list[View]:
        """The views that would show what nobody saw within `radius` of the footprint. Only what
        a view can settle is requested; the rest lies past an unexplored end, which the
        past_end request covers. For the wall band, the area past an unexplored end that lies in
        front of the scanned walls is settled by a view of the ground there, so it is asked for
        as ground."""
        within = _within(fp, radius)
        if band == "ground":
            parts = [("ground", self.unobserved_ground)]
        else:
            parts = [
                ("wall", self.scene.unobserved_wall_lines()),
                ("ground", self.scene.unexplored_area()),
            ]
        out = []
        for view, unseen in parts:
            region = unseen.intersection(within).intersection(self.scene.coverable(view))
            # Where that meets an unexplored end exactly, a sliver with no length along the wall
            # is left; a request for it could never be settled.
            extents = [
                e
                for part in getattr(region, "geoms", [region])
                if (e := self.scene.s_extent(part)) and e[1] - e[0] > _MEASURE_EPS
            ]
            if extents:
                a, b = min(a for a, _ in extents), max(b for _, b in extents)
                depth = _up(self.scene.farthest_out(region)) if view == "ground" else None
                out.append(View(view, a, b, depth))
        return out

    def _missing_bands(self, bands: list[str], fp: Polygon, radius: float) -> list[View]:
        """Every unseen band, so one recapture settles the check (a clearance can need both the
        ground and the wall)."""
        return [m for band in bands for m in self._missing(band, fp, radius)]

    def check_clearance(
        self,
        check_id: str,
        label: str,
        rule_key: str,
        rule: Value,
        piece: Piece,
        fp: Polygon,
        items: list[tuple[str, Geometry, float, bool | None]],
        band: str,
        noun: str,
    ) -> Check:
        """Minimum plan distance from the footprint to each item. `items` holds (label, geometry,
        error, counts) where counts None means an unknown attribute decides whether it applies."""
        t = rule.value
        c = Check(check_id, label, PASS, "", rule_key, rule, threshold=t, comparison="at_least")
        worst_key: tuple[int, float] | None = None
        for name, geom, err, counts in items:
            if counts is False:
                continue
            d = fp.distance(geom)
            e = err + piece.plus_minus
            outcome = at_least(d, e, t)
            cause = "margin"
            if counts is None and outcome != PASS:
                outcome, cause = UNSURE, "unknown_attribute"
            key = (_SEVERITY[outcome], -(d - e))
            if worst_key is None or key > worst_key:
                worst_key = key
                c.outcome, c.measured, c.plus_minus, c.subject = outcome, d, e, name
                c.unsure_cause = cause if outcome == UNSURE else None
        if c.outcome == FAIL:
            c.reason = (
                f"{c.subject} is {ft(c.measured or 0)} (± {ft(c.plus_minus or 0)}) from the "
                f"battery; the rule needs more than {ft(t)}."
            )
            return c
        # The footprint itself is only placed to within the wall's error, so the area that must
        # have been seen reaches that much further.
        radius = t + piece.plus_minus
        bands = ["ground", "wall"] if band == "ground+wall" else [band]
        unseen = [b for b in bands if not self._covered(fp, self._unobserved(b), radius)]
        missing_later = partial(self._missing_bands, unseen, fp, radius) if unseen else None
        if c.outcome == UNSURE:
            if c.unsure_cause == "unknown_attribute":
                c.reason = (
                    f"{c.subject} is within {ft(t)} of the battery, and whether the rule applies "
                    "to it (for example whether a window opens) was not recorded."
                )
            else:
                c.reason = (
                    f"{c.subject} is {ft(c.measured or 0)} (± {ft(c.plus_minus or 0)}) from the "
                    f"battery against a {ft(t)} rule: too close to call."
                )
            c.missing_later = missing_later
            return c
        if missing_later:
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.missing_later = missing_later
            c.reason = (
                f"Not everything within {ft(t)} of the battery was seen, so a {noun} could "
                "hide there."
            )
            return c
        if c.measured is None:
            c.reason = f"No {noun} within {ft(t)} of the battery, and that whole area was seen."
        else:
            c.reason = (
                f"Nearest {noun} is {ft(c.measured)} (± {ft(c.plus_minus or 0)}) away, clear of "
                f"the {ft(t)} rule, and the area around the battery was seen."
            )
        return c

    def _opening_counts(self, o: SceneObject) -> bool | None:
        cfg = self.r.openings
        if o.type != "window" or o.well is True:
            return True
        exempt: bool | None = False
        if cfg.exempt_fixed_windows:
            exempt = True if o.operable is False else (None if o.operable is None else False)
        if cfg.exempt_bottom_above_ft is not None and exempt is not True:
            if o.bottom is None:
                exempt = None
            elif o.bottom > cfg.exempt_bottom_above_ft:
                exempt = True
        if exempt is True and o.well is None:
            return None
        return None if exempt is None else not exempt

    def check_along_wall(self, piece: Piece, s0: float, s1: float) -> Check:
        """Wall-mounted boxes and vents directly above the battery, measured along the wall."""
        rule = self.r.clearances.wall_equipment_ft
        t = rule.value
        c = Check(
            "wall_equipment_above",
            "No box or vent above the battery",
            PASS,
            "",
            "clearances.wall_equipment_ft",
            rule,
            threshold=t,
            comparison="at_least",
        )
        worst_key: tuple[int, float] | None = None
        for o in self.equipment:
            gap = max(o.span[0] - s1, s0 - o.span[1])
            e = o.plus_minus
            outcome = at_least(gap, e, t)
            key = (_SEVERITY[outcome], -(gap - e))
            if worst_key is None or key > worst_key:
                worst_key = key
                c.outcome, c.measured, c.plus_minus, c.subject = outcome, gap, e, o.label
        if c.outcome == FAIL:
            c.reason = f"{c.subject} is on the wall directly above the battery."
            return c
        missing = self.scene.missing("wall", s0 - t, s1 + t)
        if c.outcome == UNSURE:
            c.unsure_cause = "margin"
            c.reason = f"{c.subject} ends within measurement error of the battery's edge."
        elif missing:
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.reason = "The wall above the battery was not fully seen."
        else:
            c.reason = "Nothing is mounted on the wall above the battery."
        if missing and c.outcome == UNSURE:
            c.missing = [View("wall", a, b) for a, b in missing]
        return c

    def check_meter_space(self, piece: Piece, fp: Polygon, s0: float, s1: float) -> Check:
        ws = self.r.meter_working_space
        c = Check(
            "meter_working_space",
            "Clear of the meter's working space",
            PASS,
            "",
            "meter_working_space.width_ft",
            ws.width_ft,
            threshold=0.0,
            comparison="at_least",
            subject="meter",
        )
        d = fp.distance(self.ws_poly)
        if d <= EPS:
            d = min(0.0, max(self.ws_span[0] - s1, s0 - self.ws_span[1]))
        e = self.scene.meter_plus_minus + piece.plus_minus
        c.measured, c.plus_minus = d, e
        c.outcome = at_least(d, e, 0.0)
        box = f"{ft(ws.width_ft.value)} wide by {ft(ws.depth_ft.value)} deep"
        if c.outcome == FAIL:
            c.reason = f"The battery would stand in the {box} working space in front of the meter."
        elif c.outcome == UNSURE:
            c.unsure_cause = "margin"
            c.reason = (
                f"The battery is within measurement error of the meter's {box} working space."
            )
        else:
            c.reason = f"The battery is {ft(d)} clear of the meter's {box} working space."
        return c

    def check_measured(
        self,
        check_id: str,
        label: str,
        band: str,
        entries: list[Measured],
        rule_key: str,
        rule: Value,
        s0: float,
        s1: float,
        subtract: float,
        noun: str,
        wall_error: float,
    ) -> Check:
        """Facing gap or headroom: the smallest measurement over the battery's stretch of wall."""
        t = rule.value
        c = Check(check_id, label, PASS, "", rule_key, rule, threshold=t, comparison="at_least")
        missing = self.scene.missing(band, s0, s1)
        worst_key: tuple[int, float] | None = None
        # The lowest value surely under the battery, and whether the deciding entry only might be.
        surely: tuple[float, float] | None = None
        only_maybe = False
        for m in entries:
            # plus_minus is the error of the measured height or depth; where the stretch sits
            # along the wall is known to the wall's error. A measurement that lies under the
            # battery wherever that error puts it counts in full; one that only might (it
            # reaches the battery's edge within the error) can't give a clean pass, but isn't a
            # clear failure either. With exact geometry, touching end to end is not overlap.
            a, b = m.span
            possible = a - wall_error < s1 - EPS and b + wall_error > s0 + EPS
            if not possible:
                continue
            definite = a + wall_error < s1 - EPS and b - wall_error > s0 + EPS
            value = m.value - subtract
            if definite and (surely is None or value < surely[0]):
                surely = (value, m.plus_minus)
            outcome = at_least(value, m.plus_minus, t)
            maybe = not definite and outcome == FAIL
            if maybe:
                outcome = UNSURE
            key = (_SEVERITY[outcome], -(value - m.plus_minus))
            if worst_key is None or key > worst_key:
                worst_key, only_maybe = key, maybe
                c.outcome, c.measured, c.plus_minus = outcome, value, m.plus_minus
                c.subject = f"{'overheads' if band == 'overhead' else 'facing'}[{m.index}]"
        val = f"{ft(c.measured or 0)} (± {ft(c.plus_minus or 0)})"
        if c.outcome == FAIL:
            c.reason = f"The {noun} is {val}; the rule needs more than {ft(t)}."
            return c
        if c.outcome == UNSURE and only_maybe:
            self._report_range(c, surely, wall_error, noun)
        elif c.outcome == UNSURE:
            c.unsure_cause = "margin"
            c.reason = f"The {noun} is {val} against a {ft(t)} rule: too close to call."
        elif missing:
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.reason = f"The {noun} over the battery's stretch of wall was not measured everywhere."
        elif (seen := self._seen_clear(band, entries, s0, s1)) <= t + subtract:
            # A view (a walked path, a tilt-up frame) proves the space clear only as far as it
            # reached; where nothing was measured, that is all that is known.
            c.outcome, c.unsure_cause = UNSURE, "unobserved"
            c.reason = (
                f"The {noun} was seen clear only to {ft(seen - subtract)} over part of the "
                f"battery's stretch of wall; the rule needs more than {ft(t)}."
            )
            missing = [(s0, s1)]
        elif c.measured is None:
            c.reason = f"Nothing limits the {noun} over the battery's stretch of wall."
        else:
            c.reason = f"The {noun} is {val}, clear of the {ft(t)} rule."
        if missing and c.outcome == UNSURE:
            # Seen clear further than the rule needs, or with nothing in the way, settles it.
            c.missing = [View(band, a, b, _above(t + subtract)) for a, b in missing]
        return c

    def _seen_clear(self, band: str, entries: list[Measured], s0: float, s1: float) -> float:
        """How far clear the band was seen over the parts of [s0, s1] no measurement covers:
        infinite where a view saw it all (no `out_ft`), and nothing needed where measurements
        cover the whole stretch."""
        measured = merge_intervals([m.span for m in entries])
        free = subtract_intervals((s0, s1), measured)
        return min((self.scene.seen_to(band, a, b) for a, b in free), default=math.inf)

    @staticmethod
    def _report_range(
        c: Check, surely: tuple[float, float] | None, wall_error: float, noun: str
    ) -> None:
        """The deciding entry fails the rule but only might lie over the battery: its end is
        within the wall's position error of the battery's edge. The value is then somewhere
        between that entry's and the lowest one surely over the battery, and is reported as
        that range (middle ± half its width plus the larger measurement error), so the numbers
        give UNSURE under the C5 rule as the check did."""
        low, low_err = c.measured or 0.0, c.plus_minus or 0.0
        c.unsure_cause = "margin"
        maybe = (
            f"{c.subject} ({ft(low)}) may or may not be over the battery: its end is within the "
            f"wall's ± {ft(wall_error)} of the battery's edge"
        )
        if surely is None:
            # Nothing else surely lies over the battery, so nothing bounds the value from above.
            c.measured = c.plus_minus = None
            c.reason = f"{maybe}, and nothing else limits the {noun} there."
            return
        high, high_err = surely
        c.measured = (low + high) / 2
        c.plus_minus = (high - low) / 2 + max(low_err, high_err)
        c.reason = (
            f"{maybe}. Elsewhere over the battery the {noun} is {ft(high)}, so it is between "
            f"{ft(low)} and {ft(high)} against a {ft(c.threshold or 0)} rule: too close to call."
        )

    def route_for(self, piece: Piece, s0: float, s1: float) -> tuple[Route, Check, Check]:
        r = self.r.route
        near = s0 if s0 > 0 else (s1 if s1 < 0 else 0.0)
        lo, hi = min(0.0, near), max(0.0, near)
        crossings: list[dict[str, Any]] = []
        detours: list[dict[str, Any]] = []
        effects: list[str] = []
        unknown: list[str] = []
        small_gaps: list[str] = []
        for gap in self.scene.gaps:
            if min(hi, gap.s1) - max(lo, gap.s0) > EPS:
                # A gap no longer than the two walls' errors may not be a gap at all.
                gap_err = gap.error_at(max(abs(gap.s0), abs(gap.s1)))
                clear = (gap.s1 - gap.s0) - gap_err > EPS
                crossings.append(
                    {
                        "subject": "stretch with no wall",
                        "span_ft": [gap.s0, gap.s1],
                        "effect": "fail" if clear else "review",
                    }
                )
                if clear:
                    effects.append("fail")
                else:
                    small_gaps.append(f"a {ft(gap.s1 - gap.s0)} gap between walls")
        h = r.height_ft.value
        path_line = self.scene.wall_line(lo, hi) if hi - lo > EPS else None
        e = self.scene.meter_plus_minus + piece.plus_minus
        for o in self.route_objects:
            if path_line is None or min(hi, o.span[1]) - max(lo, o.span[0]) <= EPS:
                continue
            effect = r.crossing[o.type]  # type: ignore[index]
            # Something the cable can go round or behind is only in its way when it touches the
            # wall the cable runs along; a door or garage blocks it wherever it is drawn.
            standoff = o.geom.distance(path_line) - o.plus_minus - piece.plus_minus
            if effect in ("detour", "allow") and standoff > _MEASURE_EPS:
                continue
            crossings.append({"subject": o.label, "span_ft": list(o.span), "effect": effect})
            effects.append(effect)
            if effect == "detour":
                bottom = o.bottom if o.bottom is not None else 0.0
                if o.top is None:
                    unknown.append(o.label)
                    continue
                # Heights carry the object's error; a detour that may or may not be needed, or
                # whose size is uncertain, widens the route's error by the round trip.
                if bottom - o.plus_minus <= h + EPS and o.top + o.plus_minus >= h - EPS:
                    e += 2 * o.plus_minus
                if bottom <= h + EPS and o.top >= h - EPS:
                    options = [2 * (o.top - h)]
                    if bottom > EPS:
                        options.append(2 * (h - bottom))
                    extra = min(options)
                    if extra > EPS:
                        detours.append({"subject": o.label, "extra_ft": extra})
        corners = sum(
            1
            for a, b in zip(self.scene.pieces, self.scene.pieces[1:], strict=False)
            if a.kind == "wall" and b.kind == "wall" and lo + EPS < a.s1 < hi - EPS
        )
        length = (
            (hi - lo) + corners * r.corner_allowance_ft.value + sum(d["extra_ft"] for d in detours)
        )
        missing = self.scene.missing("wall", lo, hi)

        path = Check(
            "route_path",
            "Cable route along the wall",
            PASS,
            "",
            "route.crossing",
            None,
            rule_source="docs/02 Lane C: the cable can't cross a door, a garage or a "
            "stretch with no wall",
        )
        if "fail" in effects:
            path.outcome = FAIL
            blockers = [x["subject"] for x in crossings if x["effect"] == "fail"]
            path.subject = blockers[0]
            path.reason = f"The cable would have to cross {', '.join(blockers)}."
        elif "review" in effects:
            path.outcome, path.unsure_cause = UNSURE, "rule_requires_review"
            path.subject = next(x["subject"] for x in crossings if x["effect"] == "review")
            path.reason = (
                f"The cable would route past {path.subject}, which the policy sends to a person."
            )
        elif small_gaps:
            path.outcome, path.unsure_cause = UNSURE, "margin"
            path.subject = "stretch with no wall"
            path.reason = (
                f"The cable would cross {small_gaps[0]}, no longer than the walls' own "
                "measurement error: it may be one continuous wall."
            )
        elif unknown:
            path.outcome, path.unsure_cause = UNSURE, "unknown_attribute"
            path.subject = unknown[0]
            path.reason = (
                f"The height of {unknown[0]} was not recorded, so the detour around it is unknown."
            )
        elif missing:
            path.outcome, path.unsure_cause = UNSURE, "unobserved"
            path.missing = [View("wall", a, b) for a, b in missing]
            path.reason = "Part of the wall the cable would run along was not seen."
        else:
            path.reason = "The cable runs along continuous, observed wall with nothing blocking it."

        outcome = reach_outcome(length, e, r.confident_reach_ft.value, r.max_ft.value)
        # threshold_ft is the maximum, the line past which the run fails (at_most). A run past
        # the confident reach but under the maximum is UNSURE, so the cited rule names both lines
        # and is a placeholder if either is.
        cr = r.confident_reach_ft
        rule = Value(
            value=r.max_ft.value,
            source=f"{r.max_ft.source}. Review past {ft(cr.value)}: {cr.source}",
            placeholder=r.max_ft.placeholder or cr.placeholder,
        )
        reach = Check(
            "route_length",
            "Cable run length",
            outcome,
            "",
            "route.max_ft",
            rule,
            measured=length,
            plus_minus=e,
            threshold=r.max_ft.value,
            review_threshold=cr.value,
            comparison="at_most",
            cites=("route.confident_reach_ft",),
        )
        run = f"{ft(length)} (± {ft(e)})"
        confident = cr.value
        if reach.outcome == FAIL:
            reach.reason = f"The cable run is {run}, over the {ft(r.max_ft.value)} maximum."
        elif reach.outcome == UNSURE:
            near_a_line = any(abs(length - line) <= e + EPS for line in (confident, r.max_ft.value))
            if near_a_line:
                reach.unsure_cause = "margin"
                reach.reason = (
                    f"The cable run is {run}, within its error of the {ft(confident)} confident "
                    f"reach or the {ft(r.max_ft.value)} maximum: too close to call."
                )
            else:
                reach.unsure_cause = "rule_requires_review"
                reach.reason = (
                    f"The cable run is {run}: past the {ft(confident)} confident reach, so the "
                    f"policy sends it to a person, though under the {ft(r.max_ft.value)} maximum."
                )
        else:
            reach.reason = f"The cable run is {run}, within the {ft(confident)} confident reach."
        route = Route(
            outcome=worst([path.outcome, reach.outcome]),
            length=length,
            plus_minus=e,
            polyline=self.scene.polyline(0.0, near),
            detours=detours,
            crossings=crossings,
        )
        return route, path, reach

    # --- candidates ----------------------------------------------------------------------------

    def evaluate(self, wall_piece: Piece, s0: float) -> Candidate:
        r = self.r
        s1 = s0 + self.W
        fp = self.footprint(wall_piece, s0)
        # Checks see the wall's error at the battery's far edge from the meter, where AR drift
        # is largest.
        piece = replace(
            wall_piece, plus_minus=wall_piece.error_at(max(abs(s0), abs(s1))), drift=0.0
        )
        c = r.clearances
        checks = [
            self.check_backing(piece, s0, s1),
            self.check_ground(piece, fp),
            self.check_meter_space(piece, fp, s0, s1),
            self.check_clearance(
                "gas_clearance",
                "Distance from gas equipment",
                "clearances.gas_ft",
                c.gas_ft,
                piece,
                fp,
                [(o.label, o.geom, o.plus_minus, True) for o in self.gas],
                # Gas meters hang on the wall as well as standing on the ground.
                "ground+wall",
                "gas meter or pipe",
            ),
            self.check_clearance(
                "ac_clearance",
                "Distance from AC units",
                "clearances.ac_ft",
                c.ac_ft,
                piece,
                fp,
                [(o.label, o.geom, o.plus_minus, True) for o in self.ac],
                "ground",
                "AC unit",
            ),
            self.check_clearance(
                "drive_clearance",
                "Distance from the driveway",
                "clearances.drive_ft",
                c.drive_ft,
                piece,
                fp,
                [
                    (f"ground[{g.index}] {g.type}", g.polygon, g.plus_minus, True)
                    for g in self.drives
                ],
                "ground",
                "drivable surface",
            ),
            self.check_clearance(
                "pool_clearance",
                "Distance from a pool",
                "clearances.pool_ft",
                c.pool_ft,
                piece,
                fp,
                [(o.label, o.geom, o.plus_minus, True) for o in self.pool],
                "ground",
                "pool",
            ),
            self.check_clearance(
                "opening_clearance",
                "Distance from doors and windows",
                "clearances.opening_ft",
                c.opening_ft,
                piece,
                fp,
                [(o.label, o.geom, o.plus_minus, self._opening_counts(o)) for o in self.openings],
                "wall",
                "door or window",
            ),
            self.check_along_wall(piece, s0, s1),
            self.check_measured(
                "facing_gap",
                "Open space in front",
                "facing",
                self.scene.facing,
                "facing.min_ft",
                r.facing.min_ft,
                s0,
                s1,
                self.D if r.facing.measured_from == "battery_front" else 0.0,
                "gap in front of the battery"
                if r.facing.measured_from == "battery_front"
                else "gap from the wall to whatever faces it",
                piece.plus_minus,
            ),
            self.check_measured(
                "headroom",
                "Headroom above",
                "overhead",
                self.scene.overheads,
                "headroom.min_ft",
                r.headroom.min_ft,
                s0,
                s1,
                0.0,
                "headroom",
                piece.plus_minus,
            ),
        ]
        route, path, reach = self.route_for(piece, s0, s1)
        checks += [path, reach]
        return Candidate(wall_piece, s0, s1, fp, checks, route, worst([x.outcome for x in checks]))

    def reach_limit(self, piece: Piece) -> float:
        """Past this |s| of its near edge a battery's route fails the maximum length whatever
        else is true: the route is never shorter than |s|, and its error is at most the meter's,
        the wall's and every possible detour's."""
        detour_err = sum(2 * o.plus_minus for o in self.route_objects)
        e_fixed = self.scene.meter_plus_minus + piece.plus_minus + detour_err
        if piece.drift >= 1:
            return math.inf
        # The route's error grows by the wall's drift at the battery's far edge, |s| + W from the
        # meter (as evaluate takes it): solve |s| - e(|s| + W) = max.
        e_fixed += piece.drift * self.W
        # The 1e-6 ft margin keeps the cutoff clear of the 6-decimal rounding of reported
        # starts, so a start reported past reach really fails when evaluated.
        return (self.r.route.max_ft.value + e_fixed) / (1 - piece.drift) + 1e-6

    def starts(self, piece: Piece) -> list[float]:
        """Start positions (left edge, in s) to evaluate on one straight segment."""
        W, D = self.W, self.D
        limit = self.reach_limit(piece)
        lo, hi = max(piece.s0, -limit - W), min(piece.s1 - W, limit)
        if hi < lo - EPS:
            return []
        hi = max(hi, lo)
        step = self.r.sweep.step_ft.value
        # The grid puts the battery's centre at k * step from the meter, so the start positions
        # of a scene and of its mirror image map onto each other and left and right get the same
        # treatment.
        k_lo, k_hi = math.floor(lo / step) - 1, math.ceil((hi + W) / step) + 1
        points = [lo, hi] + [k * step - W / 2 for k in range(k_lo, k_hi + 1)]
        # Along the wall: every place an interval can start or stop mattering, each with only
        # its own error offsets (combining every boundary with every error would grow as their
        # product).
        ew = piece.error_at(max(abs(lo), abs(hi)))
        around = (0.0, ew, -ew)
        boundaries: list[tuple[float, tuple[float, ...]]] = [(0.0, around)]
        boundaries += [(b, around) for b in self.ws_span]
        for o in self.scene.objects:
            e = o.plus_minus
            boundaries += [(b, (0.0, e, -e, e + ew, -(e + ew))) for b in o.span]
        for m in self.scene.overheads + self.scene.facing:
            boundaries += [(b, around) for b in m.span]
        for band in self.scene.observed.values():
            for a, b, _ in band:
                boundaries += [(a, around), (b, around)]
        for g in self.scene.gaps:
            boundaries += [(g.s0, around), (g.s1, around)]
        for b, offsets in boundaries:
            for off in offsets:
                points += [b + off, b - W - off]
        rt = self.r.route
        e_route = self.scene.meter_plus_minus + ew
        for line in (rt.confident_reach_ft.value, rt.max_ft.value):
            for x in (line - e_route, line + e_route, line):
                points += [x, -x - W]
        # Plan clearances: where a footprint corner's track along the wall crosses the line at
        # the rule's distance (and within error of it) from any part of an object, including
        # the middle of a slanted edge, and where it crosses a ground patch's edge.
        tracks = [LineString([piece.point(lo, v), piece.point(hi + W, v)]) for v in (0.0, D)]
        strip = piece.rect(lo, hi + W, 0.0, D)
        for geom, dist in self._clearance_edges(piece):
            region = geom.buffer(dist) if dist != 0 else geom
            boundary = region.boundary
            for track in tracks:
                if track.distance(boundary) > EPS:
                    continue
                for x, z in _coords_of(track.intersection(boundary)):
                    u = piece.local((x, z))[0]
                    points += [u, u - W]
            # Where the region lies inside the footprint's strip, the footprint's side edges
            # meet it at the region's extent along the wall (an object standing a little off the
            # wall is reached by a side edge, not a corner).
            inside = region.intersection(strip)
            if not inside.is_empty:
                us = [piece.local((x, z))[0] for x, z in get_coordinates(inside)]
                points += [min(us), max(us), min(us) - W, max(us) - W]
        pts = sorted({round(p, 9) for p in points if lo - EPS <= p <= hi + EPS})
        pts = [min(max(p, lo), hi) for p in pts]
        mids = [(a + b) / 2 for a, b in itertools.pairwise(pts) if b - a > 1e-6]
        return sorted(set(pts) | set(mids))

    def _clearance_edges(self, piece: Piece) -> list[tuple[Geometry, float]]:
        """(geometry, offset) pairs whose offset outlines bound some check's outcome."""
        c = self.r.clearances
        ew = piece.error_at(max(abs(piece.s0), abs(piece.s1)))
        items = [
            *((c.gas_ft.value, o.geom, o.plus_minus) for o in self.gas),
            *((c.ac_ft.value, o.geom, o.plus_minus) for o in self.ac),
            *((c.pool_ft.value, o.geom, o.plus_minus) for o in self.pool),
            *((c.opening_ft.value, o.geom, o.plus_minus) for o in self.openings),
            *((c.drive_ft.value, g.polygon, g.plus_minus) for g in self.drives),
        ]
        out: list[tuple[Geometry, float]] = []
        for t, geom, err in items:
            for d in (t, t + err + ew, t - err - ew):
                if d > 0:
                    out.append((geom, d))
        for g in self.scene.ground:
            for d in (0.0, g.plus_minus + ew, -(g.plus_minus + ew)):
                out.append((g.polygon, d))
        return out

    def candidates(self, budget_s: float) -> list[Candidate]:
        started = time.perf_counter()
        starts = [(piece, s0) for piece in self.scene.walls for s0 in self.starts(piece)]
        if len(starts) > MAX_STARTS:
            raise SceneTooComplex(
                f"the scene needs {len(starts)} battery positions checked, more than the "
                f"{MAX_STARTS} this server evaluates"
            )
        out = []
        for i, (piece, s0) in enumerate(starts):
            elapsed = time.perf_counter() - started
            if i % 64 == 0 and elapsed > budget_s:
                raise SceneTooComplex(
                    f"checking the scene took longer than {budget_s:g} seconds "
                    f"({i} of {len(starts)} positions done)"
                )
            # Refuse as soon as the pace shows the budget can't be met, rather than after
            # spending it.
            if i == PROJECT_AFTER and elapsed / i * len(starts) > budget_s:
                raise SceneTooComplex(
                    f"checking its {len(starts)} battery positions would take about "
                    f"{elapsed / i * len(starts):.0f} seconds, more than the {budget_s:g} this "
                    "server allows"
                )
            out.append(self.evaluate(piece, s0))
        return out

    def out_of_reach(self) -> list[dict[str, Any]]:
        """Sweep runs for wall that is not evaluated start by start: segments too short for the
        battery, and stretches too far along for any route to pass."""
        runs = []

        def fail_run(piece: Piece, a: float, b: float, check_id: str) -> dict[str, Any]:
            return {
                "wall_id": piece.wall_id,
                "segment": piece.index,
                "start_ft": [_round(a), _round(b)],
                "outcome": FAIL,
                "failing": [check_id],
                "unsure": [],
            }

        for piece in self.scene.walls:
            limit = self.reach_limit(piece)
            lo, hi = piece.s0, piece.s1 - self.W
            if hi < lo - EPS:
                # Too short for the battery: any start runs off the end of the segment.
                runs.append(fail_run(piece, lo, lo, "wall_backing"))
                continue
            for a, b in ((lo, min(hi, -limit - self.W)), (max(lo, limit), hi)):
                if b - a > EPS:
                    runs.append(fail_run(piece, a, b, "route_length"))
        return runs


def _coords_of(geom: Geometry) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for part in getattr(geom, "geoms", [geom]):
        if not part.is_empty and hasattr(part, "coords"):
            out += [(x, z) for x, z in part.coords]
    return out


def evaluate_start(
    scene: Scene, loaded: LoadedRules, s0: float, wall_id: str | None = None
) -> Candidate:
    """Evaluate one battery start position (its left edge at s0), even one that crosses a corner.
    The footprint follows the straight segment that contains s0."""
    solver = Solver(scene, loaded)
    walls = [p for p in scene.walls if wall_id is None or p.wall_id == wall_id]
    piece = next((p for p in walls if p.s0 - EPS <= s0 < p.s1 - EPS), walls[-1])
    return solver.evaluate(piece, s0)


# --- decision and result -------------------------------------------------------------------------


def _rank_pass(c: Candidate) -> tuple:
    return (c.route.length, abs((c.s0 + c.s1) / 2), c.s0)


def _rank_unsure(c: Candidate) -> tuple:
    return (len(c.unsure()), c.route.length, c.s0)


def _spot_json(solver: Solver, c: Candidate) -> dict[str, Any]:
    p = c.piece
    corners = [p.point(c.s0), p.point(c.s1), p.point(c.s1, solver.D), p.point(c.s0, solver.D)]
    center = p.point((c.s0 + c.s1) / 2, solver.D / 2)
    mx, mz = solver.scene.meter_xz
    return {
        "outcome": c.outcome,
        "wall_id": solver.scene.wall_at((c.s0 + c.s1) / 2),
        "segment": p.index,
        "span_ft": [_round(c.s0), _round(c.s1)],
        "width_ft": _round(solver.W),
        "depth_ft": _round(solver.D),
        "height_ft": _round(solver.H),
        "footprint": [[_round(x), _round(z)] for x, z in corners],
        "center": [_round(center[0]), _round(center[1])],
        "along": [_round(p.along[0]), _round(p.along[1])],
        "outward": [_round(p.outward[0]), _round(p.outward[1])],
        "meter_offset_ft": [_round(center[0] - mx), _round(center[1] - mz)],
        "route_length_ft": _round(c.route.length),
    }


def _route_json(solver: Solver, c: Candidate) -> dict[str, Any]:
    rt = c.route
    return {
        "outcome": rt.outcome,
        "length_ft": _round(rt.length),
        "plus_minus_ft": _round(rt.plus_minus),
        "height_ft": _round(solver.r.route.height_ft.value),
        "polyline": [[_round(x), _round(z)] for x, z in rt.polyline],
        "detours": [
            {"subject": d["subject"], "extra_ft": _round(d["extra_ft"])} for d in rt.detours
        ],
        "crossings": [
            {
                "subject": x["subject"],
                "span_ft": [_round(v) for v in x["span_ft"]],
                "effect": x["effect"],
            }
            for x in rt.crossings
        ],
    }


_DEPTH_TEXT = {
    "ground": ", at least {} out from the wall",
    "facing": ", clear at least {} out from the wall",
    "overhead": ", clear at least {} up",
}
_BAND_TEXT = {
    "wall": "wall",
    "ground": "ground in front of the wall",
    "overhead": "space overhead",
    "facing": "gap in front of the wall",
}


def _missing_json(c: Candidate, scene: Scene) -> list[dict[str, Any]]:
    by_band: dict[str, list[tuple[View, str]]] = {}
    for chk in c.checks:
        for view in chk.all_missing():
            by_band.setdefault(view.band, []).append((view, chk.id))
    out = []
    for band, items in by_band.items():
        for a, b in merge_intervals([(v.a, v.b) for v, _ in items]):
            within = [(v, i) for v, i in items if v.a < b + EPS and v.b > a - EPS]
            depths = [v.out for v, _ in within if v.out is not None]
            request: dict[str, Any] = {
                "kind": "band",
                "band": band,
                "span_ft": _outward(a, b),
                "checks": sorted({i for _, i in within}),
            }
            text = f"Show the {_BAND_TEXT[band]} from {_point(a)} to {_point(b)}"
            if depths:
                request["out_ft"] = _up(max(depths))
                text += _DEPTH_TEXT[band].format(ft(request["out_ft"]))
            request["message"] = text + "." + _past_end_hint(scene, a, b)
            out.append(request)
    return out


def _past_end_hint(scene: Scene, a: float, b: float) -> str:
    """Only a limit end has requests past it (see Scene.coverable); they are met from where the
    walk stopped."""
    sides = [
        s for s, past in (("left", a < scene.s_min - EPS), ("right", b > scene.s_max + EPS)) if past
    ]
    if not sides:
        return ""
    return (
        f" Part of it is past the {' and '.join(sides)} end: point the camera there from the end."
    )


def _sweep_json(cands: list[Candidate], scene: Scene, step: float) -> list[dict[str, Any]]:
    """Merge evaluated starts into runs. A run only grows by a start on the same straight piece
    within one sweep step of the previous one, so it never claims starts nobody evaluated."""
    runs: list[dict[str, Any]] = []
    prev: Candidate | None = None
    for c in sorted(cands, key=lambda c: c.s0):
        wall = scene.wall_at((c.s0 + c.s1) / 2)
        key = (wall, c.piece, c.outcome, sorted(c.failing()), sorted(c.unsure()))
        adjacent = prev is not None and prev.piece == c.piece and c.s0 - prev.s0 <= step + EPS
        prev = c
        if runs and adjacent and runs[-1]["_key"] == key:
            runs[-1]["start_ft"][1] = _round(c.s0)
        else:
            runs.append(
                {
                    "_key": key,
                    "wall_id": wall,
                    "segment": c.piece.index,
                    "start_ft": [_round(c.s0), _round(c.s0)],
                    "outcome": c.outcome,
                    "failing": key[3],
                    "unsure": key[4],
                }
            )
    for run in runs:
        del run["_key"]
    return runs


def solve(scene: Scene, loaded: LoadedRules, budget_s: float = SOLVE_BUDGET_S) -> dict[str, Any]:
    """Decide where the battery goes. Pure apart from the elapsed time it reports.

    Raises SceneTooComplex when the scene needs more positions checked, or more time, than a
    request may take."""
    started = time.perf_counter()
    solver = Solver(scene, loaded)
    r = loaded.rules
    cands = solver.candidates(budget_s)
    far = solver.out_of_reach()
    passes = [c for c in cands if c.outcome == PASS]
    unsures = [c for c in cands if c.outcome == UNSURE]
    fails = [c for c in cands if c.outcome == FAIL]
    auto = r.policy.auto_approve and r.policy.id is not None

    ends: dict[str, dict[str, Any]] = {}
    for side, s_end, pt, piece in (
        ("left", scene.s_min, scene.walls[0].a, scene.walls[0]),
        ("right", scene.s_max, scene.walls[-1].b, scene.walls[-1]),
    ):
        # Any spot past this end has its near edge at least |s_end| out.
        beyond = abs(s_end) > solver.reach_limit(piece)
        ends[side] = {
            "kind": scene.end_kinds[side],
            "s_ft": _round(s_end),
            "point": [_round(pt[0]), _round(pt[1])],
            "beyond_reach": beyond,
        }
    open_ends = [s for s, e in ends.items() if e["kind"] == "unexplored" and not e["beyond_reach"]]

    def past_end_requests() -> list[dict[str, Any]]:
        return [
            {
                "kind": "past_end",
                "side": side,
                "span_ft": [ends[side]["s_ft"], ends[side]["s_ft"]],
                "message": (
                    f"Keep walking past the {side} end of the scan ({where(ends[side]['s_ft'])}): "
                    "a spot within reach may be there."
                ),
            }
            for side in open_ends
        ]

    reasons: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    spot = best = nearest = None
    unexplored_reason = {
        "code": "unexplored_end",
        "message": "The wall continues past an end of the scan within cable reach.",
    }
    policy_reason = {
        "code": "policy_not_approved",
        "message": (
            "The rules in use are not approved for automatic decisions (no policy selected, or "
            "placeholder values), so a person must confirm."
        ),
    }
    if passes:
        best = spot = min(passes, key=_rank_pass)
        reasons.append(
            {"code": "all_checks_pass", "message": "A fully observed spot passes every check."}
        )
        decision = "pass" if auto else "manual_review"
        if not auto:
            reasons.append(policy_reason)
        summary = (
            f"The battery fits {where((best.s0 + best.s1) / 2)} with a "
            f"{format_ft_in(best.route.length)} cable run"
            + (": every check passes." if auto else "; a person must confirm under these rules.")
        )
    elif unsures:
        best = spot = min(unsures, key=_rank_unsure)
        decision = "manual_review"
        ids = best.unsure()
        labels = [c.label.lower() for c in best.checks if c.outcome == UNSURE]
        reasons.append(
            {
                "code": "unsure_checks",
                "checks": ids,
                "message": "The best spot has checks nobody can settle from this scan: "
                + "; ".join(labels)
                + ".",
            }
        )
        missing = _missing_json(best, scene)
        if missing:
            reasons.append(
                {
                    "code": "unobserved_area",
                    "checks": sorted({i for m in missing for i in m["checks"]}),
                    "message": "Part of the area the checks need was not seen.",
                }
            )
        if open_ends:
            reasons.append(unexplored_reason)
            missing += past_end_requests()
        spot_at = where((best.s0 + best.s1) / 2)
        if any(c.unsure_cause == "unobserved" for c in best.checks if c.outcome == UNSURE):
            summary = (
                f"More views are needed around the best spot, {spot_at}: "
                + ("1 check depends" if len(ids) == 1 else f"{len(ids)} checks depend")
                + " on areas the scan did not see."
            )
        else:
            summary = (
                f"A person needs to check the best spot, {spot_at}: " + "; ".join(labels) + "."
            )
    else:
        best = nearest = min(
            fails,
            key=lambda c: (len(c.failing()), len(c.unsure()), c.route.length, c.s0),
            default=None,
        )
        fail_counts: dict[str, int] = {}
        for c in fails:
            for i in c.failing():
                fail_counts[i] = fail_counts.get(i, 0) + 1
        if far:
            fail_counts["route_length"] = fail_counts.get("route_length", 0) + len(far)
        top = sorted(fail_counts, key=lambda i: -fail_counts[i])
        if cands or far:
            reasons.append(
                {
                    "code": "all_spots_fail",
                    "checks": top,
                    "message": "Every spot on the walls scanned fails at least one check by a "
                    "clear margin.",
                }
            )
        else:
            reasons.append(
                {
                    "code": "no_wall_segment_fits",
                    "message": "No straight stretch of scanned wall is as wide as the battery.",
                }
            )
        can_reject = auto and r.policy.allow_reject and not open_ends
        if open_ends:
            reasons.append(unexplored_reason)
            missing = past_end_requests()
        if not auto:
            reasons.append(policy_reason)
        decision = "reject" if can_reject else "manual_review"
        if decision == "reject":
            summary = (
                "No spot within reach works: every spot fails "
                + (", ".join(t.replace("_", " ") for t in top[:3]) or "the checks")
                + "."
            )
        elif open_ends:
            summary = (
                "No spot on the scanned walls works; walk further to look for one within reach."
            )
        else:
            summary = "No spot on the scanned walls works; a person must confirm before rejecting."

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": decision,
        # Every answer names whose rules decided it when the policy asks (the public demo).
        "summary": f"{summary} {r.policy.notice}" if r.policy.notice else summary,
        "reasons": reasons,
        "policy": {
            "id": r.policy.id,
            "version": r.policy.version,
            "auto_approve": auto,
            "sources": list(loaded.sources),
            "rules_sha256": loaded.sha256,
            "notice": r.policy.notice,
        },
        "spot": _spot_json(solver, spot) if spot else None,
        "route": _route_json(solver, spot) if spot else None,
        "checks": [c.to_json(loaded.private_keys) for c in best.checks] if best else [],
        "nearest_considered": _spot_json(solver, nearest) if nearest else None,
        "missing_evidence": missing,
        "ends": ends,
        "sweep": sorted(
            _sweep_json(cands, scene, r.sweep.step_ft.value) + far,
            key=lambda run: (run["start_ft"][0], run["segment"]),
        ),
        "stats": {
            "candidates": len(cands),
            "pass": len(passes),
            "unsure": len(unsures),
            "fail": len(fails),
            "elapsed_ms": round(elapsed_ms, 3),
            "input_sha256": scene.input_sha256,
        },
    }
