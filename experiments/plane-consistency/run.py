"""Plane consistency on ETH3D electro's wall (README). Writes results/plane_consistency.{md,json}.

    uv run python run.py

The wall, the app's view gates, the truth and the grading come from the evals harness (branch
t3/evals, `EVALS_COMMIT`), imported read-only from `EVALS_DIR`. Its Swift driver of the app is not
needed: `evals.coverage.app_rows` replicates the app's gates, and `plane_filter.covered_cells` its
`record`. If the evals' already-built driver binary is present it is run (it only reads stdin and
writes stdout) to check both replicas against the app's own answer in every pose setting.

Poses. `exact` is ETH3D's. `modern_assumed` applies `evals.ar_poses.degrade` with that setting's
errors (2% scale, 1 cm, 0.1 degrees) and `group_poses`' seeds, to one group of all 45 photos in
capture order. The files in eth3d/electro/ar_poses/ can't be used: they hold groups of 2 to 8
photos, each scaled about its own first photo, and 4 photos appear in none, so they give no single
trajectory. The wall is tapped in the same AR world as the poses, so the scale error moves it
with the cameras (about the first camera): AR s = 0.98 true s. Claims are mapped back to true s
before grading, and the truth always uses the true poses.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

# Importing the evals must not write __pycache__ into its worktree.
sys.dont_write_bytecode = True

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from plane_filter import (  # noqa: E402
    apply_homography,
    covered_cells,
    covered_intervals,
    noise_sigma,
    parallax_gate,
    patch_pixels,
    plain_threshold,
    plane_homography,
    sample_bilinear,
    too_plain,
    zncc,
)

EVALS_DIR = Path(
    os.environ.get(
        "EVALS_DIR", Path.home() / "Programming Projects/house-scanning-evals/experiments/evals"
    )
)
EVALS_COMMIT = "190ed339f41924ae0244e0b88c5ebe647ddd9eb4"
sys.path.insert(0, str(EVALS_DIR))

from evals import coverage as cov  # noqa: E402
from evals.ar_poses import SETTINGS, _seed, degrade  # noqa: E402
from evals.paths import ETH3D_DIR  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SCENE = "electro"
IMAGE_WIDTH = 1024  # the only resolution on disk for all 45 photos

# The app's default CoverageConfig, exactly as the evals' coverage-driver prints it (the replica
# reproduced all 337 of the app's sightings with these values, evals README section 7).
APP_CONFIG = {
    "cellWidth": 0.1524,
    "coveringBaseline": 0.25,
    "groundBandDepth": 1.2,
    "imageMargin": 0.03,
    "maxAngleFromNormal": 1.1344639,
    "maxDistance": 6,
    "rowsPerBand": 3,
    "wallBandHeight": 1.9812,
}

# The filter, fixed in the README before the run.
PATCH_HALF = 5  # 11 x 11 px
DELTAS_M = (0.0, 0.1, 0.2, 0.3, 0.4)  # wall plane slid toward the cameras
NCC_MIN = 0.6
PARALLAX_FRONT_M = 0.5
PARALLAX_MIN_PX = 3.0
# Median over electro's 45 photos at 1024 px of `plane_filter.noise_sigma` (grey levels; the
# photos range from 0.36 to 2.2), measured once before any filter run. The too-plain threshold
# is `plain_threshold(NOISE_SIGMA, NCC_MIN)` = 0.906: below it even a perfect match of a
# typical photo is expected to fall short of NCC 0.6. The run re-measures and stops if it moved.
NOISE_SIGMA = 0.573
PLAIN_STD = plain_threshold(NOISE_SIGMA, NCC_MIN)

# Secondary, not graded.
NCC_SWEEP = (0.4, 0.5, 0.6, 0.7, 0.8)
SIFT_RATIO = 0.8  # Lowe's ratio test
GUIDE_PX = 20.0  # a feature match must land this close to where the pose homography puts it
RANSAC_PX = 3.0
MIN_INLIERS = 20  # fewer and the pair keeps its pose homography

CAUSES = ("no qualifying pair", "too plain", "disagreed")
MISSED_CAUSES = (*CAUSES, "the app itself")


@dataclass
class Frame:
    """One photo in the AR world (levelled), OpenCV axes, at IMAGE_WIDTH."""

    name: str
    R_wc: np.ndarray
    t_wc: np.ndarray
    centre: np.ndarray
    K: np.ndarray
    cam: tuple  # (pose, intrinsics, size) in the app's CameraFrame convention, full resolution

    def pixel(self, X: np.ndarray) -> np.ndarray:
        x = self.K @ (self.R_wc @ X + self.t_wc)
        return x[:2] / x[2]


@dataclass
class PoseSetting:
    label: str
    scale: float
    frames: list[Frame]
    wall: cov.Wall  # the tapped wall in the AR world


@dataclass
class SampleTest:
    """One of a row's two samples, as photo A saw it: A's patch plainness and each partner."""

    a_plain: bool
    # partner photo -> (parallax px, best NCC over deltas or NaN, delta of the best, qualifies)
    partners: dict[int, tuple[float, float, float, bool]] = field(default_factory=dict)


# Setup


def check_evals() -> None:
    head = subprocess.run(
        ["git", "-C", str(EVALS_DIR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # --no-optional-locks keeps git status from rewriting the index of the evals worktree.
    dirty = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(EVALS_DIR),
            "status",
            "--porcelain",
            "--",
            "evals",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != EVALS_COMMIT or dirty:
        raise SystemExit(
            f"{EVALS_DIR} is at {head} with changes {dirty!r}; expected {EVALS_COMMIT}, clean"
        )


def load_gray(view) -> np.ndarray:
    path = ETH3D_DIR / SCENE / f"images_{IMAGE_WIDTH}" / f"{view.name}.jpg"
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(path)
    return img.astype(np.float32)


def pose_setting(setup: cov.Setup, setting: str, draw: int) -> PoseSetting:
    views = setup.views
    truth = [v.cam_to_world for v in views]
    if setting == "exact":
        c2w, scale = truth, 1.0
    else:
        error = SETTINGS[setting]
        scale = error.scales[0]
        rng = np.random.default_rng(_seed(setting) if draw == 0 else [_seed(setting), draw])
        c2w = degrade(truth, scale, error, rng)
    frames = []
    for v, D in zip(views, c2w, strict=True):
        R_c2w = setup.R @ D[:3, :3]
        centre = setup.R @ D[:3, 3]
        pose = np.eye(4)
        pose[:3, :3] = R_c2w @ np.diag([1.0, -1.0, -1.0])
        pose[:3, 3] = centre
        intr = [v.K[0, 0], v.K[1, 1], v.K[0, 2] + 0.5, v.K[1, 2] + 0.5]
        height = round(v.height * IMAGE_WIDTH / v.width)
        frames.append(
            Frame(
                v.name,
                R_c2w.T,
                -R_c2w.T @ centre,
                centre,
                v.scaled_K(IMAGE_WIDTH, height),
                (pose, intr, [float(v.width), float(v.height)]),
            )
        )
    o = setup.R @ views[0].center  # degrade scales offsets from the first camera

    def similar(x):
        return o + scale * (x - o)

    w = setup.wall
    wall = replace(
        w,
        meter=similar(w.meter),
        ground_y=float(similar(w.origin)[1]),
        left=scale * w.left,
        right=scale * w.right,
    )
    label = setting if setting == "exact" else f"{setting} draw {draw}"
    return PoseSetting(label, scale, frames, wall)


def app_sightings(ps: PoseSetting, cfg: dict) -> list[tuple[int, int, list[int]]]:
    """The app's wall sightings (photo, cell, rows), in capture order, from the replica gates."""
    out = []
    for i, f in enumerate(ps.frames):
        for index in cov.candidate_cells(ps.wall, f.cam, cfg):
            rows, _ = cov.app_rows(ps.wall, "wall", index, f.cam, cfg)
            if rows:
                out.append((i, index, sorted(rows)))
    return out


