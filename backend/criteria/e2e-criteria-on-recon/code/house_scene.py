"""Walkaround house (no ARKit, no ground truth) -> scene v0.

Geometry: an exported recon dir (points_model.ply + cameras_model.json: image-only COLMAP, optionally
plus pi3 dense on its poses). Gravity: mean camera up vector (handheld walk), refined by the ground
plane. Scale: passive prior, camera height 1.5 m above the fitted ground (A9: passive priors 5.3 % p90;
declared 95 % bound 7 %); no meter close-up exists in these videos, so A9's meter make-and-model read
is not available. Meter: hand taps in the listed frames (the capture flow's meter tap). Optional
fixtures (AC) from two taps each, as 0.8 m footprints on the ground; ground material from the
dataset's house notes, as one region around the meter wall, `inferred`.

    python house_scene.py --recon DIR --taps TAPS.json --out OUT
TAPS.json: {"site": id, "meter": {"frame": [u, v], ...}, "fixtures": [{"id", "class", "taps": {"frame": [u, v]}}],
            "ground_material": "lawn", "camera_height_m": 1.5}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path.home() / "e2e/store/internal/eval-harness"))
from evalharness.coverage import CoverageQuery, bands_from_grid, build_grid, coverage_rule, ground_sensitivity, set_wall_observed_fraction  # noqa: E402
from evalharness.extract import ExtractParams, extract_scene, profile_entries, ransac_plane_up, triangulate  # noqa: E402
from evalharness.frame import apply, pose_cv_to_arkit, scale_of  # noqa: E402
from evalharness.sceneio import read_ply, save_json, sha256_file, validate, write_ply  # noqa: E402
from evalharness.visibility import Camera, VisibilityOracle  # noqa: E402

REL_PM = 0.07


def rot_to(a, b):
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), float(a @ b)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", type=Path, required=True)
    ap.add_argument("--taps", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-points", type=int, default=300_000)
    a = ap.parse_args()
    T = json.loads(a.taps.read_text())
    d = read_ply(a.recon / "points_model.ply")
    X = np.c_[d["x"], d["y"], d["z"]].astype(float)
    cj = json.loads((a.recon / "cameras_model.json").read_text())
    P = [pose_cv_to_arkit(np.asarray(c["R_cw"]), np.asarray(c["t_cw"])) for c in cj]
    up0 = np.mean([p[:3, 1] for p in P], 0)
    up = np.array([0.0, 1.0, 0.0])
    rng = np.random.default_rng(0)
    H = T.get("camera_height_m", 1.5)

    def mat(Rm, t=np.zeros(3), sc=1.0):
        M = np.eye(4)
        M[:3, :3] = sc * Rm
        M[:3, 3] = t
        return M

    C = np.array([p[:3, 3] for p in P])
    S = mat(rot_to(up0, up))
    for tilt, band in ((15.0, None), (3.0, 0.7)):
        Xs, Cs = apply(S, X), apply(S, C)
        if band is None:
            low = Xs[Xs[:, 1] < np.median(Cs[:, 1]) - 0.2 * np.ptp(Cs[:, 1]) - 0.3]
            thr = 0.03 * np.ptp(Cs[:, 1]) + 0.02
        else:
            low = Xs[Xs[:, 1] < band]
            thr = 0.04
        n, cg, _, _ = ransac_plane_up(low, up, rng, thr=thr, max_tilt_deg=tilt)
        Rn = rot_to(n, up)
        gy = float((Rn @ cg)[1])
        S = mat(Rn, np.array([0, -gy, 0])) @ S
        h_cam = float(np.median(apply(S, C)[:, 1]))
        S = mat(np.eye(3), sc=H / h_cam) @ S
    s = scale_of(S)
    Xw = apply(S, X)
    cams = []
    for c, p in zip(cj, P):
        q = S @ p
        q[:3, :3] /= s
        cams.append(Camera(c["image"], q, np.asarray(c["intrinsics"], float), c["w"], c["h"]))
    idx = {c.id: i for i, c in enumerate(cams)}
    ox, oy = T.get("tap_offset", [0.0, 0.0])
    T["meter"] = {f: [u - ox, v - oy] for f, (u, v) in T["meter"].items()}
    for fx in T.get("fixtures", []):
        fx["taps"] = {f: [u - ox, v - oy] for f, (u, v) in fx["taps"].items()}
    taps = [(idx[f], u, v) for f, (u, v) in T["meter"].items() if f in idx]
    if len(taps) < 2:
        raise SystemExit(f"meter taps in registered frames: {len(taps)}")
    M0 = triangulate([cams[i] for i, _, _ in taps], [(u, v) for _, u, v in taps])
    keep = np.linalg.norm(Xw[:, [0, 2]] - M0[[0, 2]], axis=1) < 25
    Xw = Xw[keep]
    _, first = np.unique(np.floor(Xw / 0.05).astype(np.int64), axis=0, return_index=True)
    Xw = Xw[np.sort(first)]
    if len(Xw) > a.max_points:
        Xw = Xw[rng.choice(len(Xw), a.max_points, replace=False)]
    vis = VisibilityOracle(cams, Xw)
    tracks = [np.flatnonzero(r) for r in vis.seen_by(Xw, None)]
    p = ExtractParams(scale_rel_pm=REL_PM)
    prov = {"data": "real", "site": T["site"],
            "producer": {"path": "A1-colmap-imageonly" + ("+pi3" if "pi3" in a.recon.name else ""), "version": "0",
                         "params": {"camera_height_scale": round(s, 5), 
                                    "declared_scale_rel_pm": REL_PM, "recon": str(a.recon)}},
            "inputs": {"images_used": len(cams), "sensors_used": ["images", "taps"]},
            "scale_source": "none", "gravity_source": "ground_plane_fit",
            "notes": ["Scale from an assumed 1.5 m camera height (passive prior); no ARKit, no tape, no readable meter plate.",
                      "Meter = hand taps in the listed frames."]}
    feats = []
    for o in T.get("openings", []):
        pts = {idx[f]: {r: (u - ox, v - oy) for r, (u, v) in roles.items()} for f, roles in o["views"].items() if f in idx}
        if pts:
            feats.append({"id": o["id"], "class": o["class"], "label": o.get("label", o["class"]), "points_px": pts,
                          **({"attrs": o["attrs"]} if "attrs" in o else {})})
    scene, st = extract_scene(Xw, tracks, cams, taps, feats, p, prov, f"{T['site']}/e2e-{a.recon.name}", "colmap_model", S)
    scene["overheads"], scene["facing"] = profile_entries(st["points_c"], scene, pm_m=0.05 + REL_PM * 2, min_count=p.profile_min_count)
    for fx in T.get("fixtures", []):
        ft = [(idx[f], u, v) for f, (u, v) in fx["taps"].items() if f in idx]
        if len(ft) < 2:
            continue
        Fw = triangulate([cams[i] for i, _, _ in ft], [(u, v) for _, u, v in ft])
        Fc = apply(st["T"], Fw[None])[0]
        half = fx.get("size_m", 0.8) / 2
        cx, cz = Fc[0], Fc[2]
        scene["objects"].append({"id": fx["id"], "class": fx["class"], "label": fx.get("label", fx["class"]) + " (hand taps)",
                                 "geometry": {"kind": "footprint", "polygon_m": [[cx - half, cz - half], [cx + half, cz - half], [cx + half, cz + half], [cx - half, cz + half]],
                                              "bottom_m": 0.0, "top_m": round(float(max(Fc[1], 0.5)), 3)},
                                 "confidence": 0.9, "plus_minus_m": round(fx.get("pm_m", 0.15) + REL_PM * float(np.hypot(cx, cz)), 3),
                                 "source": {"kind": "tap", "views": [{"image": cams[i].id, "points_px": [[u, v]]} for i, u, v in ft]},
                                 "evidence": "observed"})
    if T.get("ground_material"):
        scene["ground"]["regions"] = [{"id": "g1", "material": T["ground_material"],
                                       "polygon_m": [[-10, 0.05], [10, 0.05], [10, 5], [-10, 5]], "evidence": "inferred",
                                       "plus_minus_m": 0.3, "source": {"kind": "annotation", "model": "dataset house notes (hand)"}}]
    xs = [q[0] for w in scene["walls"] for q in w["baseline_m"]]
    zs = [q[1] for w in scene["walls"] for q in w["baseline_m"]]
    lo, hi = np.array([min(xs) - 4, -0.5, min(zs) - 2]), np.array([max(xs) + 4, 6.0, max(zs) + 16])
    by_cam: dict[int, list[int]] = {}
    for pi, tr in enumerate(tracks):
        for ci in tr:
            by_cam.setdefault(int(ci), []).append(pi)
    grid = build_grid(((st["cams_c"][ci].pose[:3, 3], st["points_c"][np.array(v)], None) for ci, v in sorted(by_cam.items())), lo, hi, p.voxel_m)
    a.out.mkdir(parents=True, exist_ok=True)
    grid.save(a.out / "coverage.npz")
    write_ply(a.out / "points.ply", st["points_c"].astype(np.float32))
    scene["coverage"] = {"method": "rays from each camera to the cloud points it sees (cloud z-buffer)", "grid": grid.meta("coverage"), "rule": coverage_rule()}
    q = CoverageQuery(st["cams_c"], st["points_c"])
    scene["coverage"]["bands"] = bands_from_grid(grid, scene, query=q)
    scene["coverage"]["sensitivity"] = ground_sensitivity(grid, scene, q)
    set_wall_observed_fraction(scene, q)
    scene["assets"] = [{"id": "points", "kind": "point_cloud", "path": "points.ply", "format": "ply", "in_scene_frame": True,
                        "count": int(len(st["points_c"])), "sha256": sha256_file(a.out / "points.ply")},
                       {"id": "coverage", "kind": "coverage_grid", "path": "coverage.npz", "format": "npz"}]
    validate(scene)
    save_json(scene, a.out / "scene.json")
    rep = {"scale": s, "cams": len(cams), "points": int(len(st["points_c"])), "meter_taps": len(taps),
           "walls": [(w["id"], w["role"], w.get("s_range_m"), w.get("observed_fraction")) for w in scene["walls"]],
           "chain": scene["chain"], "objects": [o["id"] for o in scene["objects"]], "extract": st["report"]}
    (a.out / "house_report.json").write_text(json.dumps(rep, indent=1, default=float))
    print(json.dumps({k: rep[k] for k in ("scale", "cams", "points", "walls", "objects")}, default=float))


if __name__ == "__main__":
    main()
