"""Per-profile verdict assembly."""

from __future__ import annotations

import hashlib
from pathlib import Path

from server.criteria.c1.to_verdict import _span, criterion_dict, query_evidence, regions_for
from server.criteria.c3.facts import DECISION
from server.criteria.lattice import judge_join, site_decision

M = 0.3048


def placement_outcome(chosen, criteria: list[dict], runs: list[dict]) -> str:
    """Fail only when every run fails. One chosen fail beside an unsure run stays unsure."""
    if chosen is None:
        return "unsure"
    if chosen["outcome"] == "pass" and all(row["outcome"] == "pass" for row in criteria):
        return "pass"
    if runs and all(run["outcome"] == "fail" for run in runs):
        return "fail"
    return "unsure"


def decision_fact_map(fact_rows: list[dict]) -> dict:
    """not_evaluated and missing facts are omitted. A row with no outcome is absent."""
    found = {}
    for row in fact_rows:
        if row.get("id") in DECISION:
            found[row["id"]] = row
    out = {}
    for fid, row in found.items():
        if row.get("unsure_cause") == "not_evaluated":
            continue
        if row.get("outcome") in {"pass", "fail", "unsure"}:
            out[fid] = row["outcome"]
        else:
            out[fid] = None
    return out


def combine_decision(
    geometry: str, geometry_reasons: list[str], lattice: str, lattice_reasons: list[str], fact_failed: bool
) -> tuple[str, list[str]]:
    """Geometry wins when it is stricter, and a placement reject cannot override open ends or leftover unsure runs."""
    order = {"reject": 0, "manual_review": 1, "pass": 2}
    if order[geometry] < order[lattice]:
        return geometry, list(dict.fromkeys(geometry_reasons + lattice_reasons))
    if lattice == "reject" and geometry != "reject" and not fact_failed:
        return geometry, list(dict.fromkeys(geometry_reasons + lattice_reasons))
    if order[lattice] < order[geometry]:
        return lattice, list(dict.fromkeys(lattice_reasons + geometry_reasons))
    return lattice, list(dict.fromkeys(lattice_reasons + geometry_reasons))


def both_ends_ok(scene: dict, scene_dir, query) -> bool:
    ends = scene["chain"]["ends"]
    if ends["left"].get("kind") != "limit" or ends["right"].get("kind") != "limit":
        return False
    walls = scene["chain"]["wall_ids"]
    for wid, end in ((walls[0], "left"), (walls[-1], "right")):
        wall = next(w for w in scene["walls"] if w["id"] == wid)
        s0, s1 = wall.get("s_range_m") or [0.0, 1.0]
        span = [s0, s0 + 0.5] if end == "left" else [s1 - 0.5, s1]
        ev = query_evidence(scene, scene_dir, {"wall_id": wid, "s_m": span, "height_m": [0.05, 2.13]}, query)
        if ev.get("status") != "observed":
            return False
    return True


