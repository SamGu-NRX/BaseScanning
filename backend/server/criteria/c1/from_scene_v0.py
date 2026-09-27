"""scene v0 (metres) -> the dict ``engine3d.load_scene`` reads (feet).

Ground bands come from ``coverage.sensitivity`` for the requested ground rule, matching
``hs11._bands``, not always from ``coverage.bands``.
"""

from __future__ import annotations

import logging

import numpy as np
from shapely import Polygon, unary_union
from shapely.geometry import mapping

log = logging.getLogger("server.criteria")

FT = 1 / 0.3048
LOCATION_UNSURE_M = 9.99

CLASS = {
    "window": "window",
    "door": "door",
    "garage_door": "garage_door",
    "basement_well": "door",
    "gate": "door",
    "gas_meter": "gas_meter",
    "gas_regulator": "gas_meter",
    "gas_pipe": "gas_meter",
    "ac_unit": "ac",
    "pool": "pool",
    "hot_tub": "pool",
    "wall_box": "elec_box",
    "panel": "elec_box",
    "disconnect": "elec_box",
    "meter_can": "elec_box",
    "vent": "vent",
    "downspout": "downspout",
    "hose_bib": "downspout",
}
OVERHEAD = {"eave", "porch_roof"}
MOVABLE_DEFAULT = {"bin", "shrub", "tree"}
OBSTACLE_LABELS = {"water_heater", "deck", "stairs"}
MATERIAL = {
    "concrete": "concrete",
    "gravel": "gravel",
    "lawn": "lawn",
    "dirt": "dirt",
    "mulch": "mulch",
    "pavers": "concrete",
    "walkway": "concrete",
    "drive": "drive",
    "pool": "pool",
    "water": "pool",
    "deck": "deck",
}
# B/C treat bins, shrubs and trees as movable unless attrs say otherwise. A/S do not.
MOVABLE_PROFILES = {"B", "C", "public"}
# Older ETH3D scenes encode the ground rule as max_incidence_deg and omit rule_id.
GROUND_RULE_BY_DEG = {75.0: "angle75", 80.0: "angle80", 88.0: "footprint3in"}


def ground_rule_id(entry: dict) -> str | None:
    rid = entry.get("rule_id")
    if isinstance(rid, str) and rid:
        return rid
    deg = entry.get("max_incidence_deg")
    if deg is None:
        return None
    return GROUND_RULE_BY_DEG.get(round(float(deg), 1))


def stamp_ground_rule_ids(scene: dict) -> None:
    """Fill missing rule_id from max_incidence_deg so the eval adapter can select bands."""
    for entry in (scene.get("coverage") or {}).get("sensitivity") or []:
        if entry.get("surface") != "ground" or entry.get("rule_id"):
            continue
        rid = ground_rule_id(entry)
        if rid:
            entry["rule_id"] = rid


def _bands(scene: dict, ground_rule: str) -> list[dict]:
    bands = list(scene.get("coverage", {}).get("bands") or [])
    alt = next(
        (
            x
            for x in scene.get("coverage", {}).get("sensitivity") or []
            if x.get("surface") == "ground" and ground_rule_id(x) == ground_rule
        ),
        None,
    )
    sens = [x for x in (scene.get("coverage", {}).get("sensitivity") or []) if x.get("surface") == "ground"]
    if alt is None:
        # A sensitivity table that omits the requested rule is an error, not a silent fallback.
        if sens:
            raise KeyError(ground_rule)
        if ground_rule and any(b.get("band") == "ground" for b in bands):
            return bands
        raise KeyError(ground_rule)
    return [b for b in bands if b.get("band") != "ground"] + list(alt.get("bands") or [])


