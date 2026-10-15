"""Scene metres to ARKit world. All mat4 values are 16 numbers, column-major."""

from __future__ import annotations

import math

import numpy as np

from server.result.harness import chain as chain_mod
from server.result.harness import frame as frame_mod

STANDOFF_M = 0.02
ROUTE_HEIGHT_M = 0.3048
WALL_CAMERA = (3.0, 1.4)
GROUND_CAMERA = (1.0, 1.3)
FACING_HEIGHT = 1.3
CLOSE_UP_M = 1.0
CONFIRM_M = 1.5

_ARKIT_PREFIX = "arkit_world"


def parse_source_frame(source: str) -> tuple[bool, str | None]:
    """Accepted spellings: ``arkit_world``, ``arkit_world/<epoch>``, ``arkit_world:<epoch>``."""

    if source == _ARKIT_PREFIX:
        return True, None
    for sep in ("/", ":"):
        prefix = _ARKIT_PREFIX + sep
        if source.startswith(prefix) and len(source) > len(prefix):
            return True, source[len(prefix) :]
    return False, None


def scene_to_arkit(frame: dict) -> np.ndarray | None:
    """Inverse of ``frame.from_source`` when the source is an ARKit epoch."""

    ok, _epoch = parse_source_frame(str(frame.get("source_frame") or ""))
    if not ok or "from_source" not in frame:
        return None
    return np.linalg.inv(frame_mod.mat4_from_list(frame["from_source"]))


def epoch_id(frame: dict, epochs: list[str] | None = None) -> str | None:
    ok, named = parse_source_frame(str(frame.get("source_frame") or ""))
    if not ok:
        return None
    if named:
        return named
    if epochs and len(epochs) == 1:
        return epochs[0]
    return None


def box_to_arkit(t_sa: np.ndarray, center_m, size_m, yaw_deg) -> dict:
    """Box-to-ARKit-world pose. Local +x along the wall, +y up, +z out of the wall."""

    yaw = math.radians(float(yaw_deg))
    dx, dz = math.cos(yaw), -math.sin(yaw)
    nx, nz = -dz, dx
    pose = np.eye(4)
    pose[:3, 0] = (dx, 0.0, dz)
    pose[:3, 1] = (0.0, 1.0, 0.0)
    pose[:3, 2] = (nx, 0.0, nz)
    pose[:3, 3] = np.asarray(center_m, float)
    world = frame_mod.transform_pose(t_sa, pose)
    scale = frame_mod.scale_of(t_sa)
    size = [float(v) * scale for v in size_m]
    return {"pose": frame_mod.mat4_to_list(world), "size": size}


def points_to_arkit(t_sa: np.ndarray, pts_m: np.ndarray) -> list[list[float]]:
    mapped = frame_mod.apply(t_sa, np.asarray(pts_m, float))
    return [[float(x) for x in row] for row in np.atleast_2d(mapped)]


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return v / n


def look_pose(cam, target) -> np.ndarray:
    """Camera-to-world. +x right, +y up, looking along −z at ``target``."""

    up = np.array([0.0, 1.0, 0.0])
    z_cam = _unit(np.asarray(cam, float) - np.asarray(target, float))
    x_cam = np.cross(up, z_cam)
    if float(np.linalg.norm(x_cam)) < 1e-8:
        x_cam = np.cross(np.array([1.0, 0.0, 0.0]), z_cam)
    x_cam = _unit(x_cam)
    y_cam = _unit(np.cross(z_cam, x_cam))
    pose = np.eye(4)
    pose[:3, 0] = x_cam
    pose[:3, 1] = y_cam
    pose[:3, 2] = z_cam
    pose[:3, 3] = np.asarray(cam, float)
    return pose


def _wall(chain, wall_id: str | None, s: float):
    if wall_id and wall_id in chain.by_id:
        return chain.by_id[wall_id]
    found = chain.wall_at_s(s)
    if found is not None:
        return found
    return chain.walls[0] if s <= chain.s_min else chain.walls[-1]


def point_on_wall(chain, wall_id: str | None, s: float, out: float, y: float) -> np.ndarray:
    wall = _wall(chain, wall_id, s)
    plan = np.asarray(wall.plan_at(s), float)
    normal = np.asarray(wall.outward, float)
    return np.array([plan[0] + normal[0] * out, y, plan[1] + normal[1] * out])


