"""Score a reconstruction (dense or sparse) of an ETH3D scene against the laser scan.

    python score.py <scene> <sparse_run> <align> [--cloud path.ply] [--tag name]

align: gt (already in the GT frame), rigid (metric: rigid fit on camera centres + rigid ICP; the
scale error is reported, not removed), sim3 (no metric scale: similarity fit + ICP, shape only).

Metrics (all distances computed in the GT frame after alignment):
- scale error: recon length / true length - 1, from a similarity fit on the camera centres;
- ETH3D official multi-view evaluator: accuracy / completeness / F1 at 1-50 cm;
- wall surfaces (GT points with horizontal normals): recon-to-laser distance p50 / p90 (in),
  laser-to-recon completeness at 2 / 5 cm;
- ground within 14 ft of the walls (GT points with vertical normals): completeness at 5 / 10 cm;
- edge cohort (GT points at surface discontinuities: window / door jambs, sills, corners):
  completeness at 2 / 5 cm and recon accuracy p50 / p90 near them;
- facade only, using the harness's laser GT scene: wall lines fitted to the recon points alone,
  corner-to-corner length of w2, w1-w3 facing distance, corner angles, and window openings
  (sill / head heights above the recon's own ground plane, jamb-to-jamb widths) measured the
  same way on the recon and on the laser.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import open3d as o3d
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree

from common import ETH3D, ETH3D_EVAL, M_TO_IN, STORE, WORK, dump_json, read_text_model, umeyama

GT_SCENE = {"facade": STORE / "internal/eval-harness/results/eth3d-facade/gt-laser/scene.json"}
FT = 0.3048
FACADE_CRITERIA = False


def load_cloud(path: Path, voxel: float = 0.01) -> np.ndarray:
    pc = o3d.io.read_point_cloud(str(path))
    return np.asarray(pc.voxel_down_sample(voxel).points)


def sparse_points(model_dir: Path) -> np.ndarray:
    pts = []
    for ln in (model_dir / "points3D.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        pts.append([float(v) for v in ln.split()[1:4]])
    return np.array(pts)


def T_from(s, R, t) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    return T


def apply(T, X):
    return X @ T[:3, :3].T + T[:3, 3]


def icp(src: np.ndarray, gt_pc: o3d.geometry.PointCloud, T0: np.ndarray, with_scale: bool) -> np.ndarray:
    s = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(src))
    s = s.voxel_down_sample(0.05)
    T = T0
    for thr in (0.3, 0.1, 0.04):
        est = o3d.pipelines.registration.TransformationEstimationPointToPoint(with_scaling=with_scale)
        r = o3d.pipelines.registration.registration_icp(
            s, gt_pc, thr, T, est, o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60))
        T = r.transformation
    return T


def eth3d_eval(ply: Path, scene: str) -> dict:
    mlp = ETH3D / scene / "dslr_scan_eval/scan_alignment.mlp"
    out = subprocess.run([str(ETH3D_EVAL), "--reconstruction_ply_path", str(ply), "--ground_truth_mlp_path", str(mlp),
                          "--tolerances", "0.01,0.02,0.05,0.1,0.2,0.5"], capture_output=True, text=True)
    res = {}
    for key, name in (("Tolerances", "tol_m"), ("Completenesses", "completeness"), ("Accuracies", "accuracy"),
                      ("F1-scores", "f1")):
        m = re.search(key + r":\s*([^\n]*)", out.stdout)
        if m:
            res[name] = [float(v) for v in m.group(1).split()]
    if not res:
        res["raw"] = out.stdout[-2000:] + out.stderr[-2000:]
    return res


def pct(x, q):
    return float(np.percentile(x, q)) if len(x) else None


def gt_classes(gt: np.ndarray, up: np.ndarray):
    """Normals and a discontinuity score on the 1 cm laser points (computed at 3 cm)."""
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(gt))
    pc3 = pc.voxel_down_sample(0.03)
    pc3.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=40))
    P3 = np.asarray(pc3.points)
    N3 = np.asarray(pc3.normals)
    tree3 = cKDTree(P3)
    # Edge score: largest normal deviation among the 16 nearest neighbours within 8 cm.
    dd, js = tree3.query(P3, 16, distance_upper_bound=0.08, workers=4)
    ok = np.isfinite(dd)
    js = np.where(ok, js, np.arange(len(P3))[:, None])
    dots = np.abs(np.einsum("nkd,nd->nk", N3[js], N3))
    dev = np.where(ok.sum(1) > 3, 1 - dots.min(1), 0.0)
    _, idx = tree3.query(gt, workers=4)
    n = N3[idx]
    upd = np.abs(n @ up)
    return {"wall": upd < 0.3, "ground": upd > 0.9, "edge": dev[idx] > 0.5}


def surface_metrics(rec: np.ndarray, gt: np.ndarray, cls: dict, up: np.ndarray, ground_band_m: float = 14 * FT) -> dict:
    t_gt = cKDTree(gt)
    t_rec = cKDTree(rec)
    d_rec, i_rec = t_gt.query(rec, workers=4, distance_upper_bound=1.0)
    d_gt, _ = t_rec.query(gt, workers=4, distance_upper_bound=1.0)
    near = d_rec < 1.0
    out = {"rec_points_1cm": int(len(rec)), "rec_within_1m_of_laser": float(near.mean())}
    for name in ("wall", "edge"):
        m = cls[name]
        sel = near & m[np.minimum(i_rec, len(gt) - 1)]
        out[name] = {
            "gt_points": int(m.sum()),
            "completeness_2cm": float((d_gt[m] < 0.02).mean()),
            "completeness_5cm": float((d_gt[m] < 0.05).mean()),
            "accuracy_p50_in": pct(d_rec[sel], 50) * M_TO_IN if sel.any() else None,
            "accuracy_p90_in": pct(d_rec[sel], 90) * M_TO_IN if sel.any() else None,
        }
    # Ground within 14 ft (plan distance) of any wall point.
    walls = gt[cls["wall"]]
    wall_plan = walls - np.outer(walls @ up, up)
    g = gt[cls["ground"]]
    g_plan = g - np.outer(g @ up, up)
    dw, _ = cKDTree(wall_plan[::5]).query(g_plan, workers=4)
    band = dw < ground_band_m
    dg = d_gt[cls["ground"]][band]
    out["ground_14ft"] = {"gt_points": int(band.sum()), "completeness_5cm": float((dg < 0.05).mean()),
                          "completeness_10cm": float((dg < 0.10).mean())}
    return out


# ---------- facade criteria metrics (scene frame: y up, plan = x/z) ----------

def fit_line(Q):
    c = Q.mean(0)
    d = np.linalg.svd(Q - c, full_matrices=False)[2][0]
    return c, d


def intersect(c1, d1, c2, d2):
    A = np.array([d1, -d2]).T
    t = np.linalg.solve(A, c2 - c1)
    return c1 + t[0] * d1


def ground_plane(P, rng):
    """Height of the ground from the recon itself: RANSAC plane among low, near-horizontal points."""
    low = P[P[:, 1] < np.percentile(P[:, 1], 30)]
    best, bk = None, -1
    for _ in range(400):
        s = low[rng.integers(0, len(low), 3)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if abs(n[1]) < 0.97:
            continue
        k = int((np.abs((low - s[0]) @ n) < 0.04).sum())
        if k > bk:
            bk, best = k, (n, s[0])
    n, p = best
    inl = low[np.abs((low - p) @ n) < 0.04]
    c = inl.mean(0)
    n = np.linalg.svd(inl - c, full_matrices=False)[2][-1]
    n = n if n[1] > 0 else -n
    return n, c


def wall_frame(P, w, lines, n_g, c_g):
    """Points of wall w in (s, h, out) coordinates using the recon's own line fit and ground."""
    c, d = lines[w["id"]]
    a0, a1 = np.array(w["baseline_m"])
    along = (a1 - a0) / np.linalg.norm(a1 - a0)
    if d @ along < 0:
        d = -d
    outward = np.array([d[1], -d[0]])
    plan = P[:, [0, 2]]
    s = w["s_range_m"][0] + (plan - a0) @ d
    o = (plan - c) @ outward
    h = ((P - c_g) @ n_g)
    return s, h, o


