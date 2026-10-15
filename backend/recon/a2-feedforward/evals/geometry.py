"""Small rigid-geometry helpers shared by the evals. Every function here has a hand-computed test."""

from __future__ import annotations

import numpy as np


def quat_wxyz_to_matrix(q: np.ndarray) -> np.ndarray:
    """Unit quaternions (..., 4) in w, x, y, z order to rotation matrices (..., 3, 3)."""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    R = np.empty((*q.shape[:-1], 3, 3))
    R[..., 0, 0] = 1 - 2 * (y * y + z * z)
    R[..., 0, 1] = 2 * (x * y - w * z)
    R[..., 0, 2] = 2 * (x * z + w * y)
    R[..., 1, 0] = 2 * (x * y + w * z)
    R[..., 1, 1] = 1 - 2 * (x * x + z * z)
    R[..., 1, 2] = 2 * (y * z - w * x)
    R[..., 2, 0] = 2 * (x * z - w * y)
    R[..., 2, 1] = 2 * (y * z + w * x)
    R[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def rotation_angle_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    """Angle of the relative rotation Ra^T Rb, degrees."""
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def rot_y(deg: float) -> np.ndarray:
    """Rotation about +y (up in ARKit's world) by `deg` degrees, right-handed."""
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def umeyama(
    src: np.ndarray, dst: np.ndarray, with_scale: bool
) -> tuple[float, np.ndarray, np.ndarray]:
    """Least-squares similarity (s, R, t) minimising sum |dst - (s R src + t)|^2 (Umeyama 1991).

    src, dst: (N, 3), N >= 3 and not collinear. Returns scale 1 when `with_scale` is False.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3 or len(src) < 3:
        raise ValueError(f"need matching (N>=3, 3) arrays, got {src.shape} and {dst.shape}")
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (xs**2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var_s) if with_scale else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def yaw_align(src: np.ndarray, dst: np.ndarray, up_axis: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Rotation about the up axis plus translation (4 degrees of freedom) that best maps src to dst.

    Both point sets must already share the same up axis (gravity). Returns (R, t).
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    h = [k for k in range(3) if k != up_axis]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    # Rotation angle in the horizontal plane that maximises sum of dot products.
    # With h = (h0, h1) ordered so that the rotation is right-handed about up_axis.
    if up_axis == 1:
        # About +y: x' = c x + s z, z' = -s x + c z. Maximise sum b.(R a).
        num = (b[:, 0] * a[:, 2] - b[:, 2] * a[:, 0]).sum()
        den = (b[:, 0] * a[:, 0] + b[:, 2] * a[:, 2]).sum()
        R = rot_y(np.degrees(np.arctan2(num, den)))
    elif up_axis == 2:
        num = (b[:, 1] * a[:, 0] - b[:, 0] * a[:, 1]).sum()
        den = (b[:, 0] * a[:, 0] + b[:, 1] * a[:, 1]).sum()
        ang = np.arctan2(num, den)
        c, s = np.cos(ang), np.sin(ang)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    else:
        raise ValueError(f"up_axis must be 1 or 2, got {up_axis} (horizontal axes {h})")
    t = mu_d - R @ mu_s
    return R, t


def path_length(p: np.ndarray) -> np.ndarray:
    """Cumulative distance travelled along a polyline (N, 3), starting at 0."""
    steps = np.linalg.norm(np.diff(p, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)])
