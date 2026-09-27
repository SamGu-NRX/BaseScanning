"""Cast WS-07 2D boxes onto chain walls and fill one ground region per ground band.

Brief steps 19 and 20. A box in two or more registered views is cast from the
view with the smallest incidence. ``plus_minus_m`` adds 0.20 m for sills and
lintels. Corner taps stay out: packet taps live in the capture frame, and this
stage does not reproject them.
"""

from __future__ import annotations

import numpy as np

from server.common.scaleio import as_pose
from server.scene.harness import load as load_harness

CAST_CLASSES = frozenset(
    {
        "window",
        "door",
        "garage_door",
        "basement_well",
        "panel",
        "wall_box",
        "vent",
        "disconnect",
        "gas_meter",
        "ac_unit",
    }
)
MATERIALS = frozenset(
    {
        "concrete",
        "gravel",
        "lawn",
        "dirt",
        "mulch",
        "pavers",
        "deck",
        "drive",
        "walkway",
        "pool",
        "water",
        "unknown",
    }
)
ATTRS = frozenset({"operable", "ground_level", "well", "movable", "text"})
# Box edges include sills and lintels (brief step 19).
DETECTOR_PM_M = 0.20
MIN_VIEWS = 2


def ground_material(
    votes: list[dict] | None,
    images: set[str] | None = None,
) -> tuple[str, str, float | None]:
    """Material, source kind, and confidence for one ground region.

    Two or more agreeing votes from registered frames select that material
    (``vlm``, the agreeing share). One vote or a tie stays ``unknown`` with
    ``reconstruction``. A missing vote list does the same.
    """
    if not votes:
        return "unknown", "reconstruction", None
    counts: dict[str, int] = {}
    for vote in votes:
        image = str(vote.get("image") or "")
        if images is not None and image not in images:
            continue
        material = str(vote.get("material") or "")
        if material not in MATERIALS:
            continue
        counts[material] = counts.get(material, 0) + 1
    if not counts:
        return "unknown", "reconstruction", None
    best = max(counts.values())
    winners = [name for name, count in counts.items() if count == best]
    if best >= 2 and len(winners) == 1:
        return winners[0], "vlm", round(best / sum(counts.values()), 4)
    return "unknown", "reconstruction", None


def apply_detections(scene: dict, payload: dict) -> None:
    """Append cast objects and replace ``ground.regions``. Wrong format is a no-op."""
    if payload.get("format") != "hss-objects/v0":
        return
    _cast_boxes(scene, payload)
    _ground_regions(scene, payload)
    notes = scene.setdefault("provenance", {}).setdefault("notes", [])
    note = f"objects_2d:{len(payload.get('objects') or [])}"
    if note not in notes:
        notes.append(note)


def _cast_boxes(scene: dict, payload: dict) -> None:
    frames = {str(frame["id"]): frame for frame in scene.get("keyframes") or []}
    if not frames or not scene.get("walls"):
        return
    chain = load_harness()["chain"].Chain(scene)
    existing = {str(obj.get("id")) for obj in scene.get("objects") or []}
    objects = list(scene.get("objects") or [])
    for index, obj in enumerate(payload.get("objects") or []):
        if str(obj.get("class") or "") not in CAST_CLASSES:
            continue
        registered = _registered_views(obj, frames)
        if len(registered) < MIN_VIEWS:
            continue
        candidates = []
        for view in registered:
            cast = _cast_view(chain, frames[str(view["image"])], view["box_px"])
            if cast is not None:
                candidates.append(cast)
        if not candidates:
            continue
        _incidence, wall, hits = min(candidates, key=lambda row: row[0])
        record = _wall_rect(obj, index, wall, hits, registered)
        if record["id"] in existing:
            continue
        existing.add(record["id"])
        objects.append(record)
    scene["objects"] = objects


def _ground_regions(scene: dict, payload: dict) -> None:
    ground = scene.get("ground")
    if not isinstance(ground, dict):
        return
    bands = [
        band
        for band in (scene.get("coverage") or {}).get("bands") or []
        if band.get("band") == "ground" and float(band.get("out_m") or 0.0) > 0.0
    ]
    images = {str(frame["id"]) for frame in scene.get("keyframes") or []}
    votes = payload.get("ground_type") if "ground_type" in payload else None
    material, kind, confidence = ground_material(votes, images)
    slope = _slope_deg(ground)
    try:
        chain = load_harness()["chain"].Chain(scene)
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        ground["regions"] = []
        return
    regions = []
    for index, band in enumerate(bands):
        wall = chain.by_id.get(band.get("wall_id"))
        if wall is None:
            continue
        s0, s1 = float(band["s_m"][0]), float(band["s_m"][1])
        if s1 < s0:
            s0, s1 = s1, s0
        out = float(band["out_m"])
        p0 = np.asarray(wall.plan_at(s0), float)
        p1 = np.asarray(wall.plan_at(s1), float)
        normal = np.asarray(wall.outward, float)
        corners = (p0, p1, p1 + normal * out, p0 + normal * out)
        region: dict = {
            "id": f"g{index + 1}",
            "material": material,
            "polygon_m": [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in corners],
            "evidence": "observed",
            "slope_deg": slope,
            "source": {"kind": kind},
        }
        if confidence is not None:
            region["confidence"] = confidence
        regions.append(region)
    ground["regions"] = regions