def _span(view: dict, chain, wall_id: str | None) -> list[float]:
    raw = view.get("s_m") or view.get("sM")
    if raw and len(raw) == 2:
        a, b = float(raw[0]), float(raw[1])
        return [a, b] if a <= b else [b, a]
    wall = _wall(chain, wall_id, 0.0)
    side = view.get("side")
    if view.get("kind") == "past_end":
        if side == "left":
            return [wall.s0 - 2.0, wall.s0]
        return [wall.s1, wall.s1 + 2.0]
    return [wall.s0, wall.s1]


def _heights(kind: str, band: str | None) -> tuple[float, float]:
    if band == "overhead":
        return 1.22, 2.13
    if kind in {"facing", "region"} or band in {"facing"}:
        return 0.3, 1.83
    return 0.0, 2.13


def region_polygon_scene(view: dict, scene: dict) -> tuple[str, np.ndarray]:
    """``("wall"|"ground"|"free", 4x3)`` in scene metres."""

    chain = chain_mod.Chain(scene)
    kind = view.get("kind") or "band"
    band = view.get("band")
    wall_id = view.get("wall_id") or view.get("wallId")
    span = _span(view, chain, wall_id)
    s0, s1 = span
    out_m = float(view.get("out_m") if view.get("out_m") is not None else view.get("outM") or 0.0)

    if kind in {"close_up", "confirm_attribute"}:
        return "wall", _object_face(view, scene, chain)

    if kind == "past_end" or band == "wall":
        y0, y1 = 0.0, 2.13
        pts = [
            point_on_wall(chain, wall_id, s0, 0.0, y0),
            point_on_wall(chain, wall_id, s1, 0.0, y0),
            point_on_wall(chain, wall_id, s1, 0.0, y1),
            point_on_wall(chain, wall_id, s0, 0.0, y1),
        ]
        return "wall", np.vstack(pts)

    if band == "ground":
        reach = out_m if out_m > 0 else 1.0
        corners = []
        for s, out in ((s0, 0.0), (s1, 0.0), (s1, reach), (s0, reach)):
            plan = point_on_wall(chain, wall_id, s, out, 0.0)
            gy = float(np.asarray(chain_mod.ground_y(scene, plan[[0, 2]])).reshape(-1)[0])
            corners.append(np.array([plan[0], gy, plan[2]]))
        return "ground", np.vstack(corners)

    y0, y1 = _heights(kind, band)
    reach = out_m if out_m > 0 else 1.0
    pts = [
        point_on_wall(chain, wall_id, s0, reach, y0),
        point_on_wall(chain, wall_id, s1, reach, y0),
        point_on_wall(chain, wall_id, s1, reach, y1),
        point_on_wall(chain, wall_id, s0, reach, y1),
    ]
    return "free", np.vstack(pts)


def _object_face(view: dict, scene: dict, chain) -> np.ndarray:
    obj = _match_object(view, scene)
    if obj is None:
        wall_id = view.get("wall_id")
        span = _span(view, chain, wall_id)
        return np.vstack(
            [
                point_on_wall(chain, wall_id, span[0], 0.0, 0.4),
                point_on_wall(chain, wall_id, span[1], 0.0, 0.4),
                point_on_wall(chain, wall_id, span[1], 0.0, 1.8),
                point_on_wall(chain, wall_id, span[0], 0.0, 1.8),
            ]
        )
    geom = obj["geometry"]
    kind = geom["kind"]
    if kind == "wall_rect":
        wall_id = geom["wall_id"]
        s0, s1 = float(geom["s_m"][0]), float(geom["s_m"][1])
        if s0 > s1:
            s0, s1 = s1, s0
        y0, y1 = float(geom["bottom_m"]), float(geom["top_m"])
        return np.vstack(
            [
                point_on_wall(chain, wall_id, s0, 0.0, y0),
                point_on_wall(chain, wall_id, s1, 0.0, y0),
                point_on_wall(chain, wall_id, s1, 0.0, y1),
                point_on_wall(chain, wall_id, s0, 0.0, y1),
            ]
        )
    center = chain_mod.object_center(obj, chain)
    wall_id = obj.get("wall_id") or geom.get("wall_id")
    wall = _wall(chain, wall_id, 0.0)
    normal = np.asarray(wall.outward, float)
    along = np.asarray(wall.along, float)
    half_w, half_h = 0.25, 0.3
    face = center - np.array([normal[0], 0.0, normal[1]]) * 0.01
    dx = np.array([along[0], 0.0, along[1]]) * half_w
    dy = np.array([0.0, half_h, 0.0])
    return np.vstack([face - dx - dy, face + dx - dy, face + dx + dy, face - dx + dy])


