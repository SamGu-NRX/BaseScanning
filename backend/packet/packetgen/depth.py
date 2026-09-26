"""LiDAR scene depth: render a dense reference geometry (ETH3D laser scan) into ARKit's depth
grid from each clean pose, then add LiDAR-like error and ARKit-style confidence.

ARKit's depth map is 256 x 192 for a 4:3 video format, metric regardless of any VIO scale error
(it is a time-of-flight range). The error model is an assumption from published iPhone LiDAR
evaluations: about 1 cm + 1% of range out to 5 m, degrading past the ~5 m sensor range, where
ARKit keeps filling depth from images at low confidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from scipy import ndimage

from .conventions import project_arkit

PLY_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1", "char": "i1",
             "int": "<i4", "int32": "<i4", "uint": "<u4", "short": "<i2", "ushort": "<u2"}


def read_ply_xyz(path: Path) -> np.ndarray:
    """Vertex x, y, z of a binary little-endian PLY."""
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                raise ValueError(f"{path}: no end_header")
            header += line
        offset = f.tell()
    text = header.decode("ascii", errors="ignore")
    if "binary_little_endian" not in text:
        raise ValueError(f"{path}: only binary_little_endian PLY is supported")
    n, props, in_vertex = 0, [], False
    for line in text.splitlines():
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            n, in_vertex = int(parts[2]), True
        elif parts and parts[0] == "element":
            in_vertex = False
        elif in_vertex and parts and parts[0] == "property":
            props.append((parts[2], PLY_TYPES[parts[1]]))
    dtype = np.dtype(props)
    data = np.fromfile(path, dtype=dtype, count=n, offset=offset)
    return np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float32)


def load_eth3d_scan(scan_dir: Path, max_points: int, rng: np.random.Generator) -> np.ndarray:
    """Merge ETH3D's scans into the calibration frame using scan_alignment.mlp."""
    mlp = (scan_dir / "scan_alignment.mlp").read_text()
    out = []
    for name, mat in re.findall(r'filename="([^"]+)".*?<MLMatrix44>(.*?)</MLMatrix44>', mlp, flags=re.S):
        M = np.array([float(x) for x in mat.split()]).reshape(4, 4)
        P = read_ply_xyz(scan_dir / name)
        keep = rng.random(len(P)) < min(1.0, max_points / 3 / len(P))
        P = P[keep].astype(np.float64)
        out.append(P @ M[:3, :3].T + M[:3, 3])
    return np.concatenate(out).astype(np.float32)


def depth_size(image_w: int, image_h: int) -> tuple[int, int]:
    return 256, int(round(256 * image_h / image_w))


def render_depth(points: np.ndarray, T_ar: np.ndarray, K, image_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """True depth (meters along the viewing axis) at ARKit's depth resolution, and a mask of
    pixels filled from neighbours rather than hit by the scan."""
    W, H = depth_size(*image_size)
    s = W / image_size[0]
    Kd = (K[0] * s, K[1] * s, K[2] * s, K[3] * s)
    uv, z = project_arkit(T_ar, Kd, points)
    ok = (z > 0.2) & (z < 40) & (uv[:, 0] >= 0) & (uv[:, 0] < W) & (uv[:, 1] >= 0) & (uv[:, 1] < H)
    u, v, z = uv[ok, 0], uv[ok, 1], z[ok]
    depth = np.full(H * W, np.inf)
    for du in (0, 1):
        for dv in (0, 1):
            uu = np.clip((u + du - 0.5).astype(int), 0, W - 1)
            vv = np.clip((v + dv - 0.5).astype(int), 0, H - 1)
            np.minimum.at(depth, vv * W + uu, z)
    depth = depth.reshape(H, W)
    # Background points seen through gaps between sparse foreground samples: drop pixels much
    # farther than their neighbourhood's nearest surface.
    local_min = ndimage.minimum_filter(np.where(np.isfinite(depth), depth, 1e9), size=5)
    leaked = np.isfinite(depth) & (depth > 1.2 * local_min + 0.2)
    depth[leaked] = np.inf
    missing = ~np.isfinite(depth)
    if missing.all():
        return np.zeros((H, W), np.float32), np.ones((H, W), bool)
    dist, (iy, ix) = ndimage.distance_transform_edt(missing, return_indices=True)
    filled = depth[iy, ix]
    return filled.astype(np.float32), missing


def lidar_observe(depth: np.ndarray, filled: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    d = depth.astype(np.float64)
    sigma = 0.01 + 0.01 * d + np.where(d > 5.0, 0.04 * (d - 5.0), 0.0)
    noisy = d + rng.normal(0, 1, d.shape) * sigma
    noisy = ndimage.gaussian_filter(noisy, 0.8)
    noisy = np.maximum(noisy, 0.05)
    conf = np.where(d < 3.5, 2, np.where(d < 5.5, 1, 0)).astype(np.int16)
    gy, gx = np.gradient(d)
    edge = np.hypot(gx, gy) > 0.08 * d
    edge = ndimage.binary_dilation(edge)
    conf = conf - edge.astype(np.int16)
    conf[filled] = 0
    return noisy.astype(np.float32), np.clip(conf, 0, 2).astype(np.uint8)
