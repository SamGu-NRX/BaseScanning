"""Camera and world conventions: OpenCV/COLMAP to ARKit (measure-lab-session v2, PR #7).

ARKit camera frame (ARCamera.transform): +x right and +y up in the unrotated landscape sensor
image, the camera looks along -z. OpenCV/COLMAP camera frame: +x right, +y down, looks along +z.
World: ARKit .gravity alignment, right-handed, +y up, origin at the session start.
Pixels: continuous coordinates, (0, 0) is the top-left corner of the top-left pixel (COLMAP's
convention too, so COLMAP intrinsics need no half-pixel shift).
"""

from __future__ import annotations

import numpy as np

FLIP_YZ = np.diag([1.0, -1.0, -1.0])

# Columns are the ARKit camera axes written in the Core Motion device frame (x right and y toward
# the top in portrait, z out of the screen). ARCamera.transform docs: camera x runs along the long
# axis from the front camera toward the Home button (device -y), z points out of the screen side
# (device +z), so camera y is device +x. Same matrix as PR #12's DEVICE_TO_LANDSCAPE_CAMERA.
DEVICE_FROM_CAMERA = np.array(
    [
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)

# OpenCV coordinates of an upright portrait image, re-expressed in the landscape sensor image's
# OpenCV coordinates. The sensor image is the portrait image turned 90 degrees counter-clockwise:
# portrait down (+y) becomes landscape right (+x), portrait right (+x) becomes landscape up (-y).
LANDSCAPE_FROM_PORTRAIT_CV = np.array(
    [
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)


def qvec_to_rotmat(q: np.ndarray) -> np.ndarray:
    """COLMAP quaternion (w, x, y, z) to rotation matrix."""
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
            [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
            [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
        ]
    )


def rotmat_to_quat_xyzw(R: np.ndarray) -> np.ndarray:
    """Rotation matrix to (x, y, z, w), the CMQuaternion field order."""
    m = R
    tr = np.trace(m)
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
    q = np.array([x, y, z, w])
    q = q / np.linalg.norm(q)
    return q if q[3] >= 0 else -q


def rodrigues(v: np.ndarray) -> np.ndarray:
    """Rotation matrix for a rotation vector (radians)."""
    theta = float(np.linalg.norm(v))
    if theta < 1e-15:
        return np.eye(3)
    k = v / theta
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * (K @ K)


def log_so3(R: np.ndarray) -> np.ndarray:
    """Rotation vector of a rotation matrix."""
    c = np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)
    theta = np.arccos(c)
    if theta < 1e-9:
        return np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / 2
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return w * theta / (2 * np.sin(theta))


def rot_y(angle: float) -> np.ndarray:
    """Rotation about world +y (up); positive turns +z toward +x."""
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def cam_to_world_cv(R_cw: np.ndarray, t_cw: np.ndarray) -> np.ndarray:
    """4x4 OpenCV camera-to-world from COLMAP world-to-camera R, t."""
    T = np.eye(4)
    T[:3, :3] = R_cw.T
    T[:3, 3] = -R_cw.T @ t_cw
    return T


def cv_to_arkit_pose(T_cv: np.ndarray) -> np.ndarray:
    """OpenCV camera-to-world to ARKit camera-to-world (same world): flip camera y and z."""
    T = T_cv.copy()
    T[:3, :3] = T_cv[:3, :3] @ FLIP_YZ
    return T


def arkit_to_cv_pose(T_ar: np.ndarray) -> np.ndarray:
    return cv_to_arkit_pose(T_ar)


def column_major(T: np.ndarray) -> list[float]:
    """16 numbers column by column (simd_float4x4 layout)."""
    return [float(v) for v in np.asarray(T, dtype=float).T.reshape(-1)]


def from_column_major(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(4, 4).T


def portrait_to_landscape(
    T_cv_portrait: np.ndarray, K: tuple[float, float, float, float], portrait_width: float
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Upright portrait image camera (OpenCV) to the landscape sensor image camera (OpenCV).

    Continuous pixels: u_l = v_p, v_l = W_p - u_p (PR #12 evals/camera.py)."""
    fx, fy, cx, cy = K
    T = T_cv_portrait.copy()
    T[:3, :3] = T_cv_portrait[:3, :3] @ LANDSCAPE_FROM_PORTRAIT_CV.T
    return T, (fy, fx, cy, portrait_width - cx)


def pixel_ray_arkit(T_ar: np.ndarray, K: tuple[float, float, float, float], uv: np.ndarray):
    """World ray (origin, unit direction) through continuous pixel uv, PR #7's formula:
    camera-space direction ((u - cx)/fx, -(v - cy)/fy, -1)."""
    fx, fy, cx, cy = K
    d_cam = np.array([(uv[0] - cx) / fx, -(uv[1] - cy) / fy, -1.0])
    d = T_ar[:3, :3] @ d_cam
    return T_ar[:3, 3].copy(), d / np.linalg.norm(d)


def project_arkit(T_ar: np.ndarray, K: tuple[float, float, float, float], X: np.ndarray):
    """World points (N, 3) to continuous pixels (N, 2) and depth along the viewing axis (N,)."""
    fx, fy, cx, cy = K
    R = T_ar[:3, :3]
    c = T_ar[:3, 3]
    Xc = (X - c) @ R  # rows: camera-frame coordinates
    z = -Xc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = fx * Xc[:, 0] / z + cx
        v = -fy * Xc[:, 1] / z + cy
    return np.stack([u, v], axis=1), z


def world_from_device(T_ar: np.ndarray) -> np.ndarray:
    """Rotation taking Core Motion device-frame vectors to ARKit world vectors."""
    return T_ar[:3, :3] @ DEVICE_FROM_CAMERA.T


# Core Motion attitude reference frames have z up. Taking Core Motion x = ARKit x and
# Core Motion y = -ARKit z (so Core Motion z = ARKit y) gives a right-handed map. For
# xArbitraryZVertical the true yaw between the two frames is unknown to the phone.
CM_FROM_ARKIT_WORLD = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ]
)


def heading_deg_of_vector(v_world: np.ndarray, north_azimuth_of_minus_z_deg: float) -> float:
    """Compass azimuth (degrees clockwise from true north) of a world direction.

    `north_azimuth_of_minus_z_deg` is the true azimuth of ARKit world -z. ARKit world x is 90
    degrees clockwise of -z seen from above (x = right when facing -z with y up)."""
    az_rel = np.degrees(np.arctan2(v_world[0], -v_world[2]))
    return float((north_azimuth_of_minus_z_deg + az_rel) % 360.0)
