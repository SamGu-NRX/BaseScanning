"""Key distances: the criteria-relevant measurements the geometry scorer compares.

Ids and meanings are the contract (docs/scene-and-verdict-format.md section 6.1). Each entry:
{value, pm (the producer's own bound), unit m | deg, category, feeds, extent_limited}.
Facing and overhead stations are chosen from the ground truth (`stations_from`), so the recon
is probed at the same s.
"""

from __future__ import annotations

import numpy as np

from server.eval.chain import OPENING_CLASSES, Chain, ground_y, object_center, object_s_span

FACING_MAX_M = 4.0
OVERHEAD_MAX_M = 3.0
STATION_STEP_M = 0.25

FEEDS = {
    "meter.height": "meter_working_space, route_length",
    "corner.s": "wall_backing, route_length",
    "corner.turn": "wall_backing",
    "wall.length": "wall_backing",
    "opening.edge_s": "opening_clearance, route_path",
    "opening.bottom": "opening_clearance (exemptions), route_path (detours)",
    "opening.top": "route_path (detours)",
    "opening.width": "opening_clearance",
    "opening.gap": "opening_clearance",
    "equipment.meter_dist": "gas/ac/pool clearance",
    "equipment.s": "wall_equipment_above, clearances",
    "facing.depth": "facing_gap",
    "overhead.clearance": "headroom",
    "ground.region_dist": "drive/pool clearance, ground_surface",
}


def _kd(value, pm, category, unit="m", extent_limited=False):
    return {"value": float(value), "pm": float(pm), "unit": unit, "category": category,
            "feeds": FEEDS[category], "extent_limited": bool(extent_limited)}


def _entry_at(entries, wall_id, s, key):
    for e in entries:
        if e["wall_id"] == wall_id and e["s_m"][0] - 1e-6 <= s <= e["s_m"][1] + 1e-6:
            return e
    return None


def stations_from(gt: dict) -> dict:
    """GT stations where facing depth < 4 m or overhead clearance < 3 m."""
    ch = Chain(gt)
    st = {"facing": [], "overhead": []}
    for w in ch.walls:
        for s in np.arange(w.s0 + STATION_STEP_M / 2, w.s1, STATION_STEP_M):
            f = _entry_at(gt.get("facing", []), w.id, s, "depth_m")
            if f and f["depth_m"] < FACING_MAX_M and not f.get("lower_bound"):
                st["facing"].append((w.id, round(float(s), 3)))
            o = _entry_at(gt.get("overheads", []), w.id, s, "clearance_m")
            if o and o["clearance_m"] < OVERHEAD_MAX_M:
                st["overhead"].append((w.id, round(float(s), 3)))
    return st


def key_distances(scene: dict, stations: dict) -> dict[str, dict]:
    ch = Chain(scene)
    kd: dict[str, dict] = {}
    anchor = scene["frame"]["anchor"]
    a_pm = float(anchor.get("plus_minus_m", 0.0))
    meter = next(o for o in scene["objects"] if o["id"] == anchor["element_id"])
    mc = object_center(meter, ch)
    kd["meter.height"] = _kd(mc[1] - ground_y(scene, mc[[0, 2]])[0], meter["plus_minus_m"] + scene["ground"]["plus_minus_m"], "meter.height")

    corners = scene["chain"].get("corners", [])
    walls = {w["id"]: w for w in scene["walls"]}
    for c in corners:
        s = c.get("s_m", ch.by_id[c["left_wall"]].s1)
        kd[f"corner.s[{c['id']}]"] = _kd(s, c.get("plus_minus_m", 0.0) + a_pm, "corner.s")
        if "turn_deg" in c:
            kd[f"corner.turn[{c['id']}]"] = _kd(c["turn_deg"], np.degrees(np.arctan2(2 * c.get("plus_minus_m", 0.0), 1.0)),
                                                 "corner.turn", unit="deg")
    cornered = {}
    for c in corners:
        cornered.setdefault(c["left_wall"], set()).add("right")
        cornered.setdefault(c["right_wall"], set()).add("left")
    for w in ch.walls:
        free = cornered.get(w.id, set()) != {"left", "right"}
        kd[f"wall.length[{w.id}]"] = _kd(w.length, 2 * walls[w.id]["plus_minus_m"], "wall.length", extent_limited=free)

    openings = sorted([o for o in scene["objects"] if o["class"] in OPENING_CLASSES and o["geometry"]["kind"] == "wall_rect"],
                      key=lambda o: o["geometry"]["s_m"][0])
    for o in openings:
        g, pm = o["geometry"], o["plus_minus_m"]
        kd[f"opening.edge_s[{o['id']}:left]"] = _kd(g["s_m"][0], pm, "opening.edge_s")
        kd[f"opening.edge_s[{o['id']}:right]"] = _kd(g["s_m"][1], pm, "opening.edge_s")
        kd[f"opening.bottom[{o['id']}]"] = _kd(g["bottom_m"], pm, "opening.bottom")
        kd[f"opening.top[{o['id']}]"] = _kd(g["top_m"], pm, "opening.top")
        kd[f"opening.width[{o['id']}]"] = _kd(g["s_m"][1] - g["s_m"][0], 2 * pm, "opening.width")
    for a, b in zip(openings, openings[1:]):
        kd[f"opening.gap[{a['id']}~{b['id']}]"] = _kd(b["geometry"]["s_m"][0] - a["geometry"]["s_m"][1],
                                                        a["plus_minus_m"] + b["plus_minus_m"], "opening.gap")

    for o in scene["objects"]:
        if o["id"] == meter["id"] or o["class"] in OPENING_CLASSES:
            continue
        c = object_center(o, ch)
        kd[f"equipment.meter_dist[{o['id']}]"] = _kd(np.linalg.norm(c - mc), o["plus_minus_m"] + meter["plus_minus_m"], "equipment.meter_dist")
        s0, s1 = object_s_span(o, ch)
        kd[f"equipment.s[{o['id']}]"] = _kd((s0 + s1) / 2, o["plus_minus_m"] + a_pm, "equipment.s")

    for wid, s in stations.get("facing", []):
        e = _entry_at(scene.get("facing", []), wid, s, "depth_m")
        if e is not None and not e.get("lower_bound"):
            kd[f"facing.depth[{wid}@{s:.2f}]"] = _kd(e["depth_m"], e["plus_minus_m"], "facing.depth")
    for wid, s in stations.get("overhead", []):
        e = _entry_at(scene.get("overheads", []), wid, s, "clearance_m")
        if e is not None:
            kd[f"overhead.clearance[{wid}@{s:.2f}]"] = _kd(e["clearance_m"], e["plus_minus_m"], "overhead.clearance")

    mw = ch.by_id.get(meter.get("wall_id")) or ch.nearest_wall(mc[[0, 2]])
    by_mat: dict[str, list] = {}
    for r in scene["ground"].get("regions", []):
        if r["evidence"] == "generated":
            continue
        P = np.asarray(r["polygon_m"], float)
        d = float(np.min(np.abs(mw.out_of(P))))
        by_mat.setdefault(r["material"], []).append((d, r.get("plus_minus_m", 0.0)))
    for mat, v in by_mat.items():
        d, pm = min(v)
        kd[f"ground.region_dist[{mat}]"] = _kd(d, pm, "ground.region_dist")
    return kd
