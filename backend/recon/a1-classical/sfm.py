"""Sparse reconstruction modes for one scene (pycolmap 4.2, CPU).

    python sfm.py <scene> features            # SIFT + exhaustive matching, shared by every mode
    python sfm.py <scene> incremental         # mode 1: no poses, COLMAP incremental mapper
    python sfm.py <scene> global              # mode 1: no poses, GLOMAP global mapper (in COLMAP 4)
    python sfm.py <scene> gt                  # mode 3: ground-truth poses, triangulate only
    python sfm.py <scene> arkit <profile> <seed>   # mode 2: ARKit-like poses (prior BA + fixed)

Every packet gives intrinsics, so all modes start from the known pinhole intrinsics and keep them
fixed. Outputs go to WORK/<scene>/sparse/<run>/ as COLMAP text, plus <run>.json with timings.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pycolmap

from common import WORK, dump_json, read_text_model, write_text_model
from packetgen.align import align
from packetgen.noise import apply_drift, load_profiles
from packetgen.sources import from_colmap_model

THREADS = 4


def scene_paths(scene: str) -> dict[str, Path]:
    d = WORK / scene
    return {"dir": d, "images": d / "images", "db": d / "database.db", "gt": d / "gt_model", "sparse": d / "sparse"}


def features(scene: str, max_features: int = 8192) -> dict:
    p = scene_paths(scene)
    cams, images = read_text_model(p["gt"])
    if p["db"].exists():
        p["db"].unlink()
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = 2048
    ext.num_threads = THREADS
    ext.sift.max_num_features = max_features
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    names = sorted(images)
    pycolmap.extract_features(str(p["db"]), str(p["images"]), image_names=names,
                              camera_mode=pycolmap.CameraMode.PER_IMAGE, reader_options=reader,
                              extraction_options=ext, device=pycolmap.Device.cpu)
    t1 = time.time()
    db = pycolmap.Database.open(str(p["db"]))
    for img in db.read_all_images():
        _, w, h, params = cams[images[img.name]["camera_id"]]
        cam = db.read_camera(img.camera_id)
        assert cam.width == w and cam.height == h, (img.name, cam.width, cam.height, w, h)
        cam.params = params
        cam.has_prior_focal_length = True
        db.update_camera(cam)
    db.close()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(p["db"]), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    rep = {"extract_s": t1 - t0, "match_s": t2 - t1, "images": len(names), "max_features": max_features}
    dump_json(p["dir"] / "features.json", rep)
    return rep


def _fixed_intrinsics_incremental() -> pycolmap.IncrementalPipelineOptions:
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.ba_refine_focal_length = False
    o.ba_refine_principal_point = False
    o.ba_refine_extra_params = False
    o.random_seed = 0
    return o


def _save_best(maps: dict, out: Path) -> pycolmap.Reconstruction:
    if not maps:
        raise RuntimeError("no model")
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    best.write_text(str(out))
    return best


def incremental(scene: str) -> dict:
    p = scene_paths(scene)
    tmp = p["sparse"] / "_inc_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.time()
    maps = pycolmap.incremental_mapping(str(p["db"]), str(p["images"]), str(tmp), options=_fixed_intrinsics_incremental())
    dt = time.time() - t0
    rec = _save_best(maps, p["sparse"] / "incremental")
    shutil.rmtree(tmp, ignore_errors=True)
    rep = {"run": "incremental", "mapper_s": dt, "models": len(maps), "registered": rec.num_reg_images(),
           "points3D": rec.num_points3D(), "mean_reproj_px": rec.compute_mean_reprojection_error()}
    dump_json(p["sparse"] / "incremental.json", rep)
    return rep


def global_(scene: str) -> dict:
    p = scene_paths(scene)
    tmp = p["sparse"] / "_glo_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    o = pycolmap.GlobalPipelineOptions()
    o.num_threads = THREADS
    o.random_seed = 0
    o.mapper.bundle_adjustment.refine_focal_length = False
    o.mapper.bundle_adjustment.refine_principal_point = False
    o.mapper.bundle_adjustment.refine_extra_params = False
    o.mapper.global_positioning.use_gpu = False
    o.mapper.bundle_adjustment.ceres.use_gpu = False
    t0 = time.time()
    maps = pycolmap.global_mapping(str(p["db"]), str(p["images"]), str(tmp), options=o)
    dt = time.time() - t0
    rec = _save_best(maps, p["sparse"] / "global")
    shutil.rmtree(tmp, ignore_errors=True)
    rep = {"run": "global", "mapper_s": dt, "models": len(maps), "registered": rec.num_reg_images(),
           "points3D": rec.num_points3D(), "mean_reproj_px": rec.compute_mean_reprojection_error()}
    dump_json(p["sparse"] / "global.json", rep)
    return rep


def _model_with_poses(scene: str, poses: dict[str, tuple[np.ndarray, np.ndarray]], out: Path) -> pycolmap.Reconstruction:
    """Text model whose image/camera ids match the database, with the given cam_from_world poses."""
    p = scene_paths(scene)
    gt_cams, gt_images = read_text_model(p["gt"])
    db = pycolmap.Database.open(str(p["db"]))
    cams, images = {}, {}
    for img in db.read_all_images():
        if img.name not in poses:
            continue
        c = db.read_camera(img.camera_id)
        cams[img.camera_id] = ("PINHOLE", c.width, c.height, np.asarray(c.params))
        R_cw, t_cw = poses[img.name]
        images[img.name] = {"image_id": img.image_id, "camera_id": img.camera_id, "R_cw": R_cw, "t_cw": t_cw}
    db.close()
    write_text_model(out, cams, images)
    return pycolmap.Reconstruction(str(out))


def triangulate_fixed(scene: str, poses: dict, run: str) -> dict:
    p = scene_paths(scene)
    seed_dir = p["sparse"] / f"_{run}_poses"
    rec = _model_with_poses(scene, poses, seed_dir)
    out = p["sparse"] / run
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    t0 = time.time()
    rec = pycolmap.triangulate_points(rec, str(p["db"]), str(p["images"]), str(out), clear_points=True,
                                      options=_fixed_intrinsics_incremental(), refine_intrinsics=False)
    dt = time.time() - t0
    rec.write_text(str(out))
    for f in out.glob("*.bin"):
        f.unlink()
    shutil.rmtree(seed_dir, ignore_errors=True)
    rep = {"run": run, "triangulate_s": dt, "registered": rec.num_reg_images(), "points3D": rec.num_points3D(),
           "mean_reproj_px": rec.compute_mean_reprojection_error()}
    dump_json(p["sparse"] / f"{run}.json", rep)
    return rep


def gt(scene: str) -> dict:
    _, gt_images = read_text_model(scene_paths(scene)["gt"])
    poses = {n: (im["R_cw"], im["t_cw"]) for n, im in gt_images.items()}
    return triangulate_fixed(scene, poses, "gt")


_ALIGN_CACHE: dict = {}


def nn_tour(c: np.ndarray) -> np.ndarray:
    """Greedy nearest-neighbour walk over camera stations (plan distance), from frame 0."""
    left = list(range(1, len(c)))
    order = [0]
    while left:
        d = np.linalg.norm(c[left][:, [0, 2]] - c[order[-1]][[0, 2]], axis=1)
        order.append(left.pop(int(np.argmin(d))))
    return np.array(order)


def arkit_poses(scene: str, profile: str, seed: int, reset_index: int | None = -1, order: str = "name") -> dict:
    """ARKit-like observed poses from the packet generator's drift model, applied to the GT poses
    in ARKit's gravity-aligned world. Returns cam_from_world (OpenCV) in that world, plus the
    exact GT->ARKit-world transform for scoring. reset_index=None forbids a tracking reset
    (a reset starts a new ARKit world; a server would handle each epoch separately)."""
    p = scene_paths(scene)
    if scene not in _ALIGN_CACHE:
        # The GT-pose triangulation carries the GT poses plus points, which align() needs for gravity.
        rec = from_colmap_model(p["sparse"] / "gt", p["images"], metric=True, source="eth3d-gt")
        _ALIGN_CACHE[scene] = (rec, align(rec))
    rec, al = _ALIGN_CACHE[scene]
    T_ark = np.array([al.pose(v.T_cv) for v in rec.views])
    R = T_ark[:, :3, :3]
    c = T_ark[:, :3, 3]
    prof = load_profiles()[profile]
    rng = np.random.default_rng(seed)
    # "name" = DSC filename order (the photographer's stations, long back-and-forth path);
    # "tour" = greedy nearest-neighbour walk, closer to one continuous phone walkaround.
    perm = nn_tour(c) if order == "tour" else np.arange(len(c))
    inv = np.argsort(perm)
    R_obs, c_obs, epoch, draw = apply_drift(R[perm], c[perm], prof, rng, reset_index=reset_index)
    R_obs, c_obs, epoch = R_obs[inv], c_obs[inv], epoch[inv]
    c_walk = c[perm]
    poses = {}
    for v, Ro, co in zip(rec.views, R_obs, c_obs):
        R_c2w_cv = Ro @ np.diag([1.0, -1.0, -1.0])
        R_cw = R_c2w_cv.T
        poses[v.name] = (R_cw, -R_cw @ co)
    info = {
        "profile": profile, "seed": seed, "scales": draw.scales, "session_multiplier": draw.session_multiplier,
        "reset_index": draw.reset_index, "epochs": np.bincount(epoch).tolist(),
        "order": order, "path_length_m": float(np.sum(np.linalg.norm(np.diff(c_walk[:, [0, 2]], axis=0), axis=1))),
        "yaw_error_deg_final": float(draw.yaw_error_deg[-1]),
        "gt_to_ark": {"scale": al.scale, "origin_src": al.origin_src, "R_align": al.R_align},
        "names": [v.name for v in rec.views], "c_obs": c_obs, "c_true_ark": c, "epoch": epoch,
    }
    return {"poses": poses, "info": info}


def arkit(scene: str, profile: str, seed: int, prior_sigma_m: float = 0.3) -> dict:
    p = scene_paths(scene)
    ak = arkit_poses(scene, profile, seed, reset_index=None)
    tag = f"arkit_{profile}_s{seed}"
    dump_json(p["sparse"] / f"{tag}_poses.json", ak["info"])
    reps = {}
    # (b) Fixed ARKit poses, triangulate only: what "trust the phone's poses" gives.
    reps["fixed"] = triangulate_fixed(scene, ak["poses"], f"{tag}_fixed")
    # (a) Pose-prior mapping: SfM from images with ARKit positions as soft priors (metric frame).
    db = pycolmap.Database.open(str(p["db"]))
    db.clear_pose_priors()
    names = ak["info"]["names"]
    c_obs = np.asarray(ak["info"]["c_obs"])
    by_name = {n: c for n, c in zip(names, c_obs)}
    for img in db.read_all_images():
        pp = pycolmap.PosePrior()
        pp.corr_data_id = img.data_id
        pp.position = by_name[img.name]
        pp.position_covariance = np.eye(3) * prior_sigma_m ** 2
        pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    tmp = p["sparse"] / f"_{tag}_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    o = _fixed_intrinsics_incremental()
    o.use_prior_position = True
    o.use_robust_loss_on_prior_position = True
    t0 = time.time()
    maps = pycolmap.incremental_mapping(str(p["db"]), str(p["images"]), str(tmp), options=o)
    dt = time.time() - t0
    rec = _save_best(maps, p["sparse"] / f"{tag}_prior")
    shutil.rmtree(tmp, ignore_errors=True)
    db = pycolmap.Database.open(str(p["db"]))
    db.clear_pose_priors()
    db.close()
    reps["prior"] = {"run": f"{tag}_prior", "mapper_s": dt, "models": len(maps), "registered": rec.num_reg_images(),
                     "points3D": rec.num_points3D(), "mean_reproj_px": rec.compute_mean_reprojection_error(),
                     "prior_sigma_m": prior_sigma_m}
    dump_json(p["sparse"] / f"{tag}_prior.json", reps["prior"])
    return reps


if __name__ == "__main__":
    scene, cmd = sys.argv[1], sys.argv[2]
    if cmd == "features":
        print(features(scene))
    elif cmd == "incremental":
        print(incremental(scene))
    elif cmd == "global":
        print(global_(scene))
    elif cmd == "gt":
        print(gt(scene))
    elif cmd == "arkit":
        print(arkit(scene, sys.argv[3], int(sys.argv[4])))
    else:
        raise SystemExit(f"unknown command {cmd}")