def convert(scene: dict, ground_rule: str = "angle80", profile_id: str = "C") -> dict:
    walls, frames = [], {}
    for w in scene["walls"]:
        a, b = (np.array(p, float) * FT for p in w["baseline_m"])
        al = b - a
        norm = float(np.linalg.norm(al))
        if norm < 1e-9:
            al = np.array([1.0, 0.0])
            norm = 1.0
        al = al / norm
        out = np.array([-al[1], al[0]])
        s0 = (w.get("s_range_m") or [0.0])[0] * FT
        frames[w["id"]] = (a, al, out, s0, norm * FT)
        walls.append(
            {
                "id": w["id"],
                "a": a.round(4).tolist(),
                "b": b.round(4).tolist(),
                "outward": out.round(5).tolist(),
                "top_y": w["height_m"] * FT,
                "plus_minus_ft": w["plus_minus_m"] * FT,
                "real": w.get("evidence") == "observed",
            }
        )
    ch = scene["chain"]
    joints = [
        {
            "walls": [c["left_wall"], c["right_wall"]],
            "ends": ["b", "a"],
            "kind": "outside" if c.get("turn_deg", 0) > 0 else "inside",
            "angle_deg": abs(c.get("turn_deg", 90)),
            "gap_ft": 0.0,
        }
        for c in ch.get("corners", [])
    ]
    joined = {(j["walls"][i], j["ends"][i]) for j in joints for i in (0, 1)}
    free = []
    chain_ids = ch["wall_ids"]
    for w in walls:
        for e in ("a", "b"):
            if (w["id"], e) in joined:
                continue
            kind = "unexplored"
            if w["id"] == chain_ids[0] and e == "a":
                kind = ch["ends"]["left"]["kind"]
            if w["id"] == chain_ids[-1] and e == "b":
                kind = ch["ends"]["right"]["kind"]
            free.append({"wall": w["id"], "end": e, "kind": kind})

    def wpt(wid, u, o=0.0):
        a, al, n, _, _ = frames[wid]
        return (a + al * u + n * o).round(4).tolist()

    objects, obstacles, overheads, meter = [], [], [], None
    review_only, location_unsure, inferred_ids = [], [], []
    movable_default = profile_id in MOVABLE_PROFILES
    anchor = scene.get("frame", {}).get("anchor", {}).get("element_id")
    for o in scene["objects"]:
        g = o["geometry"]
        cls = o["class"]
        if cls == "meter" or o["id"] == anchor:
            p = np.array(g.get("pos_m") or g.get("center_m"), float) * FT
            meter = {
                "pos": p.round(4).tolist(),
                "wall_id": o.get("wall_id") or g.get("wall_id"),
                "plus_minus_ft": o["plus_minus_m"] * FT,
                "real": o.get("evidence") == "observed",
                "note": o.get("label", ""),
            }
            continue
        if o.get("evidence") == "generated":
            continue
        if o.get("evidence") == "inferred":
            inferred_ids.append(o["id"])
        attrs = dict(o.get("attrs") or {})
        if cls == "basement_well":
            attrs["well"] = True
        if cls == "gate":
            attrs["operable"] = True
        unsure = float(o.get("plus_minus_m", 0)) >= LOCATION_UNSURE_M
        low_conf = float(o.get("confidence", 1)) < 0.75
        if unsure:
            location_unsure.append(o["id"])
        if low_conf:
            review_only.append(o["id"])
        label = (o.get("label") or "").lower().replace(" ", "_")
        if cls in OVERHEAD or cls == "overhang" or label in {"eave", "porch_roof", "overhang"}:
            overheads.append(_overhead(o, frames, wpt))
            continue
        if cls in MOVABLE_DEFAULT or (cls == "other" and label in MOVABLE_DEFAULT):
            movable = attrs.get("movable", movable_default)
            if movable:
                continue
            obstacles.append(_obstacle(o, frames))
            continue
        if cls in {"stairs", "deck"} or (cls == "other" and label in OBSTACLE_LABELS):
            obstacles.append(_obstacle(o, frames))
            continue
        if cls in {"fence", "driveway"}:
            continue
        if cls not in CLASS and not (cls == "other" and label in OBSTACLE_LABELS):
            log.info("unmapped object class %s id %s", cls, o["id"])
            continue
        rec = {
            "id": o["id"],
            "type": CLASS[cls],
            "plus_minus_ft": o["plus_minus_m"] * FT,
            "attrs": attrs,
            "real": o.get("evidence") == "observed",
            "note": o.get("label", ""),
        }
        _fill_geom(rec, g, frames, wpt)
        objects.append(rec)

    if meter is None:
        raise KeyError("meter")

    allp = np.array([p for w in walls for p in (w["a"], w["b"])])
    lo, hi = allp.min(0) - 20, allp.max(0) + 20
    res = 0.5
    nx = int((hi[0] - lo[0]) / res) + 1
    nz = int((hi[1] - lo[1]) / res) + 1
    grid, grid_missing = _ground_height(scene, lo, hi, res, nx, nz)
    patches = []
    for r in scene["ground"].get("regions", []):
        m = MATERIAL.get(r["material"])
        if m and r.get("evidence", "observed") != "generated":
            patches.append(
                {
                    "id": r["id"],
                    "type": m,
                    "footprint": (np.array(r["polygon_m"]) * FT).round(4).tolist(),
                    "plus_minus_ft": r.get("plus_minus_m", 0.1) * FT,
                }
            )

    cw = {w["id"]: [] for w in walls}
    gpolys, opolys = [], []
    try:
        bands = _bands(scene, ground_rule)
    except KeyError:
        bands = []
        missing_ground = True
    else:
        missing_ground = False
    for b in bands:
        wid = b.get("wall_id")
        if wid not in frames:
            continue
        _, _, _, s0, _ = frames[wid]
        u0, u1 = (s * FT - s0 for s in b["s_m"])
        if b["band"] == "wall":
            cw[wid].append([min(u0, u1), max(u0, u1)])
        elif b["band"] == "ground":
            out_m = b.get("out_m", 1.0)
            gpolys.append(Polygon([wpt(wid, u0), wpt(wid, u1), wpt(wid, u1, out_m * FT), wpt(wid, u0, out_m * FT)]))
        elif b["band"] == "overhead":
            opolys.append(Polygon([wpt(wid, u0), wpt(wid, u1), wpt(wid, u1, 3.0), wpt(wid, u0, 3.0)]))
    facing_obs = []
    for f in scene.get("facing", []):
        if f.get("lower_bound") or f["wall_id"] not in frames:
            continue
        _, _, _, s0, _ = frames[f["wall_id"]]
        u0, u1 = (s * FT - s0 for s in f["s_m"])
        dep = f["depth_m"] * FT
        facing_obs.append(
            {
                "id": f"facing_{f['wall_id']}_{len(facing_obs)}",
                "type": "obstacle",
                "footprint": [
                    wpt(f["wall_id"], u0, dep),
                    wpt(f["wall_id"], u1, dep),
                    wpt(f["wall_id"], u1, dep + 0.5),
                    wpt(f["wall_id"], u0, dep + 0.5),
                ],
                "y0": 1.0,
                "y1": 6.0,
                "plus_minus_ft": f["plus_minus_m"] * FT,
                "note": f.get("object_id") or "facing obstacle",
            }
        )
    for i, o in enumerate(scene.get("overheads", [])):
        if o["wall_id"] not in frames:
            continue
        _, _, _, s0, _ = frames[o["wall_id"]]
        u0, u1 = (s * FT - s0 for s in o["s_m"])
        overheads.append(
            {
                "id": o.get("object_id") or f"ovh{i}",
                "type": "overhead",
                "footprint": [
                    wpt(o["wall_id"], u0),
                    wpt(o["wall_id"], u1),
                    wpt(o["wall_id"], u1, 3.0),
                    wpt(o["wall_id"], u0, 3.0),
                ],
                "y0": o["clearance_m"] * FT,
                "y1": o["clearance_m"] * FT + 0.5,
                "plus_minus_ft": o["plus_minus_m"] * FT,
            }
        )
        opolys.append(Polygon(overheads[-1]["footprint"]))
    interior = []
    for w in walls:
        a, al, _, _, length_ft = frames[w["id"]]
        span = float(np.linalg.norm(np.array(w["b"]) - np.array(w["a"])))
        # Past the ends as well: a limit corner's inside cap is building, not unseen ground.
        interior.append(
            [wpt(w["id"], -30, 0), wpt(w["id"], span + 30, 0), wpt(w["id"], span + 30, -60), wpt(w["id"], -30, -60)]
        )
        del a, al, length_ft
    return {
        "format": "c1-scene3d/0",
        "id": scene["scene_id"].replace("/", "__"),
        "units": "ft",
        "provenance": {
            "from": "base-scene/v0",
            "scene_id": scene["scene_id"],
            "data": scene["provenance"]["data"],
            "producer": scene["provenance"]["producer"]["path"],
        },
        "meter": meter,
        "walls": walls,
        "joints": joints,
        "free_ends": free,
        "objects": objects,
        "obstacles": obstacles + facing_obs,
        "overheads": overheads,
        "ground_patches": patches,
        "ground": {"grid": grid},
        "interior": interior,
        "coverage": {
            "wall": cw,
            "ground": mapping(unary_union(gpolys)) if gpolys else None,
            "overhead": mapping(unary_union(opolys)) if opolys else None,
        },
        "meta": {
            "converted_from": scene["scene_id"],
            "ground_rule": ground_rule,
            "ground_rule_missing": missing_ground,
            "ground_grid_missing": grid_missing,
            "review_only_ids": review_only,
            "location_unsure_ids": location_unsure,
            "inferred_ids": inferred_ids,
            "profile_id": profile_id,
        },
    }


