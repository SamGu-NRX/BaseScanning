# vendored from internal/modal-pipeline/housepipe/pipeline.py (store, 2026-09-26)
"""One packet in, a scene plus per-step timings out."""

from __future__ import annotations

import json
import shutil
import tarfile
import time
from pathlib import Path

import numpy as np

from server.poses.colmap_prior import reconstruct_epoch
from server.coverage.occlusion import MOGE_REPO, coverage_report, predict_depth
from server.dense.dense import DA3_REPO, PI3_REPO, run_dense
from server.common.geometry import colmap_to_arkit_pose, column_major
from server.scale.lidar import (
    align_scale,
    coverage_depths,
    far_points,
    frame_depth_spec,
    fuse,
    has_lidar,
    load_depths,
    place_taps,
)
from server.packet.validate_legacy import validate_packet


def _find_manifest(root: Path, variant: str) -> Path:
    matches = sorted(root.rglob(f"packet.{variant}.json"))
    if not matches:
        matches = sorted(root.rglob("packet.*.json"))
    if not matches:
        raise FileNotFoundError(f"no packet json under {root}")
    # Prefer the requested variant.
    for path in matches:
        if path.name == f"packet.{variant}.json":
            return path
    return matches[0]


def _load_frames(manifest: Path, max_frames: int | None) -> list[dict]:
    from server.common.geometry import from_column_major

    pk = json.loads(manifest.read_text())
    root = manifest.parent
    frames = []
    for k in pk.get("keyframes") or []:
        frames.append({
            "id": k["id"],
            "name": Path(k["img"]).name,
            "path": root / k["img"],
            "w": int(k["w"]),
            "h": int(k["h"]),
            "K": [float(v) for v in k["intrinsics"]],
            "T_ar": from_column_major(k["pose"]),
            "epoch": k.get("epoch") or "e1",
            "timestamp": float(k["timestamp"]),
            "depth": frame_depth_spec(k, root),
        })
    if not frames:
        for still in pk.get("stills") or []:
            K = still.get("intrinsics") or [float(still["w"]), float(still["w"]), still["w"] / 2, still["h"] / 2]
            frames.append({
                "id": still["id"],
                "name": Path(still["img"]).name,
                "path": root / still["img"],
                "w": int(still["w"]),
                "h": int(still["h"]),
                "K": [float(v) for v in K],
                "T_ar": None,
                "epoch": "photo",
                "timestamp": float(still.get("timestamp") or 0),
            })
    frames.sort(key=lambda f: (f["epoch"], f["timestamp"], f["id"]))
    if max_frames and len(frames) > max_frames:
        idx = np.linspace(0, len(frames) - 1, max_frames).round().astype(int)
        # Keep epoch groups intact enough by taking a global even sample.
        frames = [frames[i] for i in sorted(set(idx.tolist()))]
    return frames


