"""Turn PR #13's ETH3D facade annotations into a C2 labelled scene (3D boxes in the scan frame).

Reuses PR #13's own builder (verification/scenes/eth3d-facade/build_scene.py, copied unmodified
into PR13_DIR) to rebuild its frame, walls and clicked features, so the two engines see identical
openings and the identical invented meter.

    python facade_labels_from_pr13.py PR13_DIR FACADE_DATA out.json
"""

import json
import sys
from pathlib import Path

import numpy as np

pr13_dir, data, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(pr13_dir))
import build_scene as B  # noqa: E402

ann = json.loads((pr13_dir / "annotations.json").read_text())
rng = np.random.default_rng(B.RNG_SEED)
P = B.load_scan_crop(data / "dslr_scan_eval", ann["roi_scan_xy_m"])
g_n, g_c, _ = B.fit_ground(P, ann["ground_fit_scan_xy_m"], rng)
frame = B.make_frame(g_n, g_c)
walls, _ = B.fit_walls(frame.pts(P), ann["walls"], frame, rng)
mt = ann["meter"]
wm = next(w for w in walls if w.id == mt["wall_id"])
foot_plan = wm.p0 + mt["from_wall_left_end_ft"] * wm.along
foot_scan = frame.origin + frame.A.T @ (np.array([foot_plan[0], 0.0, foot_plan[1]]) * B.M_PER_FT)
frame = B.Frame(frame.A, foot_scan)
rng = np.random.default_rng(B.RNG_SEED)
walls, _ = B.fit_walls(frame.pts(P), ann["walls"], frame, rng)
wm = next(w for w in walls if w.id == mt["wall_id"])
s_meter = wm.s0 + mt["from_wall_left_end_ft"]
for w in walls:
    w.s0 -= s_meter
walls_by_id = {w.id: w for w in walls}
colmap = B.read_colmap(data / "dslr_calibration_undistorted")
kfs = [B.make_keyframe(colmap[n], frame, ann["keyframes"]["scale"], f"k{i + 1}")
       for i, n in enumerate(ann["keyframes"]["images"])]
kfs_by_name = {k.name: k for k in kfs}


def to_scan(X_scene_ft):
    X = np.atleast_2d(np.asarray(X_scene_ft, float))
    return frame.origin + (X * B.M_PER_FT) @ frame.A


def plan_scene(w, s, out_ft):
    p = w.plan_at(s) + out_ft * w.outward
    return np.array([p[0], 0.0, p[1]])


objects = []
for feat in ann["features"]:
    wall, mean, spread, _ = B.measure_feature(feat, kfs_by_name, walls_by_id)
    pm = max(ann["min_plus_minus_ft"], spread if spread is not None else ann["single_view_plus_minus_ft"],
             feat.get("plus_minus_floor_ft", {}).get("value", 0.0))
    s0, s1, bottom, top = (float(x) for x in mean)
    # opening box: along the wall over its span, from 0.5 ft behind the baseline to 0.2 ft in front
    corners = [plan_scene(wall, s, o) for s in (s0, s1) for o in (-0.5, 0.2)]
    C = to_scan(corners)[:, :2]
    mid = plan_scene(wall, (s0 + s1) / 2, 0.0)
    z_lo = to_scan([mid + [0, max(0.0, bottom), 0]])[0, 2]
    z_hi = to_scan([mid + [0, top, 0]])[0, 2]
    objects.append({
        "id": feat["label"].replace(" ", "_"),
        "type": feat["type"],
        "plan_polygon": [C[0].tolist(), C[2].tolist(), C[3].tolist(), C[1].tolist()],
        "z_range": [float(z_lo), float(z_hi)],
        "plus_minus_m": round(pm * B.M_PER_FT, 4),
        "provenance": "PR #13 hand clicks in 2-3 DSLR photos ray-cast through COLMAP poses",
        "real": True,
        "pr11": {"wall_id": wall.id, "span_ft": [round(s0, 2), round(s1, 2)],
                 "bottom_ft": round(max(0.0, bottom), 2), "top_ft": round(top, 2)},
    })

meter_scan = to_scan([[*(wm.p0 + mt["from_wall_left_end_ft"] * wm.along)[[0]], mt["height_ft"],
                       (wm.p0 + mt["from_wall_left_end_ft"] * wm.along)[1]]])[0]
chain = []
for w in walls:
    a = to_scan([[w.p0[0], 0, w.p0[1]]])[0]
    b = to_scan([[w.p1[0], 0, w.p1[1]]])[0]
    chain.append({"id": w.id, "a": a.tolist(), "b": b.tolist(), "s0_ft": w.s0, "length_ft": w.length})

gz_ = float(to_scan([[0, 0, 0]])[0, 2])
scene = {
    "id": "eth3d-facade-wing",
    "real": True,
    "source": "ETH3D facade (dslr_scan_eval laser scans + dslr_undistorted COLMAP poses); labels from "
              "PR #13 annotations.json; the electric meter is invented (PR #13).",
    "license": "ETH3D: CC BY-NC-SA 4.0 (research, non-commercial)",
    "frame": "ETH3D scan frame, metres, z up",
    "roi": {"min": [-6.0, -34.0, gz_ - 0.8], "max": [26.0, -5.0, gz_ + 4.5]},
    "ground_z_hint": gz_,
    "meter": {"pos": meter_scan.tolist(), "plus_minus_m": round(mt["plus_minus_ft"] * B.M_PER_FT, 4),
              "real": False, "note": mt["note"]},
    "objects": objects,
    "ground_types": [{"type": "gravel", "plan_polygon": [[-6, -34], [26, -34], [26, -5], [-6, -5]],
                      "provenance": "photos: fine gravel courtyard (PR #13 README: gravel drainage slope)"}],
    "keyframes": ann["keyframes"]["images"],
    "reference_chain": {"walls": chain, "ends": ann["chain_ends"],
                        "note": "PR #13 wall chain (scan frame, s in ft from the meter) for mapping C2 "
                                "spots onto PR #11's along-wall coordinate"},
}
out.write_text(json.dumps(scene, indent=1))
print("wrote", out, "objects", len(objects), "meter", meter_scan.round(3), "ground z", round(gz_, 3))
for w in chain:
    print(w["id"], np.round(w["a"], 2), np.round(w["b"], 2), round(w["s0_ft"], 2), round(w["length_ft"], 2))
