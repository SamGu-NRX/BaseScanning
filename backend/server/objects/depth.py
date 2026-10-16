"""LiDAR depth maps from the capture packet.

Copied from housepipe.lidar (read_depth, _sample, _unproject, place_taps helpers).
Stage packages do not import server.scale; a later WS-00 change can share this.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

LIDAR_MAX_M = 5.0
LIDAR_MIN_M = 0.2
MIN_CONFIDENCE = 1


def load_lidar(frame) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (depth metres, confidence) for a FrameRef that carries depth files."""
    spec = getattr(frame, "depth", None)
    if spec is None:
        return None
    return read_depth(spec)


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


def sample(depth: np.ndarray, conf: np.ndarray, u: np.ndarray, v: np.ndarray, w: int, h: int):
    dh, dw = depth.shape
    su = np.rint(np.asarray(u) * dw / w).astype(int)
    sv = np.rint(np.asarray(v) * dh / h).astype(int)
    inside = (su >= 0) & (su < dw) & (sv >= 0) & (sv < dh)
    su = np.clip(su, 0, dw - 1)
    sv = np.clip(sv, 0, dh - 1)
    return depth[sv, su], conf[sv, su], inside


def unproject(uu, vv, z, k, dw: int, dh: int, img_w: int, img_h: int, pose: np.ndarray) -> np.ndarray:
    """Back-project depth pixels. `k` is (fx, fy, cx, cy) at image resolution; pose is camera-to-scene."""
    sx = dw / float(img_w)
    sy = dh / float(img_h)
    fx, fy, cx, cy = k[0] * sx, k[1] * sy, k[2] * sx, k[3] * sy
    x = (uu - cx) / fx * z
    y = -(vv - cy) / fy * z
    cam = np.stack([x, y, -z], axis=-1)
    rot = np.asarray(pose, float)[:3, :3]
    center = np.asarray(pose, float)[:3, 3]
    return cam @ rot.T + center


def box_depth(
    depth: np.ndarray,
    conf: np.ndarray,
    box_px: list[float],
    *,
    pct: int = 30,
    min_confidence: int = MIN_CONFIDENCE,
    max_m: float = LIDAR_MAX_M,
) -> float | None:
    """30th percentile of samples in the box's central half, in (0.2, max_m] m."""
    u0, v0, u1, v1 = box_px
    cu0 = u0 + 0.25 * (u1 - u0)
    cu1 = u1 - 0.25 * (u1 - u0)
    cv0 = v0 + 0.25 * (v1 - v0)
    cv1 = v1 - 0.25 * (v1 - v0)
    dh, dw = depth.shape
    x0, x1 = int(np.floor(cu0)), int(np.ceil(cu1))
    y0, y1 = int(np.floor(cv0)), int(np.ceil(cv1))
    x0, x1 = max(0, x0), min(dw, x1)
    y0, y1 = max(0, y0), min(dh, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    patch_z = depth[y0:y1, x0:x1]
    patch_c = conf[y0:y1, x0:x1]
    ok = (patch_c >= min_confidence) & np.isfinite(patch_z) & (patch_z > LIDAR_MIN_M) & (patch_z <= max_m)
    vals = patch_z[ok]
    if vals.size < 5:
        return None
    return float(np.percentile(vals, pct))
