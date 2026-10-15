"""Copy WS-07 scene-frame objects onto a scene. Idempotent."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from server.scene.errors import SceneError
from server.scene.harness import load as load_harness


def _center(obj: dict) -> np.ndarray | None:
    geom = obj.get("geometry") or {}
    if geom.get("kind") == "point":
        return np.asarray(geom["pos_m"], float)
    if geom.get("kind") == "wall_rect":
        return np.array(
            [
                float(np.mean(geom.get("s_m", [0, 0]))),
                float(np.mean([geom.get("bottom_m", 0), geom.get("top_m", 0)])),
                0.0,
            ]
        )
    return None


def _same_place(a: dict, b: dict, tol: float = 0.3) -> bool:
    if a.get("class") != b.get("class"):
        return False
    ca, cb = _center(a), _center(b)
    if ca is None or cb is None:
        return False
    ga, gb = a.get("geometry") or {}, b.get("geometry") or {}
    if ga.get("kind") == "wall_rect" and gb.get("kind") == "wall_rect" and ga.get("wall_id") == gb.get("wall_id"):
        return abs(ca[0] - cb[0]) <= tol and abs(ca[1] - cb[1]) <= tol
    return float(np.linalg.norm(ca - cb)) <= tol


def _replaced_by(prev: dict, incoming: list[dict]) -> bool:
    """A detector or VLM object leaves only when an incoming object takes its place or its id."""
    kind = (prev.get("source") or {}).get("kind")
    if kind not in {"detector", "vlm"}:
        return False
    for obj in incoming:
        if prev.get("id") and prev.get("id") == obj.get("id"):
            return True
        if _same_place(prev, obj):
            return True
    return False


def merge_object_lists(scene: dict, objects_3d: list[dict]) -> dict:
    """Append incoming objects. Drop a detector or VLM twin that they replace.

    An empty incoming list leaves the scene objects in place. A detector that
    does not share a place or an id with the incoming list stays.
    """
    incoming = list(objects_3d)
    kept = [obj for obj in scene.get("objects", []) if not _replaced_by(obj, incoming)]
    scene["objects"] = kept + incoming
    return scene


def merge_objects(scene_path: Path, objects_3d_path: Path) -> Path:
    scene_path = Path(scene_path)
    payload = json.loads(Path(objects_3d_path).read_text())
    if payload.get("format") != "hss-objects-3d/v0":
        raise SceneError("input_missing:objects_3d", "objects_3d.json format must be hss-objects-3d/v0")
    scene = json.loads(scene_path.read_text())
    merge_object_lists(scene, list(payload.get("objects") or []))
    load_harness()["sceneio"].save_json(scene, scene_path)
    return scene_path
