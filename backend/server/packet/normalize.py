"""Normalized packet: one JPEG per image, rigid poses, derived block under ext.hss."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from server.packet.conventions import tap_replay_error
from server.packet.exif_intrinsics import read_exif, resolve_still_intrinsics
from server.packet.images import decode_all, sniff_format, transcode_heic
from server.packet.integrity import resolve_under
from server.packet.params import DEFAULT_PARAMS, PacketParams
from server.packet.poses import _center, add_flag, check_motion, reorthonormalize_pose, still_gravity
from server.packet.purposes import KNOWN_PURPOSES
from server.packet.report import Bag, PacketError, ValidationReport, pointer
from server.packet.shape import scrub


class EpochDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    keyframes: int
    durationS: float
    pathLengthM: float
    trackingHistogram: dict[str, int]
    primary: bool
    medianSpacingM: float | None = None
    keyframesPerS: float = 0.0


class DepthDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyframesWithDepth: int
    coverage: float
    medianDepthM: float | None = None
    confidenceShare: list[float] | None = None


class MeterFrames(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close: str | None = None
    oblique: str | None = None
    tapKeyframe: str | None = None


class StreamDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: int
    rateHzMeasured: float | None = None
    tMin: float | None = None
    tMax: float | None = None
    scaleToG: float = 1.0


class PacketStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyframes: int
    stills: int
    taps: int
    planes: int
    imagesDecodable: int
    durationS: float
    keyframesPerS: float
    medianSpacingM: float | None = None
    blurredShare: float = 0.0
    highIsoShare: float = 0.0


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    errors: int
    warnings: int
    reportPath: str | None = None


class PacketDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["hss-normalized-packet/v0"] = "hss-normalized-packet/v0"
    normalizedAt: str
    validator: str
    flow: Literal["guided", "photo"]
    flowSource: Literal["capture_record", "inferred"]
    branch: Literal["lidar", "image"]
    isAmendment: bool
    evidence: bool
    primaryEpoch: str | None = None
    epochs: list[EpochDerived]
    depth: DepthDerived
    purposes: dict[str, list[str]]
    meterFrames: MeterFrames
    streams: dict[str, StreamDerived]
    stats: PacketStats
    notes: list[str]
    validation: ValidationSummary
    captureCheck: dict[str, Any] | None = None


class FrameDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["keyframe", "still"]
    decodable: bool
    format: Literal["jpeg", "heic->jpeg"]
    bytes: int
    sha256: str
    flags: list[str]
    pitchDeg: float | None = None
    rollDeg: float | None = None
    gravityCam: list[float] | None = None
    intrinsicsAssumed: bool = False
    focalRelBound: float = 0.02
    originalImg: str | None = None
    originalOrientation: int | None = None


class TapDerived(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replayError: float
    epoch: str


class NormalizedPacket(BaseModel):
    """In-memory packet plus `derived`. On disk, `derived` lives at `ext.hss`."""

    model_config = ConfigDict(extra="allow")
    derived: PacketDerived
    root: Path | None = Field(default=None, exclude=True)
    _packet: dict[str, Any] = PrivateAttr()

    def __init__(self, packet: dict, derived: PacketDerived, root: Path | None = None) -> None:
        super().__init__(derived=derived, root=root)
        object.__setattr__(self, "_packet", packet)

    def to_json(self, path: Path) -> None:
        payload = copy.deepcopy(self._packet)
        ext = dict(payload.get("ext") or {})
        ext["hss"] = self.derived.model_dump(mode="json")
        payload["ext"] = ext
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(payload), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path, root: Path) -> NormalizedPacket:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ext = dict(payload.get("ext") or {})
        hss = ext.get("hss")
        if not isinstance(hss, dict):
            raise ValueError("normalized packet has no ext.hss")
        derived = PacketDerived.model_validate(hss)
        return cls(payload, derived, root)


def load_normalized(path: Path, root: Path) -> NormalizedPacket:
    return NormalizedPacket.from_json(path, root)


def _canonical(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_records(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _sha256(entry: dict | None) -> str:
    digest = entry.get("sha256") if entry else None
    return digest if isinstance(digest, str) else "0" * 64


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (ValueError, OverflowError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not np.isfinite(number):
        return default
    return number


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def validator_id() -> str:
    sha = os.environ.get("GIT_SHA", "")
    if not sha:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short=12", "HEAD"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            sha = "unknown"
    return f"ws-02/{sha[:12]}"


def derived_of(record: dict) -> FrameDerived | TapDerived:
    hss = (record.get("ext") or {}).get("hss") or {}
    if "replayError" in hss:
        return TapDerived.model_validate(hss)
    return FrameDerived.model_validate(hss)


def epoch_motion(pk: dict) -> list[dict]:
    epochs = _as_records(pk.get("epochs"))
    frames = _as_records(pk.get("keyframes"))
    counts: dict[str, int] = {}
    for frame in frames:
        counts[str(frame.get("epoch"))] = counts.get(str(frame.get("epoch")), 0) + 1
    if not epochs and frames:
        epochs = [{"id": frames[0].get("epoch"), "startTime": 0.0}]
    primary = None
    if epochs:
        primary = sorted(
            epochs, key=lambda ep: (-counts.get(str(ep.get("id")), 0), _as_float(ep.get("startTime", 0.0)))
        )[0].get("id")
    out = []
    for ep in epochs:
        eid = ep.get("id")
        group = [frame for frame in frames if frame.get("epoch") == eid]
        group.sort(key=lambda frame: (_as_float(frame.get("timestamp", 0.0)), str(frame.get("id"))))
        hist: dict[str, int] = {}
        for frame in group:
            state = str(frame.get("tracking") or "unknown")
            hist[state] = hist.get(state, 0) + 1
        normal = []
        for frame in group:
            if frame.get("tracking") not in (None, "normal"):
                continue
            center = _center(frame)
            if center is not None:
                normal.append((_as_float(frame.get("timestamp", 0.0)), center))
        length = 0.0
        steps = []
        for (_t1, c1), (_t2, c2) in zip(normal, normal[1:], strict=False):
            step = float(np.linalg.norm(c2 - c1))
            length += step
            steps.append(step)
        duration = 0.0
        if group:
            duration = _as_float(group[-1].get("timestamp", 0.0)) - _as_float(group[0].get("timestamp", 0.0))
        out.append(
            {
                "id": eid,
                "keyframes": len(group),
                "durationS": max(duration, 0.0),
                "pathLengthM": length,
                "trackingHistogram": hist,
                "primary": eid == primary,
                "medianSpacingM": float(np.median(steps)) if steps else None,
                "keyframesPerS": (len(group) / duration) if duration > 0 else 0.0,
            }
        )
    return out


def sort_keyframes(pk: dict) -> None:
    epochs = {
        ep.get("id"): float(ep.get("startTime", 0.0) or 0.0) for ep in pk.get("epochs") or [] if isinstance(ep, dict)
    }
    frames = [frame for frame in pk.get("keyframes") or [] if isinstance(frame, dict)]

    def _key(frame: dict) -> tuple:
        try:
            stamp = float(frame.get("timestamp", 0.0) or 0.0)
        except (TypeError, ValueError):
            stamp = 0.0
        return (epochs.get(frame.get("epoch"), 0.0), stamp, str(frame.get("id")))

    pk["keyframes"] = sorted(frames, key=_key)


def _branch(pk: dict, root: Path | None) -> Literal["lidar", "image"]:
    session = _as_dict(pk.get("session"))
    if not (session.get("lidarAvailable") and session.get("sceneDepthEnabled")):
        return "image"
    if session.get("lidarAvailable") is False:
        return "image"
    if root is None:
        return "image"
    for frame in _as_records(pk.get("keyframes")):
        depth = frame.get("depth")
        if not isinstance(depth, dict) or not depth.get("file") or not depth.get("confidenceFile"):
            continue
        dpath = resolve_under(root, depth["file"])
        cpath = resolve_under(root, depth["confidenceFile"])
        if dpath is None or cpath is None or not dpath.is_file() or not cpath.is_file():
            continue
        try:
            expect_d = int(depth["w"]) * int(depth["h"]) * 4
            expect_c = int(depth["w"]) * int(depth["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if dpath.stat().st_size == expect_d and cpath.stat().st_size == expect_c:
            return "lidar"
    return "image"


def _depth_derived(pk: dict, root: Path | None, params: PacketParams) -> DepthDerived:
    frames = [
        frame
        for frame in _as_records(pk.get("keyframes"))
        if isinstance(frame.get("depth"), dict) and frame["depth"].get("file")
    ]
    total = len(_as_records(pk.get("keyframes")))
    if not frames or root is None:
        return DepthDerived(keyframesWithDepth=len(frames), coverage=(len(frames) / total) if total else 0.0)
    medians = []
    shares = []
    for frame in frames[:: max(params.depth_sample_stride, 1)]:
        depth = frame["depth"]
        dpath = resolve_under(root, depth["file"])
        if dpath is None or not dpath.is_file():
            continue
        values = np.fromfile(dpath, dtype="<f4")
        finite = values[np.isfinite(values)]
        if len(values):
            medians.append(float(np.median(finite)) if len(finite) else float("nan"))
        cfile = depth.get("confidenceFile")
        if cfile:
            cpath = resolve_under(root, cfile)
            if cpath is not None and cpath.is_file():
                conf = np.fromfile(cpath, dtype="u1")
                if len(conf):
                    shares.append([(conf == j).mean() for j in (0, 1, 2)])
    median = float(np.median(medians)) if medians else None
    share = [float(v) for v in np.mean(shares, axis=0)] if shares else None
    return DepthDerived(
        keyframesWithDepth=len(frames),
        coverage=(len(frames) / total) if total else 0.0,
        medianDepthM=median,
        confidenceShare=share,
    )


def _purposes(pk: dict) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for frame in list(pk.get("keyframes") or []) + list(pk.get("stills") or []):
        if not isinstance(frame, dict):
            continue
        purpose = frame.get("purpose")
        if not isinstance(purpose, str) or not purpose:
            continue
        key = purpose if purpose in KNOWN_PURPOSES else "other"
        frame_id = frame.get("id")
        if isinstance(frame_id, str):
            index.setdefault(key, []).append(frame_id)
    return index


def _meter_frames(pk: dict) -> MeterFrames:
    def first(purpose: str) -> str | None:
        for frame in _as_records(pk.get("stills")) + _as_records(pk.get("keyframes")):
            if frame.get("purpose") == purpose:
                return frame.get("id")
        return None

    tap_kf = None
    for tap in _as_records(pk.get("taps")):
        if tap.get("label") == "meter":
            tap_kf = tap.get("keyframe")
            break
    return MeterFrames(close=first("meter_close"), oblique=first("meter_oblique"), tapKeyframe=tap_kf)


def _flag_share(pk: dict, flag: str, *, keyframes_only: bool) -> float:
    frames = _as_records(pk.get("keyframes"))
    if not keyframes_only:
        frames.extend(_as_records(pk.get("stills")))
    if not frames:
        return 0.0
    hit = 0
    for frame in frames:
        flags = _as_dict(_as_dict(frame.get("ext")).get("hss")).get("flags")
        if isinstance(flags, list) and flag in flags:
            hit += 1
    return hit / len(frames)


def _file_row(pk: dict, rel: str) -> dict | None:
    for entry in _as_records(pk.get("files")):
        if entry.get("path") == rel:
            return entry
    return None


def _attach_frame_derived(pk: dict, kind: str, record: dict, root: Path | None, decodable: bool) -> None:
    rel = record.get("img")
    entry = _file_row(pk, rel) if isinstance(rel, str) else None
    raw_ext = record.get("ext")
    ext = dict(raw_ext) if isinstance(raw_ext, dict) else {}
    raw_hss = ext.get("hss")
    previous = dict(raw_hss) if isinstance(raw_hss, dict) else {}
    # Transcode rewrites img to the JPEG and keeps the HEIC path on originalImg.
    fmt = "heic->jpeg" if previous.get("originalImg") else "jpeg"
    if fmt == "jpeg" and root is not None and isinstance(rel, str):
        full = resolve_under(root, rel)
        if full is not None and full.is_file() and sniff_format(full.read_bytes()[:32]) == "heic":
            fmt = "heic->jpeg"
    raw_flags = previous.get("flags")
    flags = [str(flag) for flag in raw_flags] if isinstance(raw_flags, list) else []
    gravity = previous.get("gravityCam")
    if not isinstance(gravity, list):
        gravity = None
    orientation = previous.get("originalOrientation")
    if isinstance(orientation, bool) or not isinstance(orientation, int):
        orientation = None
    try:
        derived = FrameDerived(
            kind=kind,  # type: ignore[arg-type]
            decodable=decodable,
            format=fmt,  # type: ignore[arg-type]
            bytes=_as_int(entry.get("bytes") if entry else 0),
            sha256=_sha256(entry),
            flags=flags,
            pitchDeg=_opt_float(previous.get("pitchDeg")),
            rollDeg=_opt_float(previous.get("rollDeg")),
            gravityCam=gravity,
            intrinsicsAssumed=bool(previous.get("intrinsicsAssumed", False)),
            focalRelBound=_as_float(previous.get("focalRelBound", 0.02), 0.02),
            originalImg=previous.get("originalImg") if isinstance(previous.get("originalImg"), str) else None,
            originalOrientation=orientation,
        )
    except Exception:
        return
    ext["hss"] = derived.model_dump(mode="json")
    record["ext"] = ext


def normalize_packet(
    manifest: Path,
    *,
    flow: Literal["guided", "photo"] | None = None,
    report: ValidationReport | None = None,
    params: PacketParams = DEFAULT_PARAMS,
    decoded: dict | None = None,
) -> NormalizedPacket:
    try:
        pk = json.loads(manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PacketError("io", message="packet.json could not be read") from exc
    if not isinstance(pk, dict):
        raise PacketError("invalid", message="packet must be an object")
    pk = scrub(pk)
    root = manifest.parent
    try:
        return _normalize_loaded(pk, root, flow, report, params, decoded)
    except PacketError:
        raise
    except Exception:
        return _fallback_normalized(pk, root, report)


def _fallback_normalized(pk: dict, root: Path, report: ValidationReport | None) -> NormalizedPacket:
    """One normalized packet when a schema-invalid subtree still cannot be walked."""
    session = _as_dict(pk.get("session"))
    derived = PacketDerived(
        normalizedAt=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        validator=validator_id(),
        flow="photo" if session.get("tier") == "photo_only" else "guided",
        flowSource="inferred",
        branch="image",
        isAmendment=False,
        evidence=_as_dict(pk.get("source")).get("kind") != "synthetic",
        epochs=[],
        depth=DepthDerived(keyframesWithDepth=0, coverage=0.0),
        purposes={},
        meterFrames=MeterFrames(),
        streams={},
        stats=PacketStats(
            keyframes=0,
            stills=0,
            taps=0,
            planes=0,
            imagesDecodable=0,
            durationS=0.0,
            keyframesPerS=0.0,
        ),
        notes=[],
        validation=ValidationSummary(
            ok=False if report is None else report.ok,
            errors=1 if report is None else len(report.errors),
            warnings=0 if report is None else len(report.warnings),
            reportPath="packet/validation.json",
        ),
    )
    return NormalizedPacket(pk, derived, root)


def _normalize_loaded(
    pk: dict,
    root: Path,
    flow: Literal["guided", "photo"] | None,
    report: ValidationReport | None,
    params: PacketParams,
    decoded: dict | None,
) -> NormalizedPacket:
    bag = Bag()
    notes = transcode_heic(pk, root, params)
    stills = _as_records(pk.get("stills"))
    for note_code in ("heic_transcoded", "heic_orientation_applied"):
        if any(note_code in (still.get("_hss_notes") or []) for still in stills):
            bag.warn(note_code, pointer("stills"), note_code)
    for still in stills:
        still.pop("_hss_notes", None)
    _apply_exif(pk, root, bag, params)
    session = _as_dict(pk.get("session"))
    for still_i, still in enumerate(stills):
        if session.get("tier") == "photo_only":
            still_gravity(still, params, bag, still_i)
        if not still.get("keyframe"):
            add_flag(still, "no_pose")
    for frame in _as_records(pk.get("keyframes")):
        if isinstance(frame.get("pose"), list):
            try:
                frame["pose"] = reorthonormalize_pose(frame["pose"])
            except Exception:
                frame.pop("pose", None)
    sort_keyframes(pk)
    from server.packet.poses import rescale_highres

    rescale_highres(pk, bag, params)
    check_motion(pk, Bag(), params)
    from server.packet.validate import apply_quality_flags

    apply_quality_flags(pk, root, params)
    epochs = epoch_motion(pk)
    primary = next((ep["id"] for ep in epochs if ep["primary"]), None)
    tier = session.get("tier")
    if flow is None:
        flow = "photo" if tier == "photo_only" else "guided"
        flow_source: Literal["capture_record", "inferred"] = "inferred"
    else:
        flow_source = "capture_record"
    branch = _branch(pk, root)
    purposes = _purposes(pk)
    meters = _meter_frames(pk)
    depth = _depth_derived(pk, root, params)
    from server.packet.streams import check_streams

    stream_stats = check_streams(pk, root, Bag(), params)
    decodable_n = 0
    if decoded is None:
        listed: dict[str, dict] = {}
        for entry in _as_records(pk.get("files")):
            path = entry.get("path")
            if isinstance(path, str) and path not in listed:
                listed[path] = entry
        decoded = decode_all(pk, root, listed)
    for i, frame in enumerate(pk.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        ok = decoded.get(("keyframe", i), True)
        decodable_n += int(ok)
        _attach_frame_derived(pk, "keyframe", frame, root, ok)
    for i, still in enumerate(pk.get("stills") or []):
        if not isinstance(still, dict):
            continue
        ok = decoded.get(("still", i), True)
        decodable_n += int(ok)
        _attach_frame_derived(pk, "still", still, root, ok)
    frames_by_id = {frame.get("id"): frame for frame in _as_records(pk.get("keyframes"))}
    for tap in _as_records(pk.get("taps")):
        raw_ext = tap.get("ext")
        ext = dict(raw_ext) if isinstance(raw_ext, dict) else {}
        raw_hss = ext.get("hss")
        hss = dict(raw_hss) if isinstance(raw_hss, dict) else {}
        tap_frame = frames_by_id.get(tap.get("keyframe"))
        try:
            if "replayError" in hss:
                replay = float(hss["replayError"])
            elif tap_frame is None:
                continue
            else:
                replay = tap_replay_error(tap_frame, tap)
        except Exception:
            continue
        ext["hss"] = TapDerived(
            replayError=replay,
            epoch=str(tap.get("epoch") or _epoch_of(pk, tap.get("keyframe")) or ""),
        ).model_dump(mode="json")
        tap["ext"] = ext
    primary_ep = next((ep for ep in epochs if ep["primary"]), None)
    stats = PacketStats(
        keyframes=len(pk.get("keyframes") or []),
        stills=len(pk.get("stills") or []),
        taps=len(pk.get("taps") or []),
        planes=len(pk.get("planes") or []),
        imagesDecodable=decodable_n if decoded else len(pk.get("keyframes") or []) + len(pk.get("stills") or []),
        durationS=float(primary_ep["durationS"]) if primary_ep else 0.0,
        keyframesPerS=float(primary_ep["keyframesPerS"]) if primary_ep else 0.0,
        medianSpacingM=primary_ep.get("medianSpacingM") if primary_ep else None,
        blurredShare=_flag_share(pk, "motion_blur", keyframes_only=True),
        highIsoShare=_flag_share(pk, "high_iso", keyframes_only=False),
    )
    validation = ValidationSummary(
        ok=True if report is None else report.ok,
        errors=0 if report is None else len(report.errors),
        warnings=0 if report is None else len(report.warnings),
        reportPath="packet/validation.json",
    )
    derived = PacketDerived(
        normalizedAt=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        validator=validator_id(),
        flow=flow,
        flowSource=flow_source,
        branch=branch,
        isAmendment=bool(pk.get("parentPacketId")),
        evidence=_as_dict(pk.get("source")).get("kind") != "synthetic",
        primaryEpoch=primary if isinstance(primary, str) else None,
        epochs=_epoch_models(epochs),
        depth=depth,
        purposes=purposes,
        meterFrames=meters,
        streams=_stream_models(stream_stats),
        stats=stats,
        notes=notes,
        validation=validation,
    )
    return NormalizedPacket(pk, derived, root)


def _epoch_models(epochs: list[dict]) -> list[EpochDerived]:
    models: list[EpochDerived] = []
    for ep in epochs:
        if not isinstance(ep.get("id"), str):
            continue
        try:
            models.append(EpochDerived.model_validate(ep))
        except Exception:
            continue
    return models


def _stream_models(stats: dict) -> dict[str, StreamDerived]:
    models: dict[str, StreamDerived] = {}
    for name, row in stats.items():
        try:
            models[name] = StreamDerived.model_validate(row)
        except Exception:
            continue
    return models


def _epoch_of(pk: dict, keyframe_id: str | None) -> str | None:
    for frame in _as_records(pk.get("keyframes")):
        if frame.get("id") == keyframe_id:
            return frame.get("epoch")
    return None


def _apply_exif(pk: dict, root: Path, bag: Bag, params: PacketParams) -> None:
    if _as_dict(pk.get("session")).get("tier") != "photo_only":
        return
    keyframes: dict[str, dict] = {}
    for frame in _as_records(pk.get("keyframes")):
        frame_id = frame.get("id")
        if isinstance(frame_id, str):
            keyframes[frame_id] = frame
    for i, still in enumerate(_as_records(pk.get("stills"))):
        file_exif = None
        rel = still.get("img")
        if isinstance(rel, str):
            full = resolve_under(root, rel)
            if full is not None and full.is_file():
                try:
                    file_exif, _gps = read_exif(full)
                except Exception:
                    file_exif = None
        try:
            decision = resolve_still_intrinsics(still, keyframes, file_exif, params)
        except Exception:
            continue
        for code, message in decision["warnings"]:
            level = "error" if code == "exif_focal_out_of_range" else "warning"
            if code == "exif_missing":
                ptr = pointer("stills", i)
            elif code == "exif_focal_out_of_range":
                ptr = pointer("stills", i, "exif", "FocalLenIn35mmFilm")
            else:
                ptr = pointer("stills", i, "intrinsics")
            bag.add(level, code, ptr, message)  # type: ignore[arg-type]
        raw_ext = still.get("ext")
        ext = dict(raw_ext) if isinstance(raw_ext, dict) else {}
        raw_hss = ext.get("hss")
        hss = dict(raw_hss) if isinstance(raw_hss, dict) else {}
        hss["intrinsicsAssumed"] = decision["intrinsicsAssumed"]
        hss["focalRelBound"] = decision["focalRelBound"]
        ext["hss"] = hss
        still["ext"] = ext
        if not decision["keep"]:
            still["intrinsics"] = decision["intrinsics"]
            still["intrinsicsSource"] = decision["intrinsicsSource"]


def merge_amendment(parent: NormalizedPacket, amendment: NormalizedPacket, aid: str) -> NormalizedPacket:
    base = copy.deepcopy(parent._packet)
    extra = copy.deepcopy(amendment._packet)
    prefix = f"{aid}/"

    def rewrite(records: list[dict], fields: tuple[str, ...]) -> None:
        for record in records:
            if "id" in record:
                record["id"] = prefix + str(record["id"])
            for field in fields:
                if record.get(field):
                    record[field] = prefix + str(record[field])

    rewrite(extra.get("keyframes") or [], ("epoch",))
    rewrite(extra.get("stills") or [], ("keyframe",))
    rewrite(extra.get("taps") or [], ("keyframe", "epoch"))
    rewrite(extra.get("planes") or [], ("epoch",))
    rewrite(extra.get("epochs") or [], ())
    for ep in extra.get("epochs") or []:
        ep["id"] = prefix + str(ep["id"])
    for key in ("keyframes", "stills", "taps", "planes", "epochs"):
        base.setdefault(key, [])
        base[key] = list(base.get(key) or []) + list(extra.get(key) or [])
    seen = {entry.get("path") for entry in base.get("files") or []}
    for entry in extra.get("files") or []:
        if entry.get("path") not in seen:
            base.setdefault("files", []).append(entry)
            seen.add(entry.get("path"))
    epochs = epoch_motion(base)
    derived = parent.derived.model_copy(deep=True)
    derived.purposes = _purposes(base)
    derived.meterFrames = _meter_frames(base)
    primary = parent.derived.primaryEpoch
    derived.primaryEpoch = primary
    derived.epochs = [EpochDerived.model_validate({**ep, "primary": ep["id"] == primary}) for ep in epochs]
    primary_ep = next((ep for ep in epochs if ep["id"] == primary), None)
    derived.stats = PacketStats(
        keyframes=len(base.get("keyframes") or []),
        stills=len(base.get("stills") or []),
        taps=len(base.get("taps") or []),
        planes=len(base.get("planes") or []),
        imagesDecodable=parent.derived.stats.imagesDecodable + amendment.derived.stats.imagesDecodable,
        durationS=float(primary_ep["durationS"]) if primary_ep else derived.stats.durationS,
        keyframesPerS=float(primary_ep["keyframesPerS"]) if primary_ep else derived.stats.keyframesPerS,
        medianSpacingM=primary_ep.get("medianSpacingM") if primary_ep else derived.stats.medianSpacingM,
        blurredShare=_flag_share(base, "motion_blur", keyframes_only=True),
        highIsoShare=_flag_share(base, "high_iso", keyframes_only=False),
    )
    return NormalizedPacket(base, derived, parent.root)
