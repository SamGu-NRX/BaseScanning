"""Build a real-geometry test scene.json from the ETH3D "facade" dataset.

Reads the laser scans, the COLMAP poses of the undistorted DSLR images and the hand annotations in
annotations.json next to this file, then writes scene.json, the keyframe JPEGs it references and
scene.zip into the output folder. See README.md for what every number means.

    uv run python scenes/eth3d-facade/build_scene.py            # from verification/
    uv run python scenes/eth3d-facade/build_scene.py --schema path/to/scene.schema.json

Frames. The scan and the COLMAP poses share one metric frame (scan z is up to within a degree).
The scene frame is ARKit-like: y is the fitted ground normal, so the ground is y = 0, x and z are
horizontal, right-handed, and every length is in feet. The origin is the ground point under the
hypothetical meter.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = Path.home() / "house-scanning-data/verify/eth3d/facade"
DEFAULT_OUT = Path.home() / "house-scanning-data/verify/scenes/eth3d-facade"

M_PER_FT = 0.3048  # exact by definition
RNG_SEED = 7  # fixed so RANSAC, and therefore every number in README.md, is reproducible

# Coverage: a surface point counts as seen by a keyframe only if it projects inside the image, is
# on the camera side of its wall, is not hidden behind another wall of the chain, and is viewed
# within this angle of the wall normal. 75 degrees is a judgement call, not a calibrated value:
# at 75 degrees one pixel spans about 4x its head-on width on the wall, and a box drawn there
# moves by feet for a few pixels of error.
MAX_VIEW_ANGLE_DEG = 75.0
COVERAGE_STEP_FT = 0.25
BATTERY_HEIGHT_FT = 39.5 / 12  # Base Core height, docs/01 rules table; the wall band's top probe
HEADROOM_FT = 6.5  # NEC 110.26 headroom, docs/01; the overhead band's top probe
GROUND_OUT_FT = 6.0  # how far out the ground band claims to have been seen
OVERHEAD_OUT_FT = 3.0  # overhead search depth in front of the wall
OVERHEAD_MAX_FT = 12.0  # anything higher than this is not reported as an overhead
OVERHEAD_MIN_FT = 4.0  # see scan_profiles
FACING_MAX_FT = 60.0


# --------------------------------------------------------------------------------------------
# Reading the dataset


def read_alignment(path: Path) -> dict[str, np.ndarray]:
    txt = path.read_text()
    out = {
        m.group(1): np.array(m.group(2).split(), float).reshape(4, 4)
        for m in re.finditer(r'filename="([^"]+)">\s*<MLMatrix44>([^<]+)<', txt)
    }
    if len(out) != 3:
        raise SystemExit(f"expected 3 scans in {path}, found {sorted(out)}")
    return out


def load_scan_crop(scan_dir: Path, roi_xy: list[list[float]]) -> np.ndarray:
    """Laser points inside the plan box roi_xy = [[xmin, ymin], [xmax, ymax]], scan frame, m."""
    (x0, y0), (x1, y1) = roi_xy
    out = []
    for name, T in read_alignment(scan_dir / "scan_alignment.mlp").items():
        with open(scan_dir / name, "rb") as f:
            header = b""
            while not header.endswith(b"end_header\n"):
                line = f.readline()
                if not line:
                    raise SystemExit(f"{name}: no end_header")
                header += line
        if b"binary_little_endian" not in header or not re.search(
            rb"element vertex \d+\nproperty float x\nproperty float y\nproperty float z\nelement",
            header,
        ):
            raise SystemExit(f"{name}: expected little-endian float x,y,z vertices first")
        n = int(re.search(rb"element vertex (\d+)", header).group(1))
        X = np.memmap(scan_dir / name, dtype="<f4", mode="r", offset=len(header), shape=(n, 3))
        for a in range(0, n, 2_000_000):
            x = np.asarray(X[a : a + 2_000_000], float) @ T[:3, :3].T + T[:3, 3]
            keep = (x[:, 0] > x0) & (x[:, 0] < x1) & (x[:, 1] > y0) & (x[:, 1] < y1)
            out.append(x[keep])
    return np.concatenate(out)


def qvec_to_rot(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


@dataclass
class ColmapImage:
    name: str  # stem, e.g. DSC_0419
    R: np.ndarray  # world-to-camera rotation, OpenCV camera (+x right, +y down, +z forward)
    t: np.ndarray
    cam: np.ndarray  # fx, fy, cx, cy of the full-size undistorted image
    w: int
    h: int
    uv: np.ndarray  # observed keypoints, full-size pixels
    pid: np.ndarray  # their 3D point ids, -1 when untriangulated


def read_colmap(calib: Path) -> dict[str, ColmapImage]:
    cams = {}
    for ln in (calib / "cameras.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        if p[1] != "PINHOLE":
            raise SystemExit(
                f"camera {p[0]} is {p[1]}; this script only handles undistorted PINHOLE"
            )
        cams[int(p[0])] = (int(p[2]), int(p[3]), np.array(p[4:8], float))
    lines = [ln for ln in (calib / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    out = {}
    for head, pts in zip(lines[0::2], lines[1::2], strict=True):
        p = head.split()
        w, h, k = cams[int(p[8])]
        a = np.array(pts.split(), float).reshape(-1, 3) if pts.strip() else np.zeros((0, 3))
        name = Path(p[9]).stem
        out[name] = ColmapImage(
            name,
            qvec_to_rot(np.array(p[1:5], float)),
            np.array(p[5:8], float),
            k,
            w,
            h,
            a[:, :2],
            a[:, 2].astype(int),
        )
    return out


def read_points3d(calib: Path) -> dict[int, np.ndarray]:
    pts = {}
    for ln in (calib / "points3D.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        pts[int(p[0])] = np.array(p[1:4], float)
    return pts


# --------------------------------------------------------------------------------------------
# Geometry


def ransac_plane(X, thr, rng, iters=600, accept=lambda n: True):
    Xs = X[rng.integers(0, len(X), min(len(X), 40000))]
    best_k, best = -1, None
    for _ in range(iters):
        s = Xs[rng.integers(0, len(Xs), 3)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if not accept(n):
            continue
        k = int((np.abs((Xs - s[0]) @ n) < thr).sum())
        if k > best_k:
            best_k, best = k, (n, s[0])
    if best is None:
        raise SystemExit("plane RANSAC found no acceptable plane")
    inl = np.abs((X - best[1]) @ best[0]) < thr
    c = X[inl].mean(0)
    n = np.linalg.svd(X[inl] - c, full_matrices=False)[2][-1]
    return n, c, inl


def ransac_line2d(Q, thr, rng, iters=400):
    """Line through 2D points: returns (point, unit direction, inlier mask)."""
    best_k, best = -1, None
    for _ in range(iters):
        a, b = Q[rng.integers(0, len(Q), 2)]
        d = b - a
        if np.linalg.norm(d) < 0.2:
            continue
        d /= np.linalg.norm(d)
        nrm = np.array([-d[1], d[0]])
        k = int((np.abs((Q - a) @ nrm) < thr).sum())
        if k > best_k:
            best_k, best = k, (a, nrm)
    inl = np.abs((Q - best[0]) @ best[1]) < thr
    c = Q[inl].mean(0)
    d = np.linalg.svd(Q[inl] - c, full_matrices=False)[2][0]
    return c, d, inl


def intersect_lines(p1, d1, p2, d2):
    A = np.array([d1, -d2]).T
    t = np.linalg.solve(A, p2 - p1)
    return p1 + t[0] * d1


@dataclass
class Frame:
    """Scan frame (m, z up) -> scene frame (ft, y = ground normal)."""

    A: np.ndarray  # rows are the scene x, y, z axes expressed in scan coordinates
    origin: np.ndarray  # scan-frame point that becomes the scene origin

    def pts(self, X):
        return (np.asarray(X) - self.origin) @ self.A.T / M_PER_FT

    def dirs(self, V):
        return np.asarray(V) @ self.A.T


def make_frame(ground_n: np.ndarray, ground_c: np.ndarray) -> Frame:
    y = ground_n / np.linalg.norm(ground_n)
    x = np.array([1.0, 0, 0]) - y[0] * y  # scan x made horizontal
    x /= np.linalg.norm(x)
    z = np.cross(x, y)  # right-handed: x cross y = z
    return Frame(np.array([x, y, z]), ground_c.copy())


@dataclass
class Wall:
    id: str
    p0: np.ndarray  # plan [x, z] ft, left end as seen from outside
    p1: np.ndarray
    s0: float = 0.0  # unrolled position of p0 along the chain before the meter shift
    height_ft: float = 0.0
    fit_rms_ft: float = 0.0
    # Signed distance (along `outward`) from the baseline to the wall face 4-8 ft up. The baseline
    # is fitted over 0.3-8 ft and lands on the stone plinth, which stands proud of the brick; the
    # windows sit in the brick, so clicks are cast onto this face, not the baseline.
    face_offset_ft: float = 0.0

    @property
    def length(self):
        return float(np.linalg.norm(self.p1 - self.p0))

    @property
    def along(self):
        return (self.p1 - self.p0) / self.length

    @property
    def outward(self):
        # scene.schema.json: outward is the baseline direction turned 90 degrees clockwise seen
        # from +y. With y up, x right on screen puts +z down-screen, so (dx, dz) -> (-dz, dx).
        dx, dz = self.along
        return np.array([-dz, dx])

    def s_of(self, q):  # q: plan points (..., 2)
        return self.s0 + (np.asarray(q) - self.p0) @ self.along

    def plan_at(self, s):
        return self.p0 + np.multiply.outer(np.asarray(s) - self.s0, self.along)


# --------------------------------------------------------------------------------------------
# Cameras in the scene frame


@dataclass
class Keyframe:
    id: str
    name: str
    R_c2w: np.ndarray  # ARKit camera axes (+x right, +y up, looks along -z) in scene axes
    C: np.ndarray  # camera centre, scene ft
    K: np.ndarray  # fx, fy, cx, cy for the saved (downscaled) image
    w: int
    h: int

    def project(self, X):
        """ARKit convention: a camera-space point (x, y, z) with z < 0 maps to
        u = cx + fx * x / -z, v = cy - fy * y / -z  (v grows downward in the image)."""
        Xc = (np.asarray(X) - self.C) @ self.R_c2w  # = R_c2w^T (X - C), row form
        z = -Xc[..., 2]
        fx, fy, cx, cy = self.K
        with np.errstate(divide="ignore", invalid="ignore"):
            u = cx + fx * Xc[..., 0] / z
            v = cy - fy * Xc[..., 1] / z
        return u, v, z

    def ray(self, u, v):
        """World direction of pixel (u, v): camera ray ((u-cx)/fx, -(v-cy)/fy, -1)."""
        fx, fy, cx, cy = self.K
        d = np.array([(u - cx) / fx, -(v - cy) / fy, -1.0])
        return self.R_c2w @ d

    def pose16(self):
        T = np.eye(4)
        T[:3, :3] = self.R_c2w
        T[:3, 3] = self.C
        return [round(float(v), 6) for v in T.flatten(order="F")]  # column-major


OPENCV_TO_ARKIT_CAM = np.diag([1.0, -1.0, -1.0])  # flip y (down -> up) and z (forward -> back)


def make_keyframe(img: ColmapImage, frame: Frame, scale: float, kid: str) -> Keyframe:
    w, h = round(img.w * scale), round(img.h * scale)
    sx, sy = w / img.w, h / img.h
    fx, fy, cx, cy = img.cam
    # Pixel coordinates are continuous with (0, 0) at the image's top-left corner, so a resize
    # scales focal lengths and principal point alike.
    K = np.array([fx * sx, fy * sy, cx * sx, cy * sy])
    R_c2w_scan = img.R.T @ OPENCV_TO_ARKIT_CAM
    C_scan = -img.R.T @ img.t
    return Keyframe(kid, img.name, frame.A @ R_c2w_scan, frame.pts(C_scan), K, w, h)


# --------------------------------------------------------------------------------------------
# Scene pieces


def fit_ground(P, roi_ground, rng):
    (x0, y0), (x1, y1) = roi_ground
    zmed = np.median(P[:, 2])
    m = (P[:, 0] > x0) & (P[:, 0] < x1) & (P[:, 1] > y0) & (P[:, 1] < y1) & (P[:, 2] < zmed)
    n, c, inl = ransac_plane(P[m], 0.03, rng, accept=lambda n: abs(n[2]) > np.cos(np.radians(10)))
    n = n if n[2] > 0 else -n
    rms = float(np.sqrt(np.mean(((P[m][inl] - c) @ n) ** 2)))
    return (
        n,
        c,
        {
            "inliers": int(inl.sum()),
            "rms_m": rms,
            "tilt_from_scan_z_deg": float(np.degrees(np.arccos(n[2]))),
        },
    )


def fit_walls(P_scene, ann_walls, frame, rng):
    """Fit each wall's plan line to scan points near its rough segment, then join neighbours."""
    lines, stats = [], []
    h = P_scene[:, 1]
    plan = P_scene[:, [0, 2]]
    for w in ann_walls:
        # rough ends are plan points read off a top view; their scan z does not matter beyond
        # a few centimetres because the scene y axis is within 1.2 degrees of scan z
        a, b = frame.pts(np.c_[np.array(w["rough_scan_xy_m"]), np.zeros(2)])[:, [0, 2]]
        d = (b - a) / np.linalg.norm(b - a)
        nrm = np.array([-d[1], d[0]])
        t = (plan - a) @ d
        L = np.linalg.norm(b - a)
        trim = 1.0  # ft kept away from the rough ends so neighbouring walls do not leak in
        m = (np.abs((plan - a) @ nrm) < 1.0) & (t > trim) & (t < L - trim) & (h > 0.3) & (h < 8.0)
        if m.sum() < 1000:
            raise SystemExit(f"wall {w['id']}: only {m.sum()} scan points near its rough segment")
        c, dd, inl = ransac_line2d(plan[m], 0.1, rng)
        if dd @ d < 0:
            dd = -dd
        res = (plan[m][inl] - c) @ np.array([-dd[1], dd[0]])
        near = (np.abs((plan - c) @ np.array([-dd[1], dd[0]])) < 0.15) & (t > trim) & (t < L - trim)
        lines.append((c, dd, a, b))
        stats.append(
            {
                "rms_ft": float(np.sqrt(np.mean(res**2))),
                "inliers": int(inl.sum()),
                "height_ft": float(np.percentile(h[near], 99.5)),
            }
        )
    walls = []
    for i, (w, (c, d, a, b), st) in enumerate(zip(ann_walls, lines, stats, strict=True)):
        if i > 0:
            p0 = intersect_lines(*lines[i - 1][:2], c, d)
        else:
            p0 = extent_end(plan, h, c, d, a, side=0)
        if i < len(lines) - 1:
            p1 = intersect_lines(c, d, *lines[i + 1][:2])
        else:
            p1 = extent_end(plan, h, c, d, b, side=1)
        walls.append(
            Wall(w["id"], p0, p1, height_ft=round(st["height_ft"], 1), fit_rms_ft=st["rms_ft"])
        )
    for w, st in zip(walls, stats, strict=True):
        lat = (plan - w.p0) @ w.along
        out = (plan - w.p0) @ w.outward
        k = (lat > 1.0) & (lat < w.length - 1.0) & (h > 4.0) & (h < 8.0) & (np.abs(out) < 1.0)
        hist, edges = np.histogram(out[k], bins=np.arange(-1.0, 1.0001, 0.02))
        w.face_offset_ft = float(edges[hist.argmax()] + 0.01)
        st["face_offset_ft"] = w.face_offset_ft
    s = 0.0
    for w in walls:
        w.s0 = s
        s += w.length
    return walls, stats


