"""Compare a metric scale with the ARKit session scale. ARKit never verifies a placement."""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from server.common.geometry import from_column_major, umeyama
from server.scale.apply import CameraRecord


@dataclass
class ArkitGate:
    available: bool
    passed: bool
    profile: str | None = None
    rel_sd: float | None = None
    s_arkit_per_source: float | None = None
    residual_pct: float | None = None
    band_pct: float | None = None


def arkit_gate(
    cameras: list[CameraRecord],
    packet: dict,
    root: Path,
    scale_factor: float | None,
    *,
    band_pct: float,
    band_pct_2018: float = 30.0,
    report: dict | None = None,
) -> ArkitGate:
    session = packet.get("session") or {}
    profile, rel_sd, band = _profile(session, band_pct, band_pct_2018)
    source_scale = _session_scale(cameras, packet, root, report or {})
    if source_scale is None or scale_factor is None or scale_factor <= 0 or source_scale <= 0:
        return ArkitGate(False, False, profile, rel_sd, None, None, band)
    residual = 100.0 * (float(scale_factor) / float(source_scale) - 1.0)
    return ArkitGate(True, abs(residual) <= band, profile, rel_sd, float(source_scale), float(residual), band)


def _session_scale(cameras, packet: dict, root: Path, report: dict) -> float | None:
    paired = _arkit_centres(cameras, packet, root)
    if paired is not None:
        sfm = np.vstack([camera.pose[:3, 3] for camera in paired[0]])
        arkit = np.vstack(paired[1])
        if len(sfm) >= 3:
            scale, _, _ = umeyama(sfm, arkit, True)
            if scale > 0:
                return float(scale)
    fit = (report.get("arkit_fit") or {}).get("scale_pct")
    if isinstance(fit, (int, float)) and 0.2 < float(fit) < 5:
        return float(fit)
    return None


def _profile(session: dict, band_pct: float, band_pct_2018: float) -> tuple[str, float, float]:
    if session.get("tier") == "arkit_lidar":
        return "lidar_modern", 0.011, band_pct
    if _modern(str(session.get("deviceModel") or "")):
        return "nonlidar_modern", 0.03, band_pct
    return "nonlidar_2018", 0.10, band_pct_2018


def _modern(model: str) -> bool:
    if "SE" in model.upper():
        return False
    match = re.search(r"iPhone\s*(\d+)", model, re.IGNORECASE)
    if not match:
        return True
    return int(match.group(1)) >= 13


def _arkit_centres(cameras, packet: dict, root: Path):
    """Pair each SfM camera with one ARKit centre from a single pose source.

    The pose stream is used only when at least three keyframes match a row by
    timestamp inside the same epoch. Otherwise the embedded keyframe poses are
    a separate set. The two sources are never concatenated, and a keyframe
    with no timestamp is not bound to the first stream row.
    """
    by_id = {camera.keyframe_id: camera for camera in cameras if camera.keyframe_id}
    by_image = {camera.image: camera for camera in cameras}
    keyframes = packet.get("keyframes") or []
    if (packet.get("session") or {}).get("tier") == "photo_only" or not keyframes:
        return None
    stream_meta = (packet.get("streams") or {}).get("arkitPoses") or {}
    stream = stream_meta.get("file")
    poses = _read_stream(root / stream) if stream else []
    rate_hz = stream_meta.get("rateHz")
    if poses:
        paired_sfm = []
        paired_arkit = []
        for frame in keyframes:
            camera = _camera_for(frame, by_id, by_image)
            sample = _nearest_pose(poses, frame, rate_hz)
            if camera is None or sample is None:
                continue
            paired_sfm.append(camera)
            paired_arkit.append(np.array([sample["tx"], sample["ty"], sample["tz"]], float))
        if len(paired_sfm) >= 3:
            return paired_sfm, paired_arkit
    paired_sfm = []
    paired_arkit = []
    for frame in keyframes:
        camera = _camera_for(frame, by_id, by_image)
        centre = _embedded_centre(frame.get("pose"))
        if camera is None or centre is None:
            continue
        paired_sfm.append(camera)
        paired_arkit.append(centre)
    if len(paired_sfm) < 3:
        return None
    return paired_sfm, paired_arkit


def _camera_for(frame: dict, by_id: dict, by_image: dict):
    identity = frame.get("id")
    return by_id.get(identity) or by_image.get(identity)


def _embedded_centre(pose):
    if not pose:
        return None
    if isinstance(pose, list) and pose and not isinstance(pose[0], list):
        matrix = from_column_major(pose)
    else:
        matrix = np.asarray(pose, float)
    if matrix.shape != (4, 4):
        return None
    return matrix[:3, 3]


def _read_stream(path: Path) -> list[dict]:
    """Read an ``arkitPoses`` file. Spec packets are gzipped CSV with a header row."""

    if not path.is_file():
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if raw[:2] == b"\x1f\x8b" or path.suffix == ".gz":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, gzip.BadGzipFile):
            return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped.startswith("{"):
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return [row for row in (_coerce_pose(item) for item in rows) if row is not None]
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        parsed = _coerce_pose(record)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _coerce_pose(row: dict) -> dict | None:
    cleaned = {}
    for key, value in row.items():
        if key is None:
            continue
        name = str(key).strip()
        cleaned[name] = value.strip() if isinstance(value, str) else value
    try:
        return {
            "t": float(cleaned["t"]),
            "epoch": cleaned.get("epoch"),
            "tx": float(cleaned["tx"]),
            "ty": float(cleaned["ty"]),
            "tz": float(cleaned["tz"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _nearest_pose(poses: list[dict], frame: dict, rate_hz) -> dict | None:
    """Match a keyframe to the stream row in its epoch nearest in timestamp."""

    stamp = frame.get("timestamp", frame.get("t"))
    if stamp is None or not poses:
        return None
    try:
        target = float(stamp)
    except (TypeError, ValueError):
        return None
    epoch = frame.get("epoch")
    pool = poses
    if epoch is not None:
        same = [row for row in poses if str(row.get("epoch")) == str(epoch)]
        if same:
            pool = same

    def gap(row: dict) -> float:
        try:
            return abs(float(row["t"]) - target)
        except (KeyError, TypeError, ValueError):
            return 1e9

    best = min(pool, key=gap)
    limit = 0.2
    try:
        if rate_hz is not None and float(rate_hz) > 0:
            limit = 2.0 / float(rate_hz)
    except (TypeError, ValueError):
        limit = 0.2
    if gap(best) > limit:
        return None
    return best
