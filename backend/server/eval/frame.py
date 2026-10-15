"""The meter-anchored scene frame and pose conventions.

Scene frame (meter_anchored/v0): meters; origin on the ground directly below the electric meter;
+y up (against gravity); +z out of the meter's wall, horizontal; +x = y cross z, which runs along
the meter wall to the right as seen by someone outside facing it. That observer's view is exactly
an ARKit camera looking at the wall, so s (along the chain, positive right) equals x on the meter
wall, matching PR #11's scene.json convention.

Poses follow the capture packet: camera-to-world 4x4, 16 numbers column by column, ARKit camera
axes (+x right, +y up, looks along -z). Pixels are continuous with (0, 0) at the top-left corner.
"""

from __future__ import annotations

import numpy as np

M_PER_FT = 0.3048
ARKIT_FROM_CV = np.diag([1.0, -1.0, -1.0])  # flips y (down -> up) and z (forward -> backward)


def mat4_from_list(v) -> np.ndarray:
    return np.asarray(v, float).reshape(4, 4, order="F")


def mat4_to_list(T: np.ndarray, nd: int = 6) -> list[float]:
    return [round(float(x), nd) for x in np.asarray(T).flatten(order="F")]


def apply(T: np.ndarray, X) -> np.ndarray:
    X = np.asarray(X, float)
    return X @ T[:3, :3].T + T[:3, 3]


def apply_dir(T: np.ndarray, V) -> np.ndarray:
    """Directions: rotation part only, renormalised (T may carry a scale)."""
    V = np.asarray(V, float) @ T[:3, :3].T
    return V / np.linalg.norm(V, axis=-1, keepdims=True)


def scale_of(T: np.ndarray) -> float:
    return float(np.cbrt(abs(np.linalg.det(T[:3, :3]))))


def canonical_from_world(meter_foot_w, up_w, outward_w, scale: float = 1.0) -> np.ndarray:
    """Similarity taking world points (any units, scaled by `scale` to meters) to the scene frame."""
    y = np.asarray(up_w, float)
    y = y / np.linalg.norm(y)
    z = np.asarray(outward_w, float)
    z = z - (z @ y) * y
    z = z / np.linalg.norm(z)
    x = np.cross(y, z)
    R = np.array([x, y, z])
    T = np.eye(4)
    T[:3, :3] = scale * R
    T[:3, 3] = -scale * R @ np.asarray(meter_foot_w, float)
    return T


def pose_cv_to_arkit(R_wc: np.ndarray, t_wc: np.ndarray) -> np.ndarray:
    """COLMAP/OpenCV world-to-camera (R, t) -> ARKit camera-to-world 4x4 in the same world."""
    T = np.eye(4)
    T[:3, :3] = R_wc.T @ ARKIT_FROM_CV
    T[:3, 3] = -R_wc.T @ t_wc
    return T


def transform_pose(T_sw: np.ndarray, pose_w: np.ndarray) -> np.ndarray:
    """Camera-to-world pose re-expressed in scene coordinates (T_sw may be a similarity)."""
    s = scale_of(T_sw)
    out = np.eye(4)
    out[:3, :3] = (T_sw[:3, :3] / s) @ pose_w[:3, :3]
    out[:3, 3] = apply(T_sw, pose_w[:3, 3])
    return out


def project_arkit(pose_c2w: np.ndarray, K, X) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pixel (u, v) and depth (> 0 in front) of scene points for an ARKit pose."""
    fx, fy, cx, cy = K
    Xc = (np.asarray(X, float) - pose_c2w[:3, 3]) @ pose_c2w[:3, :3]
    z = -Xc[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = cx + fx * Xc[..., 0] / z
        v = cy - fy * Xc[..., 1] / z
    return u, v, z


def ray_arkit(pose_c2w: np.ndarray, K, u, v) -> np.ndarray:
    fx, fy, cx, cy = K
    d = np.stack([(np.asarray(u) - cx) / fx, -(np.asarray(v) - cy) / fy, -np.ones_like(np.asarray(u, float))], -1)
    d = d @ pose_c2w[:3, :3].T
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> np.ndarray:
    """Similarity T (4x4) minimising |dst - T src|^2 (Umeyama 1991)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src))
    E = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        E[2, 2] = -1
    R = U @ E @ Vt
    s = (sig * np.diag(E)).sum() / (S**2).sum() * len(src) if with_scale else 1.0
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = mu_d - s * R @ mu_s
    return T


def align_by_rotations(R_src: np.ndarray, c_src: np.ndarray, R_dst: np.ndarray, c_dst: np.ndarray,
                       with_scale: bool = True) -> np.ndarray:
    """Similarity T with T(src cameras) ~ dst cameras: rotation = chordal mean of R_dst R_src^T
    (camera-to-world rotations, same camera axes), then scale and translation from the centres with
    that rotation fixed. Well posed even when the centres are nearly collinear (A2's method)."""
    U, _, Vt = np.linalg.svd(sum(g @ s.T for g, s in zip(R_dst, R_src)))
    R = U @ np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt))]) @ Vt
    a, b = c_src - c_src.mean(0), c_dst - c_dst.mean(0)
    s = float(np.sum(b * (a @ R.T)) / np.sum(a * a)) if with_scale else 1.0
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = c_dst.mean(0) - s * R @ c_src.mean(0)
    return T


def collinearity(c: np.ndarray) -> float:
    """Second over first singular value of the centred camera positions (0 = on one line)."""
    sv = np.linalg.svd(np.asarray(c, float) - np.mean(c, 0), compute_uv=False)
    return float(sv[1] / sv[0]) if sv[0] > 0 else 0.0