def driver_check(ps: PoseSetting, sightings, intervals) -> bool | None:
    """Whether the app itself (the evals' built coverage-driver) gives the same wall sightings and
    claims as the replicas; None when the binary is not there."""
    if not cov.DRIVER_BIN.exists():
        return None
    cams = {f.name: f.cam for f in ps.frames}
    app = cov.run_app(ps.wall, cams)
    got = {
        (x["keyframe"], x["index"], tuple(sorted(x["rows"])))
        for x in app["sightings"]
        if x["band"] == "wall"
    }
    mine = {(ps.frames[i].name, c, tuple(r)) for i, c, r in sightings}
    same_claims = len(app["covered"]["wall"]) == len(intervals) and np.allclose(
        np.asarray(app["covered"]["wall"], float).reshape(-1, 2),
        np.asarray(intervals, float).reshape(-1, 2),
        atol=1e-5,
    )
    return got == mine and bool(same_claims)


# The filter


class Homographies:
    """Pose homographies of the wall plane slid toward the cameras, cached per pair and offset,
    optionally with the delta = 0 map replaced by one fitted to wall feature matches."""

    def __init__(self, ps: PoseSetting, refined: dict | None = None):
        self.ps = ps
        self.n = ps.wall.outward
        self.d0 = float(ps.wall.origin @ ps.wall.outward)
        self.refined = refined or {}
        self.cache: dict = {}

    def pose(self, i: int, j: int, delta: float) -> np.ndarray:
        key = (i, j, delta)
        if key not in self.cache:
            a, b = self.ps.frames[i], self.ps.frames[j]
            self.cache[key] = plane_homography(
                a.K, a.R_wc, a.t_wc, b.K, b.R_wc, b.t_wc, self.n, self.d0 + delta
            )
        return self.cache[key]

    def warp(self, i: int, j: int, delta: float) -> np.ndarray:
        H = self.refined.get((i, j))
        if H is None:
            return self.pose(i, j, delta)
        # Keep the fitted wall map and move it by what the poses say delta does, in B's pixels.
        return self.pose(i, j, delta) @ np.linalg.inv(self.pose(i, j, 0.0)) @ H


