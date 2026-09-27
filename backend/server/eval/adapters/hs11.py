"""C1: PR #11's placement solver (vendor/hs11_server @ cd0da8b, unchanged) on a scene v0.

1. `to_hs11(scene)`: scene v0 (meters) -> PR #11 scene.json (feet). Generated elements are dropped;
   walls that are not `observed` get no wall band, so nothing can pass on them; coverage bands come
   from the scene (or its ground-sensitivity variant for `ground_rule`).
2. `solve` with the vendored rules (demo policy unless a rules path is given).
3. `to_verdict(result)`: PR #11 result -> verdict v0. Every measured check at the chosen spot gets
   coverage evidence (contract section 3.1) from the scene's own cameras and geometry; a pass or
   fail whose evidence is not `observed` becomes unsure (grazing / unobserved), passing sweep runs
   are re-checked the same way, and the site decision is recomputed.

    python -m server.eval.adapters.hs11 --scene S/scene.json --out V/verdict.json [--ground-rule angle75|angle80|footprint3in]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


from server.eval import VENDOR_DIR
from server.eval.chain import Chain, object_bottom_top, object_center, object_plan_points, object_s_span
from server.eval.coverage import (
    GROUND_MAX_OUT_M,
    GROUND_RULES,
    HEADROOM_M,
    CoverageQuery,
    MissingOccluderError,
    query_region,
)
from server.eval.frame import M_PER_FT
from server.eval.sceneio import load_json, save_json

sys.path.insert(0, str(VENDOR_DIR / "hs11_server"))
from rules import load_rules  # noqa: E402
from scene import parse_scene  # noqa: E402
from solver import solve  # noqa: E402

HS11_REF = "PR #11 t3/server @ cd0da8b"
TYPE_MAP = {"window": "window", "door": "door", "garage_door": "garage_door", "ac_unit": "ac", "gas_meter": "gas_meter",
            "gas_regulator": "gas_meter", "gas_pipe": "gas_meter", "wall_box": "elec_box", "panel": "elec_box",
            "disconnect": "elec_box", "meter_can": "elec_box", "vent": "vent", "downspout": "downspout", "pool": "pool"}
GROUND_MAP = {"concrete": "concrete", "pavers": "concrete", "walkway": "concrete", "gravel": "gravel", "lawn": "lawn",
              "mulch": "mulch", "deck": "deck", "drive": "drive"}
SOURCE_MAP = {"detector": "vlm", "vlm": "vlm", "tap": "tap", "annotation": "tap", "tape": "tape", "laser_scan": "tape",
              "lidar": "tap", "reconstruction": "tap"}
BATTERY_DEPTH_M = 22 * 0.0254
WALL_CHECKS = {"wall_backing", "wall_equipment_above", "opening_clearance", "meter_working_space", "gas_clearance", "ac_clearance"}
GROUND_CHECKS = {"ground_surface", "drive_clearance", "pool_clearance"}


def ft(v: float) -> float:
    return round(float(v) / M_PER_FT, 4)


def _bands(scene: dict, ground_rule: str | None) -> list[dict]:
    """The scene's bands, with the ground bands of one GROUND_RULES variant when `ground_rule` is set."""
    bands = scene["coverage"].get("bands", [])
    if ground_rule is None:
        return bands
    alt = next((x for x in scene["coverage"].get("sensitivity", []) if x["surface"] == "ground" and x.get("rule_id") == ground_rule), None)
    if alt is None:
        raise ValueError(f"scene has no ground bands for rule {ground_rule!r} (rebuild it)")
    return [b for b in bands if b["band"] != "ground"] + alt["bands"]


