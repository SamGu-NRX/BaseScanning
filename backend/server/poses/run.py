"""Turn a packet into poses, a sparse cloud and a registration report."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pycolmap

from server.common.geometry import (
    apply_sim3,
    arkit_to_colmap_pose,
    colmap_to_arkit_pose,
    column_major,
    from_column_major,
    umeyama,
)
from server.poses.blur import frame_blur_px, gyro_at, gyro_series, is_blurred
from server.poses.bridge import closeup_registered, join_accepted
from server.poses.epochs import anchor_epoch, epoch_jobs
from server.poses.errors import PosesError
from server.poses.fallback import choice_warnings, choose_model, scale_warning, trigger_details
from server.poses.fragments import FragmentInfo, choose_primary
from server.poses.host_pin import OPENBLAS_CORETYPE, OPENBLAS_THREADS
from server.poses.packet_io import Frame, PacketView, load_packet
from server.poses.pairs import cross_epoch_pairs, guided_pairs, photo_pairs
from server.poses.params import PoseParams
from server.poses.report import validate_report, write_ply
from server.poses.sfm import (
    MAPPING_THREADS,
    best_reconstruction,
    colmap_version,
    extract_features,
    incremental,
    keypoint_median,
    lock_intrinsics,
    match_imported,
    register_into,
    triangulate_fixed,
    write_priors,
)
from server.poses.spacing import photo_spacing, spacing_warnings
from server.poses.thin import budget_limit, heading_deg, select_guided, select_photo


def _beat(heartbeat, step: str, stop: threading.Event) -> None:
    if heartbeat is None:
        return
    heartbeat(step)

    def loop() -> None:
        while not stop.wait(60):
            heartbeat(step)

    threading.Thread(target=loop, daemon=True).start()


def _check(deadline: datetime | None) -> None:
    if deadline is not None and datetime.now(UTC) > deadline:
        raise PosesError("timeout")


def _fit(src: np.ndarray, dst: np.ndarray) -> tuple[float, float]:
    if len(src) < 3:
        return 0.0, 0.0
    scale, _, _ = umeyama(src, dst, with_scale=True)
    _, rotation, translation = umeyama(src, dst, with_scale=False)
    err = (src @ rotation.T) + translation - dst
    rms = float(np.sqrt((err**2).sum(axis=1).mean()))
    pct = (1.0 / scale - 1.0) * 100.0 if scale else 0.0
    return rms, pct


def _copy_images(frames: list[Frame], image_dir: Path) -> None:
    image_dir.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        dest = image_dir / frame.name
        if not dest.exists():
            shutil.copy2(frame.src, dest)


def _camera_specs(frames: list[Frame], *, radial: bool) -> dict[str, tuple[str, list[float]]]:
    specs: dict[str, tuple[str, list[float]]] = {}
    for frame in frames:
        if radial and frame.intrinsics_source != "arkit":
            focal = 0.5 * (frame.fx + frame.fy)
            specs[frame.name] = ("SIMPLE_RADIAL", [focal, frame.cx, frame.cy, 0.0])
        else:
            specs[frame.name] = ("PINHOLE", [frame.fx, frame.fy, frame.cx, frame.cy])
    return specs


def _registered_names(rec: pycolmap.Reconstruction) -> set[str]:
    return {im.name for im in rec.images.values() if im.has_pose}


def _image_stats(rec: pycolmap.Reconstruction) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    id_to_name: dict[int, str] = {}
    for image in rec.images.values():
        id_to_name[image.image_id] = image.name
        if image.has_pose:
            stats[image.name] = {"points": 0, "err": 0.0, "reproj": None}
    for point in rec.points3D.values():
        err = float(getattr(point, "error", 0.0))
        seen: set[str] = set()
        for element in point.track.elements:
            name = id_to_name.get(element.image_id)
            if name in stats and name not in seen:
                stats[name]["points"] += 1
                stats[name]["err"] += err
                seen.add(name)
    for row in stats.values():
        if row["points"]:
            row["reproj"] = row["err"] / row["points"]
    return stats


def _centers_of(rec: pycolmap.Reconstruction, names: list[str]) -> dict[str, np.ndarray]:
    by_name = {im.name: im for im in rec.images.values()}
    out: dict[str, np.ndarray] = {}
    for name in names:
        image = by_name.get(name)
        if image is not None and image.has_pose:
            out[name] = np.asarray(image.projection_center(), float)
    return out


def _arkit_pose_of(image) -> np.ndarray:
    cfw = image.cam_from_world()
    rotation = np.asarray(cfw.rotation.matrix(), float)
    translation = np.asarray(cfw.translation, float).reshape(3)
    return colmap_to_arkit_pose(rotation, translation)


def _k_of(image, rec: pycolmap.Reconstruction) -> tuple[float, float, float, float]:
    cam = rec.cameras[image.camera_id]
    params = [float(v) for v in cam.params]
    model = str(cam.model)
    if "PINHOLE" in model and len(params) >= 4:
        return params[0], params[1], params[2], params[3]
    focal = params[0]
    return focal, focal, params[1], params[2]


def _mapper_exclusion(frames: list[Frame], view: PacketView, params: PoseParams) -> tuple[set[str], list[str]]:
    """Ids kept out of the mapper, and warning strings.

    The brief's filter is WS-02's ``ext.hss.flags`` (``motion_blur``, ``high_iso``).
    The same 4 px rule applies when the packet carries exposure and gyro but
    the flag has not been written yet. Meter frames and close-ups stay in.
    """
    series = gyro_series(view.imu_path) if view.imu_path else []
    excluded: set[str] = set()
    warnings: list[str] = []
    for frame in frames:
        flagged = "motion_blur" in frame.flags or "high_iso" in frame.flags
        gyro = gyro_at(series, frame.timestamp) if series and frame.exposure_s is not None else None
        value, source = frame_blur_px(
            duration_s=frame.exposure_s,
            gyro_rad_s=gyro,
            focal_px=frame.fx,
            image=None,
            params=params,
        )
        imu_blur = (not flagged) and source == "imu" and is_blurred(value, "imu", params)
        if not flagged and not imu_blur:
            continue
        if frame.meter or frame.closeup:
            warnings.append(f"meter_frame_degraded:{frame.id}")
            continue
        excluded.add(frame.id)
        if "motion_blur" in frame.flags or imu_blur:
            warnings.append(f"blur_filtered:{frame.id}")
        if "high_iso" in frame.flags:
            warnings.append(f"high_iso:{frame.id}")
    return excluded, warnings


def _map_group(
    frames: list[Frame],
    *,
    image_dir: Path,
    db_path: Path,
    tmp: Path,
    params: PoseParams,
    guided: bool,
    prior_names: set[str],
    mapper_names: list[str],
    deadline: datetime | None,
    heartbeat,
) -> tuple[pycolmap.Reconstruction | None, dict]:
    _check(deadline)
    frames = _ordered(frames)
    names = [f.name for f in frames]
    by_name = {f.name: f for f in frames}
    pairs: list[tuple[str, str]] = []
    prior_ok: set[str] = set()
    if guided:
        # Pair list per epoch (brief §4.4). Spatial matching does not cross epochs.
        meter_ids = [frame.id for frame in frames if frame.meter]
        for job in epoch_jobs(frames, meter_ids=meter_ids, params=params):
            prior_ok.update(job.prior_names)
            posed = [frame for frame in job.frames if frame.pose is not None]
            if len(posed) < 2:
                continue
            centers = np.stack([frame.pose[:3, 3] for frame in posed])  # type: ignore[index]
            pairs.extend(guided_pairs([frame.name for frame in posed], centers, params))
        pairs = sorted(set(pairs))
        exhaustive = False
    else:
        posed = [f for f in frames if f.pose is not None]
        centers = np.stack([f.pose[:3, 3] for f in posed]) if len(posed) == len(frames) and frames else None
        pairs, exhaustive = photo_pairs(names, centers, params)
    stop = threading.Event()
    _beat(heartbeat, "extract", stop)
    t0 = time.perf_counter()
    radial = not guided
    extract_features(
        db_path,
        image_dir,
        names,
        camera_model="SIMPLE_RADIAL" if radial else "PINHOLE",
        params=params,
    )
    extract_s = time.perf_counter() - t0
    lock_intrinsics(db_path, _camera_specs(frames, radial=radial))
    median_kp = keypoint_median(db_path)
    centers_prior = {
        f.name: np.asarray(f.pose[:3, 3], float)
        for f in frames
        if f.name in prior_names and f.name in prior_ok and f.pose is not None and f.tracking == "normal"
    }
    n_prior = write_priors(db_path, centers_prior, params.prior_sigma_m) if guided else 0
    skipped = sum(1 for f in frames if f.tracking != "normal")
    t_match = time.perf_counter()
    n_pairs = match_imported(db_path, pairs, params)
    match_s = time.perf_counter() - t_match
    stop.set()
    _check(deadline)
    epoch_models: list[dict] = []
    map_s = 0.0
    model_count = 0
    if guided:
        meter_ids = [frame.id for frame in frames if frame.meter]
        jobs = epoch_jobs(frames, meter_ids=meter_ids, params=params)
        anchor_name = anchor_epoch(frames, meter_ids)
        for job in jobs:
            in_job = {frame.name for frame in job.frames}
            order = {frame.name: index for index, frame in enumerate(job.frames)}
            job_mapper = sorted(
                (name for name in mapper_names if name in in_job),
                key=lambda name: order.get(name, len(order)),
            )
            if len(job_mapper) < 2:
                epoch_models.append(
                    {
                        "epoch": job.epoch,
                        "rec": None,
                        "mapper_names": job_mapper,
                        "frame_names": [frame.name for frame in job.frames],
                        "extras": [],
                        "anchor": job.epoch == anchor_name,
                        "mapper_hit": 0,
                    }
                )
                continue
            _check(deadline)
            stop = threading.Event()
            _beat(heartbeat, f"map:{job.epoch}", stop)
            t1 = time.perf_counter()
            part = incremental(
                db_path,
                image_dir,
                tmp / job.epoch.replace("/", "_"),
                image_names=job_mapper,
                params=params,
                refine=radial,
                use_prior=guided and n_prior > 0,
            )
            map_s += time.perf_counter() - t1
            stop.set()
            model_count += len(part)
            best = best_reconstruction(part)
            hit = 0
            if best is not None:
                registered = _registered_names(best)
                hit = sum(1 for name in job_mapper if name in registered)
            extras = [model for model in part.values() if model is not best and model.num_reg_images() >= 3]
            epoch_models.append(
                {
                    "epoch": job.epoch,
                    "rec": best,
                    "mapper_names": job_mapper,
                    "frame_names": [frame.name for frame in job.frames],
                    "extras": extras,
                    "anchor": job.epoch == anchor_name,
                    "mapper_hit": hit,
                }
            )
        anchored = [item["rec"] for item in epoch_models if item["anchor"] and item["rec"] is not None]
        rec = anchored[0] if anchored else None
        if rec is None:
            rec = next((item["rec"] for item in epoch_models if item["rec"] is not None), None)
    else:
        stop = threading.Event()
        _beat(heartbeat, "map", stop)
        t1 = time.perf_counter()
        part = incremental(
            db_path,
            image_dir,
            tmp,
            image_names=mapper_names,
            params=params,
            refine=radial,
            use_prior=False,
        )
        map_s = time.perf_counter() - t1
        stop.set()
        model_count = len(part)
        rec = best_reconstruction(part)
        extras = [model for model in part.values() if model is not rec and model.num_reg_images() >= 3]
        epoch_models.append(
            {
                "epoch": "photo",
                "rec": rec,
                "mapper_names": list(mapper_names),
                "frame_names": names,
                "extras": extras,
                "anchor": True,
                "mapper_hit": len(_registered_names(rec)) if rec is not None else 0,
            }
        )
    info = {
        "pairs": n_pairs,
        "exhaustive": exhaustive,
        "extract_s": extract_s,
        "match_s": match_s,
        "mapper_s": map_s,
        "models": model_count,
        "epoch_models": epoch_models,
        "priors": n_prior,
        "skipped_limited_tracking": skipped,
        "median_keypoints": median_kp,
        "by_name": by_name,
    }
    return rec, info


def _export(rec: pycolmap.Reconstruction, frames: list[Frame], *, pose_source: str) -> tuple[list[dict], dict]:
    by_name = {f.name: f for f in frames}
    stats = _image_stats(rec)
    rows: list[dict] = []
    for image in rec.images.values():
        if not image.has_pose:
            continue
        frame = by_name.get(image.name)
        if frame is None:
            continue
        pose = _arkit_pose_of(image)
        fx, fy, cx, cy = _k_of(image, rec)
        st = stats.get(image.name, {})
        rows.append(
            {
                "image": Path(image.name).stem,
                "name": image.name,
                "keyframe_id": frame.id,
                "epoch": frame.epoch,
                "pose": column_major(pose),
                "intrinsics": [fx, fy, cx, cy],
                "w": frame.w,
                "h": frame.h,
                "pose_source": pose_source,
                "reproj_px": st.get("reproj") if st.get("reproj") is not None else 0.0,
                "points3D": int(st.get("points") or 0),
            }
        )
    rows.sort(key=lambda r: r["keyframe_id"])
    return rows, stats


def _cloud(rec: pycolmap.Reconstruction, rows: list[dict], ply_path: Path, npz_path: Path) -> dict:
    index = {row["name"]: i for i, row in enumerate(rows)}
    id_to_name = {im.image_id: im.name for im in rec.images.values()}
    xyz, rgb, nviews, indptr, indices, pids = [], [], [], [0], [], []
    for pid, point in rec.points3D.items():
        xyz.append(np.asarray(point.xyz, float))
        color = np.asarray(getattr(point, "color", [128, 128, 128]), int)
        rgb.append(color[:3])
        seen = []
        for element in point.track.elements:
            name = id_to_name.get(element.image_id)
            if name in index:
                seen.append(index[name])
        seen = sorted(set(seen))
        indices.extend(seen)
        indptr.append(len(indices))
        nviews.append(len(seen))
        pids.append(int(pid))
    if xyz:
        xyz_a = np.stack(xyz)
        rgb_a = np.stack(rgb)
    else:
        xyz_a = np.zeros((0, 3))
        rgb_a = np.zeros((0, 3), dtype=int)
    write_ply(ply_path, xyz_a, rgb_a, np.asarray(nviews, np.int32))
    np.savez(
        npz_path,
        indptr=np.asarray(indptr, np.int64),
        indices=np.asarray(indices, np.int32),
        point_ids=np.asarray(pids, np.int64),
    )
    mean_track = float(np.mean(nviews)) if nviews else 0.0
    return {"points3D": len(pids), "mean_track_length": mean_track}


def _carry_arkit(rows: list[dict], frames: list[Frame]) -> list[dict]:
    have = {r["keyframe_id"] for r in rows}
    for frame in frames:
        if frame.id in have or frame.pose is None:
            continue
        rows.append(
            {
                "image": Path(frame.name).stem,
                "name": frame.name,
                "keyframe_id": frame.id,
                "epoch": frame.epoch,
                "pose": column_major(frame.pose),
                "intrinsics": [frame.fx, frame.fy, frame.cx, frame.cy],
                "w": frame.w,
                "h": frame.h,
                "pose_source": "arkit",
                "reproj_px": 0.0,
                "points3D": 0,
            }
        )
    rows.sort(key=lambda r: r["keyframe_id"])
    return rows


def _budget(frames: list[Frame], *, guided: bool, params: PoseParams) -> tuple[list[Frame], list[Frame]]:
    """Mapper frames and leftovers. The cap is per epoch for a guided walk."""
    limit = budget_limit(params)
    if len(frames) <= limit:
        return list(frames), []
    must = [i for i, frame in enumerate(frames) if frame.meter or frame.closeup]
    if guided:
        posed_idx = [i for i, frame in enumerate(frames) if frame.pose is not None]
        if len(posed_idx) < 2:
            return list(frames), []
        centers = np.stack([frames[i].pose[:3, 3] for i in posed_idx])  # type: ignore[index]
        headings = [heading_deg(frames[i].pose) for i in posed_idx]  # type: ignore[arg-type]
        local_must = [posed_idx.index(i) for i in must if i in posed_idx]
        local = select_guided(len(posed_idx), centers, headings, must=local_must, limit=limit)
        chosen = {posed_idx[i] for i in local}
        for i in must:
            chosen.add(i)
    else:
        chosen = set(select_photo(len(frames), must=must, limit=limit))
    mapper = [frame for i, frame in enumerate(frames) if i in chosen]
    leftover = [frame for i, frame in enumerate(frames) if i not in chosen]
    return mapper, leftover


def _sim3_rms(src: np.ndarray, dst: np.ndarray) -> tuple[float, float, np.ndarray, np.ndarray]:
    scale, rotation, translation = umeyama(src, dst, with_scale=True)
    pred = apply_sim3(src, scale, rotation, translation)
    err = pred - dst
    rms = float(np.sqrt((err**2).sum(axis=1).mean()))
    return scale, rms, rotation, translation


def _posed_image(rec: pycolmap.Reconstruction, name: str):
    for image in rec.images.values():
        if image.name == name and image.has_pose:
            return image
    return None


def _transform_image_pose(image, scale: float, rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    """Move one camera from an epoch model into the primary model's world."""
    cfw = image.cam_from_world()
    r_cw = np.asarray(cfw.rotation.matrix(), float)
    t_cw = np.asarray(cfw.translation, float).reshape(3)
    centre = -r_cw.T @ t_cw
    centre_p = apply_sim3(centre, scale, rotation, translation)
    r_cw_p = r_cw @ rotation.T
    t_cw_p = -r_cw_p @ centre_p
    return colmap_to_arkit_pose(r_cw_p, t_cw_p)


