"""A10 on real phone frames with real ARKit and laser truth: LaMAR LIN (CC BY-SA 4.0), A6's segments and GT chain.

    python lamar_a10.py prep                          # gt_ios.json / gt_navvis.json from the LIN COLMAP model + laser clouds
    python lamar_a10.py <segment id> <set> [<method> ...]   sets: N40_guided N60_guided N70_guided N40_careless
    methods: colmap (images only), prior (real ARKit positions, sigma 3 cm, no robust loss: A6 recipe), fixed (A6's
             fallback: triangulate with the real ARKit poses held fixed), cda3
             (DA3-Large fed the prior model's poses + ARKit K)

Frames: A6's segment frames (packet keyframes + GT-posed frames, ~0.3 m apart, 1440x1920 ARFrame JPEGs).
guided = N frames evenly spaced along the ~35 m segment (walk the whole side); careless = the first N consecutive
frames (stop early). One database per segment: SIFT 8192 at full size, exhaustive matching, the device's ARKit
intrinsics fixed, real ARKit positions as priors. Scored with A6's alignment-free laser pair scorer at the GT-posed
frames (pairs 0.5-2 m and 15-26 ft, raw and 25 cm patches): `metric` = the recon's own (ARKit) scale, `oracle` =
Sim(3) of the recon cameras to the GT cameras (true scale), `arkit_sim3` for images-only runs.
Rows: results/lamar/<segment>/<set>/<method>.json
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pycolmap

A6 = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a6-iphone-quality")
sys.path.insert(0, str(A6))
sys.path.insert(0, str(Path.home() / "a2/code"))
HERE = Path(__file__).resolve().parent
ROOT = Path.home() / "lamar"
WORK = Path.home() / "a10/lamar"
THREADS = 4
SEGMENTS = ["2022-06-17_10.43.29_s0", "2022-02-10_14.47.24_s0", "2022-02-10_14.47.24_s1"]


def prep() -> None:
    sp = next(p for p in ROOT.glob("colmap/**/sparse") if p.is_dir())
    model = sp if (sp / "images.bin").exists() or (sp / "images.txt").exists() else next(p.parent for p in sp.rglob("images.*"))
    rec = pycolmap.Reconstruction(str(model))
    ios, nav = {}, {}
    for im in rec.images.values():
        cam = rec.cameras[im.camera_id]
        p = list(map(float, cam.params))
        fx, fy, cx, cy = (p[0], p[0], p[1], p[2]) if len(p) == 3 or cam.model.name.startswith("SIMPLE") else p[:4]
        cw = im.cam_from_world()
        g = {"R": cw.rotation.matrix().tolist(), "t": list(map(float, cw.translation)), "cam": [cam.model.name, cam.width, cam.height, fx, fy, cx, cy]}
        if im.name.startswith("ios_"):
            ios[im.name] = g
        elif im.name.startswith("navvis"):
            nav[im.name] = g
    (ROOT / "gt_ios.json").write_text(json.dumps(ios))
    (ROOT / "gt_navvis.json").write_text(json.dumps(nav))
    print("gt", len(ios), "ios", len(nav), "navvis", "model", model, flush=True)
    base = ROOT / "colmap" / "LIN"
    if not base.exists():
        base.parent.mkdir(exist_ok=True)
        os.symlink(model.parent, base)
    from laser import build_cloud  # noqa: PLC0415  (reads gt_navvis.json at import)
    from lamar_io import gt_center, load_gt  # noqa: PLC0415

    gt = load_gt()
    (ROOT / "laser").mkdir(exist_ok=True)
    for seg in json.loads((A6 / "results/segments.json").read_text()):
        if seg["id"] not in SEGMENTS:
            continue
        C = np.array([gt_center(gt[v]) for v in seg["gt_views"] if v in gt])
        P = build_cloud(C, radius=15.0, voxel=0.02, step=5, cache=str(ROOT / "laser" / f"{seg['id']}.npz"))
        print(seg["id"], "laser points", len(P), "gt views", len(C), flush=True)


def segment(sid: str) -> dict:
    return next(s for s in json.loads((A6 / "results/segments.json").read_text()) if s["id"] == sid)


def set_frames(seg: dict, set_name: str) -> list[str]:
    fr = sorted(seg["frames"], key=int)
    n = min(int(set_name.split("_")[0][1:]), len(fr))
    if set_name.endswith("careless"):
        return fr[:n]
    return [fr[int(i)] for i in np.round(np.linspace(0, len(fr) - 1, n))]


def database(seg: dict) -> Path:
    from lamar_io import load_arkit, load_intrinsics  # noqa: PLC0415

    d = WORK / seg["id"]
    img = d / "images"
    db_p = d / "database.db"
    if (d / "database.done").exists():
        return db_p
    img.mkdir(parents=True, exist_ok=True)
    for t in seg["frames"]:
        dst = img / f"{t}.jpg"
        if not dst.exists():
            os.symlink(ROOT / "raw" / seg["session"] / "raw_data/images" / f"{t}.jpg", dst)
    if db_p.exists():
        db_p.unlink()
    ak, K = load_arkit(seg["session"]), load_intrinsics(seg["session"])
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = 1920
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    pycolmap.extract_features(str(db_p), str(img), camera_mode=pycolmap.CameraMode.PER_IMAGE, reader_options=reader,
                              extraction_options=ext, device=pycolmap.Device.cpu)
    db = pycolmap.Database.open(str(db_p))
    for im in db.read_all_images():
        ts = im.name[:-4]
        _, _, fx, fy, cx, cy = K[ts]
        cam = db.read_camera(im.camera_id)
        cam.params = [fx, fy, cx, cy]
        cam.has_prior_focal_length = True
        db.update_camera(cam)
        pp = pycolmap.PosePrior()
        pp.corr_data_id = im.data_id
        pp.position = ak[ts][1]
        pp.position_covariance = np.eye(3) * 0.03**2
        pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    t1 = time.time()
    mo = pycolmap.FeatureMatchingOptions()
    mo.num_threads = THREADS
    pycolmap.match_exhaustive(str(db_p), matching_options=mo, device=pycolmap.Device.cpu)
    (d / "database.done").write_text(json.dumps({"extract_s": t1 - t0, "match_s": time.time() - t1, "frames": len(seg["frames"])}))
    return db_p


def fixed(seg: dict, set_name: str, out: Path):
    """A6's fallback: triangulate the set's matches with the real ARKit poses held fixed (every frame registered)."""
    from lamar_io import load_arkit  # noqa: PLC0415

    db_p = database(seg)
    ak = load_arkit(seg["session"])
    keep = {f"{t}.jpg" for t in set_frames(seg, set_name)}
    db = pycolmap.Database.open(str(db_p))
    rec = pycolmap.Reconstruction()
    cams = {c.camera_id: c for c in db.read_all_cameras()}
    for im in db.read_all_images():
        if im.name not in keep:
            continue
        R_wc, c = ak[im.name[:-4]]
        R_cw = R_wc.T
        rec.add_camera_with_trivial_rig(cams[im.camera_id])
        im2 = pycolmap.Image(name=im.name, camera_id=im.camera_id, image_id=im.image_id)
        rec.add_image_with_trivial_frame(im2, pycolmap.Rigid3d(pycolmap.Rotation3d(R_cw), -R_cw @ c))
    db.close()
    tmp = out / "_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    t0 = time.time()
    rec = pycolmap.triangulate_points(rec, str(db_p), str(db_p.parent / "images"), str(tmp), clear_points=True, options=o,
                                      refine_intrinsics=False)
    (out / "model").mkdir(parents=True, exist_ok=True)
    rec.write(str(out / "model"))
    shutil.rmtree(tmp, ignore_errors=True)
    return rec, {"mapper_s": round(time.time() - t0, 1), "models": 1, "registered": rec.num_reg_images(), "points3D": rec.num_points3D()}


def sfm(seg: dict, set_name: str, method: str, out: Path):
    if method == "fixed":
        return fixed(seg, set_name, out)
    db_p = database(seg)
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.random_seed = 0
    o.ba_refine_focal_length = False
    o.ba_refine_principal_point = False
    o.ba_refine_extra_params = False
    o.image_names = [f"{t}.jpg" for t in set_frames(seg, set_name)]
    if method == "prior":
        o.use_prior_position = True
        o.use_robust_loss_on_prior_position = False
    tmp = out / "_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.time()
    maps = pycolmap.incremental_mapping(str(db_p), str(db_p.parent / "images"), str(tmp), options=o)
    info = {"mapper_s": round(time.time() - t0, 1), "models": len(maps), "db": json.loads((db_p.parent / "database.done").read_text())}
    if not maps:
        return None, info | {"registered": 0}
    rec = max(maps.values(), key=lambda r: r.num_reg_images())
    (out / "model").mkdir(parents=True, exist_ok=True)
    rec.write(str(out / "model"))
    shutil.rmtree(tmp, ignore_errors=True)
    info.update(registered=rec.num_reg_images(), model_sizes=sorted((m.num_reg_images() for m in maps.values()), reverse=True),
                points3D=rec.num_points3D())
    return rec, info


def cams_vs(seg, names_pose: dict, ak, gt):
    """names_pose: frame name -> camera centre in the recon frame. Returns Sim(3) fits to ARKit and to GT."""
    from lamar_io import gt_center, umeyama  # noqa: PLC0415

    gtv = {v.split("/")[1]: v for v in seg["gt_views"] if v in gt}
    reg = list(names_pose)
    C = np.array([names_pose[n] for n in reg])
    Cak = np.array([ak[n[:-4]][1] for n in reg])
    g = [n for n in reg if n in gtv]
    Cg = np.array([gt_center(gt[gtv[n]]) for n in g])
    Crg = np.array([names_pose[n] for n in g])
    s_ak, _, _ = umeyama(C, Cak)
    s_gt, R_, t_ = umeyama(Crg, Cg) if len(g) >= 3 else (np.nan, None, None)
    rigid = None
    if len(g) >= 3:
        _, R0, t0 = umeyama(Crg, Cg, scale=False)
        rigid = float(np.sqrt(((Crg @ R0.T + t0 - Cg) ** 2).sum(1).mean()))
    return gtv, s_ak, s_gt, len(g), rigid


def evaluate_sparse(seg, rec, method, ak, gt, P_laser) -> dict:
    from laser import backproject, lookup, render_depth  # noqa: PLC0415
    from pairscore import score  # noqa: PLC0415

    pose = {im.name: im.projection_center() for im in rec.images.values() if im.has_pose}
    gtv, s_ak, s_gt, ng, rigid = cams_vs(seg, pose, ak, gt)
    Dc, P, Q = {}, [], []
    for p in rec.points3D.values():
        for el in p.track.elements:
            n = rec.images[el.image_id].name
            if n not in gtv:
                continue
            g = gt[gtv[n]]
            _, w, h, fx, fy, cx, cy = g["cam"]
            if n not in Dc:
                Dc[n] = render_depth(P_laser, g["R"], g["t"], [fx, fy, cx, cy], w, h)
            uv = np.array([rec.images[el.image_id].points2D[el.point2D_idx].xy])
            z = lookup(Dc[n], uv)[0]
            if np.isfinite(z) and z < 20:
                P.append(p.xyz)
                Q.append(backproject(uv, np.array([z]), g["R"], g["t"], [fx, fy, cx, cy])[0])
            break
    P, Q = np.array(P), np.array(Q)
    res = {"cams": {"registered": len(pose), "gt_views_registered": ng, "gt_views": len(gtv), "rigid_rms_to_gt_m": rigid,
                    "scale_vs_gt_pct": float((1 / s_gt - 1) * 100) if np.isfinite(s_gt) else None}}
    if len(P) < 20:
        return res | {"note": "too few scored points"}
    if method in ("prior", "fixed"):
        res["metric"] = score(P, Q)
    else:
        res["arkit_sim3"] = score(s_ak * P, Q)
    res["oracle_scale"] = score(s_gt * P, Q)
    return res


def cda3(seg, set_name, out: Path, ak, gt, P_laser) -> dict:
    from a2ff.run import DA3  # noqa: PLC0415
    from laser import backproject, lookup, render_depth  # noqa: PLC0415
    from lamar_io import load_intrinsics  # noqa: PLC0415
    from pairscore import score  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    rec = pycolmap.Reconstruction(str(WORK / seg["id"] / "runs" / set_name / "prior" / "model"))
    K = load_intrinsics(seg["session"])
    net = (378, 504)
    frames = [im for im in rec.images.values() if im.has_pose]
    frames.sort(key=lambda im: int(im.name[:-4]))
    rgbs, Kn, T = [], [], []
    for im in frames:
        ts = im.name[:-4]
        pil = Image.open(ROOT / "raw" / seg["session"] / "raw_data/images" / f"{ts}.jpg").convert("RGB")
        w, h = pil.size
        s = net[0] / w
        rgbs.append(np.asarray(pil.resize(net, Image.BICUBIC)))
        _, _, fx, fy, cx, cy = K[ts]
        Kn.append(np.array([fx * s, fy * s, cx * s, cy * s]))
        M = np.eye(4)
        M[:3, :3] = im.cam_from_world().rotation.matrix().T
        M[:3, 3] = im.projection_center()
        T.append(M)
    a = DA3("depth-anything/DA3-LARGE-1.1", "da3L", "Apache-2.0")
    a.net = net
    a.load()
    t0 = time.perf_counter()
    preds = a.infer(rgbs, Kn, T)
    infer_s = round(time.perf_counter() - t0, 1)
    pose = {im.name: im.projection_center() for im in frames}
    gtv, s_ak, s_gt, ng, _ = cams_vs(seg, pose, ak, gt)
    P, Q = [], []
    step = 6
    for im, p, Kx, M in zip(frames, preds, Kn, T):
        if im.name not in gtv:
            continue
        g = gt[gtv[im.name]]
        _, w, h, fx, fy, cx, cy = g["cam"]
        D = render_depth(P_laser, g["R"], g["t"], [fx, fy, cx, cy], w, h)
        dep = p["depth"]
        H, W = dep.shape
        v, u = np.mgrid[0:H:step, 0:W:step]
        u, v = u.ravel(), v.ravel()
        z = dep[v, u].astype(np.float64)
        ok = np.isfinite(z) & (z > 0) & (z < 10)
        u, v, z = u[ok], v[ok], z[ok]
        kx = p["K"]
        X = np.stack([(u - kx[2]) / kx[0] * z, (v - kx[3]) / kx[1] * z, z], 1) @ M[:3, :3].T + M[:3, 3]
        uv_full = np.stack([(u + 0.5) * w / W, (v + 0.5) * h / H], 1)
        zl = lookup(D, uv_full)
        m = np.isfinite(zl) & (zl < 20)
        P.append(X[m])
        Q.append(backproject(uv_full[m], zl[m], g["R"], g["t"], [fx, fy, cx, cy]))
    P, Q = np.concatenate(P), np.concatenate(Q)
    return {"registered": len(frames), "infer_s": infer_s, "cams": {"gt_views_registered": ng},
            "metric": score(P, Q), "oracle_scale": score(s_gt * P, Q)}


def main() -> None:
    if sys.argv[1] == "prep":
        prep()
        return
    from lamar_io import load_arkit, load_gt  # noqa: PLC0415

    sid, set_name, methods = sys.argv[1], sys.argv[2], sys.argv[3:]
    seg = segment(sid)
    gt = load_gt()
    ak = load_arkit(seg["session"])
    P_laser = np.load(ROOT / "laser" / f"{sid}.npz")["pts"]
    for m in methods:
        res = HERE / "results/lamar" / sid / set_name / f"{m}.json"
        if res.exists():
            continue
        out = WORK / sid / "runs" / set_name / m
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rep = {"segment": sid, "set": set_name, "method": m, "n": len(set_frames(seg, set_name))}
        try:
            if m == "cda3":
                rep |= cda3(seg, set_name, out, ak, gt, P_laser)
            else:
                rec, info = sfm(seg, set_name, m, out)
                rep["run"] = info
                if rec is not None:
                    rep |= evaluate_sparse(seg, rec, m, ak, gt, P_laser)
        except Exception as e:  # noqa: BLE001
            rep["error"] = repr(e)[-600:]
        rep["total_s"] = round(time.time() - t0, 1)
        res.parent.mkdir(parents=True, exist_ok=True)
        res.write_text(json.dumps(rep, indent=1, default=float))
        k = "metric" if "metric" in rep else "arkit_sim3"
        L = (rep.get(k) or {}).get("patch 4.57-7.92m (15-26 ft)") or {}
        O = (rep.get("oracle_scale") or {}).get("patch 4.57-7.92m (15-26 ft)") or {}
        S = (rep.get(k) or {}).get("patch 0.5-2m (openings)") or {}
        print(f"{time.strftime('%T')} lamar {sid} {set_name} {m}: reg {rep.get('run', {}).get('registered', rep.get('registered'))}/{rep['n']} "
              f"long {k} {L.get('med_in')}/{L.get('p90_in')} oracle {O.get('med_in')}/{O.get('p90_in')} open {S.get('med_in')}/{S.get('p90_in')} "
              f"{rep.get('error', '')[:200]}", flush=True)


if __name__ == "__main__":
    main()
