"""Turn a C2 sweep into a base-verdict/v0 file (docs/scene-and-verdict-format.md) for one scene v0.

    python -m c2vol.verdict SWEEP.json SCENE_V0.json RULES.yaml PROFILE OUT.json

Candidates are the sweep's spots on the scene's wall chain (within 0.35 m of it), grouped into runs of
equal per-criterion outcomes. Every measurement carries contract section 3.1 coverage evidence from
evalharness.coverage.query_region on the scene's own cameras and geometry; a C2 pass or fail whose
evidence is not all `observed` becomes unsure (grazing / unobserved / generated_only). C2's own
volumetric unsure (unknown voxels in an envelope) stays unsure. Spots off the chain (other buildings,
frontier spots facing unobserved space) cannot be indexed into the scene's s; they only count toward
the site decision through `unknown_route_cells_in_reach`.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import yaml

HARNESS = Path(__file__).resolve().parents[3] / "eval-harness"
sys.path.insert(0, str(HARNESS))
from evalharness.coverage import CoverageQuery, query_region  # noqa: E402
from evalharness.sceneio import validate  # noqa: E402

FT = 0.3048
WALL_H = 2.13
CHECK_TO_CRITERION = {
    "ground": "ground_surface", "facing": "facing_gap", "headroom": "headroom",
    "opening_clearance": "opening_clearance", "gas_clearance": "gas_clearance", "ac_clearance": "ac_clearance",
    "drive_clearance": "drive_clearance", "pool_clearance": "pool_clearance", "fence_clearance": "fence_clearance",
    "wall_equipment": "wall_equipment_above", "meter_working_space": "meter_working_space",
}
RULE_KEY = {"gas_clearance": "gas_ft", "ac_clearance": "ac_ft", "drive_clearance": "drive_ft",
            "pool_clearance": "pool_ft", "fence_clearance": "fence_ft", "opening_clearance": "opening_ft"}
RANK = {"pass": 0, "unsure": 1, "fail": 2}


def c5(margin, err):
    return "pass" if margin > err else ("fail" if margin < -err else "unsure")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep", type=Path)
    ap.add_argument("scene", type=Path)
    ap.add_argument("rules", type=Path)
    ap.add_argument("profile")
    ap.add_argument("out", type=Path)
    ap.add_argument("--max-dist", type=float, default=0.35)
    a = ap.parse_args()
    t0 = time.time()
    sw = json.loads(a.sweep.read_text())
    scene = json.loads(a.scene.read_text())
    rules_text = a.rules.read_text()
    R = yaml.safe_load(rules_text)
    base = a.scene.parent
    q = CoverageQuery.from_scene(scene, base)
    walls = {w["id"]: w for w in scene["walls"]}
    W = R["battery"]["width_ft"] * FT
    D = R["battery"]["depth_ft"] * FT
    cache = {}

    def ev(region):
        key = json.dumps(region, sort_keys=True)
        if key not in cache:
            e = query_region(scene, base, region, q)
            cache[key] = {k: e[k] for k in ("region", "status", "observed_share", "seen_share", "views_min",
                                            "views_median", "best_incidence_deg", "worst_best_incidence_deg",
                                            "rule", "probes", "method") if k in e}
        return cache[key]

    def clip(wid, s0, s1):
        lo, hi = walls[wid]["s_range_m"]
        return [round(max(s0, lo), 3), round(min(s1, hi), 3)]

    def span_regions(s0, s1, **extra):
        """one region per chain wall the span touches"""
        out = []
        for wid in scene["chain"]["wall_ids"]:
            lo, hi = walls[wid]["s_range_m"]
            if s1 > lo and s0 < hi:
                out.append({"wall_id": wid, "s_m": clip(wid, s0, s1), **extra})
        return out

    def regions_for(cid, wid, a0, a1, s_meter=0.0, measured_m=None):
        b0, b1 = a0, a1 + W
        if cid in ("wall_backing", "wall_equipment_above"):
            return [{"wall_id": wid, "s_m": clip(wid, b0, b1), "height_m": [0.0, WALL_H]}]
        if cid == "ground_surface":
            return [{"wall_id": wid, "s_m": clip(wid, b0, b1), "out_m": [0.0, round(D, 3)]}]
        if cid == "facing_gap":
            far = R["facing"].get("from_wall_ft", 0) * FT or (D + R["facing"].get("from_front_ft", 3) * FT)
            return [{"wall_id": wid, "s_m": clip(wid, b0, b1), "out_m": [round(D, 3), round(far, 3)], "height_m": [0.1, 1.98]}]
        if cid == "headroom":
            return [{"wall_id": wid, "s_m": clip(wid, b0, b1), "out_m": [0.1, round(D, 3)],
                     "height_m": [round(R["battery"]["height_ft"] * FT, 3), round(R["headroom"]["min_ft"] * FT, 3)]}]
        if cid in ("opening_clearance", "gas_clearance", "ac_clearance", "fence_clearance"):
            r = R["clearances"][RULE_KEY[cid]] * FT
            return span_regions(b0 - r, b1 + r, height_m=[0.0, WALL_H])
        if cid in ("drive_clearance", "pool_clearance"):
            r = R["clearances"][RULE_KEY[cid]] * FT
            if measured_m is not None and measured_m < r:
                # a measured gap below the clearance decides on the space up to that gap only
                g = max(measured_m, 0.0) + 0.1
                return span_regions(b0 - g, b1 + g, out_m=[0.0, round(max(D, g), 3)])
            return span_regions(b0 - r, b1 + r, out_m=[0.0, round(r, 3)])
        if cid == "meter_working_space":
            return [{"wall_id": wid, "s_m": clip(wid, s_meter - 0.38, s_meter + 0.38), "height_m": [0.0, WALL_H]},
                    {"wall_id": wid, "s_m": clip(wid, b0, b1), "height_m": [0.0, WALL_H]}]
        if cid in ("route_length", "route_path"):
            return span_regions(min(s_meter, b0), max(s_meter, b1), height_m=[0.0, 0.6])
        raise KeyError(cid)

    def downgrade(outcome, evs):
        if outcome == "unsure":
            return outcome, None
        bad = [e["status"] for e in evs if e["status"] != "observed"]
        if not bad:
            return outcome, None
        cause = "generated_only" if "generated" in bad else ("grazing" if "grazing" in bad else "unobserved")
        return "unsure", cause

    # chain spots -> per-spot criteria outcomes (before coverage)
    spots = [s for s in sw["spots"] if s.get("ref") and s["ref"]["dist_m"] <= a.max_dist
             and s["ref"]["wall_id"] in walls]
    spots.sort(key=lambda s: (scene["chain"]["wall_ids"].index(s["ref"]["wall_id"]), s["ref"]["start_ft"]))

    def spot_criteria(s):
        out = {}
        by = {c["id"]: c for c in s["checks"]}
        wb = [by[k] for k in ("body_clear", "wall_backing") if k in by]
        o = max((c["outcome"] for c in wb), key=RANK.get)
        out["wall_backing"] = {"outcome": o, "c2": wb, "measured": None}
        for k, cid in CHECK_TO_CRITERION.items():
            if k in by:
                out[cid] = {"outcome": by[k]["outcome"], "c2": [by[k]], "measured": by[k]}
        if "wall_equipment_above" not in out:
            out["wall_equipment_above"] = {"outcome": "pass", "c2": [], "measured": None}
        rc = by["route"]
        no_path = rc["value_ft"] is None
        corner_fail = bool(R["route"].get("same_wall")) and bool(s["route"].get("corners"))
        if no_path:
            rl = "fail"
        else:
            rl = c5(rc["margin_ft"], rc["err_ft"])
            if rl == "pass" and rc["outcome"] == "unsure":
                rl = "unsure"
        out["route_length"] = {"outcome": rl, "c2": [rc], "measured": rc}
        rp = "fail" if (no_path or corner_fail) else ("unsure" if rc["unknown_count"] else "pass")
        out["route_path"] = {"outcome": rp, "c2": [rc], "measured": None}
        return out

    per = [(s, spot_criteria(s)) for s in spots]
    runs = []
    for s, cr in per:
        sig = (s["ref"]["wall_id"], s["outcome"], tuple(sorted((k, v["outcome"]) for k, v in cr.items())))
        a0 = s["ref"]["start_ft"] * FT
        if runs and runs[-1]["sig"] == sig and a0 - runs[-1]["a1"] <= 0.25:
            runs[-1]["a1"] = a0
            runs[-1]["members"].append((s, cr))
        else:
            runs.append({"sig": sig, "a0": a0, "a1": a0, "members": [(s, cr)]})

    # spots are ~10 cm apart: extend each run to the midpoint of the gap to its neighbour (gaps up to
    # 0.3 m), so every sampled start between two evaluated spots belongs to a candidate
    for r0, r1 in zip(runs, runs[1:]):
        if r0["sig"][0] == r1["sig"][0] and 0 < r1["a0"] - r0["a1"] <= 0.3:
            mid = (r0["a1"] + r1["a0"]) / 2
            r0["a1"], r1["a0"] = mid - 1e-4, mid
    criteria, candidates, need = [], [], {}
    for n, run in enumerate(runs):
        cid_c = f"c{n:03d}"
        wid = run["sig"][0]
        s_rep, cr_rep = run["members"][len(run["members"]) // 2]
        failing, unsure = [], []
        for cid, v in cr_rep.items():
            mv = v["measured"]["value_ft"] * FT if v["measured"] and v["measured"].get("value_ft") is not None else None
            evs = [ev(r) for r in regions_for(cid, wid, run["a0"], run["a1"], measured_m=mv)]
            out, cause = downgrade(v["outcome"], evs)
            m = v["measured"]
            if out == "unsure" and cause is None:
                cause = "unobserved" if any(c.get("unknown_count") for c in v["c2"]) else "margin"
            notes = "; ".join(filter(None, (f"{c['id']}: {c['outcome']}" + (f" ({c['note']})" if c.get("note") else "")
                                            for c in v["c2"])))
            crit = {"id": cid, "scope": "candidate", "candidate_id": cid_c, "outcome": out,
                    "reason": (f"C2 {v['outcome']}" + (f", downgraded: evidence {cause}" if cause in ("grazing", "unobserved", "generated_only") and v["outcome"] != "unsure" else "")
                               + (f". {notes}" if notes else "")),
                    "measured": None, "threshold": None, "margin_ft": None, "margin_over_uncertainty": None}
            if out == "unsure":
                crit["unsure_cause"] = cause
            if m is not None and m.get("value_ft") is not None:
                pm = m["err_ft"] if m.get("err_ft") is not None else 0.16
                crit["measured"] = {"value_ft": m["value_ft"], "plus_minus_ft": pm, "coverage": evs,
                                    **({"subject": m["subject"]} if m.get("subject") else {})}
                if m.get("threshold_ft") is not None:
                    at_most = cid in ("route_length",)
                    crit["threshold"] = {"value_ft": m["threshold_ft"], "comparison": "at_most" if at_most else "at_least"}
                if m.get("margin_ft") is not None:
                    crit["margin_ft"] = m["margin_ft"]
                    crit["margin_over_uncertainty"] = round(m["margin_ft"] / pm, 3) if pm else None
                    # C5: a decisive outcome must clear its own uncertainty
                    if out != "unsure" and c5(m["margin_ft"], pm) != out:
                        out = "unsure"
                        crit["outcome"] = out
                        crit["unsure_cause"] = "margin"
                        crit["reason"] += f"; margin {m['margin_ft']} ft within +-{pm} ft (C5)"
            elif out == "pass":
                crit["fact"] = {"value": v["outcome"], "clean": True, "coverage": evs, "raw": notes or "no overlap"}
            else:
                crit["fact"] = {"value": v["outcome"], "clean": out != "unsure", "coverage": evs, "raw": notes or ""}
            criteria.append(crit)
            if out == "fail":
                failing.append(cid)
            elif out == "unsure":
                unsure.append(cid)
                if cause in ("grazing", "unobserved"):
                    for e in evs:
                        if e["status"] != "observed":
                            reg = e["region"]
                            band = "ground" if "out_m" in reg and "height_m" not in reg else (
                                "facing" if "out_m" in reg else "wall")
                            k = (reg["wall_id"], band)
                            nd = need.setdefault(k, {"s": [reg["s_m"][0], reg["s_m"][1]], "crit": set(), "status": set(),
                                                     "out": 0.0})
                            nd["s"] = [min(nd["s"][0], reg["s_m"][0]), max(nd["s"][1], reg["s_m"][1])]
                            nd["crit"].add(cid)
                            nd["status"].add(e["status"])
                            if "out_m" in reg:
                                nd["out"] = max(nd["out"], reg["out_m"][1])
        outcome = "fail" if failing else ("unsure" if unsure else "pass")
        candidates.append({"id": cid_c, "wall_id": wid, "start_s_m": [round(run["a0"], 3), round(run["a1"], 3)],
                           "outcome": outcome, "failing": sorted(failing), "unsure": sorted(unsure)})

    views_needed = []
    for (wid, band), nd in sorted(need.items()):
        v = {"id": f"v-{wid}-{band}", "kind": "band", "band": band, "wall_id": wid,
             "s_m": [round(nd["s"][0], 3), round(nd["s"][1], 3)], "criteria": sorted(nd["crit"]),
             "target": f"head-on photos of the {band} {'strip' if band == 'ground' else 'face' if band == 'wall' else 'volume'} of {wid}",
             "why": f"evidence there is {'/'.join(sorted(nd['status']))} under the section 3.1 rule"}
        if nd["out"]:
            v["out_m"] = round(nd["out"], 3)
        views_needed.append(v)

    ends = scene["chain"].get("ends", {})
    ends_limit = all(ends.get(k, {}).get("kind") == "limit" for k in ("left", "right"))
    passes = [c for c in candidates if c["outcome"] == "pass"]
    uns = [c for c in candidates if c["outcome"] == "unsure"]
    reasons = []
    if passes:
        decision = "pass"
        chosen = passes[0]["id"]
        reasons.append("a candidate passes every criterion on observed evidence")
    else:
        chosen = uns[0]["id"] if uns else None
        if uns:
            reasons.append(f"{len(uns)} candidate run(s) unsure, none pass")
        if sw.get("unknown_route_cells_in_reach"):
            reasons.append(f"C2: {sw['unknown_route_cells_in_reach']} unobserved wall-side cells within cable reach")
        if not ends_limit:
            reasons.append("chain ends not both observed limits (a reject needs both, criteria spec 4.1)")
        decision = "manual_review" if reasons else "reject"
        if decision == "reject":
            reasons.append("every candidate fails on observed evidence")
    if decision in ("pass", "reject"):
        views_needed = []
    anchor_ev = [ev({"wall_id": scene["chain"]["wall_ids"][0],
                     "s_m": clip(scene["chain"]["wall_ids"][0], -0.5, 0.5), "height_m": [0.0, WALL_H]})]
    site_out = {"pass": "pass", "reject": "fail", "manual_review": "unsure"}[decision]
    placement = {"id": "placement", "scope": "site", "outcome": site_out,
                 "reason": "; ".join(reasons), "measured": None, "threshold": None,
                 "fact": {"value": decision, "clean": decision != "manual_review", "coverage": anchor_ev,
                          "raw": f"profile {a.profile}"}}
    if site_out == "unsure":
        placement["unsure_cause"] = "unobserved" if views_needed else "rule_requires_review"
    criteria.insert(0, placement)
    verdict = {
        "format": "base-verdict/v0",
        "verdict_id": f"C2-volumetric/{scene['scene_id']}/{a.profile}/{sw['grid']}",
        "scene": {"scene_id": scene["scene_id"], "site": scene["provenance"]["site"],
                  "sha256": hashlib.sha256(a.scene.read_bytes()).hexdigest(),
                  "producer_path": scene["provenance"]["producer"]["path"], "data": scene["provenance"]["data"]},
        "evaluator": {"path": "C2-volumetric", "version": "0.1", "rules_profile": a.profile,
                      "rules_sha256": hashlib.sha256(rules_text.encode()).hexdigest(), "auto_approve": True,
                      "runtime_s": round(sw["timing_s"]["total"] + time.time() - t0, 1), "cost_usd": 0.0},
        "site": {"decision": decision, "reasons": reasons, "chosen_candidate": chosen},
        "criteria": criteria, "candidates": candidates, "views_needed": views_needed,
    }
    validate(verdict)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(verdict, indent=1))
    n_dg = sum(1 for c in criteria if "downgraded" in c["reason"])
    print(json.dumps({"decision": decision, "candidates": len(candidates),
                      "by_outcome": {k: sum(c["outcome"] == k for c in candidates) for k in RANK},
                      "downgraded_criteria": n_dg, "views_needed": len(views_needed), "reasons": reasons}))


if __name__ == "__main__":
    main()
