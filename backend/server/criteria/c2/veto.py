"""Join C2 spots onto C1 runs. C2 never turns a fail into a pass."""

from __future__ import annotations

from server.criteria.c1.to_verdict import s_of
from server.criteria.lattice import c2_veto

NAME = {
    "wall_backing": "wall_backing",
    "ground": "ground_surface",
    "facing": "facing_gap",
    "headroom": "headroom",
    "route": "route_length",
    "gas_clearance": "gas_clearance",
    "ac_clearance": "ac_clearance",
    "drive_clearance": "drive_clearance",
    "pool_clearance": "pool_clearance",
    "opening_clearance": "opening_clearance",
    "meter_working_space": "meter_working_space",
    "wall_equipment": "wall_equipment_above",
    "body_clear": "footprint_clear",
}
VETO_IDS = {"wall_backing", "footprint_clear", "ground_surface", "headroom", "facing_gap"}


def join(runs: list[dict], spots: list[dict], sm, max_dist_m: float = 0.35, width_ft: float = 0.0) -> dict:
    vetoes = 0
    positions = 0
    agreed_positions = 0
    for run in runs:
        spot = _nearest(run, spots, sm, max_dist_m, width_ft)
        if spot is None:
            continue
        before = run.get("outcome")
        positions += 1
        by_id = {}
        for ch in spot.get("checks", []):
            cid = NAME.get(ch["id"], ch["id"])
            by_id[cid] = ch["outcome"]
            if ch.get("unknown_count"):
                by_id.setdefault("footprint_clear", "unsure")
        for cid, c2 in by_id.items():
            if cid not in VETO_IDS and cid not in {c.id for c in run["cand"].checks}:
                continue
            c1 = "pass"
            if cid in run.get("failing", []):
                c1 = "fail"
            elif cid in run.get("unsure", []):
                c1 = "unsure"
            # A C2 fail is not a fail of ours. It can only pull a passing candidate to unsure.
            nxt = c2_veto(c1, "unsure" if c2 == "fail" else c2)
            if c1 == "pass" and nxt == "unsure" and run.get("outcome") == "pass":
                vetoes += 1
                run["outcome"] = "unsure"
                run["unsure"] = sorted(set(run.get("unsure") or []) | {cid})
                run["failing"] = [x for x in run.get("failing") or [] if x != cid]
                run.setdefault("c2_notes", []).append(cid)
                for check in run["cand"].checks:
                    if check.id == cid and check.outcome == "pass":
                        check.outcome = "unsure"
                        check.cause = "rule_requires_review"
                        check.reason = (check.reason + "; " if check.reason else "") + "c2_veto"
        if run.get("outcome") == before:
            agreed_positions += 1
    return {
        "vetoes": vetoes,
        "agreement_with_c1": round(agreed_positions / positions, 3) if positions else 1.0,
    }


def _nearest(run, spots, sm, max_dist_m: float, width_ft: float = 0.0):
    # Match the battery centre, not the run's left edge. Half of profile C is about 0.39 m.
    u = run.get("spot_u", run["u0"]) + width_ft / 2
    s = s_of(sm, run["wall"], u)
    ranked = []
    for spot in spots:
        if spot.get("wall_id") != run["wall"]:
            continue
        xy = spot.get("rear_mid_xy") or spot.get("center_xy")
        if not xy:
            continue
        ss = spot.get("s_m")
        dist = 0.0 if ss is None else abs(float(ss) - s)
        if dist <= max_dist_m:
            ranked.append((dist, spot))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    # A fail and a pass often share a station. Joining the pass when one is in range
    # keeps a C1 pass that C2 also accepted.
    passes = [spot for _dist, spot in ranked if spot.get("outcome") == "pass"]
    if passes:
        return passes[0]
    return ranked[0][1]
