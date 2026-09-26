"""Per-scene inputs: PR #12's ETH3D preparation plus ARKit-like poses from the packet generator.

    python -m a2ff.prepare delivery_area [--draws 3]

1. `evals.recon.prepare` (PR #12, unchanged except the group sizes): 1024 px images, laser-scan
   visibility per view, normals, and seed groups (8 seeds spread through the capture, each with its
   nearest same-facing cameras), here of 1, 2, 4, 8 and 16 views. Groups of up to 8 are identical to
   PR #12's.
2. ARKit-like poses (synthetic metadata on real images). The packet generator's `align` puts the
   ETH3D ground-truth cameras into an ARKit world (gravity y-up from scene planes, metric), then
   `apply_drift` corrupts the whole capture in DSC order (the photographer's walk) with one draw of
   a profile: per-session scale error, yaw and position random walks per metre walked, per-frame
   jitter. Tracking resets are switched off (a reset splits a session into unrelated frames, which
   the packet marks as separate epochs). Intrinsics get the profile's factory-calibration error.
   Profiles: lidar_modern, nonlidar_modern, nonlidar_2018 (profiles.json, fitted to PR #12's ADVIO
   and MARViN measurements). `exact` is the ETH3D ground truth, as a ceiling.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from a2ff.common import GROUP_SIZES, pose_file, scene_dir, WIDTH
from evals import recon
from evals.eth3d import read_views
from packetgen.align import align
from packetgen.noise import apply_drift, corrupt_intrinsics, load_profiles
from packetgen.sources import from_colmap_model

PROFILES = ("lidar_modern", "nonlidar_modern", "nonlidar_2018")


def arkit_poses(scene: str, draws: int) -> None:
    sdir = scene_dir(scene)
    views = {v.name: v for v in read_views(sdir)}
    rec = from_colmap_model(sdir / "dslr_calibration_undistorted", sdir / "images", metric=True,
                            source=f"eth3d/{scene}")
    al = align(rec)
    names = [v.name.rsplit(".", 1)[0] for v in rec.views]
    # OpenCV cameras in the ARKit world, capture (DSC) order.
    T_ar = [al.pose(v.T_cv) @ np.diag([1.0, -1.0, -1.0, 1.0]) for v in rec.views]
    R = np.array([T[:3, :3] for T in T_ar])
    c = np.array([T[:3, 3] for T in T_ar])
    K_true = {}
    for n in names:
        v = views[n]
        K = v.scaled_K(WIDTH, round(v.height * WIDTH / v.width))
        K_true[n] = [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]
    exact = {"setting": "exact", "frame": "ETH3D world (metric laser frame)",
             "views": {n: {"T": views[n].cam_to_world.tolist(), "K": K_true[n]} for n in names}}
    pose_file(scene, "exact").parent.mkdir(parents=True, exist_ok=True)
    pose_file(scene, "exact").write_text(json.dumps(exact))
    profiles = load_profiles()
    walk = float(np.sum(np.linalg.norm(np.diff(c[:, [0, 2]], axis=0), axis=1)))
    for name in PROFILES:
        prof = profiles[name]
        for d in range(draws):
            rng = np.random.default_rng(1000 * PROFILES.index(name) + d)
            R_obs, c_obs, _, draw = apply_drift(R, c, prof, rng, reset_index=None)
            fx, fy, cx, cy = corrupt_intrinsics(tuple(K_true[names[0]]), WIDTH, prof, rng)
            f_rel, (dcx, dcy) = fx / K_true[names[0]][0], (cx - K_true[names[0]][2], cy - K_true[names[0]][3])
            out = {"setting": f"{name}.draw{d}", "frame": "ARKit world (gravity y-up), OpenCV camera axes",
                   "profile": name, "draw": d, "scale": draw.scales[0],
                   "session_multiplier": draw.session_multiplier, "walk_m": walk,
                   "focal_rel": f_rel, "principal_shift_px": [dcx, dcy], "views": {}}
            for i, n in enumerate(names):
                T = np.eye(4)
                T[:3, :3], T[:3, 3] = R_obs[i], c_obs[i]
                k = K_true[n]
                out["views"][n] = {"T": T.tolist(),
                                   "K": [k[0] * f_rel, k[1] * f_rel, k[2] + dcx, k[3] + dcy]}
            pose_file(scene, f"{name}.draw{d}").write_text(json.dumps(out))
            print(f"{scene} {name} draw {d}: scale {draw.scales[0]:.4f}, session x{draw.session_multiplier:.2f}, "
                  f"focal x{f_rel:.4f}, walk {walk:.0f} m, up vs cameras {al.up_vs_cameras_deg:.1f} deg")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("--draws", type=int, default=3)
    ap.add_argument("--skip-recon", action="store_true")
    args = ap.parse_args()
    if not args.skip_recon:
        recon.GROUP_SIZES = GROUP_SIZES
        recon.prepare(args.scene)
    arkit_poses(args.scene, args.draws)


if __name__ == "__main__":
    main()
