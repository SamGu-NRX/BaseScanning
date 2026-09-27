"""Scene coverage grid (y-up) -> the C2 occupancy grid (z-up)."""

from __future__ import annotations

import math

import numpy as np

from server.criteria.c2.grid import Grid
from server.criteria.harness import load

FT = 0.3048


def from_scene(scene: dict, scene_dir) -> tuple[Grid, list[dict], list[tuple], dict] | None:
    h = load()
    grid = h.sceneio.load_coverage(scene, scene_dir)
    if grid is None:
        return None
    # (x, y, z) y-up -> (x, z, y) z-up
    state = np.transpose(grid.state, (0, 2, 1))
    origin = np.array([grid.origin[0], grid.origin[2], grid.origin[1]], float)
    g = Grid(origin, float(grid.voxel), state)
    g = _crop(g, scene)
    objects = []
    for o in scene["objects"]:
        if o.get("evidence") == "generated" or o["class"] == "meter":
            continue
        poly = _plan(o, scene)
        if poly is None:
            continue
        y0, y1 = _heights(o)
        objects.append(
            {
                "type": o["class"],
                "id": o["id"],
                "plan_polygon": poly,
                "z_range": [y0, y1],
                "plus_minus_m": o.get("plus_minus_m", 0.1),
            }
        )
    ground = []
    for r in scene["ground"].get("regions", []):
        if r.get("evidence") == "generated":
            continue
        ground.append((r["polygon_m"], r["material"]))
    meter = next(
        o for o in scene["objects"] if o["class"] == "meter" or o["id"] == scene["frame"]["anchor"]["element_id"]
    )
    pos = meter["geometry"].get("pos_m") or meter["geometry"].get("center_m")
    # C2 plan is (x, outward). Scene plan is (x, z); y is up.
    meta = {
        "meter": {
            "pos": [pos[0], pos[2]],
            "plus_minus_m": meter.get("plus_minus_m", 0.1),
            "wall_id": meter.get("wall_id"),
        }
    }
    return g, objects, ground, meta


def _wall_frame(wall: dict):
    (x0, z0), (x1, z1) = wall["baseline_m"]
    dx, dz = x1 - x0, z1 - z0
    length = math.hypot(dx, dz) or 1.0
    along = (dx / length, dz / length)
    outward = (-along[1], along[0])
    return (x0, z0), along, outward, length


def chain_offsets_m(scene: dict) -> dict[str, float]:
    """Chain s in metres at each wall start. Zero is the meter, not the wall's own origin."""
    walls = {w["id"]: w for w in scene["walls"]}
    ids = list((scene.get("chain") or {}).get("wall_ids") or walls)
    anchor = (scene.get("frame") or {}).get("anchor", {}).get("element_id")
    meter = next((o for o in scene.get("objects") or [] if o["id"] == anchor or o.get("class") == "meter"), None)
    if meter is None:
        return {}
    mid = meter.get("wall_id") or meter.get("geometry", {}).get("wall_id")
    if mid not in walls:
        return {}
    if mid not in ids:
        ids = [mid, *ids]
    pos = meter["geometry"].get("pos_m") or meter["geometry"].get("center_m")
    origin, along, _, length = _wall_frame(walls[mid])
    s_meter = (float(pos[0]) - origin[0]) * along[0] + (float(pos[2]) - origin[1]) * along[1]
    i = ids.index(mid)
    offsets = {mid: -s_meter}
    s_end = -s_meter + length
    for wid in ids[i + 1 :]:
        if wid not in walls:
            continue
        offsets[wid] = s_end
        s_end += _wall_frame(walls[wid])[3]
    s_start = offsets[mid]
    for wid in reversed(ids[:i]):
        if wid not in walls:
            continue
        s_start -= _wall_frame(walls[wid])[3]
        offsets[wid] = s_start
    return offsets


