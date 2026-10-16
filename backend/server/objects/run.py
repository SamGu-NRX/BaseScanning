"""Stage entry points. `run` is 2D (after validate); `run_3d` places objects after the scene exists."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import StageResult
from server.objects.config import PLANNED_USD, Cluster, FrameRef, ObjectsConfig, ObjectsError, effective_budget
from server.objects.confirm import Budget, run_cascade, stored_gate_thresholds
from server.objects.device import memory_snapshot, resolve_device
from server.objects.emit import class_summary, finalize_box_px, ground_votes, objects_2d, to_objects
from server.objects.gate import release_gates
from server.objects.prompts import WS08_PURPOSES, is_free_standing, row_detector_class
from server.objects.proposals import run_gdino, run_owlv2
from server.objects.select import always_still, select_frames
from server.objects.vlm import LiveSpend, VisionClient

_OPENING_LABELS = frozenset({"window", "door"})
_OBJECTS_2D_KEYS = (
    "objects/objects.json",
    "objects/presence.json",
    "objects/frame_scores.json",
    "objects/detections.jsonl",
    "objects/report.json",
)
_FILE_ROLES = {"keyframe": "keyframe", "still": "still"}


def run(ctx) -> StageResult:
    cfg = ObjectsConfig.from_env(getattr(ctx, "settings", None))
    work = Path(ctx.work_dir)
    # WS-03 hydrate_packet writes packet.json plus committed files into packet_dir.
    # A stub packet/normalized.json can land in that same directory; it is not the frame list.
    packet_dir = _packet_dir(ctx)
    packet = _load_packet(packet_dir)
    if packet is None or packet_dir is None:
        raise ObjectsError("inputs_missing:packet")
    frames = _frames(packet, packet_dir)
    if not frames:
        raise ObjectsError("inputs_missing:frames")
    result = run_2d(
        frames,
        packet,
        work / "objects",
        cfg,
        vlm_budget_usd=getattr(ctx, "vlm_budget_usd", None),
        heartbeat=getattr(ctx, "heartbeat", None),
    )
    return _wrap(
        {key: key for key in _OBJECTS_2D_KEYS},
        result.metrics,
        result.warnings,
    )


def run_3d(ctx) -> StageResult:
    cfg = ObjectsConfig.from_env(getattr(ctx, "settings", None))
    work = Path(ctx.work_dir)
    # WS-00 §3.9 keys, hydrated into the stage work dir. SD-17 does not put them on ctx.inputs.
    scene_path = work / "scene" / "scene.json"
    objects_path = work / "objects" / "objects.json"
    if not scene_path.is_file():
        raise ObjectsError("inputs_missing:scene")
    if not objects_path.is_file():
        raise ObjectsError("inputs_missing:objects")
    scene = json.loads(scene_path.read_text())
    doc = json.loads(objects_path.read_text())
    reg_path = work / "poses" / "registration_report.json"
    if reg_path.is_file():
        report = json.loads(reg_path.read_text())
        if float(report.get("registered_fraction", 1.0)) < 0.6:
            scene = {**scene, "_low_registration": True}
    tier = str(getattr(ctx, "tier", None) or "arkit")
    out = work / "objects"
    out.mkdir(parents=True, exist_ok=True)
    clusters = _clusters_from_2d(doc, scene, tier)
    objects, requests = to_objects(clusters, {}, tier, scene, cfg)
    path = out / "objects_3d.json"
    path.write_text(json.dumps({"format": "hss-objects-3d/v0", "objects": objects}, indent=1))
    # §3.9 keys. hydrate_artifacts keeps these names and drops a bare objects_3d.json (I-20, I-18).
    objects_3d_key = "objects/objects_3d.json"
    view_requests_key = "objects/view_requests.json"
    (work / view_requests_key).write_text(json.dumps(requests, indent=1))
    observed = sum(1 for o in objects if o["evidence"] == "observed")
    return _wrap(
        {objects_3d_key: objects_3d_key, view_requests_key: view_requests_key},
        {"objects_emitted": float(len(objects)), "observed": float(observed), "vlm_cost_usd": 0.0, "vlm_calls": 0.0},
        [],
    )


def run_2d(
    frames: list[FrameRef],
    packet: dict,
    out: Path,
    cfg: ObjectsConfig,
    *,
    vlm_budget_usd: float | None,
    heartbeat=None,
    scores=None,
    detections=None,
    live_spend: LiveSpend | None = None,
) -> StageResult:
    """Steps 2–9. `scores` and `detections` let tests skip the local models."""
    out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if heartbeat:
        heartbeat("objects:gate")
    frame_s: dict[str, dict[str, float]] = {}
    if scores is None:
        try:
            scores, frame_s = _embed_and_score(frames, cfg, heartbeat=heartbeat)
        except Exception as exc:  # noqa: BLE001 — a missing weight is a stage failure, not a degrade
            raise ObjectsError("gate_model_missing") from exc
    # Walkaround frames are the ones the gate ranks. Stills with a WS-08 purpose stay out of selection.
    rank_order = [f.id for f in frames if f.kind == "keyframe"]
    houses = _houses(frames, packet)
    purposes = {f.id: f.purpose or "" for f in frames}
    always = [f.id for f in frames if always_still(f.purpose)]
    sel = select_frames(
        scores,
        rank_order,
        k=cfg.top_k_per_class,
        always=always,
        houses=houses,
        purposes=purposes,
        max_selected=cfg.max_selected_frames,
    )
    # Selected stills that are not keyframes join the VLM set. WS-08 purposes never do.
    for frame in frames:
        if (
            frame.kind == "still"
            and frame.purpose not in WS08_PURPOSES
            and frame.id in always
            and frame.id not in sel.frames
        ):
            sel.frames.append(frame.id)
    (out / "frame_scores.json").write_text(json.dumps(_scores_doc(scores)))
    if detections is None:
        try:
            owl_s: dict[str, float] = {}
            detections = run_owlv2(frames, device=cfg.device, timing=owl_s, beat=heartbeat)
            frame_s["owlv2"] = owl_s
            if cfg.gdino_openings:
                detections = _merge_openings(detections, run_gdino(frames, device=cfg.device, beat=heartbeat))
        except ObjectsError:
            raise
        except Exception as exc:  # noqa: BLE001 — a missing weight is a stage failure, not an empty house
            raise ObjectsError("proposer_model_missing") from exc
    by_id = {f.id: f for f in frames}
    cap = effective_budget(cfg, vlm_budget_usd)
    budget = Budget(cap)
    client = None
    if cfg.external_vision:
        client = VisionClient(cfg, out / "vlm-cache", live_spend=live_spend)
    else:
        warnings.append("external_vision_disabled")
    gate_model = "siglip" if "siglip" in scores else "clip"
    thresholds = stored_gate_thresholds(gate_model)
    result = run_cascade(sel, by_id, client, budget, cfg, detections, gate_scores=scores, gate_thresholds=thresholds)
    warnings.extend(result.warnings)
    sizes = {f.id: (f.w, f.h) for f in frames}
    rows = finalize_box_px(objects_2d(result.candidates, cfg), sizes)
    doc = {"format": "hss-objects/v0", "objects": rows, "ground_type": ground_votes(result.presence)}
    (out / "objects.json").write_text(json.dumps(doc, indent=1))
    _write_detections(out / "detections.jsonl", frames, detections)
    presence_doc = {"frames": result.presence, "class_summary": class_summary(result.presence)}
    (out / "presence.json").write_text(json.dumps(presence_doc, indent=1))
    report = {
        "frames_total": len(frames),
        "frames_selected": len(sel.frames),
        "filtered_frac": sel.filtered_frac,
        "hazard_frames": sel.hazard_frames,
        "calls": budget.calls,
        "cost_usd": {**budget.cost_usd, "total": budget.spent},
        "planned_usd": {m: PLANNED_USD.get(m, 0.0) * n for m, n in budget.calls.items()},
        "cache_hits": client.cache_hits if client else 0,
        "skipped_stages": budget.skipped_stages,
        "gate_thresholds": thresholds,
        "objects": {"emitted": len(rows)},
        "warnings": warnings,
        "device": resolve_device(cfg.device),
        "frame_s": frame_s,
        "memory": memory_snapshot(),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1))
    return _wrap(
        {
            "objects.json": str(out / "objects.json"),
            "presence.json": str(out / "presence.json"),
            "frame_scores.json": str(out / "frame_scores.json"),
            "detections.jsonl": str(out / "detections.jsonl"),
            "report.json": str(out / "report.json"),
        },
        {
            "vlm_cost_usd": budget.spent,
            "vlm_calls": float(sum(budget.calls.values())),
            "filtered_frac": sel.filtered_frac,
            "objects_emitted": float(len(rows)),
        },
        warnings,
    )


def _clusters_from_2d(doc: dict, scene: dict, tier: str) -> list[Cluster]:
    """Place 2D rows with wall casts or triangulation when the scene has poses. Otherwise location_unsure."""
    rows = doc.get("objects") or []
    if tier in {"photo_only", "pose-free", "pose_free"}:
        return [_unsure_cluster(row) for row in rows]
    cams = _cams_from_scene(scene)
    if not cams or not scene.get("walls"):
        return [_unsure_cluster(row) for row in rows]
    from server.objects.lift import lift_triangulate, lift_wall_cast

    wall_dets: dict[str, list] = {}
    free_dets: dict[str, list] = {}
    conf_of: dict[str, float] = {}
    for row in rows:
        cls = row_detector_class(row)
        conf_of[cls] = float(row.get("confidence", 0.3))
        bucket = free_dets if is_free_standing(cls) else wall_dets
        for view in row.get("views") or []:
            fid = view.get("image")
            cam = cams.get(fid)
            box = view.get("box_px")
            if cam is None or not box or len(box) != 4 or not cam["w"] or not cam["h"]:
                continue
            norm = [box[0] / cam["w"], box[1] / cam["h"], box[2] / cam["w"], box[3] / cam["h"]]
            bucket.setdefault(fid, []).append({"label": cls, "score": float(view.get("score", 0)), "box": norm})
    placed: list[Cluster] = []
    if wall_dets:
        placed.extend(lift_wall_cast(wall_dets, cams, scene))
    if free_dets and len(cams) >= 2:
        placed.extend(lift_triangulate(free_dets, cams))
    for cluster in placed:
        conf = conf_of.get(cluster.cls, 0.5)
        for view in cluster.views:
            view["confidence"] = conf
    consumed = set()
    for cluster in placed:
        for view in cluster.views:
            box = view.get("box")
            fid = view.get("frame")
            if fid and isinstance(box, list) and len(box) == 4:
                consumed.add((cluster.cls, fid, _box_key(box)))
    for row in rows:
        views = row.get("views") or []
        if not views:
            placed.append(_unsure_cluster(row))
            continue
        cls = row_detector_class(row)
        for view in views:
            fid = view.get("image")
            box_px = view.get("box_px")
            cam = cams.get(fid) if fid else None
            if cam and isinstance(box_px, list) and len(box_px) == 4 and cam["w"] and cam["h"]:
                norm = [box_px[0] / cam["w"], box_px[1] / cam["h"], box_px[2] / cam["w"], box_px[3] / cam["h"]]
                if (cls, fid, _box_key(norm)) in consumed:
                    continue
            placed.append(_unsure_cluster({**row, "views": [view]}))
    return placed


def _box_key(box: list) -> tuple:
    return tuple(round(float(v), 3) for v in box)


def _det_label(det) -> str:
    return det["label"] if isinstance(det, dict) else det.label


def _merge_openings(owl: dict, gdino: dict) -> dict:
    """GDINO owns window and door. OWLv2 keeps every other class."""
    merged = {}
    for fid in set(owl) | set(gdino):
        kept = [det for det in owl.get(fid, []) if _det_label(det) not in _OPENING_LABELS]
        kept.extend(gdino.get(fid, []))
        merged[fid] = kept
    return merged


def _unsure_cluster(row: dict) -> Cluster:
    views = []
    for view in row.get("views") or []:
        views.append(
            {
                "frame": view.get("image"),
                "box_px": view.get("box_px"),
                "score": view.get("score", 0),
                "confidence": row.get("confidence", 0.3),
            }
        )
    cls = row_detector_class(row)
    return Cluster(cls, [0.0, 0.0, 0.0], views, 0.0, "location_unsure")


def _cams_from_scene(scene: dict) -> dict:
    cams = {}
    for kf in scene.get("keyframes") or []:
        pose = kf.get("pose") or kf.get("camera_to_scene")
        intr = kf.get("intrinsics")
        if pose is None or intr is None:
            continue
        cam = {"pose": pose, "intrinsics": intr, "w": int(kf.get("w") or 0), "h": int(kf.get("h") or 0)}
        cams[kf["id"]] = cam
        image = kf.get("image")
        if image:
            cams[Path(image).stem] = cam
    return cams


def _write_detections(path: Path, frames: list[FrameRef], detections: dict) -> None:
    lines = []
    for frame in frames:
        rows = []
        for det in detections.get(frame.id, []):
            if isinstance(det, dict):
                rows.append({"label": det["label"], "score": det["score"], "box": det["box"]})
            else:
                rows.append({"label": det.label, "score": det.score, "box": det.box})
        lines.append(
            json.dumps(
                {
                    "id": frame.id,
                    "model": "owlv2" if rows else "none",
                    "seconds": 0.0,
                    "size": [frame.w, frame.h],
                    "dets": rows,
                }
            )
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def _embed_and_score(frames: list[FrameRef], cfg: ObjectsConfig, *, heartbeat=None) -> tuple[dict, dict]:
    from server.objects.gate import class_scores, embed_frames

    scores = {}
    frame_s: dict[str, dict[str, float]] = {}
    usable = [f for f in frames if f.image.is_file()]
    if not usable:
        raise ObjectsError("gate_model_missing")
    try:
        for model in cfg.gate_models:
            if heartbeat:
                heartbeat(f"objects:{model}")
            timing: dict[str, float] = {}
            emb = embed_frames(
                usable, model, max_side=cfg.max_side_px, device=cfg.device, timing=timing, beat=heartbeat
            )
            scores[model] = class_scores(emb, model, device=cfg.device)
            frame_s[model] = timing
            release_gates()
    finally:
        release_gates()
    return scores, frame_s


def _houses(frames: list[FrameRef], packet: dict) -> dict[str, str]:
    """One capture is one house. `house/frame` ids and an explicit FrameRef.house keep their groups."""
    if any(f.house for f in frames):
        shared = next(f.house for f in frames if f.house)
        houses = {}
        for frame in frames:
            if frame.house:
                houses[frame.id] = frame.house
            elif "/" in frame.id:
                houses[frame.id] = frame.id.split("/")[0]
            else:
                houses[frame.id] = shared
        return houses
    if frames and all("/" in frame.id for frame in frames):
        return {frame.id: frame.id.split("/")[0] for frame in frames}
    shared = str(packet.get("capture_id") or packet.get("id") or "capture")
    return {frame.id: shared for frame in frames}


def _packet_dir(ctx) -> Path | None:
    """The directory WS-03 fills with packet.json and the capture's committed files."""
    raw = (getattr(ctx, "inputs", None) or {}).get("packet_dir")
    if not raw:
        return None
    path = Path(raw)
    if path.is_file() and path.name == "packet.json":
        return path.parent
    return path