def extent_end(plan, h, c, d, rough_end, side):
    """Free chain end: the last scanned wall point along the fitted line near the rough end."""
    nrm = np.array([-d[1], d[0]])
    t_rough = (rough_end - c) @ d
    m = (
        (np.abs((plan - c) @ nrm) < 0.15)
        & (h > 0.3)
        & (h < 8.0)
        & (np.abs((plan - c) @ d - t_rough) < 4)
    )
    t = (plan[m] - c) @ d
    te = np.percentile(t, 99.8) if side == 1 else np.percentile(t, 0.2)
    return c + te * d


def seg_cross(a, b, c, d):
    def orient(p, q, r):
        return np.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))

    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def visible(kf: Keyframe, wall: Wall, X, walls) -> bool:
    """X: scene point on or in front of `wall`. See MAX_VIEW_ANGLE_DEG for the criteria."""
    u, v, z = kf.project(X)
    if not (z > 0.5 and 0 <= u < kf.w and 0 <= v < kf.h):
        return False
    to_cam = kf.C - X
    n3 = np.array([wall.outward[0], 0.0, wall.outward[1]])
    cosang = (to_cam @ n3) / np.linalg.norm(to_cam)
    if cosang < np.cos(np.radians(MAX_VIEW_ANGLE_DEG)):
        return False
    a, b = kf.C[[0, 2]], X[[0, 2]]
    for w in walls:
        if w is wall:
            continue
        # shrink the other wall slightly so a shared corner does not count as a crossing
        e = 0.02 * (w.p1 - w.p0)
        if seg_cross(a, b, w.p0 + e, w.p1 - e):
            return False
    return True


