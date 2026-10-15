"""Score fused depth (and single-image depth) against ETH3D's laser scans.

    python evaluate.py --scene electro --models moge2-vits,unidepth2-vits --poses gt --scales raw,feat,oracle

Per run (scene, model, pose mode, scale mode, voxel):
- pairs/fused: PR #12's alignment-free pair-length metric on the fused mesh. Every view is a seed
  with PR #12's fixed EvalSet; the mesh is raycast from the seed's *estimated* pose through each
  evaluation point's pixel and the first hit is the prediction (a miss is a failed pair).
- pairs/single: the same seeds scored on the seed's own scaled depth (PR #12's `predict`).
- surface: mesh vertices vs scan + occluder splats (ETH3D world; SE(3) on camera centres for noisy
  poses, so the session scale error stays in). Accuracy: share of vertices within 5/10/20 cm among
  vertices with scan within 1 m (unsupported share reported separately). Completeness: share of
  evaluation points seen by at least one view within the depth truncation that have a vertex
  within 5/10/20 cm.
Output: WORK/eval/<scene>/<tag>.json; mesh at WORK/fused/<scene>/<tag>.ply.
"""

from __future__ import annotations

import argparse
import json
import time
from functools import lru_cache

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from common import WORK  # noqa: I001 (puts PR #12 on sys.path)
from evals.eth3d import occluder_points, scan_points
from evals.pairs import Points, evaluate_fixed, pool
from evals.recon import COHORTS, RANGES, EvalSet, Scene, predict
from fuse import FUSED, DepthSource, fuse, tag

EVAL = WORK / "eval"
DEPTH_TRUNC = 12.0
THRESH_M = (0.05, 0.10, 0.20)


@lru_cache(maxsize=2)
def scene_obj(scene: str) -> Scene:
    return Scene(scene)


@lru_cache(maxsize=2)
def gt_tree(scene: str) -> cKDTree:
    return cKDTree(np.concatenate([scan_points(scene), occluder_points(scene)]).astype(np.float64))


@lru_cache(maxsize=2)
def completeness_targets(scene: str) -> np.ndarray:
    sc = scene_obj(scene)
    seen = np.zeros(len(sc.cands), bool)
    for n, v in sc.views.items():
        index, _, _ = sc.visible(n)
        d = np.linalg.norm(sc.cands[index] - v.center, axis=1)
        seen[index[d <= DEPTH_TRUNC]] = True
    idx = np.flatnonzero(seen)
    rng = np.random.default_rng(0)
    return sc.cands[np.sort(rng.choice(idx, min(200_000, len(idx)), replace=False))].astype(np.float64)


@lru_cache(maxsize=64)
def eval_set(scene: str, seed: str, max_range):
    sc = scene_obj(scene)
    ev = EvalSet(sc, seed, max_range)
    index, uv, _ = sc.visible(seed)
    keep = np.ones(len(index), bool)
    if max_range is not None:
        keep = np.linalg.norm(sc.cands[index] - sc.views[seed].center, axis=1) <= max_range
    return ev, uv[keep]