def to_hs11(scene: dict, ground_rule: str | None = None) -> dict:
    ch = Chain(scene)
    walls = {w["id"]: w for w in scene["walls"]}
    anchor = scene["frame"]["anchor"]["element_id"]
    meter = next(o for o in scene["objects"] if o["id"] == anchor)
    mc = object_center(meter, ch)
    out = {"schema_version": "1.0",
           "meter": {"pos": [ft(v) for v in mc], "wall_id": meter.get("wall_id") or ch.nearest_wall(mc[[0, 2]]).id,
                     "plus_minus_ft": ft(meter["plus_minus_m"] + scene["frame"]["anchor"].get("plus_minus_m", 0.0))},
           "walls": [], "objects": [], "ground": [], "overheads": [], "facing": []}
    for cw in ch.walls:
        w = walls[cw.id]
        if w["evidence"] == "generated":
            continue
        out["walls"].append({"id": cw.id, "baseline": [[ft(v) for v in cw.p0], [ft(v) for v in cw.p1]],
                             "height_ft": ft(w["height_m"]), "plus_minus_ft": ft(w["plus_minus_m"])})
    kept = {w["id"] for w in out["walls"]}
    for o in scene["objects"]:
        t = TYPE_MAP.get(o["class"])
        if o["id"] == anchor or t is None or o["evidence"] == "generated":
            continue
        wid = o.get("wall_id") or o["geometry"].get("wall_id") or ch.nearest_wall(object_center(o, ch)[[0, 2]]).id
        if wid not in kept:
            continue
        s0, s1 = object_s_span(o, ch)
        b, top = object_bottom_top(o, scene, ch)
        e = {"type": t, "wall_id": wid, "span_ft": [ft(s0), ft(s1)], "bottom_ft": ft(max(0.0, b)), "top_ft": ft(max(0.0, top)),
             "source": SOURCE_MAP.get(o["source"]["kind"], "vlm"), "conf": o["confidence"], "plus_minus_ft": ft(o["plus_minus_m"])}
        attrs = {k: v for k, v in (o.get("attrs") or {}).items() if k in ("operable", "well")}
        if attrs:
            e["attrs"] = attrs
        if o["geometry"]["kind"] in ("box", "footprint"):
            e["footprint"] = [[ft(x), ft(z)] for x, z in object_plan_points(o, ch)]
        out["objects"].append(e)
    for r in scene["ground"].get("regions", []):
        t = GROUND_MAP.get(r["material"])
        if t and r["evidence"] != "generated":
            out["ground"].append({"type": t, "polygon": [[ft(x), ft(z)] for x, z in r["polygon_m"]],
                                  "plus_minus_ft": ft(r.get("plus_minus_m", scene["ground"]["plus_minus_m"]))})
    for key, val in (("overheads", "clearance"), ("facing", "depth")):
        for e in scene.get(key, []):
            if e["evidence"] == "generated" or e["wall_id"] not in kept:
                continue
            out[key].append({"wall_id": e["wall_id"], "span_ft": [ft(v) for v in e["s_m"]], f"{val}_ft": ft(e[f"{val}_m"]),
                             "plus_minus_ft": ft(e["plus_minus_m"])})
    observed_walls = {w["id"] for w in scene["walls"] if w["evidence"] == "observed"}
    obs = []
    for b in _bands(scene, ground_rule):
        if b["band"] == "wall" and b.get("wall_id") not in observed_walls:
            continue
        e = {"band": b["band"], "span_ft": [ft(v) for v in b["s_m"]]}
        if "out_m" in b:
            e["out_ft"] = ft(b["out_m"])
        elif b["band"] == "ground":
            e["out_ft"] = ft(GROUND_MAX_OUT_M)
        obs.append(e)
    ends = {s: {"kind": scene["chain"]["ends"][s]["kind"], **({"note": scene["chain"]["ends"][s]["note"]} if "note" in scene["chain"]["ends"][s] else {})}
            for s in ("left", "right")}
    out["coverage"] = {"ends": ends, "observed": obs}
    return out


def _check_regions(cid: str, ch: Chain, wall_id: str, s0: float, s1: float, measured_m: float | None) -> list[dict]:
    """Regions a PR #11 check at battery span [s0, s1] depends on."""
    w = ch.by_id[wall_id]
    pad = 0.91
    if cid in WALL_CHECKS:
        return [{"wall_id": wall_id, "s_m": [max(w.s0, s0 - pad), min(w.s1, s1 + pad)], "height_m": [0.05, HEADROOM_M]}]
    if cid in GROUND_CHECKS:
        out = BATTERY_DEPTH_M + (measured_m if cid != "ground_surface" and measured_m is not None else 0.3)
        return [{"wall_id": wall_id, "s_m": [s0, s1], "out_m": [0.0, min(max(out, BATTERY_DEPTH_M + 0.1), GROUND_MAX_OUT_M)]}]
    if cid == "facing_gap":
        return [{"wall_id": wall_id, "s_m": [s0, s1], "out_m": [0.1, min((measured_m or 1.5) + BATTERY_DEPTH_M, 18.0)], "height_m": [0.3, 1.83]}]
    if cid == "headroom":
        return [{"wall_id": wall_id, "s_m": [s0, s1], "out_m": [0.1, BATTERY_DEPTH_M], "height_m": [0.3, HEADROOM_M]}]
    if cid in ("route_path", "route_length"):
        regs = []
        for cw in ch.walls:
            a, b = max(cw.s0, min(0.0, s0)), min(cw.s1, max(0.0, s1))
            if b - a > 0.05:
                regs.append({"wall_id": cw.id, "s_m": [a, b], "height_m": [0.05, 1.5]})
        return regs
    return []


