"""Post-extract guards: extents, glass, phantoms, spans, incidence, pose floors."""

from __future__ import annotations

import numpy as np

from server.scene.params import SceneParams

GROUND_MATERIALS = {
    "grass": "lawn",
    "gravel": "gravel",
    "concrete_or_paving": "concrete",
    "mulch_or_soil": "mulch",
    "deck": "deck",
    "other": "unknown",
}


SCENE_MATERIALS = {"lawn", "gravel", "concrete", "pavers", "mulch", "dirt", "deck", "drive", "walkway", "unknown"}


def material_of(labels: list[str]) -> str:
    """Mode of ground votes. Needs two agreeing votes; a single vote or a tie is unknown (SD-47)."""
    mapped = []
    for label in labels:
        name = GROUND_MATERIALS.get(label, label)
        if name not in SCENE_MATERIALS:
            name = "unknown"
        if name != "unknown":
            mapped.append(name)
    if not mapped:
        return "unknown"
    counts: dict[str, int] = {}
    for name in mapped:
        counts[name] = counts.get(name, 0) + 1
    best = max(counts.values())
    winners = [name for name, count in counts.items() if count == best]
    if best < 2 or len(winners) != 1:
        return "unknown"
    return winners[0]


def sort_span(span: list[float] | tuple[float, float]) -> list[float]:
    a, b = float(span[0]), float(span[1])
    return [a, b] if a <= b else [b, a]


def sort_scene_spans(scene: dict) -> None:
    for wall in scene.get("walls", []):
        if "s_range_m" in wall:
            wall["s_range_m"] = sort_span(wall["s_range_m"])
    for band in scene.get("coverage", {}).get("bands", []):
        if "s_m" in band:
            band["s_m"] = sort_span(band["s_m"])
    for obj in scene.get("objects", []):
        geom = obj.get("geometry") or {}
        if "s_m" in geom:
            geom["s_m"] = sort_span(geom["s_m"])
    for name in ("overheads", "facing"):
        for row in scene.get(name) or []:
            if "s_m" in row:
                row["s_m"] = sort_span(row["s_m"])


def clamp_angle(value: float | None, warnings: list[str]) -> float | None:
    if value is None:
        return None
    val = float(value)
    if not np.isfinite(val):
        return None
    if val < 0.0 or val > 90.0:
        if "incidence_clamped" not in warnings:
            warnings.append("incidence_clamped")
        return float(min(90.0, max(0.0, val)))
    return val


def refit_wall_extents(scene: dict, points: np.ndarray, line_thr: float = 0.08) -> None:
    """Replace the extractor's 1–99 percentile ends with the inlier min and max.

    The reference extractor trims about 1 % off each end, which misses a 0.02 m
    length gate on a short synthetic wall. Inliers within ``line_thr`` set the ends.
    """
    pts = np.asarray(points, float)
    if len(pts) == 0:
        return
    for wall in scene["walls"]:
        if wall.get("evidence") != "observed":
            continue
        (x0, z0), (x1, z1) = wall["baseline_m"]
        direction = np.array([x1 - x0, z1 - z0], float)
        length = float(np.linalg.norm(direction))
        if length < 1e-6:
            continue
        direction /= length
        normal = np.array([-direction[1], direction[0]])
        rel = pts[:, [0, 2]] - np.array([x0, z0])
        along = rel @ direction
        off = np.abs(rel @ normal)
        keep = (off < line_thr) & (along > -0.5) & (along < length + 0.5) & (pts[:, 1] > 0.05) & (pts[:, 1] < 3.0)
        if int(keep.sum()) < 30:
            continue
        t0 = float(along[keep].min())
        t1 = float(along[keep].max())
        if t1 - t0 < 0.5:
            continue
        a = np.array([x0, z0]) + t0 * direction
        b = np.array([x0, z0]) + t1 * direction
        wall["baseline_m"] = [[round(float(v), 4) for v in a], [round(float(v), 4) for v in b]]
        s0 = float(wall["s_range_m"][0]) + t0
        wall["s_range_m"] = [round(s0, 4), round(s0 + (t1 - t0), 4)]
    _sync_chain_to_walls(scene)


