"""Ground-truth scene v0 for an ETH3D scene labelled by the C2 volumetric worker (electro, delivery_area).

Seed: `internal/breadth/c2-volumetric/scenes/<id>.json` (ETH3D scan frame, metres, z up): the invented
meter, the reference wall chain (ordered left to right seen from outside; outward = direction
turned clockwise seen from above, C2's to_pr11 convention), objects as plan polygons + z ranges
(doors, glazing, a garage door, a mesh cage, and synthetic gas / AC fixtures), ground types.

Frame (meter_anchored/v0): +y = the fitted ground normal near the meter (the scans are levelled,
so this is within a degree of scan +z); origin on the ground below the meter; +z = the meter wall's
outward normal. The laser, occluders, DSLR cameras, coverage, bands and profiles are then added by
gt_common.finish_gt, exactly as for the facade.

Objects marked synthetic in the seed do not exist in the laser or the photos (C2 inserts them into
its grid so the gas / AC rules have something to measure). They are kept as labelled fixtures
(label SYNTHETIC, source annotation) so criteria paths can be compared on them; geometry and
coverage scoring must not treat them as real.

    python eth3d/build_gt_c2.py --seed ../breadth/c2-volumetric/scenes/eth3d-electro-doors.json \
        --data ~/data/eth3d/electro --site eth3d-electro --out ~/data/eval-harness/electro-gt
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
sys.path.insert(0, str(HERE))

from gt_common import cameras_in_scene, finish_gt, load_scans, read_cameras, voxel_downsample  # noqa: E402

from server.eval.chain import Chain  # noqa: E402
from server.eval.extract import ransac_plane_up  # noqa: E402
from server.eval.frame import apply, canonical_from_world, mat4_to_list  # noqa: E402
from server.eval.sceneio import read_ply  # noqa: E402

CLASS = {"door": "door", "window": "window", "garage_door": "garage_door", "gas_meter": "gas_meter", "ac": "ac_unit"}
WALL_RECT = {"door", "window", "garage_door"}
CORNER_JOIN_M = 0.3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voxel", type=float, default=0.1)
    ap.add_argument("--ground-material", action="append", default=[], metavar="ID=MATERIAL",
                    help="override a seed ground region's material, e.g. g1=drive (recorded in provenance notes)")
    a = ap.parse_args()
    overrides = dict(x.split("=", 1) for x in a.ground_material)
    t0 = time.time()
    seed = json.loads(a.seed.read_text())
    rng = np.random.default_rng(7)
    report: dict = {"seed": str(a.seed)}

    lo, hi = np.array(seed["roi"]["min"]), np.array(seed["roi"]["max"])
    P = load_scans(a.data / "dslr_scan_eval", lo - [0, 0, 0.3], hi)
    gz = seed["ground_z_hint"]
    M = np.array(seed["meter"]["pos"], float)
    near = (np.abs(P[:, 2] - gz) < 0.3) & (np.linalg.norm(P[:, :2] - M[:2], axis=1) < 8.0)
    g_n, g_c, g_in, g_rms = ransac_plane_up(P[near], np.array([0.0, 0.0, 1.0]), rng, thr=0.04)
    report["ground"] = {"inliers": int(g_in.sum()), "rms_m": g_rms, "tilt_from_scan_z_deg": float(np.degrees(np.arccos(g_n[2])))}
    foot = M - ((M - g_c) @ g_n) * g_n

    ref = seed["reference_chain"]["walls"]
    segs = [(w["id"], np.array(w["a"][:2], float), np.array(w["b"][:2], float)) for w in ref]

    def dist_to(seg, q):
        _, A, B = seg
        d = (B - A) / np.linalg.norm(B - A)
        t = np.clip((q - A) @ d, 0, np.linalg.norm(B - A))
        return float(np.linalg.norm(A + t * d - q))

    mseg = min(segs, key=lambda s: dist_to(s, M[:2]))
    d = (mseg[2] - mseg[1]) / np.linalg.norm(mseg[2] - mseg[1])
    outward = np.array([d[1], -d[0], 0.0])
    T = canonical_from_world(foot, g_n, outward)
    n_c = T[:3, :3] @ g_n

    def plan(xy, z=None):
        X = np.array([xy[0], xy[1], gz if z is None else z], float)
        X = X - ((X - g_c) @ g_n) * g_n if z is None else X
        return apply(T, X)[[0, 2]]

    walls = []
    for wid, A, B in segs:
        pa, pb = plan(A), plan(B)
        dd = (B - A) / np.linalg.norm(B - A)
        nrm = np.array([dd[1], -dd[0]])
        rel = P[:, :2] - A
        lat, off = rel @ dd, rel @ nrm
        h = (P - g_c) @ g_n
        k = (lat > 0.2) & (lat < np.linalg.norm(B - A) - 0.2) & (np.abs(off) < 0.3) & (h > 1.0) & (h < 2.2)
        top = (lat > 0) & (lat < np.linalg.norm(B - A)) & (np.abs(off) < 0.5)
        height = float(np.percentile(h[top], 99)) if top.sum() > 50 else 3.0
        rms = float(np.sqrt(np.mean(off[k] ** 2))) if k.sum() > 20 else 0.1
        walls.append({"id": wid, "role": "house", "baseline_m": [[round(float(v), 4) for v in pa], [round(float(v), 4) for v in pb]],
                      "height_m": round(max(2.5, height), 3), "plus_minus_m": round(max(0.03, min(0.15, rms)), 3), "evidence": "observed",
                      "support": {"points": int(k.sum()), "fit_rms_m": round(rms, 4)}})
        report.setdefault("wall_fit", {})[wid] = {"points": int(k.sum()), "median_offset_m": round(float(np.median(off[k])), 3) if k.sum() else None,
                                                  "rms_m": round(rms, 3)}

    anchor_pos = apply(T, M)
    objects = [{"id": "meter", "class": "meter", "label": f"electric meter ({seed['meter'].get('note', 'invented')})",
                "geometry": {"kind": "point", "pos_m": [round(float(v), 4) for v in anchor_pos]}, "wall_id": mseg[0], "confidence": 1.0,
                "plus_minus_m": seed["meter"].get("plus_minus_m", 0.05), "source": {"kind": "annotation", "model": "invented by the C2 worker"},
                "evidence": "observed"}]
    scene = {
        "format": "base-scene/v0", "scene_id": f"{a.site}/gt-laser",
        "provenance": {"data": "real", "site": a.site,
                       "producer": {"path": "gt-laser", "version": "0",
                                    "code_ref": f"internal/eval-harness/eth3d/build_gt_c2.py from {a.seed.name}"},
                       "inputs": {"sensors_used": ["images", "intrinsics", "laser_scan", "annotations"]},
                       "scale_source": "ground_truth", "gravity_source": "ground_truth", "license": seed.get("license", "ETH3D research only"),
                       "notes": [seed.get("source", ""), "+y is the fitted ground normal near the meter.",
                                 "The electric meter is invented; objects labelled SYNTHETIC are fixtures with no laser or photo evidence."]},
        "frame": {"units": "m", "convention": "meter_anchored/v0", "anchor": {"element_id": "meter", "plus_minus_m": 0.0, "heading_plus_minus_deg": 0.5},
                  "source_frame": "eth3d_scan", "from_source": mat4_to_list(T, 9)},
        "walls": walls,
        "chain": {"wall_ids": [w["id"] for w in walls], "corners": [], "ends": {}},
        "ground": {"surface": {"kind": "plane", "normal": [round(float(v), 6) for v in n_c], "offset_m": round(float(n_c @ apply(T, g_c)), 5)},
                   "plus_minus_m": round(max(0.01, 3 * g_rms), 3), "regions": []},
        "objects": objects,
        "coverage": {"method": "laser_visibility: rays from every DSLR camera to the laser z-buffer surface; grazing hits do not mark the surface"},
    }
    ch = Chain(scene)
    for w, cw in zip(walls, ch.walls):
        w["s_range_m"] = [round(cw.s0, 4), round(cw.s1, 4)]
    for w0, w1 in zip(walls, walls[1:]):
        p, q = np.array(w0["baseline_m"][1]), np.array(w1["baseline_m"][0])
        if np.linalg.norm(p - q) < CORNER_JOIN_M:
            a0, b0 = np.array(w0["baseline_m"])
            a1, b1 = np.array(w1["baseline_m"])
            d0, d1 = (b0 - a0) / np.linalg.norm(b0 - a0), (b1 - a1) / np.linalg.norm(b1 - a1)
            scene["chain"]["corners"].append({"id": f"c{len(scene['chain']['corners']) + 1}", "left_wall": w0["id"], "right_wall": w1["id"],
                                              "point_m": w0["baseline_m"][1], "s_m": w0["s_range_m"][1],
                                              "turn_deg": round(float(np.degrees(np.arctan2(d0[0] * d1[1] - d0[1] * d1[0], d0 @ d1))), 2),
                                              "plus_minus_m": max(w0["plus_minus_m"], w1["plus_minus_m"]), "evidence": "observed"})
    for side in ("left", "right"):
        e = seed["reference_chain"]["ends"][side]
        w = walls[0] if side == "left" else walls[-1]
        scene["chain"]["ends"][side] = {"kind": e["kind"], "note": e.get("note", ""),
                                        "point_m": w["baseline_m"][0 if side == "left" else 1], "s_m": w["s_range_m"][0 if side == "left" else 1]}

    for o in seed["objects"]:
        poly = np.array(o["plan_polygon"], float)
        z0, z1 = o.get("z_range", [gz, gz + 1.0])
        synthetic = bool(o.get("synthetic"))
        label = ("SYNTHETIC " if synthetic else "") + f"{o['type']} ({o.get('provenance', o.get('note', ''))})"[:300]
        src = {"kind": "annotation", "model": "synthetic fixture (C2 worker)" if synthetic else "C2 worker: laser profiles + DSLR photos"}
        base = {"id": o["id"], "label": label, "confidence": 1.0, "plus_minus_m": o.get("plus_minus_m", 0.05), "source": src, "evidence": "observed"}
        pp = np.array([plan(p) for p in poly])
        if o["type"] == "fence":
            c = pp.mean(0)
            u = np.linalg.svd(pp - c)[2][0]
            t = (pp - c) @ u
            ends = [c + t.min() * u, c + t.max() * u]
            scene["walls"].append({"id": o["id"], "role": "fence", "baseline_m": [[round(float(v), 4) for v in e] for e in ends],
                                   "height_m": round(float(z1 - z0), 3), "plus_minus_m": base["plus_minus_m"], "evidence": "observed"})
            continue
        cls = CLASS.get(o["type"])
        if cls is None:
            continue
        if o["type"] in WALL_RECT:
            idx, s, _ = ch.locate(pp)
            w = ch.walls[int(np.bincount(idx).argmax())]
            s0, s1 = max(w.s0, float(s.min())), min(w.s1, float(s.max()))
            gy = float(np.mean([(scene["ground"]["surface"]["offset_m"] - n_c[0] * x - n_c[2] * z) / n_c[1] for x, z in pp]))
            yb = apply(T, np.r_[poly.mean(0), z0])[1] - gy
            yt = apply(T, np.r_[poly.mean(0), z1])[1] - gy
            objects.append({**base, "class": cls, "geometry": {"kind": "wall_rect", "wall_id": w.id, "s_m": [round(s0, 4), round(s1, 4)],
                                                              "bottom_m": round(max(0.0, yb), 4), "top_m": round(yt, 4)}, "wall_id": w.id})
        else:
            yb, yt = apply(T, np.r_[poly.mean(0), z0])[1], apply(T, np.r_[poly.mean(0), z1])[1]
            objects.append({**base, "class": cls, "geometry": {"kind": "footprint", "polygon_m": [[round(float(x), 4), round(float(z), 4)] for x, z in pp],
                                                              "bottom_m": round(float(yb), 4), "top_m": round(float(yt), 4)},
                            "wall_id": ch.nearest_wall(pp.mean(0)).id})
    mat = {"concrete": "concrete", "asphalt": "concrete", "gravel": "gravel", "lawn": "lawn", "drive": "drive"}
    for i, g in enumerate(seed.get("ground_types", [])):
        gid = f"g{i + 1}"
        material = overrides.get(gid, mat.get(g["type"], "unknown"))
        if gid in overrides:
            scene["provenance"]["notes"].append(f"Ground region {gid}: seed type {g['type']!r} overridden to {material!r} (label author: vans park on it).")
        scene["ground"]["regions"].append({"id": gid, "material": material,
                                           "polygon_m": [[round(float(v), 4) for v in plan(p)] for p in g["plan_polygon"]],
                                           "evidence": "observed", "plus_minus_m": 0.1,
                                           "source": {"kind": "annotation", "model": g.get("provenance", "C2 worker")}})

    Pc = voxel_downsample(apply(T, P), 0.02)
    occ = read_ply(a.data / "occlusion/splats.ply")
    Oc = apply(T, np.c_[occ["x"], occ["y"], occ["z"]].astype(float))
    xs = [q[0] for w in walls for q in w["baseline_m"]]
    zs = [q[1] for w in walls for q in w["baseline_m"]]
    box_lo, box_hi = np.array([min(xs) - 9, -5.5, min(zs) - 7]), np.array([max(xs) + 9, 11.0, max(zs) + 21])
    Oc = voxel_downsample(Oc[np.all((Oc > box_lo) & (Oc < box_hi), axis=1)], 0.03) if len(Oc) else Oc
    cams = cameras_in_scene(T, read_cameras(a.data / "dslr_calibration_undistorted"))
    scene["provenance"]["inputs"]["images_given"] = scene["provenance"]["inputs"]["images_used"] = len(cams)
    report = finish_gt(scene, Pc, Oc, cams, a.out, a.voxel, report, t0)
    print(json.dumps({k: v for k, v in report.items() if k != "bands"}, indent=1, default=float))
    print("objects:", [(o["id"], o["class"], o["geometry"].get("wall_id"), o["geometry"].get("s_m"), o["geometry"].get("bottom_m"),
                        o["geometry"].get("top_m")) for o in scene["objects"]])
    print("chain:", scene["chain"]["corners"], [(w["id"], w.get("s_range_m"), w.get("observed_fraction")) for w in scene["walls"]])


if __name__ == "__main__":
    main()
