# Vendored from internal/modal-pipeline/housepipe/lidar.py (store, 2026-09-26).
# align_lidar is the pure scale fit added for the scale stage; fuse, far_points,
# coverage_depths and place_taps stay as vendored.
"""LiDAR sceneDepth branch.

When a keyframe has both a depth map and a confidence map, that frame is
metric out to about 5 m. The image branch (π³, predicted depth, unplaced
taps) still runs for everything the sensor does not cover.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from server.common.geometry import column_major

LIDAR_MAX_M = 5.0
LIDAR_MIN_M = 0.2
MIN_CONFIDENCE = 1  # 0 low, 1 medium, 2 high. Medium still falls inside 5 m.


def frame_depth_spec(keyframe: dict, root: Path) -> dict | None:
    depth = keyframe.get("depth") or None
    if not depth or not depth.get("file") or not depth.get("confidenceFile"):
        return None
    return {
        "file": root / depth["file"],
        "confidence": root / depth["confidenceFile"],
        "w": int(depth["w"]),
        "h": int(depth["h"]),
    }


def has_lidar(frames: list[dict]) -> bool:
    for frame in frames:
        spec = frame.get("depth")
        if not spec:
            continue
        if Path(spec["file"]).is_file() and Path(spec["confidence"]).is_file():
            return True
    return False


def read_depth(spec: dict) -> tuple[np.ndarray, np.ndarray] | None:
    w, h = int(spec["w"]), int(spec["h"])
    depth_path, conf_path = Path(spec["file"]), Path(spec["confidence"])
    if not depth_path.is_file() or not conf_path.is_file():
        return None
    if depth_path.stat().st_size != w * h * 4 or conf_path.stat().st_size != w * h:
        return None
    depth = np.fromfile(depth_path, dtype="<f4", count=w * h).reshape(h, w)
    conf = np.fromfile(conf_path, dtype=np.uint8, count=w * h).reshape(h, w)
    return depth, conf


def load_depths(frames: list[dict]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Image name -> (axial depth meters, confidence)."""
    out = {}
    for frame in frames:
        spec = frame.get("depth")
        if not spec:
            continue
        pair = read_depth(spec)
        if pair is not None:
            out[frame["name"]] = pair
    return out


def _sample(depth: np.ndarray, conf: np.ndarray, u: np.ndarray, v: np.ndarray, w: int, h: int):
    dh, dw = depth.shape
    su = np.rint(u * dw / w).astype(int)
    sv = np.rint(v * dh / h).astype(int)
    inside = (su >= 0) & (su < dw) & (sv >= 0) & (sv < dh)
    su = np.clip(su, 0, dw - 1)
    sv = np.clip(sv, 0, dh - 1)
    return depth[sv, su], conf[sv, su], inside


# A 3x3 whose positive depths span more than this fraction of the center is an edge.
# Packet depth splats read short there; flat pixels stay within about 0.5% of truth.
_FLAT_REL_SPAN = 0.02
_FLAT_MIN_SAMPLES = 5


