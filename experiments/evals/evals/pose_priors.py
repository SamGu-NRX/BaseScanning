"""Can the phone's AR poses fix the learned models' scale? ETH3D, poses degraded to AR-like error.

    uv run python -m evals.pose_priors prepare    # AR-like poses per group and noise draw
    make pose-priors                              # all of it, including MapAnything runs
    uv run python -m evals.pose_priors score      # -> results/pose_priors.md

Two ways to use the poses, at each pose setting in `evals.ar_poses`:
- (a) MapAnything given the poses and the intrinsics as priors, one joint run per group. Run at
  392 px on the long side, 2 and 4 views: its native 518 px, or 8 views, needs more than the
  roughly 4 GB one process may use on this shared machine. The same runs without poses are the
  baseline, so the comparison holds resolution and precision fixed.
- (b) MoGe-2 per-photo metric depth, each photo rescaled to the points triangulated from features
  matched across the group with those poses (`evals.triangulate`), then placed with the same poses
  and fused.
As a control, MoGe-2 depth placed with the same poses but not rescaled shows what the rescale adds.
Everything is scored on the fixed evaluation sets of `evals.recon`.

Uncertainty. A row rests on a few seed groups (6 to 8) and, for settings with pose noise, on
`NOISE_DRAWS` draws of that noise per group. The point estimate pools every group's and draw's
errors. The 95% interval is a two-stage bootstrap: draw the groups with replacement, then for each
group drawn its noise draws with replacement, pool the errors drawn, and take the median and p90.
MapAnything's weights are no longer on this machine, so its rows keep their single noise draw.

`prepare` writes the poses to eth3d/<scene>/ar_poses/<setting>.json (draw 0, the file MapAnything
reads) and <setting>.draw<k>.json. `score` caches each group's triangulated scale factors in
eth3d/<scene>/triangulation_fits.json, keyed by a hash of the poses, intrinsics and threshold that
produced them, so rescoring does not refit and changed inputs do.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from evals import triangulate
from evals.ar_poses import ADVIO_ARKIT_SCALES, SETTINGS, group_poses, noise_draws
from evals.eth3d import SCENES, read_views
from evals.pairs import INCH, evaluate_fixed, pool, results_json
from evals.paths import ETH3D_DIR
from evals.recon import (
    COHORTS,
    HEADER,
    PREDICTIONS,
    RANGES,
    WIDTH,
    EvalSet,
    Method,
    Scene,
    cell,
    comparable_groups,
    load_prediction,
    model_frame_method,
    predict,
    require_complete,
)
from evals.triangulate import view_scales

GROUP_SIZES = (2, 4, 8)  # triangulation needs at least two photos
MODEL = "moge2"
BOOTSTRAP_REPS = 1000
# Percentiles interpolate and inf - inf is NaN, so failures become a finite sentinel (as in
# `evals.pairs.summarize`) and any result that touches one is reported as a failure.
SENTINEL = 1e12
# The published MapAnything advio_2018 runs were given the poses of the earlier advio_2018 setting,
# whose scales were ARKit against ARCore (0.883, 0.936, 0.958). Those runs predate the pose hash in
# run.json, so a run without one keeps this label; a run that records its poses is labelled by them.
HISTORICAL_MAPANYTHING = {"advio_2018": "advio_2018 poses, earlier ARCore-referenced scales"}
HISTORICAL_NOTE = (
    " Its advio_2018 row was run on the earlier advio_2018 poses, whose scales were ARKit against "
    "ARCore (0.883, 0.936, 0.958), with the same noise as draw 0 here."
)


def pose_file(scene: str, setting: str, draw: int = 0) -> Path:
    name = setting if draw == 0 else f"{setting}.draw{draw}"
    return ETH3D_DIR / scene / "ar_poses" / f"{name}.json"


def prepare(scene: str) -> None:
    views = {v.name: v for v in read_views(ETH3D_DIR / scene)}
    groups = json.loads((ETH3D_DIR / scene / "subsets.json").read_text())
    groups = {n: g for n, g in groups.items() if int(n) in GROUP_SIZES}
    for setting in SETTINGS:
        for draw in noise_draws(setting):
            path = pose_file(scene, setting, draw)
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(group_poses(views, groups, setting, draw)))
    count = sum(map(len, groups.values()))
    draws = {s: len(noise_draws(s)) for s in SETTINGS}
    print(f"{scene}: AR-like poses for {count} groups, noise draws per setting {draws}")


class FitCache:
    """Per-photo triangulation scales, stored with a hash of the inputs that produced them."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict = json.loads(path.read_text()) if path.exists() else {}
        self.changed = False

    def get(self, key: str, inputs: str, fit) -> dict[str, float | None]:
        entry = self.entries.get(key)
        if entry is None or entry["inputs"] != inputs:
            self.entries[key] = {"inputs": inputs, "scales": fit()}
            self.changed = True
        return self.entries[key]["scales"]

    def save(self) -> None:
        if self.changed:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries, indent=0))
            tmp.replace(self.path)
            self.changed = False