def umeyama_se3(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    mu_s, mu_d = src.mean(0), dst.mean(0)
    U, _, Vt = np.linalg.svd((dst - mu_d).T @ (src - mu_s))
    S = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
    R = U @ S @ Vt
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, mu_d - R @ mu_s
    return T


def pair_scores(scene: str, predict_seed) -> dict:
    """{range: {cohort: pooled}} over every view as a seed."""
    sc = scene_obj(scene)
    out = {}
    for rname, max_range in RANGES.items():
        raws = {k: [] for c in COHORTS for k in (c, f"{c} | predicted")}
        for seed in sc.views:
            ev, uv = eval_set(scene, seed, max_range)
            if len(ev.index) < 2:
                continue
            pts = predict_seed(seed, ev, uv, max_range)
            if pts is None:
                pts = Points(ev.gt, np.full_like(ev.gt, np.nan), np.full_like(ev.gt, np.nan))
            have = np.isfinite(pts.r).all(axis=1)
            refs_ok = ev.refs[have[ev.refs].all(axis=1)] if len(ev.refs) else ev.refs
            for c in COHORTS:
                if ev.pairs[c]:
                    raws[c].append(evaluate_fixed(pts, ev.pairs[c], ev.refs))
                    # The same pairs and tape references, restricted to points that were predicted:
                    # accuracy where the asset has surface, reported next to its coverage.
                    sub = {k: p[have[p].all(axis=1)] for k, p in ev.pairs[c].items()}
                    sub = {k: p for k, p in sub.items() if len(p)}
                    if sub and len(refs_ok):
                        raws[f"{c} | predicted"].append(evaluate_fixed(pts, sub, refs_ok))
        out[rname] = {c: pool(r) | {"seeds": len(r)} for c, r in raws.items() if r}
    return out


def mesh_predictor(scene: str, mesh: o3d.geometry.TriangleMesh, T: dict):
    rc = o3d.t.geometry.RaycastingScene()
    rc.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    sc = scene_obj(scene)

    def pred(seed, ev, uv, _max_range):
        K = sc.K(seed)
        d_cam = np.c_[(uv[:, 0] - K[0, 2]) / K[0, 0], (uv[:, 1] - K[1, 2]) / K[1, 1], np.ones(len(uv))]
        d = d_cam @ T[seed][:3, :3].T
        o = np.broadcast_to(T[seed][:3, 3], d.shape)
        rays = o3d.core.Tensor(np.c_[o, d].astype(np.float32))
        t = rc.cast_rays(rays)["t_hit"].numpy().astype(np.float64)
        t[~np.isfinite(t) | (t > DEPTH_TRUNC * 1.5)] = np.nan
        return Points(ev.gt, np.array(o, dtype=np.float64), d * t[:, None])

    return pred


def single_predictor(scene: str, src: DepthSource):
    sc = scene_obj(scene)

    def pred(seed, ev, _uv, max_range):
        d = src.depth(seed)
        if d is None:
            return None
        return predict(sc, ev, [seed], lambda n: (src.depth(n), sc.K(n), src.T[n], True), max_range)

    return pred


def surface_scores(scene: str, mesh: o3d.geometry.TriangleMesh, T: dict) -> dict:
    sc = scene_obj(scene)
    V = np.asarray(mesh.vertices, dtype=np.float64)
    if len(V) == 0:
        return {"vertices": 0}
    names = sorted(T)
    est = np.stack([T[n][:3, 3] for n in names])
    gt = np.stack([sc.views[n].center for n in names])
    A = umeyama_se3(est, gt)
    Vw = V @ A[:3, :3].T + A[:3, 3]
    d, _ = gt_tree(scene).query(Vw, distance_upper_bound=1.0)
    supported = np.isfinite(d)
    out = {"vertices": len(V), "unsupported_share": float(1 - supported.mean()),
           "align_centre_rms_m": float(np.sqrt(np.mean(np.sum((est @ A[:3, :3].T + A[:3, 3] - gt) ** 2, 1))))}
    ds = d[supported]
    out["accuracy_median_cm"] = float(np.median(ds) * 100) if len(ds) else None
    for t in THRESH_M:
        out[f"accuracy_{round(t * 100)}cm"] = float((ds <= t).mean()) if len(ds) else None
    targets = completeness_targets(scene)
    dc, _ = cKDTree(Vw).query(targets, distance_upper_bound=max(THRESH_M))
    for t in THRESH_M:
        out[f"completeness_{round(t * 100)}cm"] = float((dc <= t).mean())
    return out


def run_tag(model, pose_mode, scale_mode, voxel, min_weight, trunc_vox) -> str:
    return (tag(model, pose_mode, scale_mode, voxel) + (f"__w{min_weight:g}" if min_weight != 1.0 else "")
            + (f"__t{trunc_vox:g}" if trunc_vox != 4.0 else ""))


def run(scene: str, model: str, pose_mode: str, scale_mode: str, voxel: float = 0.04,
        min_weight: float = 1.0, trunc_vox: float = 4.0, save_mesh: bool = True) -> dict:
    t0 = time.perf_counter()
    src = DepthSource(scene, model, pose_mode, scale_mode)
    t_scale = time.perf_counter() - t0
    mesh = fuse(src, voxel, trunc_vox=trunc_vox, depth_trunc=DEPTH_TRUNC, min_weight=min_weight)
    t_fuse = time.perf_counter() - t0 - t_scale
    name = run_tag(model, pose_mode, scale_mode, voxel, min_weight, trunc_vox)
    if save_mesh:
        (FUSED / scene).mkdir(parents=True, exist_ok=True)
        o3d.io.write_triangle_mesh(str(FUSED / scene / f"{name}.ply"), mesh)
    res = {
        "scene": scene, "model": model, "poses": pose_mode, "scale": scale_mode, "voxel": voxel,
        "min_weight": min_weight, "trunc_vox": trunc_vox, "tag": name, "pose_info": src.pose_info, "depth_stats": src.stats,
        "seconds": {"scale": t_scale, "fuse": t_fuse},
        "pairs_fused": pair_scores(scene, mesh_predictor(scene, mesh, src.T)),
        "pairs_single": pair_scores(scene, single_predictor(scene, src)),
        "surface": surface_scores(scene, mesh, src.T),
    }
    res["seconds"]["total"] = time.perf_counter() - t0
    (EVAL / scene).mkdir(parents=True, exist_ok=True)
    (EVAL / scene / f"{name}.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def headline(res: dict) -> str:
    def cell(block, rng="within 6 m", cohort="surface interior", src="one_known_distance", b="1-3m"):
        x = res[block].get(rng, {}).get(cohort, {}).get(src, {}).get(b)
        if not x:
            return "n/a"
        p90 = "fail" if not np.isfinite(x["p90_in"]) else f"{x['p90_in']:.1f}"
        return f"{x['median_in']:.1f}/{p90}"

    def cov(block):
        x = res[block].get("within 6 m", {}).get("surface interior", {}).get("none", {}).get("1-3m")
        return f"{100 - x['failed_pct']:.0f}%" if x else "n/a"

    P = "surface interior | predicted"
    s = res["surface"]
    return (f"{res['tag']}: FUSED cov {cov('pairs_fused')} raw1-3 {cell('pairs_fused', cohort=P, src='none')} "
            f"tape1-3 {cell('pairs_fused', cohort=P)} tape3-10 {cell('pairs_fused', cohort=P, b='3-10m')} | "
            f"SINGLE cov {cov('pairs_single')} raw1-3 {cell('pairs_single', cohort=P, src='none')} "
            f"tape1-3 {cell('pairs_single', cohort=P)} | acc10 {s.get('accuracy_10cm', 0):.2f} "
            f"comp10 {s.get('completeness_10cm', 0):.2f} | {res['seconds']['total']:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--poses", default="gt")
    ap.add_argument("--scales", default="feat")
    ap.add_argument("--voxel", type=float, default=0.04)
    ap.add_argument("--min-weight", type=float, default=1.0)
    ap.add_argument("--trunc", type=float, default=4.0, help="TSDF truncation in voxels")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()
    for pm in args.poses.split(","):
        for model in args.models.split(","):
            for sm in args.scales.split(","):
                name = run_tag(model, pm, sm, args.voxel, args.min_weight, args.trunc)
                if args.skip_existing and (EVAL / args.scene / f"{name}.json").exists():
                    continue
                res = run(args.scene, model, pm, sm, args.voxel, args.min_weight, args.trunc)
                print(headline(res), flush=True)


if __name__ == "__main__":
    main()
