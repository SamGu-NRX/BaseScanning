"""Coverage stage entry. Reads the dense cloud and writes the grid, bands and requests."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from server.common.errors import SceneError
from server.common.params import SceneParams, scene_params_from_env
from server.common.scaleio import as_pose, load_scale
from server.common.stage import export_run
from server.common.tracks import resolve_tracks
from server.contracts.jobs import StageResult
from server.coverage.query import DepthAwareCoverageQuery
from server.eval.loader import load as load_harness
from server.scene.build import build_metric_scene


@dataclass
class CoverageReport:
    warnings: list
    views_needed: list
    timings: dict


def run_coverage(
    packet: dict,
    poses_dir: Path,
    dense_dir: Path,
    scale: dict,
    out_dir: Path,
    *,
    params: SceneParams,
    meter_override: list[tuple[str, float, float]] | None = None,
    anchor_label: str = "",
    heartbeat=None,
    deadline=None,
    models: dict | None = None,
) -> CoverageReport:
    del deadline
    started = time.perf_counter()
    models = models or {}
    if heartbeat:
        heartbeat("extract")
    dense_dir = Path(dense_dir)
    cloud = np.load(dense_dir / "cloud.npz")
    points, src = cloud["points"], cloud["src"]
    report_json = json.loads((dense_dir / "dense_report.json").read_text())
    if (dense_dir / "cameras_w.json").is_file():
        cameras = json.loads((dense_dir / "cameras_w.json").read_text())
    elif models.get("cameras"):
        cameras = models["cameras"]
    else:
        cameras = json.loads((Path(poses_dir) / "cameras.json").read_text())
    harness = _cams(cameras)
    clicks = models.get("meter_clicks")
    if clicks is None:
        clicks = _clicks(packet, harness, meter_override, anchor_label or str(models.get("anchor_label") or ""))
    if not clicks:
        raise SceneError("no_meter_anchor")
    tracks = resolve_tracks(models, dense_dir, len(points))
    flow = str(models.get("flow") or "guided")
    pose_sources = [str(c.get("pose_source", "sfm")) for c in cameras]
    t_w = np.asarray(report_json.get("t_w_from_source") or np.eye(4).flatten(order="F"), float).reshape(4, 4, order="F")
    if heartbeat:
        heartbeat("grid")
    depth_maps, depth_source = _load_depth_maps(dense_dir)

    def _query(cams, blockers, maps):
        return DepthAwareCoverageQuery(
            cams,
            blockers,
            depths=maps,
            relief_m=params.relief_m,
            relief_rel=params.relief_rel,
        )

    scene, state, needed = build_metric_scene(
        points=points,
        src=src,
        tracks=tracks,
        cameras=harness,
        meter_clicks=clicks,
        scale=scale,
        packet=packet,
        source_frame=str(report_json.get("source_frame") or "arkit_world"),
        t_w_from_source=t_w,
        params=params,
        flow=flow,
        pose_sources=pose_sources,
        scene_id=str(packet.get("packetId", "scene")),
        warnings=list(report_json.get("warnings") or []),
        coverage_query_factory=_query,
        depth_maps=depth_maps,
        depth_source=depth_source,
    )
    if heartbeat:
        heartbeat("bands")
        heartbeat("requests")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state["grid"].save(out_dir / "coverage.npz")
    bands_doc = {
        "bands": scene["coverage"].get("bands", []),
        "sensitivity": scene["coverage"].get("sensitivity") or [],
        "rule": params.ground_rule,
    }
    (out_dir / "bands.json").write_text(json.dumps(bands_doc, indent=1) + "\n")
    load_harness()["sceneio"].write_ply(
        out_dir / "points.ply", state["points_c"].astype(np.float32), {"src": state["measure_src"].astype(np.uint8)}
    )
    draft = dict(scene)
    draft.pop("assets", None)
    (out_dir / "scene_draft.json").write_text(json.dumps(draft, indent=1) + "\n")
    timings = {"coverage_s": round(time.perf_counter() - started, 3)}
    timings.update(state.get("step_s") or {})
    cov = CoverageReport(
        warnings=state["notes"],
        views_needed=needed,
        timings=timings,
    )
    (out_dir / "coverage_report.json").write_text(
        json.dumps(
            {
                "warnings": cov.warnings,
                "views_needed": cov.views_needed,
                "timings": cov.timings,
                "bands": bands_doc["bands"],
            },
            indent=1,
        )
        + "\n"
    )
    np.savez_compressed(out_dir / "state.npz", t=state["T"])
    (out_dir / "scene_ready.json").write_text(json.dumps(scene) + "\n")
    return cov


def _load_depth_maps(dense_dir: Path) -> tuple[dict[str, np.ndarray], str | None]:
    """Quarter-resolution maps keyed by keyframe id. ``scale`` is applied here."""
    depth_dir = Path(dense_dir) / "depth"
    maps: dict[str, np.ndarray] = {}
    source = None
    if not depth_dir.is_dir():
        return maps, source
    for path in sorted(depth_dir.glob("*.npz")):
        data = np.load(path)
        depth = np.asarray(data["depth"], np.float32)
        if "scale" in data.files:
            depth = depth * float(data["scale"])
        maps[path.stem] = depth
        if "source" in data.files:
            raw = data["source"]
            source = str(raw.item() if getattr(raw, "shape", ()) == () else raw)
    if source and "lidar" in source:
        return maps, "lidar"
    return maps, "moge2" if maps else None


def _cams(cameras: list[dict]) -> list:
    camera_cls = load_harness()["visibility"].Camera
    out = []
    for cam in cameras:
        k = np.asarray(cam.get("intrinsics") or cam.get("K"), float).reshape(-1)
        out.append(
            camera_cls(
                str(cam.get("keyframe_id") or cam.get("image")),
                as_pose(cam["pose"]),
                k[:4],
                int(cam["w"]),
                int(cam["h"]),
            )
        )
    return out


def _clicks(packet: dict, cameras: list, override, anchor_label: str = "") -> list[tuple[int, float, float]]:
    if override:
        by_id = {cam.id: i for i, cam in enumerate(cameras)}
        return [(by_id[fid], u, v) for fid, u, v in override if fid in by_id]
    labels = ["meter"]
    if anchor_label and anchor_label not in labels:
        labels.append(anchor_label)
    for label in labels:
        found = []
        for tap in packet.get("taps") or []:
            if tap.get("label") != label:
                continue
            key = str(tap.get("keyframe"))
            pixel = tap.get("pixel") or [0, 0]
            for i, cam in enumerate(cameras):
                if cam.id == key:
                    found.append((i, float(pixel[0]), float(pixel[1])))
        if found:
            return found
    return []


def run(ctx) -> StageResult:
    params = scene_params_from_env()
    packet_dir = Path(ctx.inputs["packet_dir"])
    packet_path = packet_dir / "packet.json"
    packet = json.loads(packet_path.read_text()) if packet_path.is_file() else {}
    flow = getattr(ctx, "flow", None) or "guided"
    scale_path = ctx.inputs.get("scale/scale.json")
    scale, warnings = load_scale(Path(scale_path) if scale_path else None, flow)
    models = dict(getattr(ctx, "models", None) or {})
    models["flow"] = flow
    models.setdefault("warnings", []).extend(warnings)
    poses_dir = Path(ctx.inputs.get("poses_dir", Path(ctx.work_dir) / "poses"))
    dense_dir = Path(ctx.work_dir) / "dense"
    out = Path(ctx.work_dir) / "coverage"
    report = run_coverage(
        packet,
        poses_dir,
        dense_dir,
        scale,
        out,
        params=params,
        heartbeat=getattr(ctx, "heartbeat", None),
        models=models,
    )
    names = (
        "coverage.npz",
        "bands.json",
        "points.ply",
        "scene_ready.json",
        "scene_draft.json",
        "coverage_report.json",
    )
    return StageResult(
        artifacts=_work_artifacts(Path(ctx.work_dir), [out / name for name in names]),
        metrics={"views_needed": float(len(report.views_needed))},
        warnings=list(report.warnings),
    )


def _work_artifacts(work: Path, paths: list[Path]) -> dict[str, str]:
    """§3.9 key and work-dir relative path for each file that exists."""
    artifacts = {}
    for path in paths:
        if path.is_file():
            rel = path.relative_to(work).as_posix()
            artifacts[rel] = rel
    return artifacts


export_run(run)