def chain_bounds_m(scene: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """World AABB of chain s ∈ [-8.5, 8.5] m and out ∈ [-0.5, 6.0] m, height [-0.3, 3.0]."""
    walls = {w["id"]: w for w in scene["walls"]}
    offsets = chain_offsets_m(scene)
    pts = []
    for wid, s0 in offsets.items():
        origin, along, outward, length = _wall_frame(walls[wid])
        u_lo = max(0.0, -8.5 - s0)
        u_hi = min(length, 8.5 - s0)
        if u_hi <= u_lo:
            continue
        for u in (u_lo, u_hi):
            for out in (-0.5, 6.0):
                pts.append(
                    (
                        origin[0] + along[0] * u + outward[0] * out,
                        origin[1] + along[1] * u + outward[1] * out,
                    )
                )
    if not pts:
        return None
    arr = np.array(pts, float)
    lo = np.array([arr[:, 0].min(), arr[:, 1].min(), -0.3])
    hi = np.array([arr[:, 0].max(), arr[:, 1].max(), 3.0])
    return lo, hi


def _crop(g: Grid, scene: dict) -> Grid:
    bounds = chain_bounds_m(scene)
    if bounds is None:
        return g
    lo, hi = bounds
    i0 = np.floor((lo - g.origin) / g.res).astype(int)
    i1 = np.ceil((hi - g.origin) / g.res).astype(int)
    i0 = np.maximum(i0, 0)
    i1 = np.minimum(i1, np.array(g.shape))
    if np.any(i1 <= i0):
        return g
    sl = tuple(slice(int(a), int(b)) for a, b in zip(i0, i1, strict=True))
    return Grid(g.origin + i0 * g.res, g.res, np.ascontiguousarray(g.state[sl]))


def _plan(o: dict, scene: dict | None = None) -> list | None:
    g = o["geometry"]
    if g["kind"] == "wall_rect":
        return _wall_rect_plan(g, scene or {})
    if g["kind"] == "footprint":
        return g["polygon_m"]
    if g["kind"] == "box":
        c = g["center_m"]
        w, _, d = g["size_m"]
        return [
            [c[0] - w / 2, c[2] - d / 2],
            [c[0] + w / 2, c[2] - d / 2],
            [c[0] + w / 2, c[2] + d / 2],
            [c[0] - w / 2, c[2] + d / 2],
        ]
    if g["kind"] == "point":
        x, _, z = g["pos_m"]
        return [[x, z], [x + 0.05, z], [x + 0.05, z + 0.05]]
    return None


def _wall_rect_plan(g: dict, scene: dict) -> list | None:
    walls = {w["id"]: w for w in scene.get("walls") or []}
    wall = walls.get(g.get("wall_id"))
    if wall is None or "s_m" not in g:
        return None
    origin, along, outward, _length = _wall_frame(wall)
    s0 = (wall.get("s_range_m") or [0.0])[0]
    u0 = float(g["s_m"][0]) - float(s0)
    u1 = float(g["s_m"][1]) - float(s0)
    depth = float(g.get("standoff_m", 0.05))

    def pt(u: float, out: float) -> list[float]:
        return [
            origin[0] + along[0] * u + outward[0] * out,
            origin[1] + along[1] * u + outward[1] * out,
        ]

    return [pt(u0, 0.0), pt(u1, 0.0), pt(u1, depth), pt(u0, depth)]


def _heights(o: dict) -> tuple[float, float]:
    g = o["geometry"]
    if g["kind"] == "wall_rect":
        return g["bottom_m"], g["top_m"]
    if g["kind"] == "box":
        c, h = g["center_m"][1], g["size_m"][1]
        return c - h / 2, c + h / 2
    if g["kind"] == "footprint":
        return g.get("bottom_m", 0.0), g.get("top_m", 1.0)
    return 0.0, 1.0


def c2_rules(profile: dict) -> dict:
    """C2's rule dict from a merged profile. Placeholders stay flagged in the verdict, not here."""
    b = profile["battery"]
    cl = profile["clearances"]
    route = profile["route"]
    return {
        "battery": {"width_ft": b["width_ft"], "depth_ft": b["depth_ft"], "height_ft": b["height_ft"]},
        "clearances": {
            "gas_ft": cl["gas_ft"],
            "ac_ft": cl.get("ac_ft") or 0.0,
            "drive_ft": cl["drive_ft"],
            "pool_ft": cl["pool_ft"],
            "fence_ft": 3.0,
            "opening_ft": profile["openings"]["min_ft"] or 0.0,
            "opening_types": profile["openings"]["types"],
            "wall_equipment_ft": cl["wall_equipment_ft"],
            "wall_equipment_types": profile["wall_equipment_types"],
        },
        "facing": {
            "from_wall_ft": profile["facing"]["min_ft"] if profile["facing"].get("min_ft") is not None else 0.0,
            "measured_from": profile["facing"].get("measured_from") or "wall",
            "min_width_ft": 2.5,
            "ignore_below_ft": 0.33,
            "top_ft": 6.5,
        },
        "headroom": {"min_ft": profile["headroom"]["min_ft"] if profile["headroom"].get("min_ft") is not None else 0.0},
        "meter_working_space": profile["meter_working_space"],
        "ground": {
            "allowed": profile["ground"]["allowed"],
            "drivable": profile["ground"].get("drivable", ["drive"]),
            "max_slope_deg": 5.0,
            "max_step_ft": profile["ground"]["max_step_ft"],
            "min_observed_fraction": 0.8,
        },
        "wall_backing": {"max_gap_ft": 0.5, "min_backed_fraction": 0.85, "min_wall_height_ft": 7.5},
        "route": {
            "max_ft": route["max_ft"],
            "confident_reach_ft": route["confident_reach_ft"],
            "same_wall": route.get("max_corners", 99) == 0,
            "corner_turn_deg": 30.0,
            "corner_penalty_ft": route.get("corner_allowance_ft", 0.5),
            "blocked_by": ["door", "garage_door", "window"],
        },
        "unknown": {"tolerance_voxels": 0, "wall_band_top_ft": 8.0, "ground_band_top_ft": 1.0},
    }