def _write_fragment(
    rec: pycolmap.Reconstruction,
    frames: list[Frame],
    dest: Path,
    *,
    pose_source: str = "sfm",
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    rows, _stats = _export(rec, frames, pose_source=pose_source)
    public = [{k: v for k, v in row.items() if k != "name"} for row in rows]
    (dest / "cameras.json").write_text(json.dumps(public, indent=1))
    sparse = dest / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    rec.write_text(str(sparse))
    xyz, rgb, nviews = [], [], []
    for point in rec.points3D.values():
        xyz.append(np.asarray(point.xyz, float))
        color = np.asarray(getattr(point, "color", [128, 128, 128]), int)
        rgb.append(color[:3])
        nviews.append(len(point.track.elements))
    if xyz:
        write_ply(dest / "sparse.ply", np.stack(xyz), np.stack(rgb), np.asarray(nviews, np.int32))
    else:
        write_ply(dest / "sparse.ply", np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros((0,), np.int32))


def _ordered(frames: list[Frame]) -> list[Frame]:
    return sorted(frames, key=lambda frame: (frame.timestamp, frame.id, frame.name))


def _split_registration(rows: list[dict], mapper_names: list[str], mode: str) -> dict[str, int]:
    """Split registered frames into mapper, post-registered, and ARKit-carried.

    The three counts are disjoint and sum to ``frames_registered``. Carried
    frames are in that total (SD-92). Model selection still uses the SfM-only
    share and does not read these counts.
    """
    mapper_set = set(mapper_names)
    mapper = 0
    post = 0
    carried = 0
    for row in rows:
        if mode == "arkit_fixed" or row.get("pose_source") == "arkit":
            carried += 1
            continue
        if row.get("name") in mapper_set:
            mapper += 1
        else:
            post += 1
    return {
        "mapper": mapper,
        "post_registered": post,
        "carried": carried,
        "frames_registered": mapper + post + carried,
    }


def _checkpoint_database(db_path: Path) -> Path:
    """Fold the WAL into ``poses/database.db`` and drop ``-wal``/``-shm`` sidecars."""
    dest = db_path.with_name("database.db")
    if db_path.is_file():
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        if db_path.resolve() != dest.resolve():
            db_path.replace(dest)
    for path in (db_path, dest):
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
            if sidecar.is_file():
                sidecar.unlink()
    return dest


def run_poses(
    packet: PacketView,
    out_dir: Path,
    *,
    params: PoseParams | None = None,
    meter_candidates: list[str] | None = None,
    base_model: Path | None = None,
    heartbeat=None,
    deadline: datetime | None = None,
) -> dict:
    params = params or PoseParams.from_env()
    started = time.perf_counter()
    wall = datetime.now(UTC) + timedelta(seconds=params.timeout_s)
    if deadline is not None:
        wall = min(wall, deadline)
    poses_dir = Path(out_dir) / "poses"
    if poses_dir.exists():
        shutil.rmtree(poses_dir)
    poses_dir.mkdir(parents=True)
    for stale in Path(out_dir).glob("_tmp_*"):
        shutil.rmtree(stale, ignore_errors=True)

    frames = _ordered(list(packet.frames))
    if len(frames) < 3:
        raise PosesError("too_few_frames")
    guided = packet.flow == "guided" and all(f.pose is not None for f in frames if f.kind == "keyframe")
    excluded, quality_warnings = _mapper_exclusion(frames, packet, params)
    mappable = [f for f in frames if f.id not in excluded]
    if len(mappable) < 3:
        raise PosesError("too_few_frames")
    warnings = list(packet.warnings)
    warnings.extend(quality_warnings)

    image_dir = poses_dir / "_images"
    _copy_images(frames, image_dir)
    db_path = poses_dir / "_database.db"
    tmp = poses_dir / "_tmp_map"

    amended = base_model is not None
    if guided:
        jobs = epoch_jobs(mappable, meter_ids=packet.meter_ids, params=params)
        mapper_frames = []
        prior_names: set[str] = set()
        for job in jobs:
            chosen, _leftover = _budget(job.frames, guided=True, params=params)
            mapper_frames.extend(chosen)
            prior_names.update(set(job.prior_names) & {frame.name for frame in chosen})
    else:
        mapper_frames, _leftover = _budget(mappable, guided=False, params=params)
        prior_names = {frame.name for frame in mapper_frames}
    mapper_names = [frame.name for frame in mapper_frames]

    if heartbeat:
        heartbeat("match")
    if amended:
        if not Path(base_model).exists():
            raise PosesError("no_model")
        rec = pycolmap.Reconstruction(str(base_model))
        existing = _registered_names(rec)
        new_frames = [frame for frame in frames if frame.name not in existing]
        registered_post = 0
        info = {
            "pairs": 0,
            "exhaustive": False,
            "extract_s": 0.0,
            "match_s": 0.0,
            "mapper_s": 0.0,
            "models": 1,
            "epoch_models": [],
            "priors": 0,
            "skipped_limited_tracking": 0,
            "median_keypoints": 10_000.0,
        }
        if new_frames:
            if heartbeat:
                heartbeat("register")
            t_ex = time.perf_counter()
            extract_features(
                db_path,
                image_dir,
                [frame.name for frame in frames],
                camera_model="PINHOLE" if guided else "SIMPLE_RADIAL",
                params=params,
            )
            lock_intrinsics(db_path, _camera_specs(frames, radial=not guided))
            info["extract_s"] = time.perf_counter() - t_ex
            info["median_keypoints"] = keypoint_median(db_path)
            pairs = [tuple(sorted((frame.name, name))) for frame in new_frames for name in existing]
            t_match = time.perf_counter()
            if pairs:
                info["pairs"] = match_imported(db_path, pairs, params)
            info["match_s"] = time.perf_counter() - t_match
            registered_post, _failed = register_into(db_path, rec, [frame.name for frame in new_frames], params)
    else:
        registered_post = 0
        rec, info = _map_group(
            mappable,
            image_dir=image_dir,
            db_path=db_path,
            tmp=tmp,
            params=params,
            guided=guided,
            prior_names=prior_names,
            mapper_names=mapper_names,
            deadline=wall,
            heartbeat=heartbeat,
        )
    if info["median_keypoints"] < 3000:
        warnings.append("low_texture_or_blur")

    if not amended:
        for item in info["epoch_models"]:
            model = item["rec"]
            if model is None:
                continue
            missing = [name for name in item["frame_names"] if name not in _registered_names(model)]
            if not missing:
                continue
            if heartbeat:
                heartbeat("register")
            extra, _failed = register_into(db_path, model, missing, params)
            registered_post += extra
    anchor_item = next((item for item in info["epoch_models"] if item.get("anchor")), None)
    if anchor_item is not None:
        mapper_registered = int(anchor_item["mapper_hit"])
        mapper_given = len(anchor_item["mapper_names"])
    else:
        mapper_registered = len(_registered_names(rec)) if rec is not None else 0
        mapper_given = len(mapper_frames)
    candidates: list[tuple[FragmentInfo, pycolmap.Reconstruction]] = []
    for item in info.get("epoch_models", []):
        model = item["rec"]
        if model is None or model.num_reg_images() < 1:
            continue
        names = _registered_names(model)
        candidates.append(
            (
                FragmentInfo(
                    item["epoch"],
                    [frame.id for frame in frames if frame.name in names],
                    len([frame for frame in frames if frame.name in names]),
                    model.num_points3D(),
                    any(frame.meter and frame.name in names for frame in frames),
                ),
                model,
            )
        )
    if candidates and not amended:
        chosen, _ignored = choose_primary([info_f for info_f, _model in candidates])
        if chosen is not None:
            rec = next(model for info_f, model in candidates if info_f is chosen)
    dropped = [f for f in frames if f.id in excluded]
    if rec is not None and dropped and not amended:
        extract_features(
            db_path,
            image_dir,
            [f.name for f in dropped],
            camera_model="PINHOLE" if guided else "SIMPLE_RADIAL",
            params=params,
            reset=False,
        )
        lock_intrinsics(db_path, _camera_specs(dropped, radial=not guided))
        registered_now = _registered_names(rec)
        quality_pairs = []
        for frame in dropped:
            for name in registered_now:
                quality_pairs.append(tuple(sorted((frame.name, name))))
        if quality_pairs:
            match_imported(db_path, quality_pairs, params)
            extra, _failed = register_into(db_path, rec, [f.name for f in dropped], params)
            registered_post += extra

    mode = "arkit_prior" if guided else "image_only"
    pose_source = "sfm"
    status = "ok"
    rms_m, scale_pct = 0.0, 0.0
    details: list[dict] = []
    fixed_registered: int | None = None
    prior_model_registered: int | None = None
    mean_reproj_px: float | None = None
    holds_meter = False
    is_largest = False
    if guided and not amended:
        if rec is not None:
            names = [f.name for f in _ordered(mapper_frames) if f.name in _registered_names(rec)]
            rec_c = _centers_of(rec, names)
            if len(rec_c) >= 3:
                by_name = {f.name: f for f in frames}
                src = np.stack([rec_c[n] for n in names if n in rec_c])
                dst = np.stack([by_name[n].pose[:3, 3] for n in names if n in rec_c])  # type: ignore[index]
                rms_m, scale_pct = _fit(src, dst)
            warn = scale_warning(scale_pct, params)
            if warn:
                warnings.append(warn)
            details = trigger_details(
                mapper_registered=mapper_registered,
                mapper_given=mapper_given,
                rms_m=rms_m,
                scale_pct=scale_pct,
                params=params,
            )
            prior_model_registered = rec.num_reg_images()
            mean_reproj_px = float(rec.compute_mean_reprojection_error()) if rec.num_points3D() else 0.0
            registered_names = _registered_names(rec)
            holds_meter = any(frame.meter and frame.name in registered_names for frame in frames)
            other_counts = [
                item["rec"].num_reg_images() for item in info.get("epoch_models", []) if item.get("rec") is not None
            ]
            is_largest = rec.num_reg_images() >= max(other_counts) if other_counts else True
            _write_fragment(rec, frames, poses_dir / "fragments" / "prior")
        keyframes = [frame for frame in frames if frame.kind == "keyframe"]
        every_keyframe_posed = bool(keyframes) and all(frame.pose is not None for frame in keyframes)
        fixed = None
        if rec is not None or every_keyframe_posed:
            fixed_poses = {}
            for frame in _ordered(frames):
                if frame.pose is None:
                    continue
                fixed_poses[frame.name] = arkit_to_colmap_pose(frame.pose)
            if len(fixed_poses) >= 2:
                fixed = triangulate_fixed(
                    db_path,
                    image_dir,
                    poses_dir / "_tmp_fixed",
                    fixed_poses,
                    params,
                    image_order=[frame.name for frame in _ordered(frames)],
                )
                fixed_registered = fixed.num_reg_images()
                _write_fragment(fixed, frames, poses_dir / "fragments" / "fixed", pose_source="arkit")
        model_choice = choose_model(
            guided=True,
            amended=False,
            frames_given=len(frames),
            prior_model_registered=prior_model_registered,
            mapper_registered=mapper_registered,
            fixed_registered=fixed_registered,
            rms_m=rms_m if rec is not None else None,
            scale_pct=scale_pct if rec is not None else None,
            triggers=details,
            params=params,
            mean_reproj_px=mean_reproj_px,
            holds_meter=holds_meter,
            is_largest=is_largest,
        )
        warnings.extend(choice_warnings(model_choice))
        if model_choice["shipped"] == "arkit_fixed" and fixed is not None:
            rec = fixed
            mode = "arkit_fixed"
            pose_source = "arkit"
            status = "fallback_arkit_fixed"
            registered_post = 0
    else:
        model_choice = choose_model(
            guided=False,
            amended=amended,
            frames_given=len(frames),
            prior_model_registered=prior_model_registered,
            mapper_registered=0,
            fixed_registered=fixed_registered,
            rms_m=rms_m,
            scale_pct=scale_pct,
            triggers=details,
            params=params,
        )

    sim3_rows: list[dict] = []
    epoch_rows: list[dict] = []
    by_name = {frame.name: frame for frame in frames}
    if guided and not amended and mode != "arkit_fixed" and rec is not None:
        anchor_ordered: list[str] = []
        if anchor_item is not None:
            anchor_frames = [by_name[name] for name in anchor_item["frame_names"] if name in by_name]
            anchor_ordered = [frame.name for frame in _ordered(anchor_frames)]
        for item in info["epoch_models"]:
            if item.get("anchor"):
                continue
            other = item["rec"]
            if other is None:
                epoch_rows.append(
                    {
                        "id": item["epoch"],
                        "frames": len(item["frame_names"]),
                        "registered": 0,
                        "priors": 0,
                        "joined": False,
                        "join_method": None,
                        "join_rms_m": None,
                    }
                )
                continue
            if heartbeat:
                heartbeat("join")
            other_frames = [by_name[name] for name in item["frame_names"] if name in by_name]
            other_ordered = [frame.name for frame in _ordered(other_frames)]
            backup = poses_dir / "_tmp_join" / item["epoch"].replace("/", "_")
            if backup.exists():
                shutil.rmtree(backup)
            backup.mkdir(parents=True)
            rec.write_text(str(backup))
            pairs = cross_epoch_pairs(anchor_ordered, other_ordered, params.sequential_overlap)
            if pairs:
                match_imported(db_path, pairs, params)
            to_reg = [name for name in item["frame_names"] if name not in _registered_names(rec)]
            extra = 0
            if to_reg:
                extra, _failed = register_into(db_path, rec, to_reg, params)
            registered_now = [name for name in item["frame_names"] if name in _registered_names(rec)]
            stats = _image_stats(rec)
            reprojs = [
                float(stats[name]["reproj"]) for name in registered_now if stats.get(name, {}).get("reproj") is not None
            ]
            mean_reproj = float(np.mean(reprojs)) if reprojs else 99.0
            shared = [name for name in registered_now if _posed_image(other, name) is not None]
            join_rms = 99.0
            scale = rotation = translation = None
            if len(shared) >= 3:
                src = np.stack([_centers_of(other, shared)[name] for name in shared])
                dst = np.stack([_centers_of(rec, shared)[name] for name in shared])
                scale, join_rms, rotation, translation = _sim3_rms(src, dst)
            accepted = join_accepted(
                n_registered=len(registered_now),
                mean_reproj_px=mean_reproj,
                rms_m=join_rms,
            )
            if not accepted:
                rec = pycolmap.Reconstruction(str(backup))
                epoch_rows.append(
                    {
                        "id": item["epoch"],
                        "frames": len(item["frame_names"]),
                        "registered": other.num_reg_images(),
                        "priors": 0,
                        "joined": False,
                        "join_method": None,
                        "join_rms_m": None if join_rms >= 90 else join_rms,
                    }
                )
                continue
            registered_post += extra
            remain = [
                name
                for name in item["frame_names"]
                if name not in _registered_names(rec) and _posed_image(other, name) is not None
            ]
            if remain and scale is not None and rotation is not None and translation is not None:
                for name in remain:
                    image = _posed_image(other, name)
                    frame = by_name.get(name)
                    if image is None or frame is None:
                        continue
                    pose = _transform_image_pose(image, float(scale), rotation, translation)
                    fx, fy, cx, cy = _k_of(image, other)
                    sim3_rows.append(
                        {
                            "image": Path(name).stem,
                            "name": name,
                            "keyframe_id": frame.id,
                            "epoch": frame.epoch,
                            "pose": column_major(pose),
                            "intrinsics": [fx, fy, cx, cy],
                            "w": frame.w,
                            "h": frame.h,
                            "pose_source": "sfm_epoch_aligned",
                            "reproj_px": 0.0,
                            "points3D": 0,
                        }
                    )
            epoch_rows.append(
                {
                    "id": item["epoch"],
                    "frames": len(item["frame_names"]),
                    "registered": len(registered_now) + len(remain),
                    "priors": 0,
                    "joined": True,
                    "join_method": "sim3" if remain else "image_registrator",
                    "join_rms_m": join_rms,
                }
            )
    if guided:
        anchor_id = anchor_item["epoch"] if anchor_item is not None else (frames[0].epoch if frames else "e0")
        anchor_frames_n = len(anchor_item["frame_names"]) if anchor_item is not None else len(frames)
        epoch_rows.insert(
            0,
            {
                "id": anchor_id or "e0",
                "frames": anchor_frames_n,
                "registered": rec.num_reg_images() if rec is not None else 0,
                "priors": info["priors"],
                "joined": True,
                "join_method": "anchor",
                "join_rms_m": 0.0,
            },
        )

    if rec is None:
        raise PosesError("no_model")

    rows, _stats = _export(rec, frames, pose_source=pose_source)
    rows.extend(sim3_rows)
    rows.sort(key=lambda row: row["keyframe_id"])
    if guided and mode != "arkit_fixed":
        rows = _carry_arkit(rows, frames)
    arkit_carried_names = [row["name"] for row in rows if row["pose_source"] == "arkit"]
    _cloud(rec, rows, poses_dir / "sparse.ply", poses_dir / "tracks.npz")
    sparse = poses_dir / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    rec.write_text(str(sparse))

    fragment_rows: list[dict] = []
    seen = 0
    for item in info.get("epoch_models", []):
        models = list(item["extras"])
        if not item.get("anchor") and item["rec"] is not None and item["rec"] is not rec:
            models.insert(0, item["rec"])
        for model in models:
            if model is None or model.num_reg_images() < 3:
                continue
            fid = f"f{seen}"
            seen += 1
            names = _registered_names(model)
            ids = [frame.id for frame in frames if frame.name in names]
            has_meter = any(frame.meter and frame.name in names for frame in frames)
            _write_fragment(model, frames, poses_dir / "fragments" / fid)
            fragment_rows.append(
                {
                    "id": fid,
                    "frames": ids,
                    "registered": len(ids),
                    "points3D": model.num_points3D(),
                    "has_meter": has_meter,
                    "primary": False,
                }
            )

    if mode == "arkit_fixed":
        registered_ids = {r["keyframe_id"] for r in rows}
    else:
        registered_ids = {r["keyframe_id"] for r in rows if r["pose_source"] != "arkit"}
    registration_counts = _split_registration(rows, mapper_names, mode)
    meter_in = any(f.id in registered_ids for f in frames if f.meter)
    if not meter_in and status == "ok":
        status = "no_meter_model"
    primary_frag = FragmentInfo(
        id="f0",
        frames=[r["keyframe_id"] for r in rows if r["pose_source"] != "arkit"],
        registered=len(registered_ids),
        points3d=rec.num_points3D(),
        has_meter=meter_in,
        primary=True,
    )
    others = [
        FragmentInfo(
            row["id"],
            row["frames"],
            row["registered"],
            row["points3D"],
            row["has_meter"],
            primary=False,
        )
        for row in fragment_rows
    ]
    _primary, frag_status = choose_primary([primary_frag, *others])
    if status == "ok":
        status = frag_status
    for row in fragment_rows:
        row["primary"] = False
    primary_row = {
        "id": "primary",
        "frames": primary_frag.frames,
        "registered": primary_frag.registered,
        "points3D": primary_frag.points3d,
        "has_meter": primary_frag.has_meter,
        "primary": True,
    }

    closeups: dict = {}
    actions: list[str] = []
    row_by_id = {r["keyframe_id"]: r for r in rows}
    present = {f.closeup for f in frames if f.closeup}
    for purpose in ("meter_close", "meter_oblique"):
        owners = [f for f in frames if f.closeup == purpose]
        if not owners and purpose not in present:
            continue
        if not owners:
            continue
        frame = owners[0]
        row = row_by_id.get(frame.id)
        points = int(row["points3D"]) if row else 0
        reproj = float(row["reproj_px"]) if row else None
        ok = closeup_registered(
            in_primary=frame.id in registered_ids,
            points3d=points,
            reproj_px=reproj,
            params=params,
        )
        closeups[purpose] = {
            "frame": frame.id,
            "registered": ok,
            "points3D": points,
            "reproj_px": None if not ok and row is None else reproj,
        }
        if not ok:
            actions.append("retake_meter_closeups")
    if actions:
        actions = ["retake_meter_closeups"]

    spacing = None
    if not guided:
        warnings.append("image_only_unchecked")
        order = sorted(mappable, key=lambda f: (f.timestamp, f.id))
        by_row = {r["name"]: r for r in rows}
        centres = []
        for frame in order:
            row = by_row.get(frame.name)
            if row is None:
                continue
            centres.append(from_column_major(row["pose"])[:3, 3])
        centres_a = np.stack(centres) if len(centres) >= 2 else None
        spacing = photo_spacing(centres_a)
        spacing["units"] = "model"
        warnings.extend(
            spacing_warnings(
                centres_a,
                registered=len(registered_ids),
                given=len(mappable),
                n_models=int(info["models"]),
                params=params,
                metric_scale=False,
            )
        )

    anchor = packet.primary_epoch or (frames[0].epoch if frames else "")
    total_s = time.perf_counter() - started
    public_rows = [{k: v for k, v in r.items() if k != "name"} for r in rows]
    (poses_dir / "cameras.json").write_text(json.dumps(public_rows, indent=1))
    report = {
        "format": "hss-registration-report/v0",
        "mode": mode,
        "status": status,
        "source_frame": f"arkit_world:{anchor}" if guided else "colmap_model",
        "flow": "guided" if guided else "photo",
        "lidar_frames": sum(1 for f in frames if f.has_depth),
        "frames_given": len(packet.frames),
        "frames_in_sfm": len(mappable),
        "frames_registered": registration_counts["frames_registered"],
        "registered_fraction": registration_counts["frames_registered"] / len(frames) if frames else 0.0,
        "registration_counts": {
            "mapper": registration_counts["mapper"],
            "post_registered": registration_counts["post_registered"],
            "carried": registration_counts["carried"],
        },
        "frame_budget": {
            "sfm_max_frames": params.sfm_max_frames,
            "mapper_frames": len(mapper_frames),
            "registered_post": registered_post,
            "arkit_carried": len(arkit_carried_names),
            "frames_dropped_quality": len(excluded),
        },
        "anchor_epoch": anchor,
        "epochs": epoch_rows
        or [
            {
                "id": anchor or "e0",
                "frames": len(frames),
                "registered": len(registered_ids),
                "priors": info["priors"],
                "joined": True,
                "join_method": "anchor",
                "join_rms_m": 0.0,
            }
        ],
        "fragments": [primary_row, *fragment_rows],
        "meter": {"frames": packet.meter_ids, "source": packet.meter_source, "in_primary": meter_in},
        "closeups": closeups,
        "actions": actions,
        "prior": {
            "sigma_m": params.prior_sigma_m,
            "robust": params.robust_prior,
            "count": info["priors"],
            "skipped_limited_tracking": info["skipped_limited_tracking"],
        },
        "arkit_fit": {"rms_m": rms_m, "scale_pct": scale_pct},
        "model_choice": model_choice,
        "matching": {
            "pairs": info["pairs"],
            "spatial_neighbors": params.spatial_neighbors,
            "spatial_radius_m": params.spatial_radius_m,
            "sequential_overlap": params.sequential_overlap,
            "exhaustive": info["exhaustive"],
        },
        "quality": {
            "mean_reproj_px": float(rec.compute_mean_reprojection_error()) if rec.num_points3D() else 0.0,
            "points3D": rec.num_points3D(),
            "mean_track_length": 0.0,
        },
        "timings_s": {
            "extract_s": info["extract_s"],
            "match_s": info["match_s"],
            "mapper_s": info["mapper_s"],
            "total_s": total_s,
        },
        "gpu": "cuda" if params.gpu and bool(getattr(pycolmap, "has_cuda", False)) else "cpu",
        "params": {
            "random_seed": params.seed,
            "num_threads": params.num_threads,
            "mapper_threads": MAPPING_THREADS,
            "blas_threads": int(OPENBLAS_THREADS),
            "blas_coretype": OPENBLAS_CORETYPE,
            "sift_matcher": "faiss",
            "prior_sigma_m": params.prior_sigma_m,
            "robust_prior": params.robust_prior,
            "sfm_max_frames": params.sfm_max_frames,
            "max_image_size": params.max_image_size,
            "max_features": params.max_features,
            "pycolmap": pycolmap.__version__,
            "colmap": colmap_version(),
            "has_cuda": bool(getattr(pycolmap, "has_cuda", False)),
        },
        "warnings": warnings,
    }
    if spacing is not None:
        report["photo_spacing"] = spacing
    # mean track filled from the npz we just wrote
    npz = np.load(poses_dir / "tracks.npz")
    if len(npz["indptr"]) > 1:
        lengths = np.diff(npz["indptr"])
        report["quality"]["mean_track_length"] = float(lengths.mean()) if len(lengths) else 0.0
    path = poses_dir / "registration_report.json"
    path.write_text(json.dumps(report, indent=1))
    validate_report(path)
    _checkpoint_database(db_path)
    if not params.keep_db:
        shutil.rmtree(image_dir, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(poses_dir / "_tmp_fixed", ignore_errors=True)
    if heartbeat:
        heartbeat("write")
    return report


def _cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the pose backbone on one packet")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--meter-frames", default="")
    parser.add_argument("--sfm-max-frames", type=int, default=None)
    parser.add_argument("--max-image-size", type=int, default=None)
    parser.add_argument("--base-model", type=Path, default=None)
    args = parser.parse_args(argv)
    params = PoseParams.from_env()
    if args.cpu:
        params = PoseParams(**{**params.__dict__, "gpu": False})
    if args.sfm_max_frames is not None:
        params = PoseParams(**{**params.__dict__, "sfm_max_frames": args.sfm_max_frames})
    if args.max_image_size is not None:
        params = PoseParams(**{**params.__dict__, "max_image_size": args.max_image_size})
    override = [p for p in args.meter_frames.split(",") if p]
    view = load_packet(args.packet, meter_override=override or None, max_frames=args.max_frames)
    report = run_poses(view, args.out, params=params, base_model=args.base_model)
    print(json.dumps({"status": report["status"], "registered": report["frames_registered"], "mode": report["mode"]}))


if __name__ == "__main__":
    _cli()