def band_probes(band: str, wall: Wall, s: float, facing_depth: float | None):
    q = wall.plan_at(s)
    o = wall.outward

    def at(out, y):
        p = q + out * o
        return np.array([p[0], y, p[1]])

    if band == "wall":
        return [at(0.0, 0.0), at(0.0, BATTERY_HEIGHT_FT)]
    if band == "ground":
        return [at(0.0, 0.0), at(GROUND_OUT_FT, 0.0)]
    if band == "overhead":
        return [at(0.0, HEADROOM_FT), at(OVERHEAD_OUT_FT, HEADROOM_FT), at(OVERHEAD_OUT_FT, 0.0)]
    if band == "facing":
        if facing_depth is None:
            return None
        return [at(0.0, 3.0), at(min(facing_depth, 20.0), 3.0)]
    raise ValueError(band)


def intervals(ss, ok, step):
    """Sample centres ss with flags ok -> [start, end] spans of consecutive True samples."""
    out = []
    for s, k in zip(ss, ok, strict=True):
        if not k:
            continue
        if out and abs(out[-1][1] - (s - step / 2)) < 1e-9:
            out[-1][1] = s + step / 2
        else:
            out.append([s - step / 2, s + step / 2])
    return out


WALL_RELIEF_FT = 1.0  # pilasters, sills and downspouts stand out less than this; see scan_profiles
BIN_INSET_FT = 0.15  # keeps a neighbouring wall that meets this one at a corner out of the end bins


