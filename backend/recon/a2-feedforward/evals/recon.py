"""Score reconstructions of ETH3D's facade and electro scenes against their laser scans.

    uv run python -m evals.recon prepare            # images at eval size, visibility, normals, groups
    uv run python -m evals.recon score              # every method with predictions on disk
    uv run python -m evals.recon sensitivity        # key rows at other visibility tolerances

`prepare` writes, under ~/house-scanning-data/evals/eth3d/<scene>/:
- `images_1024/<view>.jpg`: the undistorted images at 1024 px wide, the input to every model;
- `model_inputs/images.txt` and `intrinsics.json`: the arguments the model runners take;
- `visibility_1024_tol<N>/<view>.npz`: the evaluation points each view sees, with edge flags;
- `normals.npy`: a surface normal per evaluation point, to find vertical surfaces;
- `subsets.json`: 8 seed views spread through the capture, each with its 1, 3 and 7 nearest cameras
  that look the same way, giving groups of 1, 2, 4 and 8 views.

Evaluation sets are fixed per seed view, so adding views or changing method never changes what is
scored: the points the seed sees (within 6 m of the seed camera for the near slice), split into
cohorts (surface interior, vertical-surface interior, depth edges), with test pairs and taped
reference pairs drawn once. Other views of a group only add predictions for those points, and in
the near slice a view contributes a point only if that view's own camera is within 6 m of it.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from evals.eth3d import (
    SCENES,
    View,
    excluded_pixels,
    occluder_points,
    point_normals,
    read_views,
    resized_image,
    scan_points,
    up_direction,
    visible_scan_points,
)
from evals.pairs import BINS_M, Points, bin_key, evaluate_fixed, pool, sample_pairs
from evals.paths import ETH3D_DIR, EVALS_DIR
from evals.triangulate import sample_depth

WIDTH = 1024
EVAL_POINTS = 1_500_000
GROUP_SIZES = (1, 2, 4, 8)
SEEDS_PER_SCENE = 8
NEAR_M = 6.0
RANGES = {"all points": None, "within 6 m": NEAR_M}
COHORTS = ("surface interior", "vertical interior", "edges")
VERTICAL_MAX_UP = 0.3  # |normal . up| below this: a vertical surface (within 17 degrees)
PAIRS_PER_BIN = 3000
TAPE_REFS = 25
TOLERANCE = 0.04
PREDICTIONS = EVALS_DIR / "predictions"

# per_view(view name) -> (depth HxW, K 3x3, cam-to-world 4x4, centred). `centred` True: the pose is
# metric (true or AR-like), so scaling depth moves points along rays from the camera centre;
# False: the pose is in a model's own frame and the whole reconstruction scales about its origin.
# Depth placed with the dataset's (or AR-like) poses is back-projected with the dataset's K
# (`Scene.K`): the pixels are the real camera's. MoGe-2's own K puts the principal point at the
# image centre, 0.8 to 3.6 px from ETH3D's at 1024 px wide; the model was given only the fov.
PerView = Callable[[str], tuple[np.ndarray, np.ndarray, np.ndarray, bool]]
# method(group id, members) -> PerView, or None when that method has no output for the group.
Method = Callable[[str, list[str]], PerView | None]


def _height(view: View) -> int:
    return round(view.height * WIDTH / view.width)


def eval_candidates(scene: str) -> np.ndarray:
    """A fixed random subset of the scan points used for scoring (all points still block views)."""
    pts = scan_points(scene)
    rng = np.random.default_rng(12345)
    n = min(EVAL_POINTS, len(pts))
    return pts[np.sort(rng.choice(len(pts), size=n, replace=False))]


def view_groups(views: list[View]) -> dict[str, list[list[str]]]:
    seeds = [views[i] for i in np.linspace(0, len(views) - 1, SEEDS_PER_SCENE).round().astype(int)]
    groups: dict[str, list[list[str]]] = {}
    for n in GROUP_SIZES:
        groups[str(n)] = []
        for seed in seeds:
            similar = [v for v in views if v.name != seed.name and v.forward @ seed.forward > 0.5]
            similar.sort(key=lambda v: np.linalg.norm(v.center - seed.center))
            members = [seed.name] + [v.name for v in similar[: n - 1]]
            if len(members) == n:
                groups[str(n)].append(members)
    return groups


def visibility_dir(scene: str, tolerance: float) -> Path:
    return ETH3D_DIR / scene / f"visibility_{WIDTH}_tol{round(tolerance * 100)}"


def prepare(scene: str, tolerance: float = TOLERANCE) -> None:
    scene_dir = ETH3D_DIR / scene
    views = read_views(scene_dir)
    cands = eval_candidates(scene)
    blockers = np.concatenate([scan_points(scene), occluder_points(scene)])
    vis_dir = visibility_dir(scene, tolerance)
    vis_dir.mkdir(exist_ok=True)
    inputs = scene_dir / "model_inputs"
    inputs.mkdir(exist_ok=True)
    listing, intr = [], {}
    for v in views:
        _, w, h = resized_image(v, WIDTH)
        path = scene_dir / f"images_{WIDTH}" / f"{v.name}.jpg"
        listing.append(str(path))
        K = v.scaled_K(w, h)
        intr[str(path)] = [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]
        out = vis_dir / f"{v.name}.npz"
        if not out.exists():
            vis = visible_scan_points(
                blockers, cands, v, w, h, excluded_pixels(v, w, h), tolerance=tolerance
            )
            np.savez_compressed(out, index=vis.index, uv=vis.uv.astype(np.float32), edge=vis.edge)
    (inputs / "images.txt").write_text("\n".join(listing) + "\n")
    (inputs / "intrinsics.json").write_text(json.dumps(intr, indent=1))
    (scene_dir / "subsets.json").write_text(json.dumps(view_groups(views), indent=1))
    normals = scene_dir / "normals.npy"
    if not normals.exists():
        np.save(normals, point_normals(cands).astype(np.float32))
    print(f"{scene}: {len(views)} views, {len(cands)} evaluation points, tolerance {tolerance}")


def camera_points(depth: np.ndarray, K: np.ndarray, uv: np.ndarray) -> np.ndarray:
    z = sample_depth(depth, uv)
    x = (uv[:, 0] - K[0, 2]) / K[0, 0] * z
    y = (uv[:, 1] - K[1, 2]) / K[1, 1] * z
    return np.c_[x, y, z]


def load_prediction(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    z = np.load(path)
    depth = np.where(z["valid"], z["depth"], np.nan) if "valid" in z else z["depth"]
    fx, fy, cx, cy = map(float, z["intrinsics"])
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
    return depth, K, z.get("cam_to_world", None)


class Scene:
    def __init__(self, name: str, tolerance: float = TOLERANCE):
        self.name = name
        self.dir = ETH3D_DIR / name
        self.views = {v.name: v for v in read_views(self.dir)}
        self.cands = eval_candidates(name)
        self.groups = json.loads((self.dir / "subsets.json").read_text())
        up = up_direction(list(self.views.values()))
        self.vertical = np.abs(np.load(self.dir / "normals.npy") @ up) < VERTICAL_MAX_UP
        self.vis_dir = visibility_dir(name, tolerance)
        self._vis: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._oracle: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def K(self, view: str) -> np.ndarray:
        """The dataset's intrinsics for the view at the evaluation width."""
        v = self.views[view]
        return v.scaled_K(WIDTH, _height(v))

    def visible(self, view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if view not in self._vis:
            z = np.load(self.vis_dir / f"{view}.npz")
            self._vis[view] = (z["index"], z["uv"].astype(np.float64), z["edge"])
        return self._vis[view]

    def oracle_depth(self, view: str) -> tuple[np.ndarray, np.ndarray]:
        """Depth map splatted from every scan point with the same z-buffer as the visibility test,
        holes closed by a 5x5 minimum filter."""
        if view in self._oracle:
            return self._oracle[view]
        v = self.views[view]
        h = _height(v)
        K = v.scaled_K(WIDTH, h)
        pts = scan_points(self.name)
        Xc = pts @ v.R_wc.T + v.t_wc
        z = Xc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = np.round(K[0, 0] * Xc[:, 0] / z + K[0, 2]).astype(np.int64)
            w = np.round(K[1, 1] * Xc[:, 1] / z + K[1, 2]).astype(np.int64)
        ok = (z > 0.1) & (u >= 0) & (u < WIDTH) & (w >= 0) & (w < h)
        buf = np.full((h, WIDTH), 1e6, np.float32)
        np.minimum.at(buf, (w[ok], u[ok]), z[ok].astype(np.float32))
        buf = cv2.erode(buf, np.ones((5, 5), np.uint8))
        buf[buf >= 1e6] = np.nan
        self._oracle[view] = (buf, K)
        return buf, K


class EvalSet:
    """What one seed view's evaluation scores: its points, cohorts, test pairs and tape pairs."""

    def __init__(self, scene: Scene, seed: str, max_range: float | None):
        index, _, edge = scene.visible(seed)
        keep = np.ones(len(index), bool)
        if max_range is not None:
            centre = scene.views[seed].center
            keep = np.linalg.norm(scene.cands[index] - centre, axis=1) <= max_range
        self.index = index[keep]
        self.gt = scene.cands[self.index].astype(np.float64)
        edge = edge[keep]
        members = {
            "surface interior": ~edge,
            "vertical interior": ~edge & scene.vertical[self.index],
            "edges": edge,
        }
        # One generator per seed and range: the same draw for every method and view count.
        rng = np.random.default_rng(sum(ord(c) for c in f"{scene.name}{seed}{max_range}"))
        self.pairs: dict[str, dict[str, np.ndarray]] = {}
        for cohort, mask in members.items():
            where = np.flatnonzero(mask)
            self.pairs[cohort] = {}
            if len(where) < 2:
                continue
            for b in BINS_M:
                pr = sample_pairs(self.gt[where], *b, PAIRS_PER_BIN, rng)
                if len(pr):
                    self.pairs[cohort][bin_key(b)] = where[pr]
        interior = np.flatnonzero(members["surface interior"])
        refs = np.empty((0, 2), int)
        if len(interior) > 1:
            refs = sample_pairs(self.gt[interior], 1.0, 3.0, TAPE_REFS, rng)
        self.refs = interior[refs] if len(refs) else np.empty((0, 2), int)


def predict(
    scene: Scene, ev: EvalSet, members: list[str], per_view: PerView, max_range: float | None
) -> Points:
    """Each evaluation point averaged over the views of the group that see it (and, with
    `max_range`, whose own camera is that close to it). NaN where no view predicts it."""
    lookup = np.full(len(scene.cands), -1)
    lookup[ev.index] = np.arange(len(ev.index))
    c_sum = np.zeros((len(ev.index), 3))
    r_sum = np.zeros((len(ev.index), 3))
    count = np.zeros(len(ev.index))
    for name in members:
        index, uv, _ = scene.visible(name)
        pos = lookup[index]
        sel = pos >= 0
        if max_range is not None:
            dist = np.linalg.norm(scene.cands[index] - scene.views[name].center, axis=1)
            sel &= dist <= max_range
        if not sel.any():
            continue
        depth, K, T, centred = per_view(name)
        pc = camera_points(depth, K, uv[sel])
        good = np.isfinite(pc).all(axis=1)
        pos = pos[sel][good]
        world = pc[good] @ T[:3, :3].T
        if centred:
            np.add.at(c_sum, pos, np.broadcast_to(T[:3, 3], world.shape))
            np.add.at(r_sum, pos, world)
        else:
            np.add.at(r_sum, pos, world + T[:3, 3])
        np.add.at(count, pos, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        c = c_sum / count[:, None]
        r = r_sum / count[:, None]
    return Points(gt=ev.gt, c=c, r=r)


def _evaluate_into(raws: dict, scene: Scene, ev: EvalSet, members, per_view, max_range) -> None:
    pts = predict(scene, ev, members, per_view, max_range)
    for cohort in COHORTS:
        if ev.pairs[cohort]:
            raws[cohort].append(evaluate_fixed(pts, ev.pairs[cohort], ev.refs))


def score_groups(scene: Scene, methods: dict[str, Method], sizes=GROUP_SIZES) -> dict:
    """{range: {method: {views: {cohort: pooled summary}}}} on the fixed per-seed evaluation sets."""
    out: dict = {}
    for range_name, max_range in RANGES.items():
        sets = {m[0]: EvalSet(scene, m[0], max_range) for m in scene.groups["1"]}
        out[range_name] = {}
        for method, factory in methods.items():
            res: dict = {}
            for n in map(str, sizes):
                raws: dict[str, list] = {c: [] for c in COHORTS}
                for members in scene.groups.get(n, []):
                    per_view = factory(f"n{n}-{members[0]}", members)
                    if per_view is not None:
                        _evaluate_into(raws, scene, sets[members[0]], members, per_view, max_range)
                if raws["surface interior"]:
                    res[n] = {c: pool(r) | {"groups": len(r)} for c, r in raws.items() if r}
            if res:
                out[range_name][method] = res
    return out


def score_single_photos(scene: Scene, model: str) -> dict:
    """Every photo alone (not only the seeds), each on its own fixed evaluation set."""
    root = PREDICTIONS / scene.name / model

    def per_view(name):
        d, _, _ = load_prediction(root / f"{name}.npz")
        return d, scene.K(name), scene.views[name].cam_to_world, True

    out: dict = {}
    for range_name, max_range in RANGES.items():
        raws: dict[str, list] = {c: [] for c in COHORTS}
        for name in scene.views:
            ev = EvalSet(scene, name, max_range)
            if len(ev.index) > 1:
                _evaluate_into(raws, scene, ev, [name], per_view, max_range)
        out[range_name] = {c: pool(r) | {"photos": len(r)} for c, r in raws.items() if r}
    return out


def true_pose_methods(scene: Scene) -> dict[str, Method]:
    """The oracle and per-photo depth placed with the true poses."""
    pred_root = PREDICTIONS / scene.name

    def oracle(gid, members):
        def pv(name):
            d, K = scene.oracle_depth(name)
            return d, K, scene.views[name].cam_to_world, True

        return pv

    methods: dict[str, Method] = {"oracle": oracle}
    for model in ("moge2", "da3metric"):
        if not (pred_root / model).exists():
            continue

        def fused(gid, members, model=model):
            def pv(name):
                d, _, _ = load_prediction(pred_root / model / f"{name}.npz")
                return d, scene.K(name), scene.views[name].cam_to_world, True

            return pv

        methods[f"fused/{model}"] = fused
    return methods


def model_frame_method(root: Path) -> Method:
    """A multi-view model's own output per group, in the frame and scale it chose."""

    def method(gid, members):
        run = root / gid
        if not (run / "run.json").exists():
            return None

        def pv(name):
            d, K, T = load_prediction(run / f"{name}.npz")
            return d, K, T, False

        return pv

    return method


LABELS = {
    "oracle": "scan rendered as depth (evaluation floor)",
    "fused/moge2": "MoGe-2 per photo + true poses",
    "fused/da3metric": "Depth Anything 3 metric per photo + true poses",
}
SINGLE_LABELS = {
    "moge2": "MoGe-2, one photo (every photo)",
    "da3metric": "Depth Anything 3 metric, one photo (every photo)",
}


def cell(x: dict | None) -> str:
    if not x or x.get("pairs", 0) == 0:
        return "n/a"
    if not np.isfinite(x["p90_in"]):
        return f"{x['median_in']:.1f} / fails ({x['failed_pct']:.0f}% failed)"
    return f"{x['median_in']:.1f} / {x['p90_in']:.1f}"


def _scale(x: dict | None) -> str:
    v = (x or {}).get("scale_error_pct")
    return "n/a" if v is None else f"{v:+.1f}%"


def table(rows: list[tuple[str, str, dict]], cohort: str) -> list[str]:
    """Rows of (label, views, {cohort: pooled}) as one markdown table."""
    lines = [
        "| Method | Views | Model scale: 1-3 m | 3-10 m | Scale error: 1-3 m | 3-10 m "
        "| One taped distance: 1-3 m | 3-10 m | Tape calibrated |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, n, res in rows:
        r = res.get(cohort)
        if not r:
            continue
        a, b = r["none"].get("1-3m"), r["none"].get("3-10m")
        ta, tb = r["one_known_distance"].get("1-3m"), r["one_known_distance"].get("3-10m")
        ok = r.get("tape_calibration_success_pct")
        lines.append(
            f"| {label} | {n} | {cell(a)} | {cell(b)} | {_scale(a)} | {_scale(b)} | {cell(ta)} "
            f"| {cell(tb)} | {'n/a' if ok is None else f'{ok:.0f}%'} |"
        )
    return lines


HEADER = (
    "|Error in the distance between two scanned surface points|, inches, median / p90, against the "
    "laser scan. Scale error: median of predicted / true length, minus 1, per span bin. Failed "
    "pairs (no prediction, or a taped reference no scale could match) count as infinite error; "
    "'fails' means more than 10% failed. Cohorts: surface interior (any scanned surface away from "
    "depth edges), vertical interior (walls, fences and other vertical surfaces), edges (the near "
    "side of depth edges: window frames, equipment and fence edges)."
)


def markdown(results: dict) -> str:
    lines = ["# ETH3D reconstruction accuracy (generated by `uv run python -m evals.recon score`)"]
    lines += ["", HEADER]
    for scene, res in results.items():
        for range_name in RANGES:
            rows = [
                (SINGLE_LABELS[m], "1", per_range[range_name])
                for m, per_range in res["single"].items()
            ]
            for m, per_n in res["groups"][range_name].items():
                rows += [(LABELS.get(m, m), n, r) for n, r in per_n.items()]
            for cohort in COHORTS:
                lines += ["", f"## {scene}, {range_name}, {cohort}", ""]
                lines += table(rows, cohort)
    return "\n".join(lines) + "\n"


def score(tolerance: float = TOLERANCE) -> dict:
    results = {}
    for s in SCENES:
        scene = Scene(s, tolerance)
        results[s] = {
            "groups": score_groups(scene, true_pose_methods(scene)),
            "single": {
                m: score_single_photos(scene, m)
                for m in ("moge2", "da3metric")
                if (PREDICTIONS / s / m).exists()
            },
        }
    return results


def sensitivity() -> str:
    """Key rows at visibility tolerances of 2, 4 and 8% (each prepared on first use)."""
    lines = [
        "# Sensitivity to the visibility tolerance (generated by `uv run python -m evals.recon "
        "sensitivity`)",
        "",
        "Surface interior, within 6 m. One taped distance, median / p90 inches, and the model-scale "
        "error. A point counts as visible when it lies within the tolerance of the nearest scanned "
        "depth along its pixel.",
        "",
        "| Scene | Method | Views | Tolerance | Taped 1-3 m | Taped 3-10 m | Model scale error, 3-10 m pairs |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in SCENES:
        for tol in (0.02, 0.04, 0.08):
            prepare(s, tol)
            scene = Scene(s, tol)
            res = score_groups(scene, true_pose_methods(scene), sizes=(1, 2))["within 6 m"]
            for m, per_n in res.items():
                for n, r in per_n.items():
                    t = r["surface interior"]["one_known_distance"]
                    lines.append(
                        f"| {s} | {LABELS[m]} | {n} | {tol:.0%} | {cell(t.get('1-3m'))} | "
                        f"{cell(t.get('3-10m'))} | "
                        f"{_scale(r['surface interior']['none'].get('3-10m'))} |"
                    )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step", choices=["prepare", "score", "sensitivity"])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "results")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.step == "prepare":
        for s in SCENES:
            prepare(s)
    elif args.step == "score":
        results = score()
        (args.out / "eth3d_recon.json").write_text(json.dumps(results, indent=1))
        md = markdown(results)
        (args.out / "eth3d_recon.md").write_text(md)
        print(md)
    else:
        md = sensitivity()
        (args.out / "eth3d_visibility_sensitivity.md").write_text(md)
        print(md)


if __name__ == "__main__":
    main()