def _match_object(view: dict, scene: dict) -> dict | None:
    target = (view.get("target") or "").lower()
    wanted = view.get("target_class")
    for obj in scene.get("objects") or []:
        if obj.get("id") == "meter" and "meter" not in target and wanted != "meter":
            continue
        if wanted and obj.get("class") == wanted:
            return obj
        if obj.get("class") and obj["class"] in target:
            return obj
        if obj.get("id") and obj["id"] in target:
            return obj
    return None


def camera_pose_for_view(view: dict, scene: dict) -> np.ndarray:
    """Camera-to-scene mat4, ARKit camera axes."""

    chain = chain_mod.Chain(scene)
    kind = view.get("kind") or "band"
    band = view.get("band")
    wall_id = view.get("wall_id") or view.get("wallId")
    _surface, polygon = region_polygon_scene(view, scene)
    centre = polygon.mean(axis=0)
    span = _span(view, chain, wall_id)
    s_mid = (span[0] + span[1]) / 2
    wall = _wall(chain, wall_id, s_mid)
    normal = np.asarray(wall.outward, float)

    if kind in {"close_up", "confirm_attribute"}:
        dist = CLOSE_UP_M if kind == "close_up" else CONFIRM_M
        obj = _match_object(view, scene)
        target = chain_mod.object_center(obj, chain) if obj is not None else centre
        cam = target + np.array([normal[0], 0.0, normal[1]]) * dist
        return look_pose(cam, target)

    if band == "ground":
        out, height = GROUND_CAMERA
        reach = float(view.get("out_m") or view.get("outM") or 1.0)
        target = point_on_wall(chain, wall_id, s_mid, reach / 2, 0.0)
        gy = float(np.asarray(chain_mod.ground_y(scene, target[[0, 2]])).reshape(-1)[0])
        target = np.array([target[0], gy, target[2]])
        cam_plan = point_on_wall(chain, wall_id, s_mid, out, height)
        return look_pose(cam_plan, target)

    if band == "facing" or kind == "facing":
        cam = point_on_wall(chain, wall_id, s_mid, 0.0, FACING_HEIGHT)
        target = cam + np.array([normal[0], 0.0, normal[1]])
        return look_pose(cam, target)

    out, height = WALL_CAMERA
    cam = point_on_wall(chain, wall_id, s_mid, out, height)
    return look_pose(cam, centre)


def best_reference_crop(region_pts_m: np.ndarray, scene: dict) -> dict | None:
    """Keyframe with the smallest incidence whose projection lies inside the image."""

    pts = np.atleast_2d(np.asarray(region_pts_m, float))
    if len(pts) < 3:
        return None
    centre = pts.mean(axis=0)
    normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
    norm = float(np.linalg.norm(normal))
    if norm < 1e-9:
        return None
    normal = normal / norm
    best = None
    best_angle = 1e9
    for kf in scene.get("keyframes") or []:
        pose = frame_mod.mat4_from_list(kf["pose"])
        u, v, depth = frame_mod.project_arkit(pose, kf["intrinsics"], pts)
        if np.any(depth <= 1e-6):
            continue
        width, height = float(kf["w"]), float(kf["h"])
        if np.any(u < 0) or np.any(u > width) or np.any(v < 0) or np.any(v > height):
            continue
        ray = centre - pose[:3, 3]
        ray = ray / np.linalg.norm(ray)
        incidence = math.degrees(math.acos(float(np.clip(abs(np.dot(ray, normal)), 0.0, 1.0))))
        if incidence < best_angle:
            best_angle = incidence
            u0, u1 = float(u.min()), float(u.max())
            v0, v1 = float(v.min()), float(v.max())
            cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
            du, dv = (u1 - u0) * 1.1 / 2, (v1 - v0) * 1.1 / 2
            best = {
                "image": kf.get("image") or kf["id"],
                "boxPx": [cu - du, cv - dv, cu + du, cv + dv],
            }
    return best


def yaw_deg(along: np.ndarray) -> float:
    dx, dz = float(along[0]), float(along[1])
    return math.degrees(math.atan2(-dz, dx))