def _locally_flat(depth: np.ndarray, u: np.ndarray, v: np.ndarray, w: int, h: int) -> np.ndarray:
    """True where the depth pixel is interior and its 3x3 neighborhood is nearly constant.

    Non-positive and non-finite neighbors are ignored, so a sparse synthetic depth
    map still accepts a flat interior. A short center beside a longer neighbor does not.
    """

    dh, dw = depth.shape
    u = np.asarray(u, float)
    v = np.asarray(v, float)
    finite = np.isfinite(u) & np.isfinite(v)
    su = np.rint(np.where(finite, u, 0.0) * dw / max(int(w), 1)).astype(int)
    sv = np.rint(np.where(finite, v, 0.0) * dh / max(int(h), 1)).astype(int)
    interior = finite & (su >= 1) & (su < dw - 1) & (sv >= 1) & (sv < dh - 1)
    flat = np.zeros(su.shape, dtype=bool)
    if not np.any(interior):
        return flat
    su_i = su[interior]
    sv_i = sv[interior]
    window = np.stack([depth[sv_i + dy, su_i + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
    valid = np.isfinite(window) & (window > 0)
    count = valid.sum(axis=0)
    keep = count >= _FLAT_MIN_SAMPLES
    accepted = np.zeros(count.shape, dtype=bool)
    if np.any(keep):
        kept = np.where(valid[:, keep], window[:, keep], np.nan)
        with np.errstate(invalid="ignore"):
            span = np.nanmax(kept, axis=0) - np.nanmin(kept, axis=0)
        center = depth[sv_i[keep], su_i[keep]]
        accepted[keep] = np.isfinite(center) & (center > 0) & np.isfinite(span) & (span < _FLAT_REL_SPAN * center)
    flat[interior] = accepted
    return flat


def align_scale(
    points: np.ndarray,
    cameras: list[dict],
    depths: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    min_pairs: int = 50,
) -> dict:
    """Robust ratio of high-confidence LiDAR depth to SfM depth, within 5 m.

    Applies one similarity scale about the camera centroid so the refined
    model matches the sensor. Rotations stay put.
    """
    report = {"applied": False, "pairs": 0, "scale": 1.0, "rel_bound_95": None}
    if len(points) == 0 or not cameras or not depths:
        report["reason"] = "nothing_to_align"
        return report
    xyz = np.asarray(points, float)
    if len(xyz) > 4000:
        xyz = xyz[np.random.default_rng(0).choice(len(xyz), 4000, replace=False)]
    ratios = []
    for cam in cameras:
        packed = depths.get(cam["name"])
        if packed is None:
            continue
        depth, conf = packed
        T = np.asarray(cam["c2w"], float)
        K = cam["K_full"]
        fx, fy, cx, cy = K
        R, c = T[:3, :3], T[:3, 3]
        Xc = (xyz - c) @ R
        z = -Xc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = fx * Xc[:, 0] / z + cx
            v = -fy * Xc[:, 1] / z + cy
        z_l, conf_l, inside = _sample(depth, conf, u, v, cam["w"], cam["h"])
        ok = (
            inside
            & np.isfinite(z)
            & (z > LIDAR_MIN_M)
            & (z < 40)
            & (conf_l >= MIN_CONFIDENCE)
            & np.isfinite(z_l)
            & (z_l > LIDAR_MIN_M)
            & (z_l <= LIDAR_MAX_M)
        )
        if ok.any():
            ratios.append(z_l[ok] / z[ok])
    if not ratios:
        report["reason"] = "no_pairs"
        return report
    ratio = np.concatenate(ratios)
    ratio = ratio[np.isfinite(ratio) & (ratio > 0.5) & (ratio < 2.0)]
    report["pairs"] = int(len(ratio))
    if len(ratio) < min_pairs:
        report["reason"] = "too_few_pairs"
        return report
    scale = float(np.median(ratio))
    rel = np.abs(ratio / scale - 1.0)
    report["scale"] = scale
    report["rel_bound_95"] = round(float(np.quantile(rel, 0.95)), 4)
    origin = np.mean([np.asarray(cam["c2w"], float)[:3, 3] for cam in cameras], axis=0)
    _scale_about(points, cameras, scale, origin)
    report["applied"] = True
    report["origin"] = [round(float(v), 4) for v in origin]
    return report


def _scale_about(points: np.ndarray, cameras: list[dict], scale: float, origin: np.ndarray) -> None:
    if abs(scale - 1.0) < 1e-6:
        return
    points[:] = origin + scale * (np.asarray(points, float) - origin)
    for cam in cameras:
        T = np.asarray(cam["c2w"], float).copy()
        T[:3, 3] = origin + scale * (T[:3, 3] - origin)
        cam["c2w"] = T
        cam["center"] = T[:3, 3].tolist()
        cam["pose"] = column_major(T)


def scale_pose(T: np.ndarray, scale: float, origin: np.ndarray) -> np.ndarray:
    out = np.array(T, float, copy=True)
    if origin is None or abs(scale - 1.0) < 1e-6:
        return out
    out[:3, 3] = origin + scale * (out[:3, 3] - np.asarray(origin, float))
    return out


def _unproject(
    uu: np.ndarray, vv: np.ndarray, z: np.ndarray, K, dw: int, dh: int, img_w: int, img_h: int, T: np.ndarray
) -> np.ndarray:
    sx = dw / float(img_w)
    sy = dh / float(img_h)
    fx, fy, cx, cy = K[0] * sx, K[1] * sy, K[2] * sx, K[3] * sy
    x = (uu - cx) / fx * z
    y = -(vv - cy) / fy * z
    cam = np.stack([x, y, -z], axis=1)
    R = np.asarray(T, float)[:3, :3]
    c = np.asarray(T, float)[:3, 3]
    return cam @ R.T + c


def fuse(
    cameras: list[dict],
    depths: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    stride: int = 4,
    cap: int = 250_000,
) -> tuple[np.ndarray, dict]:
    """High-confidence LiDAR on the refined poses, kept to 5 m."""
    clouds = []
    used = 0
    for cam in cameras:
        packed = depths.get(cam["name"])
        if packed is None:
            continue
        depth, conf = packed
        dh, dw = depth.shape
        ys = np.arange(0, dh, stride)
        xs = np.arange(0, dw, stride)
        uu, vv = np.meshgrid(xs, ys)
        z = depth[vv, uu]
        c = conf[vv, uu]
        ok = (c >= MIN_CONFIDENCE) & np.isfinite(z) & (z > LIDAR_MIN_M) & (z <= LIDAR_MAX_M)
        if not np.any(ok):
            continue
        world = _unproject(
            uu[ok].astype(float),
            vv[ok].astype(float),
            z[ok].astype(float),
            cam["K_full"],
            dw,
            dh,
            cam["w"],
            cam["h"],
            cam["c2w"],
        )
        clouds.append(world.astype(np.float32))
        used += 1
    if not clouds:
        return np.zeros((0, 3), np.float32), {"frames": 0, "points": 0, "max_m": LIDAR_MAX_M}
    xyz = np.concatenate(clouds, axis=0)
    raw = int(len(xyz))
    xyz = _voxel(xyz, 0.02, cap)
    return xyz, {"frames": used, "points_raw": raw, "points": int(len(xyz)), "max_m": LIDAR_MAX_M, "stride": stride}


def far_points(xyz: np.ndarray, cameras: list[dict], radius: float = LIDAR_MAX_M) -> np.ndarray:
    """π³ points the LiDAR sensor does not reach."""
    if len(xyz) == 0 or not cameras:
        return xyz
    centers = np.stack([np.asarray(cam["c2w"], float)[:3, 3] for cam in cameras])
    mins = np.full(len(xyz), np.inf)
    for c in centers:
        mins = np.minimum(mins, np.linalg.norm(xyz - c, axis=1))
    return xyz[mins > radius]


def coverage_depths(depths: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Axial depth plus a mask of medium-or-better confidence, for the occlusion test."""
    out = {}
    for name, (depth, conf) in depths.items():
        mask = (conf >= MIN_CONFIDENCE) & np.isfinite(depth) & (depth > LIDAR_MIN_M)
        out[name] = (depth.astype(np.float32), mask)
    return out


def place_taps(
    taps: list[dict],
    frames_by_id: dict[str, dict],
    cameras_by_id: dict[str, dict],
    depths: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    scale: float,
    origin: np.ndarray | None,
) -> list[dict]:
    """One 3D position per tap. LiDAR depth wins; otherwise the tap stays unplaced."""
    placed = []
    for tap in taps:
        frame = frames_by_id.get(tap.get("keyframe"))
        record = {
            "id": tap.get("id"),
            "label": tap.get("label"),
            "keyframe": tap.get("keyframe"),
            "pixel": tap.get("pixel"),
            "placed": False,
            "method": "image_only",
            "location": "unsure",
        }
        if frame is None:
            record["method"] = "unknown_keyframe"
            placed.append(record)
            continue
        cam = cameras_by_id.get(frame["id"])
        if cam is not None:
            T = np.asarray(cam["c2w"], float)
            pose_source = "refined_sfm"
        else:
            T = scale_pose(frame["T_ar"], scale, origin)
            pose_source = "arkit"
        depth_hit = _tap_depth(frame, T, tap.get("pixel"), depths)
        if depth_hit is not None:
            record.update(depth_hit)
            record["pose_source"] = pose_source
            placed.append(record)
            continue
        hit = tap.get("hit") or {}
        if hit.get("target") == "sceneDepth" and hit.get("position"):
            pos = np.asarray(hit["position"], float)
            if origin is not None:
                pos = np.asarray(origin, float) + scale * (pos - np.asarray(origin, float))
            record.update(
                {
                    "placed": True,
                    "method": "lidar_device_hit",
                    "location": "lidar",
                    "position": [round(float(v), 4) for v in pos],
                    "pose_source": "device",
                }
            )
        placed.append(record)
    return placed


def _tap_depth(frame: dict, T: np.ndarray, pixel, depths: dict) -> dict | None:
    if not pixel or frame["name"] not in depths:
        return None
    depth, conf = depths[frame["name"]]
    u = np.array([float(pixel[0])])
    v = np.array([float(pixel[1])])
    z, c, inside = _sample(depth, conf, u, v, frame["w"], frame["h"])
    if not (inside[0] and c[0] >= MIN_CONFIDENCE and LIDAR_MIN_M < float(z[0]) <= LIDAR_MAX_M):
        return None
    dh, dw = depth.shape
    su = float(np.rint(u[0] * dw / frame["w"]))
    sv = float(np.rint(v[0] * dh / frame["h"]))
    world = _unproject(
        np.array([su]), np.array([sv]), np.array([float(z[0])]), frame["K"], dw, dh, frame["w"], frame["h"], T
    )
    return {
        "placed": True,
        "method": "lidar_depth",
        "location": "lidar",
        "position": [round(float(v), 4) for v in world[0]],
        "depth_m": round(float(z[0]), 4),
        "confidence": int(c[0]),
    }


def _voxel(xyz: np.ndarray, voxel: float, cap: int) -> np.ndarray:
    if len(xyz) == 0:
        return xyz
    if len(xyz) > cap * 4:
        xyz = xyz[np.random.default_rng(0).choice(len(xyz), cap * 4, replace=False)]
    q = np.round(xyz / voxel).astype(np.int32)
    _, idx = np.unique(q, axis=0, return_index=True)
    xyz = xyz[np.sort(idx)]
    if len(xyz) > cap:
        xyz = xyz[np.random.default_rng(0).choice(len(xyz), cap, replace=False)]
    return xyz.astype(np.float32)


@dataclass
class LidarAlignment:
    applied: bool
    scale: float
    rel_sd: float
    rel_bound_95: float
    pairs: int
    frames: int
    min_conf_used: int
    pair_spread_p95: float
    origin: np.ndarray | None
    reason: str | None


def align_lidar(
    points: np.ndarray,
    cameras: list,
    depths: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    min_conf: int,
    min_pairs: int,
    max_m: float,
    seed: int = 0,
    verified_pairs: int = 200,
    min_m: float = 0.2,
) -> LidarAlignment:
    """Pure LiDAR/SfM scale. Does not mutate ``points`` or ``cameras``.

    First pass uses ``min_conf``. When the pair count is below ``verified_pairs``
    and ``min_conf`` is above 1, a second pass uses confidence 1.
    """

    def once(conf_floor: int) -> tuple[list[np.ndarray], list[int]]:
        xyz = np.asarray(points, float)
        if len(xyz) > 4000:
            xyz = xyz[np.random.default_rng(seed).choice(len(xyz), 4000, replace=False)]
        grouped: list[np.ndarray] = []
        counts: list[int] = []
        for cam in cameras:
            packed = depths.get(cam.image)
            if packed is None:
                continue
            depth, conf = packed
            pose = np.asarray(cam.pose, float)
            fx, fy, cx, cy = (float(v) for v in cam.intrinsics)
            rotation, center = pose[:3, :3], pose[:3, 3]
            local = (xyz - center) @ rotation
            z = -local[:, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                u = fx * local[:, 0] / z + cx
                v = -fy * local[:, 1] / z + cy
            z_lidar, conf_lidar, inside = _sample(depth, conf, u, v, int(cam.w), int(cam.h))
            flat = _locally_flat(depth, u, v, int(cam.w), int(cam.h))
            ok = (
                inside
                & flat
                & np.isfinite(z)
                & (z > min_m)
                & (z < 40)
                & (conf_lidar >= conf_floor)
                & np.isfinite(z_lidar)
                & (z_lidar > min_m)
                & (z_lidar <= max_m)
            )
            if not np.any(ok):
                continue
            ratio = z_lidar[ok] / z[ok]
            ratio = ratio[np.isfinite(ratio) & (ratio > 0.5) & (ratio < 2.0)]
            if len(ratio) == 0:
                continue
            grouped.append(ratio)
            counts.append(int(len(ratio)))
        return grouped, counts

    blank = LidarAlignment(False, 1.0, 0.0, 0.0, 0, 0, int(min_conf), 0.0, None, "lidar_too_few_pairs")
    if len(np.asarray(points)) == 0 or not cameras or not depths:
        return blank
    grouped, counts = once(int(min_conf))
    used_conf = int(min_conf)
    if sum(counts) < verified_pairs and int(min_conf) > 1:
        grouped, counts = once(1)
        used_conf = 1
    n_pairs = int(sum(counts))
    if n_pairs < min_pairs or not grouped:
        return LidarAlignment(False, 1.0, 0.0, 0.0, n_pairs, len(counts), used_conf, 0.0, None, "lidar_too_few_pairs")
    ratio = np.concatenate(grouped)
    scale = float(np.median(ratio))
    log_r = np.log(ratio)
    mad = float(np.median(np.abs(log_r - np.median(log_r))))
    n_eff = max(1, sum(1 for n in counts if n >= 20))
    rel_sd = max(0.005, 1.4826 * mad / np.sqrt(n_eff))
    rel_bound = float(1.1 * 1.96 * rel_sd)
    spread = float(np.quantile(np.abs(ratio / scale - 1.0), 0.95))
    origin = np.mean([np.asarray(cam.pose, float)[:3, 3] for cam in cameras], axis=0)
    return LidarAlignment(
        True, scale, float(rel_sd), rel_bound, int(len(ratio)), len(counts), used_conf, spread, origin, None
    )
