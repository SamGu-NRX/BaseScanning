"""Write C1 engine results as shared-contract verdicts (base-verdict/v0).

Coverage evidence comes from C1's own coverage (the scene's observed wall intervals, observed ground
and overhead polygons), graded with the contract's shares (observed >= 90 %, partial >= 50 %
seen, else unobserved) and tagged with the rule C1 actually used, so a reader can tell a C1-rule
"observed" from a contract-rule one. Chain s (meters, meter at 0, + to the right seen from outside)
is derived from the wall graph.

    python to_verdict.py SCENE.json PROFILE [--contract] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from shapely import Point

import engine3d as E
from run import prof

M = 0.3048
HERE = Path(__file__).resolve().parent
CAUSE = {"unobserved": "unobserved", "margin": "margin", "unknown_attribute": "unknown_attribute",
         "rule_requires_review": "rule_requires_review", None: "margin"}
WALL_CHECKS = {"wall_backing", "opening_clearance", "under_window", "wall_equipment_above", "route_path", "route_length"}
GROUND_CHECKS = {"ground_surface", "gas_clearance", "ac_clearance", "drive_clearance", "pool_clearance", "footprint_clear"}
VOLUME_CHECKS = {"facing_gap", "headroom"}
AT_MOST = {"route_length"}


def chain_s(sc: E.Scene3D) -> dict[str, tuple[float, int]]:
    """Wall id -> (s of its a end in ft, direction +1 when s grows a->b)."""
    mw = sc.walls[sc.meter_wall]
    out = {mw.id: (-sc.meter_u, 1)}
    todo = [mw.id]
    while todo:
        wid = todo.pop()
        s_a, sg = out[wid]
        L = sc.walls[wid].L
        for j in sc.joints:
            for (w1, e1, w2, e2) in ((j.w1, j.e1, j.w2, j.e2), (j.w2, j.e2, j.w1, j.e1)):
                if w1 != wid or w2 in out:
                    continue
                s_end = s_a + sg * (0.0 if e1 == "a" else L)
                sg2 = sg if (e1 == "b") == (e2 == "a") else -sg
                s2a = s_end if e2 == "a" else s_end - sg2 * sc.walls[w2].L
                out[w2] = (s2a, sg2)
                todo.append(w2)
    return out


def s_of(sm, wid, u):
    s_a, sg = sm.get(wid, (0.0, 1))
    return (s_a + sg * u) * M


class Cov:
    def __init__(self, sc: E.Scene3D, sm, rule: dict):
        self.sc, self.sm, self.rule = sc, sm, rule

    def _grade(self, share: float, region: dict, probes: int) -> dict:
        status = "observed" if share >= 0.9 else ("partial" if share >= 0.5 else "unobserved")
        return {"region": region, "status": status, "probes": probes, "observed_share": round(share, 3),
                "views_min": int(self.rule["min_views"]) if share >= 0.9 else 0,
                "views_median": float(self.rule["min_views"]) if share > 0 else 0.0,
                "best_incidence_deg": None, "rule": self.rule,
                "method": "C1 build_scene3d coverage (z-buffer depth test on the laser scan); view counts not recorded"}

    def wall(self, wid, u0, u1):
        us = np.arange(u0 + 0.125, u1, 0.25) if u1 > u0 else np.array([u0])
        iv = self.sc.observed_wall.get(wid, [])
        share = float(np.mean([any(a <= u <= b for a, b in iv) for u in us])) if len(us) else 0.0
        region = {"wall_id": wid, "s_m": sorted([s_of(self.sm, wid, u0), s_of(self.sm, wid, u1)]), "height_m": [0.0, 2.13]}
        return self._grade(share, region, len(us))

    def strip(self, w: E.Wall, u0, u1, o0, o1, geom, height=None):
        pts = [w.pt(u, o) for u in np.arange(u0 + 0.25, u1, 0.5) for o in np.arange(o0 + 0.25, o1, 0.5)]
        share = float(np.mean([geom.contains(Point(p)) for p in pts])) if pts else 0.0
        region = {"wall_id": w.id, "s_m": sorted([s_of(self.sm, w.id, u0), s_of(self.sm, w.id, u1)]),
                  "out_m": [o0 * M, o1 * M]}
        if height:
            region["height_m"] = height
        return self._grade(share, region, len(pts))


def criterion(c: E.Check, cand_id: str, cand: E.Candidate, sc, cov: Cov, profile: dict) -> dict:
    w = sc.walls[cand.wall]
    if c.id in ("route_path", "route_length") and cand.route.stretches:
        ev = [cov.wall(wid, min(a, b), max(a, b)) for wid, a, b in cand.route.stretches]
    elif c.id == "meter_working_space":
        ev = [cov.wall(sc.meter_wall, sc.meter_u - 1.25, sc.meter_u + 1.25)]
    elif c.id in WALL_CHECKS:
        ev = [cov.wall(w.id, cand.u0, cand.u1)]
    elif c.id in VOLUME_CHECKS:
        geom = sc.observed_overhead if c.id == "headroom" else sc.observed_ground
        ev = [cov.strip(w, cand.u0, cand.u1, 0.0, 3.0 if c.id == "headroom" else 5.0, geom, [0.0, 2.13])]
    else:
        reach = (c.threshold or 0.0) + 2.0
        ev = [cov.strip(w, cand.u0, cand.u1, 0.0, max(profile["battery"]["depth_ft"], reach), sc.observed_ground)]
    out = {"id": c.id, "scope": "candidate", "candidate_id": cand_id, "outcome": c.outcome,
           "reason": c.reason or f"{c.id} {c.outcome}" + (f" ({c.subject})" if c.subject else "")}
    if c.outcome == E.UNSURE:
        out["unsure_cause"] = CAUSE.get(c.cause, "margin")
    if c.measured is not None and np.isfinite(c.measured):
        out["measured"] = {"value_ft": round(float(c.measured), 4), "plus_minus_ft": round(float(c.pm or 0.0), 4),
                           "coverage": ev}
        if c.subject:
            out["measured"]["subject"] = str(c.subject)
    else:
        out["measured"] = None
        out["fact"] = {"value": "nothing found in the checked zone" if c.outcome == E.PASS else c.outcome,
                       "clean": c.outcome != E.UNSURE, "coverage": ev}
    if c.threshold is not None:
        out["threshold"] = {"value_ft": float(c.threshold), "comparison": "at_most" if c.id in AT_MOST else "at_least",
                            "source": profile.get("source", profile["id"])}
        if c.measured is not None and np.isfinite(c.measured):
            m = (c.threshold - c.measured) if c.id in AT_MOST else (c.measured - c.threshold)
            out["margin_ft"] = round(float(m), 4)
            if c.pm:
                out["margin_over_uncertainty"] = round(float(m / c.pm), 3)
    else:
        out["threshold"] = None
    return out


SITES = {"facade-pr13cov": "eth3d-facade-pr13content", "facade-contract": "eth3d-facade", "facade-M1": "eth3d-facade",
         "facade": "eth3d-facade-alt-meter", "electro": "eth3d-electro", "delivery_area": "eth3d-delivery_area"}
PLACEMENT = {"pass": "pass", "manual_review": "unsure", "reject": "fail"}
PROFILE_OUT = {"strictest": "S"}


def build(scene_path: Path, pname: str, contract: bool) -> dict:
    pname_out = PROFILE_OUT.get(pname, pname)
    raw = json.loads(scene_path.read_text())
    sc = E.load_scene(raw)
    p = prof(pname, contract)
    t = time.time()
    r = E.decide(sc, p)
    rt = time.time() - t
    sm = chain_s(sc)
    cr = raw.get("provenance", {}).get("coverage_rule") or "c1"
    rule = ({"min_views": 2, "max_incidence_deg": 75.0, "observed_share": 0.9} if cr == "contract"
            else {"min_views": 1, "max_incidence_deg": 75.0, "observed_share": 0.9})
    cov = Cov(sc, sm, rule)
    cands = r["_cands"]
    best = None
    if r["best"]:
        best = next(c for c in cands if c.wall == r["best"]["wall"] and abs(c.u0 - r["best"]["u"][0]) < 1e-3)
    criteria, views = [], []
    if best is not None:
        for c in best.checks:
            criteria.append(criterion(c, "best", best, sc, cov, p))
        for i, c in enumerate(x for x in best.checks if x.outcome == E.UNSURE and x.cause == "unobserved"):
            band = "wall" if c.id in WALL_CHECKS else ("overhead" if c.id == "headroom" else "ground")
            views.append({"id": f"v{i}", "kind": "band", "band": band, "wall_id": best.wall,
                          "s_m": sorted([s_of(sm, best.wall, best.u0), s_of(sm, best.wall, best.u1)]),
                          "criteria": [c.id], "why": c.reason or f"{c.id}: deciding space not observed",
                          "target": f"head-on photo of wall {best.wall} {band} at the battery spot"})
    for e in r["open_ends_in_reach"]:
        wid, end = e.split(".")
        views.append({"id": f"end_{wid}_{end}", "kind": "past_end", "wall_id": wid,
                      "criteria": ["placement"], "why": "unexplored wall end within cable reach blocks a reject"})
    if r["decision"] in ("pass", "reject"):
        views = []
    runs = [{"id": f"run{i}", "wall_id": x["wall"], "start_s_m": sorted([s_of(sm, x["wall"], x["u"][0]), s_of(sm, x["wall"], x["u"][1])]),
             "outcome": x["outcome"], "failing": x["failing"], "unsure": x["unsure"]} for i, x in enumerate(r["runs"])]
    reasons = [f"{r['stats']['pass']} pass / {r['stats']['unsure']} unsure / {r['stats']['fail']} fail of {r['stats']['candidates']} candidates"]
    if best is not None:
        reasons.append(f"best {best.wall} u {best.u0:.2f}-{best.u1:.2f} ft ({best.outcome}); route {best.route.length:.2f} ft")
    if r["open_ends_in_reach"]:
        reasons.append("unexplored ends in reach: " + ", ".join(r["open_ends_in_reach"]))
    site_id = raw.get("provenance", {}).get("scene_id", raw["id"]).split("/")[0]
    for pre, site in SITES.items():
        if raw["id"].startswith(pre):
            site_id = site
            break
    place_ev = []
    for c in criteria:
        for e in (c["measured"] or c.get("fact") or {}).get("coverage", []):
            if e not in place_ev:
                place_ev.append(e)
    if not place_ev:
        place_ev = [cov.wall(sc.meter_wall, 0.0, sc.walls[sc.meter_wall].L)]
    criteria.insert(0, {
        "id": "placement", "scope": "site", "outcome": PLACEMENT[r["decision"]],
        **({"unsure_cause": "unobserved" if views else "rule_requires_review"} if r["decision"] == "manual_review" else {}),
        "reason": "; ".join(reasons), "measured": None, "threshold": None,
        "fact": {"value": pname_out, "clean": r["decision"] != "manual_review", "raw": r["decision"], "coverage": place_ev}})
    return {
        "format": "base-verdict/v0",
        "verdict_id": f"C1-rules-3d/{raw['id']}/{pname_out}{'' if contract else '-c1rule'}",
        "scene": {"scene_id": raw.get("provenance", {}).get("scene_id", raw["id"]), "site": site_id,
                  "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
                  "producer_path": raw.get("provenance", {}).get("producer", "C1-build_scene3d"),
                  "data": raw.get("provenance", {}).get("data", "real") if raw.get("provenance", {}).get("data") in ("real", "hybrid", "synthetic") else "real"},
        "evaluator": {"path": "C1-rules-3d", "version": "0", "rules_profile": pname_out,
                      "rules_sha256": hashlib.sha256((HERE / "profiles.yaml").read_bytes()).hexdigest(),
                      "auto_approve": bool(p["policy"]["auto_approve"]), "runtime_s": round(rt, 2), "cost_usd": 0.0},
        "site": {"decision": r["decision"], "reasons": reasons, "chosen_candidate": "best" if best is not None else None},
        "criteria": criteria,
        "candidates": runs,
        "views_needed": views,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=Path)
    ap.add_argument("profiles", nargs="+")
    ap.add_argument("--c1-rule", action="store_true", help="C1's native evidence rule (not contract-valid)")
    ap.add_argument("--out", type=Path, default=HERE / "outputs" / "verdicts")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for pn in a.profiles:
        v = build(a.scene, pn, not a.c1_rule)
        path = a.out / (v["verdict_id"].split("/", 1)[1].replace("/", "__") + ".json")
        path.write_text(json.dumps(v, indent=1))
        print(path)


if __name__ == "__main__":
    main()
