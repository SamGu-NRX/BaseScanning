"""Turn clusters and presence into hss-objects/v0 and scene Object dicts."""

from __future__ import annotations

from server.contracts.scene import SceneObject
from server.objects.config import PLANNED_USD, Cluster, ObjectsConfig
from server.objects.prompts import (
    ANCHOR_VIEWS_ONLY,
    FREE_STANDING,
    GROUND_MATERIAL,
    LABEL_OVERRIDE,
    OPENINGS,
    SCENE_CLASS,
    SIDECAR_ONLY,
    WALL_MOUNTED,
)

LOCATION_UNSURE_M = 10.0


def to_objects(clusters, presence, tier: str, scene: dict, cfg: ObjectsConfig) -> tuple[list[dict], list[dict]]:
    """Return (scene objects, view requests). Recognition confidence never sets plus_minus_m."""
    objects: list[dict] = []
    requests: list[dict] = []
    counts: dict[str, int] = {}
    existing = {obj.get("id") for obj in scene.get("objects", [])}
    anchor = _anchor(scene)
    low_reg = bool(scene.get("_low_registration"))
    for cluster in clusters:
        if cluster.cls in SIDECAR_ONLY or cluster.cls in ANCHOR_VIEWS_ONLY:
            continue
        scene_cls = SCENE_CLASS.get(cluster.cls)
        if scene_cls is None and cluster.cls in SCENE_CLASS.values():
            scene_cls = cluster.cls
        if scene_cls is None:
            continue
        counts[scene_cls] = counts.get(scene_cls, 0) + 1
        oid = f"obj_{scene_cls}_{counts[scene_cls]}"
        while oid in existing:
            counts[scene_cls] += 1
            oid = f"obj_{scene_cls}_{counts[scene_cls]}"
        existing.add(oid)
        confidence = _confidence(cluster, cfg)
        plus, evidence, unsure = _bound(cluster, tier, low_reg)
        geometry, wall_id = _geometry(cluster, scene_cls, unsure, anchor)
        if unsure:
            plus = LOCATION_UNSURE_M
            evidence = "inferred"
            requests.append(_view_request(oid, cluster))
        obj = {
            "id": oid,
            "class": scene_cls,
            "geometry": geometry,
            "confidence": confidence,
            "plus_minus_m": plus,
            "source": _source(cluster, cfg),
            "evidence": evidence,
        }
        if wall_id:
            obj["wall_id"] = wall_id
        label = LABEL_OVERRIDE.get(cluster.cls) or cluster.cls
        if label != scene_cls:
            obj["label"] = label
        objects.append(obj)
    return objects, requests


def objects_2d(candidates, cfg: ObjectsConfig) -> list[dict]:
    rows = []
    counts: dict[str, int] = {}
    for cand in candidates:
        if cand.cls in SIDECAR_ONLY:
            continue
        if cand.state == "drop":
            continue
        scene_cls = SCENE_CLASS.get(cand.cls, "other")
        counts[scene_cls] = counts.get(scene_cls, 0) + 1
        views = []
        entries = cand.all_boxes or [(fid, box, cand.scores.get(fid, 0.0)) for fid, box in cand.boxes.items()]
        for fid, box, score in entries:
            views.append({"image": fid, "box_px": _box_px_placeholder(box), "score": score, "_norm": box})
        if cand.grok_box and cand.best_frame:
            views.append(
                {
                    "image": cand.best_frame,
                    "box_px": _box_px_placeholder(cand.grok_box),
                    "score": cand.confidence,
                    "_norm": cand.grok_box,
                }
            )
        row = {
            "id": f"obj_{scene_cls}_{counts[scene_cls]}",
            "class": scene_cls,
            "label": cand.cls,
            "confidence": cand.confidence,
            "views": views,
        }
        rows.append(row)
    _ = cfg
    return rows