def opening(s, h, o, obj, axis, band=0.06, bin_m=0.02):
    """Find the opening (hole in the wall plane) around the annotated window along one axis.
    axis='v': vertical profile through the middle half of the window -> (sill, head).
    axis='h': horizontal profile through the middle half of the window's height -> (left, right)."""
    g = obj["geometry"]
    s0, s1, b0, b1 = g["s_m"][0], g["s_m"][1], g["bottom_m"], g["top_m"]
    on = np.abs(o) < band
    if axis == "v":
        k = on & (s > s0 + 0.25 * (s1 - s0)) & (s < s1 - 0.25 * (s1 - s0))
        coord, mid, lo, hi = h[k], 0.5 * (b0 + b1), b0 - 1.0, b1 + 1.0
    else:
        k = on & (h > b0 + 0.25 * (b1 - b0)) & (h < b1 - 0.25 * (b1 - b0))
        coord, mid, lo, hi = s[k], 0.5 * (s0 + s1), s0 - 1.0, s1 + 1.0
    edges = np.arange(lo, hi + bin_m, bin_m)
    cnt, _ = np.histogram(coord, edges)
    filled = cnt >= max(3, 0.15 * np.median(cnt[cnt > 0])) if (cnt > 0).any() else cnt > 0
    im = int((mid - lo) / bin_m)
    if filled[im]:
        return None
    a = im
    while a > 0 and not filled[a - 1]:
        a -= 1
    b = im
    while b < len(filled) - 1 and not filled[b + 1]:
        b += 1
    if a == 0 or b == len(filled) - 1:
        return None
    return float(edges[a]), float(edges[b + 1])


