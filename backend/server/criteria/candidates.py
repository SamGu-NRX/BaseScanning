"""Chosen spot and the placement sidecar WS-10 reads."""

from __future__ import annotations

import math

from server.criteria.c1.to_verdict import _span
from server.criteria.lattice import margin_outcome

M = 0.3048


def choose(runs: list[dict]) -> dict | None:
    if not runs:
        return None
    passes = [r for r in runs if r["outcome"] == "pass"]
    pool = passes or runs
    if passes:
        return min(passes, key=lambda r: (_route(r), r["u0"]))
    return min(pool, key=lambda r: (len(r.get("unsure") or []), _route(r)))


def _margin_over_uncertainty(check) -> float | None:
    if check.measured is None or check.threshold is None:
        return None
    pm = float(check.pm or 0.0)
    if pm <= 0:
        return None
    comparison = "at_most" if check.id == "route_length" else "at_least"
    _outcome, _margin, mou = margin_outcome(float(check.measured), pm, float(check.threshold), comparison)
    if mou is None:
        return None
    return round(mou, 3)


def _route(run) -> float:
    route = getattr(run.get("cand"), "route", None)
    if route is None:
        return 1e9
    return float(route.length)


def placement_doc(profile_name: str, profile: dict, run: dict | None, sc, sm) -> dict:
    if run is None:
        return {"profile": profile_name, "chosen_candidate": None}
    wall = sc.walls[run["wall"]]
    battery = profile["battery"]
    yaw = math.degrees(math.atan2(-wall.along[1], wall.along[0]))
    # wall.pt is plan feet. The box centre sits d/2 + 0.02 m out from the face.
    spot_u = run.get("spot_u", run["u0"])
    out_ft = battery["depth_ft"] / 2 + 0.02 / M
    centre = wall.pt(spot_u + battery["width_ft"] / 2, out_ft)
    center_m = [centre[0] * M, (battery["height_ft"] * M) / 2, centre[1] * M]
    route = []
    if run["cand"].route.stretches:
        for wid, a, b in run["cand"].route.stretches:
            w = sc.walls[wid]
            for u in (a, b):
                p = w.pt(u, 0.02 / M)
                route.append([round(p[0] * M, 4), round(1.0 * M, 4), round(p[1] * M, 4)])
    return {
        "profile": profile_name,
        "chosen_candidate": run.get("id"),
        "start_s_m": _span(sm, run["wall"], spot_u, spot_u)[0],
        "box_scene_m": {
            "center_m": [round(v, 4) for v in center_m],
            "size_m": [
                round(battery["width_ft"] * M, 4),
                round(battery["height_ft"] * M, 4),
                round(battery["depth_ft"] * M, 4),
            ],
            "yaw_deg": round(yaw, 3),
        },
        "route_polyline_scene_m": route,
        "route_length_ft": round(_route(run), 3),
        "checks_at_spot": [
            {"id": c.id, "outcome": c.outcome, "margin_over_uncertainty": _margin_over_uncertainty(c)}
            for c in run["cand"].checks
        ],
    }
