"""Pose, intrinsics, epoch, motion and gravity checks."""

from __future__ import annotations

import math

import numpy as np

from server.common.geometry import column_major, from_column_major
from server.packet.conventions import (
    DEVICE_FROM_CAMERA,
    orthonormalize,
    rotation_angle_deg,
)
from server.packet.params import PacketParams
from server.packet.report import Bag, pointer


def _pose_matrix(values) -> np.ndarray | None:
    try:
        matrix = from_column_major(values)
    except Exception:
        return None
    if matrix.shape != (4, 4):
        return None
    return matrix


def check_pose(bag: Bag, index: int, values, params: PacketParams) -> np.ndarray | None:
    ptr = pointer("keyframes", index, "pose")
    matrix = _pose_matrix(values)
    if matrix is None or not np.all(np.isfinite(matrix)):
        bag.error("pose_non_finite", ptr, "pose is missing or non-finite")
        return None
    if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-6):
        bag.error("pose_last_row", ptr, "last pose row is not [0, 0, 0, 1]")
    rotation = matrix[:3, :3]
    ortho = float(np.max(np.abs(rotation.T @ rotation - np.eye(3))))
    det = float(np.linalg.det(rotation))
    if ortho > params.rotation_error or abs(det - 1.0) > params.rotation_error:
        bag.error("pose_not_rigid", ptr, f"rotation orthonormality {ortho:.2e}, det {det:.6f}")
    elif ortho > params.rotation_warn or abs(det - 1.0) > params.rotation_warn:
        bag.warn("pose_rotation_drift", ptr, f"rotation drift {ortho:.2e}")
    translation = float(np.linalg.norm(matrix[:3, 3]))
    if translation > params.translation_absurd_m:
        bag.error("pose_translation_absurd", ptr, f"camera is {translation:.0f} m from the origin")
    return matrix


def reorthonormalize_pose(values) -> list[float]:
    matrix = _pose_matrix(values)
    if matrix is None:
        return list(values)
    matrix = matrix.copy()
    matrix[:3, :3] = orthonormalize(matrix[:3, :3])
    matrix[3] = [0, 0, 0, 1]
    return column_major(matrix)


def horizontal_fov_deg(fx: float, width: float) -> float:
    if fx <= 0:
        return float("nan")
    return float(math.degrees(2.0 * math.atan(width / (2.0 * fx))))


def check_intrinsics(bag: Bag, index: int, frame: dict, params: PacketParams) -> None:
    ptr = pointer("keyframes", index, "intrinsics")
    try:
        fx, fy, cx, cy = (float(v) for v in frame["intrinsics"])
        w, h = int(frame["w"]), int(frame["h"])
    except (KeyError, TypeError, ValueError):
        bag.error("intrinsics_implausible", ptr, "intrinsics could not be read")
        return
    fov = horizontal_fov_deg(fx, w)
    if (
        fx <= 0
        or fy <= 0
        or not (0 < cx < w and 0 < cy < h)
        or not (params.fov_error_deg[0] <= fov <= params.fov_error_deg[1])
    ):
        bag.error("intrinsics_implausible", ptr, f"intrinsics {frame.get('intrinsics')} for {w}x{h}")
        return
    if not (params.fov_warn_deg[0] <= fov <= params.fov_warn_deg[1]):
        bag.warn("fov_unusual", ptr, f"horizontal FOV {fov:.1f} deg")
    if abs(fx / fy - 1.0) > params.focal_aspect:
        bag.warn("focal_aspect", ptr, f"fx/fy is {fx / fy:.4f}")
    if abs(cx - w / 2) > params.principal_off_frac * w or abs(cy - h / 2) > params.principal_off_frac * h:
        bag.warn("principal_point_offcentre", ptr, "principal point is off centre")
    long_side = max(w, h)
    if long_side < params.low_resolution_px:
        bag.warn("low_resolution", pointer("keyframes", index, "w"), f"long side is {long_side} px")


