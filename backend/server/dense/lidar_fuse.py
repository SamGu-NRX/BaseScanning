"""LiDAR fusion. TSDF uses Open3D when its library loads; otherwise points.

WS-05's ``server.scale.lidar._scale_about`` rewrites a cloud and its cameras in
place, and ``read_depth`` there takes a spec dict plus a confidence file. These
helpers scale points only and read a raw depth path, so they stay here.
"""

from __future__ import annotations

import numpy as np

from server.common.errors import SceneError
from server.dense.params import DenseParams


def scale_about(points: np.ndarray, origin: np.ndarray, factor: float) -> np.ndarray:
    """Scale ``points`` about ``origin``. Distances to ``origin`` multiply by ``factor``."""
    origin = np.asarray(origin, float)
    return origin + float(factor) * (np.asarray(points, float) - origin)


def scale_pose_about(pose: np.ndarray, origin: np.ndarray, factor: float) -> np.ndarray:
    out = np.array(pose, float, copy=True)
    out[:3, 3] = scale_about(out[:3, 3], origin, factor)
    return out


def read_depth(path, w: int, h: int) -> tuple[np.ndarray, np.ndarray | None]:
    raw = np.fromfile(path, dtype=np.float32)
    if raw.size == w * h:
        return raw.reshape(h, w), None
    if raw.size == 2 * w * h:
        both = raw.reshape(h, w, 2)
        return both[..., 0], both[..., 1]
    raise SceneError("input_missing:depth", f"depth file size {raw.size} is not {w}*{h}")


def unproject(depth: np.ndarray, k_full: np.ndarray, w: int, h: int) -> np.ndarray:
    """Depth pixels to camera-frame points. Intrinsics scale with the depth map."""
    z = np.asarray(depth, float)
    dh, dw = z.shape
    fx, fy, cx, cy = [float(v) for v in np.asarray(k_full, float).reshape(-1)[:4]]
    fx *= dw / w
    fy *= dh / h
    cx *= dw / w
    cy *= dh / h
    vs, us = np.mgrid[0:dh, 0:dw]
    valid = np.isfinite(z) & (z > 0)
    x = (us[valid] - cx) * z[valid] / fx
    y = -(vs[valid] - cy) * z[valid] / fy
    # ARKit looks along camera −Z. A positive depth is in front of the camera.
    zz = -z[valid]
    return np.stack([x, y, zz], axis=1)


def _open3d():
    try:
        import open3d as o3d
    except Exception as exc:  # library missing or a dylib failed to load
        raise SceneError("input_missing:open3d", f"open3d VoxelBlockGrid unavailable: {exc}") from exc
    return o3d


def tsdf_points(
    depths: list[np.ndarray],
    poses: list[np.ndarray],
    intrinsics: list[np.ndarray],
    image_wh: list[tuple[int, int]],
    params: DenseParams,
) -> np.ndarray:
    """Open3D tensor VoxelBlockGrid.

    Open3D 0.20 keeps a voxel only when its weight is strictly greater than
    ``weight_threshold``. ``tsdf_min_weight`` is the minimum weight to keep, so
    the call passes just under that value. A threshold of 1.0 would drop a
    surface seen by one view.
    """
    o3d = _open3d()
    device = o3d.core.Device("CPU:0")
    vbg = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"),
        attr_dtypes=(o3d.core.float32, o3d.core.float32, o3d.core.float32),
        attr_channels=((1,), (1,), (3,)),
        voxel_size=float(params.tsdf_voxel_m),
        block_resolution=8,
        block_count=60000,
        device=device,
    )
    for depth, pose, k_full, (w, h) in zip(depths, poses, intrinsics, image_wh, strict=True):
        dh, dw = depth.shape
        fx, fy, cx, cy = [float(v) for v in np.asarray(k_full, float).reshape(-1)[:4]]
        k_np = np.array(
            [[fx * dw / w, 0.0, cx * dw / w], [0.0, fy * dh / h, cy * dh / h], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        depth_f = np.nan_to_num(np.asarray(depth, np.float32), nan=0.0)
        depth_t = o3d.t.geometry.Image(o3d.core.Tensor(np.ascontiguousarray(depth_f), device=device))
        color = o3d.t.geometry.Image(o3d.core.Tensor(np.zeros((dh, dw, 3), np.float32), device=device))
        extrinsic = o3d.core.Tensor(np.linalg.inv(np.asarray(pose, np.float64)), dtype=o3d.core.float64, device=device)
        k64 = o3d.core.Tensor(k_np, dtype=o3d.core.float64, device=device)
        depth_trunc = float(params.lidar_max_m)
        trunc_vox = float(params.tsdf_trunc_vox)
        try:
            blocks = vbg.compute_unique_block_coordinates(
                depth_t, k64, extrinsic, 1.0, depth_trunc, trunc_voxel_multiplier=trunc_vox
            )
        except RuntimeError as exc:
            # Open3D aborts the process when a frame hits no voxel. Skip that frame.
            if "No block is touched" not in str(exc):
                raise
            continue
        if int(blocks.shape[0]) == 0:
            continue
        vbg.integrate(blocks, depth_t, color, k64, k64, extrinsic, 1.0, depth_trunc, trunc_voxel_multiplier=trunc_vox)
    threshold = max(0.0, float(params.tsdf_min_weight) - 1e-3)
    mesh = vbg.extract_triangle_mesh(weight_threshold=threshold).to_legacy()
    verts = np.asarray(mesh.vertices)
    if len(verts) == 0:
        return np.zeros((0, 3))
    return np.asarray(verts, float)


def fuse_points(
    depths: list[np.ndarray],
    poses: list[np.ndarray],
    intrinsics: list[np.ndarray],
    image_wh: list[tuple[int, int]],
    params: DenseParams,
    stride: int = 4,
    cap: int = 250_000,
) -> np.ndarray:
    chunks = []
    for depth, pose, k_full, (w, h) in zip(depths, poses, intrinsics, image_wh, strict=True):
        z = np.asarray(depth, float)
        z = np.where((z > params.lidar_min_m) & (z <= params.lidar_max_m), z, np.nan)
        small = z[::stride, ::stride]
        cam = unproject(small, np.asarray(k_full, float), w, h)
        if len(cam) == 0:
            continue
        rot = np.asarray(pose, float)[:3, :3]
        world = cam @ rot.T + np.asarray(pose, float)[:3, 3]
        chunks.append(world)
    if not chunks:
        return np.zeros((0, 3))
    pts = np.concatenate(chunks, axis=0)
    if len(pts) > cap:
        rng = np.random.default_rng(0)
        pts = pts[rng.choice(len(pts), cap, replace=False)]
    return pts
