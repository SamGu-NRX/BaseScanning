"""Invented geometry in a Marble world vs the laser, plus an oracle similarity-ICP alignment.

python invention.py WORLD_DIR GT_DIR OUT_DIR [SCORED_SCENE_JSON [ORACLE_T_JSON]]
Needs WORLD_DIR/points_metric.ply + cameras.json (register_inputs.py) and GT_DIR/{scene.json,laser.ply,
occluders.ply,coverage.npz} (eval-harness GT build). Writes OUT_DIR/invention.json, T_metric.json,
T_oracle_sim.json, points_flagged.ply (GT frame, field `generated`).

Two views of "unseen":
- producer flag (no ground truth): the point is not visible from any registered input camera, using the
  splat's own z-buffer as the occluder. This is what a production path can compute and mark `generated`.
- truth: not visible from the true input camera poses against the laser (+ ETH3D occlusion splats).
Truth classes: outside every input frustum / inside one but hidden > 30 cm behind the visible laser surface
/ seen. Disagreement: > 30 cm from the laser where the laser has a point within 1.5 m (checkable).
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parents[2] / "eval-harness"))
from evalharness.frame import align_by_rotations, apply, mat4_from_list, project_arkit, scale_of, transform_pose, umeyama  # noqa: E402
from evalharness.geomscore import load_cameras  # noqa: E402
from evalharness.sceneio import read_ply, write_ply  # noqa: E402
from evalharness.visibility import Camera, VisibilityOracle  # noqa: E402

DISAGREE_M = 0.30
CHECK_M = 1.5  # the laser covered this neighbourhood
SEEN_TOL_M = 0.30  # a surface layer within this of the visible laser surface counts as seen


def sim_icp(src, dst, T0, iters=24, dists=(1.0, 0.6, 0.35, 0.2), ltree=None):
    tree = ltree or cKDTree(dst)
    T = T0.copy()
    for d in dists:
        for _ in range(iters // len(dists)):
            cur = apply(T, src)
            dd, j = tree.query(cur, distance_upper_bound=d)
            ok = np.isfinite(dd)
            if ok.sum() < 200:
                break
            dT = umeyama(cur[ok], dst[j[ok]], with_scale=True)
            T = dT @ T
    return T


def oracle_scale_scan(src, dst, T0, ctr, scales=np.arange(0.9, 1.71, 0.025), tol=0.10):
    """Best-case alignment using the laser: for each fixed scale about the camera centroid, rigid ICP,
    scored by F = harmonic mean of (splat->laser and laser->splat share within `tol`)."""
    dtree = cKDTree(dst)
    dsub = dst[np.random.default_rng(2).choice(len(dst), min(len(dst), 60_000), replace=False)]
    best, scan = None, []
    for s in scales:
        S = np.eye(4)
        S[:3, :3] *= s
        S[:3, 3] = ctr - s * ctr
        T = S @ T0
        for d in (1.0, 0.5, 0.25, 0.15):
            for _ in range(6):
                cur = apply(T, src)
                dd, j = dtree.query(cur, distance_upper_bound=d)
                ok = np.isfinite(dd)
                if ok.sum() < 200:
                    break
                T = umeyama(cur[ok], dst[j[ok]], with_scale=False) @ T
        cur = apply(T, src)
        p = float(np.isfinite(dtree.query(cur, distance_upper_bound=tol)[0]).mean())
        r = float(np.isfinite(cKDTree(cur).query(dsub, distance_upper_bound=tol)[0]).mean())
        f = 2 * p * r / max(p + r, 1e-9)
        scan.append({"scale": round(float(s), 3), "precision": round(p, 3), "recall": round(r, 3), "f": round(f, 3)})
        if best is None or f > best[0]:
            best = (f, T)
    return best[1], scan


def main():
    world, gtd, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    gt = json.loads((gtd / "scene.json").read_text())
    gkf = {k["id"]: k for k in gt["keyframes"]}
    d = read_ply(world / "points_metric.ply")
    X = np.c_[d["x"], d["y"], d["z"]].astype(float)
    cams_src = load_cameras(world / "cameras.json")
    names = sorted(n for n in cams_src if n in gkf)
    R_src = np.array([cams_src[n]["pose"][:3, :3] for n in names])
    c_src = np.array([cams_src[n]["pose"][:3, 3] for n in names])
    R_gt = np.array([mat4_from_list(gkf[n]["pose"])[:3, :3] for n in names])
    c_gt = np.array([mat4_from_list(gkf[n]["pose"])[:3, 3] for n in names])
    T_metric = align_by_rotations(R_src, c_src, R_gt, c_gt, with_scale=False)
    ctr = c_gt.mean(0)
    if len(sys.argv) > 4:
        # meter-anchored metric frame from the harness's score-cloud scene (camera-rigid + re-anchoring on
        # the recon's own meter through GT taps): what a production path's frame would be
        T_metric = mat4_from_list(json.loads(Path(sys.argv[4]).read_text())["frame"]["from_source"])
        ctr = np.zeros(3)

    L = read_ply(gtd / "laser.ply")
    laser = np.c_[L["x"], L["y"], L["z"]].astype(float)
    O = read_ply(gtd / "occluders.ply")
    occl = np.c_[O["x"], O["y"], O["z"]].astype(float)
    g = np.load(gtd / "coverage.npz")
    gmeta = gt["coverage"]["grid"]
    origin, vox, shape = np.array(gmeta["origin_m"]), gmeta["voxel_m"], np.array(gmeta["shape"])
    lo, hi = origin, origin + shape * vox

    def roi(P):
        return np.all((P >= lo) & (P < hi), axis=1)

    def gstate(P):
        idx = np.floor((P - origin) / vox).astype(int)
        return g["state"][idx[:, 0], idx[:, 1], idx[:, 2]]

    Xm = apply(T_metric, X)
    lroi = laser[roi(laser) & (laser[:, 1] > 0.4)]  # walls only: a ground plane does not constrain scale
    src = Xm[roi(Xm) & (Xm[:, 1] > 0.7)]
    src = src[np.random.default_rng(0).choice(len(src), min(len(src), 60_000), replace=False)]
    if len(sys.argv) > 5:
        T_oracle = np.asarray(json.loads(Path(sys.argv[5]).read_text()), float)
        res_scan = f"given oracle transform {sys.argv[5]}"
    else:
        T_oracle, res_scan = oracle_scale_scan(src, lroi, T_metric, ctr)
    (out / "T_metric.json").write_text(json.dumps(T_metric.tolist()))
    (out / "T_oracle_sim.json").write_text(json.dumps(T_oracle.tolist()))

    ltree = cKDTree(laser)
    all_inputs = sorted(p.stem for p in (world / "inputs").glob("*.jpg") if p.stem in gkf)
    true_cams = [Camera(n, mat4_from_list(gkf[n]["pose"]), np.array(gkf[n]["intrinsics"]), gkf[n]["w"], gkf[n]["h"]) for n in all_inputs]
    truth_oracle = VisibilityOracle(true_cams, np.r_[laser, occl])
    res = {"cameras_registered": names, "input_cameras_for_truth": all_inputs, "metric_scale_factor_applied": True,
           "oracle_similarity_scale": round(scale_of(T_oracle), 4),
           "oracle_scale_error_pct_of_marble_metric": round(100 * (1 / scale_of(T_oracle) - 1), 2),
           "oracle_scale_scan": res_scan,
           "roi": {"lo": lo.round(2).tolist(), "hi": hi.round(2).tolist(), "note": "GT coverage-grid box = criteria area around the meter"}}
    for tag, T in (("metric_camera_rigid", T_metric), ("oracle_similarity_icp", T_oracle)):
        P = apply(T, X)
        m = roi(P)
        P = P[m]
        P = P[np.random.default_rng(1).choice(len(P), min(len(P), 300_000), replace=False)]
        own_cams = [Camera(n, transform_pose(T, cams_src[n]["pose"]), cams_src[n]["K"], cams_src[n]["w"], cams_src[n]["h"]) for n in names]
        own = VisibilityOracle(own_cams, P).seen_by(P, None, abs_tol=SEEN_TOL_M).any(1)
        tru = truth_oracle.seen_by(P, None, abs_tol=SEEN_TOL_M).any(1)
        dl, _ = ltree.query(P, distance_upper_bound=5.0)
        in_frustum = np.zeros(len(P), bool)
        for c in true_cams:
            u, v, z = project_arkit(c.pose, c.K, P)
            in_frustum |= (z > 0.3) & (u >= 0) & (u < c.w) & (v >= 0) & (v < c.h)
        checkable = dl <= CHECK_M
        disagree = checkable & (dl > DISAGREE_M)
        ground = P[:, 1] < 0.3
        classes = {"outside_all_input_frusta": ~in_frustum, "in_frustum_but_hidden": in_frustum & ~tru, "seen": tru}

        def share(a, b):
            return round(float(a.sum() / max(1, b.sum())), 4)

        allp = np.ones(len(P), bool)
        res[tag] = {
            "points_in_roi": int(m.sum()), "points_sampled": int(len(P)),
            "producer_flag_generated_share": share(~own, allp),
            "flag_vs_truth": {"flag_and_truly_unseen": share(~own & ~tru, allp), "flag_but_truly_seen": share(~own & tru, allp),
                              "not_flagged_but_truly_unseen": share(own & ~tru, allp)},
            "classes": {k: {"share": share(c, allp), "ground_share": share(c & ground, c),
                            "checkable_share": share(c & checkable, c),
                            "disagree_share_of_checkable": share(c & disagree, c & checkable),
                            "disagree_share_of_checkable_above_ground": share(c & disagree & ~ground, c & checkable & ~ground),
                            "median_dist_to_laser_m": round(float(np.median(np.minimum(dl[c], 5.0))), 3) if c.any() else None}
                        for k, c in classes.items()},
            "producer_flagged": {"disagree_share_of_checkable": share(~own & disagree, ~own & checkable)},
            "producer_unflagged": {"disagree_share_of_checkable": share(own & disagree, own & checkable)},
        }
        if tag == "metric_camera_rigid":
            write_ply(out / "points_flagged.ply", P.astype(np.float32),
                      {"generated": (~own).astype(np.uint8), "truth_unseen": (~tru).astype(np.uint8), "disagree": disagree.astype(np.uint8), "outside_frusta": (~in_frustum).astype(np.uint8)})
    (out / "invention.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
