"""A10 second-stage methods, built on finished run.py rows of the same scene / quality / set.

    python hybrid.py <scene> <quality> <set> <method> [...]
    pi3c  pi3's own output, made self-consistent: per-view depth scale reconciled against the other views
          (z-buffer of their points, 3 iterations, median ratio), views that still disagree dropped
          (< 40 % of pixels within 5 %), lowest-confidence 30 % of pixels dropped; no GT used.
          Cameras: pi3's, true scale by Sim(3) to the GT centres (like the pi3 row).
    cda3  DA3-Large fed the images-only COLMAP poses of the same set (registered views only) and the known K;
          depth comes back in the pose scale. Poses at true scale by the COLMAP row's Sim(3) to GT.
    cpi3  pi3 depth per view, rescaled to the COLMAP sparse points that view observes (median ratio), placed
          at the COLMAP poses (true scale).
All three are scored with score-cloud --metric (rotation-aware rigid alignment) and run through C1.
Rows: results/<scene>/<quality>/<set>/<method>.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pycolmap

import run as R

KEEP_CONF = 0.7


def read_view(path: Path):
    z = np.load(path)
    d = np.where(z["valid"], z["depth"], np.nan).astype(np.float64)
    conf = z["conf"] if "conf" in z.files and z["conf"].shape == d.shape else None
    return d, np.array(z["intrinsics"], float), z["cam_to_world"].astype(np.float64), conf


def backproject(d, K, T, step=4):
    fx, fy, cx, cy = K
    v, u = np.mgrid[0:d.shape[0]:step, 0:d.shape[1]:step]
    zz = d[v, u]
    ok = np.isfinite(zz) & (zz > 0)
    u, v, zz = u[ok], v[ok], zz[ok]
    pc = np.c_[(u - cx) / fx * zz, (v - cy) / fy * zz, zz]
    return pc @ T[:3, :3].T + T[:3, 3]


def zbuffer(P, K, T, shape, step=4):
    fx, fy, cx, cy = K
    pc = (P - T[:3, 3]) @ T[:3, :3]
    pc = pc[pc[:, 2] > 0.3]
    u = np.round((fx * pc[:, 0] / pc[:, 2] + cx) / step).astype(int)
    v = np.round((fy * pc[:, 1] / pc[:, 2] + cy) / step).astype(int)
    h, w = (shape[0] + step - 1) // step, (shape[1] + step - 1) // step
    ok = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    zb = np.full((h, w), np.inf)
    np.minimum.at(zb, (v[ok], u[ok]), pc[ok, 2])
    return zb


def export(views: list[dict], out: Path) -> None:
    rng = np.random.default_rng(0)
    per_view = R.CLOUD_BUDGET // max(len(views), 1)
    pts, cams = [], []
    for vw in views:
        d, K, T = vw["d"], vw["K"], vw["T"]
        if vw.get("conf") is not None:
            c = vw["conf"]
            thr = np.nanpercentile(c[np.isfinite(d)], 100 * (1 - KEEP_CONF))
            d = np.where(c >= thr, d, np.nan)
        v, u = np.nonzero(np.isfinite(d) & (d > 0))
        sel = rng.choice(len(u), min(per_view, len(u)), replace=False)
        u, v = u[sel], v[sel]
        zz = d[v, u]
        fx, fy, cx, cy = K
        pc = np.c_[(u - cx) / fx * zz, (v - cy) / fy * zz, zz]
        pts.append(pc @ T[:3, :3].T + T[:3, 3])
        Rcw = T[:3, :3].T
        cams.append({"image": vw["id"], "R_cw": Rcw.tolist(), "t_cw": (-Rcw @ T[:3, 3]).tolist(),
                     "intrinsics": list(map(float, K)), "w": int(d.shape[1]), "h": int(d.shape[0])})
    P = np.concatenate(pts)
    _, keep = np.unique(np.floor(P / R.VOXEL).astype(np.int64), axis=0, return_index=True)
    out.mkdir(parents=True, exist_ok=True)
    R.write_ply(P[np.sort(keep)], out / "cloud.ply")
    (out / "cameras.json").write_text(json.dumps(cams))


def reconcile(views: list[dict]) -> dict:
    """Per-view depth scale against the other views' points; returns diagnostics."""
    hist = []
    for it in range(3):
        clouds = [backproject(v["d"] * v["s"], v["K"], v["T"], 6) for v in views]
        ratios = []
        for i, v in enumerate(views):
            others = np.concatenate([c for j, c in enumerate(clouds) if j != i and len(c)])
            zb = zbuffer(others, v["K"], v["T"], v["d"].shape, 4)
            di = (v["d"] * v["s"])[::4, ::4][: zb.shape[0], : zb.shape[1]]
            m = np.isfinite(zb) & np.isfinite(di) & (di > 0)
            r = di[m] / zb[m]
            r = r[np.abs(np.log(r)) < 0.4]
            if len(r) < 200:
                ratios.append((np.nan, 0.0))
                continue
            med = float(np.median(r))
            ratios.append((med, float(np.mean(np.abs(r / med - 1) < 0.05))))
        for v, (med, _) in zip(views, ratios):
            if np.isfinite(med):
                v["s"] /= med
        g = np.median([v["s"] for v in views])
        for v in views:
            v["s"] /= g
        hist.append([round(x[0], 3) if np.isfinite(x[0]) else None for x in ratios])
    for v, (_, agree) in zip(views, ratios):
        v["agree"] = agree
    return {"ratio_history": hist, "agree": [round(v["agree"], 2) for v in views]}


