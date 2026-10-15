"""Shared tail of the ETH3D ground-truth builders: laser + occluders + cameras -> a finished scene v0.

Given a scene v0 with frame, walls, chain, ground and objects already set (meter-anchored frame),
this adds the laser and occluder assets, every DSLR camera as a keyframe, the measured coverage
(rays through each camera's laser z-buffer, grazing hits do not mark the surface), bands and
ground sensitivity under the section 3.1 rule, walls' observed_fraction, and overheads / facing from
the laser with PR #13's profile rule. Then it validates and saves.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

from server.eval.coverage import CoverageQuery, bands_from_grid, build_grid, coverage_rule, ground_sensitivity, set_wall_observed_fraction
from server.eval.extract import profile_entries
from server.eval.frame import mat4_to_list, pose_cv_to_arkit, transform_pose
from server.eval.sceneio import read_ply, save_json, sha256_file, write_ply
from server.eval.visibility import Camera, VisibilityOracle


def voxel_downsample(P: np.ndarray, voxel: float) -> np.ndarray:
    keys = np.floor(P / voxel).astype(np.int64)
    _, inv, cnt = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    out = np.zeros((len(cnt), 3))
    np.add.at(out, inv, P)
    return out / cnt[:, None]


def read_alignment(path: Path) -> dict[str, np.ndarray]:
    """ETH3D scan_alignment.mlp: scan file -> 4x4 scan-to-world."""
    out = {}
    for m in re.finditer(r'filename="([^"]+)".*?<MLMatrix44>(.*?)</MLMatrix44>', Path(path).read_text(), re.S):
        out[m.group(1)] = np.array(m.group(2).split(), dtype=float).reshape(4, 4)
    return out


def load_scans(scan_dir: Path, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """All aligned laser scans of an ETH3D dslr_scan_eval folder, cropped to the box [lo, hi]."""
    parts = []
    for name, T in read_alignment(Path(scan_dir) / "scan_alignment.mlp").items():
        d = read_ply(Path(scan_dir) / name)
        X = np.c_[d["x"], d["y"], d["z"]].astype(float) @ T[:3, :3].T + T[:3, 3]
        parts.append(X[np.all((X >= lo) & (X <= hi), axis=1)])
    return np.vstack(parts)


def _qvec_to_rot(q) -> np.ndarray:
    w, x, y, z = q
    return np.array([[1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
                     [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
                     [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y]])


def read_cameras(calib_dir: Path) -> list[tuple[str, np.ndarray, np.ndarray, int, int]]:
    """COLMAP text model of ETH3D's undistorted DSLR images: (stem, R_cw, t_cw, K, w, h), sorted."""
    cams = {}
    for line in (Path(calib_dir) / "cameras.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            p = line.split()
            assert p[1] == "PINHOLE", p[1]
            cams[p[0]] = (int(p[2]), int(p[3]), np.array(p[4:8], float))
    lines = [ln for ln in (Path(calib_dir) / "images.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    out = []
    for line in lines[::2]:
        p = line.split()
        w, h, K = cams[p[8]]
        out.append((Path(p[9]).stem, _qvec_to_rot(np.array(p[1:5], float)), np.array(p[5:8], float), K, w, h))
    return sorted(out, key=lambda c: c[0])


def cameras_in_scene(T_c_src: np.ndarray, cams_cv) -> list[Camera]:
    return [Camera(n, transform_pose(T_c_src, pose_cv_to_arkit(R, t)), K, w, h) for n, R, t, K, w, h in cams_cv]


def finish_gt(scene: dict, Pc: np.ndarray, Oc: np.ndarray, cams: list[Camera], out: Path, voxel: float = 0.1,
              report: dict | None = None, t0: float | None = None, pm_profiles_m: float = 0.03) -> dict:
    """Pc / Oc: laser and occluder points already in the scene frame. Returns the build report."""
    t0 = t0 or time.time()
    report = dict(report or {})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    xs = [q[0] for w in scene["walls"] for q in w["baseline_m"]]
    zs = [q[1] for w in scene["walls"] for q in w["baseline_m"]]
    lo = np.array([min(xs) - 4.0, -0.5, min(-2.0, min(zs) - 2.0)])
    hi = np.array([max(xs) + 4.0, 6.0, max(zs) + 16.0])
    write_ply(out / "laser.ply", Pc.astype(np.float32))
    write_ply(out / "occluders.ply", Oc.astype(np.float32))
    report["laser_points"], report["occluder_points"] = int(len(Pc)), int(len(Oc))
    scene["keyframes"] = [{"id": c.id, "image": f"{c.id}.JPG", "pose": mat4_to_list(c.pose), "intrinsics": [float(v) for v in c.K],
                           "w": c.w, "h": c.h, "pose_source": "ground_truth"} for c in cams]
    t1 = time.time()
    blockers = np.vstack([Pc, Oc]) if len(Oc) else Pc
    oracle = VisibilityOracle(cams, blockers)
    grid = build_grid(oracle.rays(stride=4), lo, hi, voxel)
    grid.save(out / "coverage.npz")
    report["t_coverage_s"] = round(time.time() - t1, 1)
    scene["coverage"]["grid"] = grid.meta("coverage")
    query = CoverageQuery(cams, blockers)
    scene["coverage"]["rule"] = coverage_rule()
    scene["coverage"]["bands"] = bands_from_grid(grid, scene, query=query)
    scene["coverage"]["sensitivity"] = ground_sensitivity(grid, scene, query)
    set_wall_observed_fraction(scene, query)
    scene["overheads"], scene["facing"] = profile_entries(Pc, scene, pm_m=pm_profiles_m, min_count=5)
    scene["assets"] = [
        {"id": "laser", "kind": "point_cloud", "path": "laser.ply", "format": "ply", "in_scene_frame": True,
         "count": int(len(Pc)), "fields": ["x", "y", "z"], "sha256": sha256_file(out / "laser.ply"),
         "note": "ETH3D dslr_scan_eval laser points, 2 cm voxels"},
        {"id": "occluders", "kind": "other", "path": "occluders.ply", "format": "ply", "in_scene_frame": True,
         "count": int(len(Oc)), "sha256": sha256_file(out / "occluders.ply"),
         "note": "ETH3D occlusion splats (surfaces the scanner missed); blockers for visibility only"},
        {"id": "coverage", "kind": "coverage_grid", "path": "coverage.npz", "format": "npz", "sha256": sha256_file(out / "coverage.npz")},
    ]
    scene["provenance"]["runtime_s"] = round(time.time() - t0, 1)
    scene["provenance"]["hardware"] = "4-core CPU VM, no GPU"
    report["scene_sha256"] = save_json(scene, out / "scene.json")
    report["walls"] = [(w["id"], w.get("s_range_m"), w.get("observed_fraction")) for w in scene["walls"]]
    report["bands"] = scene["coverage"]["bands"]
    (out / "build_report.json").write_text(json.dumps(report, indent=1, default=float))
    return report