def finalize_box_px(rows: list[dict], sizes: dict[str, tuple[int, int]]) -> list[dict]:
    """Replace normalised boxes with pixel boxes once image sizes are known."""
    for row in rows:
        clean = []
        for view in row["views"]:
            norm = view.pop("_norm", None)
            w, h = sizes.get(view["image"], (0, 0))
            if norm and w and h:
                view["box_px"] = [norm[0] * w, norm[1] * h, norm[2] * w, norm[3] * h]
            clean.append({k: v for k, v in view.items() if k != "_norm"})
        row["views"] = clean
    return rows


def ground_votes(presence: dict) -> list[dict]:
    """SD-47. One material vote per frame that reported a B1 ground class."""
    votes = []
    for fid, row in presence.items():
        ground = None
        conf = 0.5
        for key in ("confirm", "cheap", "second"):
            part = row.get(key) or {}
            if part.get("ground_type"):
                ground = part["ground_type"]
                conf = 0.9 if key == "confirm" else 0.5
                break
        if ground is None and row.get("ground_type"):
            ground = row["ground_type"]
        material = GROUND_MATERIAL.get(ground or "", None)
        if material:
            votes.append({"image": fid, "material": material, "confidence": conf})
    return votes


def class_summary(presence: dict) -> dict:
    summary: dict[str, dict] = {}
    for fid, row in presence.items():
        primary = (row.get("confirm") or row.get("cheap") or {}).get("present") or {}
        second = (row.get("second") or {}).get("present") or {}
        for cls in dict.fromkeys([*primary, *second]):
            primary_ans = primary.get(cls)
            # A missing primary reader does not veto the vendor that answered.
            grok_yes = primary_ans not in {"yes", "no"} and second.get(cls) == "yes"
            if primary_ans != "yes" and not grok_yes:
                continue
            slot = summary.setdefault(cls, {"state": "present", "frames_yes": [], "best_frame": fid})
            if fid not in slot["frames_yes"]:
                slot["frames_yes"].append(fid)
            slot["best_frame"] = fid
    return summary


def validate_object(obj: dict) -> None:
    """Scene objects are `server.contracts.scene.SceneObject` (wire field `class`)."""
    SceneObject.model_validate(obj)


def _confidence(cluster: Cluster, cfg: ObjectsConfig) -> float:
    if cluster.method == "detector":
        return 0.5 if (cluster.views and max(v.get("score", 0) for v in cluster.views) >= 0.5) else 0.3
    if not cfg.external_vision:
        return 0.3
    # The cascade stored recognition confidence on the first view when present.
    if cluster.views and "confidence" in cluster.views[0]:
        return float(cluster.views[0]["confidence"])
    return 0.5


def _bound(cluster: Cluster, tier: str, low_reg: bool) -> tuple[float, str, bool]:
    n = len({v.get("frame") for v in cluster.views})
    unsure = n < 2 or cluster.method == "location_unsure"
    if cluster.method == "lidar" and n >= 2:
        bound, evidence = max(0.35, cluster.spread_m), "observed"
    elif cluster.method == "tap_lidar":
        bound, evidence = 0.10, "observed"
    elif cluster.method == "tap_wall":
        bound, evidence = (
            cluster.spread_m,
            "observed" if cluster.views and cluster.views[0].get("observed") else "inferred",
        )
    elif cluster.method == "wall_cast":
        bound, evidence = max(0.8, cluster.spread_m), "inferred"
    elif cluster.method == "triangulate":
        bound, evidence = max(1.0, cluster.spread_m), "inferred"
    else:
        bound, evidence, unsure = LOCATION_UNSURE_M, "inferred", True
    if low_reg:
        evidence = "inferred"
        bound = max(bound, 1.0)
    if tier == "photo_only" and cluster.method not in {"lidar", "tap_lidar"}:
        # Photo-flow placements that are not tied to a registered fragment stay unsure.
        if cluster.method == "location_unsure":
            unsure = True
    if cluster.cls in OPENINGS and cluster.method in {"lidar", "wall_cast"}:
        bound = bound + 0.2
    return bound, evidence, unsure


