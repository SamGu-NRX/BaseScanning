"""Turn an uploaded scene.json into plan geometry the solver can measure.

The walls are chained left to right and unrolled into one coordinate s, the distance along the
chain from the meter (negative to the left). Every straight baseline segment knows its outward
direction from its point order (scene.schema.json: points run left to right as seen from outside,
so outward is the direction turned 90 degrees clockwise viewed from above). Clearances are measured
in plan (x, z) with shapely, so two things close together round an inside corner are close even
when they are far apart in s.

What the capture observed is kept as geometry too. The unobserved part of the outdoor area near the
walls is precomputed once, so "was everything within 3 ft of this footprint seen?" is one distance.
"""

import hashlib
import itertools
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from shapely import Geometry, LineString, Point, Polygon, unary_union
from shapely.validation import explain_validity

from rules import Rules

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "scene.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))

# Tolerance for comparing lengths in feet. Far below any measurement error; it only keeps floating
# point noise (3.3 - 0.3 != 3.0) from deciding a check.
EPS = 1e-9
# Arc resolution for the outdoor-area wedges at corners.
_ARC_STEPS = 8

Point2 = tuple[float, float]


class SceneError(ValueError):
    """The upload is not a usable scene. `path` points at the offending field."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _sub(a: Point2, b: Point2) -> Point2:
    return (a[0] - b[0], a[1] - b[1])


def _norm(v: Point2) -> float:
    return math.hypot(v[0], v[1])


@dataclass(frozen=True)
class Piece:
    """A straight stretch of the chain in s: a wall segment, a gap with no wall, or an extension
    that continues an end segment past the end of the walk."""

    kind: str  # "wall", "gap" or "extension"
    wall_id: str | None
    index: int  # segment index within its wall; -1 for gaps and extensions
    a: Point2
    b: Point2
    s0: float
    s1: float
    along: Point2
    outward: Point2
    plus_minus: float
    # Extra error per foot walked from the meter, for walls placed by AR taps without their own
    # plus_minus_ft (rules.yaml errors.drift_per_ft).
    drift: float = 0.0

    def error_at(self, s: float) -> float:
        """Position error of this piece's line at s."""
        return self.plus_minus + self.drift * abs(s)

    def point(self, s: float, out: float = 0.0) -> Point2:
        t = s - self.s0
        return (
            self.a[0] + self.along[0] * t + self.outward[0] * out,
            self.a[1] + self.along[1] * t + self.outward[1] * out,
        )

    def rect(self, s_lo: float, s_hi: float, out_lo: float, out_hi: float) -> Polygon:
        return Polygon(
            [
                self.point(s_lo, out_lo),
                self.point(s_hi, out_lo),
                self.point(s_hi, out_hi),
                self.point(s_lo, out_hi),
            ]
        )

    def local(self, p: Point2) -> Point2:
        """(s, out) of a plan point relative to this piece's line."""
        d = _sub(p, self.a)
        return (
            self.s0 + d[0] * self.along[0] + d[1] * self.along[1],
            d[0] * self.outward[0] + d[1] * self.outward[1],
        )


@dataclass(frozen=True)
class SceneObject:
    index: int
    type: str
    wall_id: str
    span: tuple[float, float]
    bottom: float | None
    top: float | None
    operable: bool | None
    well: bool | None
    source: str
    plus_minus: float
    geom: Geometry

    @property
    def label(self) -> str:
        return f"objects[{self.index}] {self.type}"


@dataclass(frozen=True)
class GroundPatch:
    index: int
    type: str
    polygon: Polygon
    plus_minus: float


@dataclass(frozen=True)
class Measured:
    """An overhead clearance or a facing gap over a stretch of wall."""

    index: int
    span: tuple[float, float]
    value: float
    plus_minus: float