def _ground_height(scene: dict, lo, hi, res: float, nx: int, nz: int) -> tuple[dict, bool]:
    """Sample the ground plane into feet. A missing surface is NaN, not a fake flat zero."""
    surface = (scene.get("ground") or {}).get("surface") or {}
    rows: list[list[float | None]] = [[None] * nx for _ in range(nz)]
    missing = True
    normal = surface.get("normal")
    if surface.get("kind") == "plane" and normal and len(normal) == 3 and abs(float(normal[1])) > 1e-8:
        missing = False
        n0, n1, n2 = (float(v) for v in normal)
        offset = float(surface.get("offset_m") or 0.0)
        for iz in range(nz):
            z_m = (lo[1] + iz * res) / FT
            row: list[float | None] = []
            for ix in range(nx):
                x_m = (lo[0] + ix * res) / FT
                y_m = (offset - n0 * x_m - n2 * z_m) / n1
                row.append(round(y_m * FT, 4))
            rows[iz] = row
    return {"x0": float(lo[0]), "z0": float(lo[1]), "res": res, "h": rows}, missing


def _fill_geom(rec, g, frames, wpt):
    if g["kind"] == "wall_rect":
        _, _, _, s0, _ = frames[g["wall_id"]]
        u0, u1 = (s * FT - s0 for s in g["s_m"])
        lo, hi = min(u0, u1), max(u0, u1)
        rec.update(
            {
                "wall_id": g["wall_id"],
                "span_u": [lo, hi],
                "footprint": [wpt(g["wall_id"], lo), wpt(g["wall_id"], hi)],
                "y0": g["bottom_m"] * FT,
                "y1": g["top_m"] * FT,
            }
        )
    elif g["kind"] == "footprint":
        rec.update(
            {
                "footprint": (np.array(g["polygon_m"]) * FT).round(4).tolist(),
                "y0": g["bottom_m"] * FT,
                "y1": g["top_m"] * FT,
            }
        )
    elif g["kind"] == "box":
        c = np.array(g["center_m"]) * FT
        sw, sh, sd = (x * FT for x in g["size_m"])
        t = np.radians(g.get("yaw_deg", 0.0))
        rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        pts = [
            (c[[0, 2]] + rot @ np.array([dx, dz])).round(4).tolist()
            for dx, dz in ((-sw / 2, -sd / 2), (sw / 2, -sd / 2), (sw / 2, sd / 2), (-sw / 2, sd / 2))
        ]
        rec.update({"footprint": pts, "y0": c[1] - sh / 2, "y1": c[1] + sh / 2})
    else:
        p = np.array(g["pos_m"]) * FT
        rec.update({"footprint": [p[[0, 2]].round(4).tolist()], "y0": p[1], "y1": p[1]})