def scan_profiles(P_scene, walls):
    """Per 1 ft of chain: the lowest thing overhead near the wall and the straight-out gap.

    Overhead: something with open space under it. Points within OVERHEAD_OUT_FT of the wall are
    binned into 0.25 ft cells in plan; a cell whose lowest point is above OVERHEAD_MIN_FT does not
    reach down, so its lowest point is a clearance. Cells that reach lower (a pilaster, the
    neighbouring wall at a corner) are obstacles, not overheads. With a 2 ft floor the tops of
    the two bins by the door and the downspout shoe read as 2.1-2.5 ft overheads, because the
    scanner saw their tops but not their lower parts; the photos show nothing hanging over this
    wall below the tower's bay window, so the floor is 4 ft. A real overhang lower than that
    would be missed.
    Facing: the nearest point more than WALL_RELIEF_FT out, between 1 and 6 ft high. The wall's
    own relief is excluded so it does not read as a fence; the cost is that an obstacle within
    1 ft of the wall is not reported."""
    plan, h = P_scene[:, [0, 2]], P_scene[:, 1]
    over, facing = [], []
    for w in walls:
        lat = (plan - w.p0) @ w.along
        out = (plan - w.p0) @ w.outward
        near = (
            (out > 0.3)
            & (out < OVERHEAD_OUT_FT)
            & (h > 0.5)
            & (h < OVERHEAD_MAX_FT)
            & (lat > 0)
            & (lat < w.length)
        )
        cell = np.floor(np.c_[lat[near], out[near]] / 0.25).astype(int)
        cell_min, cell_n = {}, {}
        for c, hh in zip(map(tuple, cell), h[near], strict=True):
            cell_min[c] = min(cell_min.get(c, np.inf), hh)
            cell_n[c] = cell_n.get(c, 0) + 1
        for a in np.arange(0.0, w.length, 1.0):
            b = min(a + 1.0, w.length)
            k = (lat >= a + BIN_INSET_FT) & (lat < b - BIN_INSET_FT)
            lows = [
                v
                for c, v in cell_min.items()
                if a <= (c[0] + 0.5) * 0.25 < b and v > OVERHEAD_MIN_FT and cell_n[c] >= 5
            ]
            over.append((w.id, w.s0 + a, w.s0 + b, float(min(lows)) if lows else None))
            kf = k & (out > WALL_RELIEF_FT) & (out < FACING_MAX_FT) & (h > 1.0) & (h < 6.0)
            if kf.sum() > 20:
                depth = float(out[kf].min())
            elif (
                k & (np.abs(h) < 0.5) & (out > FACING_MAX_FT - 5) & (out < FACING_MAX_FT)
            ).sum() > 20:
                # the scan saw open ground that far out and nothing standing before it: report
                # the cap, a lower bound, which is safe for an "at least" check
                depth = FACING_MAX_FT
            else:
                depth = None
            facing.append((w.id, w.s0 + a, w.s0 + b, depth))
    return over, facing


