"""Fill 3D-engine unsure cells from the PR #11 chain solver. A chain pass never lifts a candidate."""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import yaml

from server.criteria.c1.from_scene_v0 import stamp_ground_rule_ids
from server.criteria.c1.to_verdict import _span, coverage_status
from server.criteria.harness import load

M = 0.3048


def chain_inputs_observed(scene: dict) -> bool:
    anchor = scene.get("frame", {}).get("anchor", {}).get("element_id")
    meter = next((o for o in scene["objects"] if o["id"] == anchor or o["class"] == "meter"), None)
    if meter is None or meter.get("evidence") != "observed":
        return False
    walls = {w["id"]: w for w in scene["walls"]}
    for wid in scene["chain"]["wall_ids"]:
        if walls.get(wid, {}).get("evidence") != "observed":
            return False
    opening = {"window", "door", "garage_door", "basement_well", "gate"}
    for o in scene["objects"]:
        wid = o.get("wall_id") or o.get("geometry", {}).get("wall_id")
        if o["class"] in opening and wid in walls and wid in scene["chain"]["wall_ids"]:
            if o.get("evidence") != "observed":
                return False
    return True


def _vendor_modules(h):
    server_dir = next(
        p
        for p in (
            Path(h.hs11.__file__).resolve().parents[2] / "vendor" / "hs11_server",
            Path(h.hs11.__file__).resolve().parents[1] / "vendor" / "hs11_server",
        )
        if p.is_dir()
    )
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    return importlib.import_module("rules"), importlib.import_module("scene"), importlib.import_module("solver")


def _rules_file(profile: dict) -> Path:
    h = load()
    here = Path(h.hs11.__file__).resolve()
    candidates = [
        here.parents[2] / "vendor" / "hs11_server" / "rules.yaml",
        here.parents[1] / "vendor" / "hs11_server" / "rules.yaml",
        here.parents[3] / "vendor" / "hs11_server" / "rules.yaml",
    ]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        raise FileNotFoundError("vendored hs11 rules.yaml")
    rules = yaml.safe_load(src.read_text())
    b = profile["battery"]
    rules["battery"]["width_ft"]["value"] = b["width_ft"]
    rules["battery"]["depth_ft"]["value"] = b["depth_ft"]
    rules["battery"]["height_ft"]["value"] = b["height_ft"]
    cl = profile["clearances"]
    for key, dest in (
        ("gas_ft", "gas_ft"),
        ("ac_ft", "ac_ft"),
        ("drive_ft", "drive_ft"),
        ("pool_ft", "pool_ft"),
        ("wall_equipment_ft", "wall_equipment_ft"),
    ):
        if cl.get(key) is not None and dest in rules["clearances"]:
            rules["clearances"][dest]["value"] = float(cl[key])
    op = profile["openings"]
    rules["openings"]["exempt_bottom_above_ft"] = op.get("ground_level_max_bottom_ft")
    if "opening_ft" in rules["clearances"] and op.get("min_ft") is not None:
        rules["clearances"]["opening_ft"]["value"] = float(op["min_ft"])
    if profile["facing"].get("min_ft") is not None:
        rules["facing"]["min_ft"]["value"] = float(profile["facing"]["min_ft"])
        rules["facing"]["measured_from"] = profile["facing"].get("measured_from", "wall")
    if profile["headroom"].get("min_ft") is not None:
        rules["headroom"]["min_ft"]["value"] = float(profile["headroom"]["min_ft"])
    rules["route"]["max_ft"]["value"] = float(profile["route"]["max_ft"])
    rules["route"]["confident_reach_ft"]["value"] = float(profile["route"]["confident_reach_ft"])
    # The vendored solver's GroundType has no `dirt`. B/C/S allow it in the 3D engine.
    # Dropping it here lets the chain solver load; dirt stays a 3D-engine material.
    rules_mod, _, _ = _vendor_modules(h)
    vendor_ground = set(rules_mod.GroundType.__args__)
    rules["ground"]["allowed"] = [g for g in profile["ground"]["allowed"] if g in vendor_ground]
    rules["policy"]["id"] = profile["id"]
    rules["policy"]["allow_reject"] = bool(profile["policy"]["allow_reject"])
    rules["policy"]["auto_approve"] = bool(profile["policy"].get("auto_approve", True))
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, prefix="hss-rules-")
    yaml.safe_dump(rules, tmp)
    tmp.close()
    return Path(tmp.name)


def _sort_spans(raw: dict) -> dict:
    for obj in raw.get("objects", []):
        span = obj.get("span_ft")
        if span and len(span) == 2 and span[0] > span[1]:
            obj["span_ft"] = [span[1], span[0]]
    return raw


def solve_chain(
    scene: dict, scene_path: Path, profile: dict, ground_rule: str, query
) -> tuple[dict | None, str | None]:
    """Return (chain verdict, degraded code)."""
    if not chain_inputs_observed(scene):
        return None, "chain_inputs_unobserved"
    h = load()
    if h.hs11 is None:
        return None, "chain_solver_failed"
    try:
        rules_path = _rules_file(profile)
        stamp_ground_rule_ids(scene)
        raw = _sort_spans(h.hs11.to_hs11(scene, ground_rule))
        # The vendored solver is a sibling package, imported by path so a reversed span
        # is sorted here before parse_scene sees it.
        rules_mod, scene_mod, solver_mod = _vendor_modules(h)
        loaded = rules_mod.load_rules(rules_path)
        result = solver_mod.solve(scene_mod.parse_scene(raw, loaded.rules), loaded)
        verdict = h.hs11.to_verdict(result, scene, scene_path, raw, loaded, query, ground_rule, 0.0)
        verdict["evaluator"]["path"] = "hs11-chain"
        return verdict, None
    except Exception:
        return None, "chain_solver_failed"


