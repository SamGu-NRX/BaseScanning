"""Write the C2 labelled scenes for ETH3D electro and delivery_area (scan frame, metres).

Geometry numbers come from laser-point profiles along each wall (tools/elevation.py and the offset
profiles recorded in internal/breadth/C2-volumetric.md); object types come from the DSLR photos.
The electric meters are invented (neither scene has one where placed). Objects marked
"synthetic": true do not exist: c2vol.build inserts them into the grid as occupied voxels so the gas
and AC rules have something to measure.

    python make_scenes.py OUT_DIR
"""

import json
import sys
from pathlib import Path

import numpy as np

FT = 0.3048


def box_along(A, d, n, s0, s1, o0, o1):
    A, d, n = map(np.asarray, (A, d, n))
    return [(A + s * d + o * n).round(3).tolist() for s, o in ((s0, o0), (s1, o0), (s1, o1), (s0, o1))]


def chain_with_s(walls, meter_xy):
    """walls: list of (id, a_xy, b_xy). s0_ft so that s = 0 at the meter's projection."""
    cum, out = 0.0, []
    best = None
    for wid, a, b in walls:
        a, b = np.array(a, float), np.array(b, float)
        L = float(np.linalg.norm(b - a))
        d = (b - a) / L
        t = float(np.clip(np.dot(meter_xy - a, d), 0, L))
        dist = float(np.linalg.norm(a + t * d - meter_xy))
        if best is None or dist < best[0]:
            best = (dist, cum + t)
        out.append({"id": wid, "a": a.tolist(), "b": b.tolist(), "cum": cum, "length_ft": L / FT})
        cum += L
    for w in out:
        w["s0_ft"] = (w.pop("cum") - best[1]) / FT
    return out


def electro():
    gz = -4.942
    # utility-door wall: pillar faces 0.27 m behind the line (1,5)->(9.2,9.5)
    A0, B0 = np.array([1.0, 5.0]), np.array([9.2, 9.5])
    d = (B0 - A0) / np.linalg.norm(B0 - A0)
    n = np.array([d[1], -d[0]])
    A, B = A0 - 0.27 * n, B0 - 0.27 * n
    # glass facade: face 0.2 m behind (-7,0.4)->(-0.2,4.3)
    G0, G1 = np.array([-7.0, 0.4]), np.array([-0.2, 4.3])
    gd = (G1 - G0) / np.linalg.norm(G1 - G0)
    gn = np.array([gd[1], -gd[0]])
    Ga = G0 - 0.2 * gn + gd * 4.73
    Gb = G1 - 0.2 * gn
    objs = []
    for i, (s0, s1) in enumerate([(-0.30, 0.75), (1.35, 3.40), (4.00, 6.05), (6.80, 8.70)], 1):
        objs.append({"id": f"door_{i}", "type": "door", "plan_polygon": box_along(A, d, n, s0, s1, -0.25, 0.05),
                     "z_range": [gz, gz + 3.04], "plus_minus_m": 0.05, "real": True,
                     "provenance": "laser: 0.16 m door recess between pillars (offset profile at 1-2 m height), top 3.04 m; photos: grey steel doors"})
    L = float(np.linalg.norm(Gb - Ga))
    objs.append({"id": "glass_facade", "type": "window", "plan_polygon": box_along(Ga, gd, gn, 0.0, L, -0.15, 0.05),
                 "z_range": [gz, gz + 5.7], "plus_minus_m": 0.1, "real": True,
                 "provenance": "photos: floor-to-ceiling glazing with a glass entrance; span = laser extent of the facade in the ROI"})
    objs.append({"id": "gas_meter_synthetic", "type": "gas_meter", "synthetic": True, "real": False,
                 "plan_polygon": box_along(A0, d, n, 3.50, 3.90, -0.27, 0.0), "z_range": [gz + 0.5, gz + 1.0],
                 "plus_minus_m": 0.0, "provenance": "SYNTHETIC: 0.4 x 0.27 x 0.5 m box on the pillar between doors 2 and 3, inserted as occupied voxels"})
    meter = A0 + 6.375 * d - 0.14 * n
    walls = chain_with_s([("w1", Ga, Gb), ("w2", A, B)], meter)
    return {
        "id": "eth3d-electro-doors", "real": True,
        "source": "ETH3D electro (dslr_scan_eval laser scans); labels hand-placed from laser profiles and DSLR photos; meter invented; gas meter synthetic.",
        "license": "ETH3D: CC BY-NC-SA 4.0 (research, non-commercial)", "frame": "ETH3D scan frame, metres, z up",
        "roi": {"min": [-3.0, 1.0, gz - 0.8], "max": [12.0, 12.0, gz + 4.5]}, "ground_z_hint": gz,
        "meter": {"pos": [*meter.round(3).tolist(), gz + 1.5], "plus_minus_m": 0.05, "real": False,
                  "note": "INVENTED: on the pillar between doors 3 and 4, 1.5 m up"},
        "objects": objs,
        "ground_types": [{"type": "concrete", "plan_polygon": [[-3, 1], [12, 1], [12, 12], [-3, 12]],
                          "provenance": "photos: asphalt/concrete yard"}],
        "keyframes": [],
        "reference_chain": {"walls": walls, "ends": {
            "left": {"kind": "unexplored", "note": "the glass facade continues past the ROI"},
            "right": {"kind": "unexplored", "note": "the door wall turns past its east end"}},
            "note": "w1 glass facade face, gap (the cable alcove), w2 door-wall pillar faces"},
    }