def merge_runs(rows, key):
    """(wall_id, s_a, s_b, value) rows -> merged runs, grouped by key(value), keeping the min."""
    out = []
    for wid, a, b, v in rows:
        if v is None:
            continue
        if out and out[-1][0] == wid and abs(out[-1][2] - a) < 1e-6 and key(out[-1][3]) == key(v):
            out[-1] = (wid, out[-1][1], b, min(out[-1][3], v))
        else:
            out.append((wid, a, b, v))
    return out


# --------------------------------------------------------------------------------------------
# Annotations


def backproject(kf: Keyframe, wall: Wall, uv):
    """Intersect pixel rays with the wall's vertical plane. Returns scene points (n, 3)."""
    n3 = np.array([wall.outward[0], 0.0, wall.outward[1]])
    face = wall.p0 + wall.face_offset_ft * wall.outward
    P0 = np.array([face[0], 0.0, face[1]])
    out = []
    for u, v in uv:
        d = kf.ray(u, v)
        den = d @ n3
        if abs(den) < 1e-6:
            raise SystemExit(f"{kf.name}: ray parallel to wall {wall.id}")
        t = ((P0 - kf.C) @ n3) / den
        if t <= 0:
            raise SystemExit(f"{kf.name}: wall {wall.id} is behind the camera at pixel {u, v}")
        out.append(kf.C + t * d)
    return np.array(out)


POINT_ROLES = ("left", "right", "bottom", "top")


def measure_feature(feat, kfs_by_name, walls_by_id):
    """Each image gives [s_left, s_right, bottom, top]. Every value comes from one clicked point
    cast onto the wall plane: a point anywhere on the left jamb gives s_left, on the right jamb
    s_right, on the sill or threshold the bottom height, at the arch apex the top height. One point
    per value means a hidden corner or a leaning vertical in a tilted photo does not matter."""
    wall = walls_by_id[feat["wall_id"]]
    per_image = {}
    for img, pts in feat["points_px"].items():
        if sorted(pts) != sorted(POINT_ROLES):
            raise SystemExit(
                f"{feat['label']} @ {img}: need exactly the points {POINT_ROLES}, got {sorted(pts)}"
            )
        X = backproject(kfs_by_name[img], wall, [pts[r] for r in POINT_ROLES])
        s = wall.s_of(X[:, [0, 2]])
        s_left, s_right, bottom, top = s[0], s[1], X[2, 1], X[3, 1]
        per_image[img] = np.array([s_left, s_right, bottom, top])
        if not (s_left < s_right and bottom < top):
            raise SystemExit(f"{feat['label']} @ {img}: left/right or bottom/top came out swapped")
    vals = np.array(list(per_image.values()))
    mean = vals.mean(0)
    spread = float(np.max(vals.max(0) - vals.min(0))) if len(vals) >= 2 else None
    return wall, mean, spread, per_image