@functools.cache
def _file_digest(path: Path, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, computed once per (path, modification time, size)."""
    st = path.stat()
    return _file_digest(path, st.st_mtime_ns, st.st_size)


def fit_inputs(
    T: dict,
    K: dict,
    max_reproj_px: float,
    images: dict[str, Path],
    depths: dict[str, Path],
    *,
    min_points: int = 20,
    min_angle_deg: float = 2.0,
) -> str:
    """Everything a group's scale fit reads: poses, intrinsics, the threshold, the matching
    settings (`triangulate.RATIO`, `triangulate.MAX_FEATURES`, and the point count and angle
    floors `view_scales` is called with, whose defaults these mirror), and the bytes of each
    member's image and depth prediction. Changing any of them gives a new key, so new fits
    replace the cached ones instead of silently reusing them."""
    spec = {
        "model": MODEL,
        "poses": {m: np.asarray(t).tolist() for m, t in T.items()},
        "K": {m: np.asarray(k).tolist() for m, k in K.items()},
        "max_reproj_px": max_reproj_px,
        "match_ratio": triangulate.RATIO,
        "match_max_features": triangulate.MAX_FEATURES,
        "min_points": min_points,
        "min_angle_deg": min_angle_deg,
        "images": {m: file_digest(p) for m, p in images.items()},
        "depths": {m: file_digest(p) for m, p in depths.items()},
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def mapanything_label(run_root: Path, setting: str, poses_file: Path) -> str:
    """A MapAnything row's label, from the poses its groups' run.json files record. Runs that
    record no pose hash are the published historical ones; runs that record one must all match
    the current pose file, or the row would mix poses."""
    recorded = {json.loads(p.read_text()).get("poses_sha256") for p in run_root.glob("*/run.json")}
    if recorded <= {None}:
        return HISTORICAL_MAPANYTHING.get(setting, f"{setting} poses")
    if recorded != {file_digest(poses_file)}:
        raise SystemExit(
            f"{run_root}: its groups were run on poses other than {poses_file} (or a mix of "
            f"recorded and unrecorded runs); rerun `make pose-priors` for {setting}"
        )
    return f"{setting} poses"


def uses_historical_rows(results: dict) -> bool:
    labels = {f"mapanything@392 + {label}" for label in HISTORICAL_MAPANYTHING.values()}
    return any(
        method in labels
        for ranges in results.values()
        for per_method in ranges.values()
        for method in per_method
    )


def pose_methods(scene: Scene, fits: FitCache) -> dict[str, list[Method]]:
    """{row label: one Method per noise draw}."""
    root = PREDICTIONS / scene.name
    gray: dict[str, np.ndarray] = {}
    depths: dict[str, np.ndarray] = {}

    def image_path(name):
        return scene.dir / f"images_{WIDTH}" / f"{name}.jpg"

    def depth_path(name):
        return root / MODEL / f"{name}.npz"

    def image(name):
        if name not in gray:
            gray[name] = cv2.imread(str(image_path(name)), 0)
        return gray[name]

    def depth(name):
        if name not in depths:
            depths[name] = load_prediction(depth_path(name))[0]
        return depths[name]

    def fused_with(setting: str, draw: int, rescale: bool) -> Method:
        poses_by_group = json.loads(pose_file(scene.name, setting, draw).read_text())

        def method(gid, members):
            if gid not in poses_by_group:
                return None
            T = {m: np.array(poses_by_group[gid]["poses"][m]) for m in members}
            scale: dict[str, float | None] = {m: 1.0 for m in members}
            if rescale:
                K = {m: scene.K(m) for m in members}
                px = SETTINGS[setting].reprojection_px

                def fit():
                    f = view_scales(
                        {m: image(m) for m in members},
                        K,
                        T,
                        {m: depth(m) for m in members},
                        max_reproj_px=px,
                    )
                    return {m: f[m].scale for m in members}

                key = fit_inputs(
                    T,
                    K,
                    px,
                    {m: image_path(m) for m in members},
                    {m: depth_path(m) for m in members},
                )
                scale = fits.get(f"{setting}/draw{draw}/{gid}", key, fit)

            def pv(name):
                d, s = depth(name), scale[name]
                return (
                    (d * s if s is not None else np.full_like(d, np.nan)),
                    scene.K(name),
                    T[name],
                    True,
                )

            return pv

        return method

    methods: dict[str, list[Method]] = {
        "mapanything@392, images only": [model_frame_method(root / "ma392_images")]
    }
    for setting in SETTINGS:
        label = mapanything_label(
            root / f"ma392_{setting}", setting, pose_file(scene.name, setting)
        )
        methods[f"mapanything@392 + {label}"] = [model_frame_method(root / f"ma392_{setting}")]
    for setting in SETTINGS:
        draws = noise_draws(setting)
        methods[f"MoGe-2 placed with {setting} poses"] = [
            fused_with(setting, d, rescale=False) for d in draws
        ]
        methods[f"MoGe-2 rescaled by triangulation, {setting} poses"] = [
            fused_with(setting, d, rescale=True) for d in draws
        ]
    return methods


def pooled_percentiles(
    sorted_values: np.ndarray, sorted_unit: np.ndarray, weights: np.ndarray, qs
) -> np.ndarray:
    """np.percentile (linear) of the multiset in which each value appears `weights[unit]` times.
    `sorted_values` ascending, finite (failures as SENTINEL); `sorted_unit` the unit of each."""
    cum = np.cumsum(weights[sorted_unit])
    n = cum[-1]
    h = np.asarray(qs, float) / 100 * (n - 1)
    lo = np.floor(h)
    frac = h - lo
    # The k-th value (0-based) of the expanded multiset is the first whose cumulative count > k.
    a = sorted_values[np.searchsorted(cum, lo, side="right")]
    b = sorted_values[np.searchsorted(cum, np.minimum(lo + 1, n - 1), side="right")]
    out = a + frac * (b - a)
    return np.where((a >= SENTINEL) | ((b >= SENTINEL) & (frac > 0)), np.inf, out)


def bootstrap_interval(
    units: list[list[np.ndarray]], qs=(50, 90), reps: int = BOOTSTRAP_REPS, seed: int = 0
) -> list[tuple[float, float]]:
    """95% interval of the pooled percentiles `qs` of `units[group][draw]` (|errors|, inf for a
    failure). Each replicate draws the groups with replacement, then for each group drawn its noise
    draws with replacement, and pools every value drawn. Interval ends are order statistics of the
    replicates (no interpolation, so a replicate that fails cannot blur into a finite end)."""
    values, unit_of, unit_ids = [], [], []
    u = 0
    for draws in units:
        ids = []
        for v in draws:
            values.append(np.asarray(v, float))
            unit_of.append(np.full(len(v), u))
            ids.append(u)
            u += 1
        unit_ids.append(np.array(ids))
    v = np.concatenate(values)
    unit = np.concatenate(unit_of)
    v = np.where(np.isfinite(v), v, SENTINEL)
    order = np.argsort(v, kind="stable")
    v, unit = v[order], unit[order]
    rng = np.random.default_rng(seed)
    reps_out = np.empty((reps, len(qs)))
    for r in range(reps):
        w = np.zeros(u, np.int64)
        for g in rng.integers(0, len(units), len(units)):
            ids = unit_ids[g]
            np.add.at(w, ids[rng.integers(0, len(ids), len(ids))], 1)
        reps_out[r] = pooled_percentiles(v, unit, w, qs)
    lo, hi = np.quantile(reps_out, [0.025, 0.975], axis=0, method="inverted_cdf")
    return [(_round(a), _round(b)) for a, b in zip(lo, hi, strict=True)]


def _round(x: float) -> float:
    return float(x) if not np.isfinite(x) else round(float(x), 2)


def summarize_groups(units: list[list[dict]]) -> dict:
    """`units[group][draw]` are `evaluate_fixed` results. Pools them all (`evals.pairs.pool`) and
    adds a 95% interval for the model-scale median and p90 per span bin."""
    out = pool([r for draws in units for r in draws])
    out["groups"] = len(units)
    out["noise_draws"] = max(len(draws) for draws in units)
    out["interval_95"] = {}
    for key in out["none"]:
        errs = [
            [np.abs(r["none"][key][0]) / INCH for r in draws if key in r["none"]] for draws in units
        ]
        errs = [e for e in errs if e]
        (m_lo, m_hi), (p_lo, p_hi) = bootstrap_interval(errs)
        out["interval_95"][key] = {"median_in": [m_lo, m_hi], "p90_in": [p_lo, p_hi]}
    return out


def score_scene(scene: Scene, methods: dict[str, list[Method]], sizes=GROUP_SIZES) -> dict:
    """{range: {method: {views: {cohort: summary}}}} on `evals.recon`'s fixed evaluation sets."""
    out: dict = {}
    for range_name, max_range in RANGES.items():
        sets = {m[0]: EvalSet(scene, m[0], max_range) for m in scene.groups["1"]}
        out[range_name] = {}
        for method, draws in methods.items():
            res: dict = {}
            groups = comparable_groups(scene.groups, sizes)
            for n in map(str, sizes):
                units: dict[str, list[list[dict]]] = {c: [] for c in COHORTS}
                missing = []
                for members in groups.get(n, []):
                    ev = sets[members[0]]
                    per_cohort: dict[str, list[dict]] = {c: [] for c in COHORTS}
                    for d, factory in enumerate(draws):
                        per_view = factory(f"n{n}-{members[0]}", members)
                        if per_view is None:
                            missing.append(f"n{n}-{members[0]}" + (f" draw {d}" if d else ""))
                            continue
                        pts = predict(scene, ev, members, per_view, max_range)
                        for c in COHORTS:
                            if ev.pairs[c]:
                                per_cohort[c].append(evaluate_fixed(pts, ev.pairs[c], ev.refs))
                    for c, raws in per_cohort.items():
                        if raws:
                            units[c].append(raws)
                require_complete(
                    scene.name, method, n, missing, len(groups.get(n, [])) * len(draws)
                )
                if units["surface interior"]:
                    res[n] = {c: summarize_groups(u) for c, u in units.items() if u}
            if res:
                out[range_name][method] = res
    return out


def _with_interval(x: dict | None, iv: dict | None) -> str:
    """'median [lo, hi] / p90 [lo, hi]' in inches."""
    if not x or x.get("pairs", 0) == 0 or not iv:
        return cell(x)

    def num(y):
        return f"{y:.1f}" if np.isfinite(y) else "inf"

    def one(v, lo, hi):
        return f"{'fails' if not np.isfinite(v) else num(v)} [{num(lo)}, {num(hi)}]"

    return f"{one(x['median_in'], *iv['median_in'])} / {one(x['p90_in'], *iv['p90_in'])}"


def _scale(x: dict | None) -> str:
    v = (x or {}).get("scale_error_pct")
    return "n/a" if v is None else f"{v:+.1f}%"


def table(rows: list[tuple[str, str, dict]], cohort: str) -> list[str]:
    lines = [
        "| Method | Views | Groups x noise draws | Model scale: 1-3 m | 3-10 m | Scale error: 1-3 m "
        "| 3-10 m | One taped distance: 1-3 m | 3-10 m | Tape calibrated |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, n, res in rows:
        r = res.get(cohort)
        if not r:
            continue
        a, b = r["none"].get("1-3m"), r["none"].get("3-10m")
        iv = r["interval_95"]
        ta, tb = r["one_known_distance"].get("1-3m"), r["one_known_distance"].get("3-10m")
        ok = r.get("tape_calibration_success_pct")
        lines.append(
            f"| {label} | {n} | {r['groups']} x {r['noise_draws']} "
            f"| {_with_interval(a, iv.get('1-3m'))} | {_with_interval(b, iv.get('3-10m'))} "
            f"| {_scale(a)} | {_scale(b)} | {cell(ta)} | {cell(tb)} "
            f"| {'n/a' if ok is None else f'{ok:.0f}%'} |"
        )
    return lines


def markdown(results: dict) -> str:
    advio = ", ".join(f"{s:.3f}" for s in ADVIO_ARKIT_SCALES)
    lines = ["# Pose priors (generated by `uv run python -m evals.pose_priors score`)", "", HEADER]
    lines.append(
        "\nPose settings (assumptions, see `evals/ar_poses.py`): exact = the true poses; "
        f"advio_2018 = scale {advio} per group (ADVIO's ARKit against the GPS-rescaled truth), "
        "5 cm and 0.2 degrees of noise; modern_assumed = 2% short, 1 cm, 0.1 degrees, a guess "
        "with no data."
    )
    lines.append(
        "\nModel-scale cells: median [95% interval] / p90 [95% interval], inches. The point "
        "estimate pools every seed group and noise draw; the interval is a bootstrap over seed "
        f"groups and, within each, noise draws ({BOOTSTRAP_REPS} replicates). 'Groups x noise "
        "draws' counts both. MapAnything rows have one noise draw: its weights are no longer on "
        "this machine." + (HISTORICAL_NOTE if uses_historical_rows(results) else "")
    )
    for scene, ranges in results.items():
        for range_name in RANGES:
            rows = [(m, n, r) for m, per_n in ranges[range_name].items() for n, r in per_n.items()]
            for cohort in COHORTS:
                lines += ["", f"## {scene}, {range_name}, {cohort}", ""]
                lines += table(rows, cohort)
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step", choices=["prepare", "score"])
    args = ap.parse_args()
    if args.step == "prepare":
        for s in SCENES:
            prepare(s)
        return
    results = {}
    for s in SCENES:
        scene = Scene(s)
        fits = FitCache(scene.dir / "triangulation_fits.json")
        results[s] = score_scene(scene, pose_methods(scene, fits))
        fits.save()
    out = Path(__file__).resolve().parents[1] / "results"
    (out / "pose_priors.json").write_text(results_json(results))
    md = markdown(results)
    (out / "pose_priors.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
