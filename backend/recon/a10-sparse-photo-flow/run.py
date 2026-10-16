"""A10: reconstruct one homeowner photo set with one method, score it, run C1 on the result.

    python run.py <scene: facade|electro|delivery_area> <quality: dslr_ref|combo_day> <set> <method> [<method> ...]
    methods: colmap (images only); prior (COLMAP + ARKit position priors, A6 recipe: sigma 3 cm, no robust loss, prior
             positions = true centres x 1.026 + 2 cm jitter, calibrated to A6's real no-depth LaMAR sessions);
             prior_old (A1 recipe: sigma 0.3 m robust on A1's simulated nonlidar_modern draw); pi3, da3, vggt

Sets come from selections/eth3d-<site>.json (select.py). Images: A7's degrade.make (dslr_ref = the DSLR
frame at 2048 px, JPEG q95; combo_day = iPhone 15 main-camera FOV at 1920x1440, ISO 50, 1/1000 s,
30 deg/s hand motion + rolling shutter, Smart-HDR-like tone map + sharpen, JPEG q90).

COLMAP: one database per scene x quality with every image (SIFT 8192, exhaustive matching, PINHOLE
intrinsics fixed to the known K, which a phone's EXIF gives); each set is mapped with
IncrementalPipelineOptions.image_names. `colmap` is scored shape-only (similarity to the GT cameras =
true scale); `prior` uses A1's stored nonlidar_modern seed-0 ARKit positions (sigma 0.3 m, robust) and is
scored rigidly (--metric), so ARKit's drawn scale error stays in.
pi3 / da3 / vggt: images only, all N views in one pass, cloud scaled by a Sim(3) fit of the model's camera
centres to the GT centres (true scale), then A2's rotation-aware rigid alignment.

Outputs: ~/a10/runs/<scene>/<quality>/<set>/<method>/{cloud.ply, cameras|model, score/, c1/} and a summary row
<store>/internal/breadth/a10-sparse-photo/results/<scene>/<quality>/<set>/<method>.json
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

A7 = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/breadth/a7-iphone-degradation")
sys.path.insert(0, str(A7))
sys.path.insert(0, str(Path.home() / "a2/code"))

import numpy as np  # noqa: E402
import open3d as o3d  # noqa: E402
import pycolmap  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import degrade  # noqa: E402

HOME = Path.home()
degrade.WORK = HOME / "a10/work"
STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
HERE = STORE / "internal/breadth/a10-sparse-photo"
A1 = STORE / "internal/breadth/a1-classical/results"
HARNESS = HOME / "a10/harness"
RUNS = HOME / "a10/runs"
SITE = {"facade": "eth3d-facade", "electro": "eth3d-electro", "delivery_area": "eth3d-delivery"}
GT = {"facade": HOME / "a10/gt/facade-gt/scene.json", "electro": HOME / "a10/gt/electro-gt/scene.json",
      "delivery_area": HOME / "a10/gt/delivery-gt/scene.json"}
THREADS = 4
CLOUD_BUDGET = 900_000  # points before the 2 cm voxel, split over the views
VOXEL = 0.02


def qvec_to_R(q):
    w, x, y, z = np.asarray(q, float) / np.linalg.norm(q)
    return np.array([[1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
                     [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
                     [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y]])


def umeyama(src, dst, with_scale=True):
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / a.var(0).sum()) if with_scale else 1.0
    return s, R, mu_d - s * R @ mu_s


def gt_c2w(scene: str) -> dict[str, np.ndarray]:
    _, imgs = degrade.read_cameras(scene)
    out = {}
    for n, im in imgs.items():
        R = qvec_to_R(im["q"])
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R.T, -R.T @ np.array(im["t"])
        out[Path(n).stem] = T
    return out


def members(scene: str, set_name: str) -> list[str]:
    return json.loads((HERE / "selections" / f"{SITE[scene]}.json").read_text())["sets"][set_name]["images"]


def write_ply(P: np.ndarray, path: Path) -> None:
    P = P.astype(np.float32)
    with open(path, "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(P)}\nproperty float x\n"
                f"property float y\nproperty float z\nend_header\n".encode())
        f.write(P.tobytes())


# ---------------------------------------------------------------- COLMAP


def database(scene: str, quality: str) -> Path:
    wd = degrade.make(scene, quality)
    db_path = wd / "database.db"
    done = wd / "database.done"
    if done.exists():
        return db_path
    if db_path.exists():
        db_path.unlink()
    frames = json.loads((wd / "frames.json").read_text())["frames"]
    names = sorted(f["file"] for f in frames.values())
    K_by_file = {f["file"]: f["K"] for f in frames.values()}
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = 2048
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    pycolmap.extract_features(str(db_path), str(wd / "images"), image_names=names,
                              camera_mode=pycolmap.CameraMode.PER_IMAGE, reader_options=reader,
                              extraction_options=ext, device=pycolmap.Device.cpu)
    db = pycolmap.Database.open(str(db_path))
    for img in db.read_all_images():
        cam = db.read_camera(img.camera_id)
        cam.params = np.array(K_by_file[img.name], float)
        cam.has_prior_focal_length = True
        db.update_camera(cam)
    db.close()
    t1 = time.time()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(db_path), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    pri = json.loads((A1 / scene / "sparse/arkit_nonlidar_modern_s0_poses.json").read_text())
    c_obs = {Path(n).stem: np.array(c) for n, c in zip(pri["names"], pri["c_obs"])}
    db = pycolmap.Database.open(str(db_path))
    db.clear_pose_priors()
    for img in db.read_all_images():
        pp = pycolmap.PosePrior()
        pp.corr_data_id = img.data_id
        pp.position = c_obs[Path(img.name).stem]
        pp.position_covariance = np.eye(3) * 0.3**2
        pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    done.write_text(json.dumps({"images": len(names), "extract_s": t1 - t0, "match_s": t2 - t1,
                                "prior_drawn_scale": pri["scales"][0]}))
    return db_path


PRIOR_SCALE = 1.026  # A6: real ARKit, LaMAR no-depth sessions (non-LiDAR proxy), segment scale +1.6 .. +2.7 %
PRIOR_JITTER_M = 0.02
PRIOR_SIGMA_M = 0.03


def calibrated_prior_db(scene: str, db_path: Path, out: Path) -> tuple[Path, dict]:
    """Copy of the scene database with ARKit-like priors calibrated to A6's real measurements: true centres x
    PRIOR_SCALE about their mean plus 2 cm iid jitter (seeded by scene); sigma 3 cm, used without robust loss."""
    dst = out / "db_prior.db"
    src = pycolmap.Database.open(str(db_path))
    src.close()
    shutil.copyfile(db_path, dst)
    gt = gt_c2w(scene)
    C = {k: v[:3, 3] for k, v in gt.items()}
    mu = np.mean(list(C.values()), axis=0)
    rng = np.random.default_rng(sum(map(ord, scene)))
    db = pycolmap.Database.open(str(dst))
    db.clear_pose_priors()
    for img in db.read_all_images():
        c = C[Path(img.name).stem]
        pp = pycolmap.PosePrior()
        pp.corr_data_id = img.data_id
        pp.position = mu + PRIOR_SCALE * (c - mu) + rng.normal(0, PRIOR_JITTER_M, 3)
        pp.position_covariance = np.eye(3) * PRIOR_SIGMA_M**2
        pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    return dst, {"scale": PRIOR_SCALE, "jitter_m": PRIOR_JITTER_M, "sigma_m": PRIOR_SIGMA_M, "robust": False,
                 "source": "A6 real LaMAR ARKit, no-depth sessions"}


def colmap_run(scene: str, quality: str, set_name: str, method: str, out: Path) -> dict:
    db_path = database(scene, quality)
    wd = db_path.parent
    frames = json.loads((wd / "frames.json").read_text())["frames"]
    file_by_stem = {Path(n).stem: f["file"] for n, f in frames.items()}
    mem = members(scene, set_name)
    names = [file_by_stem[m] for m in mem]
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.ba_refine_focal_length = False
    o.ba_refine_principal_point = False
    o.ba_refine_extra_params = False
    o.random_seed = 0
    o.image_names = names
    rep = {"db": json.loads((wd / "database.done").read_text())}
    if method == "prior_old":
        o.use_prior_position = True
        o.use_robust_loss_on_prior_position = True
    elif method == "prior":
        o.use_prior_position = True
        o.use_robust_loss_on_prior_position = False
        db_path, rep["prior_draw"] = calibrated_prior_db(scene, db_path, out)
    tmp = out / "_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.time()
    maps = pycolmap.incremental_mapping(str(db_path), str(wd / "images"), str(tmp), options=o)
    if method == "prior":
        db_path.unlink()
    rep.update(mapper_s=round(time.time() - t0, 1), models=len(maps))
    if not maps:
        rep["registered"] = 0
        return rep
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    model = out / "model"
    shutil.rmtree(model, ignore_errors=True)
    model.mkdir(parents=True)
    best.write_text(str(model))
    shutil.rmtree(tmp, ignore_errors=True)
    rep.update(registered=best.num_reg_images(), model_sizes=sorted((m.num_reg_images() for m in maps.values()), reverse=True),
               points3D=best.num_points3D(), mean_reproj_px=best.compute_mean_reprojection_error())
    gt = gt_c2w(scene)
    C, G = [], []
    for im in best.images.values():
        if im.has_pose:
            C.append(im.projection_center())
            G.append(gt[Path(im.name).stem][:3, 3])
    C, G = np.array(C), np.array(G)
    s, R, t = umeyama(C, G)
    err = np.linalg.norm(s * C @ R.T + t - G, axis=1)
    rep["centre_rms_sim3_m"] = float(np.sqrt(np.mean(err**2)))
    rep["centres_within_0p3m"] = int(np.sum(err < 0.3))
    if method.startswith("prior"):
        s0, R0, t0_ = umeyama(C, G, False)
        rep["centre_rms_rigid_m"] = float(np.sqrt(np.mean(np.sum((C @ R0.T + t0_ - G) ** 2, 1))))
        rep["scale_error_pct"] = 100 * (1 / s - 1)
    pts = np.array([p.xyz for p in best.points3D.values()])
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts)).voxel_down_sample(VOXEL)
    P = np.asarray(pc.points)
    d, _ = cKDTree(C).query(P, workers=THREADS)
    write_ply(P[d < 20.0 * (1 if method.startswith("prior") else 1 / max(s, 1e-9))], out / "cloud.ply")
    rep["score_args"] = ["--cameras", str(model)] + (["--metric"] if method.startswith("prior") else [])
    return rep


# ---------------------------------------------------------------- feed-forward


def ff_adapter(method: str, net):
    import a2ff.run as runmod  # noqa: PLC0415  (torch stack only when a feed-forward method runs)

    if method == "pi3":
        a = runmod.Pi3()
    elif method == "da3":
        a = runmod.DA3("depth-anything/DA3-LARGE-1.1", "da3L", "Apache-2.0 (DA3-LARGE-1.1)")
    else:
        a = runmod.VGGT()
    a.net = net
    a.load()
    return a


_ADAPTERS: dict = {}


def ff_run(scene: str, quality: str, set_name: str, method: str, out: Path) -> dict:
    import cv2  # noqa: PLC0415
    from a2ff.common import write_view  # noqa: PLC0415
    from models.common import cover_crop_resample, to_network_image  # noqa: PLC0415

    three_two = quality == "dslr_ref"
    net = {"pi3": (518, 336) if three_two else (518, 392), "da3": (504, 336) if three_two else (504, 378),
           "vggt": (518, 336) if three_two else (518, 392)}[method]
    key = (method, net)
    if key not in _ADAPTERS:
        _ADAPTERS.clear()
        _ADAPTERS[key] = ff_adapter(method, net)
    adapter = _ADAPTERS[key]
    wd = degrade.make(scene, quality)
    frames = json.loads((wd / "frames.json").read_text())["frames"]
    by_stem = {Path(n).stem: f for n, f in frames.items()}
    mem = members(scene, set_name)
    rgbs, rs = [], []
    for m in mem:
        f = by_stem[m]
        img = cv2.imread(str(wd / "images" / f["file"]))[..., ::-1]
        W, H = 1024, int(round(f["h"] * 1024 / f["w"]))
        rgb = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        r = cover_crop_resample(W, H, net[0], net[1])
        rgbs.append(to_network_image(np.ascontiguousarray(rgb), r))
        rs.append(r)
    t0 = time.perf_counter()
    preds = adapter.infer(rgbs, None, None)
    rep = {"infer_s": round(time.perf_counter() - t0, 1), "net": net}
    run = out / "preds"
    for m, r, p in zip(mem, rs, preds):
        write_view(run, m, p["depth"], p["K"], p["c2w"], r, p.get("conf"), p.get("mask"))
    gt = gt_c2w(scene)
    src = np.array([np.load(run / f"{m}.npz")["cam_to_world"][:3, 3] for m in mem])
    dst = np.array([gt[m][:3, 3] for m in mem])
    sc, R, t = umeyama(src, dst)
    err = np.linalg.norm(sc * src @ R.T + t - dst, axis=1)
    rot = []
    for m in mem:
        Rp = R @ np.load(run / f"{m}.npz")["cam_to_world"][:3, :3]
        rot.append(np.degrees(np.arccos(np.clip((np.trace(gt[m][:3, :3].T @ Rp) - 1) / 2, -1, 1))))
    rot = np.array(rot)
    rep.update(registered=int(np.sum((err < 0.3) & (rot < 5))), centre_rms_sim3_m=float(np.sqrt(np.mean(err**2))),
               rot_err_median_deg=float(np.median(rot)), centres_within_0p3m=int(np.sum(err < 0.3)))
    rng = np.random.default_rng(0)
    per_view = CLOUD_BUDGET // len(mem)
    pts, cams = [], []
    for m in mem:
        z = np.load(run / f"{m}.npz")
        d = np.where(z["valid"], z["depth"], np.nan)
        fx, fy, cx, cy = map(float, z["intrinsics"])
        T = z["cam_to_world"].astype(np.float64).copy()
        T[:3, 3] = sc * R @ T[:3, 3] + t
        T[:3, :3] = R @ T[:3, :3]
        h, w = d.shape
        v, u = np.nonzero(np.isfinite(d) & (d > 0))
        sel = rng.choice(len(u), min(per_view, len(u)), replace=False)
        u, v = u[sel], v[sel]
        zz = d[v, u] * sc
        pc = np.c_[(u - cx) / fx * zz, (v - cy) / fy * zz, zz]
        pts.append(pc @ T[:3, :3].T + T[:3, 3])
        Rcw = T[:3, :3].T
        cams.append({"image": m, "R_cw": Rcw.tolist(), "t_cw": (-Rcw @ T[:3, 3]).tolist(),
                     "intrinsics": [fx, fy, cx, cy], "w": int(w), "h": int(h)})
    P = np.concatenate(pts)
    _, keep = np.unique(np.floor(P / VOXEL).astype(np.int64), axis=0, return_index=True)
    write_ply(P[np.sort(keep)], out / "cloud.ply")
    (out / "cameras.json").write_text(json.dumps(cams))
    # A2's rotation-aware rigid alignment onto the GT keyframes.
    gkf = {k["id"]: k for k in json.loads(GT[scene].read_text())["keyframes"]}
    flip = np.diag([1.0, -1.0, -1.0])
    Rs, Rg, cs, cg = [], [], [], []
    for c in cams:
        Rcw = np.array(c["R_cw"])
        Rs.append(Rcw.T @ flip)
        cs.append(-Rcw.T @ np.array(c["t_cw"]))
        G = np.asarray(gkf[c["image"]]["pose"], float).reshape(4, 4, order="F")
        Rg.append(G[:3, :3])
        cg.append(G[:3, 3])
    U, _, Vt = np.linalg.svd(sum(g @ s.T for g, s in zip(Rg, Rs)))
    Ra = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    cs, cg = np.array(cs), np.array(cg)
    Ta = np.eye(4)
    Ta[:3, :3], Ta[:3, 3] = Ra, cg.mean(0) - Ra @ cs.mean(0)
    (out / "transform.json").write_text(json.dumps(Ta.tolist()))
    rep["score_args"] = ["--cameras", str(out / "cameras.json"), "--metric", "--transform", str(out / "transform.json")]
    del preds
    return rep


# ---------------------------------------------------------------- scoring + criteria


def summarize(path: Path) -> dict:
    g = json.loads(path.read_text())
    kd = g["key_distances"]
    comp = g["completeness"]
    cov = {r: comp[r]["coverage"] for r in comp if isinstance(comp[r], dict) and "coverage" in comp[r]}
    return {"kd": kd["overall"], "kd_observed": kd.get("on_truly_observed_elements"), "status": kd["status_counts"],
            "false_observed_claims": len(kd.get("false_observed_claims", [])),
            "scale_error_pct": g["scale"].get("scale_error_pct"),
            "cloud_scale_error_pct": (g["scale"].get("alignment") or {}).get("cloud_scale_error_pct"),
            "anchor_m": g["anchor"].get("icp_translation_norm_m"),
            "completeness_10cm_icp": {r: comp[r]["after_icp"]["within_10cm"] for r in comp if isinstance(comp[r], dict) and "after_icp" in comp[r]},
            "coverage_by_rule": {r: {k: v.get(k) for k in ("truly_observed_by_rule", "claimed_by_rule", "false_observed_by_rule",
                                                           "claimed_observed", "truly_seen")} | {"sensitivity": v.get("sensitivity")}
                                 for r, v in cov.items()},
            "rows": {r["id"]: {"err_in": r.get("error_in", r.get("error")), "gt": r.get("gt"), "status": r["status"]} for r in kd["rows"]}}


def score(scene: str, out: Path, args: list[str], producer: str, tag: str = "score", cloud: Path | None = None) -> dict:
    cmd = [sys.executable, "-m", "evalharness.cli", "score-cloud", "--gt", str(GT[scene]), "--cloud", str(cloud or out / "cloud.ply"),
           *args, "--producer", producer, "--out", str(out / tag)]
    t0 = time.time()
    subprocess.run(cmd, check=True, cwd=HARNESS, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return summarize(out / tag / "geomscore.json") | {"score_s": round(time.time() - t0, 1)}


def criteria(out: Path, tag: str = "score") -> dict:
    scene = out / tag / "scene.json"
    res = {}
    for g in ("80", "75"):
        v = out / tag / f"c1.g{g}.json"
        p = subprocess.run([sys.executable, "-m", "evalharness.cli", "run-c1", "--scene", str(scene), "--out", str(v), "--ground-limit", g],
                           cwd=HARNESS, capture_output=True, text=True)
        if p.returncode or not v.exists():
            res[f"g{g}"] = {"error": (p.stderr or p.stdout)[-400:]}
            continue
        res[f"g{g}"] = decidability(json.loads(v.read_text()))
    return res


def decidability(v: dict) -> dict:
    crit = v.get("criteria", [])
    cnt: dict[str, int] = {}
    by_id: dict[str, dict[str, int]] = {}
    causes: dict[str, int] = {}
    for c in crit:
        o = c.get("outcome")
        cnt[o] = cnt.get(o, 0) + 1
        by_id.setdefault(c["id"], {}).setdefault(o, 0)
        by_id[c["id"]][o] += 1
        if o == "unsure":
            k = c.get("unsure_cause", "?")
            causes[k] = causes.get(k, 0) + 1
    n = sum(cnt.values()) or 1
    return {"decision": v.get("site", {}).get("decision"), "reasons": v.get("site", {}).get("reasons"),
            "n_criteria": sum(cnt.values()), "outcomes": cnt, "decidable_share": (cnt.get("pass", 0) + cnt.get("fail", 0)) / n,
            "unsure_causes": causes, "by_criterion": by_id, "views_needed": v.get("views_needed"),
            "candidates": len(v.get("candidates", []))}


def main() -> None:
    scene, quality, set_name, methods = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    for method in methods:
        res = HERE / "results" / scene / quality / set_name / f"{method}.json"
        if res.exists():
            continue
        out = RUNS / scene / quality / set_name / method
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            rep = colmap_run(scene, quality, set_name, method, out) if method in ("colmap", "prior", "prior_old") else ff_run(scene, quality, set_name, method, out)
        except Exception as e:  # noqa: BLE001
            rep = {"error": repr(e)[-600:]}
        rep.update(scene=scene, quality=quality, set=set_name, method=method, n=len(members(scene, set_name)))
        if rep.get("registered", 0) >= 3 and "score_args" in rep:
            try:
                rep["score"] = score(scene, out, rep["score_args"], f"A10-{method}-{quality}-{set_name}")
                rep["c1"] = criteria(out)
            except subprocess.CalledProcessError as e:
                rep["score_error"] = (e.stderr or b"").decode()[-800:]
        rep["total_s"] = round(time.time() - t0, 1)
        res.parent.mkdir(parents=True, exist_ok=True)
        res.write_text(json.dumps(rep, indent=1, default=float))
        s = rep.get("score", {}).get("kd", {})
        c = rep.get("c1", {}).get("g80", {})
        print(f"{time.strftime('%T')} {scene} {quality} {set_name} {method}: reg {rep.get('registered')}/{rep['n']} "
              f"kd n={s.get('n')} med={s.get('median_in')} p90={s.get('p90_in')} c1={c.get('decision')} "
              f"dec={c.get('decidable_share')} {rep.get('error', '')[:200]} {rep.get('score_error', '')[-200:]}", flush=True)


if __name__ == "__main__":
    main()