def _overlap(a: list[float], b: list[float]) -> bool:
    return min(a[1], b[1]) - max(a[0], b[0]) >= -0.05


def adopt(
    runs: list[dict],
    chain: dict | None,
    scene,
    scene_dir,
    profile,
    sc,
    sm,
    query,
    ground_rule: str | None = None,
) -> dict:
    """Mutate runs. Chain fail with observed chain evidence is adopted. Chain pass is counted only."""
    del scene, scene_dir, sc, query
    stats = {"engine3d_pass": 0, "engine3d_fail": 0, "chain_pass": 0, "chain_fail": 0}
    cells_total = 0
    if chain is None:
        for run in runs:
            ids = set(run["failing"]) | set(run["unsure"]) | _passed(run)
            cells_total += len(ids)
            stats["engine3d_pass"] += len(_passed(run))
            stats["engine3d_fail"] += len(run["failing"])
        decided = stats["engine3d_pass"] + stats["engine3d_fail"]
        return {
            "cells_total": cells_total,
            "cells_decided": decided,
            "share": _share(decided, cells_total),
            "by_engine": stats,
        }

    by_wall: dict[str, list[dict]] = {}
    for cand in chain.get("candidates") or []:
        by_wall.setdefault(cand["wall_id"], []).append(cand)
    for run in runs:
        span = _span(sm, run["wall"], run["u0"], run["u1"] + profile["battery"]["width_ft"])
        match = next((c for c in by_wall.get(run["wall"], []) if _overlap(span, c["start_s_m"])), None)
        causes = run.get("causes") or {}
        still_unsure = []
        adopted_fail = list(run["failing"])
        for cid in run["unsure"]:
            cause = causes.get(cid)
            if match is None or cause not in {"unobserved", "grazing"}:
                still_unsure.append(cid)
                continue
            chain_fail = cid in (match.get("failing") or [])
            chain_unsure = cid in (match.get("unsure") or [])
            # A cell the sweep passed stays a pass even when another check failed the candidate.
            chain_pass = not chain_fail and not chain_unsure
            if chain_pass and _chain_cell_observed(chain, match, cid, ground_rule):
                stats["chain_pass"] += 1
                still_unsure.append(cid)
                continue
            if chain_fail and _chain_cell_observed(chain, match, cid, ground_rule):
                adopted_fail.append(cid)
                stats["chain_fail"] += 1
                run.setdefault("chain_reasons", []).append(cid)
                continue
            still_unsure.append(cid)
        run["failing"] = sorted(set(adopted_fail))
        run["unsure"] = sorted(set(still_unsure))
        if run["outcome"] != "pass":
            if run["failing"]:
                run["outcome"] = "fail"
            else:
                run["outcome"] = "unsure"
        ids = set(run["failing"]) | set(run["unsure"]) | _passed(run)
        cells_total += max(len(ids), 1)
        stats["engine3d_pass"] += len(_passed(run))
        stats["engine3d_fail"] += len([c for c in run["failing"] if c not in (run.get("chain_reasons") or [])])
    decided = stats["engine3d_pass"] + stats["engine3d_fail"] + stats["chain_pass"] + stats["chain_fail"]
    return {
        "cells_total": cells_total,
        "cells_decided": decided,
        "share": _share(decided, cells_total),
        "by_engine": stats,
    }


def _cov_of(row: dict) -> list[dict]:
    measured = row.get("measured") or {}
    fact = row.get("fact") or {}
    return list(measured.get("coverage") or fact.get("coverage") or [])


def _chain_cell_coverage(chain: dict, match: dict, cid: str) -> list[dict]:
    """Coverage on this chain cell. Missing rows are not observed."""
    for row in match.get("criteria") or []:
        if row.get("id") == cid:
            return _cov_of(row)
    chosen = (chain.get("site") or {}).get("chosen_candidate")
    match_id = match.get("id")
    for row in chain.get("criteria") or []:
        if row.get("id") != cid:
            continue
        cand = row.get("candidate_id")
        if match_id and cand and cand not in {match_id, chosen, "spot"}:
            continue
        if chosen and match_id and match_id not in {chosen, "spot"} and cand not in {None, match_id}:
            continue
        return _cov_of(row)
    return []


def _chain_cell_observed(chain: dict, match: dict, cid: str, ground_rule: str | None) -> bool:
    cov = _chain_cell_coverage(chain, match, cid)
    if not cov:
        return False
    return all(coverage_status(ev, ground_rule) == "observed" for ev in cov)


def _passed(run) -> set[str]:
    known = set(run["failing"]) | set(run["unsure"])
    return {c.id for c in run["cand"].checks if c.id not in known and c.outcome == "pass"}


def _share(decided: int, total: int) -> float:
    return round(decided / total, 4) if total else 0.0