def facade_criteria(rec_c: np.ndarray, gt_c: np.ndarray, scene: dict) -> dict:
    rng = np.random.default_rng(0)
    res = {}
    measures = {}
    for name, P in (("laser", gt_c), ("recon", rec_c)):
        n_g, c_g = ground_plane(P, rng)
        lines = {}
        for w in scene["walls"]:
            a0, a1 = np.array(w["baseline_m"])
            L = np.linalg.norm(a1 - a0)
            along = (a1 - a0) / L
            nrm = np.array([along[1], -along[0]])
            plan = P[:, [0, 2]]
            t = (plan - a0) @ along
            off = (plan - a0) @ nrm
            h = P[:, 1]
            k = (np.abs(off) < 0.25) & (t > 0.3) & (t < L - 0.3) & (h > 0.3) & (h < 3.0)
            Q = plan[k]
            if len(Q) < 50:
                lines[w["id"]] = None
                continue
            c, d = fit_line(Q)
            # one refinement: keep points within 6 cm of the first fit
            r = np.abs((Q - c) @ np.array([d[1], -d[0]]))
            c, d = fit_line(Q[r < 0.06])
            lines[w["id"]] = (c, d)
        m = {"ground_normal_tilt_deg": float(np.degrees(np.arccos(abs(n_g[1]))))}
        ids = [w["id"] for w in scene["walls"]]
        if all(lines[i] is not None for i in ids):
            c1 = intersect(*lines["w1"], *lines["w2"])
            c2 = intersect(*lines["w2"], *lines["w3"])
            m["w2_corner_to_corner_m"] = float(np.linalg.norm(c2 - c1))
            # w1-w3 facing distance: distance from w3's centre to line w1 (they are ~parallel).
            c1l, d1l = lines["w1"]
            c3l, _ = lines["w3"]
            m["w1_w3_facing_m"] = float(abs((c3l - c1l) @ np.array([d1l[1], -d1l[0]])))
            for a, b in (("w1", "w2"), ("w2", "w3")):
                da, db = lines[a][1], lines[b][1]
                m[f"angle_{a}_{b}_deg"] = float(np.degrees(np.arccos(min(1.0, abs(da @ db)))))
        wins = {}
        for obj in scene["objects"]:
            if obj["class"] not in ("window", "door"):
                continue
            w = next(x for x in scene["walls"] if x["id"] == obj["wall_id"])
            if lines[w["id"]] is None:
                continue
            s, h, o = wall_frame(P, w, lines, n_g, c_g)
            v = opening(s, h, o, obj, "v")
            hz = opening(s, h, o, obj, "h")
            wins[obj["id"]] = {"sill_m": v[0] if v else None, "head_m": v[1] if v else None,
                               "left_s_m": hz[0] if hz else None, "right_s_m": hz[1] if hz else None,
                               "width_m": (hz[1] - hz[0]) if hz else None}
        m["openings"] = wins
        measures[name] = m
    res["laser"] = measures["laser"]
    res["recon"] = measures["recon"]
    err = {}
    L, R = measures["laser"], measures["recon"]
    for k in ("w2_corner_to_corner_m", "w1_w3_facing_m"):
        if k in L and k in R:
            err[k.replace("_m", "_err_in")] = (R[k] - L[k]) * M_TO_IN
    op_err = []
    for oid, lo in L["openings"].items():
        ro = R["openings"].get(oid)
        if not ro:
            continue
        for k in ("sill_m", "head_m", "width_m"):
            if lo[k] is not None and ro[k] is not None:
                op_err.append({"id": oid, "what": k[:-2], "laser_m": lo[k], "recon_m": ro[k],
                               "err_in": (ro[k] - lo[k]) * M_TO_IN})
    err["openings"] = op_err
    ae = np.abs([e["err_in"] for e in op_err])
    n_possible = 3 * len(L["openings"])
    err["openings_summary"] = {"measured": len(op_err), "of": n_possible,
                               "abs_err_p50_in": pct(ae, 50), "abs_err_p90_in": pct(ae, 90),
                               "abs_err_max_in": float(ae.max()) if len(ae) else None}
    res["errors"] = err
    return res