def delivery():
    gz = -4.959
    xw, yw = 5.92, -5.06
    objs = [
        {"id": "sliding_door", "type": "garage_door", "real": True,
         "plan_polygon": [[5.80, 5.10], [6.05, 5.10], [6.05, -3.30], [5.80, -3.30]], "z_range": [gz, gz + 4.1],
         "plus_minus_m": 0.05, "provenance": "laser: panelled face x=5.92 over y -3.3..5.1, 4.1 m tall; photos: large sliding door with striped jambs"},
        {"id": "mesh_cage", "type": "fence", "real": True,
         "plan_polygon": [[-0.5, -5.05], [-0.5, -4.90], [-7.0, -4.90], [-7.0, -5.05]], "z_range": [gz, gz + 2.3],
         "plus_minus_m": 0.05, "provenance": "laser: sparse mesh returns 0.08 m in front of the back wall; photos: wire-mesh storage cage with gates"},
        {"id": "ac_synthetic", "type": "ac", "synthetic": True, "real": False,
         "plan_polygon": [[4.2, -5.0], [5.0, -5.0], [5.0, -4.2], [4.2, -4.2]], "z_range": [gz, gz + 0.8],
         "plus_minus_m": 0.0, "provenance": "SYNTHETIC: 0.8 x 0.8 x 0.8 m condenser on the floor near the corner, inserted as occupied voxels"},
    ]
    meter = np.array([xw + 0.05, -4.2])
    walls = chain_with_s([("w1", [xw, 5.1], [xw, yw]), ("w2", [xw, yw], [-12.5, yw])], meter)
    return {
        "id": "eth3d-delivery-garage", "real": True,
        "source": "ETH3D delivery_area (dslr_scan_eval laser scans); labels hand-placed from laser profiles and DSLR photos; meter invented; AC synthetic.",
        "license": "ETH3D: CC BY-NC-SA 4.0 (research, non-commercial)", "frame": "ETH3D scan frame, metres, z up",
        "roi": {"min": [-13.0, -7.0, gz - 0.8], "max": [7.5, 8.0, gz + 4.5]}, "ground_z_hint": gz,
        "meter": {"pos": [*meter.round(3).tolist(), gz + 1.5], "plus_minus_m": 0.05, "real": False,
                  "note": "INVENTED: on the jamb south of the sliding door, 1.5 m up"},
        "objects": objs,
        "ground_types": [{"type": "concrete", "plan_polygon": [[-13, -7], [7.5, -7], [7.5, 8], [-13, 8]],
                          "provenance": "photos: concrete garage floor"}],
        "keyframes": [],
        "reference_chain": {"walls": walls, "ends": {
            "left": {"kind": "unexplored", "note": "the door wall continues north"},
            "right": {"kind": "unexplored", "note": "the back wall continues west past the ROI"}},
            "note": "w1 east wall (sliding door + jamb), w2 south back wall (cages, clutter in front)"},
    }


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for sc in (electro(), delivery()):
    (out / f"{sc['id']}.json").write_text(json.dumps(sc, indent=1))
    print(sc["id"], "meter", sc["meter"]["pos"], [(w["id"], round(w["s0_ft"], 2), round(w["length_ft"], 2)) for w in sc["reference_chain"]["walls"]])