def rescale_highres(pk: dict, bag: Bag, params: PacketParams) -> None:
    frames = pk.get("keyframes") or []
    ratios = []
    for frame in frames:
        if frame.get("highResolution"):
            continue
        try:
            ratios.append(float(frame["intrinsics"][0]) / float(frame["w"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    if not ratios:
        return
    median = float(np.median(ratios))
    video = ((pk.get("session") or {}).get("videoFormat") or {}).get("imageResolution") or [0, 0]
    video_w = float(video[0] or 0)
    for i, frame in enumerate(frames):
        if not frame.get("highResolution") or video_w <= 0:
            continue
        try:
            fx = float(frame["intrinsics"][0])
            w = float(frame["w"])
            ratio = fx / w
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if median <= 0 or abs(ratio - median) / median <= params.intrinsics_rescale_frac:
            continue
        scale = w / video_w
        fx, fy, cx, cy = (float(v) * scale for v in frame["intrinsics"])
        frame["intrinsics"] = [fx, fy, cx, cy]
        ext = dict(frame.get("ext") or {})
        hss = dict(ext.get("hss") or {})
        flags = list(hss.get("flags") or [])
        if "intrinsics_rescaled" not in flags:
            flags.append("intrinsics_rescaled")
        hss["flags"] = flags
        ext["hss"] = hss
        frame["ext"] = ext
        bag.warn("intrinsics_rescaled", pointer("keyframes", i, "intrinsics"), "high-resolution intrinsics rescaled")


def check_resolution(pk: dict, bag: Bag) -> None:
    video = ((pk.get("session") or {}).get("videoFormat") or {}).get("imageResolution")
    if not video or len(video) < 2:
        return
    try:
        expect = (int(video[0]), int(video[1]))
    except (TypeError, ValueError):
        return
    for i, frame in enumerate(pk.get("keyframes") or []):
        if frame.get("highResolution"):
            continue
        try:
            size = (int(frame.get("w", 0)), int(frame.get("h", 0)))
        except (TypeError, ValueError):
            continue
        if size != expect:
            bag.warn(
                "keyframe_resolution_differs",
                pointer("keyframes", i, "w"),
                f"keyframe is {frame.get('w')}x{frame.get('h')}, video format is {expect[0]}x{expect[1]}",
            )


def check_epochs(pk: dict, bag: Bag, params: PacketParams) -> str | None:
    epochs = pk.get("epochs") or []
    ids = [ep.get("id") for ep in epochs]
    seen: set[str] = set()
    for i, eid in enumerate(ids):
        if eid in seen:
            bag.error("epoch_duplicate", pointer("epochs", i, "id"), f"duplicate epoch {eid}")
        if isinstance(eid, str):
            seen.add(eid)
    starts = [float(ep.get("startTime", 0.0)) for ep in epochs]
    if starts != sorted(starts):
        bag.warn("epoch_order", pointer("epochs"), "epochs are not sorted by startTime")
    by_id = {ep.get("id"): ep for ep in epochs}
    frames = pk.get("keyframes") or []
    counts: dict[str, int] = {ep.get("id"): 0 for ep in epochs}
    for i, frame in enumerate(frames):
        eid = frame.get("epoch")
        if eid not in by_id:
            bag.error("unknown_epoch", pointer("keyframes", i, "epoch"), f"unknown epoch {eid}")
            continue
        counts[eid] = counts.get(eid, 0) + 1
        start = float(by_id[eid].get("startTime", 0.0))
        if float(frame.get("timestamp", 0.0)) + params.timestamp_before_epoch_s < start:
            bag.warn(
                "timestamp_before_epoch",
                pointer("keyframes", i, "timestamp"),
                "timestamp is more than 0.5 s before the epoch start",
            )
    for i, ep in enumerate(epochs):
        if counts.get(ep.get("id"), 0) == 0:
            bag.warn("empty_epoch", pointer("epochs", i, "id"), f"epoch {ep.get('id')} has no keyframes")
    kf_ids = {frame.get("id"): frame for frame in frames}
    for i, tap in enumerate(pk.get("taps") or []):
        if not tap.get("epoch"):
            continue
        frame = kf_ids.get(tap.get("keyframe"))
        if frame is None or frame.get("epoch") not in by_id:
            continue
        if tap["epoch"] != frame.get("epoch"):
            bag.error("tap_epoch_mismatch", pointer("taps", i, "epoch"), "tap epoch does not match its keyframe")
    order = sorted(range(len(frames)), key=lambda i: _sort_key(frames[i], by_id))
    if order != list(range(len(frames))):
        bag.warn("keyframe_order", pointer("keyframes"), "keyframes are not ordered by epoch start, timestamp, id")
    _duplicate_times(frames, by_id, bag, params)
    return _primary_epoch(epochs, counts)


def _sort_key(frame: dict, epochs: dict) -> tuple:
    ep = epochs.get(frame.get("epoch")) or {}
    return (float(ep.get("startTime", 0.0)), float(frame.get("timestamp", 0.0)), str(frame.get("id")))


def _duplicate_times(frames: list[dict], epochs: dict, bag: Bag, params: PacketParams) -> None:
    grouped: dict[str, list[int]] = {}
    for i, frame in enumerate(frames):
        grouped.setdefault(str(frame.get("epoch")), []).append(i)
    for indexes in grouped.values():
        indexes.sort(key=lambda i: (float(frames[i].get("timestamp", 0.0)), str(frames[i].get("id"))))
        for a, b in zip(indexes, indexes[1:], strict=False):
            dt = abs(float(frames[b].get("timestamp", 0.0)) - float(frames[a].get("timestamp", 0.0)))
            if dt >= params.timestamp_dup_s:
                continue
            ca = _center(frames[a])
            cb = _center(frames[b])
            if ca is None or cb is None:
                continue
            if float(np.linalg.norm(ca - cb)) > params.timestamp_dup_m:
                bag.error(
                    "keyframe_timestamp_conflict",
                    pointer("keyframes", b, "timestamp"),
                    "two keyframes share a timestamp but not a pose",
                )
            else:
                bag.warn("duplicate_keyframe", pointer("keyframes", b, "id"), "duplicate keyframe timestamp and pose")


def _center(frame: dict) -> np.ndarray | None:
    matrix = _pose_matrix(frame.get("pose"))
    if matrix is None or not np.all(np.isfinite(matrix)):
        return None
    return matrix[:3, 3]


def _primary_epoch(epochs: list[dict], counts: dict[str, int]) -> str | None:
    if not epochs:
        return None

    def rank(ep: dict) -> tuple[int, float]:
        eid = ep.get("id")
        count = counts.get(eid, 0) if isinstance(eid, str) else 0
        try:
            start = float(ep.get("startTime", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        return (-count, start)

    best = sorted(epochs, key=rank)
    return best[0].get("id")


def pitch_roll_deg(rotation: np.ndarray) -> tuple[float, float]:
    """Viewing axis (−z) against the horizontal, up positive, and roll of camera +y."""
    forward = -rotation[:, 2]
    pitch = math.degrees(math.atan2(float(forward[1]), float(math.hypot(forward[0], forward[2]))))
    up = rotation[:, 1]
    right = rotation[:, 0]
    roll = (
        math.degrees(math.atan2(float(right[1]), float(up[1])))
        if abs(float(up[1])) + abs(float(right[1])) > 1e-9
        else 0.0
    )
    return pitch, roll


def add_flag(record: dict, flag: str) -> None:
    ext = dict(record.get("ext") or {})
    hss = dict(ext.get("hss") or {})
    flags = list(hss.get("flags") or [])
    if flag not in flags:
        flags.append(flag)
    hss["flags"] = flags
    ext["hss"] = hss
    record["ext"] = ext


def check_motion(pk: dict, bag: Bag, params: PacketParams) -> None:
    frames = pk.get("keyframes") or []
    epochs = {ep.get("id"): ep for ep in pk.get("epochs") or []}
    grouped: dict[str, list[int]] = {}
    limited = 0
    for i, frame in enumerate(frames):
        if frame.get("tracking") != "normal":
            limited += 1
            add_flag(frame, "limited_tracking")
        grouped.setdefault(str(frame.get("epoch")), []).append(i)
        matrix = _pose_matrix(frame.get("pose"))
        if matrix is not None and np.all(np.isfinite(matrix)):
            pitch, roll = pitch_roll_deg(matrix[:3, :3])
            ext = dict(frame.get("ext") or {})
            hss = dict(ext.get("hss") or {})
            hss["pitchDeg"] = pitch
            hss["rollDeg"] = roll
            ext["hss"] = hss
            frame["ext"] = ext
            if pitch > params.pitch_up_deg:
                add_flag(frame, "pitched_up")
            if pitch < params.pitch_down_deg:
                add_flag(frame, "pitched_down")
    if frames and limited / len(frames) > params.limited_tracking_share:
        bag.warn("limited_tracking_share", pointer("keyframes"), f"{limited} of {len(frames)} keyframes are limited")
    bad_pairs = 0
    pairs = 0
    for indexes in grouped.values():
        usable = []
        for i in indexes:
            if frames[i].get("tracking") != "normal":
                continue
            matrix = _pose_matrix(frames[i].get("pose"))
            if matrix is None or not np.all(np.isfinite(matrix)):
                continue
            usable.append((i, matrix))
        usable.sort(key=lambda item: float(frames[item[0]].get("timestamp", 0.0)))
        for (i1, m1), (i2, m2) in zip(usable, usable[1:], strict=False):
            dt = float(frames[i2].get("timestamp", 0.0)) - float(frames[i1].get("timestamp", 0.0))
            if dt <= 0:
                continue
            pairs += 1
            jumped = False
            if dt >= params.pose_jump_min_dt_s:
                speed = float(np.linalg.norm(m2[:3, 3] - m1[:3, 3])) / dt
                if speed > params.pose_jump_m_s:
                    bag.warn("pose_jump", pointer("keyframes", i2, "pose"), f"speed {speed:.2f} m/s")
                    add_flag(frames[i2], "pose_jump_after")
                    jumped = True
            spin = rotation_angle_deg(m1[:3, :3], m2[:3, :3]) / dt
            if spin > params.pose_spin_deg_s:
                bag.warn("pose_spin", pointer("keyframes", i2, "pose"), f"spin {spin:.0f} deg/s")
                jumped = True
            if jumped:
                bad_pairs += 1
    if pairs and bad_pairs / pairs > params.tracking_unstable_share:
        bag.warn("tracking_unstable", pointer("keyframes"), f"{bad_pairs} of {pairs} steps jump or spin")
    _ = epochs


def check_gravity_stream(pk: dict, rows: list[tuple[float, np.ndarray]] | None, bag: Bag, params: PacketParams) -> None:
    """`rows` are (timestamp, grav xyz) from the motion stream, already in the device frame."""
    if not rows:
        return
    times = np.array([t for t, _ in rows])
    angles = []
    for frame in pk.get("keyframes") or []:
        matrix = _pose_matrix(frame.get("pose"))
        if matrix is None:
            continue
        idx = int(np.argmin(np.abs(times - float(frame.get("timestamp", 0.0)))))
        if abs(float(times[idx]) - float(frame.get("timestamp", 0.0))) > params.gravity_match_s:
            continue
        grav = rows[idx][1]
        norm = float(np.linalg.norm(grav))
        if norm == 0:
            continue
        g_pose = (matrix[:3, :3] @ DEVICE_FROM_CAMERA.T).T @ np.array([0.0, -1.0, 0.0])
        cosine = float(np.clip(np.dot(grav, g_pose) / norm, -1.0, 1.0))
        angles.append(math.degrees(math.acos(cosine)))
    if not angles:
        return
    median = float(np.median(angles))
    if median > params.gravity_error_deg and len(angles) >= params.gravity_error_min_rows:
        bag.error("gravity_conventions", pointer("streams", "motion"), f"median gravity error {median:.1f} deg")
    elif median > params.gravity_warn_deg:
        bag.warn("gravity_mismatch", pointer("streams", "motion"), f"median gravity error {median:.1f} deg")


def still_gravity(still: dict, params: PacketParams, bag: Bag, index: int) -> None:
    grav = still.get("gravity")
    if grav is None:
        return
    vector = np.asarray(grav, dtype=float)
    norm = float(np.linalg.norm(vector))
    if not (params.gravity_mag[0] <= norm <= params.gravity_mag[1]):
        bag.warn("gravity_magnitude", pointer("stills", index, "gravity"), f"gravity magnitude {norm:.3f}")
        if norm == 0:
            return
    camera = DEVICE_FROM_CAMERA.T @ (vector / norm)
    # Camera +y is up; gravity in a level camera is (0, -1, 0). Pitch from the viewing axis.
    pitch = math.degrees(math.atan2(float(-camera[2]), float(-camera[1])))
    roll = math.degrees(math.atan2(float(camera[0]), float(-camera[1])))
    ext = dict(still.get("ext") or {})
    hss = dict(ext.get("hss") or {})
    hss["gravityCam"] = [float(v) for v in camera]
    hss["pitchDeg"] = pitch
    hss["rollDeg"] = roll
    ext["hss"] = hss
    still["ext"] = ext
    if pitch > params.pitch_up_deg:
        add_flag(still, "pitched_up")
    if pitch < params.pitch_down_deg:
        add_flag(still, "pitched_down")
