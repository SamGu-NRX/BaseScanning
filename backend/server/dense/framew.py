"""Frame W: source scaled about the origin, then rotated so up is +y."""

from __future__ import annotations

import numpy as np

from server.dense.lidar_fuse import scale_about, scale_pose_about

DEVICE_FROM_CAMERA = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])


def up_from_gravity(poses: list[np.ndarray], gravities: list[np.ndarray]) -> np.ndarray:
    """Median of ``R_c2w @ DEVICE_FROM_CAMERA.T @ (-g)`` over stills that carry gravity."""
    ups = []
    for pose, g in zip(poses, gravities, strict=True):
        if g is None:
            continue
        r = np.asarray(pose, float)[:3, :3]
        direction = r @ DEVICE_FROM_CAMERA.T @ (-np.asarray(g, float))
        n = np.linalg.norm(direction)
        if n < 1e-8:
            continue
        ups.append(direction / n)
    if not ups:
        raise ValueError("no gravity samples")
    med = np.median(np.stack(ups, axis=0), axis=0)
    return med / np.linalg.norm(med)


def up_from_cameras(centers: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Normal of the camera-centre plane, sign so most points lie below."""
    c = np.asarray(centers, float)
    if len(c) < 3:
        return np.array([0.0, 1.0, 0.0])
    _, _, vt = np.linalg.svd(c - c.mean(0), full_matrices=False)
    n = vt[-1]
    below = np.asarray(points, float) - c.mean(0)
    if len(below) and np.median(below @ n) > 0:
        n = -n
    # "below" means points have a negative component along up, so up points toward cameras
    # from the points. If median point is in the +n direction from cameras, flip.
    return n / np.linalg.norm(n)


def rotation_up_to_y(up: np.ndarray) -> np.ndarray:
    """4×4 rotation taking ``up`` to +y. +y in, identity out."""
    y = np.asarray(up, float)
    y = y / np.linalg.norm(y)
    target = np.array([0.0, 1.0, 0.0])
    if float(np.linalg.norm(y - target)) < 1e-8:
        return np.eye(4)
    axis = np.cross(y, target)
    sin = np.linalg.norm(axis)
    cos = float(np.clip(y @ target, -1, 1))
    if sin < 1e-8:
        rot3 = np.diag([1.0, -1.0, -1.0]) if cos < 0 else np.eye(3)
    else:
        axis = axis / sin
        kx, ky, kz = axis
        cross = np.array([[0, -kz, ky], [kz, 0, -kx], [-ky, kx, 0]])
        rot3 = np.eye(3) + cross * sin + (cross @ cross) * (1 - cos)
    rot = np.eye(4)
    rot[:3, :3] = rot3
    return rot


def to_frame_w(
    points: np.ndarray,
    poses: list[np.ndarray],
    origin: np.ndarray,
    factor: float,
    up: np.ndarray | None,
) -> tuple[np.ndarray, list[np.ndarray], np.ndarray]:
    """Return points and poses in W, and ``T_W_from_source`` (scale then rotate)."""
    origin = np.asarray(origin, float)
    scaled = scale_about(points, origin, factor) if len(points) else np.zeros((0, 3))
    scaled_poses = [scale_pose_about(p, origin, factor) for p in poses]
    if up is None:
        up = np.array([0.0, 1.0, 0.0])
    rot = rotation_up_to_y(up)
    # T = rot @ scale_about_origin. scale_about is (x-origin)*factor + origin = factor*x + (1-factor)*origin
    transform = rot.copy()
    transform[:3, :3] *= factor
    transform[:3, 3] = rot[:3, :3] @ ((1.0 - factor) * origin)

    # Apply rot to already-scaled coordinates (origin stays put only when we rotate about origin).
    # rotation_up_to_y rotates about the world origin, not about `origin`. Do it about origin:
    def apply_rot(xyz: np.ndarray) -> np.ndarray:
        return (np.asarray(xyz, float) - origin) @ rot[:3, :3].T + origin

    points_w = apply_rot(scaled) if len(scaled) else scaled
    poses_w = []
    for pose in scaled_poses:
        out = np.array(pose, float, copy=True)
        out[:3, :3] = rot[:3, :3] @ out[:3, :3]
        out[:3, 3] = apply_rot(out[:3, 3])
        poses_w.append(out)
    # Rebuild T so it matches apply: p_w = R (factor (p - origin)) + origin = factor R p + origin - factor R origin
    transform = np.eye(4)
    transform[:3, :3] = factor * rot[:3, :3]
    transform[:3, 3] = origin - factor * (rot[:3, :3] @ origin)
    return points_w, poses_w, transform
