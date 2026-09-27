"""Turn an engine3d decision into verdict pieces with contract coverage evidence."""

from __future__ import annotations

from server.criteria.c1 import engine3d as E
from server.criteria.harness import load
from server.criteria.lattice import margin_outcome

M = 0.3048
WALL_CHECKS = {"wall_backing", "opening_clearance", "under_window", "wall_equipment_above"}
ROUTE_CHECKS = {"route_path", "route_length"}
VOLUME = {"facing_gap", "headroom"}
GROUND_CHECKS = {
    "ground_surface",
    "gas_clearance",
    "ac_clearance",
    "drive_clearance",
    "pool_clearance",
    "footprint_clear",
}
AT_MOST = {"route_length"}
SOURCE_ERR_FT = {
    "tap": 0.3,
    "annotation": 0.3,
    "lidar": 0.3,
    "reconstruction": 0.3,
    "detector": 1.5,
    "vlm": 1.5,
    "mesh": 0.5,
    "laser_scan": 0.3,
}


def chain_s(sc: E.Scene3D, scene: dict | None = None) -> dict[str, tuple[float, int]]:
    mw = sc.walls[sc.meter_wall]
    out = {mw.id: (-sc.meter_u, 1)}
    todo = [mw.id]
    while todo:
        wid = todo.pop()
        s_a, sg = out[wid]
        length = sc.walls[wid].L
        for j in sc.joints:
            for w1, e1, w2, e2 in ((j.w1, j.e1, j.w2, j.e2), (j.w2, j.e2, j.w1, j.e1)):
                if w1 != wid or w2 in out:
                    continue
                s_end = s_a + sg * (0.0 if e1 == "a" else length)
                sg2 = sg if (e1 == "b") == (e2 == "a") else -sg
                s2a = s_end if e2 == "a" else s_end - sg2 * sc.walls[w2].L
                out[w2] = (s2a, sg2)
                todo.append(w2)
    # A gap in the chain has no joint, so the walk never reaches that wall.
    # The scene's s_range_m is the same chain coordinate the solver and labels use.
    if scene:
        for wall in scene.get("walls") or []:
            span = wall.get("s_range_m")
            if not span or wall["id"] in out or wall["id"] not in sc.walls:
                continue
            out[wall["id"]] = (float(span[0]) / M, 1)
    return out


def s_of(sm, wid, u) -> float:
    s_a, sg = sm.get(wid, (0.0, 1))
    return (s_a + sg * u) * M


def _span(sm, wid, u0, u1) -> list[float]:
    a, b = s_of(sm, wid, u0), s_of(sm, wid, u1)
    return [round(min(a, b), 4), round(max(a, b), 4)]


def regions_for(check: E.Check, cand: E.Candidate, sc: E.Scene3D, sm, profile: dict) -> list[dict]:
    w = sc.walls[cand.wall]
    depth = profile["battery"]["depth_ft"] * M
    if check.id in ROUTE_CHECKS and cand.route.stretches:
        return [
            {"wall_id": wid, "s_m": _span(sm, wid, a, b), "height_m": [0.05, 1.5]} for wid, a, b in cand.route.stretches
        ]
    if check.id == "meter_working_space":
        return [
            {
                "wall_id": sc.meter_wall,
                "s_m": _span(sm, sc.meter_wall, sc.meter_u - 1.25, sc.meter_u + 1.25),
                "height_m": [0.05, 2.13],
            }
        ]
    if check.id in WALL_CHECKS:
        return [{"wall_id": w.id, "s_m": _span(sm, w.id, cand.u0, cand.u1), "height_m": [0.05, 2.13]}]
    if check.id == "headroom":
        return [
            {"wall_id": w.id, "s_m": _span(sm, w.id, cand.u0, cand.u1), "out_m": [0.1, 0.91], "height_m": [0.3, 2.13]}
        ]
    if check.id == "facing_gap":
        reach = ((check.measured or 0.0) * M) + depth
        return [
            {
                "wall_id": w.id,
                "s_m": _span(sm, w.id, cand.u0, cand.u1),
                "out_m": [0.1, max(reach, depth)],
                "height_m": [0.3, 1.83],
            }
        ]
    thr = (check.threshold or 0.0) * M
    out = max(depth, thr + 2 * M)
    # Brief region: out_m starts at the wall. The gravel pass holds with that start.
    return [{"wall_id": w.id, "s_m": _span(sm, w.id, cand.u0, cand.u1), "out_m": [0.0, out]}]


def _clamp_evidence(ev: dict) -> dict:
    ang = ev.get("best_incidence_deg")
    if isinstance(ang, (int, float)) and ang > 90:
        ev["best_incidence_deg"] = 90.0
    worst = ev.get("worst_best_incidence_deg")
    if isinstance(worst, (int, float)) and worst > 90:
        ev["worst_best_incidence_deg"] = 90.0
    return ev


