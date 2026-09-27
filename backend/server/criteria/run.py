"""Evaluate one scene v0 into one verdict per rules profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import time
from pathlib import Path

import yaml
from pydantic import ValidationError
from shapely.geometry import Polygon

from server.contracts.scene import Scene, scene_consistency_errors
from server.contracts.verdict import Verdict, verdict_contract_errors
from server.criteria.c1 import engine3d as E
from server.criteria.c1.chain_hybrid import adopt, solve_chain
from server.criteria.c1.from_scene_v0 import convert, stamp_ground_rule_ids
from server.criteria.c1.to_verdict import _span, apply_review_flags, chain_s, group_runs, regrade_runs, s_of
from server.criteria.c2.grid_adapter import c2_rules, chain_offsets_m, from_scene
from server.criteria.c2.sweep import Sweep
from server.criteria.c2.veto import join as c2_join
from server.criteria.c3.facts import apply_reads, disabled_facts
from server.criteria.c3.judge import resolve_judge
from server.criteria.candidates import choose, placement_doc
from server.criteria.compose import build_verdict
from server.criteria.config import CriteriaConfig
from server.criteria.harness import load
from server.criteria.occluders import missing_occluder_geometry
from server.criteria.views_needed import build as build_views
from server.criteria.views_needed import coverage_requests, view_requests

log = logging.getLogger("server.criteria")
HERE = Path(__file__).resolve().parent
GROUND_DEG = {"angle75": 75.0, "angle80": 80.0, "footprint3in": 88.0}


class CriteriaInputError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _profiles() -> dict:
    return yaml.safe_load((HERE / "profiles.yaml").read_text())


def merged_profile(name: str) -> dict:
    raw = _profiles()
    if name not in raw["profiles"]:
        raise CriteriaInputError("profile_unknown")
    return E._merge(E._merge(raw["base"], raw["profiles"][name]), raw["contract"])


def _scale(scene_dir: Path, scene: dict) -> dict | None:
    path = scene_dir / "scale.json"
    if path.is_file():
        return json.loads(path.read_text())
    return (scene.get("provenance") or {}).get("producer", {}).get("params", {}).get("scale")


def _query(scene: dict, scene_dir: Path, degraded: list[str], *, allow_missing: bool = False):
    """Visibility blockers are the laser or recon PLY, plus a declared occluders asset.

    A missing cloud is an error. The old voxel fallback and an empty blocker list both
    score the wall as seen. ``allow_missing`` is the debug switch: the run continues
    with no visibility query and the report is marked provisional.
    """
    if missing_occluder_geometry(scene, scene_dir):
        degraded.append("occluders_missing")
        if not allow_missing:
            raise CriteriaInputError("occluders_missing")
        return None
    if not scene.get("keyframes"):
        degraded.append("no_keyframes")
        return None
    h = load()
    try:
        return h.coverage.CoverageQuery.from_scene(scene, scene_dir)
    except Exception as exc:
        log.exception("occluder load failed")
        degraded.append("occluders_missing")
        if not allow_missing:
            raise CriteriaInputError("occluders_missing") from exc
        return None


def _views_from_runs(runs, sm, scene) -> list[dict]:
    items = []
    for run in runs:
        if run["outcome"] == "pass":
            continue
        for cid in run.get("unsure") or []:
            cause = (run.get("causes") or {}).get(cid)
            # Only coverage gaps ask for another photo. A margin or a review flag does not.
            if cause not in {"unobserved", "grazing", "unknown_attribute"}:
                continue
            band = (
                "ground" if cid in {"ground_surface", "drive_clearance", "pool_clearance", "gas_clearance"} else "wall"
            )
            items.append(
                {
                    "kind": "band",
                    "band": band,
                    "wall_id": run["wall"],
                    "s_m": _span_run(sm, run),
                    "criteria": [cid],
                    "why": cause,
                }
            )
    for end_name, side in (("left", "left"), ("right", "right")):
        if scene["chain"]["ends"][end_name].get("kind") == "unexplored":
            wid = scene["chain"]["wall_ids"][0 if side == "left" else -1]
            items.append(
                {
                    "kind": "past_end",
                    "side": side,
                    "wall_id": wid,
                    "s_m": _end_span(scene, sm, wid, side),
                    "criteria": ["placement"],
                    "why": "unobserved",
                }
            )
    return items


def _span_run(sm, run):
    return _span(sm, run["wall"], run["u0"], run["u1"])


def _end_span(scene, sm, wall_id: str, side: str) -> list[float]:
    wall = next((w for w in scene.get("walls") or [] if w["id"] == wall_id), None)
    span = (wall or {}).get("s_range_m")
    if span and len(span) == 2:
        s0, s1 = float(span[0]), float(span[1])
        if side == "left":
            return [round(s0 - 0.5, 4), round(s0, 4)]
        return [round(s1, 4), round(s1 + 0.5, 4)]
    origin = s_of(sm, wall_id, 0.0)
    if side == "left":
        return [round(origin - 0.5, 4), round(origin, 4)]
    return [round(origin, 4), round(origin + 0.5, 4)]


def _stamp_spots(spots, scene: dict) -> None:
    offsets = chain_offsets_m(scene)
    for spot in spots:
        xy = spot.get("rear_mid_xy") or spot.get("center_xy")
        if not xy:
            continue
        x, z = xy
        best, s_hit, dist = None, 0.0, 1e9
        for w in scene["walls"]:
            (x0, z0), (x1, z1) = w["baseline_m"]
            dx, dz = x1 - x0, z1 - z0
            length = math.hypot(dx, dz) or 1.0
            t = max(0.0, min(1.0, ((x - x0) * dx + (z - z0) * dz) / (length * length)))
            d = math.hypot(x0 + t * dx - x, z0 + t * dz - z)
            s0 = offsets.get(w["id"], (w.get("s_range_m") or [0.0])[0])
            if d < dist:
                best, s_hit, dist = w["id"], s0 + t * length, d
        spot["s_m"] = s_hit
        spot["wall_dist_m"] = dist
        spot["wall_id"] = best


def _run_c2(scene, scene_dir, profile, budget_s: float) -> tuple[list[dict], str]:
    adapted = from_scene(scene, scene_dir)
    if adapted is None:
        return [], "no_coverage_grid"
    grid, objects, ground, meta = adapted
    t0 = time.time()
    # The first numba compile is about 10 s, so a sub-second budget cannot finish.
    if budget_s < 1.0:
        return [], "skipped"
    try:
        rules = c2_rules(profile)
        sw = Sweep(
            {"objects": objects, "meter": meta["meter"]},
            grid,
            rules,
            step=0.2,
            ground_types=[(Polygon(poly), mat) for poly, mat in ground],
            ground_err=0.05,
        )
        if time.time() - t0 > budget_s:
            return [], "skipped"
        spots = sw.run()
    except SystemExit as exc:
        if "no observed ground" in str(exc).lower():
            return [], "no_observed_ground"
        raise
    if time.time() - t0 > budget_s:
        return spots, "skipped"
    _stamp_spots(spots, scene)
    return spots, "ok"


def downgrade_c1_passes_without_c2(runs: list[dict]) -> None:
    """No coverage grid: C2 cannot pass a check, so C1 passes do not stand."""
    for run in runs:
        checks = list(getattr(run.get("cand"), "checks", []))
        for check in checks:
            if check.outcome == "pass":
                check.outcome = "unsure"
                check.cause = "not_evaluated"
        if run.get("outcome") == "pass":
            run["outcome"] = "unsure"
        unsure_ids = {c.id for c in checks if c.outcome == "unsure"}
        run["unsure"] = sorted(set(run.get("unsure") or []) | unsure_ids)
        run["failing"] = [cid for cid in (run.get("failing") or []) if cid not in unsure_ids]
        if not run["failing"] and run.get("outcome") == "fail":
            run["outcome"] = "unsure"
        causes = dict(run.get("causes") or {})
        for check in checks:
            if check.outcome == "unsure" and check.cause:
                causes[check.id] = check.cause
        run["causes"] = causes


def c2_sweep_document(by_profile: dict[str, dict], default_profile: str) -> dict:
    """Top-level counts are the default profile, not whichever profile ran last."""
    chosen = by_profile.get(default_profile)
    if chosen is None and by_profile:
        chosen = next(iter(by_profile.values()))
    chosen = chosen or {"status": "off", "n_pass": 0, "n_spots": 0}
    return {
        "status": chosen["status"],
        "n_pass": chosen["n_pass"],
        "n_spots": chosen["n_spots"],
        "by_profile": by_profile,
    }


def _require_scene(scene: dict) -> None:
    try:
        Scene.model_validate(scene)
    except ValidationError as exc:
        raise CriteriaInputError("scene_invalid") from exc
    if scene_consistency_errors(scene):
        raise CriteriaInputError("scene_invalid")


def _require_verdict(verdict: dict) -> None:
    try:
        Verdict.model_validate(verdict)
    except ValidationError as exc:
        raise CriteriaInputError("verdict_invalid") from exc
    if verdict_contract_errors(verdict):
        raise CriteriaInputError("verdict_invalid")


def evaluate_scene(
    scene_dir: Path,
    out_dir: Path,
    *,
    reads: Path | None = None,
    view_requests_path: Path | None = None,
    config: CriteriaConfig | None = None,
) -> dict[str, dict]:
    cfg = config or CriteriaConfig.from_env()
    scene_dir = Path(scene_dir)
    scene_path = scene_dir / "scene.json" if scene_dir.is_dir() else Path(scene_dir)
    scene_dir = scene_path.parent
    try:
        scene = json.loads(scene_path.read_text())
        _require_scene(scene)
    except CriteriaInputError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CriteriaInputError("scene_invalid") from exc
    anchor = scene.get("frame", {}).get("anchor", {}).get("element_id")
    meter = next((o for o in scene["objects"] if o["id"] == anchor), None)
    if meter is None or meter.get("class") != "meter":
        raise CriteriaInputError("no_meter_anchor")
    if not scene.get("chain", {}).get("wall_ids"):
        raise CriteriaInputError("meter_wall_missing")
    if meter.get("wall_id") not in {w["id"] for w in scene["walls"]}:
        raise CriteriaInputError("meter_wall_missing")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp_ground_rule_ids(scene)
    degraded: list[str] = []
    if cfg.ground_rule not in GROUND_DEG and not any(
        s.get("rule_id") == cfg.ground_rule for s in scene.get("coverage", {}).get("sensitivity") or []
    ):
        if cfg.ground_rule not in GROUND_DEG:
            raise CriteriaInputError("ground_rule_missing")
    sens = {s.get("rule_id") for s in scene.get("coverage", {}).get("sensitivity") or []}
    if sens and cfg.ground_rule not in sens and cfg.ground_rule in GROUND_DEG:
        # bands may still carry the default; from_scene_v0 raises KeyError only when sensitivity exists without the rule
        pass
    try:
        convert(scene, cfg.ground_rule, "C")
    except KeyError as exc:
        if str(exc).strip("'") == cfg.ground_rule or exc.args == (cfg.ground_rule,):
            raise CriteriaInputError("ground_rule_missing") from exc
        if exc.args == ("meter",):
            raise CriteriaInputError("no_meter_anchor") from exc
        raise
    query = _query(scene, scene_dir, degraded, allow_missing=cfg.allow_missing_occluders)
    read_doc = json.loads(Path(reads).read_text()) if reads else None
    read_facts = apply_reads(scene, read_doc)
    scale = _scale(scene_dir, scene)
    rules_sha = hashlib.sha256((HERE / "profiles.yaml").read_bytes()).hexdigest()
    timings = {}
    decidability = {}
    judge, judge_reasons, judge_record = resolve_judge(cfg)
    c2_report = {"status": "off", "agreement_with_c1": None, "vetoes": 0}
    c2_by_profile: dict[str, dict] = {}
    verdicts = {}
    t_all = time.time()
    for name in cfg.profiles:
        profile = merged_profile(name)
        t0 = time.time()
        scene3d = convert(scene, cfg.ground_rule, name)
        if scene3d["meta"].get("ground_rule_missing"):
            raise CriteriaInputError("ground_rule_missing")
        if scene3d["meta"].get("ground_grid_missing"):
            degraded.append("ground_grid_missing")
        sc = E.load_scene(scene3d)
        sm = chain_s(sc, scene)
        decided = E.decide(sc, profile, step=cfg.step_ft)
        for cand in decided["_cands"]:
            apply_review_flags(cand.checks, scene3d["meta"])
        # Grade every spot before runs are merged, so an observed spot is not
        # hidden behind an unobserved neighbour from the engine's own bands.
        regrade_runs(
            [{"cand": cand} for cand in decided["_cands"]],
            scene,
            scene_dir,
            profile,
            sc,
            sm,
            query,
            cfg.ground_rule,
        )
        runs = group_runs(decided["_cands"], cfg.step_ft)
        chain_doc = None
        if cfg.hybrid:
            chain_doc, code = solve_chain(scene, scene_path, profile, cfg.ground_rule, query)
            if code:
                degraded.append(code)
            (out_dir / f"c1.{name}.chain.json").write_text(json.dumps(chain_doc or {"skipped": code}) + "\n")
        decidability[name] = adopt(
            runs,
            chain_doc if cfg.hybrid else None,
            scene,
            scene_dir,
            profile,
            sc,
            sm,
            query,
            ground_rule=cfg.ground_rule,
        )
        if not cfg.hybrid:
            decidability[name]["by_engine"]["chain_pass"] = 0
            decidability[name]["by_engine"]["chain_fail"] = 0
        c2_spots: list[dict] = []
        run_c2 = cfg.c2 and (cfg.c2_profiles == "all" or name == cfg.default_profile)
        if run_c2:
            try:
                c2_spots, status = _run_c2(scene, scene_dir, profile, cfg.c2_budget_s)
            except Exception:
                log.exception("c2 failed")
                c2_spots, status = [], "skipped"
            if status == "no_coverage_grid":
                degraded.append("no_coverage_grid")
                downgrade_c1_passes_without_c2(runs)
                c2_report = {"status": "no_coverage_grid", "agreement_with_c1": None, "vetoes": 0}
            elif status == "no_observed_ground":
                degraded.append("no_observed_ground")
                for run in runs:
                    run["outcome"] = "unsure"
                c2_report = {"status": "no_observed_ground", "agreement_with_c1": None, "vetoes": 0}
            elif status == "skipped":
                for run in runs:
                    if run["outcome"] == "pass":
                        run["outcome"] = "unsure"
                        run["unsure"] = sorted(set(run["unsure"]) | {"c2_unavailable"})
                c2_report = {"status": "skipped", "agreement_with_c1": None, "vetoes": 0}
            else:
                stamped = c2_join(
                    runs,
                    [s for s in c2_spots if s.get("wall_dist_m", 99) <= 1.2],
                    sm,
                    width_ft=profile["battery"]["width_ft"],
                )
                c2_report = {
                    "status": "ok",
                    "agreement_with_c1": stamped["agreement_with_c1"],
                    "vetoes": stamped["vetoes"],
                }
                c2_by_profile[name] = {
                    "status": "ok",
                    "n_pass": sum(1 for s in c2_spots if s.get("outcome") == "pass"),
                    "n_spots": len(c2_spots),
                }
            if name not in c2_by_profile:
                c2_by_profile[name] = {
                    "status": c2_report["status"],
                    "n_pass": 0,
                    "n_spots": len(c2_spots),
                }
        _apply_scale(runs, scale, degraded)
        chosen = choose(runs)
        view_items = list((read_doc or {}).get("views_needed") or [])
        view_items.extend(_views_from_runs(runs, sm, scene))
        view_items.extend(coverage_requests(scene_dir))
        view_items.extend(view_requests(view_requests_path))
        views = _views_for_decision(
            chosen,
            build_views(
                view_items,
                cfg.max_views,
                meter_wall_id=scene["chain"]["wall_ids"][0],
                chain_wall_ids=scene["chain"]["wall_ids"],
            ),
        )
        if "no_keyframes" in degraded or "occluders_missing" in degraded:
            for run in runs:
                run["outcome"] = "unsure"
            fallback = _views_from_runs(runs, sm, scene) or [
                {
                    "kind": "band",
                    "band": "wall",
                    "wall_id": scene["chain"]["wall_ids"][0],
                    "s_m": [0, 1],
                    "criteria": ["placement"],
                    "why": "unobserved",
                }
            ]
            views = build_views(
                fallback,
                cfg.max_views,
                meter_wall_id=scene["chain"]["wall_ids"][0],
                chain_wall_ids=scene["chain"]["wall_ids"],
            )
        decision = "manual_review"
        reasons: list[str] = []
        if any(r["outcome"] == "pass" for r in runs) and (chosen and chosen["outcome"] == "pass"):
            decision = "pass" if profile["policy"].get("auto_approve", True) else "manual_review"
        elif any(r["outcome"] == "unsure" for r in runs) or not runs:
            decision = "manual_review"
        elif profile["policy"].get("allow_reject") and runs and all(r["outcome"] == "fail" for r in runs):
            open_ends = decided.get("open_ends_in_reach") or []
            if open_ends:
                decision = "manual_review"
                reasons.append("ends_not_limit")
            else:
                decision = "reject"
                reasons.append("all_spots_fail")
        else:
            decision = "manual_review"
        if chosen and chosen.get("c2_notes"):
            reasons.append("c2_veto")
            if decision == "pass":
                decision = "manual_review"
        if scale and float(scale.get("rel_bound_95") or 0) > 0.12:
            reasons.append("scale_bound_exceeded")
            decision = "manual_review"
        verdict = build_verdict(
            scene=scene,
            scene_path=scene_path,
            profile_name=name,
            profile=profile,
            runs=runs,
            chosen=chosen,
            sc=sc,
            sm=sm,
            query=query,
            scene_dir=scene_dir,
            reasons=reasons,
            decision=decision,
            runtime_s=time.time() - t0,
            rules_sha=rules_sha,
            ground_rule=cfg.ground_rule,
            ground_deg=GROUND_DEG.get(cfg.ground_rule),
            read_facts=read_facts,
            cfg=cfg,
            views=views,
            judge=judge,
            judge_reasons=judge_reasons,
        )
        if not cfg.c3 or not cfg.external_vision:
            for fact in disabled_facts():
                if fact["id"] not in {c["id"] for c in verdict["criteria"]}:
                    verdict["criteria"].append(fact)
        # Contract: pass/reject clear views. Re-apply after fact review.
        if verdict["site"]["decision"] in {"pass", "reject"}:
            verdict["views_needed"] = []
        _require_verdict(verdict)
        (out_dir / f"verdict.{name}.json").write_text(json.dumps(verdict, indent=1) + "\n")
        (out_dir / f"c1.{name}.raw.json").write_text(json.dumps(_raw(decided), indent=1) + "\n")
        (out_dir / f"placement.{name}.json").write_text(
            json.dumps(placement_doc(name, profile, chosen, sc, sm), indent=1) + "\n"
        )
        verdicts[name] = verdict
        timings[name] = round(time.time() - t0, 3)
    sweep = c2_sweep_document(c2_by_profile, cfg.default_profile)
    if cfg.default_profile in c2_by_profile:
        c2_report = {**c2_report, "status": sweep["status"]}
    live_c3 = bool(cfg.c3 and cfg.external_vision)
    fact_rows = [] if live_c3 else disabled_facts()
    (out_dir / "c2.sweep.json").write_text(json.dumps(sweep, indent=1) + "\n")
    (out_dir / "c3.facts.json").write_text(
        json.dumps({"status": "disabled" if not live_c3 else "off", "facts": fact_rows}) + "\n"
    )
    (out_dir / "c3.judge.json").write_text(json.dumps(judge_record) + "\n")
    report = {
        "profiles": list(cfg.profiles),
        "timings_s": {**timings, "total": round(time.time() - t_all, 3)},
        "degraded": sorted(set(degraded)),
        "decidability": decidability,
        "c2": c2_report,
        "c3": {
            "facts_calls": 0,
            "judge_calls": len(judge_record.get("calls") or []),
            "cost_usd": float(judge_record.get("cost_usd") or 0.0),
            "status": judge_record.get("status") or ("disabled" if not live_c3 else "idle"),
        },
        "downgrades": {
            "coverage": 0,
            "c2": c2_report.get("vetoes") or 0,
            "judge": 1 if judge not in {None, "pass"} else 0,
            "attrs": 0,
        },
        "ground_rule": cfg.ground_rule,
        "scale": scale,
        "provisional": "occluders_missing" in set(degraded),
    }
    (out_dir / "criteria_report.json").write_text(json.dumps(report, indent=1) + "\n")
    return verdicts


def _views_for_decision(chosen, views: list) -> list:
    """A chosen pass is not blocked by views from other runs."""
    if chosen and chosen.get("outcome") == "pass":
        return []
    return views


def _mark_check_unsure(run: dict, cid: str, reason: str) -> None:
    for check in getattr(run.get("cand"), "checks", []):
        if check.id != cid:
            continue
        if check.outcome == "pass" or (cid == "route_length" and check.outcome == "fail"):
            check.outcome = "unsure"
            check.cause = "margin"
            check.reason = (check.reason + "; " if check.reason else "") + reason
    run["unsure"] = sorted(set(run.get("unsure") or []) | {cid})
    run["failing"] = [item for item in (run.get("failing") or []) if item != cid]
    if run.get("outcome") == "pass" or (run.get("outcome") == "fail" and not run["failing"]):
        run["outcome"] = "unsure"


def _apply_scale(runs: list[dict], scale: dict | None, degraded: list[str]) -> None:
    """Route scale uncertainty. >0.12 blanks every pass; >0.06 blocks a 16–26 ft reach reject; >0.02 blocks opening passes."""
    if not scale:
        return
    bound = float(scale.get("rel_bound_95") or 0)
    if bound > 0.12:
        degraded.append("scale_bound_exceeded")
        for run in runs:
            for check in list(getattr(run.get("cand"), "checks", [])):
                if check.outcome == "pass":
                    _mark_check_unsure(run, check.id, "scale_bound_exceeded")
            if run.get("outcome") == "pass":
                run["outcome"] = "unsure"
        return
    if bound > 0.02:
        for run in runs:
            checks = getattr(run.get("cand"), "checks", [])
            for cid in ("opening_clearance", "under_window"):
                if any(c.id == cid and c.outcome == "pass" for c in checks):
                    _mark_check_unsure(run, cid, "scale_bound_exceeded")
    if bound > 0.06:
        for run in runs:
            if "route_length" not in (run.get("failing") or []):
                continue
            check = next((c for c in run["cand"].checks if c.id == "route_length"), None)
            measured = getattr(check, "measured", None) if check is not None else None
            if measured is None or not (16.0 <= float(measured) <= 26.0):
                continue
            _mark_check_unsure(run, "route_length", "scale_bound_exceeded")


def _raw(decided: dict) -> dict:
    raw = {k: v for k, v in decided.items() if k != "_cands"}
    return raw


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate a scene v0 with the criteria engine")
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profiles", nargs="*", default=None)
    ap.add_argument("--ground-rule", default=None)
    ap.add_argument("--reads", type=Path, default=None)
    ap.add_argument("--view-requests", type=Path, default=None)
    ap.add_argument("--no-c2", action="store_true")
    ap.add_argument("--no-c3", action="store_true")
    ap.add_argument(
        "--allow-missing-occluders",
        action="store_true",
        help="Debug only: score without laser/recon PLYs and mark the report provisional",
    )
    args = ap.parse_args()
    cfg = CriteriaConfig.from_env()
    if args.profiles:
        cfg = CriteriaConfig(**{**cfg.__dict__, "profiles": tuple(args.profiles)})
    if args.ground_rule:
        cfg = CriteriaConfig(**{**cfg.__dict__, "ground_rule": args.ground_rule})
    if args.no_c2:
        cfg = CriteriaConfig(**{**cfg.__dict__, "c2": False})
    if args.no_c3:
        cfg = CriteriaConfig(**{**cfg.__dict__, "c3": False})
    if args.allow_missing_occluders:
        cfg = CriteriaConfig(**{**cfg.__dict__, "allow_missing_occluders": True})
    evaluate_scene(
        args.scene if args.scene.is_dir() else args.scene.parent,
        args.out,
        reads=args.reads,
        view_requests_path=args.view_requests,
        config=cfg,
    )


if __name__ == "__main__":
    main()
