"""Wall completion from ARKit vertical planes and wall-end taps.

Completed walls join the chain. They do not receive a coverage band the grid
did not produce, so an unseen wall stays ``unobserved``.
"""

from __future__ import annotations

import numpy as np

from server.scene.features import _dir, _sync_chain_to_walls
from server.scene.params import SceneParams


def _mat4(values) -> np.ndarray:
    m = np.asarray(values, float).reshape(4, 4)
    # Packet mat4 is column-major.
    return m.T


def _apply(transform: np.ndarray, points: np.ndarray) -> np.ndarray:
    pts = np.atleast_2d(np.asarray(points, float))
    hom = np.c_[pts, np.ones(len(pts))]
    out = hom @ transform.T
    return out[:, :3]


def plane_baseline_scene(plane: dict, to_scene: np.ndarray) -> tuple[np.ndarray, np.ndarray, float] | None:
    extent = plane.get("planeExtent") or {}
    width = float(extent.get("width", 0.0))
    height = float(extent.get("height", 0.0))
    if "transform" not in plane:
        return None
    local = _mat4(plane["transform"])
    center = local[:3, 3]
    axis = local[:3, 0]
    a = center - axis * (width / 2.0)
    b = center + axis * (width / 2.0)
    ab = _apply(to_scene, np.stack([a, b]))
    return ab[0], ab[1], height


def complete_walls(
    scene: dict, planes: list[dict], taps: list[dict], to_scene: np.ndarray, params: SceneParams, warnings: list[str]
) -> None:
    if not params.complete_walls:
        return
    walls = scene["walls"]
    classified = [p for p in planes if p.get("alignment") == "vertical" and p.get("classification") == "wall"]
    pool = classified or [p for p in planes if p.get("alignment") == "vertical"]
    next_id = 0
    for plane in pool:
        parsed = plane_baseline_scene(plane, to_scene)
        if parsed is None:
            continue
        a3, b3, height = parsed
        if height < 1.0:
            continue
        a = np.array([a3[0], a3[2]])
        b = np.array([b3[0], b3[2]])
        if float(np.linalg.norm(b - a)) < params.plane_min_extent_m:
            continue
        host = _matching_wall(walls, a, b)
        if host is not None:
            _extend_wall(host, a, b)
            warnings.append(f"wall_completed:{host['id']}:{plane.get('id', 'plane')}")
            continue
        joined = _join_new_wall(scene, a, b, height, params, f"wp{next_id}")
        if joined:
            warnings.append(f"wall_completed:{joined}:{plane.get('id', 'plane')}")
            next_id += 1
    _sync_chain_to_walls(scene)
    for tap in taps:
        label = str(tap.get("label", ""))
        if label not in {"corner", "wall_end_left", "wall_end_right"}:
            continue
        hit = (tap.get("hit") or {}).get("position")
        if hit is None:
            continue
        point = _apply(to_scene, np.asarray(hit, float))[0]
        plan = np.array([point[0], point[2]])
        if label == "corner":
            _snap_corner(scene, plan)
        else:
            side = "left" if label.endswith("left") else "right"
            _snap_end(scene, side, plan)


def _matching_wall(walls: list[dict], a: np.ndarray, b: np.ndarray) -> dict | None:
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-6:
        return None
    d = d / n
    best = None
    for wall in walls:
        wd = _dir(wall["baseline_m"])
        ang = float(np.degrees(np.arccos(np.clip(abs(float(np.dot(d, wd))), -1, 1))))
        if ang > 5.0:
            continue
        p0 = np.asarray(wall["baseline_m"][0], float)
        normal = np.array([-wd[1], wd[0]])
        off = max(abs(float((a - p0) @ normal)), abs(float((b - p0) @ normal)))
        if off <= 0.15 and (best is None or off < best[0]):
            best = (off, wall)
    return None if best is None else best[1]


def _extend_wall(wall: dict, a: np.ndarray, b: np.ndarray) -> None:
    p0 = np.asarray(wall["baseline_m"][0], float)
    p1 = np.asarray(wall["baseline_m"][1], float)
    d = _dir(wall["baseline_m"])
    ts = [(a - p0) @ d, (b - p0) @ d, 0.0, float(np.linalg.norm(p1 - p0))]
    t0, t1 = min(ts), max(ts)
    wall["baseline_m"] = [[round(float(v), 4) for v in p0 + t0 * d], [round(float(v), 4) for v in p0 + t1 * d]]
    s0 = float(wall["s_range_m"][0]) + t0
    wall["s_range_m"] = [round(s0, 4), round(s0 + (t1 - t0), 4)]


def _join_new_wall(
    scene: dict, a: np.ndarray, b: np.ndarray, height: float, params: SceneParams, wid: str
) -> str | None:
    chain = scene["chain"]
    walls = {w["id"]: w for w in scene["walls"]}
    ids = chain["wall_ids"]
    if not ids:
        return None
    left = walls[ids[0]]
    right = walls[ids[-1]]
    left_pt = np.asarray(left["baseline_m"][0], float)
    right_pt = np.asarray(right["baseline_m"][1], float)
    ends = [(float(np.linalg.norm(a - left_pt)), "left", a, b), (float(np.linalg.norm(b - left_pt)), "left", b, a)]
    ends += [(float(np.linalg.norm(a - right_pt)), "right", a, b), (float(np.linalg.norm(b - right_pt)), "right", b, a)]
    dist, side, near, far = min(ends, key=lambda row: row[0])
    if dist > 1.5:
        return None
    if side == "left":
        s_join = float(left["s_range_m"][0])
        length = float(np.linalg.norm(far - near))
        s_range = [round(s_join - length, 4), round(s_join, 4)]
        baseline = [[round(float(v), 4) for v in far], [round(float(v), 4) for v in near]]
        chain["wall_ids"] = [wid, *ids]
    else:
        s_join = float(right["s_range_m"][1])
        length = float(np.linalg.norm(far - near))
        s_range = [round(s_join, 4), round(s_join + length, 4)]
        baseline = [[round(float(v), 4) for v in near], [round(float(v), 4) for v in far]]
        chain["wall_ids"] = [*ids, wid]
    scene["walls"].append(
        {
            "id": wid,
            "role": "house",
            "baseline_m": baseline,
            "height_m": round(max(height, 1.0), 2),
            "s_range_m": s_range,
            "plus_minus_m": params.completion_pm_m,
            "evidence": "inferred",
            "observed_fraction": 0.0,
            "support": {"points": 0, "views": []},
        }
    )
    _sync_chain_to_walls(scene)
    for corner in scene["chain"]["corners"]:
        if wid in (corner["left_wall"], corner["right_wall"]) and corner.get("evidence") != "observed":
            corner["evidence"] = "inferred"
            corner["plus_minus_m"] = params.completion_pm_m
    return wid


def _snap_end(scene: dict, side: str, plan: np.ndarray) -> None:
    end = scene["chain"]["ends"][side]
    current = np.asarray(end.get("point_m", plan), float)
    if float(np.linalg.norm(plan - current)) > 1.0:
        return
    end["point_m"] = [round(float(v), 4) for v in plan]
    end["note"] = f"tap wall_end_{side}"


def _snap_corner(scene: dict, plan: np.ndarray) -> None:
    best = None
    for corner in scene["chain"].get("corners", []):
        dist = float(np.linalg.norm(plan - np.asarray(corner["point_m"], float)))
        if dist <= 1.0 and (best is None or dist < best[0]):
            best = (dist, corner)
    if best is not None:
        best[1]["point_m"] = [round(float(v), 4) for v in plan]
