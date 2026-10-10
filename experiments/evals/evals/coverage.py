"""Does the app's coverage map claim wall and ground that no photo saw? (METHODS.md section 7)

The app exports as observed the stretches `CoverageMap.coveredIntervals` returns (`ios/HouseScanKit`
on t3/ios-mvf), and the server passes a check only over them. This runs that code, unmodified, on
ETH3D's photos and true poses (`coverage_driver/`, a Swift executable built against a read-only
checkout of HouseScanKit at `KIT_COMMIT`), and compares its answer with what each photo saw, using
the laser scan's depth for occlusion.

Frames. ETH3D's world is levelled on the ground plane under the cameras (ARKit's +y is up). Poses go
to the app in ARKit's camera axes (+x right, +y up, looking along -z), intrinsics unrotated, with
(0, 0) at the image's top-left corner, the convention of `CameraFrame`.

The truth, the pass criteria and the cause labels are defined in METHODS.md section 7. The only copy of
app logic here is `app_rows`, a replica of `CoverageMap.visibleRows` used to name why the app turned
a photo down; `main` refuses to report unless it reproduces the app's own sightings exactly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial import cKDTree

from evals.eth3d import (
    View,
    excluded_pixels,
    occluder_points,
    read_views,
    scan_points,
    up_direction,
)
from evals.pairs import results_json
from evals.paths import ETH3D_DIR, EVALS_DIR

KIT_COMMIT = "beede15f568b3a4d694fb275caf9eaaa882c546b"
KIT_CHECKOUT = EVALS_DIR / f"housescankit-{KIT_COMMIT[:7]}"
KIT_DIR = KIT_CHECKOUT / "ios" / "HouseScanKit"
DRIVER_DIR = Path(__file__).resolve().parents[1] / "coverage_driver"
DRIVER_BIN = DRIVER_DIR / ".build" / "release" / "coverage-driver"
RESULTS = Path(__file__).resolve().parents[1] / "results"

FEET = 0.3048
UP = np.array([0.0, 1.0, 0.0])
COLUMN_M = 0.02  # truth column width along the wall
SAMPLE_M = 0.05  # truth sample spacing up the wall and out across the ground
UNSEEN_MIN_SAMPLES = 2  # a column is unseen when at least this many samples (about 10 cm) are
PASS_FT = 0.5  # false-observed length allowed per scene and band: one 6 in cell
# A scan surface hides a sample when it is this much nearer. The floor covers the wall's own
# roughness; the relative part covers the 5-cell minimum filter on oblique views (at 6 m and 75
# degrees, neighbouring cells of the wall differ by up to about 16 cm).
HIDE_ABS_M = 0.10
HIDE_REL = 0.04
ZBUF_WIDTH = 512
ZBUF_WINDOW = 5
MASK_WIDTH = 1024
MISSING_FROM_SCAN = 2  # ETH3D mask label: objects the scanner missed, such as people and trees
METER_HEIGHT_M = 1.5
WALL_BAND_M = (0.3, 2.0)  # heights above ground used to find walls in the scan
MIN_STRETCH_M = 2.0


def rotation_to_y(up: np.ndarray) -> np.ndarray:
    """The smallest rotation taking the unit vector `up` to +y (Rodrigues)."""
    up = up / np.linalg.norm(up)
    v = np.cross(up, UP)
    s, c = np.linalg.norm(v), float(up @ UP)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        raise ValueError("up points straight down")
    V = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + V + V @ V * ((1 - c) / s**2)


def level_world(views: list[View], scan: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Rotation into a world whose +y is up, the ground's height in it, and the ground fit's RMS.

    Up is the normal of a plane through the ground under each camera (the lower half of the scan
    points 1 to 2.5 m below it, within 0.7 m). The mean of the cameras' own up axes, the first guess,
    was 10 degrees off on facade.
    """
    guess = up_direction(views)
    a = np.cross(guess, [1.0, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(guess, a)
    h = scan @ guess
    tree = cKDTree(np.c_[scan @ a, scan @ b])
    feet = []
    for v in views:
        c = v.center
        idx = np.asarray(tree.query_ball_point([c @ a, c @ b], 0.7), dtype=np.int64)
        below = idx[(h[idx] < c @ guess - 1.0) & (h[idx] > c @ guess - 2.5)]
        if len(below) >= 30:
            feet.append(scan[below][np.argsort(h[below])[: len(below) // 2]].mean(axis=0))
    feet = np.asarray(feet)
    if len(feet) < 10:
        raise ValueError(f"ground found under only {len(feet)} cameras")
    centre = feet.mean(axis=0)
    normal = np.linalg.svd(feet - centre)[2][2]
    normal *= np.sign(normal @ guess)
    residual = (feet - centre) @ normal
    R = rotation_to_y(normal)
    return R, float((R @ centre)[1]), float(np.sqrt(np.mean(residual**2)))


@dataclass(frozen=True)
class Wall:
    """The app's WallFrame (WallFrame.swift), plus the marked ends, in the levelled world."""

    meter: np.ndarray
    outward: np.ndarray  # horizontal unit vector
    ground_y: float
    left: float  # s of the marked ends, meters from the meter
    right: float
    fit_rms_m: float  # RMS distance from the plane of wall-band scan points within 10 cm of it

    @property
    def along(self) -> np.ndarray:
        a = np.cross(-self.outward, UP)
        return a / np.linalg.norm(a)

    @property
    def origin(self) -> np.ndarray:
        return np.array([self.meter[0], self.ground_y, self.meter[2]])

    def world(self, s: np.ndarray, height: np.ndarray, out: np.ndarray | float = 0.0) -> np.ndarray:
        s, height, out = np.broadcast_arrays(s, height, out)
        return (
            self.origin
            + s[..., None] * self.along
            + out[..., None] * self.outward
            + height[..., None] * UP
        )

    def out_of(self, points: np.ndarray) -> np.ndarray:
        return (points - self.origin) @ self.outward

    def s_of(self, points: np.ndarray) -> np.ndarray:
        return (points - self.origin) @ self.along


def straight_runs(t: np.ndarray, gap: float, min_length: float) -> list[tuple[float, float]]:
    """Stretches of sorted positions `t` with no gap wider than `gap`, at least `min_length` long."""
    t = np.sort(t)
    breaks = np.flatnonzero(np.diff(t) > gap)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(t) - 1]
    return [(t[i], t[j]) for i, j in zip(starts, ends, strict=True) if t[j] - t[i] >= min_length]


def wall_lines(plan: np.ndarray, rng: np.random.Generator, tol: float = 0.06) -> list[dict]:
    """Straight wall stretches in plan points (N, 2): repeated RANSAC line fits, each split where
    the scan has a gap over 1 m, keeping stretches of `MIN_STRETCH_M` or more.

    `tol` is 6 cm because electro's longest wall steps by about 10 cm, within what a tapped wall
    line is off anyway; at 3 cm it broke into three stretches. Gaps up to 1 m are doors and glass,
    which return no laser points but are part of the wall."""
    remaining = plan
    lines = []
    for _ in range(20):
        if len(remaining) < 200:
            break
        best = None
        for _ in range(400):
            p, q = remaining[rng.choice(len(remaining), 2, replace=False)]
            d = q - p
            if np.linalg.norm(d) < 0.5:
                continue
            n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
            inl = np.abs((remaining - p) @ n) < tol
            if best is None or inl.sum() > best.sum():
                best = inl
        if best is None:
            break
        pts = remaining[best]
        centre = pts.mean(axis=0)
        direction = np.linalg.svd(pts - centre)[2][0]
        t = (pts - centre) @ direction
        for lo, hi in straight_runs(t, 1.0, MIN_STRETCH_M):
            lines.append({"centre": centre, "direction": direction, "t": (lo, hi)})
        remaining = remaining[~best]
    return lines


def to_arkit(view: View, R: np.ndarray) -> tuple[np.ndarray, list[float], list[float]]:
    """Camera-to-world pose (levelled world, ARKit camera axes), intrinsics with (0, 0) at the image's
    top-left corner, and image size, for `CameraFrame`."""
    pose = np.eye(4)
    pose[:3, :3] = R @ view.R_wc.T @ np.diag([1.0, -1.0, -1.0])
    pose[:3, 3] = R @ view.center
    K = view.K
    # read_views puts pixel centres on integers (OpenCV); CameraFrame's first pixel spans [0, 1].
    intrinsics = [K[0, 0], K[1, 1], K[0, 2] + 0.5, K[1, 2] + 0.5]
    return pose, intrinsics, [float(view.width), float(view.height)]


def app_pixels(pose: np.ndarray, intrinsics: list[float], points: np.ndarray):
    """`CameraFrame.pixel(of:)`: pixel and whether the point is at least 5 cm in front."""
    local = (points - pose[:3, 3]) @ pose[:3, :3]
    depth = -local[..., 2]
    fx, fy, cx, cy = intrinsics
    with np.errstate(divide="ignore", invalid="ignore"):
        u = cx + fx * local[..., 0] / depth
        v = cy - fy * local[..., 1] / depth
    return u, v, depth >= 0.05


def app_gates(pose, intrinsics, size, points, normal, cfg) -> dict[str, np.ndarray]:
    """The three per-point tests inside `CoverageMap.visibleRows`, each as a boolean array."""
    to_camera = pose[:3, 3] - points
    distance = np.linalg.norm(to_camera, axis=-1)
    u, v, front = app_pixels(pose, intrinsics, points)
    mx, my = size[0] * cfg["imageMargin"], size[1] * cfg["imageMargin"]
    with np.errstate(invalid="ignore"):
        framed = front & (u >= mx) & (v >= my) & (u <= size[0] - mx) & (v <= size[1] - my)
        facing = (to_camera @ normal) / distance >= np.cos(cfg["maxAngleFromNormal"])
    return {
        "range": (distance <= cfg["maxDistance"]) & (distance > 0),
        "angle": facing,
        "frame": framed,
    }


def row_offsets(band: str, cfg: dict) -> np.ndarray:
    extent = cfg["wallBandHeight"] if band == "wall" else cfg["groundBandDepth"]
    count = max(2, int(cfg["rowsPerBand"]))
    return extent * np.arange(count) / (count - 1)


def row_points(wall: Wall, band: str, index: int, cfg: dict) -> np.ndarray:
    """The app's two samples per row of one cell, shape (rows, 2, 3)."""
    w = cfg["cellWidth"]
    alongs = index * w + w * np.array([0.25, 0.75])
    offs = row_offsets(band, cfg)
    s = np.broadcast_to(alongs, (len(offs), 2))
    o = np.broadcast_to(offs[:, None], (len(offs), 2))
    return wall.world(s, o) if band == "wall" else wall.world(s, np.zeros_like(s), o)


def band_normal(wall: Wall, band: str) -> np.ndarray:
    return wall.outward if band == "wall" else UP


def app_rows(wall: Wall, band: str, index: int, cam, cfg: dict) -> tuple[set[int], dict]:
    """Replica of `CoverageMap.visibleRows` for one cell: the rows seen, and the per-point gates."""
    pose, intrinsics, size = cam
    if wall.out_of(pose[:3, 3]) <= 0:
        return set(), {}
    gates = app_gates(
        pose, intrinsics, size, row_points(wall, band, index, cfg), band_normal(wall, band), cfg
    )
    ok = gates["range"] & gates["angle"] & gates["frame"]
    return {int(r) for r in np.flatnonzero(ok.all(axis=1))}, gates


def cell_index(s: float, cfg: dict) -> int:
    return int(np.floor(s / cfg["cellWidth"]))


def candidate_cells(wall: Wall, cam, cfg: dict) -> range:
    """`CoverageMap.candidateIndices`: cells within maxDistance along the wall, inside the ends."""
    w, tol = cfg["cellWidth"], cfg["cellWidth"] * 1e-3
    s = wall.s_of(cam[0][:3, 3])
    first = cell_index(s - cfg["maxDistance"] + tol, cfg)
    last = max(first, cell_index(s + cfg["maxDistance"] - tol, cfg))
    lo = max(first, cell_index(wall.left + tol, cfg))  # cells ending at or left of the end fail
    hi = min(last, int(np.ceil(wall.right / w)) - 1)
    return range(lo, hi + 1)


def replica_sightings(wall: Wall, cams: dict, cfg: dict) -> set[tuple]:
    out = set()
    for name, cam in cams.items():
        for band in ("wall", "ground"):
            for index in candidate_cells(wall, cam, cfg):
                rows, _ = app_rows(wall, band, index, cam, cfg)
                if rows:
                    out.add((name, band, index, frozenset(rows)))
    return out


def call_driver(payload: dict) -> dict:
    done = subprocess.run(
        [str(DRIVER_BIN)], input=json.dumps(payload), capture_output=True, text=True, check=False
    )
    if done.returncode:
        raise RuntimeError(f"coverage-driver failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def app_config() -> dict:
    """The app's default CoverageConfig, as the driver reads it."""
    return call_driver({})["config"]


def run_app(
    wall: Wall, cams: dict, variant: dict | None = None, hidden: list | None = None
) -> dict:
    """Feeds the keyframes, in order, to the app's CoverageMap through the Swift driver. `variant`
    and `hidden` model the occlusion options (see the driver's header)."""
    payload = {
        "wall": {
            "meter": wall.meter.tolist(),
            "outward": wall.outward.tolist(),
            "groundY": wall.ground_y,
        },
        "leftEnd": wall.left,
        "rightEnd": wall.right,
        "keyframes": [
            {"id": n, "pose": c[0].T.ravel().tolist(), "intrinsics": c[1], "size": c[2]}
            for n, c in cams.items()
        ],
    }
    if variant:
        payload["variant"] = variant
    if hidden:
        payload["hidden"] = hidden
    return call_driver(payload)


def prepare() -> None:
    """Checks out HouseScanKit at the pinned commit, read-only and outside the repo, and builds the
    driver against it."""
    repo = Path(__file__).resolve().parents[3]
    if not KIT_CHECKOUT.exists():
        have = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{KIT_COMMIT}^{{commit}}"], check=False
        )
        if have.returncode:
            subprocess.run(["git", "-C", str(repo), "fetch", "origin", KIT_COMMIT], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(KIT_CHECKOUT), KIT_COMMIT],
            check=True,
        )
    subprocess.run(
        ["swift", "build", "-c", "release", "--package-path", str(DRIVER_DIR)],
        env={**os.environ, "HOUSESCANKIT_DIR": str(KIT_DIR)},
        check=True,
    )


def check_kit() -> None:
    """The HouseScanKit checkout must be the pinned commit, unmodified."""
    if not KIT_DIR.is_dir():
        raise FileNotFoundError(f"{KIT_DIR} missing; run `uv run python -m evals.coverage prepare`")
    head = subprocess.run(
        ["git", "-C", str(KIT_CHECKOUT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(KIT_CHECKOUT), "status", "--porcelain", "--", "ios/HouseScanKit"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != KIT_COMMIT or dirty:
        raise RuntimeError(
            f"{KIT_CHECKOUT} is at {head} with changes {dirty!r}; expected {KIT_COMMIT}, clean"
        )
    if not DRIVER_BIN.exists():
        raise FileNotFoundError(
            f"{DRIVER_BIN} missing; run `uv run python -m evals.coverage prepare`"
        )


# Truth


def depth_buffer(view: View, blockers: np.ndarray) -> np.ndarray:
    """Nearest scan depth per cell of a `ZBUF_WIDTH`-wide grid over the image, holes closed by a
    `ZBUF_WINDOW` minimum filter; cells with no scan point read 1e6."""
    zw = ZBUF_WIDTH
    zh = round(view.height * zw / view.width)
    u, v, z, ok = project(view, blockers)
    cu = np.clip((u[ok] * zw / view.width).astype(np.int64), 0, zw - 1)
    cv_ = np.clip((v[ok] * zh / view.height).astype(np.int64), 0, zh - 1)
    buf = np.full((zh, zw), 1e6, dtype=np.float32)
    np.minimum.at(buf, (cv_, cu), z[ok].astype(np.float32))
    return cv2.erode(buf, np.ones((ZBUF_WINDOW, ZBUF_WINDOW), np.uint8))


def project(view: View, points: np.ndarray):
    """Pixel (u, v) with (0, 0) at the image's top-left corner, depth, and whether it lands inside
    the full image at least 5 cm in front."""
    Xc = points @ view.R_wc.T.astype(points.dtype) + view.t_wc.astype(points.dtype)
    z = Xc[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = view.K[0, 0] * Xc[..., 0] / z + view.K[0, 2] + 0.5
        v = view.K[1, 1] * Xc[..., 1] / z + view.K[1, 2] + 0.5
        ok = (z > 0.05) & (u >= 0) & (u <= view.width) & (v >= 0) & (v <= view.height)
    return u, v, z, ok


@dataclass
class PhotoTruth:
    """Per sample, for one photo: why it did or did not see it."""

    framed: np.ndarray
    in_range: np.ndarray
    facing: np.ndarray
    hidden: np.ndarray
    no_scan: np.ndarray  # no scan point anywhere near the pixel, so occlusion is unknown

    @property
    def saw(self) -> np.ndarray:
        # No scan point near the pixel leaves occlusion unknown, which is not seen.
        return self.framed & self.in_range & self.facing & ~self.hidden & ~self.no_scan


def photo_truth(
    view, R, zbuf, missing, points_lev, facing, max_distance, hide_abs: float = HIDE_ABS_M
) -> PhotoTruth:
    X = points_lev @ R  # levelled -> ETH3D world (R is orthonormal)
    u, v, z, framed = project(view, X)
    zh, zw = zbuf.shape
    cu = np.clip(np.nan_to_num(u * zw / view.width).astype(np.int64), 0, zw - 1)
    cv_ = np.clip(np.nan_to_num(v * zh / view.height).astype(np.int64), 0, zh - 1)
    near = zbuf[cv_, cu]
    mh, mw = missing.shape
    mu = np.clip(np.nan_to_num(u * mw / view.width).astype(np.int64), 0, mw - 1)
    mv = np.clip(np.nan_to_num(v * mh / view.height).astype(np.int64), 0, mh - 1)
    hidden = framed & ((near < z - np.maximum(hide_abs, HIDE_REL * z)) | missing[mv, mu])
    distance = np.linalg.norm(X - view.center, axis=-1)
    return PhotoTruth(
        framed=framed,
        in_range=distance <= max_distance,
        facing=np.broadcast_to(facing, framed.shape),
        hidden=hidden,
        no_scan=framed & (near >= 1e5),
    )


def face_offsets(wall: Wall, scan_lev: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Per column, how far the wall's own face stands in front of the tapped plane (0 if on it).

    A tapped wall is a plane, but real walls have pilasters: electro's stand 0.36 m proud. Behind
    one, a plane sample is inside the wall, and the pilaster would count as hiding it. A column's
    face is moved forward when, in at least 75% of its 10 cm height bins from 0.3 to 1.9 m, the
    front-most scan point within 0.6 m lies within 3 cm of their median: a flat, full-height face.
    A bush or a free-standing object is neither, so it still hides what is behind it.
    """
    rel = scan_lev - wall.origin
    t, out, h = rel @ wall.along, rel @ wall.outward, rel[:, 1]
    keep = (t >= s[0] - COLUMN_M) & (t <= s[-1] + COLUMN_M) & (out > -0.15) & (out < 0.6)
    keep &= (h > 0.3) & (h < 1.9)
    col = np.floor((t[keep] - s[0]) / COLUMN_M + 0.5).astype(np.int64)
    row = np.floor((h[keep] - 0.3) / 0.1).astype(np.int64)
    ok = (col >= 0) & (col < len(s))
    front = np.full((len(s), 16), -np.inf)
    np.maximum.at(front, (col[ok], row[ok]), out[keep][ok])
    offsets = np.zeros(len(s))
    for c in range(len(s)):
        f = front[c][np.isfinite(front[c])]
        if len(f) < 12:
            continue
        m = float(np.median(f))
        if np.sum(np.abs(f - m) <= 0.03) >= 12 and m > HIDE_ABS_M:
            offsets[c] = m
    return offsets


def band_samples(
    wall: Wall, band: str, cfg: dict, faces: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Column centres s (C,), sample offsets (R,) and points (C, R, 3) over the band. Wall samples
    sit on the wall's face (`faces`, per column); ground samples inside a pilaster are dropped
    (returned as NaN)."""
    columns = int(np.floor((wall.right - wall.left) / COLUMN_M))
    s = wall.left + COLUMN_M * (np.arange(columns) + 0.5)
    extent = cfg["wallBandHeight"] if band == "wall" else cfg["groundBandDepth"]
    n = round(extent / SAMPLE_M)
    offs = extent * (np.arange(n) + 0.5) / n
    grid_s, grid_off = np.meshgrid(s, offs, indexing="ij")
    faces = np.zeros(len(s)) if faces is None else faces
    if band == "wall":
        pts = wall.world(grid_s, grid_off, faces[:, None])
    else:
        pts = wall.world(grid_s, np.zeros_like(grid_s), grid_off)
        pts[grid_off < faces[:, None]] = np.nan
    return s, offs, pts


def two_positions(saw: np.ndarray, centres: np.ndarray, baseline: float) -> np.ndarray:
    """Per sample, whether two photos at least `baseline` apart both saw it. saw: (..., photos)."""
    far = np.linalg.norm(centres[:, None] - centres[None], axis=-1) >= baseline
    S = saw.astype(np.float32)
    return ((S @ far.astype(np.float32)) * S).sum(axis=-1) > 0


def in_intervals(s: np.ndarray, intervals: list[list[float]]) -> np.ndarray:
    out = np.zeros(s.shape, bool)
    for lo, hi in intervals:
        out |= (s >= lo) & (s <= hi)
    return out


def majority(counts: Counter, order: tuple[str, ...]) -> str:
    """Most common label; ties go to the first in `order`."""
    return max(order, key=lambda k: (counts[k], -order.index(k)))


FALSE_CAUSES = ("occlusion", "frame edge", "range")
MISSED_CAUSES = ("frame edge", "grazing angle", "range", "one position")


def false_observed_cause(
    truths: list[PhotoTruth], credited: list[int], c: int, rows: np.ndarray
) -> str:
    """Why the photos the app credited did not see the unseen samples of column c."""
    counts: Counter = Counter()
    for i in credited:
        t = truths[i]
        for r in rows:
            if not t.framed[c, r]:
                counts["frame edge"] += 1
            elif not t.in_range[c, r]:
                counts["range"] += 1
            elif t.hidden[c, r]:
                counts["occlusion"] += 1
    return majority(counts, FALSE_CAUSES)


def deficient_rows(
    sightings: list[tuple[int, set[int]]], centres: np.ndarray, rows: int, baseline: float
) -> list[int]:
    """`CoverageMap.record` for one cell: rows without two positions `baseline` apart, from the
    keyframes' sightings in capture order."""
    held: list[list[np.ndarray]] = [[] for _ in range(rows)]
    for i, seen in sightings:
        for r in seen:
            if len(held[r]) < 2 and all(
                np.linalg.norm(p - centres[i]) >= baseline for p in held[r]
            ):
                held[r].append(centres[i])
    return [r for r in range(rows) if len(held[r]) < 2]


def missed_cause(wall, band, index, rows, seers: dict[int, list[int]], cams: list, cfg) -> str:
    """Why the app did not credit the photos that saw column c. seers: deficient row -> photos
    that saw the truth sample nearest that row."""
    counts: Counter = Counter()
    for r in rows:
        for i in seers[r]:
            seen, gates = app_rows(wall, band, index, cams[i], cfg)
            if r in seen:
                counts["one position"] += 1
            elif not gates or not gates["frame"][r].all():
                counts["frame edge"] += 1
            elif not gates["angle"][r].all():
                counts["grazing angle"] += 1
            else:
                counts["range"] += 1
    return majority(counts, MISSED_CAUSES)


def evaluate_band(
    wall, band, cfg, app, views, cams, truths_by_band, centres, faces, causes: bool = True
) -> dict:
    """Scores one band. `cfg` is the app's default config: the truth's two-position bar stays at its
    baseline whatever option produced `app`. Causes are named only for the app as it is."""
    names = [v.name for v in views]
    s, offs, pts = band_samples(wall, band, cfg, faces)
    absent = np.isnan(pts[..., 0])  # ground inside a pilaster: nothing there to see
    truths = truths_by_band[band]
    saw = np.stack([t.saw for t in truths], axis=-1)  # (C, R, photos)
    seen1 = saw.any(axis=-1) | absent
    seen2 = two_positions(saw, centres, cfg["coveringBaseline"]) | absent
    claimed = in_intervals(s, app["covered"][band])

    by_cell: dict[int, list[tuple[int, set[int]]]] = {}
    for x in app["sightings"]:
        if x["band"] == band:
            by_cell.setdefault(x["index"], []).append((names.index(x["keyframe"]), set(x["rows"])))

    false_cols = claimed & ((~seen1).sum(axis=1) >= UNSEEN_MIN_SAMPLES)
    false2_cols = claimed & ((~seen2).sum(axis=1) >= UNSEEN_MIN_SAMPLES)
    missed_cols = ~claimed & seen2.all(axis=1)
    false_causes: Counter = Counter()
    missed_causes: Counter = Counter()
    for c in np.flatnonzero(false_cols) if causes else []:
        credited = [i for i, _ in by_cell.get(cell_index(s[c], cfg), [])]
        false_causes[false_observed_cause(truths, credited, c, np.flatnonzero(~seen1[c]))] += 1
    app_offs = row_offsets(band, cfg)
    for c in np.flatnonzero(missed_cols) if causes else []:
        index = cell_index(s[c], cfg)
        rows = deficient_rows(
            by_cell.get(index, []), centres, len(app_offs), cfg["coveringBaseline"]
        )
        nearest = {r: int(np.argmin(np.abs(offs - app_offs[r]))) for r in rows}
        seers = {r: list(np.flatnonzero(saw[c, nearest[r]])) for r in rows}
        missed_causes[
            missed_cause(wall, band, index, rows, seers, cams, cfg) if rows else "one position"
        ] += 1

    ft = COLUMN_M / FEET
    claimed_ft = claimed.sum() * ft
    # Samples no photo saw, but where some photo framed them over an empty scan pixel: unknown
    # rather than unseen. Counted apart so a laser hole is visible in the result.
    unknown = np.stack([t.framed & t.in_range & t.facing & t.no_scan for t in truths], axis=-1)
    only_unknown = ~seen1 & unknown.any(axis=-1)
    return {
        "claimed_ft": claimed_ft,
        "false_observed_ft": false_cols.sum() * ft,
        "false_observed_share": false_cols.sum() / max(claimed.sum(), 1),
        "false_observed_by_cause_ft": {k: false_causes[k] * ft for k in FALSE_CAUSES},
        "false_two_view_ft": false2_cols.sum() * ft,
        "false_two_view_share": false2_cols.sum() / max(claimed.sum(), 1),
        "seen_two_view_ft": seen2.all(axis=1).sum() * ft,
        "missed_ft": missed_cols.sum() * ft,
        "missed_by_cause_ft": {k: missed_causes[k] * ft for k in MISSED_CAUSES},
        "claimed_samples_unknown_for_lack_of_scan": float(only_unknown[claimed].mean())
        if claimed.any()
        else 0.0,
        # Nothing claimed tests nothing.
        "passes": bool(false_cols.sum() * ft <= PASS_FT) if claimed.any() else None,
    }


def is_wall(band: np.ndarray, line: dict, d: np.ndarray, n: np.ndarray, ground_y: float) -> bool:
    """A wall, not a stair riser or a kerb: in most 10 cm stretches along the line, scan points
    within 5 cm of it span at least 1.2 m of the 0.3 to 2.0 m band."""
    centre = np.array([line["centre"][0], 0.0, line["centre"][1]])
    rel = band - centre
    near = np.abs(rel @ n) < 0.05
    t, y = rel[near] @ d, band[near, 1]
    lo, hi = line["t"]
    keep = (t >= lo) & (t <= hi)
    bins = np.floor((t[keep] - lo) / 0.1).astype(np.int64)
    if not len(bins):
        return False
    top = np.full(bins.max() + 1, -np.inf)
    bottom = np.full(bins.max() + 1, np.inf)
    np.maximum.at(top, bins, y[keep])
    np.minimum.at(bottom, bins, y[keep])
    tall = (top - bottom) >= 1.2
    return tall.sum() >= 0.5 * np.ceil((hi - lo) / 0.1)


def pick_wall(ground_y, scan_lev, cams, cfg, rng) -> Wall | None:
    """The straight wall stretch the most photos see (at least 1 m of it, within the app's range),
    as taps would give it; None when no photo sees any."""
    y = scan_lev[:, 1]
    band = scan_lev[(y > ground_y + WALL_BAND_M[0]) & (y < ground_y + WALL_BAND_M[1])]
    plan = np.unique(np.floor(band[:, [0, 2]] / 0.05), axis=0) * 0.05 + 0.025
    best, best_score = None, -1
    for line in wall_lines(plan, rng):
        d = np.array([line["direction"][0], 0.0, line["direction"][1]])
        n = np.cross(d, UP)
        lo, hi = line["t"]
        if not is_wall(band, line, d, n, ground_y):
            continue
        mid = np.array([line["centre"][0], ground_y + 1.0, line["centre"][1]])
        ts = np.arange(lo, hi, 0.1)
        pts = mid + ts[:, None] * d
        for sign in (1.0, -1.0):
            score = 0
            for cam in cams.values():
                if (cam[0][:3, 3] - mid) @ (sign * n) <= 0:
                    continue
                u, v, front = app_pixels(*cam[:2], pts)
                dist = np.linalg.norm(pts - cam[0][:3, 3], axis=1)
                with np.errstate(invalid="ignore"):
                    inside = (
                        front
                        & (u >= 0)
                        & (v >= 0)
                        & (u <= cam[2][0])
                        & (v <= cam[2][1])
                        & (dist <= cfg["maxDistance"])
                    )
                score += int(inside.sum() * 0.1 >= 1.0)
            if score > best_score:
                best, best_score = (line, d, sign * n), score
    if best is None or best_score == 0:
        return None
    line, d, outward = best
    lo, hi = line["t"]
    centre = np.array([line["centre"][0], 0.0, line["centre"][1]])
    meter_plan = centre + d * (lo + hi) / 2
    # Ground at the foot: scan points 0.2 to 1.2 m out, along the stretch, near the ground plane.
    rel = scan_lev - meter_plan
    s_all, out_all = rel @ np.cross(-outward, UP), rel @ outward
    along_ok = (s_all > -(hi - lo) / 2) & (s_all < (hi - lo) / 2)
    foot = along_ok & (out_all > 0.2) & (out_all < 1.2) & (np.abs(scan_lev[:, 1] - ground_y) < 0.3)
    local_ground = float(np.median(scan_lev[foot, 1]))
    wall_pts = (
        along_ok
        & (np.abs(out_all) < 0.10)
        & (y > local_ground + WALL_BAND_M[0])
        & (y < local_ground + WALL_BAND_M[1])
    )
    meter = meter_plan + UP * (local_ground + METER_HEIGHT_M)
    # The meter is mid-stretch, so the marked ends sit half the stretch either side of it.
    half = (hi - lo) / 2
    rms = float(np.sqrt(np.mean(out_all[wall_pts] ** 2)))
    return Wall(meter, outward, local_ground, -half, half, rms)


# Runs


RUNS = ("electro", "facade")


@dataclass
class Setup:
    """One scene ready to score: the wall, the keyframes and each photo's truth and depth."""

    scene: str
    cfg: dict
    views: list[View]
    R: np.ndarray
    cams: dict
    wall: Wall
    ground_rms: float
    centres: np.ndarray
    faces: np.ndarray
    truths: dict[str, list[PhotoTruth]]
    zbufs: list[np.ndarray]
    missing: list[np.ndarray]

    def score(self, app: dict, causes: bool = True) -> dict:
        return {
            band: evaluate_band(
                self.wall,
                band,
                self.cfg,
                app,
                self.views,
                list(self.cams.values()),
                self.truths,
                self.centres,
                self.faces,
                causes,
            )
            for band in ("wall", "ground")
        }


def setup_scene(scene: str) -> Setup | None:
    """None when no photo sees a straight wall within the app's range."""
    cfg = app_config()
    views = read_views(ETH3D_DIR / scene)
    scan = scan_points(scene)
    R, ground_y, ground_rms = level_world(views, scan[::3].astype(np.float64))
    cams = {v.name: to_arkit(v, R) for v in views}
    wall = pick_wall(
        ground_y, scan[::3].astype(np.float64) @ R.T, cams, cfg, np.random.default_rng(0)
    )
    if wall is None:
        return None
    blockers = np.concatenate([scan, occluder_points(scene)])
    centres = np.array([c[0][:3, 3] for c in cams.values()])
    columns = band_samples(wall, "wall", cfg)[0]
    faces = face_offsets(wall, scan.astype(np.float64) @ R.T, columns)
    samples = {band: band_samples(wall, band, cfg, faces)[2] for band in ("wall", "ground")}
    truths: dict[str, list[PhotoTruth]] = {"wall": [], "ground": []}
    zbufs, missings = [], []
    for v, centre in zip(views, centres, strict=True):
        zbuf = depth_buffer(v, blockers)
        missing = excluded_pixels(
            v, MASK_WIDTH, round(v.height * MASK_WIDTH / v.width), labels=(MISSING_FROM_SCAN,)
        )
        zbufs.append(zbuf)
        missings.append(missing)
        facing = {"wall": wall.out_of(centre) > 0, "ground": centre[1] > wall.ground_y}
        for band, pts in samples.items():
            truths[band].append(
                photo_truth(v, R, zbuf, missing, pts, facing[band], cfg["maxDistance"])
            )
    return Setup(
        scene, cfg, views, R, cams, wall, ground_rms, centres, faces, truths, zbufs, missings
    )


def evaluate_scene(scene: str) -> dict:
    setup = setup_scene(scene)
    if setup is None:
        return {"scene": scene, "photos": len(read_views(ETH3D_DIR / scene)), "wall": None}
    wall, cams = setup.wall, setup.cams
    app = run_app(wall, cams)
    got = {(x["keyframe"], x["band"], x["index"], frozenset(x["rows"])) for x in app["sightings"]}
    mismatched = len(got ^ replica_sightings(wall, cams, setup.cfg))
    seen_by = {x["keyframe"] for x in app["sightings"]}
    return {
        "scene": scene,
        "photos": len(setup.views),
        "photos_with_a_sighting": len(seen_by),
        "wall_length_ft": (wall.right - wall.left) / FEET,
        "wall_fit_rms_cm": 100 * wall.fit_rms_m,
        "pilaster_ft": float((setup.faces > 0).sum() * COLUMN_M / FEET),
        "ground_fit_rms_cm": 100 * setup.ground_rms,
        "meter_levelled_m": wall.meter.round(3).tolist(),
        "outward": wall.outward.round(4).tolist(),
        "wall": "picked",
        "sightings": len(got),
        "replica_mismatches": mismatched,
        "bands": setup.score(app),
    }


def _ft(x: float) -> str:
    return f"{x:.1f}"


def markdown(runs: list[dict]) -> str:
    lines = [
        "# Does the app's coverage map claim surface no photo saw? (generated by `make coverage`)",
        "",
        f"HouseScanKit `CoverageMap` at {KIT_COMMIT} (t3/ios-mvf), unmodified, default "
        "`CoverageConfig`. ETH3D photos with true poses, "
        "every photo a kept keyframe. Lengths in feet along the wall. Definitions and pass criteria: "
        "METHODS.md section 7.",
        "",
        "| Scene | Band | Claimed | False-observed (share) | Pass (<= 0.5 ft) | Causes: occlusion / frame edge / range | False-observed, 2-position truth (share) | Seen from 2 positions | Missed | Missed causes: frame edge / grazing / range / one position |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        if r["wall"] is None:
            continue
        for band, b in r["bands"].items():
            fc, mc = b["false_observed_by_cause_ft"], b["missed_by_cause_ft"]
            lines.append(
                f"| {r['scene']} | {band} | {_ft(b['claimed_ft'])} | "
                f"{_ft(b['false_observed_ft'])} ({100 * b['false_observed_share']:.0f}%) | "
                f"{ {True: 'yes', False: 'no', None: 'untested'}[b['passes']] } | "
                f"{' / '.join(_ft(fc[k]) for k in FALSE_CAUSES)} | "
                f"{_ft(b['false_two_view_ft'])} ({100 * b['false_two_view_share']:.0f}%) | "
                f"{_ft(b['seen_two_view_ft'])} | {_ft(b['missed_ft'])} | "
                f"{' / '.join(_ft(mc[k]) for k in MISSED_CAUSES)} |"
            )
    lines += [
        "",
        "## Setup and validity checks",
        "",
        "| Scene | Photos (with a sighting) | Wall stretch | Of it, pilaster faces | Wall plane fit RMS | Ground fit RMS | App sightings | Replica mismatches | Claimed samples unknown for lack of scan (wall / ground) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        if r["wall"] is None:
            lines.append(
                f"| {r['scene']} | {r['photos']} (0) | none within the app's range | | | | | | |"
            )
            continue
        b = r["bands"]
        lines.append(
            f"| {r['scene']} | {r['photos']} ({r['photos_with_a_sighting']}) | "
            f"{r['wall_length_ft']:.1f} ft | {r['pilaster_ft']:.1f} ft | "
            f"{r['wall_fit_rms_cm']:.1f} cm | {r['ground_fit_rms_cm']:.1f} cm | "
            f"{r['sightings']} | {r['replica_mismatches']} | "
            f"{100 * b['wall']['claimed_samples_unknown_for_lack_of_scan']:.1f}% / "
            f"{100 * b['ground']['claimed_samples_unknown_for_lack_of_scan']:.1f}% |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    check_kit()
    runs = [evaluate_scene(scene) for scene in RUNS]
    bad = [r["scene"] for r in runs if r["wall"] and r["replica_mismatches"]]
    # Nothing is written until the mismatch check passes: a failed run must not overwrite the
    # tracked JSON (map3d reads it) or leave a coverage.md beside it, and the module refuses
    # to report at all rather than reporting causes it cannot stand behind.
    if bad:
        raise SystemExit(f"cause replica disagrees with the app on {bad}; causes would be wrong")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "coverage.json").write_text(results_json(runs))
    md = markdown(runs)
    (RESULTS / "coverage.md").write_text(md)
    print(md)


if __name__ == "__main__":
    if sys.argv[1:] == ["prepare"]:
        prepare()
    elif sys.argv[1:]:
        raise SystemExit("usage: python -m evals.coverage [prepare]")
    else:
        main()