def _load_packet(packet_dir: Path | None) -> dict | None:
    """Read packet.json only. normalized.json in the same directory is a different artifact."""
    if packet_dir is None:
        return None
    target = packet_dir / "packet.json"
    if not target.is_file():
        return None
    loaded = json.loads(target.read_text())
    return loaded if isinstance(loaded, dict) else None


def _image_path(root: Path, rel: str) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _frames(packet: dict, root: Path) -> list[FrameRef]:
    frames: list[FrameRef] = []
    for kf in packet.get("keyframes") or []:
        img = kf.get("img")
        if not img:
            continue
        frames.append(
            FrameRef(
                kf["id"],
                _image_path(root, img),
                int(kf.get("w") or 0),
                int(kf.get("h") or 0),
                "keyframe",
                epoch=kf.get("epoch"),
            )
        )
    for still in packet.get("stills") or []:
        img = still.get("img")
        if not img:
            continue
        frames.append(
            FrameRef(
                still["id"],
                _image_path(root, img),
                int(still.get("w") or 0),
                int(still.get("h") or 0),
                "still",
                purpose=still.get("purpose"),
            )
        )
    if frames:
        return frames
    # A packet can list images only in files[] (role keyframe/still). Those bytes sit beside packet.json.
    seen: set[str] = set()
    for item in packet.get("files") or []:
        if not isinstance(item, dict):
            continue
        kind = _FILE_ROLES.get(str(item.get("role") or ""))
        rel = item.get("path")
        if kind is None or not rel:
            continue
        fid = Path(str(rel)).stem
        if fid in seen:
            continue
        seen.add(fid)
        frames.append(FrameRef(fid, _image_path(root, str(rel)), 0, 0, kind))
    return frames


def _scores_doc(scores: dict) -> dict:
    """frame -> {clip: {class: score}, siglip: {...}} as the brief's sidecar."""
    frames: dict[str, dict] = {}
    for model, rows in scores.items():
        for fid, cls_scores in rows.items():
            frames.setdefault(fid, {})[model] = cls_scores
    return frames


def _wrap(artifacts: dict, metrics: dict, warnings: list[str]) -> StageResult:
    return StageResult(
        artifacts=artifacts, metrics={key: float(value) for key, value in metrics.items()}, warnings=warnings
    )
