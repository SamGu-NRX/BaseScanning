# vendored from internal/modal-pipeline/housepipe/geometry.py (store, 2026-09-26)
"""Pose conversions and alignment. ARKit conventions match packetgen.conventions."""

from __future__ import annotations

import numpy as np

FLIP_YZ = np.diag([1.0, -1.0, -1.0])


def from_column_major(values) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(4, 4).T


def column_major(T: np.ndarray) -> list[float]:
    return [float(v) for v in np.asarray(T, dtype=float).T.reshape(-1)]


def pixel_ray_arkit(T_ar: np.ndarray, K, uv: np.ndarray):
    fx, fy, cx, cy = K
    d_cam = np.array([(uv[0] - cx) / fx, -(uv[1] - cy) / fy, -1.0])
    d = T_ar[:3, :3] @ d_cam
    n = np.linalg.norm(d)
    return T_ar[:3, 3].copy(), d / n if n > 0 else d


def arkit_center(T_ar: np.ndarray) -> np.ndarray:
    return np.asarray(T_ar[:3, 3], dtype=float).copy()


def arkit_to_colmap_pose(T_ar: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ARKit camera-to-world -> OpenCV/COLMAP world-to-camera (R_cw, t_cw).

    OpenCV camera-to-world is ``T_ar @ diag(1, -1, -1, 1)`` (capture spec).
    """
    T_cv = np.eye(4)
    T_cv[:3, :3] = T_ar[:3, :3] @ FLIP_YZ
    T_cv[:3, 3] = T_ar[:3, 3]
    R_cw = T_cv[:3, :3].T
    t_cw = -R_cw @ T_cv[:3, 3]
    return R_cw, t_cw


def colmap_to_arkit_pose(R_cw: np.ndarray, t_cw: np.ndarray) -> np.ndarray:
    T_cv = np.eye(4)
    T_cv[:3, :3] = R_cw.T
    T_cv[:3, 3] = -R_cw.T @ t_cw
    T_ar = np.eye(4)
    T_ar[:3, :3] = T_cv[:3, :3] @ FLIP_YZ
    T_ar[:3, 3] = T_cv[:3, 3]
    return T_ar


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True):
    """s, R, t minimising |s R src + t - dst|^2. Same fit as a1-classical/common.py."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    cov = b.T @ a / len(src)
    U, S, Vt = np.linalg.svd(cov)
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    var = float((a ** 2).sum() / len(src))
    s = float(np.trace(np.diag(S) @ D) / var) if with_scale and var > 0 else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def apply_sim3(xyz: np.ndarray, s: float, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    return (s * (np.asarray(xyz, float) @ R.T)) + t


def qvec_to_rotmat(q) -> np.ndarray:
    w, x, y, z = np.asarray(q, float)
    n = np.linalg.norm([w, x, y, z])
    w, x, y, z = (w / n, x / n, y / n, z / n)
    return np.array(
        [
            [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
            [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
            [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
        ]
    )
