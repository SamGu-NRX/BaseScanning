"""Geometry helpers over a scene v0: the unrolled wall chain, ground height, object extents."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

OPENING_CLASSES = ("window", "door", "garage_door", "basement_well")
OVERHEAD_CLASSES = ("eave", "porch_roof", "stairs", "deck", "overhang")


@dataclass
class ChainWall:
    id: str
    p0: np.ndarray
    p1: np.ndarray
    s0: float
    height: float
    plus_minus: float
    evidence: str
    base_y: float = 0.0

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.p1 - self.p0))

    @property
    def along(self) -> np.ndarray:
        return (self.p1 - self.p0) / self.length

    @property
    def outward(self) -> np.ndarray:
        dx, dz = self.along
        return np.array([-dz, dx])

    @property
    def s1(self) -> float:
        return self.s0 + self.length

    def s_of(self, q) -> np.ndarray:
        return self.s0 + (np.asarray(q, float) - self.p0) @ self.along

    def out_of(self, q) -> np.ndarray:
        return (np.asarray(q, float) - self.p0) @ self.outward

    def plan_at(self, s) -> np.ndarray:
        return self.p0 + np.multiply.outer(np.asarray(s, float) - self.s0, self.along)


class Chain:
    def __init__(self, scene: dict):
        walls = {w["id"]: w for w in scene["walls"]}
        self.walls: list[ChainWall] = []
        meter = meter_object(scene)
        mpos = object_center(meter, None)
        for wid in scene["chain"]["wall_ids"]:
            w = walls[wid]
            p0, p1 = (np.asarray(p, float) for p in w["baseline_m"])
            cw = ChainWall(wid, p0, p1, 0.0, float(w["height_m"]), float(w["plus_minus_m"]),
                           w["evidence"], float(w.get("base_y_m", 0.0)))
            self.walls.append(cw)
        if all("s_range_m" in walls[w.id] for w in self.walls):
            for cw in self.walls:
                cw.s0 = float(walls[cw.id]["s_range_m"][0])
        else:
            self._unroll(mpos[[0, 2]], meter)
        self.by_id = {w.id: w for w in self.walls}

    def _unroll(self, m_plan, meter) -> None:
        mid = meter.get("wall_id") or self.nearest_wall(m_plan).id
        k = [w.id for w in self.walls].index(mid)
        self.walls[k].s0 = -float((m_plan - self.walls[k].p0) @ self.walls[k].along)
        for i in range(k + 1, len(self.walls)):
            prev = self.walls[i - 1]
            self.walls[i].s0 = prev.s1 + float(np.linalg.norm(self.walls[i].p0 - prev.p1))
        for i in range(k - 1, -1, -1):
            nxt = self.walls[i + 1]
            gap = float(np.linalg.norm(nxt.p0 - self.walls[i].p1))
            self.walls[i].s0 = nxt.s0 - gap - self.walls[i].length

    @property
    def s_min(self) -> float:
        return self.walls[0].s0

    @property
    def s_max(self) -> float:
        return self.walls[-1].s1

    def wall_at_s(self, s: float) -> ChainWall | None:
        for w in self.walls:
            if w.s0 - 1e-9 <= s <= w.s1 + 1e-9:
                return w
        return None

    def nearest_wall(self, q) -> ChainWall:
        q = np.asarray(q, float)
        best, bd = None, np.inf
        for w in self.walls:
            t = np.clip((q - w.p0) @ w.along, 0, w.length)
            d = np.linalg.norm(q - (w.p0 + t * w.along))
            if d < bd:
                best, bd = w, d
        return best

    def locate(self, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Plan points -> (index of nearest chain wall, s, signed distance out from it)."""
        Q = np.atleast_2d(np.asarray(Q, float))
        best_d = np.full(len(Q), np.inf)
        idx = np.zeros(len(Q), int)
        s = np.zeros(len(Q))
        out = np.zeros(len(Q))
        for i, w in enumerate(self.walls):
            t = np.clip((Q - w.p0) @ w.along, 0, w.length)
            d = np.linalg.norm(Q - (w.p0 + np.outer(t, w.along)), axis=1)
            better = d < best_d
            best_d[better] = d[better]
            idx[better] = i
            s[better] = w.s0 + t[better]
            out[better] = ((Q - w.p0) @ w.outward)[better]
        return idx, s, out


def meter_object(scene: dict) -> dict:
    mid = scene["frame"]["anchor"]["element_id"]
    return next(o for o in scene["objects"] if o["id"] == mid)


def ground_y(scene: dict, plan) -> np.ndarray:
    """Ground height below plan points [x, z] (plane surfaces; heightfields fall back to 0)."""
    plan = np.atleast_2d(np.asarray(plan, float))
    surf = scene["ground"]["surface"]
    if surf["kind"] != "plane":
        return np.zeros(len(plan))
    n = np.asarray(surf["normal"], float)
    return (surf["offset_m"] - n[0] * plan[:, 0] - n[2] * plan[:, 1]) / n[1]


def object_center(obj: dict, chain: Chain | None) -> np.ndarray:
    g = obj["geometry"]
    k = g["kind"]
    if k == "point":
        return np.asarray(g["pos_m"], float)
    if k == "box":
        return np.asarray(g["center_m"], float)
    if k == "footprint":
        p = np.asarray(g["polygon_m"], float).mean(0)
        return np.array([p[0], (g["bottom_m"] + g["top_m"]) / 2, p[1]])
    w = chain.by_id[g["wall_id"]]
    q = w.plan_at((g["s_m"][0] + g["s_m"][1]) / 2)
    return np.array([q[0], w.base_y + (g["bottom_m"] + g["top_m"]) / 2, q[1]])


def object_plan_points(obj: dict, chain: Chain | None) -> np.ndarray:
    """Plan outline points [x, z] of an object (a segment for wall rectangles)."""
    g = obj["geometry"]
    k = g["kind"]
    if k == "point":
        return np.asarray(g["pos_m"], float)[[0, 2]][None]
    if k == "footprint":
        return np.asarray(g["polygon_m"], float)
    if k == "box":
        c = np.asarray(g["center_m"], float)
        w, _, d = g["size_m"]
        a = np.radians(g.get("yaw_deg", 0.0))
        ex, ez = np.array([np.cos(a), -np.sin(a)]), np.array([np.sin(a), np.cos(a)])
        return np.array([c[[0, 2]] + sx * w / 2 * ex + sz * d / 2 * ez for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    w = chain.by_id[g["wall_id"]]
    pts = w.plan_at(np.asarray(g["s_m"], float))
    so = g.get("standoff_m", 0.0)
    if so:
        pts = np.vstack([pts, pts + so * w.outward])
    return pts


def object_s_span(obj: dict, chain: Chain) -> tuple[float, float]:
    g = obj["geometry"]
    if g["kind"] == "wall_rect":
        return float(g["s_m"][0]), float(g["s_m"][1])
    _, s, _ = chain.locate(object_plan_points(obj, chain))
    return float(s.min()), float(s.max())


def object_bottom_top(obj: dict, scene: dict, chain: Chain) -> tuple[float, float]:
    """Heights above the ground directly below the object."""
    g = obj["geometry"]
    k = g["kind"]
    if k == "wall_rect":
        return float(g["bottom_m"]), float(g["top_m"])
    c = object_center(obj, chain)
    gy = float(ground_y(scene, c[[0, 2]])[0])
    if k == "box":
        h = g["size_m"][1]
        return c[1] - h / 2 - gy, c[1] + h / 2 - gy
    if k == "footprint":
        return g["bottom_m"] - gy, g["top_m"] - gy
    return c[1] - gy, c[1] - gy
