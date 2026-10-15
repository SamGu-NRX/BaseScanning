"""ARKit anchors. Matrices are 16 numbers, column-major."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

Mat = np.ndarray


def _mat(values: Sequence[float]) -> Mat:
    if len(values) != 16:
        raise ValueError("expected 16 column-major numbers")
    return np.array(values, dtype=np.float64).reshape((4, 4), order="F")


def _flat(matrix: Mat) -> list[float]:
    return [float(x) for x in np.asarray(matrix, dtype=np.float64).reshape(16, order="F")]


def _similarity_scale(matrix: Mat) -> float:
    linear = matrix[:3, :3]
    column_norms = np.linalg.norm(linear, axis=0)
    scale = float(np.mean(column_norms))
    if scale <= 0 or not np.isfinite(scale):
        raise ValueError("from_source scale is not positive")
    return scale


def frame_to_arkit(from_source: list[float]) -> list[float]:
    """Inverse of a column-major similarity. Upper 3×3 is (1/s)·Rᵀ and t' = −(1/s)·Rᵀ·t."""
    matrix = _mat(from_source)
    scale = _similarity_scale(matrix)
    rotation = matrix[:3, :3] / scale
    if abs(float(np.linalg.det(rotation)) - 1.0) > 1e-3:
        # Fall back to a general inverse when the linear part is not a similarity.
        return _flat(np.linalg.inv(matrix))
    inverse = np.eye(4)
    inverse[:3, :3] = rotation.T / scale
    inverse[:3, 3] = -(rotation.T / scale) @ matrix[:3, 3]
    return _flat(inverse)


def transform_points(mat4: list[float], pts: Sequence[Sequence[float]]) -> list[list[float]]:
    matrix = _mat(mat4)
    out: list[list[float]] = []
    for point in pts:
        if len(point) != 3:
            raise ValueError("expected 3-vectors")
        homog = np.array([point[0], point[1], point[2], 1.0], dtype=np.float64)
        mapped = matrix @ homog
        out.append([float(mapped[0]), float(mapped[1]), float(mapped[2])])
    return out


def _yaw_matrix(yaw_deg: float) -> Mat:
    yaw = np.deg2rad(yaw_deg)
    cosine, sine = float(np.cos(yaw)), float(np.sin(yaw))
    rotation = np.array(
        [[cosine, 0.0, sine], [0.0, 1.0, 0.0], [-sine, 0.0, cosine]],
        dtype=np.float64,
    )
    pose = np.eye(4)
    pose[:3, :3] = rotation
    return pose


def box_pose_arkit(
    from_source: list[float],
    center_m: Sequence[float],
    size_m: Sequence[float],
    yaw_deg: float,
) -> tuple[list[float], list[float]]:
    """Box-centre-to-ARKit-world pose, and size in ARKit units (size_m / s)."""
    to_arkit = _mat(frame_to_arkit(from_source))
    scene = _yaw_matrix(yaw_deg)
    scene[:3, 3] = np.array(center_m, dtype=np.float64)
    pose = to_arkit @ scene
    scale = _similarity_scale(_mat(from_source))
    size = [float(value) / scale for value in size_m]
    return _flat(pose), size


def look_at(eye: Sequence[float], target: Sequence[float]) -> list[float]:
    """Camera-to-world. ARKit camera looks along −z, +y up, +x right."""
    eye_v = np.array(eye, dtype=np.float64)
    target_v = np.array(target, dtype=np.float64)
    forward = target_v - eye_v
    norm = float(np.linalg.norm(forward))
    if norm < 1e-9:
        raise ValueError("camera eye and target are the same point")
    forward = forward / norm
    z_axis = -forward
    up = np.array([0.0, 1.0, 0.0])
    x_axis = np.cross(up, z_axis)
    x_norm = float(np.linalg.norm(x_axis))
    if x_norm < 1e-8:
        up = np.array([0.0, 0.0, 1.0])
        x_axis = np.cross(up, z_axis)
        x_norm = float(np.linalg.norm(x_axis))
    x_axis = x_axis / x_norm
    y_axis = np.cross(z_axis, x_axis)
    pose = np.eye(4)
    pose[:3, 0] = x_axis
    pose[:3, 1] = y_axis
    pose[:3, 2] = z_axis
    pose[:3, 3] = eye_v
    return _flat(pose)
