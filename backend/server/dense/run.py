"""Dense stage: metric cloud, flagged windows, quarter-resolution depth."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from server.common.errors import SceneError
from server.common.role import coverage_only_dense
from server.common.scaleio import as_pose, load_scale
from server.common.stage import export_run
from server.common.tracks import empty_tracks, read_tracks, write_tracks
from server.contracts.jobs import StageResult
from server.dense.dense import fuse_windows, resolve_second_opinion, subsample_indices
from server.dense.framew import to_frame_w, up_from_gravity
from server.dense.lidar_fuse import fuse_points, tsdf_points
from server.dense.models import infer_da3_second_opinion, infer_moge_depths, infer_pi3_predictions
from server.dense.params import DenseParams, dense_params_from_env
from server.eval.loader import load as load_harness


@dataclass
class DenseReport:
    windows: list[dict] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    geometry_role: str = "measure"
    second_opinion: str = "none"
    t_w_from_source: list[float] = field(default_factory=list)
    source_frame: str = "arkit_world"


def deadline_epoch(value) -> float | None:
    """Unix seconds for a stage deadline. ISO-8601 strings (including ``Z``) and numbers."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _stage_result(artifacts: dict[str, str], metrics: dict, warnings: list[str]) -> StageResult:
    numbers = {key: float(value) for key, value in metrics.items()}
    return StageResult(artifacts=artifacts, metrics=numbers, warnings=list(warnings))