def pi3c(scene, quality, set_name, out: Path) -> dict:
    src = R.RUNS / scene / quality / set_name / "pi3"
    mem = R.members(scene, set_name)
    gt = R.gt_c2w(scene)
    C = np.array([np.load(src / "preds" / f"{m}.npz")["cam_to_world"][:3, 3] for m in mem])
    G = np.array([gt[m][:3, 3] for m in mem])
    views = []
    for m in mem:
        d, K, T, conf = read_view(src / "preds" / f"{m}.npz")
        views.append({"id": m, "d": d, "K": K, "T": T, "conf": conf, "s": 1.0})
    diag = reconcile(views)
    kept = [v for v in views if v["agree"] >= 0.4]
    # Sim(3) to GT on the kept views only (true scale), then apply.
    Ck = np.array([v["T"][:3, 3] for v in kept])
    Gk = np.array([gt[v["id"]][:3, 3] for v in kept])
    s, Rm, t = R.umeyama(Ck, Gk)
    for v in kept:
        T = v["T"].copy()
        T[:3, 3] = s * Rm @ T[:3, 3] + t
        T[:3, :3] = Rm @ T[:3, :3]
        v["T"], v["d"] = T, v["d"] * v["s"] * s
    export(kept, out)
    err = np.linalg.norm(s * Ck @ Rm.T + t - Gk, axis=1)
    return {"registered": len(kept), "dropped": [v["id"] for v in views if v["agree"] < 0.4],
            "view_scales": {v["id"]: round(v["s"], 3) for v in views}, "reconcile": diag,
            "centre_rms_sim3_m": float(np.sqrt(np.mean(err**2)))}


def colmap_poses(scene, quality, set_name):
    model = R.RUNS / scene / quality / set_name / "colmap" / "model"
    rec = pycolmap.Reconstruction(str(model))
    gt = R.gt_c2w(scene)
    poses, stems = {}, []
    for im in rec.images.values():
        if im.has_pose:
            T = np.eye(4)
            T[:3, :3] = im.cam_from_world().rotation.matrix().T
            T[:3, 3] = im.projection_center()
            poses[Path(im.name).stem] = (T, im)
    stems = list(poses)
    C = np.array([poses[m][0][:3, 3] for m in stems])
    G = np.array([gt[m][:3, 3] for m in stems])
    s, Rm, t = R.umeyama(C, G)
    out = {}
    for m in stems:
        T = poses[m][0].copy()
        T[:3, 3] = s * Rm @ T[:3, 3] + t
        T[:3, :3] = Rm @ T[:3, :3]
        out[m] = T
    pts = np.array([p.xyz for p in rec.points3D.values()])
    pts = s * pts @ Rm.T + t
    obs = {}
    for pid, p in rec.points3D.items():
        for el in p.track.elements:
            obs.setdefault(el.image_id, []).append(pid)
    ids = {Path(im.name).stem: im.image_id for im in rec.images.values() if im.has_pose}
    pid_index = {pid: i for i, pid in enumerate(rec.points3D.keys())}
    vis = {m: pts[[pid_index[p] for p in obs.get(ids[m], [])]] for m in stems}
    return out, vis, s


def cpi3(scene, quality, set_name, out: Path) -> dict:
    src = R.RUNS / scene / quality / set_name / "pi3"
    poses, vis, _ = colmap_poses(scene, quality, set_name)
    views, ratios = [], {}
    for m, T in poses.items():
        d, K, _, conf = read_view(src / "preds" / f"{m}.npz")
        P = vis[m]
        if len(P) < 30:
            continue
        pc = (P - T[:3, 3]) @ T[:3, :3]
        u = np.round(K[0] * pc[:, 0] / pc[:, 2] + K[2]).astype(int)
        v = np.round(K[1] * pc[:, 1] / pc[:, 2] + K[3]).astype(int)
        ok = (pc[:, 2] > 0.3) & (u >= 0) & (u < d.shape[1]) & (v >= 0) & (v < d.shape[0])
        dd = d[v[ok], u[ok]]
        good = np.isfinite(dd) & (dd > 0)
        if good.sum() < 30:
            continue
        r = float(np.median(pc[ok, 2][good] / dd[good]))
        ratios[m] = round(r, 4)
        views.append({"id": m, "d": d * r, "K": K, "T": T, "conf": conf})
    export(views, out)
    return {"registered": len(views), "per_view_scale": ratios}