def score(scene: str, run: str, align: str, cloud: Path | None, tag: str | None) -> dict:
    t0 = time.time()
    d = WORK / scene
    tag = tag or (run + ("_dense" if cloud else "_sparse"))
    _, gt_images = read_text_model(d / "gt_model")
    _, rec_images = read_text_model(d / "sparse" / run)
    names = sorted(set(gt_images) & set(rec_images))
    C_rec = np.array([rec_images[n]["center"] for n in names])
    C_gt = np.array([gt_images[n]["center"] for n in names])
    s_sim, R_sim, t_sim = umeyama(C_rec, C_gt, True)
    rep = {"scene": scene, "run": run, "align": align, "tag": tag, "registered": len(rec_images),
           "images": len(gt_images), "scale_error": 1.0 / s_sim - 1.0}
    rep["camera_center_rmse_after_sim3_m"] = float(np.sqrt(np.mean(np.sum(
        (apply(T_from(s_sim, R_sim, t_sim), C_rec) - C_gt) ** 2, 1))))
    rec = load_cloud(cloud) if cloud else sparse_points(d / "sparse" / run)
    gt_pc = o3d.io.read_point_cloud(str(d / "gt_scan_1cm.ply"))
    gt = np.asarray(gt_pc.points)
    gt5 = gt_pc.voxel_down_sample(0.05)
    if align == "gt":
        T = np.eye(4)
    elif align == "rigid":
        s1, R1, t1 = umeyama(C_rec, C_gt, False)
        T = icp(rec, gt5, T_from(1.0, R1, t1), with_scale=False)
    elif align == "sim3":
        T = icp(rec, gt5, T_from(s_sim, R_sim, t_sim), with_scale=True)
    else:
        raise SystemExit(align)
    rep["T_rec_to_gt"] = T
    rec_g = apply(T, rec)
    out_dir = d / "scores"
    out_dir.mkdir(exist_ok=True)
    ply = out_dir / f"{tag}_in_gt.ply"
    v = np.zeros(len(rec_g), dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    v["x"], v["y"], v["z"] = rec_g.T
    PlyData([PlyElement.describe(v, "vertex")], text=False).write(str(ply))
    rep["eth3d"] = eth3d_eval(ply, scene)
    ply.unlink()
    up = np.array([0.0, 0.0, 1.0])
    cls_path = d / "gt_classes.npz"
    if cls_path.exists():
        z = np.load(cls_path)
        cls = {k: z[k] for k in ("wall", "ground", "edge")}
    else:
        cls = gt_classes(gt, up)
        np.savez_compressed(cls_path, **cls)
    rep["surfaces"] = surface_metrics(rec_g, gt, cls, up)
    # Criteria key distances come from the shared scorer (hscore.py); this block is kept for reference only.
    if scene in GT_SCENE and FACADE_CRITERIA:
        sc = json.loads(GT_SCENE[scene].read_text())
        Ts = np.asarray(sc["frame"]["from_source"], float).reshape(4, 4, order="F")
        rep["facade_criteria"] = facade_criteria(apply(Ts, rec_g), apply(Ts, gt), sc)
    rep["score_s"] = time.time() - t0
    dump_json(out_dir / f"{tag}.json", rep)
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("run")
    ap.add_argument("align", choices=["gt", "rigid", "sim3"])
    ap.add_argument("--cloud", type=Path)
    ap.add_argument("--tag")
    a = ap.parse_args()
    r = score(a.scene, a.run, a.align, a.cloud, a.tag)
    r.pop("T_rec_to_gt", None)
    print(json.dumps(r, indent=1, default=float)[:6000])