def _canonicalize_point_rows(
    points: np.ndarray, src: np.ndarray, tracks: list[np.ndarray]
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """Sort point rows once and keep source and observation rows aligned."""
    order = np.lexsort((points[:, 2], points[:, 1], points[:, 0], -src))
    return points[order], src[order], [tracks[int(index)] for index in order]


def _work_artifacts(work: Path, paths: list[Path]) -> dict[str, str]:
    """§3.9 key and work-dir relative path for each file that exists."""
    artifacts = {}
    for path in paths:
        if path.is_file():
            rel = path.relative_to(work).as_posix()
            artifacts[rel] = rel
    return artifacts


def run_dense(
    packet: dict,
    poses_dir: Path,
    scale: dict,
    out_dir: Path,
    *,
    params: DenseParams,
    models: dict,
    heartbeat=None,
    deadline=None,
) -> DenseReport:
    started = time.time()
    deadline_s = deadline_epoch(deadline)
    if deadline_s is not None and time.time() > deadline_s:
        raise SceneError("timeout")
    poses_dir = Path(poses_dir)
    cameras_path = poses_dir / "cameras.json"
    if not cameras_path.is_file() and "cameras" not in models:
        raise SceneError("input_missing:cameras.json")
    cameras = models.get("cameras")
    if cameras is None:
        cameras = json.loads(cameras_path.read_text())
    if heartbeat:
        heartbeat("dense:0")
    flow = str(models.get("flow") or packet.get("flow") or "guided")
    source_frame = str(models.get("source_frame") or "arkit_world:e0")
    report = DenseReport(source_frame=source_frame)
    report.warnings.extend(models.get("warnings") or [])
    factor = float(scale.get("scale_factor") or 1.0)
    centers = [as_pose(c["pose"])[:3, 3] for c in cameras]
    origin = scale.get("origin_m")
    origin = np.asarray(origin if origin is not None else np.mean(centers, axis=0), float)
    poses = [as_pose(c["pose"]) for c in cameras]
    up = np.array([0.0, 1.0, 0.0])
    gravities = models.get("gravities")
    if gravities:
        up = up_from_gravity(poses, gravities)
        report.warnings.append("gravity_source:imu")
    points = models.get("points_w")
    src = models.get("point_src")
    dense_tracks: list[np.ndarray] | None = None
    learned: dict = {}
    if points is None:
        points, src, window_rows, opinion, dense_tracks = _acquire_cloud(models, cameras, poses, params, flow, report)
        report.windows = window_rows
        report.second_opinion = opinion
        report.flagged = [f"window_pose_inconsistent:{row['index']}" for row in window_rows if row["flagged"]]
        report.warnings.extend(report.flagged)
        if models.get("window_predictions") is not None:
            learned = _attach_learned_depth(models, cameras, params, report)
    points = np.asarray(points, float)
    src = np.asarray(src if src is not None else np.ones(len(points), int), int)
    if dense_tracks is None:
        dense_tracks = _injected_tracks(len(points), models)
    if len(points):
        points, src, dense_tracks = _canonicalize_point_rows(points, src, dense_tracks)
    sparse = _sparse_points(poses_dir)
    sparse_tracks, sparse_ids = _sparse_tracks(poses_dir, len(sparse), report)
    if len(sparse):
        points = sparse if len(points) == 0 else np.vstack([points, sparse])
        src = np.concatenate([src, np.zeros(len(sparse), int)])
    tracks = list(dense_tracks) + sparse_tracks
    point_ids = np.concatenate([np.full(len(dense_tracks), -1, np.int64), sparse_ids])
    points_w, poses_w, transform = to_frame_w(points, poses, origin, factor, up)
    if coverage_only_dense(source_frame, flow):
        report.geometry_role = "coverage_only"
    else:
        report.geometry_role = "measure"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sceneio = load_harness()["sceneio"]
    keep = np.ones(len(points_w), dtype=bool)
    flagged_pts = models.get("flagged_points")
    sceneio.write_ply(out_dir / "dense.ply", points_w[keep].astype(np.float32), {"src": src[keep].astype(np.uint8)})
    flagged = np.asarray(flagged_pts, float) if flagged_pts is not None else np.zeros((0, 3))
    if flagged.ndim != 2 or flagged.shape[1] != 3:
        flagged = np.zeros((0, 3))
    if len(flagged):
        # Flagged windows are still in the source frame. The same W transform as the cloud.
        flagged, _, _ = to_frame_w(flagged, poses, origin, factor, up)
    sceneio.write_ply(out_dir / "flagged.ply", flagged.astype(np.float32), {"src": np.ones(len(flagged), np.uint8)})
    cameras_w = []
    for cam, pose in zip(cameras, poses_w, strict=True):
        row = dict(cam)
        row["pose"] = pose.tolist()
        cameras_w.append(row)
    (out_dir / "cameras_w.json").write_text(json.dumps(cameras_w) + "\n")
    if heartbeat:
        heartbeat("depth:0")
    _write_depths(out_dir, models, cameras)
    report.counts = {
        "points": int(len(points_w)),
        "flagged_points": int(len(flagged)),
        "frames": len(cameras),
        **learned,
    }
    report.timings = {"dense_s": round(time.time() - started, 3)}
    report.t_w_from_source = [float(v) for v in transform.flatten(order="F")]
    (out_dir / "dense_report.json").write_text(json.dumps(_report_dict(report), indent=1) + "\n")
    np.savez_compressed(out_dir / "cloud.npz", points=points_w.astype(np.float32), src=src.astype(np.int16))
    write_tracks(out_dir / "tracks.npz", tracks, point_ids)
    return report


def _acquire_cloud(
    models: dict, cameras: list[dict], poses: list[np.ndarray], params: DenseParams, flow: str, report: DenseReport
):
    """LiDAR when the geometry mode asks for it, then injected windows, then the weight loaders."""
    geometry = str(params.geometry or "auto")
    if geometry != "image":
        lidar = _lidar_cloud(models, cameras, poses, params)
        if lidar is not None:
            points, src = lidar
            report.warnings.append("lidar_fused")
            return points, src, [], "none", empty_tracks(len(points))
        if geometry == "lidar":
            raise SceneError("input_missing:depth", "lidar geometry was requested and no depth maps were found")
    if models.get("window_predictions") is not None:
        return _from_predictions(models, poses, params, flow)
    images = models.get("images")
    if images is None:
        images = _packet_images(models, cameras)
    if images is not None and len(images) >= 3:
        try:
            predictions = infer_pi3_predictions(
                list(images), cameras, params, device=str(models.get("device") or "cuda")
            )
        except FileNotFoundError as exc:
            raise SceneError(
                "input_missing:weights",
                "π³ weights are not loaded and no points were injected. Modal token and HSS_DENSE_WEIGHTS are required for a real dense run.",
            ) from exc
        models["window_predictions"] = predictions
        return _from_predictions(models, poses, params, flow)
    raise SceneError(
        "input_missing:weights",
        "π³ weights are not loaded and no points were injected. Modal token and HSS_DENSE_WEIGHTS are required for a real dense run.",
    )


def _attach_learned_depth(models: dict, cameras: list[dict], params: DenseParams, report: DenseReport) -> dict:
    """MoGe depth for coverage, and DA3 when the second opinion resolves to it.

    Neither cloud is a measurement source (SD-65). Missing weights are an error
    on this path because the image branch already committed to a real model.
    """
    images = models.get("images")
    if images is None:
        images = _packet_images(models, cameras)
    if not images:
        return {}
    device = str(models.get("device") or "cuda")
    stats: dict = {}
    if models.get("depths") is None:
        try:
            models["depths"] = infer_moge_depths(list(images), cameras, params, device=device)
        except FileNotFoundError as exc:
            raise SceneError("input_missing:weights", str(exc)) from exc
        report.warnings.append("moge_depth")
        stats["moge_frames"] = len(models["depths"])
    if report.second_opinion == "da3":
        try:
            opinion = infer_da3_second_opinion(list(images), params, device=device)
        except FileNotFoundError as exc:
            raise SceneError("input_missing:weights", str(exc)) from exc
        report.warnings.append("da3_second_opinion")
        stats["da3_frames"] = int(opinion["frames"])
        stats["da3_finite"] = int(opinion["finite"])
    return stats


def _mask_confidence(depth: np.ndarray, conf, min_conf: int) -> np.ndarray:
    z = np.asarray(depth, float)
    if conf is None:
        return z
    c = np.asarray(conf)
    if c.shape == z.shape:
        z = np.where(c >= min_conf, z, np.nan)
    return z


def _lidar_cloud(models: dict, cameras: list[dict], poses: list[np.ndarray], params: DenseParams):
    rows = _lidar_rows(models, cameras, poses, params)
    if not rows:
        return None
    depths, used_poses, intrinsics, image_wh = [], [], [], []
    for depth, pose, k_full, wh in rows:
        depths.append(depth)
        used_poses.append(pose)
        intrinsics.append(k_full)
        image_wh.append(wh)
    if params.lidar_method == "points":
        points = fuse_points(depths, used_poses, intrinsics, image_wh, params)
    else:
        try:
            points = tsdf_points(depths, used_poses, intrinsics, image_wh, params)
        except SceneError:
            if params.geometry == "lidar":
                raise
            points = fuse_points(depths, used_poses, intrinsics, image_wh, params)
    if len(points) == 0 and params.geometry != "lidar":
        return None
    return points, np.full(len(points), 2, int)


def _lidar_rows(models: dict, cameras: list[dict], poses: list[np.ndarray], params: DenseParams):
    given = models.get("lidar_depths")
    rows = []
    if given:
        for index, row in enumerate(given):
            cam = cameras[min(index, len(cameras) - 1)]
            pose = as_pose(row["pose"]) if row.get("pose") is not None else poses[min(index, len(poses) - 1)]
            k_full = np.asarray(row.get("intrinsics") or cam.get("intrinsics") or cam.get("K"), float)
            width = int(row.get("w") or cam["w"])
            height = int(row.get("h") or cam["h"])
            depth = _mask_confidence(row["depth"], row.get("conf"), params.lidar_min_conf)
            rows.append((depth, pose, k_full, (width, height)))
        return rows
    packet_dir = models.get("packet_dir")
    if not packet_dir:
        return []
    root = Path(packet_dir)
    packet_path = root / "packet.json"
    if not packet_path.is_file():
        return []
    packet = json.loads(packet_path.read_text())
    by_id = {}
    for frame in packet.get("keyframes") or []:
        spec = frame.get("depth") or {}
        if not spec.get("file"):
            continue
        loaded = _read_packet_depth(root, spec)
        if loaded is None:
            continue
        by_id[str(frame.get("id"))] = loaded
    if not by_id:
        return []
    for index, cam in enumerate(cameras):
        key = str(cam.get("keyframe_id") or cam.get("image") or cam.get("id") or "")
        loaded = by_id.get(key)
        if loaded is None:
            continue
        depth, conf = loaded
        pose = poses[index]
        k_full = np.asarray(cam.get("intrinsics") or cam.get("K"), float)
        rows.append(
            (
                _mask_confidence(depth, conf, params.lidar_min_conf),
                pose,
                k_full,
                (int(cam["w"]), int(cam["h"])),
            )
        )
    return rows


def _read_packet_depth(root: Path, spec: dict):
    width, height = int(spec["w"]), int(spec["h"])
    path = root / spec["file"]
    if not path.is_file() or path.stat().st_size != width * height * 4:
        return None
    depth = np.fromfile(path, dtype="<f4", count=width * height).reshape(height, width)
    conf = None
    conf_name = spec.get("confidenceFile")
    if conf_name:
        conf_path = root / conf_name
        if conf_path.is_file() and conf_path.stat().st_size == width * height:
            conf = np.fromfile(conf_path, dtype=np.uint8, count=width * height).reshape(height, width)
    return depth, conf


def _packet_images(models: dict, cameras: list[dict]):
    packet_dir = models.get("packet_dir")
    if not packet_dir:
        return None
    root = Path(packet_dir)
    packet_path = root / "packet.json"
    if not packet_path.is_file():
        return None
    packet = json.loads(packet_path.read_text())
    by_id = {str(frame.get("id")): frame for frame in packet.get("keyframes") or []}
    images = []
    for cam in cameras:
        key = str(cam.get("keyframe_id") or cam.get("image") or "")
        frame = by_id.get(key)
        name = None if frame is None else frame.get("img") or frame.get("image") or frame.get("file")
        if name is None:
            return None
        path = root / name
        if not path.is_file():
            return None
        images.append(np.asarray(Image.open(path).convert("RGB")))
    return images


def _from_predictions(models: dict, poses: list[np.ndarray], params: DenseParams, flow: str):
    predictions = models.get("window_predictions")
    if predictions is None:
        raise SceneError(
            "input_missing:weights",
            "π³ weights are not loaded and no points were injected. Modal token and HSS_DENSE_WEIGHTS are required for a real dense run.",
        )
    windows = [p["frames"] for p in predictions]
    pred_c = [np.asarray(p["pred_centers"], float) for p in predictions]
    sfm_c = [np.stack([poses[i][:3, 3] for i in p["frames"]]) for p in predictions]
    pred_pts = [np.asarray(p["points"], float) for p in predictions]
    fused, _median = fuse_windows(windows, pred_c, sfm_c, pred_pts, params)
    opinion = resolve_second_opinion(params.second_opinion, flow)
    chunks, flagged = [], []
    point_tracks: list[np.ndarray] = []
    rows = []
    for item in fused:
        rows.append(
            {
                "index": item.index,
                "scale": item.scale,
                "rms_m": item.rms_m,
                "flagged": item.flagged,
                "frames": item.frame_ids,
            }
        )
        if item.flagged:
            flagged.append(item.points)
            continue
        chunks.append(item.points)
        cameras = np.asarray(item.frame_ids, np.int32)
        point_tracks.extend([cameras] * len(item.points))
    if chunks:
        stacked = np.concatenate(chunks)
        kept = subsample_indices(stacked, cap=params.dense_cap)
        points = stacked[kept]
        src = np.ones(len(points), int)
        point_tracks = [point_tracks[int(i)] for i in kept]
    else:
        points, src = np.zeros((0, 3)), np.zeros(0, int)
        point_tracks = []
    models["flagged_points"] = np.concatenate(flagged) if flagged else np.zeros((0, 3))
    return points, src, rows, opinion, point_tracks


def _injected_tracks(n: int, models: dict) -> list[np.ndarray]:
    """Caller-supplied tracks for injected points. Missing tracks stay empty."""
    given = models.get("point_tracks")
    if given is not None and len(given) == n:
        return [np.asarray(row, np.int32).ravel() for row in given]
    return empty_tracks(n)


def _sparse_tracks(poses_dir: Path, n: int, report: DenseReport) -> tuple[list[np.ndarray], np.ndarray]:
    """Camera indices for ``sparse.ply`` rows. A missing or mismatched file claims no view."""
    ids = np.full(n, -1, np.int64)
    if n == 0:
        return [], ids[:0]
    loaded = read_tracks(Path(poses_dir) / "tracks.npz")
    if loaded is None:
        report.warnings.append("tracks_missing")
        return empty_tracks(n), ids
    tracks, point_ids = loaded
    if len(tracks) != n:
        report.warnings.append("tracks_count_mismatch")
        return empty_tracks(n), ids
    return tracks, point_ids


def _sparse_points(poses_dir: Path) -> np.ndarray:
    """WS-04 sparse cloud in the same frame as ``cameras.json``. ``src`` 0, so it can measure."""
    path = Path(poses_dir) / "sparse.ply"
    if not path.is_file():
        return np.zeros((0, 3))
    ply = load_harness()["sceneio"].read_ply(path)
    return np.stack([ply["x"], ply["y"], ply["z"]], axis=1).astype(float)


def _write_depths(out_dir: Path, models: dict, cameras: list[dict]) -> None:
    depth_dir = out_dir / "depth"
    depth_dir.mkdir(exist_ok=True)
    maps = models.get("depths") or {}
    for cam in cameras:
        key = cam.get("keyframe_id") or cam.get("image")
        if key not in maps:
            continue
        payload = maps[key]
        np.savez_compressed(
            depth_dir / f"{key}.npz",
            depth=np.asarray(payload["depth"], np.float32),
            scale=np.float32(payload.get("scale", 1.0)),
            source=np.array(payload.get("source", "moge2")),
        )


def _report_dict(report: DenseReport) -> dict:
    return {
        "windows": report.windows,
        "flagged": report.flagged,
        "counts": report.counts,
        "timings": report.timings,
        "warnings": report.warnings,
        "geometry_role": report.geometry_role,
        "second_opinion": report.second_opinion,
        "t_w_from_source": report.t_w_from_source,
        "source_frame": report.source_frame,
    }


def run(ctx) -> object:
    params = dense_params_from_env()
    packet_dir = Path(ctx.inputs["packet_dir"])
    packet = json.loads((packet_dir / "packet.json").read_text()) if (packet_dir / "packet.json").is_file() else {}
    poses_dir = Path(ctx.inputs.get("poses/cameras.json", ctx.work_dir / "poses" / "cameras.json")).parent
    if "poses_dir" in ctx.inputs:
        poses_dir = Path(ctx.inputs["poses_dir"])
    scale_path = ctx.inputs.get("scale/scale.json")
    flow = getattr(ctx, "flow", None) or "guided"
    scale, warnings = load_scale(Path(scale_path) if scale_path else None, flow)
    models = dict(getattr(ctx, "models", None) or {})
    models.setdefault("warnings", []).extend(warnings)
    models["flow"] = flow
    models["packet_dir"] = str(packet_dir)
    out = Path(ctx.work_dir) / "dense"
    deadline = getattr(ctx, "deadline_at", None)
    report = run_dense(
        packet,
        poses_dir,
        scale,
        out,
        params=params,
        models=models,
        heartbeat=getattr(ctx, "heartbeat", None),
        deadline=deadline,
    )
    names = ("dense.ply", "flagged.ply", "dense_report.json", "cloud.npz", "tracks.npz", "cameras_w.json")
    paths = [out / name for name in names]
    depth_dir = out / "depth"
    if depth_dir.is_dir():
        paths.extend(sorted(depth_dir.glob("*.npz")))
    return _stage_result(
        _work_artifacts(Path(ctx.work_dir), paths),
        {"points": int(report.counts.get("points", 0)), "dense_s": float(report.timings.get("dense_s", 0.0))},
        list(report.warnings),
    )


export_run(run)
