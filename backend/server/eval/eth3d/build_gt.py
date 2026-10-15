"""Ground-truth scene v0 for the ETH3D "facade" courtyard wing, from the laser scan.

Reuses PR #13's builder (vendor/hs13_facade/build_scene.py, unchanged) for the laser fits and the
hand-clicked openings, then writes a scene v0 instead of a PR #11 scene.json:

- frame: PR #13's (y = fitted ground normal, 1.3 deg off the scanner's vertical; origin on the
  ground below the invented meter), rotated so +z is w1's outward normal, in meters;
- walls w1-w3, windows 1-5 and the entrance door exactly as PR #13 measured them;
- the laser points of the wing (2 cm voxels) and ETH3D's occluder splats as assets;
- all 76 DSLR cameras with their dataset poses;
- coverage measured, not asserted: rays from every camera through its laser z-buffer, grazing
  hits (> 75 deg, PR #13's judgement call) do not mark the surface as seen;
- facing and overheads from the laser with PR #13's profile rule (in meters).

    python eth3d/build_gt.py --data ~/data/eth3d/facade --out ~/data/eval-harness/facade-gt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "vendor/hs13_facade"))
sys.path.insert(0, str(HERE))

import build_scene as hs13  # noqa: E402

from gt_common import finish_gt, voxel_downsample  # noqa: E402

from server.eval.frame import M_PER_FT, apply, mat4_to_list, pose_cv_to_arkit, transform_pose  # noqa: E402
from server.eval.sceneio import read_ply  # noqa: E402
from server.eval.visibility import Camera  # noqa: E402

ANN = HERE.parent / "vendor/hs13_facade/annotations.json"
# Openings PR #13 did not click, read from laser wall elevations by the C1 rules-3d worker
# (internal/breadth/c1-rules-3d/annotations/facade.json). u = feet from the wall's left end.
LASER_OPENINGS = [
    {"id": "win_w2_arch", "class": "window", "wall_id": "w2", "u_ft": [1.0, 6.5], "h_ft": [3.5, 13.0], "plus_minus_ft": 0.3,
     "label": "w2 tower-side ground-floor arched window (laser elevation: recessed reveal; seen obliquely in DSC_0419; missed by PR #13)"},
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voxel", type=float, default=0.1, help="coverage voxel (m)")
    args = ap.parse_args()
    t0 = time.time()
    ann = json.loads(ANN.read_text())
    report: dict = {}

    # PR #13 steps 1-3: scan crop, ground plane, walls, meter-anchored frame (feet).
    rng = np.random.default_rng(hs13.RNG_SEED)
    P = hs13.load_scan_crop(args.data / "dslr_scan_eval", ann["roi_scan_xy_m"])
    g_n, g_c, g_stats = hs13.fit_ground(P, ann["ground_fit_scan_xy_m"], rng)
    frame = hs13.make_frame(g_n, g_c)
    walls, _ = hs13.fit_walls(frame.pts(P), ann["walls"], frame, rng)
    mt = ann["meter"]
    wm = next(w for w in walls if w.id == mt["wall_id"])
    foot_plan = wm.p0 + mt["from_wall_left_end_ft"] * wm.along
    foot_scan = frame.origin + frame.A.T @ (np.array([foot_plan[0], 0.0, foot_plan[1]]) * M_PER_FT)
    frame = hs13.Frame(frame.A, foot_scan)
    rng = np.random.default_rng(hs13.RNG_SEED)
    walls, wall_stats = hs13.fit_walls(frame.pts(P), ann["walls"], frame, rng)
    wm = next(w for w in walls if w.id == mt["wall_id"])
    s_meter = wm.s0 + mt["from_wall_left_end_ft"]
    for w in walls:
        w.s0 -= s_meter
    walls_by_id = {w.id: w for w in walls}
    report["ground"] = g_stats

    # Canonical rotation: x along w1, z = w1 outward (plan rotation about y), feet -> meters.
    a, o = wm.along, wm.outward
    R_c = np.array([[a[0], 0, a[1]], [0, 1, 0], [o[0], 0, o[1]]])
    T_c_scan = np.eye(4)
    T_c_scan[:3, :3] = R_c @ frame.A
    T_c_scan[:3, 3] = -R_c @ frame.A @ frame.origin

    def plan_c(q_ft):
        q = np.asarray(q_ft, float)
        return [round(float(q @ a) * M_PER_FT, 4), round(float(q @ o) * M_PER_FT, 4)]

    # Openings (PR #13 step 5), measured on the 7 annotated keyframes at annotation scale.
    colmap = hs13.read_colmap(args.data / "dslr_calibration_undistorted")
    kscale = ann["keyframes"]["scale"]
    kfs = [hs13.make_keyframe(colmap[n], frame, kscale, n) for n in ann["keyframes"]["images"]]
    kfs_by_name = {k.name: k for k in kfs}
    objects = [{
        "id": "meter", "class": "meter", "label": "electric meter (INVENTED: placed by PR #13 between windows 4 and 5)",
        "geometry": {"kind": "point", "pos_m": [0.0, round(mt["height_ft"] * M_PER_FT, 4), 0.0]},
        "wall_id": "w1", "confidence": 1.0, "plus_minus_m": round(mt["plus_minus_ft"] * M_PER_FT, 4),
        "source": {"kind": "annotation", "model": "invented by PR #13"}, "evidence": "observed",
    }]
    for i, feat in enumerate(ann["features"]):
        wall, mean, spread, per_image = hs13.measure_feature(feat, kfs_by_name, walls_by_id)
        pm = max(ann["min_plus_minus_ft"], spread if spread is not None else ann["single_view_plus_minus_ft"],
                 feat.get("plus_minus_floor_ft", {}).get("value", 0.0))
        oid = feat["label"].replace("w1 window ", "win").replace("w3 entrance door", "door")
        objects.append({
            "id": oid, "class": feat["type"], "label": feat["label"],
            "geometry": {"kind": "wall_rect", "wall_id": wall.id,
                         "s_m": [round(float(mean[0]) * M_PER_FT, 4), round(float(mean[1]) * M_PER_FT, 4)],
                         "bottom_m": round(max(0.0, float(mean[2])) * M_PER_FT, 4),
                         "top_m": round(float(mean[3]) * M_PER_FT, 4)},
            "wall_id": wall.id, "confidence": 1.0, "plus_minus_m": round(pm * M_PER_FT, 4),
            "source": {"kind": "annotation", "model": "PR #13 hand clicks ray-cast through dataset poses onto laser walls",
                       "views": [{"image": k, "points_px": [list(map(float, feat["points_px"][k][r])) for r in hs13.POINT_ROLES]}
                                 for k in feat["points_px"]]},
            **({"attrs": feat["attrs"]} if "attrs" in feat else {}),
            "evidence": "observed",
        })

    for lo_ in LASER_OPENINGS:
        w = walls_by_id[lo_["wall_id"]]
        objects.append({
            "id": lo_["id"], "class": lo_["class"], "label": lo_["label"],
            "geometry": {"kind": "wall_rect", "wall_id": w.id,
                         "s_m": [round((w.s0 + lo_["u_ft"][0]) * M_PER_FT, 4), round((w.s0 + lo_["u_ft"][1]) * M_PER_FT, 4)],
                         "bottom_m": round(lo_["h_ft"][0] * M_PER_FT, 4), "top_m": round(lo_["h_ft"][1] * M_PER_FT, 4)},
            "wall_id": w.id, "confidence": 1.0, "plus_minus_m": round(lo_["plus_minus_ft"] * M_PER_FT, 4),
            "source": {"kind": "laser_scan", "model": "C1 rules-3d worker: laser wall elevation", "views": [{"image": "DSC_0419"}]},
            "evidence": "observed",
        })

    wall_json = []
    for w, st in zip(walls, wall_stats):
        wall_json.append({
            "id": w.id, "role": "house", "baseline_m": [plan_c(w.p0), plan_c(w.p1)],
            "height_m": round(w.height_ft * M_PER_FT, 3),
            "s_range_m": [round(w.s0 * M_PER_FT, 4), round((w.s0 + w.length) * M_PER_FT, 4)],
            "plus_minus_m": round(max(0.03, 3 * w.fit_rms_ft * M_PER_FT), 3),
            "evidence": "observed",
            "support": {"points": int(st["inliers"]), "fit_rms_m": round(st["rms_ft"] * M_PER_FT, 4)},
        })
    corners = []
    for i in range(len(walls) - 1):
        d0, d1 = walls[i].along, walls[i + 1].along
        d0c, d1c = np.array([d0 @ a, d0 @ o]), np.array([d1 @ a, d1 @ o])
        corners.append({"id": f"c{i + 1}", "left_wall": walls[i].id, "right_wall": walls[i + 1].id,
                        "point_m": plan_c(walls[i].p1), "s_m": round((walls[i].s0 + walls[i].length) * M_PER_FT, 4),
                        "turn_deg": round(float(np.degrees(np.arctan2(d0c[0] * d1c[1] - d0c[1] * d1c[0], d0c @ d1c))), 2),
                        "plus_minus_m": max(wall_json[i]["plus_minus_m"], wall_json[i + 1]["plus_minus_m"]),
                        "evidence": "observed"})
    ends = {side: {"kind": ann["chain_ends"][side]["kind"], "note": ann["chain_ends"][side]["note"],
                   "point_m": plan_c(walls[0].p0 if side == "left" else walls[-1].p1),
                   "s_m": round((walls[0].s0 if side == "left" else walls[-1].s0 + walls[-1].length) * M_PER_FT, 4)}
            for side in ("left", "right")}

    scene = {
        "format": "base-scene/v0",
        "scene_id": "eth3d-facade/gt-laser",
        "provenance": {
            "data": "real", "site": "eth3d-facade",
            "producer": {"path": "gt-laser", "version": "0",
                         "code_ref": "internal/eval-harness/eth3d/build_gt.py + PR #13 build_scene.py @ 52aa87f"},
            "inputs": {"images_given": 76, "images_used": 76, "sensors_used": ["images", "intrinsics", "laser_scan", "annotations"]},
            "scale_source": "ground_truth", "gravity_source": "ground_truth",
            "license": "ETH3D data: research use only (CC BY-NC-SA)",
            "notes": ["+y is the fitted courtyard ground normal (PR #13), 1.3 deg off the scanner's vertical.",
                      "The electric meter is invented (PR #13). Openings are hand clicks cast onto laser walls; windows agree with the scan within +/-0.01-0.37 ft.",
                      "Coverage records what the 76 photos saw (laser z-buffer ray casting), not what the laser saw."],
        },
        "frame": {"units": "m", "convention": "meter_anchored/v0",
                  "anchor": {"element_id": "meter", "plus_minus_m": 0.0, "heading_plus_minus_deg": 0.1},
                  "source_frame": "eth3d_world", "from_source": mat4_to_list(T_c_scan, 9)},
        "walls": wall_json,
        "chain": {"wall_ids": [w.id for w in walls], "corners": corners, "ends": ends},
        "ground": {"surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0},
                   "plus_minus_m": round(max(0.01, 3 * g_stats["rms_m"]), 3), "regions": []},
        "objects": objects,
        "coverage": {"method": "laser_visibility: rays from all 76 cameras to the laser z-buffer surface; hits over 75 deg incidence do not mark the surface"},
    }
    Pc = voxel_downsample(apply(T_c_scan, P), 0.02)
    xs = [q[0] for wj in wall_json for q in wj["baseline_m"]]
    zs = [q[1] for wj in wall_json for q in wj["baseline_m"]]
    lo, hi = np.array([min(xs) - 4.0, -0.5, -2.0]), np.array([max(xs) + 4.0, 6.0, max(zs) + 16.0])
    occ = read_ply(args.data / "occlusion/splats.ply")
    Oc = apply(T_c_scan, np.c_[occ["x"], occ["y"], occ["z"]].astype(float))
    Oc = voxel_downsample(Oc[np.all((Oc > lo - 5) & (Oc < hi + 5), axis=1)], 0.03)
    cams = [Camera(name, transform_pose(T_c_scan, pose_cv_to_arkit(colmap[name].R, colmap[name].t)), colmap[name].cam,
                   colmap[name].w, colmap[name].h) for name in sorted(colmap)]
    report = finish_gt(scene, Pc, Oc, cams, args.out, args.voxel, report, t0)
    print(json.dumps({k: v for k, v in report.items() if k != "bands"}, indent=1, default=float))
    print("bands:")
    for b in scene["coverage"]["bands"]:
        print("  ", b)


if __name__ == "__main__":
    main()