def _sync_chain_to_walls(scene: dict) -> None:
    walls = scene["walls"]
    by_id = {w["id"]: w for w in walls}
    chain = scene["chain"]
    ids = [i for i in chain["wall_ids"] if i in by_id]
    chain["wall_ids"] = ids
    if not ids:
        return
    chain["ends"]["left"]["point_m"] = by_id[ids[0]]["baseline_m"][0]
    chain["ends"]["left"]["s_m"] = by_id[ids[0]]["s_range_m"][0]
    chain["ends"]["right"]["point_m"] = by_id[ids[-1]]["baseline_m"][1]
    chain["ends"]["right"]["s_m"] = by_id[ids[-1]]["s_range_m"][1]
    corners = []
    for i in range(len(ids) - 1):
        left, right = by_id[ids[i]], by_id[ids[i + 1]]
        prev = next((c for c in scene["chain"].get("corners", []) if c["left_wall"] == left["id"]), {})
        d0 = _dir(left["baseline_m"])
        d1 = _dir(right["baseline_m"])
        turn = float(np.degrees(np.arctan2(d0[0] * d1[1] - d0[1] * d1[0], float(np.dot(d0, d1)))))
        corners.append(
            {
                "id": prev.get("id", f"c{i + 1}"),
                "left_wall": left["id"],
                "right_wall": right["id"],
                "point_m": left["baseline_m"][1],
                "s_m": left["s_range_m"][1],
                "turn_deg": round(turn, 2),
                "plus_minus_m": max(float(left["plus_minus_m"]), float(right["plus_minus_m"])),
                "evidence": prev.get("evidence", "inferred"),
            }
        )
    chain["corners"] = corners


def _dir(baseline: list) -> np.ndarray:
    a, b = np.asarray(baseline[0], float), np.asarray(baseline[1], float)
    d = b - a
    n = np.linalg.norm(d)
    return d / n if n > 1e-9 else np.array([1.0, 0.0])


def drop_glass_walls(scene: dict, params: SceneParams, warnings: list[str]) -> None:
    """Drop a short segment 0.2–1.2 m behind a longer chain wall and within 5° of it."""
    walls = list(scene["walls"])
    drop: set[str] = set()
    lo, hi = params.glass_behind_m
    for wall in walls:
        if wall["id"] in drop or wall.get("evidence") != "observed":
            continue
        for other in walls:
            if other["id"] == wall["id"] or other["id"] in drop:
                continue
            ang, off, behind = _parallel_offset(other["baseline_m"], wall["baseline_m"])
            if ang > 5.0 or not (lo <= off <= hi) or not behind:
                continue
            front_n = int(other.get("support", {}).get("points", 0))
            back_n = int(wall.get("support", {}).get("points", 0))
            if front_n > 0 and back_n < 0.30 * front_n:
                drop.add(wall["id"])
                warnings.append(f"glass_segment_dropped:{wall['id']}")
    if drop:
        scene["walls"] = [w for w in walls if w["id"] not in drop]
        _sync_chain_to_walls(scene)


def _parallel_offset(front: list, back: list) -> tuple[float, float, bool]:
    d = _dir(front)
    normal = np.array([-d[1], d[0]])
    a = np.asarray(front[0], float)
    b0 = np.asarray(back[0], float)
    b1 = np.asarray(back[1], float)
    d2 = _dir(back)
    ang = float(np.degrees(np.arccos(np.clip(abs(float(np.dot(d, d2))), -1, 1))))
    signed = float(np.mean([(b0 - a) @ normal, (b1 - a) @ normal]))
    return ang, abs(signed), signed < 0


