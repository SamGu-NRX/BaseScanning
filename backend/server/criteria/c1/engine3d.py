"""C1: placement rules on a multi-wall 3D scene.

Extends PR #11's solver (house-scanning t3/server, server/solver.py) from one unrolled wall chain
to a graph of walls in 3D. What is kept from PR #11:
- the strict three-valued margin rule (PASS needs margin > error, FAIL needs a miss > error,
  otherwise UNSURE) and "unseen is never clear";
- the check set (wall backing, ground, meter working space, gas / AC / drive / pool / opening
  clearances, wall equipment above, facing gap, headroom, route path, route length);
- the decision rule (any PASS -> pass; else any UNSURE -> manual_review; reject only when every
  spot fails and no unexplored wall end is within reach).

What changes for 3D:
- walls are a graph (corners are joints with a kind: inside or outside), so a closed footprint,
  a side wing, or a wall not on the meter's chain are all candidates;
- the cable route is the shortest path along wall faces from the meter over the graph, wrapping
  inside and outside corners, with height-aware crossings (over / under windows, doors);
- clearances use the true distance between the battery's box and each object's box (plan or 3D
  per rule profile), so upper-floor windows and objects on perpendicular walls are measured, not
  projected onto a line;
- headroom, facing and footprint collisions are measured against scanned overhead and standing
  geometry in the actual zone of each candidate;
- ground height, slope and step under the footprint come from a height grid, and ground coverage
  is a measured polygon rather than an asserted depth.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely import Geometry, LineString, Point, Polygon, box, get_coordinates, make_valid, set_precision, unary_union
from shapely.geometry import shape

PASS, FAIL, UNSURE = "pass", "fail", "unsure"
SEV = {PASS: 0, UNSURE: 1, FAIL: 2}
EPS = 1e-9
WALL_FOOT_FT = 0.25
# smallest plan width an unseen object of each type could have (ft): gas meter + riser, condenser,
# pool, and a driveway lane. Unseen shadows narrower than this cannot hide one.
HIDE_FT = {"gas_meter": 1.0, "ac": 2.0, "pool": 6.0, "drive": 8.0}


def at_least(value: float, err: float, thr: float) -> str:
    if value - err - thr > EPS:
        return PASS
    if thr - (value + err) > EPS:
        return FAIL
    return UNSURE


def banded(value: float, err: float, fail_below: float, pass_above: float) -> str:
    """PASS when clearly above pass_above, FAIL when clearly below fail_below, else UNSURE."""
    if value - err - pass_above > EPS:
        return PASS
    if fail_below - (value + err) > EPS:
        return FAIL
    return UNSURE


def at_most(value: float, err: float, confident: float, maximum: float) -> str:
    if (value - err) - maximum > EPS:
        return FAIL
    if confident - (value + err) > EPS:
        return PASS
    return UNSURE


def worst(outs: list[str]) -> str:
    return max(outs, key=SEV.__getitem__, default=PASS)


# --------------------------------------------------------------------------------------------
# Scene


@dataclass
class Wall:
    id: str
    a: np.ndarray
    b: np.ndarray
    outward: np.ndarray
    top: float  # scene y of the wall's top
    pm: float
    real: bool = True
    base: list = field(default_factory=list)  # per 1 ft of u: plinth / base proud of the face

    def base_at(self, u0: float, u1: float) -> float:
        if not self.base:
            return 0.0
        i0, i1 = max(0, int(u0)), min(len(self.base) - 1, int(math.ceil(u1)) - 1)
        return max(self.base[i0 : i1 + 1], default=0.0)

    def __post_init__(self):
        d = self.b - self.a
        self.L = float(np.linalg.norm(d))
        self.along = d / self.L
        # make outward exactly perpendicular, keeping its side
        n = np.array([-self.along[1], self.along[0]])
        self.outward = n if n @ self.outward > 0 else -n

    def pt(self, u: float, out: float = 0.0) -> tuple[float, float]:
        p = self.a + self.along * u + self.outward * out
        return (float(p[0]), float(p[1]))

    def local(self, p) -> tuple[float, float]:
        d = np.asarray(p, float) - self.a
        return float(d @ self.along), float(d @ self.outward)

    def rect(self, u0: float, u1: float, o0: float, o1: float) -> Polygon:
        return Polygon([self.pt(u0, o0), self.pt(u1, o0), self.pt(u1, o1), self.pt(u0, o1)])

    def line(self, u0: float = 0.0, u1: float | None = None) -> LineString:
        u1 = self.L if u1 is None else u1
        return LineString([self.pt(u0), self.pt(u1)])


@dataclass
class Obj:
    id: str
    type: str
    geom: Geometry  # plan geometry (polygon, line or point)
    y0: float  # scene y of bottom
    y1: float  # scene y of top
    pm: float
    wall_id: str | None = None
    span: tuple[float, float] | None = None  # u-span on wall_id, for wall-mounted objects
    attrs: dict = field(default_factory=dict)
    real: bool = True
    note: str = ""
    evidence: str = "observed"


@dataclass
class Joint:
    w1: str
    e1: str
    w2: str
    e2: str
    kind: str  # inside | outside | straight
    angle_deg: float
    gap_ft: float


@dataclass
class HeightGrid:
    x0: float
    z0: float
    res: float
    h: np.ndarray  # [iz, ix], NaN where no ground was scanned

    def at(self, x: float, z: float) -> float:
        ix = int(round((x - self.x0) / self.res))
        iz = int(round((z - self.z0) / self.res))
        if 0 <= iz < self.h.shape[0] and 0 <= ix < self.h.shape[1]:
            return float(self.h[iz, ix])
        return math.nan

    def samples(self, poly: Polygon, step: float | None = None) -> np.ndarray:
        step = step or self.res
        minx, minz, maxx, maxz = poly.bounds
        xs = np.arange(minx + step / 2, maxx, step)
        zs = np.arange(minz + step / 2, maxz, step)
        out = []
        for x in xs:
            for z in zs:
                if poly.contains(Point(x, z)):
                    out.append((x, z, self.at(x, z)))
        return np.array(out) if out else np.zeros((0, 3))


@dataclass
class Scene3D:
    id: str
    walls: dict[str, Wall]
    joints: list[Joint]
    free_ends: dict[tuple[str, str], str]  # (wall, 'a'|'b') -> unexplored | limit
    meter_wall: str
    meter_u: float
    meter_pos: np.ndarray
    meter_pm: float
    objects: list[Obj]
    obstacles: list[Obj]
    overheads: list[Obj]
    ground_patches: list[Obj]  # type in obj.type, geom polygon
    ground: HeightGrid
    interior: Geometry
    observed_wall: dict[str, list[tuple[float, float]]]
    observed_ground: Geometry
    observed_overhead: Geometry
    meta: dict = field(default_factory=dict)


def _poly(coords) -> Geometry:
    if len(coords) == 1:
        return Point(coords[0])
    if len(coords) == 2:
        return LineString(coords)
    p = Polygon(coords)
    return p if p.is_valid else make_valid(p)


def _multi(v) -> Geometry:
    """Area geometry snapped to a 0.01 ft grid: perturbed coverage polygons with nearly coincident
    edges otherwise make GEOS overlays run for minutes."""
    if v is None:
        return Polygon()
    g = shape(v) if isinstance(v, dict) else (unary_union([Polygon(p) for p in v]) if v else Polygon())
    return set_precision(make_valid(g), 0.01)


def load_scene(d: dict[str, Any]) -> Scene3D:
    walls = {
        w["id"]: Wall(
            w["id"],
            np.array(w["a"], float),
            np.array(w["b"], float),
            np.array(w["outward"], float),
            float(w["top_y"]),
            float(w["plus_minus_ft"]),
            w.get("real", True),
            list(w.get("base_profile_ft", [])),
        )
        for w in d["walls"]
    }
    joints = [
        Joint(j["walls"][0], j["ends"][0], j["walls"][1], j["ends"][1], j["kind"],
              j.get("angle_deg", 90.0), j.get("gap_ft", 0.0))
        for j in d.get("joints", [])
    ]
    free = {(f["wall"], f["end"]): f["kind"] for f in d.get("free_ends", [])}

    def objs(key):
        out = []
        for o in d.get(key, []):
            out.append(
                Obj(
                    o.get("id", f"{key}{len(out)}"),
                    o.get("type", key),
                    _poly(o["footprint"]),
                    float(o.get("y0", 0.0)),
                    float(o.get("y1", 0.0)),
                    float(o.get("plus_minus_ft", 0.1)),
                    o.get("wall_id"),
                    tuple(o["span_u"]) if "span_u" in o else None,
                    o.get("attrs", {}),
                    o.get("real", True),
                    o.get("note", ""),
                    o.get("evidence") or ("observed" if o.get("real", True) else "inferred"),
                )
            )
        return out

    g = d["ground"]["grid"]
    grid = HeightGrid(g["x0"], g["z0"], g["res"],
                      np.array([[math.nan if v is None else v for v in row] for row in g["h"]], float))
    m = d["meter"]
    mw = walls[m["wall_id"]]
    mu, _ = mw.local(np.array(m["pos"])[[0, 2]])
    # a meter measured past its wall's end would give the route graph a negative edge
    mu = min(max(mu, 0.0), mw.L)
    cov = d["coverage"]
    return Scene3D(
        d["id"], walls, joints, free, m["wall_id"], mu, np.array(m["pos"], float),
        float(m.get("plus_minus_ft", 0.3)),
        objs("objects"), objs("obstacles"), objs("overheads"), objs("ground_patches"),
        grid, _multi(d.get("interior")),
        {k: [tuple(x) for x in v] for k, v in cov["wall"].items()},
        _multi(cov["ground"]), _multi(cov.get("overhead")), d.get("meta", {}),
    )


# --------------------------------------------------------------------------------------------
# Rules profile


@dataclass
class Profile:
    raw: dict

    def __getattr__(self, k):
        try:
            return self.raw[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# --------------------------------------------------------------------------------------------
# Route graph


@dataclass
class Route:
    length: float
    err: float
    stretches: list[tuple[str, float, float]]  # (wall, u_from, u_to)
    corners: list[Joint]
    ok: bool  # a continuous wall path exists


class RouteGraph:
    def __init__(self, sc: Scene3D, corner_allowance: float):
        self.sc = sc
        self.adj: dict[Any, list[tuple[Any, float, Any]]] = {}
        for w in sc.walls.values():
            self._edge((w.id, "a"), (w.id, "b"), w.L, ("wall", w.id))
        for j in sc.joints:
            self._edge((j.w1, j.e1), (j.w2, j.e2), corner_allowance + j.gap_ft, ("joint", j))
        mw = sc.walls[sc.meter_wall]
        self._edge("M", (mw.id, "a"), sc.meter_u, ("mstretch", mw.id, sc.meter_u, 0.0))
        self._edge("M", (mw.id, "b"), mw.L - sc.meter_u, ("mstretch", mw.id, sc.meter_u, mw.L))
        self.dist, self.prev = self._dijkstra("M")

    def _edge(self, p, q, w, tag):
        self.adj.setdefault(p, []).append((q, w, tag))
        self.adj.setdefault(q, []).append((p, w, tag))

    def _dijkstra(self, src):
        dist = {src: 0.0}
        prev: dict[Any, tuple[Any, Any]] = {}
        pq = [(0.0, 0, src)]
        k = 0
        while pq:
            d, _, n = heapq.heappop(pq)
            if d > dist.get(n, math.inf) + EPS:
                continue
            for m, w, tag in self.adj.get(n, []):
                nd = d + w
                if nd < dist.get(m, math.inf) - EPS:
                    dist[m] = nd
                    prev[m] = (n, tag)
                    k += 1
                    heapq.heappush(pq, (nd, k, m))
        return dist, prev

    def _path_to(self, node) -> tuple[list[tuple[str, float, float]], list[Joint]]:
        stretches, corners = [], []
        while node != "M":
            p, tag = self.prev[node]
            if tag[0] == "wall":
                w = self.sc.walls[tag[1]]
                u_from = 0.0 if p == (w.id, "a") else w.L
                stretches.append((w.id, u_from, w.L - u_from))
            elif tag[0] == "joint":
                corners.append(tag[1])
            else:
                stretches.append((tag[1], tag[2], tag[3]))
            node = p
        return stretches[::-1], corners[::-1]

    def route_to(self, wid: str, u0: float, u1: float) -> Route:
        sc = self.sc
        w = sc.walls[wid]
        opts = []
        if wid == sc.meter_wall:
            mu = sc.meter_u
            near = u0 if u0 > mu else (u1 if u1 < mu else mu)
            opts.append((abs(near - mu), [(wid, mu, near)], []))
        for end, tail in (("a", u0), ("b", w.L - u1)):
            node = (wid, end)
            if node in self.dist:
                st, co = self._path_to(node)
                u_end = 0.0 if end == "a" else w.L
                u_near = u0 if end == "a" else u1
                opts.append((self.dist[node] + tail, st + [(wid, u_end, u_near)], co))
        if not opts:
            return Route(math.inf, 0.0, [], [], False)
        L, st, co = min(opts, key=lambda o: o[0])
        err = sc.meter_pm + max((sc.walls[s[0]].pm for s in st), default=0.0)
        return Route(L, err, [s for s in st if abs(s[2] - s[1]) > EPS], co, True)


# --------------------------------------------------------------------------------------------
# Solver


@dataclass
class Check:
    id: str
    outcome: str
    measured: float | None = None
    pm: float | None = None
    threshold: float | None = None
    subject: str | None = None
    cause: str | None = None
    reason: str = ""
    # Geometric outcome before a coverage downgrade. Restored when query_region says observed.
    geom_outcome: str | None = None
    # True when the subject is inferred, or any inferred object of this check lies within
    # threshold + its ± + the check's reach. A pass on inferred geometry stays blocked.
    inferred: bool = False

    def js(self):
        skip = {"geom_outcome"}
        if not self.inferred:
            skip.add("inferred")
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()
                if k not in skip and v is not None and v != ""}


def mark_unobserved(c: Check, cause: str = "unobserved") -> None:
    """Hold the geometric outcome until an external coverage query says the region is observed."""
    if c.geom_outcome is None:
        c.geom_outcome = c.outcome
    c.outcome = UNSURE
    c.cause = cause


@dataclass
class Candidate:
    wall: str
    u0: float
    u1: float
    fp: Polygon
    y_g: float
    checks: list[Check]
    route: Route

    @property
    def outcome(self) -> str:
        return worst([c.outcome for c in self.checks])

    def failing(self):
        return sorted(c.id for c in self.checks if c.outcome == FAIL)

    def unsure(self):
        return sorted(c.id for c in self.checks if c.outcome == UNSURE)


def _dist(fp: Polygon, fy0: float, fy1: float, o: Obj, mode: str) -> float:
    d_plan = fp.distance(o.geom)
    if mode == "plan":
        return d_plan
    d_v = max(0.0, o.y0 - fy1, fy0 - o.y1)
    return math.hypot(d_plan, d_v)


def _intervals_cover(ivs: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    """Missing parts of [a, b] not covered by intervals."""
    miss, cur = [], a
    for x, y in sorted(ivs):
        if y <= cur + EPS:
            continue
        if x > b - EPS:
            break
        if x > cur + EPS:
            miss.append((cur, min(x, b)))
        cur = max(cur, y)
        if cur >= b - EPS:
            break
    if cur < b - EPS:
        miss.append((cur, b))
    return miss


class Solver3D:
    def __init__(self, sc: Scene3D, prof: dict):
        self.sc = sc
        self.p = prof
        b = prof["battery"]
        self.W, self.D, self.H = b["width_ft"], b["depth_ft"], b["height_ft"]
        self.graph = RouteGraph(sc, prof["route"]["corner_allowance_ft"])
        # unobserved wall lines and ground
        segs = []
        for w in sc.walls.values():
            for a, bb in _intervals_cover(sc.observed_wall.get(w.id, []), 0.0, w.L):
                segs.append(w.line(a, bb))
        self.unseen_wall_lines = unary_union(segs) if segs else Polygon()
        reach = max(prof["clearances"][k] for k in ("gas_ft", "ac_ft", "drive_ft", "pool_ft")) + 4.0
        walls_u = unary_union([w.line() for w in sc.walls.values()])
        self.outdoor = walls_u.buffer(reach + self.D + 2.0).difference(sc.interior)
        # Shapely 2.1 snaps coverage to 0.01 ft, then a 1e-6 buffer collapses to area 0 and
        # the subtraction removes nothing. 0.02 ft is one snap cell and keeps the epsilon intent.
        self.unseen_ground = self.outdoor.difference(sc.observed_ground.buffer(0.02))
        # unexplored free ends: everything within reach of them counts as unseen, except what
        # was actually observed and the building behind the walls
        discs, conts = [], []
        for (wid, e), k in sc.free_ends.items():
            if k != "unexplored":
                continue
            w = sc.walls[wid]
            u_end, sgn = (0.0, -1.0) if e == "a" else (w.L, 1.0)
            discs.append(Point(w.pt(u_end)).buffer(reach + 4.0))
            # the wall may carry on past an unexplored end: its continuation is unseen wall
            conts.append(LineString([w.pt(u_end), w.pt(u_end + sgn * (reach + 4.0))]))
        if discs:
            extra = unary_union(discs).difference(sc.observed_ground.buffer(0.02)).difference(sc.interior)
            self.unseen_ground = unary_union([self.unseen_ground, extra])
            self.unseen_wall_lines = unary_union([self.unseen_wall_lines, *conts])
        feet = [w.rect(a, b, -0.1, max(w.base, default=0.0) + WALL_FOOT_FT)
                for w in sc.walls.values() for a, b in sc.observed_wall.get(w.id, [])]
        if feet:
            self.unseen_ground = self.unseen_ground.difference(unary_union(feet))
        self._unseen_by_size: dict[float, Geometry] = {}
        self.walls_geom = [(w.id, w.line()) for w in sc.walls.values()]

    # -- helpers -------------------------------------------------------------------------------

    def unseen_for(self, min_width: float) -> Geometry:
        """Unseen ground that could hide an object at least min_width across (opening removes
        narrower shadows); the footprint's own ground check stays strict."""
        if min_width not in self._unseen_by_size:
            r = min_width / 2
            self._unseen_by_size[min_width] = self.unseen_ground.buffer(-r).buffer(r)
        return self._unseen_by_size[min_width]

    def _ground_under(self, fp: Polygon):
        s = self.sc.ground.samples(fp, 0.25)
        if len(s) == 0:
            return None
        ok = ~np.isnan(s[:, 2])
        if ok.mean() < 0.75:
            return None
        s = s[ok]
        y = s[:, 2]
        A = np.c_[s[:, 0], s[:, 1], np.ones(len(s))]
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        slope = float(math.hypot(coef[0], coef[1]))
        return float(np.max(y)), float(np.max(y) - np.min(y)), slope

    def _covered(self, fp: Polygon, unseen: Geometry, radius: float) -> bool:
        if unseen.is_empty:
            return True
        if radius <= 0:
            return fp.intersection(unseen).area < 0.02
        return fp.distance(unseen) >= radius - 1e-6

    def _ground_fp(self, w: Wall, u0: float, u1: float, bo: float) -> Polygon:
        """Ground to inspect under a footprint. The half grid cell at the wall foot has no laser
        ground points (they sit on the face); it counts as seen where the wall's low band is."""
        if not _intervals_cover(self.sc.observed_wall.get(w.id, []), max(0.0, u0), min(w.L, u1)):
            return w.rect(u0, u1, bo + WALL_FOOT_FT, bo + self.D)
        return w.rect(u0, u1, bo, bo + self.D)

    # -- checks --------------------------------------------------------------------------------

    def check_backing(self, w: Wall, u0: float, u1: float, y_g: float | None) -> Check:
        need = max(self.W, self.p.get("backing_min_ft", 0.0))
        pad = (need - self.W) / 2
        a, b = u0 - pad, u1 + pad
        margin = min(a, w.L - b)
        out = at_least(margin, w.pm, 0.0)
        c = Check("wall_backing", out, margin, w.pm, 0.0)
        if out == FAIL:
            c.reason = "footprint (or the usable wall it needs) runs past a corner or wall end"
            return c
        if y_g is not None and w.top < y_g + self.H + 0.1:
            c.outcome, c.reason = FAIL, f"wall top {w.top - y_g:.2f} ft above ground is below the battery"
            return c
        miss = _intervals_cover(self.sc.observed_wall.get(w.id, []), max(0, a), min(w.L, b))
        if miss and out == PASS:
            mark_unobserved(c)
        if out == UNSURE:
            c.cause = "margin"
        return c

    def check_ground(self, w: Wall, fp: Polygon, gu) -> Check:
        g = self.p["ground"]
        c = Check("ground_surface", PASS)
        covered = self._covered(fp, self.unseen_ground, 0.0)
        if gu is None:
            c.outcome, c.cause = UNSURE, "unobserved"
            c.geom_outcome = UNSURE
            c.reason = "no ground height under the footprint"
            return c
        _, step, slope = gu
        c.measured = slope
        ok_geom = worst([
            at_least(g["max_slope"] - slope, g.get("slope_err", 0.02), 0.0),
            at_least(g["max_step_ft"] - step, g.get("step_err_ft", 0.05), 0.0),
        ])
        if ok_geom != PASS:
            c.outcome, c.cause = ok_geom, "margin" if ok_geom == UNSURE else None
            c.reason = f"slope {slope:.3f}, step {step:.2f} ft"
            return c
        types = [(o, fp.intersection(o.geom.buffer(-o.pm)).area) for o in self.sc.ground_patches]
        bad = [o for o, a in types if o.type not in g["allowed"] and a > 1e-4]
        if bad:
            c.outcome, c.subject = FAIL, bad[0].type
        else:
            good = unary_union([o.geom.buffer(o.pm) for o in self.sc.ground_patches if o.type in g["allowed"]])
            if good.is_empty or not good.covers(fp):
                c.outcome, c.cause = UNSURE, "unknown_attribute"
                c.reason = "surface type under the footprint not recorded or near a boundary"
        if c.outcome in (PASS, FAIL) and not covered:
            mark_unobserved(c)
        return c

    def check_meter_space(self, w: Wall, fp: Polygon) -> Check:
        ws = self.p["meter_working_space"]
        mw = self.sc.walls[self.sc.meter_wall]
        zone = mw.rect(self.sc.meter_u - ws["width_ft"] / 2, self.sc.meter_u + ws["width_ft"] / 2,
                       0.0, ws["depth_ft"])
        d = fp.distance(zone)
        if d <= EPS and fp.intersection(zone).area > 1e-6:
            d = -math.sqrt(fp.intersection(zone).area)
        e = self.sc.meter_pm + w.pm
        return Check("meter_working_space", at_least(d, e, 0.0), d, e, 0.0, "meter")

    def check_clearance(self, cid: str, w: Wall, fp, y_g, items: list[Obj], thr: float,
                        bands: list[str], mode: str, counts=None, hide_ft: float = 1.0) -> Check:
        c = Check(cid, PASS, threshold=thr)
        wk = None
        # radius the check already searches for unobserved space
        check_reach = thr + w.pm
        for o in items:
            cnt = True if counts is None else counts(o)
            if cnt is False:
                continue
            d = self._obj_dist(w, fp, y_g, o, mode)
            e = o.pm + w.pm
            out = at_least(d, e, thr)
            cause = "margin"
            if cnt is None and out != PASS:
                out, cause = UNSURE, "unknown_attribute"
            # A nearer observed object can win the subject and still leave this inferred one
            # inside the clearance the check is claiming.
            if o.evidence == "inferred" and d <= thr + o.pm + check_reach:
                c.inferred = True
            key = (SEV[out], -(d - e))
            if wk is None or key > wk:
                wk = key
                c.outcome, c.measured, c.pm, c.subject = out, d, e, o.id
                c.cause = cause if out == UNSURE else None
                if o.evidence == "inferred":
                    c.inferred = True
        if c.outcome == FAIL or c.outcome == UNSURE:
            return c
        radius = thr + w.pm
        unseen = []
        if "ground" in bands and not self._covered(fp, self.unseen_for(hide_ft), radius):
            unseen.append("ground")
        if "wall" in bands and not self._covered(fp, self.unseen_wall_lines, radius):
            unseen.append("wall")
        if unseen:
            mark_unobserved(c)
            c.reason = "unobserved " + "+".join(unseen) + f" within {radius:.2f} ft"
        return c

    def _obj_dist(self, w: Wall, fp: Polygon, y_g: float, o: Obj, mode: str) -> float:
        """Plan (or prism) distance; for an object on the candidate's own wall the along-wall gap
        is signed, so a spot directly below a window measures negative, not zero."""
        if o.wall_id != w.id or o.span is None:
            return _dist(fp, y_g, y_g + self.H, o, mode)
        u0, u1 = w.local(fp.exterior.coords[0])[0], w.local(fp.exterior.coords[1])[0]
        gap = max(o.span[0] - max(u0, u1), min(u0, u1) - o.span[1])
        if mode == "plan":
            return gap
        d_v = max(0.0, o.y0 - (y_g + self.H), y_g - o.y1)
        if gap > 0:
            return math.hypot(gap, d_v)
        return d_v if d_v > 0 else gap

    def _opening_counts(self, o: Obj):
        op = self.p["openings"]
        if o.type not in op["types"]:
            return False
        if o.type != "window":
            return True
        if op.get("ground_level_max_bottom_ft") is not None:
            yg = self.sc.ground.at(*o.geom.centroid.coords[0])
            yg = 0.0 if math.isnan(yg) else yg
            if o.y0 - yg > op["ground_level_max_bottom_ft"] + o.pm:
                return False
            if o.y0 - yg > op["ground_level_max_bottom_ft"] - o.pm:
                return None
        if op.get("exempt_fixed_windows") and o.attrs.get("operable") is False:
            return False
        if op.get("exempt_fixed_windows") and o.attrs.get("operable") is None:
            return None
        return True

    def check_openings(self, w: Wall, u0, u1, fp, y_g) -> list[Check]:
        op = self.p["openings"]
        items = [o for o in self.sc.objects if o.type in op["types"]]
        c = self.check_clearance("opening_clearance", w, fp, y_g, items, op["min_ft"], ["wall"],
                                 op.get("measure", "plan"), self._opening_counts)
        out = [c]
        if op.get("under_window_any_height"):
            # A: "clear of windows" - nothing directly under a window on the same wall, any height
            u = Check("under_window", PASS, threshold=0.0)
            for o in self.sc.objects:
                if o.type != "window" or o.wall_id != w.id or o.span is None:
                    continue
                gap = max(o.span[0] - u1, u0 - o.span[1])
                r = at_least(gap, o.pm + w.pm, 0.0)
                if SEV[r] > SEV[u.outcome]:
                    u.outcome, u.measured, u.pm, u.subject = r, gap, o.pm + w.pm, o.id
            out.append(u)
        return out

    def check_wall_equipment(self, w: Wall, u0, u1) -> Check:
        c = Check("wall_equipment_above", PASS, threshold=self.p["clearances"]["wall_equipment_ft"])
        for o in self.sc.objects:
            if o.type not in self.p["wall_equipment_types"] or o.wall_id != w.id or o.span is None:
                continue
            gap = max(o.span[0] - u1, u0 - o.span[1])
            r = at_least(gap, o.pm + w.pm, c.threshold)
            if SEV[r] > SEV[c.outcome]:
                c.outcome, c.measured, c.pm, c.subject = r, gap, o.pm + w.pm, o.id
        if c.outcome == PASS and _intervals_cover(self.sc.observed_wall.get(w.id, []), max(0, u0), min(w.L, u1)):
            mark_unobserved(c)
        return c

    def _standing(self):
        """Everything that stands on the ground in front of a wall: scanned obstacles, standing
        objects, and the other walls (a facing wall, or the far side of an inside corner)."""
        out = list(self.sc.obstacles)
        out += [o for o in self.sc.objects if o.type in ("ac", "gas_meter", "pool") and o.wall_id is None]
        return out

    def check_footprint_clear(self, w: Wall, fp: Polygon, y_g) -> Check:
        """3D: nothing scanned stands inside the battery's box (bins, pilasters, pipes)."""
        c = Check("footprint_clear", PASS, threshold=0.0)
        core = fp  # battery box footprint
        for o in self._standing():
            if o.y1 < y_g + 0.15 or o.y0 > y_g + self.H:
                continue
            d = core.distance(o.geom)
            inter = core.intersection(o.geom).area if isinstance(o.geom, Polygon) else 0.0
            val = -math.sqrt(inter) if inter > 1e-6 else d
            e = o.pm + w.pm
            r = at_least(val, e, 0.0)
            if SEV[r] > SEV[c.outcome] or (r == c.outcome and c.measured is not None and val < c.measured):
                c.outcome, c.measured, c.pm, c.subject = r, val, e, o.id
        for wid, ln in self.walls_geom:
            if wid == w.id:
                continue
            d = core.distance(ln)
            if core.intersection(ln).length > 1e-3:
                c.outcome, c.measured, c.subject = FAIL, -1.0, wid
        if c.outcome != FAIL and not self._covered(fp, self.unseen_ground, 0.0):
            mark_unobserved(c)
        return c

    def check_facing(self, w: Wall, u0, u1, y_g) -> Check:
        f = self.p["facing"]
        if f.get("min_ft") is None:
            return Check("facing_gap", PASS, reason="not required by this profile")
        need_from_wall = w.base_at(u0, u1) + f["min_ft"] + (self.D if f["measured_from"] == "battery_front" else 0.0)
        look = need_from_wall + 3.0
        strip = w.rect(u0, u1, 0.0, look)
        best, subj, e = math.inf, None, 0.0
        for o in self._standing():
            if o.y1 < y_g + 0.5 or o.y0 > y_g + 6.0:
                continue
            inter = strip.intersection(o.geom)
            if inter.is_empty:
                continue
            outs = [w.local(p)[1] for p in get_coordinates(inter)]
            if outs and min(outs) < best:
                best, subj, e = min(outs), o.id, o.pm + w.pm
        for wid, ln in self.walls_geom:
            if wid == w.id:
                continue
            inter = strip.intersection(ln)
            if inter.is_empty or inter.length < 1e-3:
                continue
            outs = [w.local(p)[1] for p in get_coordinates(inter)]
            if min(outs) < best:
                best, subj, e = min(outs), wid, self.sc.walls[wid].pm + w.pm
        c = Check("facing_gap", PASS, threshold=need_from_wall, subject=subj)
        if best < math.inf:
            c.measured, c.pm = best, e
            c.outcome = at_least(best, e, need_from_wall)
            if c.outcome != PASS:
                c.cause = "margin" if c.outcome == UNSURE else None
                return c
        zone = w.rect(u0, u1, 0.0, need_from_wall + w.pm)
        if not self._covered(zone, self.unseen_ground, 0.0):
            mark_unobserved(c)
        return c

    def check_headroom(self, w: Wall, u0, u1, y_g) -> Check:
        hr = self.p["headroom"]
        if hr.get("min_ft") is None:
            return Check("headroom", PASS, reason="not required by this profile")
        zone = w.rect(u0, u1, 0.0, hr.get("zone_out_ft", 3.0))
        low, subj, e = math.inf, None, 0.0
        for o in self.sc.overheads:
            if zone.intersects(o.geom) and zone.intersection(o.geom).area > 1e-4:
                if o.y0 - y_g < low:
                    low, subj, e = o.y0 - y_g, o.id, o.pm
        c = Check("headroom", PASS, threshold=hr["min_ft"], subject=subj)
        rev = hr.get("pass_above_ft", hr["min_ft"])
        if low < math.inf:
            c.measured, c.pm = low, e
            c.outcome = banded(low, e, hr["min_ft"], rev)
            if c.outcome != PASS:
                c.cause = "margin" if c.outcome == UNSURE else None
                return c
        if not zone.difference(self.sc.observed_overhead.buffer(0.02)).area < 1e-3:
            mark_unobserved(c)
        return c

    def route_checks(self, w: Wall, u0, u1, y_g) -> tuple[Route, list[Check]]:
        rp = self.p["route"]
        r = self.graph.route_to(w.id, u0, u1)
        path = Check("route_path", PASS)
        reach = Check("route_length", PASS, threshold=rp["max_ft"])
        if not r.ok:
            path.outcome = reach.outcome = FAIL
            path.reason = "no continuous wall from the meter to this wall"
            return r, [path, reach]
        effects, detour, extra_err = [], 0.0, 0.0
        h = rp["height_ft"]
        for wid, a, b in r.stretches:
            lo, hi = min(a, b), max(a, b)
            for o in self.sc.objects:
                if o.wall_id != wid or o.span is None:
                    continue
                if min(hi, o.span[1]) - max(lo, o.span[0]) <= EPS:
                    continue
                eff = rp["crossing"].get(o.type, "allow")
                if eff == "allow":
                    continue
                yg = y_g
                rh = h + (self.sc.ground.at(*o.geom.centroid.coords[0]) if not math.isnan(
                    self.sc.ground.at(*o.geom.centroid.coords[0])) else yg)
                straddles = o.y0 - o.pm <= rh <= o.y1 + o.pm
                if eff == "detour":
                    if not straddles:
                        continue
                    opts = [2 * (o.y1 - rh)] + ([2 * (rh - o.y0)] if o.y0 - (rh - h) > 0.05 else [])
                    detour += max(0.0, min(opts))
                    extra_err += 2 * o.pm
                    effects.append(("detour", o.id))
                elif eff == "door_over_review":
                    top_room = self.sc.walls[wid].top - o.y1
                    effects.append(("review" if top_room > 0.5 else "fail", o.id))
                else:
                    effects.append((eff, o.id))
        # stretches of wall nobody saw
        unseen = any(
            _intervals_cover(self.sc.observed_wall.get(wid, []), min(a, b), max(a, b))
            for wid, a, b in r.stretches
        )
        n_corners = len(r.corners)
        if n_corners > rp.get("max_corners", 99):
            effects.append(("fail", f"{n_corners} corner(s); rule allows {rp.get('max_corners')}"))
        kinds = [e for e, _ in effects]
        if "fail" in kinds:
            path.outcome, path.subject = FAIL, next(s for e, s in effects if e == "fail")
        elif "review" in kinds:
            path.outcome, path.cause = UNSURE, "rule_requires_review"
            path.subject = next(s for e, s in effects if e == "review")
        elif unseen:
            mark_unobserved(path)
        L = r.length + detour
        e = r.err + extra_err
        reach.measured, reach.pm = L, e
        reach.outcome = at_most(L, e, rp["confident_reach_ft"], rp["max_ft"])
        if reach.outcome == UNSURE:
            reach.cause = "margin" if any(abs(L - x) <= e for x in (rp["confident_reach_ft"], rp["max_ft"])) else "rule_requires_review"
        path.measured = float(n_corners)
        r.length = L
        r.err = e
        return r, [path, reach]

    # -- candidates ----------------------------------------------------------------------------

    def evaluate(self, w: Wall, u0: float) -> Candidate:
        u1 = u0 + self.W
        bo = w.base_at(u0, u1)
        fp = w.rect(u0, u1, bo, bo + self.D)
        gfp = self._ground_fp(w, u0, u1, bo)
        gu = self._ground_under(gfp)
        y_g = gu[0] if gu else 0.0
        cl = self.p["clearances"]
        mode = self.p.get("clearance_measure", "plan")
        gas = [o for o in self.sc.objects if o.type == "gas_meter"]
        ac = [o for o in self.sc.objects if o.type == "ac"]
        pool = [o for o in self.sc.objects if o.type == "pool"]
        drive = [o for o in self.sc.ground_patches if o.type in self.p["ground"]["drivable"]]
        checks = [
            self.check_backing(w, u0, u1, y_g if gu else None),
            self.check_ground(w, gfp, gu),
            self.check_meter_space(w, fp),
            self.check_clearance("gas_clearance", w, fp, y_g, gas, cl["gas_ft"], ["ground", "wall"], mode,
                               hide_ft=HIDE_FT["gas_meter"]),
            self.check_clearance("ac_clearance", w, fp, y_g, ac, cl["ac_ft"], ["ground"], mode, hide_ft=HIDE_FT["ac"]),
            self.check_clearance("drive_clearance", w, fp, y_g, drive, cl["drive_ft"], ["ground"], "plan",
                               hide_ft=HIDE_FT["drive"]),
            self.check_clearance("pool_clearance", w, fp, y_g, pool, cl["pool_ft"], ["ground"], "plan",
                               hide_ft=HIDE_FT["pool"]),
            *self.check_openings(w, u0, u1, fp, y_g),
            self.check_wall_equipment(w, u0, u1),
            self.check_facing(w, u0, u1, y_g),
            self.check_headroom(w, u0, u1, y_g),
            self.check_footprint_clear(w, fp, y_g),
        ]
        route, rc = self.route_checks(w, u0, u1, y_g)
        checks = checks + rc
        if self.p.get("fail_needs_observed"):
            self._contract_fails(w, u0, u1, checks, route)
        return Candidate(w.id, u0, u1, fp, y_g, checks, route)

    # -- contract (docs/scene-and-verdict-format.md 3.1): a FAIL must rest on observed evidence --

    def _wall_seen(self, wid: str, a: float, b: float) -> bool:
        return not _intervals_cover(self.sc.observed_wall.get(wid, []), max(0.0, a + 0.25),
                                    min(self.sc.walls[wid].L, b - 0.25))

    def _obj_seen(self, oid: str | None) -> bool:
        if oid is None:
            return True
        if oid in self.sc.walls:
            return self._wall_seen(oid, 0.0, self.sc.walls[oid].L)
        for o in (*self.sc.objects, *self.sc.obstacles, *self.sc.overheads, *self.sc.ground_patches):
            if o.id != oid:
                continue
            if o.wall_id and o.span:
                return self._wall_seen(o.wall_id, *o.span)
            if o.type in self.p["ground"]["drivable"] or o in self.sc.ground_patches:
                return True  # a region's edge near the footprint is what matters; see ground check
            g = o.geom if o.geom.area > 0 else o.geom.buffer(0.25)
            return g.intersection(self.sc.observed_ground).area >= 0.9 * g.area
        return True

    def _share(self, geom: Geometry, w: Wall, u0, u1, o0, o1) -> float:
        pts = [w.pt(u, o) for u in np.arange(u0 + 0.25, u1, 0.5) for o in np.arange(o0 + 0.25, o1, 0.5)]
        return float(np.mean([geom.contains(Point(p)) for p in pts])) if pts else 0.0

    def evidence_ok(self, c: Check, w: Wall, u0, u1, route: Route) -> bool:
        """The region a check's outcome rests on is observed (contract 3.1 shares: >= 90 %)."""
        if c.id in ("route_path", "route_length"):
            return all(self._wall_seen(wid, min(a, b), max(a, b)) for wid, a, b in route.stretches)
        if c.id == "meter_working_space":
            mw = self.sc.walls[self.sc.meter_wall]
            return self._wall_seen(mw.id, self.sc.meter_u - 1.25, self.sc.meter_u + 1.25)
        if c.id in ("wall_backing", "opening_clearance", "under_window", "wall_equipment_above"):
            return self._wall_seen(w.id, u0, u1) and self._obj_seen(c.subject if isinstance(c.subject, str) else None)
        if c.id == "headroom":
            return self._share(self.sc.observed_overhead, w, u0, u1, 0.0, 3.0) >= 0.9
        if c.id == "facing_gap":
            return self._share(self.sc.observed_ground, w, u0, u1, 0.0, 5.0) >= 0.9
        reach = max(self.D, (c.threshold or 0.0) + 2.0)
        return (self._share(self.sc.observed_ground, w, u0, u1, 0.0, reach) >= 0.9
                and self._obj_seen(c.subject if isinstance(c.subject, str) else None))

    def _contract_fails(self, w: Wall, u0, u1, checks: list[Check], route: Route):
        for c in checks:
            if c.outcome == UNSURE or self.evidence_ok(c, w, u0, u1, route):
                continue
            mark_unobserved(c)
            c.reason = (c.reason + "; " if c.reason else "") + "outcome rests on space not observed (contract 3.1)"

    def candidates(self, step: float = 1 / 6) -> list[Candidate]:
        out = []
        for w in self.sc.walls.values():
            if w.L < self.W:
                continue
            n = int(math.floor((w.L - self.W) / step))
            us = [i * step for i in range(n + 1)] + [w.L - self.W]
            for u0 in us:
                out.append(self.evaluate(w, u0))
        return out


