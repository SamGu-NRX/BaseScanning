"""Read a shared-contract scene (base-scene/v0, docs/scene-and-verdict-format.md) into C1's
c1-scene3d/0 dict so engine3d can evaluate any Track A/B output.

Mapping (meters -> feet; plan [x, z] kept, so the meter frame carries over):
- walls: baseline_m left->right seen from outside = C1 a->b; outward = baseline turned 90 deg
  clockwise from above ([dx, dz] -> [-dz, dx]); s_range_m gives each wall's s at a.
- chain.corners -> joints (turn > 0 outside, < 0 inside); chain.ends -> free ends; walls not on
  the chain get unexplored free ends.
- objects: wall_rect -> wall-mounted span (u = s - s_a); footprint / box / point -> plan polygon.
  Classes map onto C1 types (ac_unit -> ac, gas_* -> gas_meter, wall_box/panel -> elec_box).
  Evidence `generated` objects are dropped (unknown), `inferred` kept (fail-or-unsure only is the
  contract's rule; C1 applies it through fail_needs_observed and never passes on them anyway).
- ground: plane -> flat height grid; regions -> ground patches (material unknown -> no patch).
- coverage.bands: wall -> observed wall intervals; ground -> strip polygons 0..out_m; overhead ->
  strip 0..0.91 m; facing (not lower_bound) -> an obstacle line at depth_m.
- overheads[] -> overhead polygons 0..0.91 m out at clearance_m.
- interior: the region behind each wall (60 ft deep), so nothing is searched inside the building.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from shapely import Polygon, unary_union
from shapely.geometry import mapping

FT = 1 / 0.3048
CLASS = {"window": "window", "door": "door", "garage_door": "garage_door", "gas_meter": "gas_meter",
         "gas_regulator": "gas_meter", "gas_pipe": "gas_meter", "ac_unit": "ac", "pool": "pool",
         "hot_tub": "pool", "wall_box": "elec_box", "panel": "elec_box", "vent": "vent",
         "downspout": "downspout", "basement_well": "door"}
MATERIAL = {"concrete": "concrete", "gravel": "gravel", "lawn": "lawn", "dirt": "dirt", "mulch": "mulch",
            "pavers": "concrete", "walkway": "concrete", "drive": "drive", "pool": "pool", "water": "pool",
            "deck": "deck"}


def convert(d: dict) -> dict:
    walls, frames = [], {}
    for w in d["walls"]:
        a, b = (np.array(p, float) * FT for p in w["baseline_m"])
        al = (b - a) / np.linalg.norm(b - a)
        out = np.array([-al[1], al[0]])
        s0 = (w.get("s_range_m") or [0.0])[0] * FT
        frames[w["id"]] = (a, al, out, s0)
        walls.append({"id": w["id"], "a": a.round(4).tolist(), "b": b.round(4).tolist(),
                      "outward": out.round(5).tolist(), "top_y": w["height_m"] * FT,
                      "plus_minus_ft": w["plus_minus_m"] * FT, "real": w.get("evidence") == "observed"})
    ch = d["chain"]
    joints = [{"walls": [c["left_wall"], c["right_wall"]], "ends": ["b", "a"],
               "kind": "outside" if c["turn_deg"] > 0 else "inside", "angle_deg": abs(c["turn_deg"]),
               "gap_ft": 0.0} for c in ch.get("corners", [])]
    joined = {(j["walls"][i], j["ends"][i]) for j in joints for i in (0, 1)}
    free = []
    for w in walls:
        for e in ("a", "b"):
            if (w["id"], e) in joined:
                continue
            kind = "unexplored"
            if w["id"] == ch["wall_ids"][0] and e == "a":
                kind = ch["ends"]["left"]["kind"]
            if w["id"] == ch["wall_ids"][-1] and e == "b":
                kind = ch["ends"]["right"]["kind"]
            free.append({"wall": w["id"], "end": e, "kind": kind})

    def wpt(wid, u, o=0.0):
        a, al, n, _ = frames[wid]
        return (a + al * u + n * o).round(4).tolist()

    objects, meter = [], None
    for o in d["objects"]:
        g = o["geometry"]
        if o["class"] == "meter":
            p = np.array(g.get("pos_m") or g.get("center_m"), float) * FT
            meter = {"pos": p.round(4).tolist(), "wall_id": o["wall_id"], "plus_minus_ft": o["plus_minus_m"] * FT,
                     "real": o.get("evidence") == "observed", "note": o.get("label", "")}
            continue
        if o.get("evidence") == "generated" or o["class"] not in CLASS:
            continue
        rec = {"id": o["id"], "type": CLASS[o["class"]], "plus_minus_ft": o["plus_minus_m"] * FT,
               "attrs": o.get("attrs") or {}, "real": o.get("evidence") == "observed", "note": o.get("label", "")}
        if g["kind"] == "wall_rect":
            _, _, _, s0 = frames[g["wall_id"]]
            u0, u1 = (s * FT - s0 for s in g["s_m"])
            rec.update({"wall_id": g["wall_id"], "span_u": [u0, u1], "footprint": [wpt(g["wall_id"], u0), wpt(g["wall_id"], u1)],
                        "y0": g["bottom_m"] * FT, "y1": g["top_m"] * FT})
        elif g["kind"] == "footprint":
            rec.update({"footprint": (np.array(g["polygon_m"]) * FT).round(4).tolist(),
                        "y0": g["bottom_m"] * FT, "y1": g["top_m"] * FT})
        elif g["kind"] == "box":
            c = np.array(g["center_m"]) * FT
            sw, sh, sd = (x * FT for x in g["size_m"])
            t = np.radians(g.get("yaw_deg", 0.0))
            R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
            pts = [(c[[0, 2]] + R @ np.array([dx, dz])).round(4).tolist()
                   for dx, dz in ((-sw / 2, -sd / 2), (sw / 2, -sd / 2), (sw / 2, sd / 2), (-sw / 2, sd / 2))]
            rec.update({"footprint": pts, "y0": c[1] - sh / 2, "y1": c[1] + sh / 2})
        else:
            p = np.array(g["pos_m"]) * FT
            rec.update({"footprint": [p[[0, 2]].round(4).tolist()], "y0": p[1], "y1": p[1]})
        objects.append(rec)

    # ground
    allp = np.array([p for w in walls for p in (w["a"], w["b"])])
    lo, hi = allp.min(0) - 60, allp.max(0) + 60
    res = 0.5
    nx, nz = int((hi[0] - lo[0]) / res) + 1, int((hi[1] - lo[1]) / res) + 1
    grid = {"x0": float(lo[0]), "z0": float(lo[1]), "res": res, "h": [[0.0] * nx for _ in range(nz)]}
    patches = []
    for r in d["ground"].get("regions", []):
        m = MATERIAL.get(r["material"])
        if m and r.get("evidence", "observed") != "generated":
            patches.append({"id": r["id"], "type": m, "footprint": (np.array(r["polygon_m"]) * FT).round(4).tolist(),
                            "plus_minus_ft": r.get("plus_minus_m", 0.1) * FT})

    # coverage
    cw = {w["id"]: [] for w in walls}
    gpolys, opolys, obstacles = [], [], []
    for b in d["coverage"].get("bands", []):
        wid = b["wall_id"]
        if wid not in frames:
            continue
        _, _, _, s0 = frames[wid]
        u0, u1 = (s * FT - s0 for s in b["s_m"])
        if b["band"] == "wall":
            cw[wid].append([u0, u1])
        elif b["band"] == "ground":
            gpolys.append(Polygon([wpt(wid, u0), wpt(wid, u1), wpt(wid, u1, b["out_m"] * FT), wpt(wid, u0, b["out_m"] * FT)]))
        elif b["band"] == "overhead":
            opolys.append(Polygon([wpt(wid, u0), wpt(wid, u1), wpt(wid, u1, 3.0), wpt(wid, u0, 3.0)]))
    for f in d.get("facing", []):
        if f.get("lower_bound") or f["wall_id"] not in frames:
            continue
        _, _, _, s0 = frames[f["wall_id"]]
        u0, u1 = (s * FT - s0 for s in f["s_m"])
        dep = f["depth_m"] * FT
        obstacles.append({"id": f"facing_{f['wall_id']}_{len(obstacles)}", "type": "obstacle",
                          "footprint": [wpt(f["wall_id"], u0, dep), wpt(f["wall_id"], u1, dep),
                                        wpt(f["wall_id"], u1, dep + 0.5), wpt(f["wall_id"], u0, dep + 0.5)],
                          "y0": 1.0, "y1": 6.0, "plus_minus_ft": f["plus_minus_m"] * FT,
                          "note": f.get("object_id") or "facing obstacle"})
    overheads = []
    for i, o in enumerate(d.get("overheads", [])):
        _, _, _, s0 = frames[o["wall_id"]]
        u0, u1 = (s * FT - s0 for s in o["s_m"])
        overheads.append({"id": f"ovh{i}", "type": "overhead",
                          "footprint": [wpt(o["wall_id"], u0), wpt(o["wall_id"], u1), wpt(o["wall_id"], u1, 3.0), wpt(o["wall_id"], u0, 3.0)],
                          "y0": o["clearance_m"] * FT, "y1": o["clearance_m"] * FT + 0.5, "plus_minus_ft": o["plus_minus_m"] * FT})
        opolys.append(Polygon(overheads[-1]["footprint"]))
    interior = [[wpt(w["id"], 0, 0), wpt(w["id"], frames[w["id"]][1] @ (np.array(w["b"]) - np.array(w["a"])), 0),
                 wpt(w["id"], frames[w["id"]][1] @ (np.array(w["b"]) - np.array(w["a"])), -60), wpt(w["id"], 0, -60)]
                for w in walls]
    return {
        "format": "c1-scene3d/0", "id": d["scene_id"].replace("/", "__"), "units": "ft",
        "provenance": {"from": "base-scene/v0", "scene_id": d["scene_id"], "data": d["provenance"]["data"],
                       "producer": d["provenance"]["producer"]["path"]},
        "meter": meter, "walls": walls, "joints": joints, "free_ends": free,
        "objects": objects, "obstacles": obstacles, "overheads": overheads, "ground_patches": patches,
        "ground": {"grid": grid}, "interior": interior,
        "coverage": {"wall": cw, "ground": mapping(unary_union(gpolys)) if gpolys else None,
                     "overhead": mapping(unary_union(opolys)) if opolys else None},
        "meta": {"converted_from": d["scene_id"], "coverage_rule": d["coverage"].get("rule")},
    }


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.write_text(json.dumps(convert(json.loads(src.read_text()))))
    print(dst)