def test_samples(
    ps: PoseSetting, sightings, cfg: dict, images: dict[int, np.ndarray], H: Homographies
) -> dict[tuple[int, int, int], list[SampleTest]]:
    """For every row the app credits a photo with, both samples tested against every other photo
    the app credits with the same row."""
    credited: dict[tuple[int, int], list[int]] = {}
    for i, cell, rows in sightings:
        for r in rows:
            credited.setdefault((cell, r), []).append(i)
    out = {}
    for i, cell, rows in sightings:
        points = cov.row_points(ps.wall, "wall", cell, cfg)
        img_a = images[i]
        for r in rows:
            tests = []
            for X in points[r]:
                uv_a = ps.frames[i].pixel(X)
                grid = patch_pixels(uv_a, PATCH_HALF)
                a = sample_bilinear(img_a, grid)
                if a is None:
                    raise RuntimeError(
                        f"{ps.frames[i].name}: credited sample's patch off the image"
                    )
                t = SampleTest(a_plain=too_plain(a, PLAIN_STD))
                for j in credited[(cell, r)]:
                    if j == i:
                        continue
                    par, qualifies = parallax_gate(
                        H.pose(i, j, 0.0), H.pose(i, j, PARALLAX_FRONT_M), uv_a, PARALLAX_MIN_PX
                    )
                    best, best_delta = float("nan"), float("nan")
                    if qualifies and not t.a_plain:
                        for delta in DELTAS_M:
                            b = sample_bilinear(
                                images[j], apply_homography(H.warp(i, j, delta), grid)
                            )
                            if b is None or too_plain(b, PLAIN_STD):
                                continue
                            score = zncc(a, b)
                            if np.isnan(best) or score > best:
                                best, best_delta = score, delta
                    t.partners[j] = (par, best, best_delta, qualifies)
                tests.append(t)
            out[(i, cell, r)] = tests
    return out


def sample_verdict(t: SampleTest, ncc_min: float) -> str | None:
    """None when the sample survives, else the cause."""
    if t.a_plain:
        return "too plain"
    qualifying = [p for p in t.partners.values() if p[3]]
    if not qualifying:
        return "no qualifying pair"
    if any(p[1] >= ncc_min for p in qualifying):
        return None
    if all(np.isnan(p[1]) for p in qualifying):
        return "too plain"
    return "disagreed"