def build_verdict(
    *,
    scene: dict,
    scene_path: Path,
    profile_name: str,
    profile: dict,
    runs: list[dict],
    chosen,
    sc,
    sm,
    query,
    scene_dir,
    reasons: list[str],
    decision: str,
    runtime_s: float,
    rules_sha: str,
    ground_rule: str,
    ground_deg: float | None,
    read_facts: list[dict],
    cfg,
    views: list[dict],
    judge: str | None = None,
    judge_reasons: list[str] | None = None,
) -> dict:
    criteria = []
    if chosen is not None:
        for check in chosen["cand"].checks:
            if check.reason == "not required by this profile":
                continue
            evs = [
                query_evidence(scene, scene_dir, reg, query, ground_rule)
                for reg in regions_for(check, chosen["cand"], sc, sm, profile)
            ]
            criteria.append(criterion_dict(check, chosen.get("id") or "best", evs, None))
        # Chain-adopted fails on the chosen run must carry observed evidence.
        have = {c["id"] for c in criteria}
        for cid in chosen.get("chain_reasons") or []:
            if cid in have:
                row = next(c for c in criteria if c["id"] == cid)
                row["outcome"] = "fail"
                row["reason"] = "chain: " + row.get("reason", cid)
                row.pop("unsure_cause", None)
    placement = placement_outcome(chosen, criteria, runs)
    if decision == "reject" and not both_ends_ok(scene, scene_dir, query):
        decision = "manual_review"
        reasons = list(reasons) + ["ends_not_limit"]
        placement = "unsure" if placement == "fail" else placement
    fact_rows = list(read_facts)
    decision_facts = decision_fact_map(fact_rows)
    feature = {}
    for row in fact_rows:
        if row["id"] == "meter_number":
            feature["meter_number"] = {"clean": row.get("outcome") == "pass"}
    site, site_reasons = site_decision(placement, decision_facts, feature, views, profile, cfg)
    fact_failed = any(outcome == "fail" for outcome in decision_facts.values())
    site, site_reasons = combine_decision(decision, reasons, site, site_reasons, fact_failed)
    site = judge_join(site, judge, bool(getattr(cfg, "judge_may_reject", False)))
    if judge_reasons and judge not in {None, "pass"}:
        site_reasons = list(dict.fromkeys([*site_reasons, *judge_reasons]))
    if site in {"pass", "reject"}:
        views = []
    _link_views(criteria, views)
    place = {
        "id": "placement",
        "scope": "site",
        "outcome": {"pass": "pass", "manual_review": "unsure", "reject": "fail"}[site],
        "reason": "; ".join(site_reasons) or site,
        "measured": None,
        "threshold": None,
        "fact": {
            "value": profile_name,
            "clean": site != "manual_review",
            "coverage": _placement_coverage(criteria, scene, scene_dir, query, sc, ground_rule),
        },
    }
    if site == "manual_review":
        place["unsure_cause"] = "rule_requires_review"
    criteria.insert(0, place)
    seen = {row["id"] for row in criteria}
    for row in fact_rows:
        fid = row.get("id")
        if not fid or fid in seen:
            continue
        criteria.append(row)
        seen.add(fid)
    candidates = []
    for i, run in enumerate(runs):
        run["id"] = f"run{i}"
        candidates.append(
            {
                "id": run["id"],
                "wall_id": run["wall"],
                "start_s_m": _span(sm, run["wall"], run["u0"], run["u1"]),
                "outcome": run["outcome"],
                "failing": list(run.get("failing") or []),
                "unsure": list(run.get("unsure") or []),
            }
        )
    if chosen is not None:
        chosen_id = chosen.get("id")
    else:
        chosen_id = None
    return {
        "format": "base-verdict/v0",
        "verdict_id": f"{scene['scene_id']}/{profile_name}",
        "scene": {
            "scene_id": scene["scene_id"],
            "site": scene["provenance"]["site"],
            "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
            "producer_path": scene["provenance"]["producer"]["path"],
            "data": scene["provenance"]["data"],
        },
        "evaluator": {
            "path": "C1-rules-3d+hs11-chain" if cfg.hybrid else "C1-rules-3d",
            "version": "0",
            "rules_profile": profile_name,
            "rules_sha256": rules_sha,
            "ground_rule": ground_rule,
            **({"ground_incidence_deg": ground_deg} if ground_deg is not None else {}),
            "auto_approve": bool(profile["policy"].get("auto_approve", True)),
            "runtime_s": round(runtime_s, 3),
            "cost_usd": 0.0,
        },
        "site": {"decision": site, "reasons": site_reasons, "chosen_candidate": chosen_id},
        "criteria": criteria,
        "candidates": candidates,
        "views_needed": views,
    }


def _link_views(criteria: list[dict], views: list[dict]) -> None:
    for row in criteria:
        if row.get("outcome") != "unsure":
            continue
        ids = [v["id"] for v in views if row["id"] in (v.get("criteria") or [])]
        if ids:
            row["views_needed"] = ids


def _placement_coverage(criteria, scene, scene_dir, query, sc, ground_rule: str | None = None):
    for row in criteria:
        cov = (row.get("measured") or row.get("fact") or {}).get("coverage") or []
        if cov:
            return cov
    ev = query_evidence(
        scene,
        scene_dir,
        {"wall_id": sc.meter_wall, "s_m": [-0.5, 0.5], "height_m": [0.05, 2.13]},
        query,
        ground_rule,
    )
    return [ev]