@dataclass
class Scene:
    input_sha256: str
    pieces: list[Piece]  # extension, walls and gaps in chain order, extension
    meter_pos: tuple[float, float, float]
    meter_plus_minus: float
    meter_piece: Piece
    # Each uploaded wall segment, numbered as uploaded, before collinear points and walls are
    # merged: (wall id, segment index, s start, s end, the wall's declared height_ft or None).
    # What a spot's wall_id and segment refer to, and the height a battery backs onto.
    wall_spans: list[tuple[str, int, float, float, float | None]]
    objects: list[SceneObject]
    # Objects with no footprint past an unexplored end: (index, type, side, s at their middle).
    # The wall may turn there, so they have no known place and are not measured (issue #42).
    set_aside: list[tuple[int, str, str, float]]
    ground: list[GroundPatch]
    overheads: list[Measured]
    facing: list[Measured]
    observed: dict[str, list[tuple[float, float, float | None]]]
    end_kinds: dict[str, str]
    reach_ft: float  # how far out from the walls the outdoor area is modelled
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    # --- chain -----------------------------------------------------------------------------------

    @property
    def walls(self) -> list[Piece]:
        return [p for p in self.pieces if p.kind == "wall"]

    @property
    def gaps(self) -> list[Piece]:
        return [p for p in self.pieces if p.kind == "gap"]

    @property
    def s_min(self) -> float:
        return self.walls[0].s0

    @property
    def s_max(self) -> float:
        return self.walls[-1].s1

    @property
    def meter_xz(self) -> Point2:
        return (self.meter_pos[0], self.meter_pos[2])

    def segment_at(self, s: float) -> tuple[str, int]:
        """The uploaded wall and its segment under s: both from the same original segment, since
        a joined straight piece can span two walls."""
        wid, index, _, _, _ = min(self.wall_spans, key=lambda w: max(w[2] - s, s - w[3], 0.0))
        return wid, index

    def lowest_wall(self, s_lo: float, s_hi: float) -> float | None:
        """The lowest declared height of the uploaded walls over [s_lo, s_hi], or None."""
        heights = [
            h
            for _, _, a, b, h in self.wall_spans
            if h is not None and a < s_hi - EPS and b > s_lo + EPS
        ]
        return min(heights, default=None)

    def wall_at(self, s: float) -> str:
        return self.segment_at(s)[0]

    def piece_at(self, s: float) -> Piece:
        for p in self.pieces:
            if p.s0 - EPS <= s <= p.s1 + EPS:
                return p
        return self.pieces[0] if s < self.pieces[0].s0 else self.pieces[-1]

    def point_at(self, s: float, out: float = 0.0) -> Point2:
        return self.piece_at(s).point(s, out)

    def polyline(self, s_a: float, s_b: float) -> list[Point2]:
        """Plan points along the chain from s_a to s_b (either order), corners included."""
        lo, hi = min(s_a, s_b), max(s_a, s_b)
        pts = [self.point_at(lo)]
        for p in self.pieces:
            if lo + EPS < p.s1 < hi - EPS:
                pts.append(p.b)
        pts.append(self.point_at(hi))
        return pts if s_a <= s_b else pts[::-1]

    def _past_limit(self, p: Piece) -> bool:
        """Whether p is the straight continuation past an end the walk marked as a limit."""
        side = "left" if p is self.pieces[0] else "right" if p is self.pieces[-1] else None
        return side is not None and self.end_kinds.get(side) == "limit"

    def band_polygon(self, s_lo: float, s_hi: float, out: float) -> Geometry:
        """The ground strip in front of the chain from s_lo to s_hi, out to `out` from the wall,
        with the wedges that fill the outside of convex corners. Past a limit end there is no
        house behind the continued line, so the strip there covers both sides of it: a fence or
        corner doesn't make the ground beyond it clear, and a camera pointed past the end from
        where the walk stopped sees both sides."""
        if s_hi - s_lo <= EPS or out <= EPS:
            return Polygon()
        parts: list[Geometry] = []
        inside = [p for p in self.pieces if p.s1 > s_lo + EPS and p.s0 < s_hi - EPS]
        for p in inside:
            near = -out if self._past_limit(p) else 0.0
            parts.append(p.rect(max(s_lo, p.s0), min(s_hi, p.s1), near, out))
        for prev, nxt in itertools.pairwise(inside):
            parts.append(_wedge(prev.b, prev.outward, nxt.outward, out))
        return unary_union(parts)

    def s_of(self, p: Point2) -> float:
        """s of the chain point nearest to a plan point."""
        best = min(self.pieces, key=lambda q: Point(p).distance(LineString([q.a, q.b])))
        return min(max(best.local(p)[0], best.s0), best.s1)

    def s_extent(self, geom: Geometry) -> tuple[float, float] | None:
        """The stretch of chain a region lies in front of."""
        if geom.is_empty:
            return None
        coords: list[Point2] = []
        for part in getattr(geom, "geoms", [geom]):
            ring = part.exterior if hasattr(part, "exterior") else part
            coords += [(x, z) for x, z in ring.coords]
        values = [self.s_of(c) for c in coords]
        return (min(values), max(values))

    def wall_line(self, s_lo: float, s_hi: float) -> Geometry:
        if s_hi - s_lo <= EPS:
            return Point(self.point_at(s_lo))
        return LineString(self.polyline(s_lo, s_hi))

    # --- coverage --------------------------------------------------------------------------------

    def observed_intervals(
        self, band: str, up_to: float | None = None
    ) -> list[tuple[float, float]]:
        """Observed stretches of a 1D band. With `up_to`, only views that reached above that
        height count: a wall entry's out_ft is how high up the face was seen, and one without
        out_ft saw the whole band."""
        return merge_intervals(
            [
                (a, b)
                for a, b, out in self.observed.get(band, [])
                if up_to is None or out is None or out > up_to
            ]
        )

    def missing(
        self, band: str, s_lo: float, s_hi: float, up_to: float | None = None
    ) -> list[tuple[float, float]]:
        """Parts of [s_lo, s_hi] not observed in a 1D band (above `up_to`, see
        observed_intervals). A gap narrower than COVERAGE_TOLERANCE_FT is rounding between the
        capture's spans and the unrolled walls (the app reported a wall seen to 15 ft that
        unrolls to 15.00006 ft), not an unseen stretch."""
        gaps = subtract_intervals((s_lo, s_hi), self.observed_intervals(band, up_to))
        return [(a, b) for a, b in gaps if b - a >= COVERAGE_TOLERANCE_FT]

    def unobserved_ground(self) -> Geometry:
        if "ground" not in self._cache:
            lo, hi = self.pieces[0].s0, self.pieces[-1].s1
            outdoor = self.band_polygon(lo, hi, self.reach_ft)
            seen = unary_union(
                [
                    self.band_polygon(a, b, out or 0.0)
                    for a, b, out in self.observed.get("ground", [])
                ]
            )
            # Growing what was seen by half the tolerance closes gaps between observed spans
            # narrower than it, as missing() ignores them for the 1D bands.
            unseen = outdoor.difference(seen.buffer(COVERAGE_TOLERANCE_FT / 2))
            self._cache["ground"] = unary_union([unseen, *self._unexplored_discs()])
        return self._cache["ground"]

    def _unexplored_discs(self) -> list[Geometry]:
        """Round an unexplored end the walls may turn any way, so the straight extension past it
        proves nothing: everything within reach of that end counts as unseen, except ground in
        front of the scanned walls that was actually observed and the house behind them."""
        ends = {"left": self.walls[0].a, "right": self.walls[-1].b}
        discs = [
            Point(ends[side]).buffer(self.reach_ft)
            for side, kind in self.end_kinds.items()
            if kind == "unexplored"
        ]
        if not discs:
            return []
        seen_in_front = unary_union(
            [
                self.band_polygon(max(a, self.s_min), min(b, self.s_max), out or 0.0)
                for a, b, out in self.observed.get("ground", [])
            ]
        ).buffer(COVERAGE_TOLERANCE_FT / 2)
        # Behind a scanned segment is the house itself, not ground a hazard could hide on.
        behind = unary_union([p.rect(p.s0, p.s1, -self.reach_ft, 0.0) for p in self.walls])
        known = unary_union([seen_in_front, behind])
        return [d.difference(known) for d in discs]

    def seen_to(self, band: str, s_lo: float, s_hi: float) -> float:
        """How far out (or up) the band was seen over all of [s_lo, s_hi]: at each s the
        deepest view covering it, and over the stretch the shallowest of those. A view without
        `out_ft` saw all the way (infinite); a stretch no view covers gives 0."""
        spans = [
            (a, b, math.inf if out is None else out) for a, b, out in self.observed.get(band, [])
        ]
        cuts = sorted({s_lo, s_hi} | {x for a, b, _ in spans for x in (a, b) if s_lo < x < s_hi})
        least = math.inf
        for p, q in itertools.pairwise(cuts):
            if q - p <= EPS:
                continue
            here = max((out for a, b, out in spans if a <= p + EPS and q - EPS <= b), default=0.0)
            least = min(least, here)
        return least

    def farthest_out(self, region: Geometry) -> float:
        """The largest distance from the chain's line (walls and their continuations past the
        ends) of any point of `region`: how far out a ground view must reach to cover it."""
        line = LineString(self.polyline(self.pieces[0].s0, self.pieces[-1].s1))
        points = [
            Point(x, z)
            for part in getattr(region, "geoms", [region])
            if not part.is_empty
            for x, z in (part.exterior.coords if hasattr(part, "exterior") else part.coords)
        ]
        return max((line.distance(p) for p in points), default=0.0)

    def coverable(self, band: str) -> Geometry:
        """Where observing `band` can settle what is unseen: in front of the scanned walls, and
        past a limit end. Past an unexplored end the walls may turn any way, so no view settles
        it; only walking on does (a past_end request)."""
        key = f"coverable-{band}"
        if key not in self._cache:
            left, right = self.pieces[0], self.pieces[-1]
            lo = self.s_min if self.end_kinds.get("left") == "unexplored" else left.s0
            hi = self.s_max if self.end_kinds.get("right") == "unexplored" else right.s1
            if band == "ground":
                self._cache[key] = self.band_polygon(lo, hi, self.reach_ft)
            else:
                self._cache[key] = self.wall_line(lo, hi).buffer(1e-6, cap_style="flat")
        return self._cache[key]

    def unobserved_wall_lines(self, up_to: float | None = None) -> Geometry:
        """Stretches of the chain's line nobody saw up to height `up_to`, the part a view of the
        wall settles."""
        key = f"wall-lines-{up_to}"
        if key not in self._cache:
            lo, hi = self.pieces[0].s0, self.pieces[-1].s1
            gaps = self.missing("wall", lo, hi, up_to)
            self._cache[key] = unary_union([self.wall_line(a, b) for a, b in gaps])
        return self._cache[key]

    def unexplored_area(self) -> Geometry:
        """Where the walls past an unexplored end might run: in front of the scanned walls only
        a view of the ground there rules that out."""
        if "unexplored" not in self._cache:
            self._cache["unexplored"] = unary_union(self._unexplored_discs())
        return self._cache["unexplored"]

    def unobserved_wall(self, up_to: float | None = None) -> Geometry:
        key = f"wall-{up_to}"
        if key not in self._cache:
            self._cache[key] = unary_union(
                [self.unobserved_wall_lines(up_to), self.unexplored_area()]
            )
        return self._cache[key]


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + EPS:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract_intervals(
    target: tuple[float, float], covered: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    lo, hi = target
    missing: list[tuple[float, float]] = []
    cursor = lo
    for a, b in merge_intervals(covered):
        if b <= cursor + EPS:
            continue
        if a >= hi - EPS:
            break
        if a > cursor + EPS:
            missing.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
        if cursor >= hi - EPS:
            break
    if cursor < hi - EPS:
        missing.append((cursor, hi))
    return missing


def _wedge(v: Point2, n1: Point2, n2: Point2, radius: float) -> Polygon:
    a1, a2 = math.atan2(n1[1], n1[0]), math.atan2(n2[1], n2[0])
    delta = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    pts = [v]
    for k in range(_ARC_STEPS + 1):
        ang = a1 + delta * k / _ARC_STEPS
        # Scale up so the polygon's chords stay outside the arc.
        r = radius / math.cos(abs(delta) / (2 * _ARC_STEPS))
        pts.append((v[0] + r * math.cos(ang), v[1] + r * math.sin(ang)))
    poly = Polygon(pts)
    return poly if poly.is_valid and poly.area > EPS else Polygon()


# Baseline points closer than this (0.6 in) to the line through their neighbours are one straight
# wall. Deliberately well below the tap error: a real step in the wall must stay a corner.
COLLINEAR_FT = 0.05


def _uploaded_spans(
    wid: str, uploaded: list[Point2], s0: float, s1: float, height: float | None
) -> list[tuple[str, int, float, float, float | None]]:
    """Each uploaded segment of a wall with its stretch of s, numbered as uploaded. The segments
    are laid out by their lengths, scaled to the merged wall's [s0, s1], since merging points
    within COLLINEAR_FT of a line shortens the path by a hair."""
    lengths = [_norm(_sub(b, a)) for a, b in itertools.pairwise(uploaded)]
    scale = (s1 - s0) / sum(lengths)
    out, s = [], s0
    for i, length in enumerate(lengths):
        out.append((wid, i, s, s + length * scale, height))
        s += length * scale
    return out


def _merge_collinear(pts: list[Point2], tol: float, path: str) -> list[Point2]:
    """Drop baseline points that lie on the straight line between their neighbours (within the
    wall's error), so a tap in the middle of a straight wall is not a corner the battery can't
    straddle."""
    out = [pts[0]]
    dropped: list[Point2] = []
    for i in range(1, len(pts) - 1):
        a, b = out[-1], pts[i + 1]
        # Every point dropped since the last kept one must stay on the new chord, so error
        # can't accumulate along a gently curving line of taps.
        if all(_on_chord(a, q, b, tol, f"{path}/{i + 1}") for q in [*dropped, pts[i]]):
            dropped.append(pts[i])
        else:
            out.append(pts[i])
            dropped = []
    out.append(pts[-1])
    return out


def _on_chord(a: Point2, p: Point2, b: Point2, tol: float, path: str) -> bool:
    ab = _sub(b, a)
    length = _norm(ab)
    if length < 1e-6:
        raise SceneError(path, "repeats an earlier point")
    t_along = ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / (length * length)
    off = abs((p[0] - a[0]) * ab[1] - (p[1] - a[1]) * ab[0]) / length
    return 0 < t_along < 1 and off <= tol


def _join_straight_walls(pieces: list[Piece]) -> list[Piece]:
    """Two walls that meet in a straight line are one straight stretch a battery can span. The
    joined piece keeps the first wall's id; Scene.wall_at names the wall under any s."""
    out: list[Piece] = []
    for p in pieces:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == p.kind == "wall"
            and prev.wall_id != p.wall_id
            and _norm(_sub(prev.b, p.a)) <= COLLINEAR_FT
            and _on_chord(prev.a, prev.b, p.b, COLLINEAR_FT, "/walls")
        ):
            length = _norm(_sub(p.b, prev.a))
            along = ((p.b[0] - prev.a[0]) / length, (p.b[1] - prev.a[1]) / length)
            out[-1] = Piece(
                "wall",
                prev.wall_id,
                prev.index,
                prev.a,
                p.b,
                prev.s0,
                prev.s0 + length,
                along,
                _outward(along),
                max(prev.plus_minus, p.plus_minus),
                max(prev.drift, p.drift),
            )
        else:
            out.append(p)
    return out


