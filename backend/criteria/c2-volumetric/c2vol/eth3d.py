"""ETH3D loaders: laser scans with their scanner origins, and COLMAP undistorted cameras.

All geometry stays in the dataset's scan frame: metres, z up (the scans are levelled), the frame the
COLMAP poses of the undistorted DSLR images already share.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from plyfile import PlyData


@dataclass
class Scan:
    name: str
    origin: np.ndarray  # scanner position in the scan frame, (3,)
    points: np.ndarray  # (N, 3) float32, scan frame


def read_alignment(path: Path) -> dict[str, np.ndarray]:
    text = path.read_text()
    out = {}
    for m in re.finditer(r'filename="([^"]+)".*?<MLMatrix44>(.*?)</MLMatrix44>', text, re.S):
        out[m.group(1)] = np.array(m.group(2).split(), dtype=float).reshape(4, 4)
    return out


def load_scans(scan_dir: Path, roi_xy: tuple[tuple[float, float], tuple[float, float]] | None = None,
               z_range: tuple[float, float] | None = None) -> list[Scan]:
    scans = []
    for name, T in read_alignment(scan_dir / "scan_alignment.mlp").items():
        v = PlyData.read(str(scan_dir / name))["vertex"]
        X = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
        X = X @ T[:3, :3].T + T[:3, 3]
        keep = np.ones(len(X), bool)
        if roi_xy is not None:
            (x0, y0), (x1, y1) = roi_xy
            keep &= (X[:, 0] >= x0) & (X[:, 0] <= x1) & (X[:, 1] >= y0) & (X[:, 1] <= y1)
        if z_range is not None:
            keep &= (X[:, 2] >= z_range[0]) & (X[:, 2] <= z_range[1])
        scans.append(Scan(name, T[:3, 3].copy(), X[keep].astype(np.float32)))
    return scans


def qvec_to_rot(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
        [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
        [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
    ])


@dataclass
class Camera:
    name: str
    R: np.ndarray  # world-to-camera rotation (OpenCV: +x right, +y down, +z forward)
    t: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float
    w: int
    h: int

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t


def read_cameras(calib_dir: Path) -> dict[str, Camera]:
    cams = {}
    for line in (calib_dir / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        assert p[1] == "PINHOLE", p[1]
        cams[p[0]] = (int(p[2]), int(p[3]), *map(float, p[4:8]))
    out = {}
    lines = [l for l in (calib_dir / "images.txt").read_text().splitlines() if l and not l.startswith("#")]
    for line in lines[::2]:
        p = line.split()
        w, h, fx, fy, cx, cy = cams[p[8]]
        name = Path(p[9]).stem
        out[name] = Camera(name, qvec_to_rot(np.array(p[1:5], float)), np.array(p[5:8], float),
                           fx, fy, cx, cy, w, h)
    return out
