"""Score A2 runs against the ETH3D laser scans with PR #12's fixed per-seed evaluation sets.

    python -m a2ff.score --scene delivery_area electro facade --sizes 8 [--methods ma518_images ...]

Real images, real laser truth; ARKit poses synthetic (packet generator).

For every method directory ~/a2/preds/<scene>/<method>/<group id>/ with a run.json, each group is
scored in the model's own frame and scale (`centred=False`: the reconstruction scales about its
origin) on the seed's evaluation set, for ranges "all points" / "within 6 m" and cohorts surface
interior / vertical interior / edges. Variants:

- `raw`: every finite depth the model output (unmasked; edges included);
- `masked`: depth outside the model's own mask (`model_mask`) removed, as PR #12 ran MapAnything;
- `posthoc:<setting>` (images-only runs): the reconstruction multiplied by the scale of a Sim(3)
  fit of the run's camera centres to that setting's ARKit-like centres: metric scale from the
  phone's poses applied after the fact.

Per variant: model-scale median / p90 inches and scale error for 1-3 m and 3-10 m pairs, failed %,
the one-taped-distance variant, pass rates (% of pairs within 3 in and 6 in; failures count as
misses) and completeness (fraction of the seed's evaluation points with a finite prediction).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from a2ff.common import PRED_ROOT, ROOT, load_poses, umeyama_sim3
from evals.pairs import INCH, Points, evaluate_fixed, pool
from evals.recon import COHORTS, RANGES, EvalSet, Scene, load_prediction, predict

POSE_SETTINGS = ("exact",) + tuple(f"{p}.draw{d}" for p in ("lidar_modern", "nonlidar_modern", "nonlidar_2018")
                                   for d in range(3))
RESULTS = ROOT / "results"
MIN_VIEWS, MAX_INCIDENCE_DEG = 2, 75.0


def per_view_factory(run: Path, masked: bool):
    def pv(name):
        d, K, T = load_prediction(run / f"{name}.npz")
        if masked:
            z = np.load(run / f"{name}.npz")
            if "model_mask" in z:
                d = np.where(z["model_mask"], d, np.nan)
        return d, K, T, False

    return pv


def has_mask(run: Path, members: list[str]) -> bool:
    return "model_mask" in np.load(run / f"{members[0]}.npz").files


def posthoc_scale(run: Path, members: list[str], scene: str, setting: str) -> float:
    src = np.array([np.load(run / f"{m}.npz")["cam_to_world"][:3, 3] for m in members])
    poses = load_poses(scene, setting)
    dst = np.array([poses[m]["T"][:3, 3] for m in members])
    s, _, _ = umeyama_sim3(src, dst)
    return s


def pass_rates(raws: list[dict], source: str, key: str) -> dict:
    parts = [r[source][key][0] for r in raws if key in r[source]]
    if not parts:
        return {}
    a = np.abs(np.concatenate(parts)) / INCH
    return {"within_3in_pct": round(float(100 * np.mean(a <= 3)), 1),
            "within_6in_pct": round(float(100 * np.mean(a <= 6)), 1)}


def completeness(pts: Points, edge: np.ndarray) -> dict:
    ok = np.isfinite(pts.r).all(axis=1)
    return {"all": float(ok.mean()) if len(ok) else float("nan"),
            "edges": float(ok[edge].mean()) if edge.any() else float("nan"),
            "points": int(len(ok))}


def well_observed(scene: Scene, normals: np.ndarray, ev: EvalSet, members: list[str],
                  max_range: float | None) -> np.ndarray:
    """Contract §3.1 per evaluation point: seen (in image, unoccluded by the laser) by >= MIN_VIEWS
    group views at <= MAX_INCIDENCE_DEG from the laser normal. Coverage from the true cameras."""
    lookup = np.full(len(scene.cands), -1)
    lookup[ev.index] = np.arange(len(ev.index))
    count = np.zeros(len(ev.index), int)
    for name in members:
        index, _, _ = scene.visible(name)
        pos = lookup[index]
        sel = pos >= 0
        P = scene.cands[index]
        ray = scene.views[name].center - P
        dist = np.linalg.norm(ray, axis=1)
        if max_range is not None:
            sel &= dist <= max_range
        cos = np.abs(np.sum(normals[index] * ray, axis=1)) / np.maximum(dist, 1e-9)
        sel &= cos >= np.cos(np.radians(MAX_INCIDENCE_DEG))
        np.add.at(count, pos[sel], 1)
    return count >= MIN_VIEWS


def seed_edges(scene: Scene, seed: str, max_range: float | None) -> np.ndarray:
    index, _, edge = scene.visible(seed)
    keep = np.ones(len(index), bool)
    if max_range is not None:
        keep = np.linalg.norm(scene.cands[index] - scene.views[seed].center, axis=1) <= max_range
    return edge[keep]


def score_scene(name: str, sizes: list[int], methods: list[str] | None) -> dict:
    scene = Scene(name)
    normals = np.load(scene.dir / "normals.npy").astype(np.float64)
    root = PRED_ROOT / name
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and d.name != "arkit_poses")
    if methods:
        dirs = [d for d in dirs if d.name in methods]
    out: dict = {}
    for range_name, max_range in RANGES.items():
        sets = {m[0]: EvalSet(scene, m[0], max_range) for m in scene.groups["1"]}
        edges = {s: seed_edges(scene, s, max_range) for s in sets}
        out[range_name] = {}
        for mdir in dirs:
            images_only = mdir.name.endswith("_images")
            for n in map(str, sizes):
                variants: dict[str, dict] = {}
                for members in scene.groups.get(n, []):
                    run = mdir / f"n{n}-{members[0]}"
                    if not (run / "run.json").exists():
                        continue
                    ev = sets[members[0]]
                    wo = well_observed(scene, normals, ev, members, max_range)
                    obs_pairs = {c: {k: pr[wo[pr[:, 0]] & wo[pr[:, 1]]] for k, pr in ev.pairs[c].items()}
                                 for c in COHORTS}
                    todo = [("raw", False)] + ([("masked", True)] if has_mask(run, members) else [])
                    for vname, masked in todo:
                        pts = predict(scene, ev, members, per_view_factory(run, masked), max_range)
                        scaled = [(vname, pts, None)]
                        if images_only and not masked:
                            for st in POSE_SETTINGS:
                                s = posthoc_scale(run, members, name, st)
                                scaled.append((f"posthoc:{st}", Points(gt=pts.gt, c=pts.c, r=pts.r * s), s))
                        for label, p, s in scaled:
                            v = variants.setdefault(label, {"raws": {c: [] for c in COHORTS},
                                                            "obs": {c: [] for c in COHORTS},
                                                            "obs_n": {c: [0, 0] for c in COHORTS},
                                                            "comp": [], "scales": []})
                            for c in COHORTS:
                                if ev.pairs[c]:
                                    v["raws"][c].append(evaluate_fixed(p, ev.pairs[c], ev.refs))
                                    op = {k: x for k, x in obs_pairs[c].items() if len(x)}
                                    v["obs_n"][c][0] += sum(len(x) for x in obs_pairs[c].values())
                                    v["obs_n"][c][1] += sum(len(x) for x in ev.pairs[c].values())
                                    if op:
                                        v["obs"][c].append(evaluate_fixed(p, op, ev.refs))
                            v["comp"].append(completeness(p, edges[members[0]]))
                            if s is not None:
                                v["scales"].append(s)
                res = {}
                for label, v in variants.items():
                    if not v["raws"]["surface interior"]:
                        continue
                    r = {"groups": len(v["comp"]),
                         "completeness_all": round(float(np.nanmean([c["all"] for c in v["comp"]])), 4),
                         "completeness_edges": round(float(np.nanmean([c["edges"] for c in v["comp"]])), 4)}
                    if v["scales"]:
                        r["posthoc_scales"] = [round(x, 4) for x in v["scales"]]
                    for c, raws in v["raws"].items():
                        if not raws:
                            continue
                        pooled = pool(raws)
                        for src in ("none", "one_known_distance"):
                            for key in pooled[src]:
                                pooled[src][key].update(pass_rates(raws, src, key))
                        n_obs, n_all = v["obs_n"][c]
                        pooled["well_observed_pairs_pct"] = round(100 * n_obs / max(n_all, 1), 1)
                        if v["obs"][c]:
                            po = pool(v["obs"][c])
                            for src in ("none", "one_known_distance"):
                                for key in po[src]:
                                    po[src][key].update(pass_rates(v["obs"][c], src, key))
                            pooled["well_observed"] = po
                        r[c] = pooled
                    res[label] = r
                if res:
                    out[range_name].setdefault(mdir.name, {})[n] = res
                    print(f"{name} {range_name} {mdir.name} n{n}: {len(res)} variants", flush=True)
    return out


def fmt(x: dict | None) -> str:
    if not x or x.get("pairs", 0) == 0:
        return "n/a"
    p90 = "fails" if not np.isfinite(x["p90_in"]) else f"{x['p90_in']:.1f}"
    med = "fails" if not np.isfinite(x["median_in"]) else f"{x['median_in']:.1f}"
    return f"{med} / {p90}"


def sc(x: dict | None) -> str:
    v = (x or {}).get("scale_error_pct")
    return "n/a" if v is None else f"{v:+.1f}%"


def markdown(results: dict, cohorts=COHORTS) -> str:
    lines = ["# A2 feed-forward scores (real images, real laser truth; ARKit poses synthetic)", "",
             "Median / p90 |pair-length error|, inches. Pass = % of pairs within 3 in / 6 in (failures "
             "count as misses). Compl = fraction of seed-visible evaluation points predicted (all / edges).", ""]
    for scene, per_range in results.items():
        for range_name, per_method in per_range.items():
            for cohort in cohorts:
                lines += [f"## {scene}, {range_name}, {cohort}", "",
                          "| Method | Variant | Views | Groups | 1-3 m | 3-10 m | Scale 1-3 m | Scale 3-10 m "
                          "| Pass 1-3 m (3/6 in) | Taped 1-3 m | Taped 3-10 m | Compl "
                          "| Well-obs pairs | Well-obs 1-3 m | Well-obs pass 1-3 m |",
                          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
                for method, per_n in per_method.items():
                    for n, per_v in per_n.items():
                        for variant, r in per_v.items():
                            c = r.get(cohort)
                            if not c:
                                continue
                            a, b = c["none"].get("1-3m"), c["none"].get("3-10m")
                            ta, tb = c["one_known_distance"].get("1-3m"), c["one_known_distance"].get("3-10m")
                            pr = f"{a.get('within_3in_pct', 'n/a')} / {a.get('within_6in_pct', 'n/a')}" if a else "n/a"
                            wa = c.get("well_observed", {}).get("none", {}).get("1-3m")
                            wpr = f"{wa.get('within_3in_pct')} / {wa.get('within_6in_pct')}" if wa else "n/a"
                            lines.append(
                                f"| {method} | {variant} | {n} | {r['groups']} | {fmt(a)} | {fmt(b)} | {sc(a)} "
                                f"| {sc(b)} | {pr} | {fmt(ta)} | {fmt(tb)} "
                                f"| {r['completeness_all']:.3f} / {r['completeness_edges']:.3f} "
                                f"| {c.get('well_observed_pairs_pct', 'n/a')}% | {fmt(wa)} | {wpr} |")
                lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", nargs="+", required=True)
    ap.add_argument("--sizes", nargs="+", type=int, default=[8])
    ap.add_argument("--methods", nargs="*", default=None)
    ap.add_argument("--tag", default="scores")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    results = {s: score_scene(s, args.sizes, args.methods) for s in args.scene}
    (RESULTS / f"{args.tag}.json").write_text(json.dumps(results, indent=1))
    md = markdown(results)
    (RESULTS / f"{args.tag}.md").write_text(md)
    print(f"wrote {RESULTS / args.tag}.json/.md")


if __name__ == "__main__":
    main()