def decide(tests, ncc_min: float) -> tuple[set, dict[tuple, Counter]]:
    """Surviving (photo, cell, row) sightings, and each dropped one's sample causes. A row survives
    only when both its samples do, as the app sees a row only when it sees both."""
    keep, causes = set(), {}
    for key, samples in tests.items():
        verdicts = [sample_verdict(t, ncc_min) for t in samples]
        if all(v is None for v in verdicts):
            keep.add(key)
        else:
            causes[key] = Counter(v for v in verdicts if v is not None)
    return keep, causes


def filtered(sightings, keep: set) -> list[tuple[int, int, list[int]]]:
    out = []
    for i, cell, rows in sightings:
        kept = [r for r in rows if (i, cell, r) in keep]
        if kept:
            out.append((i, cell, kept))
    return out


# Grading


@dataclass
class Truth:
    """Section 7's per-column truth on the true wall."""

    s: np.ndarray
    seen1: np.ndarray  # (C, R) some photo saw the sample
    seen2: np.ndarray  # (C, R) two photos 0.25 m apart saw it
    pilaster: np.ndarray  # (C,) the column's face stands proud of the plane


def truth_of(setup: cov.Setup, cfg: dict) -> Truth:
    s, _, pts = cov.band_samples(setup.wall, "wall", cfg, setup.faces)
    saw = np.stack([t.saw for t in setup.truths["wall"]], axis=-1)
    absent = np.isnan(pts[..., 0])
    return Truth(
        s,
        saw.any(axis=-1) | absent,
        cov.two_positions(saw, setup.centres, cfg["coveringBaseline"]) | absent,
        setup.faces > 0,
    )


def grade(setup, cfg, truth: Truth, ps: PoseSetting, cells: set[int]) -> dict:
    ar = covered_intervals(cells, cfg["cellWidth"], ps.wall.left, ps.wall.right)
    true_intervals = [[lo / ps.scale, hi / ps.scale] for lo, hi in ar]
    band = cov.evaluate_band(
        setup.wall,
        "wall",
        cfg,
        {"covered": {"wall": true_intervals}, "sightings": []},
        setup.views,
        [],
        setup.truths,
        setup.centres,
        setup.faces,
        causes=False,
    )
    claimed = cov.in_intervals(truth.s, true_intervals)
    false_cols = claimed & ((~truth.seen1).sum(axis=1) >= cov.UNSEEN_MIN_SAMPLES)
    missed_cols = ~claimed & truth.seen2.all(axis=1)
    ft = cov.COLUMN_M / cov.FEET
    # The per-column arrays below must add up to the harness's own grading.
    if not (
        np.isclose(false_cols.sum() * ft, band["false_observed_ft"])
        and np.isclose(missed_cols.sum() * ft, band["missed_ft"])
        and np.isclose(claimed.sum() * ft, band["claimed_ft"])
    ):
        raise RuntimeError("column grading disagrees with evals.coverage.evaluate_band")
    return {
        "band": band,
        "intervals_true_s": true_intervals,
        "claimed": claimed,
        "false": false_cols,
        "missed": missed_cols,
    }


def column_causes(
    truth: Truth, ps: PoseSetting, cfg, cols: np.ndarray, base_cells, sightings, keep, causes
) -> Counter:
    """Why each column in `cols` lost its claim: the majority cause over the dropped sightings of
    the rows that the filter left without two positions, or "the app itself" when the app did not
    claim the cell either. Counted in columns."""
    positions = np.array([f.centre for f in ps.frames])
    rows = int(cfg["rowsPerBand"])
    out: Counter = Counter()
    for c in np.flatnonzero(cols):
        cell = cov.cell_index(ps.scale * truth.s[c], cfg)
        if cell not in base_cells:
            out["the app itself"] += 1
            continue
        mine = [(i, x, rs) for i, x, rs in filtered(sightings, keep) if x == cell]
        # `record` treats rows independently, so each row is run alone as a one-row cell.
        deficient = [
            r
            for r in range(rows)
            if not covered_cells(
                [(i, 0, [0]) for i, _, rs in mine if r in rs], positions, 1, cfg["coveringBaseline"]
            )
        ]
        counts: Counter = Counter()
        for i, x, rs in sightings:
            if x == cell:
                for r in rs:
                    if r in deficient and (i, x, r) in causes:
                        counts.update(causes[(i, x, r)])
        out[cov.majority(counts, CAUSES)] += 1
    return out


