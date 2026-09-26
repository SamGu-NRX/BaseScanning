"""Photo-built model (A1 prior poses + pi3 dense) -> scene v0 under one scale mode.

Shape comes from the reconstruction; the meter frame comes from a similarity fit of the A1 cameras
to the dataset cameras (the same perfect-tap convention as score-cloud). The scale mode then
rescales the cloud and cameras about the meter, so scale error grows with distance from it as in
a real meter-anchored pipeline:

- true:    scale error 0, declared bound 0 (ceiling);
- arkit:   the A1 model's own ARKit scale error (simulated non-LiDAR draw), bound 6 %;
- meterModel: A9's no-tape method (meter make and model read from two close-ups, datasheet cover
           size, ARKit as gate): median |error| 0.6 %, applied on the adverse (+) side, with A9's
           median declared 95 % bound 1.4 %;
- meterP90: the same method at its p90 error (2.0 %) with a 2.0 % bound (stress case).

Semantics the photos cannot yet supply are copied from the GT scene as perfect detections, the
same way score-cloud supplies perfect meter taps and opening clicks: ground-region materials and
non-opening fixtures (the synthetic gas meter and AC). Their bounds get the mode's scale term.

    python e2e_scene.py --gt GT/scene.json --recon WORK/pi3 --mode true|arkit|noTape --out DIR
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np

H = Path.home() / "e2e/store/internal/eval-harness"
sys.path.insert(0, str(H))
from evalharness.extract import ExtractParams  # noqa: E402
from evalharness.frame import align_by_rotations, apply, mat4_from_list, pose_cv_to_arkit, scale_of  # noqa: E402
from evalharness.geomscore import score_cloud, score_scene  # noqa: E402
from evalharness.sceneio import load_json, read_ply, save_json, validate, write_ply  # noqa: E402

import evalharness.extract as _ex  # noqa: E402

_ransac = _ex.ransac_plane_up


def ransac_plane_gravity(X, up, rng, thr=0.05, iters=800, max_tilt_deg=12.0):
    """Ground plane with ARKit gravity: tilt limited to 3 deg (dense clouds otherwise let the
    wall feet and the grazing courtyard pull the plane 12 deg off level)."""
    n, c, inl, rms = _ransac(X, up, rng, thr, iters, 3.0)
    if np.degrees(np.arccos(np.clip(n @ up, -1, 1))) > 3.0:
        n = up.copy()
        rms = float(np.sqrt(np.mean(((X[inl] - c) @ n) ** 2)))
    return n, c, inl, rms


_ex.ransac_plane_up = ransac_plane_gravity

MODES = {"true": (None, 0.0), "arkit": ("model", 0.06), "meterModel": (0.006, 0.014), "meterP90": (0.020, 0.020)}
SCALE_SOURCE = {"true": "ground_truth", "arkit": "arkit_poses", "meterModel": "scale_reference", "meterP90": "scale_reference"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--recon", type=Path, required=True)
    ap.add_argument("--mode", choices=sorted(MODES), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-points", type=int, default=300_000)
    a = ap.parse_args()
    gt = load_json(a.gt)
    gkf = {k["id"]: k for k in gt["keyframes"]}
    cams = json.loads((a.recon / "cameras_model.json").read_text())
    cams = [c for c in cams if c["image"] in gkf]
    P = [pose_cv_to_arkit(np.asarray(c["R_cw"]), np.asarray(c["t_cw"])) for c in cams]
    R_src = np.array([p[:3, :3] for p in P]); c_src = np.array([p[:3, 3] for p in P])
    G = [mat4_from_list(gkf[c["image"]]["pose"]) for c in cams]
    R_gt = np.array([g[:3, :3] for g in G]); c_gt = np.array([g[:3, 3] for g in G])
    S = align_by_rotations(R_src, c_src, R_gt, c_gt, with_scale=True)
    s = scale_of(S)
    model_err = 1 / s - 1
    how, pm = MODES[a.mode]
    e = 0.0 if how is None else (model_err if how == "model" else how)
    meter = np.asarray(next(o for o in gt["objects"] if o["id"] == gt["frame"]["anchor"]["element_id"])["geometry"]["pos_m"], float)
    d = read_ply(a.recon / "points_model.ply")
    X = apply(S, np.c_[d["x"], d["y"], d["z"]].astype(float))
    X = meter + (1 + e) * (X - meter)
    X = X[np.linalg.norm(X[:, [0, 2]] - meter[[0, 2]], axis=1) < 25.0]
    _, first = np.unique(np.floor(X / 0.05).astype(np.int64), axis=0, return_index=True)
    X = X[np.sort(first)]
    a.out.mkdir(parents=True, exist_ok=True)
    src = a.out / "src"
    src.mkdir(exist_ok=True)
    write_ply(src / "cloud.ply", X.astype(np.float32))
    cj = []
    for c, p in zip(cams, P):
        q = S @ p
        q[:3, :3] /= s
        q[:3, 3] = meter + (1 + e) * (q[:3, 3] - meter)
        cj.append({"image": c["image"], "pose": q.T.reshape(-1).tolist(), "intrinsics": c["intrinsics"], "w": c["w"], "h": c["h"]})
    (src / "cameras.json").write_text(json.dumps(cj))
    info = {"mode": a.mode, "applied_scale_error_pct": round(100 * e, 3), "model_arkit_scale_error_pct": round(100 * model_err, 3),
            "declared_scale_rel_pm": pm, "cameras": len(cj), "points": int(len(X))}
    p = ExtractParams(scale_rel_pm=pm)
    score_cloud(a.gt, src / "cloud.ply", src / "cameras.json", a.out, metric=True, producer=f"A1prior+pi3/{a.mode}",
                transform=np.eye(4), params=p, max_points=a.max_points, visibility=False)
    sc = load_json(a.out / "scene.json")
    # declared bounds: scale term on facing depth and overhead clearance
    for f in sc.get("facing", []):
        f["plus_minus_m"] = round(f["plus_minus_m"] + pm * f["depth_m"], 3)
    for o in sc.get("overheads", []):
        o["plus_minus_m"] = round(o["plus_minus_m"] + pm * o["clearance_m"], 3)
    # the reference extractor can emit a reversed span for an opening on a wall it fitted backwards
    for o in sc["objects"]:
        g = o["geometry"]
        if g["kind"] == "wall_rect" and g["s_m"][0] > g["s_m"][1]:
            g["s_m"] = sorted(g["s_m"])
    # perfect semantics from the GT scene
    walls = sc["walls"]

    def nearest_wall(xz):
        best, bd = None, 1e9
        for w in walls:
            A, B = np.asarray(w["baseline_m"], float)
            t = np.clip(np.dot(xz - A, B - A) / max(np.dot(B - A, B - A), 1e-9), 0, 1)
            dd = np.linalg.norm(A + t * (B - A) - xz)
            if dd < bd:
                best, bd = w["id"], dd
        return best

    def sc_xz(q):
        q = np.asarray(q, float)
        return (meter[[0, 2]] + (1 + e) * (q - meter[[0, 2]])).round(4).tolist()

    injected = []
    for o in gt["objects"]:
        if o["class"] in ("meter", "window", "door", "garage_door", "basement_well") or o["id"] in {x["id"] for x in sc["objects"]}:
            continue
        o = copy.deepcopy(o)
        g = o["geometry"]
        if g["kind"] == "footprint":
            g["polygon_m"] = [sc_xz(q) for q in g["polygon_m"]]
            c = np.mean(np.asarray(g["polygon_m"]), 0)
        elif g["kind"] == "point":
            g["pos_m"] = (meter + (1 + e) * (np.asarray(g["pos_m"]) - meter)).round(4).tolist()
            c = np.asarray(g["pos_m"])[[0, 2]]
        elif g["kind"] == "box":
            g["center_m"] = (meter + (1 + e) * (np.asarray(g["center_m"]) - meter)).round(4).tolist()
            c = np.asarray(g["center_m"])[[0, 2]]
        else:
            continue
        o["wall_id"] = nearest_wall(c) if walls else None
        if o["wall_id"] is None:
            o.pop("wall_id")
        o["plus_minus_m"] = round(o.get("plus_minus_m", 0) + 0.05 + pm * float(np.linalg.norm(c)), 3)
        o["source"] = {"kind": "annotation", "model": "perfect detection copied from the GT scene (E2E run)"}
        sc["objects"].append(o)
        injected.append(o["id"])
    regs = []
    for r in gt["ground"].get("regions", []):
        r = copy.deepcopy(r)
        r["polygon_m"] = [sc_xz(q) for q in r["polygon_m"]]
        r["source"] = {"kind": "annotation", "model": "perfect material label copied from the GT scene (E2E run)"}
        regs.append(r)
    sc["ground"]["regions"] = regs
    sc["provenance"]["scale_source"] = SCALE_SOURCE[a.mode]
    sc["provenance"]["gravity_source"] = "arkit"
    sc["provenance"]["data"] = "hybrid"
    sc["provenance"]["notes"] = sc["provenance"].get("notes", []) + [
        f"E2E: A1 COLMAP (simulated non-LiDAR ARKit priors, seed 0) + pi3 dense on the refined poses; scale mode {a.mode}: {json.dumps(info)}",
        f"Perfect semantics copied from GT: objects {injected}, ground regions {[r['id'] for r in regs]}."]
    validate(sc)
    save_json(sc, a.out / "scene.json")
    r = score_scene(a.gt, a.out / "scene.json", a.out, visibility=False)
    info["injected"] = injected
    info["kd"] = r["key_distances"]["overall"]
    info["kd_status"] = r["key_distances"]["status_counts"]
    info["walls"] = [(w["id"], w["role"], w.get("s_range_m"), w.get("observed_fraction")) for w in sc["walls"]]
    info["chain"] = sc["chain"]["wall_ids"]
    (a.out / "e2e_info.json").write_text(json.dumps(info, indent=1, default=float))
    print(json.dumps(info, default=float))


if __name__ == "__main__":
    main()
