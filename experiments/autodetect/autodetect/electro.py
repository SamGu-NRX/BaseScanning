"""Geometry of the ETH3D electro capture packet (packet 1.1) and its laser scan, in the meter frame.

Conventions (packet/README.md and docs/00): poses map ARKit camera coordinates (x right, y up,
looking down -z) into the meter frame (+y up, +z out of the meter's wall), 16 numbers column-major.
Intrinsics are for the stored landscape photo. Depth is float32 meters along the camera's -z axis
on a grid 1/16 of the photo, 0 = none. The ray through photo pixel (u, v) is
((u - cx) / fx, -(v - cy) / fy, -1) before rotation.

The laser scan (ETH3D scan_points_10mm.npy, from the evals lane) is in ETH3D's frame. Its map to
the meter frame is meter_from_cam @ diag(1, -1, -1) @ cam_from_scan, where cam_from_scan is
ETH3D's COLMAP pose; all eight photos give the same map to 1e-15 m.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

from .paths import DATA, ELECTRO

# ETH3D's evaluation packet for this capture: COLMAP poses and the laser scan, outside git.
EVALS = Path.home() / "house-scanning-data" / "evals" / "eth3d" / "electro"
# Feet per meter; extent.py reports door measurements in feet by multiplying meters by this.
FT = 3.280839895


@dataclass
class Photo:
    """One packet photo with the geometry that lifts its pixels into the meter frame.

    The depth map covers the photo's view on a coarser grid, 1/16 of the photo; a depth
    pixel at row r, column c sees the full-photo pixel ((c + 0.5) * W / w, (r + 0.5) * H / h).
    """

    id: str
    W: int
    H: int
    K: np.ndarray  # fx, fy, cx, cy for the full photo
    pose: np.ndarray  # 4x4 camera (ARKit axes) to meter frame
    depth: np.ndarray  # (h, w) meters along -z, 0 = none

    def ray(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Origin and unit direction in the meter frame of rays through full-photo pixels.

        u and v are pixel coordinates in the full photo, scalars or arrays of one shape.
        Returns the pose translation and unit directions of that shape. The ray through
        the principal point runs along the rotated -z axis.

        >>> K = np.array([100.0, 100.0, 50.0, 40.0])
        >>> photo = Photo("x", 100, 80, K, np.eye(4), np.zeros((5, 4), np.float32))
        >>> o, d = photo.ray(np.array(50.0), np.array(40.0))
        >>> o.tolist(), d.tolist()
        ([0.0, 0.0, 0.0], [0.0, 0.0, -1.0])
        """
        fx, fy, cx, cy = self.K
        d = np.stack([(u - cx) / fx, -(v - cy) / fy, -np.ones_like(u, dtype=float)], -1)
        d = d @ self.pose[:3, :3].T
        return self.pose[:3, 3], d / np.linalg.norm(d, axis=-1, keepdims=True)

    def depth_points(self, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        """Meter-frame points of every depth pixel whose centre lies in a normalized box.

        The box (x0, y0, x1, y1) is normalized to the full photo, 0 to 1. The column
        window runs from floor(x0 * w) to ceil(x1 * w), so a centre can fall up to one
        pixel outside an edge. Pixels with depth 0 are dropped. Returns an (n, 3) array
        in the meter frame, empty when nothing qualifies.

        >>> K = np.array([2.0, 1.0, 2.0, 1.0])
        >>> photo = Photo("x", 4, 2, K, np.eye(4), np.array([[1.0, 2.0], [0.0, 4.0]]))
        >>> photo.depth_points(0.0, 0.0, 1.0, 1.0).tolist()
        [[-0.5, 0.5, -1.0], [1.0, 1.0, -2.0], [2.0, -2.0, -4.0]]
        """
        h, w = self.depth.shape
        c0, c1 = int(np.floor(max(x0, 0) * w)), int(np.ceil(min(x1, 1) * w))
        r0, r1 = int(np.floor(max(y0, 0) * h)), int(np.ceil(min(y1, 1) * h))
        rr, cc = np.mgrid[r0:r1, c0:c1]
        z = self.depth[r0:r1, c0:c1]
        ok = z > 0
        u = (cc[ok] + 0.5) * self.W / w
        v = (rr[ok] + 0.5) * self.H / h
        fx, fy, cx, cy = self.K
        pc = np.stack([(u - cx) / fx * z[ok], -(v - cy) / fy * z[ok], -z[ok]], -1)
        return pc @ self.pose[:3, :3].T + self.pose[:3, 3]

    def project(self, p: np.ndarray) -> np.ndarray:
        """Normalized image coordinates (x, y in [0, 1] inside the photo) of meter-frame points.

        Inverse of ray for points in front of the camera: project(origin + s * direction)
        recovers (u / W, v / H). A point behind the camera (camera z > 0) gives a wrong
        answer, not an error.

        >>> K = np.array([100.0, 100.0, 50.0, 40.0])
        >>> photo = Photo("x", 100, 80, K, np.eye(4), np.zeros((1, 1), np.float32))
        >>> photo.project(np.array([[0.0, 0.0, -2.0]])).tolist()
        [[0.5, 0.5]]
        """
        R, t = self.pose[:3, :3], self.pose[:3, 3]
        pc = (p - t) @ R  # into camera axes
        fx, fy, cx, cy = self.K
        u = fx * pc[..., 0] / -pc[..., 2] + cx
        v = -fy * pc[..., 1] / -pc[..., 2] + cy
        return np.stack([u / self.W, v / self.H], -1)


@cache
def manifest() -> dict:
    """The packet's manifest.json, read once: the session record and each photo's pose,
    intrinsics and depth-map layout. Lives under paths.ELECTRO, outside git."""
    return json.loads((ELECTRO / "manifest.json").read_text())


def ground_y() -> float:
    """Height in meters of the meter's ground anchor, from the manifest session record."""
    return manifest()["session"]["meter_anchor"]["ground_y_m"]


@cache
def photos() -> dict[str, Photo]:
    """Every packet photo as a Photo keyed by id, read once.

    Depth maps load as little-endian float32 and poses decode from 16 column-major
    numbers through reshape(4, 4).T.
    """
    out = {}
    for p in manifest()["photos"]:
        d = p["depth"]
        depth = np.fromfile(ELECTRO / d["map"]["path"], "<f4").reshape(d["height"], d["width"])
        pose = np.array(p["pose"], dtype=float).reshape(4, 4).T  # column-major
        out[p["id"]] = Photo(p["id"], p["width"], p["height"], np.array(p["intrinsics"], float), pose, depth)
    return out


def _colmap_poses() -> dict[str, np.ndarray]:
    """Camera-from-scan poses for the packet's DSLR images, keyed by file name.

    Parses ETH3D's COLMAP images.txt: quaternion w, x, y, z then translation per image.
    """
    out = {}
    for line in (EVALS / "dslr_calibration_undistorted" / "images.txt").read_text().splitlines():
        f = line.split()
        if line.startswith("#") or len(f) != 10 or not f[9].startswith("dslr_images"):
            continue
        w, x, y, z = map(float, f[1:5])
        R = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R, list(map(float, f[5:8]))
        out[f[9].split("/")[-1]] = T
    return out


def meter_from_scan() -> np.ndarray:
    """Scan frame to meter frame; raises if the eight photos disagree by more than 1 mm.

    Composes each photo's pose with diag(1, -1, -1, 1), which converts COLMAP camera
    axes (y down, +z forward) to ARKit axes (y up, -z forward), and with the COLMAP
    pose of the matching DSC_ file. Returns the first photo's map once all agree.
    """
    first = int(manifest()["session"]["id"].split("-")[2])  # eth3d-electro-9257-9264
    cols = _colmap_poses()
    Ts = [
        photos()[p["id"]].pose @ np.diag([1.0, -1.0, -1.0, 1.0]) @ cols[f"DSC_{first + k}.JPG"]
        for k, p in enumerate(manifest()["photos"])
    ]
    spread = np.abs(np.array(Ts) - Ts[0]).max()
    if spread > 1e-3:
        raise ValueError(f"photos disagree on the scan-to-meter map by {spread}")
    return Ts[0]


@cache
def scan() -> np.ndarray:
    """Laser points in the meter frame (float32, cached in DATA).

    Reads scan_points_10mm.npy from EVALS, applies meter_from_scan(), and writes
    DATA/scan_meter_frame.npy so later calls skip the transform. Delete that file to
    force a recompute.
    """
    cached = DATA / "scan_meter_frame.npy"
    if cached.exists():
        return np.load(cached)
    T = meter_from_scan()
    P = np.load(EVALS / "scan_points_10mm.npy")
    Q = (P @ T[:3, :3].T + T[:3, 3]).astype(np.float32)
    np.save(cached, Q)
    return Q


@dataclass
class WallFrame:
    """A vertical plane: origin, outward unit normal (toward the cameras), and unit axis along
    the wall to the right as seen facing it. Height is meter-frame y above the meter's ground."""

    origin: np.ndarray
    normal: np.ndarray
    along: np.ndarray

    @staticmethod
    def from_normal(origin: np.ndarray, normal: np.ndarray) -> "WallFrame":
        """Frame for the plane through origin with the given normal, forced vertical.

        The normal's y component is dropped and the rest renormalized, so tilt goes
        away. The sign is kept: pass a normal pointing at the cameras for the outward
        convention. along is up x normal.

        >>> f = WallFrame.from_normal(np.zeros(3), np.array([0.3, 5.0, 0.4]))
        >>> f.normal.tolist()
        [0.6, 0.0, 0.8]
        >>> f.along.tolist()
        [0.8, 0.0, -0.6]
        """
        n = np.array([normal[0], 0.0, normal[2]])
        n /= np.linalg.norm(n)
        along = np.cross([0.0, 1.0, 0.0], n)  # up x out = right, facing the wall
        return WallFrame(np.asarray(origin, float), n, along / np.linalg.norm(along))

    def coords(self, p: np.ndarray) -> np.ndarray:
        """(along, height, out) in meters.

        along and out measure from the frame's origin; height is the point's absolute
        meter-frame y minus ground_y(), independent of the origin's height. Negative
        out means behind the wall.
        """
        d = p - self.origin
        return np.stack([d @ self.along, p[..., 1] - ground_y(), d @ self.normal], -1)

    def intersect(self, o: np.ndarray, d: np.ndarray) -> np.ndarray:
        """Points where rays from o with direction d hit the plane.

        o is one origin shared by a batch of directions, as photo.ray returns. A ray
        parallel to the plane divides by zero and the point comes out inf or nan.

        >>> f = WallFrame.from_normal(np.zeros(3), np.array([0.0, 0.0, 1.0]))
        >>> f.intersect(np.array([0.0, 0.0, 2.0]), np.array([[0.0, 0.0, -1.0]])).tolist()
        [[0.0, 0.0, 0.0]]
        """
        t = ((self.origin - o) @ self.normal) / (d @ self.normal)
        return o + t[..., None] * d


def fit_vertical_plane(pts: np.ndarray, toward: np.ndarray, tol: float = 0.02, iters: int = 400, seed: int = 0) -> tuple[WallFrame, float]:
    """RANSAC plane whose normal is within 10 degrees of horizontal, oriented toward `toward`.

    Samples 3 points per iteration and keeps candidates with |normal y| at most
    sin(10 degrees); tol is the inlier distance in meters, iters the sample count. The
    winning inliers are refined by SVD, whose smallest singular vector becomes the
    normal, and from_normal forces it vertical. Returns the frame (origin = inlier
    centroid) and the fraction of all points inside tol. Raises ValueError when no
    sample of 3 points spans a near-vertical candidate.

    >>> pts = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]])
    >>> frame, frac = fit_vertical_plane(pts, toward=np.array([0.0, 0.0, 5.0]))
    >>> round(float(frac), 3)
    1.0
    >>> frame.normal.tolist()
    [0.0, 0.0, 1.0]
    """
    rng = np.random.default_rng(seed)
    best, best_n = None, -1
    for _ in range(iters):
        a, b, c = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if abs(n[1]) > np.sin(np.radians(10)):
            continue
        inl = np.abs((pts - a) @ n) < tol
        if inl.sum() > best_n:
            best, best_n = inl, inl.sum()
    if best is None:
        raise ValueError("no vertical plane found")
    q = pts[best]
    c = q.mean(0)
    # refine: normal = smallest singular vector of the inliers, forced vertical
    n = np.linalg.svd(q - c, full_matrices=False)[2][-1]
    if (toward - c) @ n < 0:
        n = -n
    return WallFrame.from_normal(c, n), best_n / len(pts)