def ft_of(n: int) -> float:
    return n * cov.COLUMN_M / cov.FEET


def column_runs(s: np.ndarray, mask: np.ndarray) -> list[list[float]]:
    """Stretches of s (m, column edges) where `mask` holds on consecutive columns."""
    runs: list[list[float]] = []
    for c in np.flatnonzero(mask):
        lo, hi = s[c] - cov.COLUMN_M / 2, s[c] + cov.COLUMN_M / 2
        if runs and abs(runs[-1][1] - lo) < 1e-6:
            runs[-1][1] = hi
        else:
            runs.append([lo, hi])
    return [[round(lo, 2), round(hi, 2)] for lo, hi in runs]


# Secondary: homographies refined from wall feature matches


def wall_keypoints(ps: PoseSetting, cfg, i: int, image: np.ndarray, sift):
    """SIFT keypoints of photo i whose ray meets the wall band's plane inside the marked stretch."""
    kps, desc = sift.detectAndCompute(image.astype(np.uint8), None)
    if desc is None:
        return np.zeros((0, 2)), None
    uv = np.array([k.pt for k in kps])
    f, w = ps.frames[i], ps.wall
    rays = np.linalg.inv(f.K) @ np.c_[uv, np.ones(len(uv))].T
    dirs = (f.R_wc.T @ rays).T
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((w.origin - f.centre) @ w.outward) / (dirs @ w.outward)
    X = f.centre + t[:, None] * dirs
    s, h = w.s_of(X), X[:, 1] - w.ground_y
    ok = (t > 0) & (s >= w.left) & (s <= w.right) & (h >= 0) & (h <= cfg["wallBandHeight"])
    return uv[ok], desc[ok]


def refine(ps: PoseSetting, cfg, pairs, images, H: Homographies) -> tuple[dict, int]:
    sift = cv2.SIFT_create()
    feats = {i: wall_keypoints(ps, cfg, i, images[i], sift) for i in {p for q in pairs for p in q}}
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    refined = {}
    for i, j in sorted(pairs):
        (ua, da), (ub, db) = feats[i], feats[j]
        if da is None or db is None or len(ua) < MIN_INLIERS or len(ub) < 2:
            continue
        good = [
            m for m, n2 in matcher.knnMatch(da, db, k=2) if m.distance < SIFT_RATIO * n2.distance
        ]
        if len(good) < MIN_INLIERS:
            continue
        pa = ua[[m.queryIdx for m in good]]
        pb = ub[[m.trainIdx for m in good]]
        near = np.linalg.norm(apply_homography(H.pose(i, j, 0.0), pa) - pb, axis=1) < GUIDE_PX
        if near.sum() < MIN_INLIERS:
            continue
        Hf, inl = cv2.findHomography(pa[near], pb[near], cv2.RANSAC, RANSAC_PX)
        if Hf is not None and inl.sum() >= MIN_INLIERS:
            refined[(i, j)] = Hf
    return refined, len(pairs)


# Run