def _write_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray | None = None) -> None:
    xyz = np.asarray(xyz, np.float32)
    n = len(xyz)
    props = "property float x\nproperty float y\nproperty float z\n"
    extra = b""
    if rgb is not None and len(rgb) == n:
        props += "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        rgb = np.asarray(rgb, np.uint8)
    header = f"ply\nformat binary_little_endian 1.0\nelement vertex {n}\n{props}end_header\n"
    buf = bytearray(header.encode("ascii"))
    if rgb is not None and len(rgb) == n:
        rec = np.empty(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
        rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
        buf += rec.tobytes()
    else:
        buf += xyz.astype("<f4").tobytes()
    path.write_bytes(buf)


def _source_names(root: Path) -> dict[str, str]:
    """Keyframe id -> dataset stem, for the local scorer only. Not used as a prior."""
    truth = next(root.rglob("_truth/truth.json"), None)
    if truth is None:
        return {}
    data = json.loads(truth.read_text())
    out = {}
    for image in data.get("images") or []:
        source = image.get("source") or ""
        stem = Path(source).stem
        if image.get("id") and stem:
            out[image["id"]] = stem
    return out


def run_packet(
    tar_path: Path,
    out_dir: Path,
    *,
    variant: str = "arkit-nonlidar",
    max_frames: int | None = None,
    window: int = 8,
    prior_sigma_m: float = 0.03,
    max_features: int = 8192,
    max_image_size: int = 2048,
    max_neighbors: int = 20,
    spatial_radius_m: float = 12.0,
    robust_prior: bool = False,
    geometry: str = "auto",
    dense: str = "pi3",
    models: dict | None = None,
    depth_stride: int = 1,
) -> dict:
    models = models or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    timings: dict[str, float] = {}
    t_all = time.perf_counter()

    t0 = time.perf_counter()
    with tarfile.open(tar_path) as tar:
        # Python 3.11 on the image has no tarfile extraction filter.
        tar.extractall(work / "packet")
    extract_root = work / "packet"
    manifest = _find_manifest(extract_root, variant)
    timings["untar_s"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    validation = validate_packet(manifest)
    timings["validate_s"] = round(time.perf_counter() - t0, 3)
    if not validation["ok"]:
        return {
            "ok": False,
            "validation": validation,
            "timings_s": timings,
            "total_s": round(time.perf_counter() - t_all, 3),
        }

    t0 = time.perf_counter()
    frames = _load_frames(manifest, max_frames)
    image_dir = work / "images"
    image_dir.mkdir()
    by_epoch: dict[str, list] = {}
    for frame in frames:
        dest = image_dir / frame["name"]
        if not dest.exists():
            shutil.copy(frame["path"], dest)
        frame["path"] = dest
        by_epoch.setdefault(frame["epoch"], []).append(frame)
    epoch_id, epoch_frames = max(by_epoch.items(), key=lambda kv: len(kv[1]))
    timings["prepare_s"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    sfm = reconstruct_epoch(
        work / "sfm",
        epoch_frames,
        max_image_size=max_image_size,
        max_features=max_features,
        prior_sigma_m=prior_sigma_m,
        robust_prior=robust_prior,
        max_neighbors=max_neighbors,
        spatial_radius_m=spatial_radius_m,
    )
    timings["colmap_s"] = round(time.perf_counter() - t0, 3)
    timings.update({f"colmap_{k}": v for k, v in sfm["timings"].items() if k.endswith("_s") or k in ("registered", "points3D", "pairs", "pose_priors", "mean_reproj_px", "prior_sigma_m", "robust_prior")})

    id_by_name = {f["name"]: f["id"] for f in epoch_frames}
    full_k = {f["name"]: f["K"] for f in epoch_frames}
    full_size = {f["name"]: (f["w"], f["h"]) for f in epoch_frames}
    source_names = _source_names(extract_root)
    cameras = []
    cameras_by_name = {}
    for cam in sfm["cameras"]:
        T_ar = colmap_to_arkit_pose(cam["R_cw"], cam["t_cw"])
        name = cam["name"]
        record = {
            "name": name,
            "keyframe_id": id_by_name.get(name, name),
            "source_stem": source_names.get(id_by_name.get(name, ""), None),
            "center": cam["center"].tolist(),
            "c2w": T_ar,
            "K_full": full_k.get(name, cam["K"]),
            "w": full_size.get(name, (cam["w"], cam["h"]))[0],
            "h": full_size.get(name, (cam["w"], cam["h"]))[1],
            "pose": column_major(T_ar),
        }
        cameras.append(record)
        cameras_by_name[name] = record

    lidar_frames = has_lidar(epoch_frames)
    if geometry not in ("auto", "lidar", "image"):
        raise ValueError(f"geometry must be auto, lidar or image, got {geometry}")
    lidar_on = lidar_frames if geometry == "auto" else geometry == "lidar"
    if geometry == "lidar" and not lidar_frames:
        lidar_on = False
    lidar_depths = load_depths(epoch_frames) if lidar_on else {}
    scale_report = {"applied": False, "scale": 1.0, "origin": None}
    if lidar_on:
        t0 = time.perf_counter()
        scale_report = align_scale(sfm["points"], cameras, lidar_depths)
        timings["lidar_scale_s"] = round(time.perf_counter() - t0, 3)
        for cam in cameras:
            cameras_by_name[cam["name"]] = cam

    import cv2

    order = [f["name"] for f in epoch_frames if f["name"] in cameras_by_name]
    images = []
    for name in order:
        bgr = cv2.imread(str(image_dir / name), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"failed to read {name}")
        images.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    dense_reports = []
    dense_xyz = np.zeros((0, 3), np.float32)
    wanted = ["pi3"] if dense in ("pi3", "both") else []
    if dense in ("da3", "both"):
        wanted.append("da3")
    for model_name in wanted:
        model = models.get(model_name)
        if model is None:
            dense_reports.append({"model": model_name, "error": "model not loaded"})
            continue
        report = run_dense(model_name, model, images, cameras_by_name, order, window=window)
        xyz = report.pop("points_xyz")
        if model_name == "pi3" or len(dense_xyz) == 0:
            dense_xyz = xyz
        dense_reports.append(report)
        timings[f"dense_{model_name}_s"] = report["seconds"]

    lidar_fuse = {"frames": 0, "points": 0}
    if lidar_on:
        t0 = time.perf_counter()
        lidar_xyz, lidar_fuse = fuse([cameras_by_name[n] for n in order], lidar_depths)
        timings["lidar_fuse_s"] = round(time.perf_counter() - t0, 3)
        far = far_points(dense_xyz, [cameras_by_name[n] for n in order]) if len(dense_xyz) else dense_xyz
        for report in dense_reports:
            if report.get("model") in ("pi3", "da3") and "points" in report:
                report["points_before_lidar_cut"] = report["points"]
                report["points"] = int(len(far)) if report.get("model") == "pi3" else report["points"]
        parts = [cloud for cloud in (lidar_xyz, far) if len(cloud)]
        dense_xyz = np.concatenate(parts, axis=0) if parts else np.zeros((0, 3), np.float32)
        dense_reports.append({
            "model": "lidar",
            "points": int(lidar_fuse.get("points") or 0),
            "points_far": int(len(far)),
            "seconds": timings["lidar_fuse_s"],
            **{k: v for k, v in lidar_fuse.items() if k != "points"},
        })

    # LiDAR depth is the occlusion test where the packet has it. Predicted
    # depth fills frames (or the whole house) that have none.
    t0 = time.perf_counter()
    depths = coverage_depths(lidar_depths) if lidar_on else {}
    depth_model = models.get("moge")
    depth_times = []
    if depth_model is not None:
        for i, name in enumerate(order):
            if name in depths or i % depth_stride != 0:
                continue
            cam = cameras_by_name[name]
            depth, mask, seconds = predict_depth(depth_model, images[i], cam["K_full"])
            depths[name] = (depth, mask)
            depth_times.append(seconds)
    timings["depth_s"] = round(time.perf_counter() - t0, 3)
    timings["depth_frames"] = len(depths)
    timings["depth_s_per_frame"] = round(float(np.mean(depth_times)), 4) if depth_times else None

    cloud = dense_xyz if len(dense_xyz) else sfm["points"]
    t0 = time.perf_counter()
    coverage = coverage_report(cloud, [cameras_by_name[n] for n in order], depths)
    timings["coverage_s"] = round(time.perf_counter() - t0, 3)

    sparse_path = out_dir / "sparse.ply"
    dense_path = out_dir / "dense.ply"
    _write_ply(sparse_path, sfm["points"], sfm["colors"])
    if len(dense_xyz):
        _write_ply(dense_path, dense_xyz)
    cam_export = []
    for cam in cameras:
        cam_export.append({
            "image": cam["source_stem"] or Path(cam["name"]).stem,
            "keyframe_id": cam["keyframe_id"],
            "pose": cam["pose"],
            "intrinsics": cam["K_full"],
            "w": cam["w"],
            "h": cam["h"],
        })
    (out_dir / "cameras.json").write_text(json.dumps(cam_export))

    posed = sfm["posed"]
    manifest_pk = json.loads(manifest.read_text())
    frames_by_id = {f["id"]: f for f in epoch_frames}
    cameras_by_id = {cam["keyframe_id"]: cam for cam in cameras}
    origin = scale_report.get("origin")
    objects = place_taps(
        manifest_pk.get("taps") or [],
        frames_by_id,
        cameras_by_id,
        lidar_depths if lidar_on else {},
        scale=float(scale_report.get("scale") or 1.0) if scale_report.get("applied") else 1.0,
        origin=np.asarray(origin, float) if origin else None,
    )
    if scale_report.get("applied"):
        scale_block = {
            "source": "lidar_scene_depth",
            "status": "lidar_metric",
            "scale": scale_report["scale"],
            "rel_bound_95": scale_report.get("rel_bound_95"),
            "pairs": scale_report.get("pairs"),
            "prior_sigma_m": prior_sigma_m if posed else None,
            "robust_prior": bool(robust_prior) if posed else None,
            "note": "SfM distances are scaled by the robust ratio of high-confidence LiDAR depth to SfM depth within 5 m.",
        }
    else:
        scale_block = {
            "source": "arkit_position_prior" if posed else "unscaled_sfm",
            "status": "single_reference" if posed else "unverified",
            "rel_bound_95": 0.06 if posed else None,
            "prior_sigma_m": prior_sigma_m if posed else None,
            "robust_prior": bool(robust_prior) if posed else None,
            "lidar_scale": {k: scale_report.get(k) for k in ("applied", "pairs", "reason", "scale")},
            "note": "Tight ARKit position priors (default 3 cm, no robust loss) keep the SfM in the phone's local frame. Scale is still the phone's session scale.",
        }
    scene = {
        "format": "house-scene/v0-modal",
        "packetId": validation["packetId"],
        "variant": variant,
        "epoch": epoch_id,
        "frames_used": len(epoch_frames),
        "frames_registered": sfm["timings"].get("registered"),
        "geometry_branch": "lidar" if lidar_on else "image",
        "frame": "lidar+arkit-prior-sfm" if lidar_on and posed else ("arkit-prior-sfm" if posed else "sfm-image-only"),
        "scale": scale_block,
        "sparse": {
            "points": int(len(sfm["points"])),
            "mean_reproj_px": sfm["timings"].get("mean_reproj_px"),
            "ply": sparse_path.name,
        },
        "dense": dense_reports,
        "coverage": coverage,
        "objects": objects,
        "models": {
            "pi3": PI3_REPO,
            "da3": DA3_REPO,
            "depth": "lidar_scene_depth" if lidar_on else MOGE_REPO,
        },
        "validation": {k: validation[k] for k in ("ok", "tier", "keyframes", "taps", "max_tap_replay_error", "warnings", "errors")},
    }
    # Drop non-json bits already removed from dense reports.
    timings["total_s"] = round(time.perf_counter() - t_all, 3)
    scene["timings_s"] = timings
    (out_dir / "scene.json").write_text(json.dumps(scene, indent=1, default=str))
    scene["ok"] = True
    scene["out_dir"] = str(out_dir)
    shutil.rmtree(work, ignore_errors=True)
    return scene