def _obstacle(o, frames):
    rec = {
        "id": o["id"],
        "type": "obstacle",
        "plus_minus_ft": o["plus_minus_m"] * FT,
        "attrs": o.get("attrs") or {},
        "real": o.get("evidence") == "observed",
        "note": o.get("label", ""),
    }
    _fill_geom(rec, o["geometry"], frames, _wpt_from(frames))
    return rec


def _wpt_from(frames):
    def wpt(wid, u, o=0.0):
        a, al, n, _, _ = frames[wid]
        return (a + al * u + n * o).round(4).tolist()

    return wpt


def _overhead(o, frames, wpt):
    g = o["geometry"]
    if g["kind"] == "wall_rect" and g["wall_id"] in frames:
        _, _, _, s0, _ = frames[g["wall_id"]]
        u0, u1 = (s * FT - s0 for s in g["s_m"])
        fp = [wpt(g["wall_id"], u0), wpt(g["wall_id"], u1), wpt(g["wall_id"], u1, 3.0), wpt(g["wall_id"], u0, 3.0)]
        y0 = g["bottom_m"] * FT
    else:
        fp = (np.array(g.get("polygon_m") or [[0, 0], [1, 0], [1, 1]]) * FT).round(4).tolist()
        y0 = float(g.get("bottom_m", g.get("clearance_m", 2.5))) * FT
    return {
        "id": o["id"],
        "type": "overhead",
        "footprint": fp,
        "y0": y0,
        "y1": y0 + 0.5,
        "plus_minus_ft": o["plus_minus_m"] * FT,
    }
