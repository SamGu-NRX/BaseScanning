"""Coverage-driven ``views_needed`` for chain walls, ground strips and open ends."""

from __future__ import annotations

import numpy as np

from server.common.params import SceneParams

WALL_ANGLE = [0, 30]
GROUND_ANGLE = [20, 60]


def _stations(s0: float, s1: float, step: float = 0.25) -> np.ndarray:
    if s1 - s0 < step:
        return np.array([(s0 + s1) / 2.0])
    return np.arange(s0 + step / 2.0, s1, step)


def _covered(stations: np.ndarray, spans: list[list[float]]) -> np.ndarray:
    ok = np.zeros(len(stations), dtype=bool)
    for a, b in spans:
        ok |= (stations >= min(a, b)) & (stations <= max(a, b))
    return ok


def _spans_of(stations: np.ndarray, bad: np.ndarray, step: float = 0.25) -> list[list[float]]:
    spans: list[list[float]] = []
    start = None
    prev = None
    for s, flag in zip(stations, bad, strict=True):
        if flag and start is None:
            start = s
        if flag:
            prev = s
        elif start is not None:
            spans.append([start - step / 2.0, prev + step / 2.0])
            start = None
    if start is not None and prev is not None:
        spans.append([start - step / 2.0, prev + step / 2.0])
    return [span for span in spans if span[1] - span[0] >= 0.5]


def framing_ground_request(scene: dict) -> dict:
    """A7 framing trigger: wall base was not observed, so ask for it on the meter wall."""
    wid = _meter_wall_id(scene)
    walls = {w["id"]: w for w in scene["walls"]}
    wall = walls[wid]
    s0, s1 = float(wall["s_range_m"][0]), float(wall["s_range_m"][1])
    center = _meter_s(scene, wall)
    a, b = max(s0, center - 2.0), min(s1, center + 2.0)
    if b < a:
        a, b = s0, s1
    return {
        "id": f"vn-cov-{wid}-ground-datum",
        "kind": "band",
        "band": "ground",
        "wall_id": wid,
        "s_m": [round(a, 3), round(b, 3)],
        "target": "from 3 m out, hold the phone level so the bottom of the wall and at least 1 m of ground are in frame",
        "criteria": [],
        "why": "ground datum: wall base not observed",
        "view_angle_deg": list(GROUND_ANGLE),
        "_meter": True,
        "_length": 1.0e6,
    }


def views_needed(
    scene: dict, bands: list[dict], params: SceneParams, *, ground_base_unobserved: bool = False
) -> list[dict]:
    walls = {w["id"]: w for w in scene["walls"]}
    meter_id = _meter_wall_id(scene)
    requests: list[dict] = []
    for wid in scene["chain"]["wall_ids"]:
        wall = walls[wid]
        s0, s1 = wall["s_range_m"]
        stations = _stations(float(s0), float(s1))
        wall_spans = [b["s_m"] for b in bands if b.get("wall_id") == wid and b.get("band") == "wall"]
        bad = ~_covered(stations, wall_spans)
        for b in bands:
            if b.get("wall_id") != wid or b.get("band") != "wall":
                continue
            views = b.get("views")
            inc = b.get("best_incidence_deg")
            if views is not None and int(views) < 2:
                bad |= _covered(stations, [b["s_m"]])
            if inc is not None and float(inc) > 75.0:
                bad |= _covered(stations, [b["s_m"]])
        for k, (a, b0) in enumerate(_spans_of(stations, bad)):
            requests.append(
                {
                    "id": f"vn-cov-{wid}-{k}",
                    "kind": "band",
                    "band": "wall",
                    "wall_id": wid,
                    "s_m": [round(a, 3), round(b0, 3)],
                    "target": (
                        f"head-on view of wall {wid} between s = {a:.2f} m and {b0:.2f} m: "
                        "stand 3–5 m out, camera within 30° of the wall normal, wall filling most of the frame"
                    ),
                    "criteria": [],
                    "why": "coverage: unobserved",
                    "view_angle_deg": list(WALL_ANGLE),
                    "_meter": wid == meter_id,
                    "_length": b0 - a,
                }
            )
        ground_spans = [b["s_m"] for b in bands if b.get("wall_id") == wid and b.get("band") == "ground"]
        missing = ~_covered(stations, ground_spans)
        for k, (a, b0) in enumerate(_spans_of(stations, missing)):
            requests.append(
                {
                    "id": f"vn-cov-{wid}-ground-{k}",
                    "kind": "band",
                    "band": "ground",
                    "wall_id": wid,
                    "s_m": [round(a, 3), round(b0, 3)],
                    "out_m": 3.7,
                    "target": f"look down at the ground in front of wall {wid} from 2 m and 4 m out, camera about 45° down",
                    "criteria": [],
                    "why": "coverage: ground strip unobserved",
                    "view_angle_deg": list(GROUND_ANGLE),
                    "_meter": wid == meter_id,
                    "_length": b0 - a,
                }
            )
    for side in ("left", "right"):
        end = scene["chain"]["ends"][side]
        if end.get("kind") != "unexplored":
            continue
        wid = scene["chain"]["wall_ids"][0 if side == "left" else -1]
        requests.append(
            {
                "id": f"vn-cov-{wid}-past-{side}",
                "kind": "past_end",
                "side": side,
                "wall_id": wid,
                "target": f"walk past the {side} end of wall {wid} and photograph the adjoining wall head-on",
                "criteria": [],
                "why": "coverage: end unexplored",
                "view_angle_deg": list(WALL_ANGLE),
                "_meter": wid == meter_id,
                "_length": 1.0,
            }
        )
    if ground_base_unobserved:
        requests.append(framing_ground_request(scene))
    requests.sort(key=lambda r: (not r["_meter"], -r["_length"]))
    kept = requests[: params.views_needed_max]
    for row in kept:
        row.pop("_meter", None)
        row.pop("_length", None)
    return kept


def _meter_s(scene: dict, wall: dict) -> float:
    for obj in scene.get("objects", []):
        if obj.get("class") != "meter":
            continue
        geom = obj.get("geometry") or {}
        span = geom.get("s_m")
        if isinstance(span, (list, tuple)) and len(span) == 2:
            return 0.5 * (float(span[0]) + float(span[1]))
        pos = geom.get("pos_m")
        if pos is not None and len(pos) >= 3:
            (x0, z0), (x1, z1) = wall["baseline_m"]
            direction = np.array([x1 - x0, z1 - z0], float)
            length = float(np.linalg.norm(direction)) or 1.0
            direction /= length
            return float((np.array([pos[0], pos[2]]) - np.array([x0, z0])) @ direction)
    s0, s1 = wall["s_range_m"]
    return 0.5 * (float(s0) + float(s1))


def _meter_wall_id(scene: dict) -> str:
    for obj in scene.get("objects", []):
        if obj.get("class") == "meter" and obj.get("wall_id"):
            return obj["wall_id"]
    for wall in scene["walls"]:
        s0, s1 = wall.get("s_range_m", [0, 0])
        if s0 <= 0.0 <= s1:
            return wall["id"]
    return scene["chain"]["wall_ids"][0]
