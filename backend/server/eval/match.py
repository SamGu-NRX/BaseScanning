"""Match a reconstruction's elements to ground truth in the shared meter-anchored frame.

Both scenes put the meter at the origin with the meter wall along +x, so no registration is
needed: matching is by geometry in that frame. Recon ids are then renamed to GT ids so key
distances can be compared by id.
"""

from __future__ import annotations

import copy

import numpy as np
from scipy.optimize import linear_sum_assignment

from server.eval.chain import Chain, object_center

CLASS_GROUPS = {"door": "door", "garage_door": "door", "window": "window", "basement_well": "well"}
WALL_MAX_ANGLE_DEG = 20.0
WALL_MAX_OFFSET_M = 0.5
OBJ_GATE_M = 1.5
OBJ_GATE_REL = 0.05


def _group(cls: str) -> str:
    return CLASS_GROUPS.get(cls, cls)


def match_walls(gt: dict, rc: dict) -> dict[str, str]:
    """Chain walls: direction within 20 deg, s-ranges overlapping, baseline within 0.5 m."""
    g_ch, r_ch = Chain(gt), Chain(rc)
    pairs = []
    for rw in r_ch.walls:
        for gw in g_ch.walls:
            ang = np.degrees(np.arccos(np.clip(rw.along @ gw.along, -1, 1)))
            if ang > WALL_MAX_ANGLE_DEG:
                continue
            ov = min(rw.s1, gw.s1) - max(rw.s0, gw.s0)
            if ov <= 0.3 * min(rw.length, gw.length):
                continue
            ends = np.array([rw.p0, rw.p1])
            off = float(np.mean(np.abs((ends - gw.p0) @ gw.outward)))
            if off > WALL_MAX_OFFSET_M:
                continue
            pairs.append((-ov, off, rw.id, gw.id))
    out, used = {}, set()
    for _, _, r, g in sorted(pairs):
        if r not in out and g not in used:
            out[r] = g
            used.add(g)
    return out


def match_objects(gt: dict, rc: dict) -> dict[str, str]:
    """Meter by anchor; others by class group, Hungarian on centre distance, gate 1.5 m + 5 %."""
    g_ch, r_ch = Chain(gt), Chain(rc)
    g_anchor, r_anchor = gt["frame"]["anchor"]["element_id"], rc["frame"]["anchor"]["element_id"]
    out = {r_anchor: g_anchor}
    g_objs = [o for o in gt["objects"] if o["id"] != g_anchor]
    r_objs = [o for o in rc["objects"] if o["id"] != r_anchor]
    for grp in {_group(o["class"]) for o in g_objs} & {_group(o["class"]) for o in r_objs}:
        G = [o for o in g_objs if _group(o["class"]) == grp]
        R = [o for o in r_objs if _group(o["class"]) == grp]
        gc = np.array([object_center(o, g_ch) for o in G])
        rcn = np.array([object_center(o, r_ch) for o in R])
        D = np.linalg.norm(gc[:, None] - rcn[None], axis=2)
        gate = OBJ_GATE_M + OBJ_GATE_REL * np.linalg.norm(gc, axis=1)[:, None]
        cost = np.where(D <= gate, D, 1e6)
        gi, ri = linear_sum_assignment(cost)
        for a, b in zip(gi, ri):
            if cost[a, b] < 1e6:
                out[R[b]["id"]] = G[a]["id"]
    return out


def match_corners(gt: dict, rc: dict, walls: dict[str, str]) -> dict[str, str]:
    g_by_pair = {(c["left_wall"], c["right_wall"]): c["id"] for c in gt["chain"].get("corners", [])}
    out = {}
    for c in rc["chain"].get("corners", []):
        key = (walls.get(c["left_wall"]), walls.get(c["right_wall"]))
        if key in g_by_pair:
            out[c["id"]] = g_by_pair[key]
    return out


def match(gt: dict, rc: dict) -> dict:
    walls = match_walls(gt, rc)
    return {"walls": walls, "corners": match_corners(gt, rc, walls), "objects": match_objects(gt, rc)}


def renamed(rc: dict, m: dict) -> dict:
    """Copy of the recon scene with matched ids replaced by GT ids and unmatched ids prefixed
    `rc:` (so they can never collide with a GT id)."""
    s = copy.deepcopy(rc)
    wmap = {w["id"]: m["walls"].get(w["id"], "rc:" + w["id"]) for w in s["walls"]}
    omap = {o["id"]: m["objects"].get(o["id"], "rc:" + o["id"]) for o in s["objects"]}
    cmap = {c["id"]: m["corners"].get(c["id"], "rc:" + c["id"]) for c in s["chain"].get("corners", [])}
    for w in s["walls"]:
        w["id"] = wmap[w["id"]]
    s["chain"]["wall_ids"] = [wmap[w] for w in s["chain"]["wall_ids"]]
    for c in s["chain"].get("corners", []):
        c["id"], c["left_wall"], c["right_wall"] = cmap[c["id"]], wmap[c["left_wall"]], wmap[c["right_wall"]]
    for o in s["objects"]:
        o["id"] = omap[o["id"]]
        if "wall_id" in o:
            o["wall_id"] = wmap.get(o["wall_id"], o["wall_id"])
        if o["geometry"].get("wall_id"):
            o["geometry"]["wall_id"] = wmap.get(o["geometry"]["wall_id"], o["geometry"]["wall_id"])
    s["frame"]["anchor"]["element_id"] = omap[s["frame"]["anchor"]["element_id"]]
    for key in ("overheads", "facing"):
        for e in s.get(key, []):
            e["wall_id"] = wmap.get(e["wall_id"], e["wall_id"])
    for b in s["coverage"].get("bands", []):
        if "wall_id" in b:
            b["wall_id"] = wmap.get(b["wall_id"], b["wall_id"])
    return s