def run_setting(setup, cfg, truth, ps: PoseSetting, images_of, footing_cols) -> dict:
    sightings = app_sightings(ps, cfg)
    base_cells = covered_cells(
        [(i, c, r) for i, c, r in sightings],
        np.array([f.centre for f in ps.frames]),
        int(cfg["rowsPerBand"]),
        cfg["coveringBaseline"],
    )
    base = grade(setup, cfg, truth, ps, base_cells)
    photos = sorted({i for i, _, _ in sightings})
    images = {i: images_of(i) for i in photos}
    H = Homographies(ps)
    tests = test_samples(ps, sightings, cfg, images, H)
    positions = np.array([f.centre for f in ps.frames])

    def outcome(tests_, ncc_min):
        keep, causes = decide(tests_, ncc_min)
        cells = covered_cells(
            filtered(sightings, keep), positions, int(cfg["rowsPerBand"]), cfg["coveringBaseline"]
        )
        g = grade(setup, cfg, truth, ps, cells)
        return keep, causes, cells, g

    keep, causes, _cells, g = outcome(tests, NCC_MIN)
    missed_split = column_causes(truth, ps, cfg, g["missed"], base_cells, sightings, keep, causes)
    lost_footing = footing_cols & ~g["claimed"]
    footing_split = column_causes(truth, ps, cfg, lost_footing, base_cells, sightings, keep, causes)
    # Deltas of the accepted matches on pilaster cells, and how many row sightings survived there.
    pil_cells = {cov.cell_index(ps.scale * s, cfg) for s in truth.s[truth.pilaster]}
    pil_deltas: Counter = Counter()
    for (i, cell, r), ts in tests.items():
        if cell in pil_cells and (i, cell, r) in keep:
            for t in ts:
                best = max(
                    (p for p in t.partners.values() if p[3] and p[1] >= NCC_MIN),
                    key=lambda p: p[1],
                )
                pil_deltas[f"{best[2]:g}"] += 1
    sweep = {}
    for m in NCC_SWEEP:
        gm = outcome(tests, m)[3]["band"]
        sweep[f"{m:g}"] = {
            "false_observed_ft": gm["false_observed_ft"],
            "missed_ft": gm["missed_ft"],
        }

    pairs = {(i, j) for (i, _, _), ts in tests.items() for t in ts for j in t.partners}
    refined, n_pairs = refine(ps, cfg, pairs, images, H)
    rtests = test_samples(ps, sightings, cfg, images, Homographies(ps, refined))
    rg = outcome(rtests, NCC_MIN)[3]["band"]

    row_sightings = len(tests)
    return {
        "setting": ps.label,
        "app_driver_agrees": driver_check(
            ps,
            sightings,
            covered_intervals(base_cells, cfg["cellWidth"], ps.wall.left, ps.wall.right),
        ),
        "app": summary(base["band"]),
        "filter": summary(g["band"]),
        "row_sightings": row_sightings,
        "row_sightings_kept": len(keep),
        "dropped_row_causes": dict(sum(causes.values(), Counter())),
        "missed_by_cause_ft": {k: ft_of(missed_split[k]) for k in MISSED_CAUSES},
        "footing_ft": ft_of(int(footing_cols.sum())),
        "footing_still_claimed_ft": ft_of(int((footing_cols & g["claimed"]).sum())),
        "footing_still_claimed_s_m": column_runs(truth.s, footing_cols & g["claimed"]),
        "footing_dropped_by_cause_ft": {k: ft_of(footing_split[k]) for k in MISSED_CAUSES},
        "false_outside_footing_ft": ft_of(int((g["false"] & ~footing_cols).sum())),
        "pilaster_ft": ft_of(int(truth.pilaster.sum())),
        "pilaster_claimed_app_ft": ft_of(int((truth.pilaster & base["claimed"]).sum())),
        "pilaster_claimed_filter_ft": ft_of(int((truth.pilaster & g["claimed"]).sum())),
        "pilaster_accepted_delta_samples": dict(sorted(pil_deltas.items())),
        "ncc_sweep": sweep,
        "feature_refined": {
            **summary(rg),
            "pairs": n_pairs,
            "pairs_refined": len(refined),
        },
    }


def summary(band: dict) -> dict:
    return {
        "claimed_ft": band["claimed_ft"],
        "false_observed_ft": band["false_observed_ft"],
        "missed_ft": band["missed_ft"],
    }


def measured_noise(setup) -> float:
    return float(np.median([noise_sigma(load_gray(v)) for v in setup.views]))


def peak_rss_mb() -> float:
    # getrusage reports bytes on macOS and KiB on Linux.
    divisor = 2**20 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor


def main() -> None:
    started = time.time()
    check_evals()
    cfg = dict(APP_CONFIG)
    cov.app_config = lambda: dict(APP_CONFIG)  # setup_scene would otherwise ask the Swift driver
    setup = cov.setup_scene(SCENE)
    if setup is None:
        raise SystemExit(f"{SCENE}: no wall within the app's range")
    sigma = measured_noise(setup)
    if abs(sigma - NOISE_SIGMA) > 0.1 * NOISE_SIGMA:
        raise SystemExit(f"noise floor is {sigma:.3f}, not the {NOISE_SIGMA} the threshold assumes")
    truth = truth_of(setup, cfg)

    exact = pose_setting(setup, "exact", 0)
    for f, v in zip(exact.frames, setup.views, strict=True):
        if not np.allclose(f.cam[0], setup.cams[v.name][0]):
            raise RuntimeError(f"{v.name}: exact AR pose differs from evals.coverage.to_arkit")
    base_exact = grade(
        setup,
        cfg,
        truth,
        exact,
        covered_cells(
            app_sightings(exact, cfg),
            np.array([f.centre for f in exact.frames]),
            int(cfg["rowsPerBand"]),
            cfg["coveringBaseline"],
        ),
    )
    b = base_exact["band"]
    validity = {
        "claimed_ft": b["claimed_ft"],
        "false_observed_ft": b["false_observed_ft"],
        "missed_ft": b["missed_ft"],
        "evals_section_7": {"claimed_ft": 19.3, "false_observed_ft": 1.1, "missed_ft": 0.0},
    }
    expected = validity["evals_section_7"]
    if any(abs(round(validity[k], 1) - expected[k]) > 0.1 + 1e-9 for k in expected):
        raise SystemExit(f"baseline not reproduced: {validity}")
    footing_cols = base_exact["false"]

    def images_of(i):
        return load_gray(setup.views[i])

    runs = [run_setting(setup, cfg, truth, exact, images_of, footing_cols)]
    draws = range(5)
    for d in draws:
        ps = pose_setting(setup, "modern_assumed", d)
        runs.append(run_setting(setup, cfg, truth, ps, images_of, footing_cols))
        print(f"{ps.label} done", file=sys.stderr)

    res = {
        "scene": SCENE,
        "evals_commit": EVALS_COMMIT,
        "housescankit_commit": cov.KIT_COMMIT,
        "noise_sigma_measured": sigma,
        "noise_sigma_fixed": NOISE_SIGMA,
        "plain_std": PLAIN_STD,
        "validity": validity,
        "wall_ft": (setup.wall.right - setup.wall.left) / cov.FEET,
        "runs": runs,
        "seconds": round(time.time() - started),
        "peak_rss_mb": round(peak_rss_mb()),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "plane_consistency.json").write_text(json.dumps(res, indent=1, default=_json))
    md = markdown(res)
    (RESULTS / "plane_consistency.md").write_text(md)
    print(md)


def _json(x):
    if isinstance(x, np.generic):
        return x.item()
    raise TypeError(type(x))


def _f(x: float) -> str:
    return f"{x:.1f}"