def coverage_status(ev: dict, ground_rule: str | None = None) -> str:
    """Ground rows carry one status per sensitivity rule. Walls have a single status."""
    if ground_rule:
        for row in ev.get("sensitivity") or []:
            if row.get("rule_id") == ground_rule and row.get("status"):
                return str(row["status"])
    status = ev.get("status")
    return str(status) if status else "unobserved"


def _unobserved_evidence(region: dict, method: str) -> dict:
    return _clamp_evidence(
        {
            "region": region,
            "status": "unobserved",
            "probes": 0,
            "observed_share": 0.0,
            "views_median": 0.0,
            "views_min": 0,
            "best_incidence_deg": None,
            "rule": {"min_views": 2, "max_incidence_deg": 75.0, "observed_share": 0.9, "surface": "wall"},
            "method": method,
        }
    )


def query_evidence(scene, scene_dir, region, query, ground_rule: str | None = None) -> dict:
    h = load()
    if query is None:
        return _unobserved_evidence(region, "no coverage query")
    try:
        ev = dict(h.coverage.query_region(scene, scene_dir, region, query))
    except KeyError:
        # query_region only indexes chain walls. A cage or other off-chain wall cannot be probed.
        return _unobserved_evidence(region, "wall not on chain")
    if ground_rule and ev.get("sensitivity"):
        match = next((row for row in ev["sensitivity"] if row.get("rule_id") == ground_rule), None)
        if match:
            ev["status"] = match.get("status", ev.get("status"))
            if "observed_share" in match:
                ev["observed_share"] = match["observed_share"]
            if "views_median" in match:
                ev["views_median"] = match["views_median"]
            if "views_min" in match:
                ev["views_min"] = match["views_min"]
            rule = dict(ev.get("rule") or {})
            if match.get("max_incidence_deg") is not None:
                rule["max_incidence_deg"] = match["max_incidence_deg"]
            if "max_footprint_m" in match:
                rule["max_footprint_m"] = match["max_footprint_m"]
            ev["rule"] = rule
    ev["method"] = ev.get("method") or "server.eval.coverage.query_region"
    return _clamp_evidence(ev)


def _region_key(region: dict, ground_rule: str | None) -> tuple:
    def _pair(name: str) -> tuple:
        raw = region.get(name) or []
        return tuple(round(float(v), 2) for v in raw)

    return (region.get("wall_id"), _pair("s_m"), _pair("out_m"), _pair("height_m"), ground_rule)


def _restore_geometric(check: E.Check) -> str:
    saved = getattr(check, "geom_outcome", None)
    if saved not in {"pass", "fail", "unsure"}:
        if check.measured is not None and check.threshold is not None:
            comparison = "at_most" if check.id in AT_MOST else "at_least"
            pm = float(check.pm or 0.0)
            saved, _margin, _mou = margin_outcome(
                float(check.measured), pm if pm > 0 else 0.3, float(check.threshold), comparison
            )
        else:
            saved = "pass"
    # The flag is the gate. Reason text is not: a rewritten sentence must not clear the check,
    # and the words "inferred geometry" must not block a check that is not inferred.
    if getattr(check, "inferred", False) and saved == "pass":
        return "unsure"
    return saved


def regrade_runs(runs: list[dict], scene, scene_dir, profile, sc, sm, query, ground_rule: str) -> None:
    """Replace engine3d's internal coverage with query_region.

    Observed evidence restores the geometric outcome. Anything else downgrades a
    pass or fail to unsure. Grazing stays grazing so a requested view says why.
    """
    cache: dict[tuple, dict] = {}

    def evidence(region: dict) -> dict:
        key = _region_key(region, ground_rule if "out_m" in region and "height_m" not in region else None)
        if key not in cache:
            rule = ground_rule if "out_m" in region and "height_m" not in region else None
            cache[key] = query_evidence(scene, scene_dir, region, query, rule)
        return cache[key]

    for run in runs:
        cand = run.get("cand")
        if cand is None:
            continue
        for check in cand.checks:
            if check.reason == "not required by this profile":
                continue
            if check.outcome == E.UNSURE and check.cause not in {"unobserved", "grazing"}:
                continue
            regs = regions_for(check, cand, sc, sm, profile)
            if not regs:
                continue
            evs = [evidence(reg) for reg in regs]
            if all(ev.get("status") == "observed" for ev in evs):
                if check.outcome == E.UNSURE and check.cause in {"unobserved", "grazing"}:
                    check.outcome = _restore_geometric(check)
                    check.cause = "margin" if check.outcome == E.UNSURE else None
                continue
            statuses = [ev.get("status") for ev in evs]
            check.outcome = E.UNSURE
            check.cause = "grazing" if "grazing" in statuses else "unobserved"
        run["failing"] = cand.failing()
        run["unsure"] = cand.unsure()
        run["outcome"] = cand.outcome
        run["causes"] = {c.id: c.cause for c in cand.checks if c.outcome == E.UNSURE and c.cause}


