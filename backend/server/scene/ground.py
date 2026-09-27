"""Step 10 (g) ground-fit guard.

The ground is the lowest well-supported horizontal plane. A candidate whose height
is more than 0.30 m above the lowest wall-base points (the lowest 5 % of points
within 0.30 m of a chain-wall baseline) is rejected. When the 0–0.30 m band above
the fitted ground along the meter wall has fewer than 60 points or fewer than 2
views, the datum is unsure: ``plus_minus_m`` is floored at 0.30 m and
``ground_base_unobserved`` is warned.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from server.scene.params import SceneParams

# Contact band from step 10 (g): points within 0.30 m of a baseline, and 0–0.30 m above the ground.
WALL_BASE_RADIUS_M = 0.30
BASE_BAND_M = 0.30


@dataclass
class GroundGuard:
    ground_y: float | None
    warnings: list[str]
    plus_minus_floor: float
    rejected_high: bool
    verified: bool
    base_points: int
    base_views: int


def guard_ground(
    points: np.ndarray,
    baselines: list[tuple[tuple[float, float], tuple[float, float]]],
    params: SceneParams,
    tracks: list[np.ndarray] | None = None,
    meter_baseline: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> GroundGuard:
    pts = np.asarray(points, float)
    if pts.ndim != 2 or pts.shape[1] != 3:
        pts = np.zeros((0, 3))
    base_y = _lowest_base_y(pts, baselines, WALL_BASE_RADIUS_M)
    slab = _slab_mask(pts)
    rejected = False
    rejected_height: float | None = None
    ground_y: float | None
    if base_y is None:
        ground_y = None
    else:
        limit = base_y + params.ground_guard_max_above_base_m
        low = slab & (pts[:, 1] <= limit)
        high = slab & (pts[:, 1] > limit)
        rejected = int(high.sum()) >= 30
        if rejected:
            rejected_height = float(np.median(pts[high, 1]))
        if int(low.sum()) >= 8:
            ground_y = float(np.median(pts[low, 1]))
        else:
            ground_y = float(base_y)
    meter = meter_baseline or (baselines[0] if baselines else None)
    band_n, band_views = _band_support(pts, tracks, meter, ground_y, params)
    views_fail = tracks is not None and band_views < params.ground_base_min_views
    verified = ground_y is not None and band_n >= params.ground_base_min_pts and not views_fail
    warnings: list[str] = []
    if rejected and rejected_height is not None:
        warnings.append(f"ground_plane_rejected:{rejected_height:.2f}")
    floor = 0.0
    if not verified:
        warnings.append("ground_base_unobserved")
        floor = params.ground_unobserved_pm_m
    return GroundGuard(ground_y, warnings, floor, rejected, verified, band_n, band_views)


def reanchor_ground(scene: dict, state: dict, ground_y: float) -> None:
    """Move the scene frame so a rejected plane's replacement ground is y = 0.

    Heights that were measured from the old origin (wall tops, meter, openings)
    shift by the same amount. Plan baselines stay put.
    """
    dy = -float(ground_y)
    if abs(dy) < 1e-6:
        _set_ground_plane(scene, 0.0)
        return
    points = state.get("points_c")
    if points is not None and len(points):
        points[:, 1] = np.asarray(points[:, 1], float) + dy
    for cam in state.get("cams_c") or []:
        pose = np.array(cam.pose, float, copy=True)
        pose[1, 3] += dy
        cam.pose = pose
    transform = state.get("T")
    if transform is not None:
        shift = np.eye(4)
        shift[1, 3] = dy
        state["T"] = shift @ np.asarray(transform, float)
    _set_ground_plane(scene, 0.0)
    for wall in scene.get("walls") or []:
        if "height_m" in wall:
            wall["height_m"] = round(float(wall["height_m"]) + dy, 3)
    for obj in scene.get("objects") or []:
        geom = obj.get("geometry") or {}
        if geom.get("kind") == "point" and geom.get("pos_m"):
            pos = [float(v) for v in geom["pos_m"]]
            pos[1] = round(pos[1] + dy, 4)
            geom["pos_m"] = pos
        if geom.get("kind") == "wall_rect":
            if "bottom_m" in geom:
                geom["bottom_m"] = round(float(geom["bottom_m"]) + dy, 4)
            if "top_m" in geom:
                geom["top_m"] = round(float(geom["top_m"]) + dy, 4)
    frame = scene.get("frame") or {}
    raw = frame.get("from_source")
    if isinstance(raw, list) and len(raw) == 16:
        current = np.asarray(raw, float).reshape(4, 4, order="F")
        shift = np.eye(4)
        shift[1, 3] = dy
        updated = shift @ current
        frame["from_source"] = [round(float(v), 6) for v in updated.flatten(order="F")]


def _set_ground_plane(scene: dict, offset: float) -> None:
    ground = scene.get("ground")
    if not ground:
        return
    surface = ground.setdefault("surface", {})
    surface["kind"] = surface.get("kind") or "plane"
    surface["normal"] = [0.0, 1.0, 0.0]
    surface["offset_m"] = round(float(offset), 5)


def apply_ground_guard(
    scene: dict,
    points: np.ndarray,
    tracks: list[np.ndarray] | None,
    params: SceneParams,
    notes: list[str],
    state: dict | None = None,
) -> bool:
    """Reject a high fitted plane and floor bounds when the wall base is thin."""
    baselines = [_baseline(w) for w in scene.get("walls", [])]
    baselines = [b for b in baselines if b is not None]
    meter_id = _meter_wall_id(scene)
    meter = None
    for wall in scene.get("walls", []):
        if wall.get("id") == meter_id:
            meter = _baseline(wall)
            break
    guard = guard_ground(points, baselines, params, tracks=tracks, meter_baseline=meter)
    for note in guard.warnings:
        if note not in notes:
            notes.append(note)
    if guard.rejected_high and guard.ground_y is not None and scene.get("ground"):
        fitted = _plane_height(scene["ground"])
        if fitted is None or fitted > guard.ground_y + params.ground_guard_max_above_base_m:
            if state is not None:
                reanchor_ground(scene, state, float(guard.ground_y))
                if "ground_reanchored" not in notes:
                    notes.append("ground_reanchored")
            else:
                scene["ground"]["surface"]["normal"] = [0.0, 1.0, 0.0]
                scene["ground"]["surface"]["offset_m"] = round(float(guard.ground_y), 5)
    if not guard.verified:
        floor_plus_minus(scene, guard.plus_minus_floor)
    return not guard.verified


def floor_plus_minus(scene: dict, floor: float) -> None:
    """Raise every height-referenced bound to at least ``floor`` metres."""
    if floor <= 0:
        return
    for wall in scene.get("walls", []):
        wall["plus_minus_m"] = round(max(float(wall["plus_minus_m"]), floor), 3)
    ground = scene.get("ground")
    if ground is not None and "plus_minus_m" in ground:
        ground["plus_minus_m"] = round(max(float(ground["plus_minus_m"]), floor), 3)
    for obj in scene.get("objects", []):
        if "plus_minus_m" in obj:
            obj["plus_minus_m"] = round(max(float(obj["plus_minus_m"]), floor), 3)
    for corner in scene.get("chain", {}).get("corners", []):
        if "plus_minus_m" in corner:
            corner["plus_minus_m"] = round(max(float(corner["plus_minus_m"]), floor), 3)
    anchor = scene.get("frame", {}).get("anchor")
    if anchor is not None and "plus_minus_m" in anchor:
        anchor["plus_minus_m"] = round(max(float(anchor["plus_minus_m"]), floor), 3)


def _baseline(wall: dict):
    raw = wall.get("baseline_m")
    if not raw or len(raw) != 2:
        return None
    return (tuple(raw[0]), tuple(raw[1]))


def _meter_wall_id(scene: dict) -> str | None:
    for obj in scene.get("objects", []):
        if obj.get("class") == "meter" and obj.get("wall_id"):
            return obj["wall_id"]
        geom = obj.get("geometry") or {}
        if obj.get("class") == "meter" and geom.get("wall_id"):
            return geom["wall_id"]
    walls = scene.get("walls") or []
    return walls[0]["id"] if walls else None


def _plane_height(ground: dict) -> float | None:
    surface = ground.get("surface") or {}
    normal = np.asarray(surface.get("normal") or [0, 1, 0], float)
    if abs(float(normal[1])) < 1e-6:
        return None
    offset = float(surface.get("offset_m") or 0.0)
    # n · x = offset, so y = (offset - n_x x - n_z z) / n_y. At the origin this is offset / n_y.
    return offset / float(normal[1])


def _dist_to_segments(xz: np.ndarray, segments: list) -> np.ndarray:
    best = np.full(len(xz), np.inf)
    for a, b in segments:
        a = np.asarray(a, float)
        b = np.asarray(b, float)
        ab = b - a
        length2 = float(ab @ ab) or 1.0
        t = np.clip(((xz - a) @ ab) / length2, 0.0, 1.0)
        proj = a + t[:, None] * ab
        best = np.minimum(best, np.linalg.norm(xz - proj, axis=1))
    return best


def _lowest_base_y(pts: np.ndarray, baselines: list, radius: float) -> float | None:
    """Lowest horizontal contact near a baseline.

    A vertical wall that reaches down to 0.1 m must not pull the datum up to
    its 5th percentile. Horizontal points within 5 cm of the lowest contact
    set the height. With no horizontal contact, the lowest near point is only
    an upper bound (the wall bottom), and the band check stays unverified.
    """
    if len(pts) == 0 or not baselines:
        return None
    near = _dist_to_segments(pts[:, [0, 2]], baselines) <= radius
    if not np.any(near):
        return None
    horizontal = near & _slab_mask(pts)
    if np.any(horizontal):
        floor = float(pts[horizontal, 1].min())
        cluster = horizontal & (pts[:, 1] <= floor + 0.05)
        return float(np.median(pts[cluster, 1]))
    return float(pts[near, 1].min())


def _slab_mask(pts: np.ndarray, plan_bin: float = 0.25, max_extent: float = 0.20) -> np.ndarray:
    """Points that sit in a plan bin flatter than a wall. Density is judged later."""
    mask = np.zeros(len(pts), dtype=bool)
    if len(pts) == 0:
        return mask
    keys = np.floor(pts[:, [0, 2]] / plan_bin).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    keys_s = keys[order]
    change = np.zeros(0, dtype=bool) if len(keys_s) < 2 else np.any(np.diff(keys_s, axis=0) != 0, axis=1)
    starts = np.r_[0, np.flatnonzero(change) + 1]
    ends = np.r_[starts[1:], len(keys_s)]
    for a, b in zip(starts, ends, strict=True):
        idx = order[a:b]
        ys = pts[idx, 1]
        if float(ys.max() - ys.min()) <= max_extent:
            mask[idx] = True
    return mask


def _band_support(pts, tracks, meter_baseline, ground_y: float | None, params: SceneParams) -> tuple[int, int]:
    if ground_y is None or meter_baseline is None or len(pts) == 0:
        return 0, 0
    near = _dist_to_segments(pts[:, [0, 2]], [meter_baseline]) <= WALL_BASE_RADIUS_M
    # Wall-face points are vertical. Only a horizontal slab in the band counts as base support.
    band = near & _slab_mask(pts) & (pts[:, 1] >= ground_y) & (pts[:, 1] <= ground_y + BASE_BAND_M)
    count = int(band.sum())
    views = 0
    if tracks is not None and count:
        seen: set[int] = set()
        for index in np.flatnonzero(band):
            if index < len(tracks):
                seen.update(int(v) for v in np.asarray(tracks[index]).ravel())
        views = len(seen)
    return count, views