def markdown(res: dict) -> str:
    runs = res["runs"]
    modern = [r for r in runs if r["setting"] != "exact"]
    worst_fo = max(r["filter"]["false_observed_ft"] for r in modern)
    worst_missed = max(r["filter"]["missed_ft"] for r in modern)
    v = res["validity"]
    agree = {r["app_driver_agrees"] for r in runs}
    lines = [
        "# Plane consistency on ETH3D electro (generated by `uv run python run.py`)",
        "",
        f"Evals harness at {res['evals_commit']} (t3/evals); the app's view gates and `record` "
        f"replicated from HouseScanKit {res['housescankit_commit'][:7]}. The "
        f"{res['wall_ft']:.1f} ft wall and truth of evals section 7. Feet along the wall. "
        "False-observed: claimed wall with 10 cm or more of band no photo saw (pass at 0.5 or "
        "less). Missed: band two photos 0.25 m apart saw, left unclaimed (pass at 3.3 or less).",
        "",
        "## Validity",
        "",
        f"- The app without the filter, exact poses: claimed {_f(v['claimed_ft'])}, "
        f"false-observed {_f(v['false_observed_ft'])}, missed {_f(v['missed_ft'])} "
        "(evals section 7: 19.3, 1.1, 0.0).",
        "- The app's own code (the evals' built coverage-driver) agrees with the replicas on "
        "every wall sighting and claim in every pose setting: "
        + {
            frozenset({True}): "yes.",
            frozenset({None}): "not checked, driver binary absent.",
        }.get(frozenset(agree), f"NO ({[r['app_driver_agrees'] for r in runs]})."),
        f"- Too plain: patch standard deviation below {res['plain_std']:.3f} grey levels, from a "
        f"noise floor of {res['noise_sigma_fixed']} (measured this run: "
        f"{res['noise_sigma_measured']:.3f}).",
        "",
        "## The filter, graded",
        "",
        "| Poses | App claimed | App false-observed | App missed | Claimed | False-observed | Missed | Missed: no qualifying pair / too plain / disagreed / the app itself | Row sightings kept |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        a, f, m = r["app"], r["filter"], r["missed_by_cause_ft"]
        lines.append(
            f"| {r['setting']} | {_f(a['claimed_ft'])} | {_f(a['false_observed_ft'])} | "
            f"{_f(a['missed_ft'])} | {_f(f['claimed_ft'])} | {_f(f['false_observed_ft'])} | "
            f"{_f(f['missed_ft'])} | {' / '.join(_f(m[k]) for k in MISSED_CAUSES)} | "
            f"{r['row_sightings_kept']} of {r['row_sightings']} |"
        )
    lines += [
        "",
        f"Worst `modern_assumed` draw: false-observed {_f(worst_fo)} "
        f"({'PASS' if worst_fo <= 0.5 else 'FAIL'}), missed {_f(worst_missed)} "
        f"({'PASS' if worst_missed <= 3.3 else 'FAIL'}).",
        "",
        "## Footings and pilasters",
        "",
        "Footing columns: the columns the app claims falsely with exact poses. Pilaster columns: "
        "the wall face stands 0.36 m proud of the tapped plane.",
        "",
        "| Poses | Footing ft | Still claimed | At s (m) | Dropped: no qualifying pair / too plain / disagreed | False-observed elsewhere | Pilaster ft | Claimed by app | Claimed by filter | Accepted delta (m: samples) on pilaster cells |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        fd = r["footing_dropped_by_cause_ft"]
        deltas = ", ".join(f"{k}: {n}" for k, n in r["pilaster_accepted_delta_samples"].items())
        at = ", ".join(f"{lo:g} to {hi:g}" for lo, hi in r["footing_still_claimed_s_m"])
        lines.append(
            f"| {r['setting']} | {_f(r['footing_ft'])} | {_f(r['footing_still_claimed_ft'])} | "
            f"{at or 'none'} | "
            f"{' / '.join(_f(fd[k]) for k in CAUSES)} | {_f(r['false_outside_footing_ft'])} | "
            f"{_f(r['pilaster_ft'])} | {_f(r['pilaster_claimed_app_ft'])} | "
            f"{_f(r['pilaster_claimed_filter_ft'])} | {deltas or 'none'} |"
        )
    lines += [
        "",
        "## Secondary, not graded",
        "",
        "### NCC threshold: false-observed / missed",
        "",
        "| Poses | " + " | ".join(f"NCC {m:g}" for m in NCC_SWEEP) + " |",
        "| --- |" + " --- |" * len(NCC_SWEEP),
    ]
    for r in runs:
        cells = [
            f"{_f(r['ncc_sweep'][f'{m:g}']['false_observed_ft'])} / "
            f"{_f(r['ncc_sweep'][f'{m:g}']['missed_ft'])}"
            for m in NCC_SWEEP
        ]
        lines.append(f"| {r['setting']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "### Wall homography fitted to SIFT matches instead of taken from the poses",
        "",
        f"Matches must pass Lowe's ratio at {SIFT_RATIO}, lie on the wall band's image, and land "
        f"within {GUIDE_PX:g} px of the pose homography's prediction; RANSAC at {RANSAC_PX:g} px "
        f"needs {MIN_INLIERS} inliers, else the pair keeps its pose homography. The offsets "
        "delta still come from the poses.",
        "",
        "| Poses | Claimed | False-observed | Missed | Pairs refined |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        fr = r["feature_refined"]
        lines.append(
            f"| {r['setting']} | {_f(fr['claimed_ft'])} | {_f(fr['false_observed_ft'])} | "
            f"{_f(fr['missed_ft'])} | {fr['pairs_refined']} of {fr['pairs']} |"
        )
    lines += ["", f"Run time {res['seconds']} s, peak memory {res['peak_rss_mb']} MB."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