# --------------------------------------------------------------------------------------------


def round3(a):
    return [round(float(x), 3) for x in a]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="extracted ETH3D facade folder")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--annotations", type=Path, default=HERE / "annotations.json")
    ap.add_argument("--schema", type=Path, help="validate scene.json against this JSON Schema")
    ap.add_argument(
        "--overlays",
        action="store_true",
        help="also write keyframes with the annotations and the wall chain drawn on",
    )
    args = ap.parse_args()

    ann = json.loads(args.annotations.read_text())
    rng = np.random.default_rng(RNG_SEED)
    report = {}

    # 1. Laser scan, ground plane, scene frame ---------------------------------------------
    P = load_scan_crop(args.data / "dslr_scan_eval", ann["roi_scan_xy_m"])
    g_n, g_c, g_stats = fit_ground(P, ann["ground_fit_scan_xy_m"], rng)
    frame = make_frame(g_n, g_c)
    report["ground"] = g_stats

    # 2. Wall chain (first pass with a provisional origin) ---------------------------------
    P_scene = frame.pts(P)
    walls, wall_stats = fit_walls(P_scene, ann["walls"], frame, rng)

    # 3. Meter: hypothetical, placed on a wall at a documented distance from that wall's left end
    mt = ann["meter"]
    wm = next(w for w in walls if w.id == mt["wall_id"])
    if not 0 <= mt["from_wall_left_end_ft"] <= wm.length:
        raise SystemExit(f"meter is off wall {wm.id} (length {wm.length:.2f} ft)")
    foot_plan = wm.p0 + mt["from_wall_left_end_ft"] * wm.along
    # move the origin to the ground point under the meter and redo everything in that frame
    foot_scan = frame.origin + frame.A.T @ (np.array([foot_plan[0], 0.0, foot_plan[1]]) * M_PER_FT)
    frame = Frame(frame.A, foot_scan)
    P_scene = frame.pts(P)
    rng = np.random.default_rng(RNG_SEED)
    walls, wall_stats = fit_walls(P_scene, ann["walls"], frame, rng)
    wm = next(w for w in walls if w.id == mt["wall_id"])
    s_meter = wm.s0 + mt["from_wall_left_end_ft"]
    for w in walls:
        w.s0 -= s_meter  # s = 0 at the meter from here on
    walls_by_id = {w.id: w for w in walls}
    meter_pos = np.array([*(wm.p0 + mt["from_wall_left_end_ft"] * wm.along), mt["height_ft"]])[
        [0, 2, 1]
    ]
    report["walls"] = [
        {
            "id": w.id,
            "length_ft": w.length,
            "length_m": w.length * M_PER_FT,
            "s_range_ft": [w.s0, w.s0 + w.length],
            "fit": st,
        }
        for w, st in zip(walls, wall_stats, strict=True)
    ]

    # 4. Keyframes ------------------------------------------------------------------------
    colmap = read_colmap(args.data / "dslr_calibration_undistorted")
    scale = ann["keyframes"]["scale"]
    kfs = [
        make_keyframe(colmap[n], frame, scale, f"k{i + 1}")
        for i, n in enumerate(ann["keyframes"]["images"])
    ]
    kfs_by_name = {k.name: k for k in kfs}
    for w in walls:
        cams_in_front = [k.name for k in kfs if (k.C[[0, 2]] - w.p0) @ w.outward > 0]
        if not cams_in_front:
            raise SystemExit(
                f"wall {w.id}: no keyframe on its outward side; baseline order is wrong"
            )

    # Convention check: COLMAP's own triangulated points, moved into the scene frame and
    # projected with the ARKit formula, must land on the keypoints COLMAP observed them at.
    pts3d = read_points3d(args.data / "dslr_calibration_undistorted")
    conv = {}
    for k in kfs:
        im = colmap[k.name]
        sel = im.pid >= 0
        X = frame.pts(np.array([pts3d[i] for i in im.pid[sel]]))
        u, v, z = k.project(X)
        sx, sy = k.w / im.w, k.h / im.h
        err = np.hypot(u - im.uv[sel, 0] * sx, v - im.uv[sel, 1] * sy)
        conv[k.name] = {
            "points": int(sel.sum()),
            "all_in_front": bool((z > 0).all()),
            "median_px": float(np.median(err)),
            "p95_px": float(np.percentile(err, 95)),
        }
    report["arkit_projection_check"] = conv
    if any(c["median_px"] > 1.0 or not c["all_in_front"] for c in conv.values()):
        raise SystemExit(f"pose convention check failed: {conv}")

    # 5. Objects --------------------------------------------------------------------------
    objects, feature_rows = [], []
    for feat in ann["features"]:
        wall, mean, spread, per_image = measure_feature(feat, kfs_by_name, walls_by_id)
        # plus_minus_ft = max(0.1 ft, largest disagreement between images over the four values,
        # the feature's documented floor). The floor exists for cases where the images share a
        # bias that their agreement cannot show (see the door in annotations.json).
        pm = max(
            ann["min_plus_minus_ft"],
            spread if spread is not None else ann["single_view_plus_minus_ft"],
            feat.get("plus_minus_floor_ft", {}).get("value", 0.0),
        )
        obj = {
            "type": feat["type"],
            "wall_id": wall.id,
            "span_ft": [round(float(mean[0]), 2), round(float(mean[1]), 2)],
            "bottom_ft": round(max(0.0, float(mean[2])), 2),
            "top_ft": round(float(mean[3]), 2),
            "source": "tap",
            "plus_minus_ft": round(float(pm), 2),
        }
        if "attrs" in feat:
            obj["attrs"] = feat["attrs"]
        objects.append(obj)
        feature_rows.append(
            {
                "label": feat["label"],
                **obj,
                "per_image": {k: [round(float(x), 2) for x in v] for k, v in per_image.items()},
                "spread_ft": spread,
            }
        )

    # 6. Overheads and facing from the scan, coverage from the keyframes -----------------------
    over, facing = scan_profiles(P_scene, walls)  # walls are already in meter-relative s
    over_runs = merge_runs(over, key=lambda v: round(v))
    # facing depths are grouped in 2 ft steps (everything past 20 ft in one group) so the list
    # stays short; each run reports the smallest depth inside it
    face_runs = merge_runs(facing, key=lambda v: min(int(v // 2), 10))

    observed = []
    per_band = {}
    for band in ("wall", "ground", "overhead", "facing"):
        spans = []
        for w in walls:
            ss = np.arange(w.s0 + COVERAGE_STEP_FT / 2, w.s0 + w.length, COVERAGE_STEP_FT)
            ok = []
            for s in ss:
                fd = next((v for (wid, a, b, v) in facing if wid == w.id and a <= s < b), None)
                probes = band_probes(band, w, s, fd)
                ok.append(
                    probes is not None
                    and any(all(visible(k, w, X, walls) for X in probes) for k in kfs)
                )
            spans += [(w.id, a, b) for a, b in intervals(ss, ok, COVERAGE_STEP_FT)]
        # join spans that touch across a corner
        merged = []
        for _, a, b in spans:
            if merged and abs(merged[-1][1] - a) < 1e-6:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        per_band[band] = merged
        for a, b in merged:
            entry = {"band": band, "span_ft": [round(a, 2), round(b, 2)]}
            if band == "ground":
                entry["out_ft"] = GROUND_OUT_FT
            observed.append(entry)

    # 7. Assemble ----------------------------------------------------------------------------
    scene = {
        "schema_version": "1.0",
        "meter": {
            "pos": round3(meter_pos),
            "wall_id": wm.id,
            "plus_minus_ft": mt["plus_minus_ft"],
        },
        "walls": [
            {
                "id": w.id,
                "baseline": [round3(w.p0), round3(w.p1)],
                "height_ft": w.height_ft,
                "plus_minus_ft": round(max(0.1, 3 * w.fit_rms_ft), 2),
            }
            for w in walls
        ],
        "objects": objects,
        "overheads": [
            {
                "wall_id": wid,
                "span_ft": [round(a, 2), round(b, 2)],
                "clearance_ft": round(v, 2),
                "plus_minus_ft": 0.1,
            }
            for wid, a, b, v in over_runs
        ],
        "facing": [
            {
                "wall_id": wid,
                "span_ft": [round(a, 2), round(b, 2)],
                "depth_ft": round(v, 2),
                "plus_minus_ft": 0.1,
            }
            for wid, a, b, v in face_runs
        ],
        "coverage": {"ends": ann["chain_ends"], "observed": observed},
        "keyframes": [
            {
                "id": k.id,
                "pose": k.pose16(),
                "intrinsics": [round(float(x), 4) for x in k.K],
                "w": k.w,
                "h": k.h,
                "img": f"{k.id}.jpg",
            }
            for k in kfs
        ],
    }

    # 8. Write ----------------------------------------------------------------------------------
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    (out / "bundle").mkdir(parents=True)
    img_dir = args.data / "images/dslr_images_undistorted"
    for k in kfs:
        im = Image.open(img_dir / f"{k.name}.JPG")
        im.draft("RGB", (k.w, k.h))
        im.convert("RGB").resize((k.w, k.h), Image.Resampling.LANCZOS).save(
            out / "bundle" / f"{k.id}.jpg", quality=90
        )
    (out / "bundle/scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    shutil.copy(out / "bundle/scene.json", out / "scene.json")
    with zipfile.ZipFile(out / "scene.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((out / "bundle").iterdir()):
            z.write(f, f.name)

    # Sanity check: a corner clicked in one image, cast onto the wall, projected into the other
    # image, compared with where it was clicked there. Only features whose "left" point is the
    # same physical corner (bottom-left, at the sill) in both images take part.
    repro = []
    for feat in ann["features"]:
        if feat.get("left_is_bottom_left_corner_in") is None:
            continue
        i1, i2 = feat["left_is_bottom_left_corner_in"]
        wall = walls_by_id[feat["wall_id"]]
        X = backproject(kfs_by_name[i1], wall, [feat["points_px"][i1]["left"]])
        k2 = kfs_by_name[i2]
        u, v, _ = k2.project(X)
        cu, cv = feat["points_px"][i2]["left"]
        px_ft = k2.K[0] / float(-((X[0] - k2.C) @ k2.R_c2w)[2])  # pixels per foot at that depth
        err_px = float(np.hypot(u[0] - cu, v[0] - cv))
        repro.append(
            {
                "label": feat["label"],
                "from": i1,
                "into": i2,
                "predicted_px": [round(float(u[0]), 1), round(float(v[0]), 1)],
                "clicked_px": [cu, cv],
                "err_px": round(err_px, 1),
                "err_ft_at_wall": round(err_px / px_ft, 2),
            }
        )
    report["reprojection"] = repro
    report["coverage"] = per_band
    report["features"] = feature_rows
    report["meter_s_from_chain_start_ft"] = s_meter
    (out / "build_report.json").write_text(json.dumps(report, indent=1, default=float) + "\n")

    if args.overlays:
        draw_overlays(out, kfs, walls, ann, kfs_by_name, walls_by_id)

    if args.schema:
        jsonschema.validate(scene, json.loads(args.schema.read_text()))
        print(f"scene.json validates against {args.schema}")

    print(
        json.dumps(
            {k: report[k] for k in ("ground", "walls", "arkit_projection_check", "reprojection")},
            indent=1,
            default=float,
        )
    )
    print("coverage:", json.dumps(per_band))
    for r in feature_rows:
        print(
            f"{r['label']:<34} {r['type']:<10} {r['wall_id']} span {r['span_ft']} "
            f"h {r['bottom_ft']}-{r['top_ft']} +-{r['plus_minus_ft']}"
        )
    print(f"meter (INVENTED): wall {wm.id}, s = 0, pos {scene['meter']['pos']}")
    print(f"wrote {out / 'scene.json'} and {out / 'scene.zip'}")


def draw_overlays(out, kfs, walls, ann, kfs_by_name, walls_by_id):
    """Debug images outside the bundle: the chain at ground and battery height, feet ticks along
    it, and each annotated outline plus its reprojection from the other image."""
    od = out / "overlays"
    od.mkdir(exist_ok=True)
    for k in kfs:
        im = Image.open(out / "bundle" / f"{k.id}.jpg").convert("RGB")
        d = ImageDraw.Draw(im)
        for w in walls:
            for y, col in ((0.0, (255, 0, 255)), (BATTERY_HEIGHT_FT, (0, 255, 255))):
                ss = np.linspace(w.s0, w.s0 + w.length, 60)
                q = w.plan_at(ss)
                X = np.c_[q[:, 0], np.full(len(ss), y), q[:, 1]]
                u, v, z = k.project(X)
                pts = [(a, b) for a, b, c in zip(u, v, z, strict=True) if c > 0.5]
                if len(pts) > 1:
                    d.line(pts, fill=col, width=2)
            for s in np.arange(np.ceil(w.s0), w.s0 + w.length, 1.0):
                q = w.plan_at(s)
                u, v, z = k.project(
                    np.array([[q[0], 0.0, q[1]], [q[0], 1.0 if s % 5 else 2.0, q[1]]])
                )
                if (z > 0.5).all():
                    d.line([(u[0], v[0]), (u[1], v[1])], fill=(255, 255, 0), width=2)
                    if s % 5 == 0:
                        d.text((u[1] + 2, v[1] - 12), f"{int(s)}", fill=(255, 255, 0))
        for feat in ann["features"]:
            wall = walls_by_id[feat["wall_id"]]
            for img, pts in feat["points_px"].items():
                # the measured rectangle on the wall, drawn green from this image's own clicks
                # and red when measured in another image
                _, _, _, pim = measure_feature(
                    {**feat, "points_px": {img: pts}}, kfs_by_name, walls_by_id
                )
                sl, sr, yb, yt = pim[img]
                q = wall.plan_at(np.array([sl, sr, sr, sl]))
                X = np.c_[q[:, 0], [yt, yt, yb, yb], q[:, 1]]
                u, v, z = k.project(X)
                if (z > 0.5).all():
                    d.polygon(
                        list(zip(u, v, strict=True)),
                        outline=(0, 255, 0) if img == k.name else (255, 60, 60),
                    )
                if img == k.name:
                    for r in POINT_ROLES:
                        a, b = pts[r]
                        d.ellipse([a - 3, b - 3, a + 3, b + 3], outline=(255, 255, 255))
        im.save(od / f"{k.name}.jpg", quality=85)


if __name__ == "__main__":
    sys.exit(main())
