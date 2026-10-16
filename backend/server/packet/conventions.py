"""Device and camera frames ported from packetgen.conventions.

Column-major poses, ARKit rays, and COLMAP quaternions come from `server.common.geometry`.
`project_arkit` is not in that module, so it stays here.
"""

from __future__ import annotations

import numpy as np

from server.common.geometry import column_major, from_column_major, pixel_ray_arkit, qvec_to_rotmat

__all__ = [
    "CM_FROM_ARKIT_WORLD",
    "DEVICE_FROM_CAMERA",
    "column_major",
    "from_column_major",
    "orthonormalize",
    "pixel_ray_arkit",
    "project_arkit",
    "qvec_to_rotmat",
    "rotmat_to_quat_xyzw",
    "rotation_angle_deg",
    "tap_replay_error",
    "world_from_device",
]

DEVICE_FROM_CAMERA = np.array(
    [
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)

CM_FROM_ARKIT_WORLD = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ]
)


def rotmat_to_quat_xyzw(R: np.ndarray) -> np.ndarray:
    """Rotation matrix to (x, y, z, w)."""
    m = np.asarray(R, dtype=float)
    tr = float(np.trace(m))
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    quat = np.array([x, y, z, w], dtype=float)
    quat = quat / np.linalg.norm(quat)
    return quat if quat[3] >= 0 else -quat


def world_from_device(T_ar: np.ndarray) -> np.ndarray:
    """Rotation taking Core Motion device-frame vectors into ARKit world."""
    return T_ar[:3, :3] @ DEVICE_FROM_CAMERA.T


def tap_replay_error(frame: dict, tap: dict) -> float:
    """max(|Δorigin|∞, |Δdirection|∞) between the stored tap ray and a fresh replay."""
    pixel = tap.get("pixel") or [0.0, 0.0]
    matrix = from_column_major(frame["pose"])
    origin, direction = pixel_ray_arkit(matrix, tuple(frame["intrinsics"]), np.asarray(pixel, dtype=float))
    delta_origin = np.abs(origin - np.asarray(tap["rayOrigin"], dtype=float))
    delta_direction = np.abs(direction - np.asarray(tap["rayDirection"], dtype=float))
    if not np.all(np.isfinite(delta_origin)) or not np.all(np.isfinite(delta_direction)):
        return float("nan")
    return max(float(np.max(delta_origin)), float(np.max(delta_direction)))


def project_arkit(T_ar: np.ndarray, K: tuple[float, float, float, float], X: np.ndarray):
    fx, fy, cx, cy = K
    rotation = T_ar[:3, :3]
    center = T_ar[:3, 3]
    camera = (np.asarray(X, dtype=float) - center) @ rotation
    depth = -camera[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = fx * camera[:, 0] / depth + cx
        v = -fy * camera[:, 1] / depth + cy
    return np.stack([u, v], axis=1), depth


def orthonormalize(rotation: np.ndarray) -> np.ndarray:
    """Closest rotation matrix (det +1) in Frobenius norm."""
    u, _, vt = np.linalg.svd(np.asarray(rotation, dtype=float))
    out = u @ vt
    if np.linalg.det(out) < 0:
        u = u.copy()
        u[:, -1] *= -1
        out = u @ vt
    return out


def rotation_angle_deg(r1: np.ndarray, r2: np.ndarray) -> float:
    relative = r1.T @ r2
    cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))