def _registered_views(obj: dict, frames: dict) -> list[dict]:
    rows = []
    for view in obj.get("views") or []:
        image = str(view.get("image") or "")
        box = view.get("box_px")
        if image not in frames or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        rows.append(view)
    return rows


def _cast_view(chain, frame: dict, box) -> tuple[float, object, dict[str, tuple[float, float]]] | None:
    u0, v0, u1, v1 = (float(v) for v in box)
    roles = {
        "left": (u0, (v0 + v1) / 2.0),
        "right": (u1, (v0 + v1) / 2.0),
        "bottom": ((u0 + u1) / 2.0, v1),
        "top": ((u0 + u1) / 2.0, v0),
    }
    pose = as_pose(frame["pose"])
    intrinsics = frame["intrinsics"]
    hits: dict[str, tuple[float, float]] = {}
    wall_ids: list[str] = []
    incidences: list[float] = []
    for role, (u, v) in roles.items():
        origin, direction = _ray(pose, intrinsics, u, v)
        hit = _closest_wall(chain, origin, direction)
        if hit is None:
            return None
        wall, along, height, incidence = hit
        hits[role] = (along, height)
        wall_ids.append(wall.id)
        incidences.append(incidence)
    if len(set(wall_ids)) != 1:
        return None
    return min(incidences), chain.by_id[wall_ids[0]], hits


def _closest_wall(chain, origin: np.ndarray, direction: np.ndarray):
    best = None
    for wall in chain.walls:
        hit = _hit_wall(wall, origin, direction)
        if hit is None:
            continue
        distance, along, height, incidence = hit
        if best is None or distance < best[0]:
            best = (distance, wall, along, height, incidence)
    if best is None:
        return None
    _distance, wall, along, height, incidence = best
    return wall, along, height, incidence


def _hit_wall(wall, origin: np.ndarray, direction: np.ndarray):
    normal = np.array([wall.outward[0], 0.0, wall.outward[1]])
    point = np.array([wall.p0[0], 0.0, wall.p0[1]])
    denom = float(direction @ normal)
    if abs(denom) < 1e-6:
        return None
    distance = float((point - origin) @ normal) / denom
    if distance <= 0.3:
        return None
    hit = origin + distance * direction
    if float((origin - hit) @ normal) <= 0.0:
        return None
    along = float(wall.s_of(np.array([hit[0], hit[2]])))
    if along < wall.s0 - 0.5 or along > wall.s1 + 0.5:
        return None
    cosine = float(np.clip(float((-direction) @ normal), -1.0, 1.0))
    incidence = float(np.degrees(np.arccos(cosine)))
    return distance, along, float(hit[1]), incidence


def _ray(pose: np.ndarray, intrinsics, u: float, v: float) -> tuple[np.ndarray, np.ndarray]:
    fx, fy, cx, cy = (float(value) for value in np.asarray(intrinsics, float).reshape(-1)[:4])
    direction = pose[:3, :3] @ np.array([(u - cx) / fx, -((v - cy) / fy), -1.0])
    norm = float(np.linalg.norm(direction))
    if norm < 1e-9:
        return pose[:3, 3], np.array([0.0, 0.0, -1.0])
    return pose[:3, 3], direction / norm


def _wall_rect(obj: dict, index: int, wall, hits: dict[str, tuple[float, float]], views: list[dict]) -> dict:
    s0, s1 = hits["left"][0], hits["right"][0]
    bottom, top = hits["bottom"][1], hits["top"][1]
    if s1 < s0:
        s0, s1 = s1, s0
    if top < bottom:
        bottom, top = top, bottom
    source_in = obj.get("source") if isinstance(obj.get("source"), dict) else {}
    copied = []
    for view in views:
        row: dict = {"image": str(view["image"]), "box_px": [float(value) for value in view["box_px"]]}
        if view.get("score") is not None:
            row["score"] = float(view["score"])
        copied.append(row)
    source: dict = {"kind": "detector", "views": copied}
    if source_in.get("model"):
        source["model"] = str(source_in["model"])
    confidence = float(obj.get("confidence") or 0.0)
    record = {
        "id": str(obj.get("id") or f"det-{index + 1}"),
        "class": str(obj["class"]),
        "geometry": {
            "kind": "wall_rect",
            "wall_id": wall.id,
            "s_m": [round(s0, 4), round(s1, 4)],
            "bottom_m": round(bottom, 4),
            "top_m": round(top, 4),
        },
        "wall_id": wall.id,
        "confidence": min(1.0, max(0.0, confidence)),
        "plus_minus_m": round(float(wall.plus_minus) + DETECTOR_PM_M, 3),
        "source": source,
        "evidence": "observed",
    }
    if obj.get("label"):
        record["label"] = str(obj["label"])
    attrs = obj.get("attrs")
    if isinstance(attrs, dict):
        kept = {key: value for key, value in attrs.items() if key in ATTRS}
        if kept:
            record["attrs"] = kept
    return record


def _slope_deg(ground: dict) -> float:
    surface = ground.get("surface") or {}
    normal = np.asarray(surface.get("normal") or [0.0, 1.0, 0.0], float)
    norm = float(np.linalg.norm(normal)) or 1.0
    cosine = float(np.clip(float(normal @ np.array([0.0, 1.0, 0.0])) / norm, -1.0, 1.0))
    return round(float(np.degrees(np.arccos(cosine))), 3)