def _views_needed_for(ev: dict, cid: str) -> dict:
    reg = ev["region"]
    kind = "ground" if "out_m" in reg and "height_m" not in reg else ("wall" if "out_m" not in reg else "facing")
    target = {"ground": "downward photo of the ground in front of this wall, from chest height, within ~2 m",
              "wall": "head-on photo of this wall section from 2-4 m", "facing": "photo straight out from this wall"}[kind]
    return {"kind": "band", "band": kind if kind != "facing" else "facing", "wall_id": reg["wall_id"], "s_m": [round(v, 3) for v in reg["s_m"]],
            **({"out_m": round(reg["out_m"][1], 3)} if "out_m" in reg else {}), "target": target, "criteria": [cid],
            "why": f"{ev['status']}: {ev['observed_share']:.0%} of the region well observed, best angle {ev['best_incidence_deg']} deg"}


def to_verdict(result: dict, scene: dict, scene_path: Path, hs11_scene: dict, loaded, query: CoverageQuery | None,
               ground_rule: str | None, runtime_s: float) -> dict:
    ch = Chain(scene)
    spot = result["spot"] or result.get("nearest_considered")
    crit, views = [], []

    def add_view(v):
        v["id"] = f"vn{len(views) + 1}"
        views.append(v)
        return v["id"]

    for m in result["missing_evidence"]:
        s = m.get("span_ft", [0, 0])
        s_m = [s[0] * M_PER_FT, s[1] * M_PER_FT]
        w = ch.wall_at_s((s_m[0] + s_m[1]) / 2) or (ch.walls[0] if m.get("side") == "left" else ch.walls[-1])
        v = {"kind": m["kind"], "wall_id": w.id, "s_m": [round(x, 3) for x in s_m], "criteria": m.get("checks", []), "why": m["message"]}
        if "band" in m:
            v["band"] = m["band"]
        if "out_ft" in m:
            v["out_m"] = round(m["out_ft"] * M_PER_FT, 3)
        if "side" in m:
            v["side"] = m["side"]
        if m.get("band") == "ground":
            v["target"] = "downward photo of the ground in front of this wall, from chest height"
        add_view(v)

    downgraded = []
    for c in result["checks"]:
        e = {"id": c["id"], "scope": "candidate", "candidate_id": "spot", "outcome": c["outcome"], "reason": c["reason"],
             "measured": None, "threshold": None}
        if c.get("unsure_cause"):
            e["unsure_cause"] = c["unsure_cause"]
        if c["threshold_ft"] is not None and c["comparison"]:
            e["threshold"] = {"value_ft": c["threshold_ft"], "comparison": c["comparison"], "rule_key": c["rule"]["key"],
                              "source": c["rule"]["source"], "placeholder": c["rule"]["placeholder"],
                              **({"review_value_ft": c["review_threshold_ft"]} if "review_threshold_ft" in c else {})}
        evs = []
        if spot is not None:
            s0, s1 = (v * M_PER_FT for v in spot["span_ft"])
            meas_m = c["measured_ft"] * M_PER_FT if c["measured_ft"] is not None else None
            for reg in _check_regions(c["id"], ch, spot["wall_id"], s0, s1, meas_m):
                if query is not None:
                    ev = query_region(scene, scene_path.parent, reg, query)
                else:
                    ev = {"region": reg, "status": "unobserved", "observed_share": 0.0, "views_min": 0, "views_median": 0.0,
                          "best_incidence_deg": None, "rule": {"min_views": 2, "max_incidence_deg": None}, "method": "no keyframes: cannot tell"}
                # The facade golden records this producer name. The callable is
                # server.eval.coverage.query_region; the label stays so run-hs11
                # matches gt.C1.g80.json.
                ev["method"] = ev.get("method", "evalharness.coverage.query_region")
                evs.append(ev)
        if c["measured_ft"] is not None:
            pm = c["plus_minus_ft"] or 0.0
            e["measured"] = {"value_ft": c["measured_ft"], "plus_minus_ft": pm, "coverage": evs or [
                {"region": {"description": "no spot"}, "status": "unobserved", "observed_share": 0.0, "views_min": 0, "views_median": 0.0,
                 "best_incidence_deg": None, "rule": {"min_views": 2, "max_incidence_deg": None}}]}
            if c.get("subject"):
                e["measured"]["subject"] = c["subject"]
            if e["threshold"]:
                sign = 1 if c["comparison"] == "at_least" else -1
                e["margin_ft"] = round(sign * (c["measured_ft"] - c["threshold_ft"]), 4)
                e["margin_over_uncertainty"] = round(e["margin_ft"] / pm, 3) if pm > 0 else None
        bad = [ev for ev in evs if ev["status"] != "observed"]
        if e["outcome"] in ("pass", "fail") and (bad or e["measured"] is None and e["outcome"] == "pass" and evs):
            if bad:
                e["reason"] = f"[coverage: {bad[0]['status']}] " + e["reason"]
                e["outcome"] = "unsure"
                e["unsure_cause"] = "grazing" if any(ev["status"] == "grazing" for ev in bad) else "unobserved"
                e["views_needed"] = [add_view(_views_needed_for(ev, c["id"])) for ev in bad]
                downgraded.append(c["id"])
        if e["outcome"] == "pass" and e["measured"] is None:
            e["fact"] = {"value": True, "clean": True, "raw": "pass", "coverage": evs or [
                {"region": {"description": "no spot"}, "status": "unobserved", "observed_share": 0.0, "views_min": 0,
                 "views_median": 0.0, "best_incidence_deg": None, "rule": {"min_views": 2, "max_incidence_deg": None}}]}
            if any(ev["status"] != "observed" for ev in e["fact"]["coverage"]):
                e["outcome"], e["unsure_cause"] = "unsure", "unobserved"
                downgraded.append(c["id"])
        crit.append(e)

    cands = []
    fail_cache: dict = {}
    fail_downgraded: list[str] = []
    for i, r in enumerate(result["sweep"]):
        s_m = [r["start_ft"][0] * M_PER_FT, r["start_ft"][1] * M_PER_FT]
        cand = {"id": f"sw{i + 1}", "wall_id": r["wall_id"], "start_s_m": [round(v, 4) for v in s_m], "outcome": r["outcome"],
                "failing": r["failing"], "unsure": r["unsure"]}
        if r["outcome"] == "pass" and query is not None:
            width = loaded.rules.battery.width_ft.value * M_PER_FT
            span = [s_m[0], s_m[1] + width]
            w = ch.by_id[r["wall_id"]]
            regs = [{"wall_id": w.id, "s_m": [max(w.s0, span[0]), min(w.s1, span[1])], "height_m": [0.05, HEADROOM_M]},
                    {"wall_id": w.id, "s_m": [max(w.s0, span[0]), min(w.s1, span[1])], "out_m": [0.0, BATTERY_DEPTH_M + 0.3]}]
            bad = [ev for ev in (query_region(scene, scene_path.parent, g, query) for g in regs) if ev["status"] != "observed"]
            if bad:
                cand["outcome"] = "unsure"
                cand["unsure"] = sorted(set(cand["unsure"]) | {"coverage"})
        elif r["outcome"] == "fail" and query is not None:
            # Contract 3.1: a fail needs observed evidence too. The run stays a fail only if at
            # least one failing check rests on regions that are all observed.
            width = loaded.rules.battery.width_ft.value * M_PER_FT
            backed, dropped = [], []
            for cid in r["failing"]:
                regs = _check_regions(cid, ch, r["wall_id"], s_m[0], s_m[1] + width, None)
                key = (cid, r["wall_id"], round(s_m[0], 2), round(s_m[1], 2))
                if key not in fail_cache:
                    fail_cache[key] = bool(regs) and all(query_region(scene, scene_path.parent, g, query)["status"] == "observed" for g in regs)
                (backed if fail_cache[key] else dropped).append(cid)
            if not backed:
                cand["outcome"] = "unsure"
                cand["failing"] = []
                cand["unsure"] = sorted(set(cand["unsure"]) | set(dropped) | {"coverage"})
                fail_downgraded.append(cand["id"])
            elif dropped:
                cand["failing"] = backed
        cands.append(cand)

    decision = result["decision"]
    reasons = [x["code"] for x in result["reasons"]]
    spot_outcome = "unsure" if any(e["outcome"] == "unsure" for e in crit) else ("fail" if any(e["outcome"] == "fail" for e in crit) else "pass")
    if decision == "pass" and spot_outcome != "pass":
        decision, reasons = "manual_review", reasons + ["coverage_downgrade"]
    if decision == "reject" and (downgraded or fail_downgraded):
        decision, reasons = "manual_review", reasons + ["coverage_downgrade"]
        if fail_downgraded:
            for c in cands:
                if c["id"] in fail_downgraded:
                    for reg in _check_regions("wall_backing", ch, c["wall_id"], c["start_s_m"][0], c["start_s_m"][1] + 0.79, None):
                        add_view({"kind": "band", "band": "wall", "wall_id": reg["wall_id"], "s_m": [round(v, 3) for v in reg["s_m"]],
                                  "target": "head-on photo of this wall section from 2-4 m", "criteria": c["unsure"],
                                  "why": "fails here rest only on grazing or unobserved evidence"})
    if decision in ("pass", "reject"):
        views = []
        for e in crit:
            e.pop("views_needed", None)
    policy = result["policy"]
    return {
        "format": "base-verdict/v0",
        "verdict_id": f"{scene['scene_id']}/C1-rules-hs11" + (f"/{ground_rule}" if ground_rule else ""),
        "scene": {"scene_id": scene["scene_id"], "site": scene["provenance"]["site"],
                  "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
                  "producer_path": scene["provenance"]["producer"]["path"], "data": scene["provenance"]["data"]},
        "evaluator": {"path": "C1-rules-hs11", "version": HS11_REF, "rules_profile": policy.get("id") or "none",
                      "rules_sha256": policy["rules_sha256"], "auto_approve": policy["auto_approve"], "runtime_s": round(runtime_s, 2),
                      **({"ground_rule": ground_rule, **({"ground_incidence_deg": GROUND_RULES[ground_rule]["max_incidence_deg"]}
                                                          if ground_rule in GROUND_RULES else {})} if ground_rule else {})},
        "site": {"decision": decision, "reasons": reasons, "chosen_candidate": "spot" if result["spot"] else None},
        "criteria": crit, "candidates": cands, "views_needed": views,
    }


