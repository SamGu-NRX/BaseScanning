"""COLMAP model of the ETH3D facade -> scene v0, with metric scale from simulated ARKit poses.

The SfM model has no scale and an arbitrary frame. A phone would supply both through ARKit, so:

1. simulated ARKit poses: the dataset (ground-truth) camera poses in capture order, corrupted with
   one draw of a packet-generator drift profile (scale, yaw and position random walks, jitter; no
   tracking reset), then moved by a random yaw + translation so the "ARKit world" is not the
   ground-truth frame;
2. SfM -> ARKit world: rotation = chordal mean of R_arkit R_sfm^T over cameras, then scale and
   translation by least squares on camera centres with that rotation fixed;
3. the reference extractor (server.eval.extract) turns points + cameras + a meter tap + PR #13's
   opening clicks into a scene v0.

`--oracle` aligns to the exact ground-truth poses instead (no ARKit error), which isolates the
reconstruction's shape error from scale error. The meter tap is the ground-truth meter projected
into the three nearest frontal views (a perfect tap), so anchor error measures the pipeline only.

    python eth3d/scene_from_recon.py --gt ~/data/eval-harness/facade-gt/scene.json \
        --model ~/data/eval-harness/facade-colmap/sparse/best --out ~/data/eval-harness/facade-colmap-nonlidar \
        --profile nonlidar_modern --seed 0
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pycolmap

HERE = Path(__file__).resolve().parent
STORE = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(STORE / "internal/packet-generator"))

from packetgen.noise import apply_drift, load_profiles  # noqa: E402

from server.eval.coverage import CoverageQuery, bands_from_grid, coverage_rule, ground_sensitivity, set_wall_observed_fraction  # noqa: E402
from server.eval.extract import ExtractParams, coverage_from_tracks, extract_scene, profile_entries  # noqa: E402
from server.eval.frame import apply, mat4_from_list, pose_cv_to_arkit, project_arkit, transform_pose  # noqa: E402
from server.eval.sceneio import CoverageGrid, load_json, save_json, sha256_file, write_ply  # noqa: E402
from server.eval.visibility import Camera  # noqa: E402

ANN = HERE.parent / "vendor/hs13_facade/annotations.json"
SCALE_REL_PM = {"nonlidar_modern": 0.06, "nonlidar_2018": 0.2, "lidar_modern": 0.03, "oracle": 0.0}


def rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def project_so3(M: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(M)
    return U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt


def align_sfm(R_sfm, c_sfm, R_ref, c_ref) -> np.ndarray:
    """Similarity T with T(sfm) ~ ref: rotation from orientations, then scale + translation."""
    R = project_so3(sum(Ra @ Rs.T for Ra, Rs in zip(R_ref, R_sfm)))
    A = c_ref - c_ref.mean(0)
    S = (c_sfm - c_sfm.mean(0)) @ R.T
    s = float((A * S).sum() / (S * S).sum())
    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = c_ref.mean(0) - s * R @ c_sfm.mean(0)
    return T


def pick_meter_views(gt_cams: list[Camera], meter: np.ndarray, n: int = 3) -> list[tuple[int, float, float]]:
    """Views where the meter projects inside the image, facing the wall within 45 deg; nearest first."""
    cands = []
    for i, c in enumerate(gt_cams):
        u, v, z = project_arkit(c.pose, c.K, meter[None])
        if not (z[0] > 0.5 and 0.05 * c.w < u[0] < 0.95 * c.w and 0.05 * c.h < v[0] < 0.95 * c.h):
            continue
        to_cam = c.pose[:3, 3] - meter
        if to_cam[2] / np.linalg.norm(to_cam) < np.cos(np.radians(45)):
            continue
        cands.append((float(np.linalg.norm(to_cam)), i, float(u[0]), float(v[0])))
    return [(i, u, v) for _, i, u, v in sorted(cands)[:n]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", default="nonlidar_modern")
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--voxel", type=float, default=0.1)
    args = ap.parse_args()
    t0 = time.time()
    tag = "oracle" if args.oracle else args.profile
    rng = np.random.default_rng(args.seed)
    gt = load_json(args.gt)
    ann = json.loads(ANN.read_text())

    rec = pycolmap.Reconstruction(str(args.model))
    gt_kf = {k["id"]: k for k in gt["keyframes"]}
    ims = sorted((im for im in rec.images.values() if Path(im.name).stem in gt_kf), key=lambda im: im.name)
    names = [Path(im.name).stem for im in ims]
    gt_cams = [Camera(n, mat4_from_list(gt_kf[n]["pose"]), np.array(gt_kf[n]["intrinsics"]), gt_kf[n]["w"], gt_kf[n]["h"])
               for n in names]

    sfm_pose = []
    rec_cams = []
    for im in ims:
        cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
        P = pose_cv_to_arkit(cfw.rotation.matrix(), np.asarray(cfw.translation))
        sfm_pose.append(P)
        cam = rec.cameras[im.camera_id]
        fx, fy, cx, cy = cam.params[:4]
        rec_cams.append((np.array([fx, fy, cx, cy]), int(cam.width), int(cam.height)))
    R_sfm = np.array([P[:3, :3] for P in sfm_pose])
    c_sfm = np.array([P[:3, 3] for P in sfm_pose])
    R_gt = np.array([c.pose[:3, :3] for c in gt_cams])
    c_gt = np.array([c.pose[:3, 3] for c in gt_cams])

    params: dict = {"profile": tag, "seed": args.seed, "images_in_model": len(ims)}
    if args.oracle:
        R_ref, c_ref, T_w_gt = R_gt, c_gt, np.eye(4)
        source_frame = "colmap_model (aligned to ground-truth poses)"
        scale_source, gravity_source = "ground_truth", "ground_truth"
    else:
        prof = load_profiles()[args.profile]
        R_obs, c_obs, _, draw = apply_drift(R_gt, c_gt, prof, rng, reset_index=None)
        yaw = float(rng.uniform(-np.pi, np.pi))
        shift = rng.uniform(-20, 20, 3) * np.array([1, 0.1, 1])
        T_w_gt = np.eye(4)
        T_w_gt[:3, :3] = rot_y(yaw)
        T_w_gt[:3, 3] = shift
        R_ref = np.array([T_w_gt[:3, :3] @ R for R in R_obs])
        c_ref = apply(T_w_gt, c_obs)
        params["drift_draw"] = {"scale": draw.scales[0], "session_multiplier": round(draw.session_multiplier, 4),
                                "final_yaw_error_deg": round(float(draw.yaw_error_deg[-1]), 3),
                                "max_abs_yaw_error_deg": round(float(np.abs(draw.yaw_error_deg).max()), 3),
                                "max_drift_m": round(float(np.linalg.norm(draw.drift_m, axis=1).max()), 4),
                                "arkit_world_yaw_deg": round(float(np.degrees(yaw)), 3),
                                "arkit_world_shift_m": [round(float(v), 3) for v in shift]}
        source_frame = "colmap_model"
        scale_source, gravity_source = "arkit_poses", "arkit"

    T_ws = align_sfm(R_sfm, c_sfm, R_ref, c_ref)
    resid = np.linalg.norm(apply(T_ws, c_sfm) - c_ref, axis=1)
    params["align_rms_m"] = round(float(np.sqrt(np.mean(resid**2))), 4)
    params["align_scale"] = round(float(np.cbrt(np.linalg.det(T_ws[:3, :3]))), 6)

    pids = sorted(rec.points3D)
    X_sfm = np.array([rec.points3D[p].xyz for p in pids])
    id_to_idx = {im.image_id: k for k, im in enumerate(ims)}
    tracks = [np.unique([id_to_idx[e.image_id] for e in rec.points3D[p].track.elements if e.image_id in id_to_idx])
              for p in pids]
    points_w = apply(T_ws, X_sfm)
    cams_w = [Camera(n, transform_pose(T_ws, P), K, w, h) for n, P, (K, w, h) in zip(names, sfm_pose, rec_cams)]

    meter_gt = np.asarray(next(o for o in gt["objects"] if o["id"] == gt["frame"]["anchor"]["element_id"])["geometry"]["pos_m"])
    taps = []
    for i, u, v in pick_meter_views(gt_cams, meter_gt):
        r = rec_cams[i][1] / gt_cams[i].w
        taps.append((i, u * r, v * r))
    params["meter_tap_views"] = [names[i] for i, _, _ in taps]

    idx_by_name = {n: i for i, n in enumerate(names)}
    kscale = ann["keyframes"]["scale"]
    features = []
    for f in ann["features"]:
        pts = {}
        for img, roles in f["points_px"].items():
            if img not in idx_by_name:
                continue
            i = idx_by_name[img]
            r = rec_cams[i][1] / (gt_cams[i].w * kscale)
            pts[i] = {role: (roles[role][0] * r, roles[role][1] * r) for role in roles}
        oid = f["label"].replace("w1 window ", "win").replace("w3 entrance door", "door")
        features.append({"id": oid, "class": f["type"], "label": f["label"], "points_px": pts})

    provenance = {
        "data": "hybrid", "site": gt["provenance"]["site"],
        "producer": {"path": "A1-colmap" + ("-oracle" if args.oracle else ""), "version": "0",
                     "code_ref": "internal/eval-harness/eth3d/recon_colmap.py + scene_from_recon.py", "params": params},
        "inputs": {"images_given": 76, "images_used": len(ims),
                   "sensors_used": ["images", "intrinsics", "arkit_poses", "gravity", "taps", "annotations"]},
        "scale_source": scale_source, "gravity_source": gravity_source,
        "license": "ETH3D data: research use only (CC BY-NC-SA); COLMAP BSD",
        "notes": ["Simulated ARKit: dataset poses + packetgen drift draw (see producer.params)." if not args.oracle
                  else "Oracle: SfM aligned to exact dataset poses; scale and gravity are ground truth.",
                  "Meter tap = ground-truth meter projected into the listed views (perfect tap).",
                  "Openings = PR #13 hand clicks, rescaled to the 1600 px SfM images, cast through SfM cameras."],
    }
    p = ExtractParams(scale_rel_pm=SCALE_REL_PM.get(tag, 0.06), voxel_m=args.voxel)
    scene, st = extract_scene(points_w, tracks, cams_w, taps, features, p, provenance,
                              f"{gt['provenance']['site']}/colmap-{tag}", source_frame, T_ws)
    for o in scene["objects"]:
        src = next((f for f in ann["features"] if f["label"] == o.get("label")), None)
        if src and "attrs" in src:
            o["attrs"] = src["attrs"]
    pts_c = st["points_c"]
    pm_prof = round(0.05 + p.scale_rel_pm * 5.0, 3)
    scene["overheads"], scene["facing"] = profile_entries(pts_c, scene, pm_m=pm_prof, min_count=p.profile_min_count)

    xs = [q[0] for w in scene["walls"] for q in w["baseline_m"]]
    zs = [q[1] for w in scene["walls"] for q in w["baseline_m"]]
    lo = np.array([min(xs) - 4.0, -0.5, min(zs) - 2.0])
    hi = np.array([max(xs) + 4.0, 6.0, max(zs) + 16.0])
    t1 = time.time()
    grid: CoverageGrid = coverage_from_tracks(pts_c, tracks, st["cams_c"], lo, hi, args.voxel)
    t_cov = round(time.time() - t1, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    grid.save(args.out / "coverage.npz")
    write_ply(args.out / "points.ply", pts_c.astype(np.float32), {"n_views": np.array([len(t) for t in tracks], np.int32)})
    scene["coverage"] = {"method": "rays_to_triangulated_points: camera centre to every SfM point it observed",
                         "grid": grid.meta("coverage")}
    query = CoverageQuery(st["cams_c"], pts_c)
    scene["coverage"]["rule"] = coverage_rule()
    scene["coverage"]["bands"] = bands_from_grid(grid, scene, query=query)
    scene["coverage"]["sensitivity"] = ground_sensitivity(grid, scene, query)
    set_wall_observed_fraction(scene, query)
    scene["assets"] = [
        {"id": "points", "kind": "point_cloud", "path": "points.ply", "format": "ply", "in_scene_frame": True,
         "count": int(len(pts_c)), "fields": ["x", "y", "z", "n_views"], "sha256": sha256_file(args.out / "points.ply"),
         "note": "COLMAP sparse points (pycolmap 4.2, 1600 px)"},
        {"id": "coverage", "kind": "coverage_grid", "path": "coverage.npz", "format": "npz",
         "sha256": sha256_file(args.out / "coverage.npz")},
    ]
    scene["provenance"]["runtime_s"] = round(time.time() - t0, 1)
    scene["provenance"]["hardware"] = "4-core CPU VM, no GPU"
    sha = save_json(scene, args.out / "scene.json")
    report = {"scene_sha256": sha, "t_coverage_s": t_cov, "params": params, **st["report"],
              "walls": [(w["id"], w["s_range_m"], w.get("observed_fraction")) for w in scene["walls"]],
              "objects": [(o["id"], o["geometry"]) for o in scene["objects"]]}
    (args.out / "build_report.json").write_text(json.dumps(report, indent=1, default=float))
    print(json.dumps(report, indent=1, default=float))


if __name__ == "__main__":
    main()