def cda3(scene, quality, set_name, out: Path) -> dict:
    import cv2  # noqa: PLC0415
    from a2ff.common import write_view  # noqa: PLC0415
    from models.common import cover_crop_resample, intrinsics_to_network, to_network_image  # noqa: PLC0415

    poses, _, _ = colmap_poses(scene, quality, set_name)
    net = (504, 336) if quality == "dslr_ref" else (504, 378)
    key = ("da3", net)
    if key not in R._ADAPTERS:
        R._ADAPTERS.clear()
        R._ADAPTERS[key] = R.ff_adapter("da3", net)
    wd = R.degrade.make(scene, quality)
    frames = json.loads((wd / "frames.json").read_text())["frames"]
    by_stem = {Path(n).stem: f for n, f in frames.items()}
    mem = [m for m in R.members(scene, set_name) if m in poses]
    rgbs, rs, Ks = [], [], []
    for m in mem:
        f = by_stem[m]
        img = cv2.imread(str(wd / "images" / f["file"]))[..., ::-1]
        W, H = 1024, int(round(f["h"] * 1024 / f["w"]))
        rgb = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        r = cover_crop_resample(W, H, net[0], net[1])
        rgbs.append(to_network_image(np.ascontiguousarray(rgb), r))
        rs.append(r)
        s = W / f["w"]
        fx, fy, cx, cy = f["K"]
        Ks.append(np.array([fx * s, fy * H / f["h"], cx * s - 0.5, cy * H / f["h"] - 0.5]))
    K_net = [intrinsics_to_network(K, r) for K, r in zip(Ks, rs)]
    t0 = time.perf_counter()
    preds = R._ADAPTERS[key].infer(rgbs, K_net, [poses[m] for m in mem])
    infer_s = round(time.perf_counter() - t0, 1)
    run = out / "preds"
    for m, r, p in zip(mem, rs, preds):
        write_view(run, m, p["depth"], p["K"], p["c2w"], r, p.get("conf"), p.get("mask"))
    views = []
    shift = []
    for m in mem:
        d, K, T, conf = read_view(run / f"{m}.npz")
        shift.append(float(np.linalg.norm(T[:3, 3] - poses[m][:3, 3])))
        views.append({"id": m, "d": d, "K": K, "T": poses[m], "conf": conf})
    export(views, out)
    return {"registered": len(views), "infer_s": infer_s, "da3_pose_shift_median_m": float(np.median(shift))}


def main() -> None:
    scene, quality, set_name, methods = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    for method in methods:
        res = R.HERE / "results" / scene / quality / set_name / f"{method}.json"
        if res.exists():
            continue
        out = R.RUNS / scene / quality / set_name / method
        t0 = time.time()
        try:
            rep = {"pi3c": pi3c, "cpi3": cpi3, "cda3": cda3}[method](scene, quality, set_name, out)
        except Exception as e:  # noqa: BLE001
            rep = {"error": repr(e)[-600:]}
        rep.update(scene=scene, quality=quality, set=set_name, method=method, n=len(R.members(scene, set_name)))
        if rep.get("registered", 0) >= 3:
            try:
                rep["score"] = R.score(scene, out, ["--cameras", str(out / "cameras.json"), "--metric", "--align", "rotations"],
                                       f"A10-{method}-{quality}-{set_name}")
                rep["c1"] = R.criteria(out)
            except Exception as e:  # noqa: BLE001
                rep["score_error"] = repr(e)[-600:]
        rep["total_s"] = round(time.time() - t0, 1)
        res.parent.mkdir(parents=True, exist_ok=True)
        res.write_text(json.dumps(rep, indent=1, default=float))
        s = rep.get("score", {}).get("kd", {})
        c = rep.get("c1", {}).get("g80", {})
        print(f"{time.strftime('%T')} {scene} {quality} {set_name} {method}: reg {rep.get('registered')}/{rep['n']} "
              f"kd n={s.get('n')} med={s.get('median_in')} p90={s.get('p90_in')} c1={c.get('decision')} "
              f"dec={c.get('decidable_share')} {rep.get('error', '')[:300]} {rep.get('score_error', '')[-300:]}", flush=True)


if __name__ == "__main__":
    main()