def run(scene_path: Path, out_path: Path | None = None, ground_rule: str | None = None, rules_path: Path | None = None) -> tuple[dict, dict]:
    t0 = time.time()
    scene_path = Path(scene_path)
    scene = load_json(scene_path)
    raw = to_hs11(scene, ground_rule)
    loaded = load_rules(rules_path) if rules_path else load_rules()
    result = solve(parse_scene(raw, loaded.rules), loaded)
    try:
        query = CoverageQuery.from_scene(scene, scene_path.parent)
    except ValueError:
        # No keyframes: there is no coverage query to run. A missing PLY is
        # FileNotFoundError or MissingOccluderError and must not be swallowed (SD-61).
        query = None
    v = to_verdict(result, scene, scene_path, raw, loaded, query, ground_rule, time.time() - t0)
    if out_path:
        out_path = Path(out_path)
        save_json(v, out_path)
        (out_path.parent / (out_path.stem + ".hs11-scene.json")).write_text(json.dumps(raw, indent=1) + "\n")
        (out_path.parent / (out_path.stem + ".hs11-result.json")).write_text(json.dumps(result, indent=1) + "\n")
    return v, result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ground-rule", choices=sorted(GROUND_RULES), help="use the scene's ground bands under this rule variant (sensitivity run)")
    ap.add_argument("--rules", type=Path, help="private rules.yaml to merge over the vendored public one")
    a = ap.parse_args()
    v, r = run(a.scene, a.out, a.ground_rule, a.rules)
    print(json.dumps({"hs11_decision": r["decision"], "verdict_decision": v["site"]["decision"], "reasons": v["site"]["reasons"],
                      "spot": r["spot"] and {k: r["spot"][k] for k in ("wall_id", "span_ft", "outcome")},
                      "checks": {c["id"]: c["outcome"] for c in v["criteria"]}, "views_needed": len(v["views_needed"]),
                      "stats": r["stats"]}, indent=1))


if __name__ == "__main__":
    main()