def _geometry(cluster: Cluster, scene_cls: str, unsure: bool, anchor: list[float]) -> tuple[dict, str | None]:
    if unsure:
        pos = [anchor[0] + 0.0, anchor[1] + 1.0, anchor[2] + 2.0]
        return {"kind": "point", "pos_m": pos}, None
    if scene_cls in {"ac_unit", "pool"} and cluster.method == "lidar":
        size = cluster.size_m or [0.8, 0.7, 0.8]
        return {"kind": "box", "center_m": cluster.pos_m, "size_m": size, "yaw_deg": 0}, cluster.wall_id
    if cluster.method == "wall_cast" and cluster.cls in WALL_MOUNTED | OPENINGS and cluster.s_m and cluster.wall_id:
        bottom = cluster.bottom_m if cluster.bottom_m is not None else 0.0
        top = cluster.top_m if cluster.top_m is not None else bottom + 1.0
        if top < bottom:
            bottom, top = top, bottom
        s0, s1 = cluster.s_m
        if s1 < s0:
            s0, s1 = s1, s0
        return {
            "kind": "wall_rect",
            "wall_id": cluster.wall_id,
            "s_m": [s0, s1],
            "bottom_m": bottom,
            "top_m": top,
        }, cluster.wall_id
    if cluster.cls in FREE_STANDING or cluster.method in {"triangulate", "lidar", "tap_lidar", "tap_wall"}:
        return {"kind": "point", "pos_m": cluster.pos_m}, cluster.wall_id
    return {"kind": "point", "pos_m": cluster.pos_m}, cluster.wall_id


def _source(cluster: Cluster, cfg: ObjectsConfig) -> dict:
    kind = (
        "lidar"
        if cluster.method in {"lidar", "tap_lidar"}
        else "tap"
        if cluster.method.startswith("tap")
        else "detector"
        if not cfg.external_vision
        else "vlm"
    )
    if cluster.method == "lidar":
        kind = "lidar"
    model = "owlv2-base" if not cfg.external_vision else "owlv2-base+gpt-5.5+grok-4.7"
    if cfg.second_reader == "off":
        model = "owlv2-base+gpt-5.5"
    views = []
    for view in cluster.views:
        image = view.get("frame") or view.get("image")
        if not isinstance(image, str) or not image:
            continue
        item: dict = {"image": image}
        box = view.get("box_px")
        if isinstance(box, list) and len(box) == 4:
            item["box_px"] = [float(v) for v in box]
        if "score" in view and view["score"] is not None:
            item["score"] = float(view["score"])
        views.append(item)
    return {"kind": kind, "model": model, "views": views}


def _view_request(oid: str, cluster: Cluster) -> dict:
    ref = cluster.views[0] if cluster.views else {}
    return {
        "object_id": oid,
        "class": SCENE_CLASS.get(cluster.cls, cluster.cls),
        "kind": "region",
        "wall_id": cluster.wall_id,
        "s_m": (cluster.s_m or [None])[0],
        "target": f"photo of the {cluster.cls.replace('_', ' ')} from 2-4 m, head-on, with the wall around it",
        "why": "location_unsure",
        "reference": {"image": ref.get("frame"), "box_px": ref.get("box_px")},
    }


def _anchor(scene: dict) -> list[float]:
    anchor = (scene.get("frame") or {}).get("anchor") or {}
    pos = anchor.get("position_m") or anchor.get("pos_m")
    if isinstance(pos, list) and len(pos) == 3:
        return [float(v) for v in pos]
    for obj in scene.get("objects", []):
        if obj.get("id") == "meter" or obj.get("class") == "meter":
            geom = obj.get("geometry") or {}
            if "pos_m" in geom:
                return list(geom["pos_m"])
            if "center_m" in geom:
                return list(geom["center_m"])
    return [0.0, 0.0, 0.0]


def _box_px_placeholder(box: list[float]) -> list[float]:
    return list(box)


def planned_total(calls: dict[str, int]) -> float:
    return sum(PLANNED_USD.get(model, 0.0) * n for model, n in calls.items())
