"""Mode 2, cheap and multi-seed: metric scale for a no-pose SfM model from ARKit-noisy camera
centres, without rerunning SfM.

    python scale_study.py <scene> <sfm_run> [--seeds 50]

Per profile and seed: draw ARKit-like poses (packet generator drift model, resets allowed), take
the largest tracking epoch, similarity-fit the SfM camera centres to the noisy ARKit centres
(least squares, and RANSAC + least squares on inliers), and compare the resulting scale with the
true one (similarity fit to the ground-truth centres). Also reports the raw ARKit odometry
scale error of that draw for reference.
"""

from __future__ import annotations

import argparse

import numpy as np

from common import WORK, dump_json, read_text_model, umeyama
from sfm import arkit_poses

PROFILES = ("lidar_modern", "nonlidar_modern", "nonlidar_2018")


def ransac_sim3(src, dst, thr, rng, iters=300):
    best = None
    for _ in range(iters):
        i = rng.choice(len(src), 3, replace=False)
        s, R, t = umeyama(src[i], dst[i], True)
        r = np.linalg.norm(s * src @ R.T + t - dst, axis=1)
        k = r < thr
        if best is None or k.sum() > best.sum():
            best = k
    return umeyama(src[best], dst[best], True), int(best.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("run")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--order", choices=["name", "tour"], default="name")
    a = ap.parse_args()
    d = WORK / a.scene
    _, gt_images = read_text_model(d / "gt_model")
    _, rec_images = read_text_model(d / "sparse" / a.run)
    out = {"scene": a.scene, "run": a.run, "seeds": a.seeds, "profiles": {}}
    for prof in PROFILES:
        rows = []
        for seed in range(a.seeds):
            ak = arkit_poses(a.scene, prof, seed, order=a.order)
            info = ak["info"]
            names = np.array(info["names"])
            epoch = np.asarray(info["epoch"])
            big = np.bincount(epoch).argmax()
            keep = [(n, c) for n, c, e in zip(names, info["c_obs"], epoch) if e == big and n in rec_images]
            nn = [k[0] for k in keep]
            C_obs = np.array([k[1] for k in keep])
            C_sfm = np.array([rec_images[n]["center"] for n in nn])
            C_gt = np.array([gt_images[n]["center"] for n in nn])
            s_true = umeyama(C_sfm, C_gt, True)[0] * info["gt_to_ark"]["scale"]
            s_ls = umeyama(C_sfm, C_obs, True)[0]
            rng = np.random.default_rng(1000 + seed)
            (s_rs, _, _), n_in = ransac_sim3(C_sfm, C_obs, 0.3, rng)
            rows.append({"seed": seed, "frames": len(nn), "reset": info["reset_index"] is not None,
                         "arkit_scale_err": info["scales"][big] - 1.0,
                         "sfm_ls_scale_err": s_ls / s_true - 1.0, "sfm_ransac_scale_err": s_rs / s_true - 1.0,
                         "ransac_inliers": n_in, "session_multiplier": info["session_multiplier"],
                         "path_length_m": info["path_length_m"]})
        summ = {}
        for k in ("arkit_scale_err", "sfm_ls_scale_err", "sfm_ransac_scale_err"):
            v = np.abs([r[k] for r in rows]) * 100
            summ[k + "_abs_pct"] = {"p50": float(np.median(v)), "p90": float(np.percentile(v, 90)),
                                    "max": float(v.max())}
            # Error on a distance at the criteria reach (26 ft = 7.92 m from the meter), inches.
            summ[k + "_at_26ft_in"] = {q: summ[k + "_abs_pct"][q] / 100 * 7.92 * 39.37 for q in ("p50", "p90")}
        summ["resets"] = int(sum(r["reset"] for r in rows))
        summ["path_length_m"] = rows[0]["path_length_m"]
        out["profiles"][prof] = {"summary": summ, "rows": rows}
        print(prof, summ)
    out["order"] = a.order
    dump_json(d / "scores" / f"scale_study_{a.run}_{a.order}.json", out)


if __name__ == "__main__":
    main()