def _outward(along: Point2) -> Point2:
    # Points run left to right seen from outside, so outward is `along` turned clockwise viewed
    # from above: (ux, uz) -> (-uz, ux).
    return (-along[1], along[0])


def _xz(point: list[float]) -> Point2:
    return (float(point[0]), float(point[1]))


def _span(value: list[float], path: str) -> tuple[float, float]:
    a, b = float(value[0]), float(value[1])
    if a > b:
        raise SceneError(path, f"span_ft must be [start, end] with start <= end, got [{a}, {b}]")
    return (a, b)


def _geometry(points: list[Point2], path: str) -> Geometry:
    if len(points) == 1:
        return Point(points[0])
    if len(points) == 2:
        return LineString(points)
    poly = Polygon(points)
    if not poly.is_valid:
        raise SceneError(path, f"polygon is not simple ({explain_validity(poly)})")
    return poly


def _error(item: dict[str, Any], default: float) -> float:
    return float(item["plus_minus_ft"]) if "plus_minus_ft" in item else default


# Coverage gaps narrower than this (1/8 in) are float noise between a capture's rounded spans and
# the unrolled walls, 25 times smaller than the smallest default position error (tape, 0.05 ft).
COVERAGE_TOLERANCE_FT = 0.01

# Coordinates beyond this are not a house scan; they would only exhaust memory in the sweep.
MAX_COORDINATE_FT = 1e5