def _pm(check: E.Check, source_kind: str | None) -> float:
    pm = float(check.pm or 0.0)
    if pm > 1e-9:
        return pm
    return SOURCE_ERR_FT.get(source_kind or "", 0.3)


def criterion_dict(check: E.Check, cand_id: str, evidence: list[dict], source_kind: str | None) -> dict:
    out = {
        "id": check.id,
        "scope": "candidate",
        "candidate_id": cand_id,
        "outcome": check.outcome,
        "reason": check.reason or f"{check.id} {check.outcome}",
        "measured": None,
        "threshold": None,
    }
    if check.outcome == E.UNSURE:
        out["unsure_cause"] = check.cause or "margin"
    comparison = "at_most" if check.id in AT_MOST else "at_least"
    if check.threshold is not None and check.measured is not None and check.measured == check.measured:
        pm = _pm(check, source_kind)
        outcome, margin, mou = margin_outcome(float(check.measured), pm, float(check.threshold), comparison)
        # The engine already applied the margin rule. Coverage may only downgrade afterward.
        if _SEV[outcome] > _SEV[check.outcome]:
            out["outcome"] = outcome
            if outcome == "unsure":
                out["unsure_cause"] = "margin"
        out["measured"] = {
            "value_ft": round(float(check.measured), 4),
            "plus_minus_ft": round(pm, 4),
            "coverage": evidence,
        }
        if check.subject:
            out["measured"]["subject"] = str(check.subject)
        out["threshold"] = {
            "value_ft": float(check.threshold),
            "comparison": comparison,
            "source": "profiles.yaml",
            "placeholder": check.id in {"ground_surface", "headroom", "under_window"},
        }
        out["margin_ft"] = round(margin, 4)
        if mou is not None:
            out["margin_over_uncertainty"] = round(mou, 3)
    elif evidence:
        out["fact"] = {
            "value": check.outcome,
            "clean": check.outcome != E.UNSURE,
            "coverage": evidence,
        }
    bad = [ev for ev in evidence if ev.get("status") != "observed"]
    if out["outcome"] in ("pass", "fail") and bad:
        out["outcome"] = "unsure"
        out["unsure_cause"] = "grazing" if any(ev.get("status") == "grazing" for ev in bad) else "unobserved"
        out["reason"] = "coverage: " + out["reason"]
    if getattr(check, "inferred", False) and out["outcome"] == "pass":
        out["outcome"] = "unsure"
        out["unsure_cause"] = "unobserved"
    return out


_SEV = {"pass": 0, "unsure": 1, "fail": 2}


def group_runs(cands: list[E.Candidate], step: float) -> list[dict]:
    runs = []
    for c in sorted(cands, key=lambda c: (c.wall, c.u0)):
        key = (c.wall, c.outcome, tuple(c.failing()), tuple(c.unsure()))
        if runs and runs[-1]["_k"] == key and c.u0 - runs[-1]["u1"] <= step + 1e-6:
            runs[-1]["u1"] = c.u0
            # The run span stays the whole stretch. The sidecar uses the shortest route inside it.
            if c.route.length < runs[-1]["cand"].route.length:
                runs[-1]["cand"] = c
                runs[-1]["spot_u"] = c.u0
        else:
            runs.append(
                {
                    "_k": key,
                    "wall": c.wall,
                    "u0": c.u0,
                    "u1": c.u0,
                    "spot_u": c.u0,
                    "cand": c,
                    "outcome": c.outcome,
                    "failing": c.failing(),
                    "unsure": c.unsure(),
                    "causes": {ch.id: ch.cause for ch in c.checks if ch.outcome == E.UNSURE},
                }
            )
    for r in runs:
        del r["_k"]
    return runs


def apply_review_flags(checks: list[E.Check], meta: dict) -> None:
    unsure_ids = set(meta.get("location_unsure_ids") or [])
    review_ids = set(meta.get("review_only_ids") or [])
    inferred_ids = set(meta.get("inferred_ids") or [])
    for c in checks:
        if c.subject in unsure_ids:
            c.outcome, c.cause = E.UNSURE, "unobserved"
            c.reason = (c.reason + "; " if c.reason else "") + "location_unsure"
        elif c.subject in review_ids and c.outcome in (E.PASS, E.FAIL):
            c.outcome, c.cause = E.UNSURE, "unknown_attribute"
            c.reason = (c.reason + "; " if c.reason else "") + "confidence below 0.75"
        if c.inferred or c.subject in inferred_ids:
            c.inferred = True
            if c.outcome == E.PASS or getattr(c, "geom_outcome", None) == E.PASS:
                # Pin the saved geometric outcome. An observed query must not restore this pass.
                c.geom_outcome = E.UNSURE
                if c.outcome == E.PASS:
                    c.outcome, c.cause = E.UNSURE, "unobserved"