def decide(sc: Scene3D, prof: dict, step: float = 1 / 6) -> dict[str, Any]:
    s = Solver3D(sc, prof)
    cands = s.candidates(step)
    passes = [c for c in cands if c.outcome == PASS]
    uns = [c for c in cands if c.outcome == UNSURE]
    fails = [c for c in cands if c.outcome == FAIL]
    rp = prof["route"]
    # an unexplored free end within reach blocks a reject
    open_ends = []
    for (wid, e), kind in sc.free_ends.items():
        if kind != "unexplored":
            continue
        d = s.graph.dist.get((wid, e), math.inf)
        if d - sc.meter_pm <= rp["max_ft"]:
            open_ends.append(f"{wid}.{e}")
    if passes:
        best = min(passes, key=lambda c: c.route.length)
        decision = "pass" if prof["policy"]["auto_approve"] else "manual_review"
    elif uns:
        best = min(uns, key=lambda c: (len(c.unsure()), c.route.length))
        decision = "manual_review"
    else:
        best = min(fails, key=lambda c: (len(c.failing()), c.route.length)) if fails else None
        decision = "reject" if prof["policy"]["allow_reject"] and not open_ends else "manual_review"
    runs = []
    for c in sorted(cands, key=lambda c: (c.wall, c.u0)):
        key = (c.wall, c.outcome, tuple(c.failing()), tuple(c.unsure()))
        if runs and runs[-1]["_k"] == key and c.u0 - runs[-1]["u"][1] <= step + 1e-6:
            runs[-1]["u"][1] = round(c.u0, 3)
        else:
            runs.append({"_k": key, "wall": c.wall, "u": [round(c.u0, 3), round(c.u0, 3)],
                         "outcome": c.outcome, "failing": c.failing(), "unsure": c.unsure()})
    for r in runs:
        del r["_k"]
    return {
        "scene": sc.id,
        "profile": prof["id"],
        "decision": decision,
        "open_ends_in_reach": open_ends,
        "best": None if best is None else {
            "wall": best.wall, "u": [round(best.u0, 3), round(best.u1, 3)],
            "outcome": best.outcome, "route_ft": round(best.route.length, 3),
            "route_corners": [f"{j.w1}.{j.e1}-{j.w2}.{j.e2}:{j.kind}" for j in best.route.corners],
            "checks": [c.js() for c in best.checks],
        },
        "stats": {"candidates": len(cands), "pass": len(passes), "unsure": len(uns), "fail": len(fails)},
        "runs": runs,
        "_cands": cands,
    }