def _check_numbers(raw: Any) -> None:
    """JSON Schema accepts NaN and infinities as numbers; nothing in a scene may be either.
    Walks with an explicit stack, so deeply nested input can't exhaust Python's recursion."""
    stack: list[tuple[Any, str]] = [(raw, "")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, dict):
            stack.extend((v, f"{path}/{k}") for k, v in value.items())
        elif isinstance(value, list):
            stack.extend((v, f"{path}/{i}") for i, v in enumerate(value))
        elif isinstance(value, float | int) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise SceneError(path or "/", f"{value!r} is not a finite number")
            if abs(value) > MAX_COORDINATE_FT:
                raise SceneError(
                    path or "/", f"{value} ft is beyond the {MAX_COORDINATE_FT:g} ft bound"
                )


def validate_schema(raw: Any) -> None:
    _check_numbers(raw)
    errors = sorted(_VALIDATOR.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        path = "/" + "/".join(str(p) for p in e.absolute_path)
        raise SceneError(path, _capped(e.message, e.instance))


# jsonschema quotes the whole offending value in its message ("<value> is too long"); a refusal
# shouldn't echo a large upload back. The quote is cut to its start, keeping the verdict after it,
# and the path already says where the problem is.
QUOTE_CHARS = 200
MESSAGE_CHARS = 1000


def _capped(message: str, instance: Any) -> str:
    quoted = repr(instance)
    if len(quoted) > QUOTE_CHARS and quoted in message:
        cut = len(quoted) - QUOTE_CHARS
        message = message.replace(quoted, f"{quoted[:QUOTE_CHARS]}… ({cut} characters cut)", 1)
    if len(message) > MESSAGE_CHARS:
        message = f"{message[:MESSAGE_CHARS]}…"
    return message


def parse_scene(raw: dict[str, Any], rules: Rules, input_bytes: bytes | None = None) -> Scene:
    """Validate against scene.schema.json, then build the unrolled chain and plan geometry."""
    validate_schema(raw)
    if input_bytes is None:
        input_bytes = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    errors = rules.errors
    drift = errors.drift_per_ft.value
    source_error = {
        "tap": errors.tap_ft.value,
        "vlm": errors.vlm_ft.value,
        "tape": errors.tape_ft.value,
    }

    # Chain the walls. s is provisional (0 at the chain's left end) until the meter is placed.
    join_tol = rules.sweep.wall_join_ft.value
    pieces: list[Piece] = []
    spans: list[tuple[str, int, float, float, float | None]] = []
    wall_ids: set[str] = set()
    s = 0.0
    prev_end: Point2 | None = None
    prev_err = prev_drift = 0.0
    for wi, wall in enumerate(raw["walls"]):
        wid = wall["id"]
        if wid in wall_ids:
            raise SceneError(f"/walls/{wi}/id", f"duplicate wall id {wid!r}")
        wall_ids.add(wid)
        pts = [_xz(p) for p in wall["baseline"]]
        # How the wall's line was found sets its default error: AR taps (also when `source` is
        # absent), the LiDAR mesh, or detected planes. All three drift with distance walked.
        default = {
            "tap": errors.wall_ft,
            "mesh": errors.mesh_ft,
            "plane": errors.plane_ft,
        }[wall.get("source", "tap")]
        wall_err = _error(wall, default.value)
        wall_drift = 0.0 if "plus_minus_ft" in wall else drift
        uploaded = pts
        pts = _merge_collinear(pts, COLLINEAR_FT, f"/walls/{wi}/baseline")
        if prev_end is not None:
            gap = _norm(_sub(pts[0], prev_end))
            # Two walls meet when the space between their ends is within both walls' errors (the
            # taps may simply disagree), capped at sweep.wall_join_ft. A wider space is a real
            # gap: a stretch with no wall, which s counts. Exact walls keep any gap above float
            # noise (EPS); coverage rounding says nothing about where walls end.
            meets = min(join_tol, max(prev_err + wall_err, EPS))
            if gap > meets:
                along = (
                    (pts[0][0] - prev_end[0]) / gap,
                    (pts[0][1] - prev_end[1]) / gap,
                )
                # The gap's length is only known to within both walls' errors.
                gap_err = prev_err + wall_err
                pieces.append(
                    Piece(
                        "gap",
                        None,
                        -1,
                        prev_end,
                        pts[0],
                        s,
                        s + gap,
                        along,
                        _outward(along),
                        gap_err,
                        prev_drift + wall_drift,
                    )
                )
                s += gap
        wall_s0 = s
        for i in range(len(pts) - 1):
            length = _norm(_sub(pts[i + 1], pts[i]))
            if length < 1e-6:
                raise SceneError(f"/walls/{wi}/baseline/{i + 1}", "repeats the previous point")
            along = ((pts[i + 1][0] - pts[i][0]) / length, (pts[i + 1][1] - pts[i][1]) / length)
            pieces.append(
                Piece(
                    "wall",
                    wid,
                    i,
                    pts[i],
                    pts[i + 1],
                    s,
                    s + length,
                    along,
                    _outward(along),
                    wall_err,
                    wall_drift,
                )
            )
            s += length
        spans += _uploaded_spans(wid, uploaded, wall_s0, s, wall.get("height_ft"))
        prev_end, prev_err, prev_drift = pts[-1], wall_err, wall_drift

    # Place the meter: s = 0 at its projection onto its own wall.
    meter = raw["meter"]
    if meter["wall_id"] not in wall_ids:
        raise SceneError("/meter/wall_id", f"no wall with id {meter['wall_id']!r}")
    mx, my, mz = (float(v) for v in meter["pos"])
    mpos = (mx, my, mz)
    mxz = (mpos[0], mpos[2])
    best: tuple[float, Piece, float, float] | None = None
    for p in pieces:
        if p.wall_id != meter["wall_id"]:
            continue
        s_local, _out = p.local(mxz)
        s_clamped = min(max(s_local, p.s0), p.s1)
        dist = _norm(_sub(mxz, p.point(s_clamped)))
        if best is None or dist < best[0]:
            best = (dist, p, s_clamped, abs(s_local - s_clamped))
    assert best is not None
    meter_err = _error(meter, errors.meter_ft.value)
    overshoot = best[3]
    if overshoot > meter_err + best[1].plus_minus + EPS:
        # Clamping would silently shorten every cable route by the overshoot.
        raise SceneError(
            "/meter/pos",
            f"the meter is {overshoot:.2f} ft past the end of wall {meter['wall_id']!r}, more "
            "than the meter's and the wall's errors allow; extend the wall to the meter",
        )
    max_off = rules.sweep.meter_to_wall_max_ft.value
    if best[0] > max_off:
        raise SceneError(
            "/meter/pos",
            f"the meter is {best[0]:.2f} ft from wall {meter['wall_id']!r}; "
            f"more than {max_off} ft means the scene is misaligned",
        )
    shift = best[2]
    pieces = [replace(p, s0=p.s0 - shift, s1=p.s1 - shift) for p in pieces]
    wall_spans = [(wid, i, a - shift, b - shift, h) for wid, i, a, b, h in spans]
    pieces = _join_straight_walls(pieces)
    meter_piece = next(p for p in pieces if p.kind == "wall" and p.s0 - EPS <= 0 <= p.s1 + EPS)

    # How far out from the walls the outdoor area matters: the largest clearance, plus the
    # battery, plus the largest wall error anywhere, because a check must have seen everything
    # within its clearance plus the footprint's own error. Anything out there nobody observed
    # then counts as unseen.
    c = rules.clearances
    reach = max(
        c.gas_ft.value,
        c.ac_ft.value,
        c.battery_ft.value,
        c.opening_ft.value,
        c.drive_ft.value,
        c.pool_ft.value,
    )
    wall_error = max(p.error_at(max(abs(p.s0), abs(p.s1))) for p in pieces if p.kind == "wall")
    reach += rules.battery.depth_ft.value + wall_error + 1.0
    first, last = pieces[0], pieces[-1]
    ext_len = reach + rules.battery.width_ft.value
    left_ext = Piece(
        "extension",
        None,
        -1,
        first.point(first.s0 - ext_len),
        first.a,
        first.s0 - ext_len,
        first.s0,
        first.along,
        first.outward,
        first.plus_minus,
        first.drift,
    )
    right_ext = Piece(
        "extension",
        None,
        -1,
        last.b,
        last.point(last.s1 + ext_len),
        last.s1,
        last.s1 + ext_len,
        last.along,
        last.outward,
        last.plus_minus,
        last.drift,
    )
    pieces = [left_ext, *pieces, right_ext]

    scene = Scene(
        input_sha256=hashlib.sha256(input_bytes).hexdigest(),
        pieces=pieces,
        meter_pos=mpos,
        meter_plus_minus=meter_err,
        meter_piece=meter_piece,
        wall_spans=wall_spans,
        objects=[],
        set_aside=[],
        ground=[],
        overheads=[],
        facing=[],
        observed={},
        end_kinds={"left": "unexplored", "right": "unexplored"},
        reach_ft=reach,
    )

    # What each end is decides where an object without a footprint can be placed, so it is read
    # before the objects.
    for side, end in raw.get("coverage", {}).get("ends", {}).items():
        scene.end_kinds[side] = end["kind"]

    for i, obj in enumerate(raw.get("objects", [])):
        path = f"/objects/{i}"
        if obj["wall_id"] not in wall_ids:
            raise SceneError(f"{path}/wall_id", f"no wall with id {obj['wall_id']!r}")
        span = _span(obj["span_ft"], f"{path}/span_ft")
        bottom, top = obj.get("bottom_ft"), obj.get("top_ft")
        if bottom is not None and top is not None and bottom > top:
            raise SceneError(path, f"bottom_ft {bottom} is above top_ft {top}")
        if "footprint" in obj:
            geom = _geometry([_xz(p) for p in obj["footprint"]], f"{path}/footprint")
        else:
            # Without a footprint an object lies on the wall's line. Past an unexplored end that
            # line may not exist (the wall may turn), so only the part on the scanned wall counts.
            lo, hi = span
            if scene.end_kinds["left"] == "unexplored":
                lo = max(lo, scene.s_min)
            if scene.end_kinds["right"] == "unexplored":
                hi = min(hi, scene.s_max)
            # Set aside when nothing of a mark with length remains on the scanned wall (its span
            # can start exactly at the end, leaving a single point); a point mark stays.
            if hi < lo - EPS or (span[1] - span[0] > EPS and hi - lo <= EPS):
                # A span starting exactly at the right end is past it too.
                side = "right" if span[0] >= scene.s_max - EPS else "left"
                scene.set_aside.append((i, obj["type"], side, (span[0] + span[1]) / 2))
                continue
            geom = scene.wall_line(lo, max(lo, hi))
        attrs = obj.get("attrs", {})
        scene.objects.append(
            SceneObject(
                index=i,
                type=obj["type"],
                wall_id=obj["wall_id"],
                span=span,
                bottom=None if bottom is None else float(bottom),
                top=None if top is None else float(top),
                operable=attrs.get("operable"),
                well=attrs.get("well"),
                source=obj["source"],
                plus_minus=_error(
                    obj,
                    source_error[obj["source"]]
                    + (drift * max(abs(span[0]), abs(span[1])) if obj["source"] != "tape" else 0),
                ),
                geom=geom,
            )
        )

    for i, patch in enumerate(raw.get("ground", [])):
        poly = _geometry([_xz(p) for p in patch["polygon"]], f"/ground/{i}/polygon")
        walked = max(abs(scene.s_of(c)) for c in poly.exterior.coords)
        default = errors.tap_ft.value + drift * walked
        scene.ground.append(GroundPatch(i, patch["type"], poly, _error(patch, default)))

    for key, target, value_key in (
        ("overheads", scene.overheads, "clearance_ft"),
        ("facing", scene.facing, "depth_ft"),
    ):
        for i, item in enumerate(raw.get(key, [])):
            if item["wall_id"] not in wall_ids:
                raise SceneError(f"/{key}/{i}/wall_id", f"no wall with id {item['wall_id']!r}")
            target.append(
                Measured(
                    i,
                    _span(item["span_ft"], f"/{key}/{i}/span_ft"),
                    float(item[value_key]),
                    _error(item, errors.mesh_ft.value),
                )
            )

    coverage = raw.get("coverage", {})
    for i, obs in enumerate(coverage.get("observed", [])):
        span = _span(obs["span_ft"], f"/coverage/observed/{i}/span_ft")
        scene.observed.setdefault(obs["band"], []).append((span[0], span[1], obs.get("out_ft")))

    _check_orientation(scene, raw.get("keyframes", []))
    return scene


def _check_orientation(scene: Scene, keyframes: list[dict[str, Any]]) -> None:
    """Cameras stand outside the house. If most keyframes sit on the inward side of the wall they
    face, the baseline points were almost certainly ordered right to left."""
    walls = scene.walls
    inward = total = 0
    for kf in keyframes:
        pose = kf["pose"]
        cam = (float(pose[12]), float(pose[14]))
        nearest = min(walls, key=lambda p: Point(cam).distance(LineString([p.a, p.b])))
        s_local, out = nearest.local(cam)
        if nearest.s0 <= s_local <= nearest.s1:
            total += 1
            inward += out < 0
    if total >= 3 and inward * 2 > total:
        raise SceneError(
            "/walls",
            f"{inward} of {total} keyframe cameras sit on the inward side of the wall they face; "
            "baseline points must run left to right as seen from outside",
        )
