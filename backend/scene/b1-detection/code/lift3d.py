"""Lift 2D window/door detections on the 76 ETH3D facade frames into 3D and score them against the
laser-scan ground truth of PR #13's hand-clicked features (results/eth3d_gt.json).

Two lifts:
- depth: back-project each box centre with laser-scan depth rendered into that frame (the oracle
  stand-in for a LiDAR depth map or a dense reconstruction), then cluster across views;
- triangulate: no depth at all. Box centres of nearby frame pairs that agree epipolarly are
  triangulated, the candidate points are voted on by every frame (does the point project inside a
  same-class box there?), and winners are refined by multi-view DLT with reprojection gating.

Scoring ROI: the ground-floor band of walls w1-w3, where PR #13's labels are complete (centre height
-1.5..2.5 m in scan z, within 1.5 m of the wall lines). Poses can be corrupted with the packet
generator's ARKit drift profiles.

usage: python lift3d.py --dets ../results/facade76/gdino-tiny.jsonl --thr 0.35 \
           [--noise lidar_modern,nonlidar_modern,nonlidar_2018 --seeds 20]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from common import B1, STORE, WORK, read_jsonl
from eth3d import camera_center, depth_map, load_poses, load_scan, pr13_annotations

sys.path.insert(0, str(STORE / "internal/packet-generator"))
from packetgen.noise import apply_drift, corrupt_intrinsics, load_profiles  # noqa: E402

CLASSES = ("window", "door")
MATCH_M = 0.8
# scan z-up -> packet generator y-up
A = np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])


def load_dets(path, thr):
    out = {}
    for r in read_jsonl(path):
        if "error" in r:
            continue
        name = r["id"].split("/")[-1]
        out[name] = [d for d in r["dets"] if d["label"] in CLASSES and d["score"] >= thr]
    return out


def wall_segments():
    ann = pr13_annotations()
    return [np.array(w["rough_scan_xy_m"]) for w in ann["walls"]]


def in_roi(X, walls):
    if not (-1.5 <= X[2] <= 2.5):
        return False
    p = X[:2]
    for a, b in walls:
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1)
        if np.linalg.norm(p - (a + t * ab)) < 1.5:
            return True
    return False


def P_of(pose):
    return pose["K"] @ np.hstack([pose["R"], pose["t"][:, None]])


def box_px(d, pose):
    x0, y0, x1, y1 = d["box"]
    return np.array([x0 * pose["w"], y0 * pose["h"], x1 * pose["w"], y1 * pose["h"]])


def centre(b):
    return np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])


def backproject(pose, u, v, z):
    K = pose["K"]
    xc = np.array([(u - K[0, 2]) / K[0, 0] * z, (v - K[1, 2]) / K[1, 1] * z, z])
    return pose["R"].T @ (xc - pose["t"])


def cluster(points, radius=0.6, min_views=2):
    """points: list of (X, cls, frame, score). Greedy clustering by class; returns objects."""
    objs = []
    for X, c, f, s in sorted(points, key=lambda p: -p[3]):
        for o in objs:
            if o["cls"] == c and np.linalg.norm(np.median(o["X"], 0) - X) < radius:
                o["X"].append(X)
                o["frames"].add(f)
                break
        else:
            objs.append({"cls": c, "X": [X], "frames": {f}})
    return [{"cls": o["cls"], "X": np.median(o["X"], 0), "n_views": len(o["frames"])}
            for o in objs if len(o["frames"]) >= min_views]


def lift_depth(dets, poses_obs, depths):
    pts = []
    for name, ds in dets.items():
        if name not in depths:
            continue
        D, pose = depths[name], poses_obs[name]
        for d in ds:
            b = box_px(d, pose) / 2  # depth maps are at 1/2 resolution
            w, h = b[2] - b[0], b[3] - b[1]
            patch = D[int(b[1] + h / 4): int(b[3] - h / 4) + 1, int(b[0] + w / 4): int(b[2] - w / 4) + 1]
            f = patch[np.isfinite(patch)]
            if f.size < 5:
                continue
            z = float(np.percentile(f, 30))
            u, v = centre(box_px(d, pose))
            pts.append((backproject(pose, u, v, z), d["label"], name, d["score"]))
    return cluster(pts)


def dlt(Ps, uvs):
    rows = []
    for P, (u, v) in zip(Ps, uvs):
        rows.append(u * P[2] - P[0])
        rows.append(v * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.array(rows))
    X = Vt[-1]
    return X[:3] / X[3]


def reproj(P, X):
    x = P @ np.append(X, 1)
    return x[:2] / x[2], x[2]


def lift_triangulate(dets, poses, names, max_gap=6, epi_px=25, vote_min=3, reproj_px=30, min_angle_deg=0.0):
    Ps = {n: P_of(poses[n]) for n in names}
    C = {n: camera_center(poses[n]) for n in names}
    cands = []
    for i, a in enumerate(names):
        for b in names[i + 1: i + 1 + max_gap]:
            if np.linalg.norm(C[a] - C[b]) < 0.3:
                continue
            for da in dets.get(a, []):
                ua = centre(box_px(da, poses[a]))
                for db in dets.get(b, []):
                    if db["label"] != da["label"]:
                        continue
                    ub = centre(box_px(db, poses[b]))
                    X = dlt([Ps[a], Ps[b]], [ua, ub])
                    ea, za = reproj(Ps[a], X)
                    eb, zb = reproj(Ps[b], X)
                    if za <= 0 or zb <= 0 or np.linalg.norm(ea - ua) > epi_px or np.linalg.norm(eb - ub) > epi_px:
                        continue
                    if np.linalg.norm(X - C[a]) > 60:
                        continue
                    ra, rb = X - C[a], X - C[b]
                    ang = np.degrees(np.arccos(np.clip(ra @ rb / np.linalg.norm(ra) / np.linalg.norm(rb), -1, 1)))
                    if ang < min_angle_deg:
                        continue
                    cands.append((X, da["label"]))
    if not cands:
        return []
    # voxel-merge candidates, then vote with every frame
    vox = {}
    for X, c in cands:
        k = (c, *np.floor(X / 0.25).astype(int))
        vox.setdefault(k, []).append(X)
    voted = []
    for k, xs in vox.items():
        c, X = k[0], np.mean(xs, 0)
        support = []
        for n in names:
            (u, v), z = reproj(Ps[n], X)
            if z <= 0:
                continue
            for d in dets.get(n, []):
                if d["label"] != c:
                    continue
                bb = box_px(d, poses[n])
                if bb[0] <= u <= bb[2] and bb[1] <= v <= bb[3]:
                    support.append((n, centre(bb)))
                    break
        if len(support) >= vote_min:
            voted.append((len(support) + 0.01 * len(xs), c, X, support))
    # NMS in 3D by support, then refine with DLT over supporting box centres
    objs = []
    for s, c, X, support in sorted(voted, key=lambda v: -v[0]):
        if any(o["cls"] == c and np.linalg.norm(o["X0"] - X) < 0.8 for o in objs):
            continue
        views = support
        for _ in range(3):
            Xr = dlt([Ps[n] for n, _ in views], [uv for _, uv in views])
            keep = [(n, uv) for n, uv in views if np.linalg.norm(reproj(Ps[n], Xr)[0] - uv) < reproj_px]
            if len(keep) < vote_min or len(keep) == len(views):
                break
            views = keep
        if len(views) >= vote_min and not any(o["cls"] == c and np.linalg.norm(o["X"] - Xr) < 0.8 for o in objs):
            objs.append({"cls": c, "X0": X, "X": Xr, "n_views": len(views)})
    return objs


def score(objs, gt, walls, class_agnostic=False):
    objs = [o for o in objs if in_roi(o["X"], walls)]
    used, errs, errs_xy, found = set(), [], [], []
    for g in gt:
        gc = np.array(g["center"])
        best, bj = MATCH_M, -1
        for j, o in enumerate(objs):
            if j in used or (not class_agnostic and o["cls"] != g["type"]):
                continue
            dd = np.linalg.norm(o["X"] - gc)
            if dd < best:
                best, bj = dd, j
        if bj >= 0:
            used.add(bj)
            errs.append(best)
            errs_xy.append(float(np.linalg.norm(objs[bj]["X"][:2] - gc[:2])))
            found.append(g["label"])
    return {"recovered": len(found), "n_gt": len(gt), "false_3d": len(objs) - len(used),
            "centre_err_m_median": float(np.median(errs)) if errs else None,
            "centre_err_m_max": float(np.max(errs)) if errs else None,
            "centre_err_xy_m_median": float(np.median(errs_xy)) if errs_xy else None, "found": found,
            "roi_objects": [{"cls": o["cls"], "X": [round(float(v), 2) for v in o["X"]], "n_views": o["n_views"]} for o in objs]}


def noisy_poses(poses, names, profile, rng):
    R_c2w = np.stack([A @ poses[n]["R"].T for n in names])
    c = np.stack([A @ camera_center(poses[n]) for n in names])
    R_obs, c_obs, _, _ = apply_drift(R_c2w, c, profile, rng, reset_index=None)
    out = {}
    for k, n in enumerate(names):
        Rw = A.T @ R_obs[k]
        cw = A.T @ c_obs[k]
        R = Rw.T
        K = poses[n]["K"]
        fx, fy, cx, cy = corrupt_intrinsics((K[0, 0], K[1, 1], K[0, 2], K[1, 2]), poses[n]["w"], profile, rng)
        out[n] = {**poses[n], "R": R, "t": -R @ cw, "K": np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dets", required=True)
    ap.add_argument("--thr", type=float, default=0.0)
    ap.add_argument("--noise", default="")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    poses = load_poses()
    names = sorted(poses)
    gt = json.loads((B1 / "results/eth3d_gt.json").read_text())
    walls = wall_segments()
    dets = load_dets(args.dets, args.thr)
    depths = cached_depths(poses, names)
    res = {"dets": args.dets, "thr": args.thr, "n_frames_with_dets": len(dets)}
    res["clean"] = {
        "depth": score(lift_depth(dets, poses, depths), gt, walls),
        "triangulate": score(lift_triangulate(dets, poses, names), gt, walls),
        "triangulate_class_agnostic": score(lift_triangulate(dets, poses, names), gt, walls, class_agnostic=True),
    }
    for k, v in res["clean"].items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "roi_objects"})
    res["noise"] = {}
    profiles = load_profiles()
    for pname in [p for p in args.noise.split(",") if p]:
        runs = []
        for s in range(args.seeds):
            rng = np.random.default_rng(1000 + s)
            pn = noisy_poses(poses, names, profiles[pname], rng)
            runs.append({"seed": s,
                         "depth": score(lift_depth(dets, pn, depths), gt, walls),
                         "triangulate": score(lift_triangulate(dets, pn, names), gt, walls)})
        res["noise"][pname] = runs
        for m in ("depth", "triangulate"):
            e = [r[m]["centre_err_m_median"] for r in runs if r[m]["centre_err_m_median"] is not None]
            rec = [r[m]["recovered"] for r in runs]
            fp = [r[m]["false_3d"] for r in runs]
            print(pname, m, "recovered median", np.median(rec), "false median", np.median(fp),
                  "centre err median of medians", np.median(e) if e else None, "p90", np.percentile(e, 90) if e else None)
    out = Path(args.out) if args.out else B1 / f"results/lift3d_{Path(args.dets).stem}.json"
    out.write_text(json.dumps(res, indent=1))


def cached_depths(poses, names):
    path = WORK / "eth3d_facade_depths.npz"
    if path.exists():
        z = np.load(path)
        if all(n in z for n in names):
            return {n: z[n] for n in names}
    pts = load_scan()
    depths = {n: depth_map(pts, poses[n]) for n in names}
    np.savez_compressed(path, **depths)
    return depths


if __name__ == "__main__":
    main()
