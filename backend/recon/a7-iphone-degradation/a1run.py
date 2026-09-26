"""A1 leading path on degraded frames: COLMAP incremental SfM with simulated non-LiDAR ARKit position
priors (A1's exact recipe and its stored seed-0 draw), then the shared scorer.

    python a1run.py <scene> <condition>

Same settings as internal/breadth/a1-classical/sfm.py `arkit nonlidar_modern 0`: SIFT 8192, exhaustive
matching, PINHOLE intrinsics fixed to the frame's known K, prior sigma 0.3 m with a robust loss. The
prior positions are A1's stored `c_obs` (so every condition sees the same simulated ARKit trajectory).
Outputs: ~/a7/work/<scene>/<cond>/{database.db, sparse/prior/}, ~/a7/scores/<scene>/A1/<cond>/.
Summary row: <store>/internal/breadth/a7-iphone-degradation/results/<scene>/A1/<cond>.json
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import open3d as o3d
import pycolmap
from scipy.spatial import cKDTree

from degrade import ETH3D, WORK, make, read_cameras

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
A1 = STORE / "internal/breadth/a1-classical/results"
RESULTS = STORE / "internal/breadth/a7-iphone-degradation/results"
HARNESS = Path.home() / "a7/harness"
SCORES = Path.home() / "a7/scores"
THREADS = int(os.environ.get("A7_THREADS", 4))


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


def gt_centres(scene: str) -> dict[str, np.ndarray]:
    _, imgs = read_cameras(scene)
    out = {}
    for n, im in imgs.items():
        R = qvec_to_R(im["q"])
        out[Path(n).stem] = -R.T @ np.array(im["t"])
    return out


def priors(scene: str) -> dict[str, np.ndarray]:
    d = json.loads((A1 / scene / "sparse/arkit_nonlidar_modern_s0_poses.json").read_text())
    return {Path(n).stem: np.array(c) for n, c in zip(d["names"], d["c_obs"])}, d["scales"][0]


def sfm(scene: str, cond: str) -> dict:
    wd = make(scene, cond)
    frames = json.loads((wd / "frames.json").read_text())["frames"]
    db_path = wd / "database.db"
    if db_path.exists():
        db_path.unlink()
    names = sorted(f["file"] for f in frames.values())
    K_by_file = {f["file"]: (f["K"], f["w"], f["h"]) for f in frames.values()}
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
    t1 = time.time()
    pri, drawn_scale = priors(scene)
    db = pycolmap.Database.open(str(db_path))
    n_kp = []
    for img in db.read_all_images():
        K, w, h = K_by_file[img.name]
        cam = db.read_camera(img.camera_id)
        assert cam.width == w and cam.height == h, (img.name, cam.width, cam.height, w, h)
        cam.params = np.array(K, float)
        cam.has_prior_focal_length = True
        db.update_camera(cam)
        n_kp.append(db.read_keypoints(img.image_id).shape[0])
    db.close()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(db_path), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    db = pycolmap.Database.open(str(db_path))
    db.clear_pose_priors()
    for img in db.read_all_images():
        pp = pycolmap.PosePrior()
        pp.corr_data_id = img.data_id
        pp.position = pri[Path(img.name).stem]
        pp.position_covariance = np.eye(3) * 0.3**2
        pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    with sqlite3.connect(str(db_path)) as con:
        n_inlier_pairs = con.execute("SELECT COUNT(*) FROM two_view_geometries WHERE rows >= 15").fetchone()[0]
        inliers_median = con.execute("SELECT rows FROM two_view_geometries WHERE rows >= 15").fetchall()
    inliers_median = float(np.median([r[0] for r in inliers_median])) if inliers_median else 0.0
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.ba_refine_focal_length = False
    o.ba_refine_principal_point = False
    o.ba_refine_extra_params = False
    o.random_seed = 0
    o.use_prior_position = True
    o.use_robust_loss_on_prior_position = True
    tmp = wd / "sparse/_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    maps = pycolmap.incremental_mapping(str(db_path), str(wd / "images"), str(tmp), options=o)
    t3 = time.time()
    rep = {"scene": scene, "condition": cond, "images": len(names), "models": len(maps),
           "keypoints_median": float(np.median(n_kp)), "extract_s": t1 - t0, "match_s": t2 - t1, "mapper_s": t3 - t2,
           "prior_drawn_scale": drawn_scale, "inlier_pairs": n_inlier_pairs, "inliers_per_pair_median": inliers_median}
    if not maps:
        rep.update(registered=0)
        return rep
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    out = wd / "sparse/prior"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    best.write_text(str(out))
    shutil.rmtree(tmp, ignore_errors=True)
    rep.update(registered=best.num_reg_images(), model_sizes=sorted((m.num_reg_images() for m in maps.values()), reverse=True),
               points3D=best.num_points3D(), mean_reproj_px=best.compute_mean_reprojection_error(),
               mean_track_length=best.compute_mean_track_length())
    # Camera-centre accuracy vs the ETH3D ground truth (model is in the ARKit-like metric frame).
    gtc = gt_centres(scene)
    C, G = [], []
    for im in best.images.values():
        if not im.has_pose:
            continue
        C.append(im.projection_center())
        G.append(gtc[Path(im.name).stem])
    C, G = np.array(C), np.array(G)
    s, R, t = umeyama(C, G, True)
    s0, R0, t0_ = umeyama(C, G, False)
    rep["centre_rms_rigid_m"] = float(np.sqrt(np.mean(np.sum((C @ R0.T + t0_ - G) ** 2, 1))))
    rep["centre_rms_sim3_m"] = float(np.sqrt(np.mean(np.sum((s * C @ R.T + t - G) ** 2, 1))))
    rep["scale_error_pct"] = 100 * (1 / s - 1)  # model length / true length - 1
    return rep


def score(scene: str, cond: str) -> dict:
    wd = WORK / scene / cond
    model = wd / "sparse/prior"
    out = SCORES / scene / "A1" / cond
    out.mkdir(parents=True, exist_ok=True)
    pts = [ln.split()[1:4] for ln in (model / "points3D.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.array(pts, float))).voxel_down_sample(0.02)
    rec = pycolmap.Reconstruction(str(model))
    C = np.array([im.projection_center() for im in rec.images.values() if im.has_pose])
    dist, _ = cKDTree(C).query(np.asarray(pc.points), workers=4)
    pc = pc.select_by_index(np.flatnonzero(dist < 20.0))
    ply = out / "cloud.ply"
    o3d.io.write_point_cloud(str(ply), pc, write_ascii=False)
    gt = Path.home() / f"a7/gt/{scene}-gt/scene.json"
    cmd = [sys.executable, "-m", "evalharness.cli", "score-cloud", "--gt", str(gt), "--cloud", str(ply), "--cameras", str(model),
           "--metric", "--producer", f"A7-A1-{cond}", "--out", str(out / "score")]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=HARNESS, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError(f"scorer failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
    return summarize_score(out / "score" / "geomscore.json") | {"score_s": time.time() - t0}


def summarize_score(path: Path) -> dict:
    g = json.loads(path.read_text())
    kd = g["key_distances"]
    comp = g["completeness"]
    return {"kd": kd["overall"], "kd_observed": kd["on_truly_observed_elements"], "status": kd["status_counts"],
            "false_observed_claims": len(kd["false_observed_claims"]),
            "scale_error_pct": g["scale"].get("scale_error_pct"),
            "cloud_scale_error_pct": (g["scale"].get("alignment") or {}).get("cloud_scale_error_pct"),
            "anchor_m": g["anchor"].get("icp_translation_norm_m"),
            "wall_face_10cm_icp": comp["wall_face"]["after_icp"]["within_10cm"],
            "meter_wall_10cm_icp": comp["meter_wall"]["after_icp"]["within_10cm"],
            "rows": {r["id"]: r.get("error") for r in kd["rows"] if r["status"] == "scored"}}


def main(scene: str, cond: str) -> None:
    res = RESULTS / scene / "A1" / f"{cond}.json"
    res.parent.mkdir(parents=True, exist_ok=True)
    rep = sfm(scene, cond)
    if rep.get("registered", 0) >= 3:
        try:
            rep["score"] = score(scene, cond)
        except Exception as e:  # noqa: BLE001
            rep["score_error"] = repr(e)
    res.write_text(json.dumps(rep, indent=1, default=float))
    print(json.dumps({k: v for k, v in rep.items() if k != "score"}, default=float), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