def drop_phantom_walls(
    scene: dict,
    points: np.ndarray,
    src: np.ndarray,
    params: SceneParams,
    warnings: list[str],
    line_thr: float = 0.08,
) -> None:
    """Drop a π³-dominated segment with fewer than 30 sparse points. LiDAR is exempt."""
    pts = np.asarray(points, float)
    src = np.asarray(src)
    drop: set[str] = set()
    for wall in scene["walls"]:
        if wall.get("evidence") != "observed":
            continue
        (x0, z0), (x1, z1) = wall["baseline_m"]
        direction = np.array([x1 - x0, z1 - z0], float)
        length = float(np.linalg.norm(direction)) or 1.0
        direction /= length
        normal = np.array([-direction[1], direction[0]])
        rel = pts[:, [0, 2]] - np.array([x0, z0])
        near = (np.abs(rel @ normal) < line_thr) & (rel @ direction > -0.2) & (rel @ direction < length + 0.2)
        if int(near.sum()) < 10:
            continue
        kinds = src[near]
        lidar = int(np.sum(kinds == 2))
        if lidar > 0.1 * len(kinds):
            continue
        pi3_share = float(np.mean(kinds == 1)) if len(kinds) else 0.0
        sparse = int(np.sum(kinds == 0))
        if pi3_share >= 0.90 and sparse < params.phantom_min_sparse_pts:
            drop.add(wall["id"])
            warnings.append(f"wall_dropped_no_sparse_support:{wall['id']}")
    if drop:
        scene["walls"] = [w for w in scene["walls"] if w["id"] not in drop]
        if not scene["walls"]:
            return
        _sync_chain_to_walls(scene)


def apply_pose_floors(scene: dict, pose_sources: list[str], params: SceneParams, warnings: list[str]) -> None:
    if any(src == "arkit" for src in pose_sources):
        _add_pm(scene, params.fallback_floor_m)
        if "fallback_poses_widened" not in warnings:
            warnings.append("fallback_poses_widened")


def apply_base_widen(scene: dict, extra: float) -> None:
    if extra > 0:
        _add_pm(scene, extra)


def _add_pm(scene: dict, extra: float) -> None:
    for wall in scene["walls"]:
        wall["plus_minus_m"] = round(float(wall["plus_minus_m"]) + extra, 3)
    ground = scene.get("ground")
    if ground is not None:
        ground["plus_minus_m"] = round(float(ground["plus_minus_m"]) + extra, 3)
    for obj in scene.get("objects", []):
        obj["plus_minus_m"] = round(float(obj["plus_minus_m"]) + extra, 3)
    for corner in scene.get("chain", {}).get("corners", []):
        corner["plus_minus_m"] = round(float(corner.get("plus_minus_m", 0.0)) + extra, 3)
    anchor = scene.get("frame", {}).get("anchor")
    if anchor is not None and "plus_minus_m" in anchor:
        anchor["plus_minus_m"] = round(float(anchor["plus_minus_m"]) + extra, 3)


def upgrade_evidence(scene: dict, grid, warnings: list[str]) -> None:
    """Corner inferred→observed when both faces are seen; end unexplored→limit on free space."""
    del warnings
    if grid is None:
        return
    corners = scene["chain"].get("corners", [])
    walls = {w["id"]: w for w in scene["walls"]}
    for corner in corners:
        if corner.get("evidence") != "inferred":
            continue
        left, right = walls.get(corner["left_wall"]), walls.get(corner["right_wall"])
        if left is None or right is None:
            continue
        if float(left.get("observed_fraction") or 0) >= 0.5 and float(right.get("observed_fraction") or 0) >= 0.5:
            if _near_corner_observed(grid, corner["point_m"]):
                corner["evidence"] = "observed"
    for side in ("left", "right"):
        end = scene["chain"]["ends"][side]
        if end.get("kind") != "unexplored":
            continue
        if _free_past_end(grid, end.get("point_m"), walls, scene["chain"]["wall_ids"], side):
            end["kind"] = "limit"
            end["note"] = "free space observed past the end"


def _near_corner_observed(grid, point) -> bool:
    x, z = point
    probes = [[x, y, z] for y in (0.15, 0.5, 1.0)]
    return bool(np.all(grid.observed_at(np.asarray(probes, float))))


def _free_past_end(grid, point, walls: dict, wall_ids: list[str], side: str) -> bool:
    if point is None or not wall_ids:
        return False
    wall = walls[wall_ids[0] if side == "left" else wall_ids[-1]]
    d = _dir(wall["baseline_m"])
    if side == "left":
        d = -d
    origin = np.asarray(point, float)
    probes = []
    for dist in (0.5, 1.0, 1.5):
        for y in (0.5, 1.0, 1.5):
            xz = origin + dist * d
            probes.append([xz[0], y, xz[1]])
    seen = grid.observed_at(np.asarray(probes, float))
    return bool(seen.mean() >= 0.5)
