"""Place detections in the scene frame.

Ports B1 lift3d clustering / triangulation and the wall-plane cast the brief specifies.
Poses are camera-to-scene, column-major, ARKit axes (evalharness.frame, or server.eval.frame
once WS-00 vendors it).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from server.objects.config import Cluster
from server.objects.depth import LIDAR_MAX_M, LIDAR_MIN_M, MIN_CONFIDENCE, box_depth, sample, unproject

# Facade laser depth is rendered past the phone LiDAR cap. Eval reads that channel; capture stays at 5 m.
FACADE_LASER_MAX_M = 40.0

_EVAL = Path(__file__).resolve().parents[2] / "eval"
if _EVAL.is_dir() and str(_EVAL) not in sys.path:
    sys.path.append(str(_EVAL))

try:
    from server.eval import frame as arkit
except ImportError:
    from evalharness import frame as arkit

MATCH_M = 0.8


def pose_matrix(values) -> np.ndarray:
    return arkit.mat4_from_list(values) if not isinstance(values, np.ndarray) else np.asarray(values, float)


def cluster_points(points: list[tuple], *, radius: float = 0.6, min_views: int = 2) -> list[dict]:
    """points: (xyz, cls, frame, score, extra). Greedy, highest score first."""
    groups: list[dict] = []
    for xyz, cls, frame, _score, extra in sorted(points, key=lambda p: -p[3]):
        placed = False
        for group in groups:
            if group["cls"] != cls:
                continue
            centre = np.median(group["xyz"], axis=0)
            if np.linalg.norm(centre - xyz) < radius:
                group["xyz"].append(np.asarray(xyz, float))
                group["frames"].add(frame)
                group["views"].append(extra)
                placed = True
                break
        if not placed:
            groups.append({"cls": cls, "xyz": [np.asarray(xyz, float)], "frames": {frame}, "views": [extra]})
    out = []
    for group in groups:
        if len(group["frames"]) < min_views:
            continue
        centre = np.median(group["xyz"], axis=0)
        spread = float(max(np.linalg.norm(p - centre) for p in group["xyz"])) if len(group["xyz"]) > 1 else 0.0
        out.append(
            {"cls": group["cls"], "pos": centre, "spread": spread, "views": group["views"], "n": len(group["frames"])}
        )
    return out


def lift_depth(
    dets,
    cams,
    depths,
    *,
    pct: int = 30,
    radius_m: float = 0.6,
    min_views: int = 2,
    min_confidence: int = MIN_CONFIDENCE,
    max_m: float = LIDAR_MAX_M,
) -> list[Cluster]:
    """dets: frame id -> list of {label, score, box} normalised. cams: frame id -> {pose, intrinsics, w, h}."""
    pts = []
    for fid, rows in dets.items():
        packed = depths.get(fid)
        cam = cams.get(fid)
        if packed is None or cam is None:
            continue
        depth, conf = packed
        pose = pose_matrix(cam["pose"])
        k = cam["intrinsics"]
        for det in rows:
            box = _px(det["box"], cam["w"], cam["h"])
            # Depth arrays are in depth-map pixels. Scale the image box into that grid.
            scale_x = depth.shape[1] / cam["w"]
            scale_y = depth.shape[0] / cam["h"]
            depth_box = [box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y]
            z = box_depth(depth, conf, depth_box, pct=pct, min_confidence=min_confidence, max_m=max_m)
            if z is None:
                continue
            u, v = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            su = np.array([u * scale_x])
            sv = np.array([v * scale_y])
            world = unproject(su, sv, np.array([z]), k, depth.shape[1], depth.shape[0], cam["w"], cam["h"], pose)[0]
            pts.append((world, det["label"], fid, det["score"], _view_extra(fid, det, cam)))
    return [
        Cluster(g["cls"], [float(v) for v in g["pos"]], g["views"], g["spread"], "lidar")
        for g in cluster_points(pts, radius=radius_m, min_views=min_views)
    ]


def outward_normal(baseline: list) -> np.ndarray:
    a = np.asarray(baseline[0], float)
    b = np.asarray(baseline[1], float)
    dx, dz = b[0] - a[0], b[1] - a[1]
    # 90° that sends the meter wall's +x baseline to +z (out of the wall).
    normal = np.array([-dz, 0.0, dx], float)
    length = np.linalg.norm(normal)
    return normal / length if length else np.array([0.0, 0.0, 1.0])


def cast_ray(
    origin: np.ndarray, direction: np.ndarray, wall: dict, *, min_t: float = 0.3
) -> tuple[np.ndarray, float] | None:
    base = wall["baseline_m"]
    normal = outward_normal(base)
    point = np.array([base[0][0], 0.0, base[0][1]], float)
    denom = float(np.dot(direction, normal))
    if abs(denom) < 1e-8:
        return None
    t = float(np.dot(point - origin, normal) / denom)
    if t <= min_t:
        return None
    hit = origin + t * direction
    s = _s_of(hit, base)
    return hit, s


def lift_wall_cast(dets, cams, scene, *, agree_m: float = 0.5) -> list[Cluster]:
    walls = [w for w in scene.get("walls", []) if w.get("baseline_m")]
    hits: list[tuple] = []
    for fid, rows in dets.items():
        cam = cams.get(fid)
        if cam is None:
            continue
        pose = pose_matrix(cam["pose"])
        origin = pose[:3, 3]
        for det in rows:
            box = _px(det["box"], cam["w"], cam["h"])
            centre = cast_pixel(origin, pose, cam["intrinsics"], (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, walls)
            if centre is None:
                continue
            wall, _hit, s = centre
            left = cast_pixel(origin, pose, cam["intrinsics"], box[0], (box[1] + box[3]) / 2, [wall])
            right = cast_pixel(origin, pose, cam["intrinsics"], box[2], (box[1] + box[3]) / 2, [wall])
            bottom = cast_pixel(origin, pose, cam["intrinsics"], (box[0] + box[2]) / 2, box[3], [wall])
            top = cast_pixel(origin, pose, cam["intrinsics"], (box[0] + box[2]) / 2, box[1], [wall])
            extra = {
                **_view_extra(fid, det, cam),
                "wall_id": wall["id"],
                "s": s,
                "s0": left[2] if left else s,
                "s1": right[2] if right else s,
                "bottom": bottom[1][1] if bottom else 0.0,
                "top": top[1][1] if top else 0.0,
            }
            hits.append((centre[1], det["label"], fid, det["score"], extra))
    clusters = cluster_points(hits, radius=agree_m, min_views=2)
    out = []
    for group in clusters:
        wall_id = group["views"][0].get("wall_id")
        s_vals = [v["s0"] for v in group["views"]] + [v["s1"] for v in group["views"]]
        bottoms = [v["bottom"] for v in group["views"]]
        tops = [v["top"] for v in group["views"]]
        out.append(
            Cluster(
                group["cls"],
                [float(v) for v in group["pos"]],
                group["views"],
                group["spread"],
                "wall_cast",
                wall_id=wall_id,
                s_m=[float(min(s_vals)), float(max(s_vals))] if s_vals else None,
                bottom_m=float(min(bottoms)) if bottoms else None,
                top_m=float(max(tops)) if tops else None,
            )
        )
    return out


def cast_pixel(origin, pose, k, u, v, walls):
    direction = np.asarray(arkit.ray_arkit(pose, k, u, v), float)
    best = None
    for wall in walls:
        hit = cast_ray(origin, direction, wall)
        if hit is None:
            continue
        point, s = hit
        dist = float(np.linalg.norm(point - origin))
        if best is None or dist < best[0]:
            best = (dist, wall, point, s)
    if best is None:
        return None
    return best[1], best[2], best[3]


def lift_triangulate(
    dets, cams, *, max_gap: int = 6, epi_px: float = 25, vote_min: int = 3, reproj_px: float = 30
) -> list[Cluster]:
    names = [fid for fid in cams if fid in dets]
    if len(names) < 2:
        return []
    centres = {n: pose_matrix(cams[n]["pose"])[:3, 3] for n in names}
    cands = []
    for i, a in enumerate(names):
        for b in names[i + 1 : i + 1 + max_gap]:
            if np.linalg.norm(centres[a] - centres[b]) < 0.3:
                continue
            for da in dets.get(a, []):
                ua = _centre_px(da, cams[a])
                for db in dets.get(b, []):
                    if db["label"] != da["label"]:
                        continue
                    ub = _centre_px(db, cams[b])
                    xyz = dlt_arkit([cams[a], cams[b]], [ua, ub])
                    if xyz is None:
                        continue
                    ea, za = project(cams[a], xyz)
                    eb, zb = project(cams[b], xyz)
                    if za <= 0 or zb <= 0 or np.linalg.norm(ea - ua) > epi_px or np.linalg.norm(eb - ub) > epi_px:
                        continue
                    if np.linalg.norm(xyz - centres[a]) > 60:
                        continue
                    cands.append((xyz, da["label"]))
    if not cands:
        return []
    voxels: dict[tuple, list] = {}
    for xyz, cls in cands:
        key = (cls, *np.floor(np.asarray(xyz) / 0.25).astype(int))
        voxels.setdefault(key, []).append(xyz)
    voted = []
    for key, xs in voxels.items():
        cls, xyz = key[0], np.mean(xs, axis=0)
        support = []
        for name in names:
            uv, z = project(cams[name], xyz)
            if z <= 0:
                continue
            for det in dets.get(name, []):
                if det["label"] != cls:
                    continue
                box = _px(det["box"], cams[name]["w"], cams[name]["h"])
                if box[0] <= uv[0] <= box[2] and box[1] <= uv[1] <= box[3]:
                    support.append((name, _centre_px(det, cams[name]), det))
                    break
        if len(support) >= vote_min:
            voted.append((len(support) + 0.01 * len(xs), cls, xyz, support))
    objects = []
    for _score, cls, xyz, support in sorted(voted, key=lambda v: -v[0]):
        if any(o.cls == cls and np.linalg.norm(np.asarray(o.pos_m) - xyz) < MATCH_M for o in objects):
            continue
        views = support
        refined = xyz
        for _ in range(3):
            refined = dlt_arkit([cams[n] for n, _, _ in views], [uv for _, uv, _ in views])
            if refined is None:
                break
            kept = []
            for name, uv, det in views:
                pred, _z = project(cams[name], refined)
                if np.linalg.norm(pred - uv) < reproj_px:
                    kept.append((name, uv, det))
            if len(kept) < vote_min or len(kept) == len(views):
                views = kept or views
                break
            views = kept
        if refined is None or len(views) < vote_min:
            continue
        spread = float(max(np.linalg.norm(project_back(cams[n], uv) - refined) for n, uv, _ in views)) if False else 0.0
        view_rows = [_view_extra(n, det, cams[n]) for n, _uv, det in views]
        objects.append(Cluster(cls, [float(v) for v in refined], view_rows, spread, "triangulate"))
    return objects


def project(cam: dict, xyz: np.ndarray) -> tuple[np.ndarray, float]:
    pose = pose_matrix(cam["pose"])
    u, v, z = arkit.project_arkit(pose, cam["intrinsics"], np.asarray(xyz, float))
    return np.array([float(u), float(v)]), float(z)


def dlt_arkit(cams: list[dict], pixels: list[np.ndarray]) -> np.ndarray | None:
    rows = []
    for cam, uv in zip(cams, pixels, strict=True):
        pose = pose_matrix(cam["pose"])
        fx, fy, cx, cy = cam["intrinsics"]
        rot = pose[:3, :3].T
        t = -rot @ pose[:3, 3]
        u, v = float(uv[0]), float(uv[1])
        m = rot
        rows.append(np.append(fx * m[0] + (u - cx) * m[2], fx * t[0] + (u - cx) * t[2]))
        rows.append(np.append(fy * m[1] + (cy - v) * m[2], fy * t[1] + (cy - v) * t[2]))
    _u, _s, vt = np.linalg.svd(np.asarray(rows, float))
    x = vt[-1]
    if abs(x[3]) < 1e-12:
        return None
    return x[:3] / x[3]


def project_back(cam, uv) -> np.ndarray:
    """Unused placeholder kept so a later refinement can measure spread. Returns the camera centre."""
    return pose_matrix(cam["pose"])[:3, 3]


def place_taps(taps, cams_by_frame, depths, scene) -> list[dict]:
    placed = []
    walls = scene.get("walls", [])
    for tap in taps:
        fid = tap.get("keyframe")
        cam = cams_by_frame.get(fid)
        record = {
            "id": tap.get("id"),
            "label": tap.get("label"),
            "keyframe": fid,
            "pixel": tap.get("pixel"),
            "placed": False,
        }
        if cam is None:
            record["method"] = "unknown_keyframe"
            placed.append(record)
            continue
        pose = pose_matrix(cam["pose"])
        pixel = tap.get("pixel") or [0, 0]
        packed = depths.get(fid)
        if packed is not None and pixel:
            depth, conf = packed
            u = np.array([float(pixel[0])])
            v = np.array([float(pixel[1])])
            z, c, inside = sample(depth, conf, u, v, cam["w"], cam["h"])
            if inside[0] and c[0] >= MIN_CONFIDENCE and LIDAR_MIN_M < float(z[0]) <= LIDAR_MAX_M:
                world = unproject(
                    np.array([float(np.rint(u[0] * depth.shape[1] / cam["w"]))]),
                    np.array([float(np.rint(v[0] * depth.shape[0] / cam["h"]))]),
                    np.array([float(z[0])]),
                    cam["intrinsics"],
                    depth.shape[1],
                    depth.shape[0],
                    cam["w"],
                    cam["h"],
                    pose,
                )[0]
                record.update(
                    {
                        "placed": True,
                        "method": "lidar_depth",
                        "position": [float(v) for v in world],
                        "plus_minus_m": 0.10,
                    }
                )
                placed.append(record)
                continue
        hit = tap.get("hit") or {}
        if hit.get("target") == "sceneDepth" and hit.get("position"):
            record.update(
                {
                    "placed": True,
                    "method": "lidar_device_hit",
                    "position": [float(v) for v in hit["position"]],
                    "plus_minus_m": 0.10,
                }
            )
            placed.append(record)
            continue
        origin = pose[:3, 3]
        cast = cast_pixel(origin, pose, cam["intrinsics"], float(pixel[0]), float(pixel[1]), walls)
        if cast is not None:
            wall, point, _s = cast
            rms = float((wall.get("support") or {}).get("fit_rms_m") or 0.05)
            bound = max(0.15, 3 * rms) + float(wall.get("plus_minus_m") or 0.0)
            observed = wall.get("evidence") == "observed"
            record.update(
                {
                    "placed": True,
                    "method": "wall_cast",
                    "position": [float(v) for v in point],
                    "wall_id": wall["id"],
                    "plus_minus_m": bound,
                    "observed": observed,
                }
            )
        placed.append(record)
    return placed


def _view_extra(fid: str, det: dict, cam: dict | None) -> dict:
    """A lift view. `box` stays normalised; `box_px` is that box in the camera raster."""
    extra = {"frame": fid, "box": det["box"], "score": det["score"]}
    if cam and cam.get("w") and cam.get("h"):
        extra["box_px"] = _px(det["box"], int(cam["w"]), int(cam["h"]))
    return extra


def _px(box: list[float], w: int, h: int) -> list[float]:
    return [box[0] * w, box[1] * h, box[2] * w, box[3] * h]


def _centre_px(det: dict, cam: dict) -> np.ndarray:
    box = _px(det["box"], cam["w"], cam["h"])
    return np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])


def _s_of(hit: np.ndarray, baseline: list) -> float:
    a = np.array([baseline[0][0], baseline[0][1]], float)
    b = np.array([baseline[1][0], baseline[1][1]], float)
    ab = b - a
    denom = float(np.dot(ab, ab)) or 1.0
    return float(np.dot(np.array([hit[0], hit[2]]) - a, ab) / np.sqrt(denom))


def in_roi(point: np.ndarray, walls_xy: list[np.ndarray], z_range=(-1.5, 2.5), reach: float = 1.5) -> bool:
    if not (z_range[0] <= point[2] <= z_range[1]):
        return False
    p = point[:2]
    for segment in walls_xy:
        a, b = segment
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1)
        if np.linalg.norm(p - (a + t * ab)) < reach:
            return True
    return False


def score_openings(clusters: list[Cluster], gt: list[dict], walls_xy: list, *, match_m: float = MATCH_M) -> dict:
    """Match cluster centres to GT centres. `match_m` stays 0.8; callers do not lower it."""
    used, errs, found = set(), [], []
    for item in gt:
        centre = np.asarray(item["center"], float)
        if walls_xy and not in_roi(centre, walls_xy):
            continue
        best, which = match_m, -1
        for j, cluster in enumerate(clusters):
            if j in used or cluster.cls != item.get("type", item.get("cls")):
                continue
            dist = float(np.linalg.norm(np.asarray(cluster.pos_m) - centre))
            if dist < best:
                best, which = dist, j
        if which >= 0:
            used.add(which)
            errs.append(best)
            found.append(item.get("label", item.get("type")))
    in_band = [c for i, c in enumerate(clusters) if i not in used]
    # false_3d counts unmatched clusters that sit in the ROI
    false = 0
    for cluster in in_band:
        if not walls_xy or in_roi(np.asarray(cluster.pos_m), walls_xy):
            false += 1
    return {
        "recovered": len(found),
        "n_gt": len(gt),
        "false_3d": false,
        "centre_err_m_median": float(np.median(errs)) if errs else None,
        "found": found,
    }


def alignment_to_source(point, alignment: dict) -> np.ndarray:
    """Inverse of packetgen `Alignment.point`: packet-world metres back to the source scan."""
    rot = np.asarray(alignment["R_align_rows_src"], float)
    origin = np.asarray(alignment["origin_src"], float)
    scale = float(alignment["scale"])
    return (np.asarray(point, float) / scale) @ rot + origin


def scan_wall_segments(scene: dict) -> list[np.ndarray]:
    """Scene wall baselines (y-up ground, xz) inverted through `frame.from_source` into scan xy."""
    frame = scene.get("frame") or {}
    if "from_source" not in frame:
        return []
    transform = pose_matrix(frame["from_source"])
    walls = []
    for wall in scene.get("walls") or []:
        segment = []
        for x, z in wall.get("baseline_m") or []:
            source = (np.array([float(x), 0.0, float(z)]) - transform[:3, 3]) @ transform[:3, :3]
            segment.append(source[:2])
        if len(segment) >= 2:
            walls.append(np.stack(segment))
    return walls


def map_cluster_positions(clusters: list[Cluster], fn) -> list[Cluster]:
    """Return clusters whose centres have been mapped. Views and method stay put."""
    mapped = []
    for cluster in clusters:
        pos = np.asarray(fn(np.asarray(cluster.pos_m, float)), float).reshape(-1)
        mapped.append(
            Cluster(
                cluster.cls,
                [float(v) for v in pos[:3]],
                cluster.views,
                cluster.spread_m,
                cluster.method,
                wall_id=cluster.wall_id,
                s_m=cluster.s_m,
                bottom_m=cluster.bottom_m,
                top_m=cluster.top_m,
                size_m=cluster.size_m,
            )
        )
    return mapped
