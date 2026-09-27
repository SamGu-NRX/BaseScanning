"""Manifest checks that need no geometry. WS-02 owns the full-stage checks."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from server.contracts._base import JsonInt, JsonNum, Strict, keep_null
from server.contracts.api import ApiError, ApiErrorItem
from server.contracts.packet import Packet, Tier

IssueLevel = Literal["error", "warning"]
_HEAD_ON_MAX_DEG = 30.0
_OBLIQUE_DEG = (15.0, 50.0)
_DISTANCE_M = (0.3, 3.0)


class ValidationIssue(Strict):
    code: str
    level: IssueLevel
    pointer: str
    message: str
    value: Any | None = keep_null()


class ValidationReport(Strict):
    format: Literal["hss-validation-report/v0"] = "hss-validation-report/v0"
    packetId: str | None = None
    formatVersion: str | None = None
    tier: Tier | None = None
    stage: Literal["manifest", "full"]
    ok: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    keyframes: JsonInt = 0
    stills: JsonInt = 0
    taps: JsonInt = 0
    epochs: JsonInt = 0
    files: JsonInt = 0
    filesHashed: JsonInt = 0
    maxTapReplayError: JsonNum | None = None
    imagesUsable: bool
    imagesDecodable: JsonInt = 0
    timingsS: dict[str, float] = Field(default_factory=dict)

    def to_api_error(self) -> ApiError:
        return ApiError(
            errors=[ApiErrorItem(code=item.code, pointer=item.pointer, message=item.message) for item in self.errors]
        )


def _issue(code: str, level: IssueLevel, pointer: str, message: str, value: Any | None = None) -> ValidationIssue:
    if value is None:
        return ValidationIssue(code=code, level=level, pointer=pointer, message=message)
    return ValidationIssue(code=code, level=level, pointer=pointer, message=message, value=value)


def _box_inside(box: list[float], width: int, height: int) -> bool:
    u0, v0, u1, v1 = box
    return 0 <= u0 < u1 <= width and 0 <= v0 < v1 <= height


def check_packet(pk: Packet, root: Path | None = None) -> ValidationReport:
    """Semantic manifest checks. Tap-ray replay, depth sizes and stream headers are WS-02."""

    started = time.perf_counter()
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    epochs = {epoch.id for epoch in pk.epochs or []}
    keyframes = {frame.id: frame for frame in pk.keyframes}
    stills = {still.id: still for still in pk.stills or []}
    frames: dict[str, tuple[int, int]] = {key: (int(frame.w), int(frame.h)) for key, frame in keyframes.items()}
    frames.update({key: (int(still.w), int(still.h)) for key, still in stills.items()})
    listed = {entry.path for entry in pk.files}

    last_t: dict[str, float] = {}
    for index, frame in enumerate(pk.keyframes):
        if frame.epoch not in epochs:
            errors.append(
                _issue(
                    "unknown_epoch",
                    "error",
                    f"/keyframes/{index}/epoch",
                    f"keyframe {frame.id} names unknown epoch {frame.epoch}",
                    frame.epoch,
                )
            )
        previous = last_t.get(frame.epoch)
        if previous is not None and frame.timestamp <= previous:
            warnings.append(
                _issue(
                    "keyframe_timestamp_conflict",
                    "warning",
                    f"/keyframes/{index}/timestamp",
                    f"keyframe {frame.id} timestamp is not increasing within epoch {frame.epoch}",
                    frame.timestamp,
                )
            )
        last_t[frame.epoch] = float(frame.timestamp)

    for index, tap in enumerate(pk.taps or []):
        if tap.keyframe not in keyframes:
            errors.append(
                _issue(
                    "tap_unknown_keyframe",
                    "error",
                    f"/taps/{index}/keyframe",
                    f"tap {tap.id} names unknown keyframe {tap.keyframe}",
                    tap.keyframe,
                )
            )
    for index, still in enumerate(pk.stills or []):
        if still.keyframe and still.keyframe not in keyframes:
            errors.append(
                _issue(
                    "still_unknown_keyframe",
                    "error",
                    f"/stills/{index}/keyframe",
                    f"still {still.id} names unknown keyframe {still.keyframe}",
                    still.keyframe,
                )
            )

    _check_scale(pk, stills, frames, listed, errors, warnings)

    files_hashed = 0
    if root is not None:
        for index, entry in enumerate(pk.files):
            path = root / entry.path
            if not path.is_file():
                errors.append(
                    _issue("file_missing", "error", f"/files/{index}/path", f"{entry.path} missing", entry.path)
                )
                continue
            size = path.stat().st_size
            if size != int(entry.bytes):
                errors.append(
                    _issue(
                        "file_size_mismatch",
                        "error",
                        f"/files/{index}/bytes",
                        f"{entry.path} is {size} bytes, manifest says {int(entry.bytes)}",
                        size,
                    )
                )
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files_hashed += 1
            if digest != entry.sha256:
                errors.append(
                    _issue(
                        "sha256_mismatch",
                        "error",
                        f"/files/{index}/sha256",
                        f"{entry.path} sha256 mismatch",
                        digest,
                    )
                )

    elapsed = time.perf_counter() - started
    return ValidationReport(
        packetId=pk.packetId,
        formatVersion=pk.formatVersion,
        tier=pk.session.tier,
        stage="manifest",
        ok=not errors,
        errors=errors,
        warnings=warnings,
        keyframes=len(pk.keyframes),
        stills=len(pk.stills or []),
        taps=len(pk.taps or []),
        epochs=len(pk.epochs or []),
        files=len(pk.files),
        filesHashed=files_hashed,
        imagesUsable=True,
        imagesDecodable=0,
        timingsS={"manifest": elapsed},
    )


def _check_scale(
    pk: Packet,
    stills: dict[str, Any],
    frames: dict[str, tuple[int, int]],
    listed: set[str],
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> None:
    scale = pk.scaleReference
    if scale is None:
        return
    _close_up(
        "meterCloseUp",
        "/scaleReference/meterCloseUp",
        scale.meterCloseUp,
        "meter_close",
        lambda angle: angle <= _HEAD_ON_MAX_DEG,
        "scale_head_on_angle",
        stills,
        errors,
        warnings,
    )
    ext = scale.ext
    if ext is not None:
        _close_up(
            "obliqueCloseUp",
            "/scaleReference/ext/obliqueCloseUp",
            ext.obliqueCloseUp,
            "meter_oblique",
            lambda angle: _OBLIQUE_DEG[0] <= angle <= _OBLIQUE_DEG[1],
            "scale_oblique_angle",
            stills,
            errors,
            warnings,
        )
        for index, view in enumerate(ext.doorViews or []):
            size = frames.get(view.frame)
            pointer = f"/scaleReference/ext/doorViews/{index}"
            if size is None:
                errors.append(
                    _issue(
                        "door_view_unknown_frame",
                        "error",
                        pointer + "/frame",
                        f"door view on unknown frame {view.frame}",
                        view.frame,
                    )
                )
            elif view.box is not None and not _box_inside([float(v) for v in view.box], size[0], size[1]):
                errors.append(
                    _issue(
                        "door_box_outside",
                        "error",
                        pointer + "/box",
                        f"door box {view.box} not inside {view.frame}",
                        view.box,
                    )
                )
        if ext.doorOnMeterSide and not ext.doorViews:
            warnings.append(
                _issue(
                    "door_on_meter_side_no_view",
                    "warning",
                    "/scaleReference/ext/doorOnMeterSide",
                    "doorOnMeterSide is true but no door view was saved",
                    True,
                )
            )
        scan = ext.trueDepthScan
        if scan is not None and scan.captured and scan.depthDataAccuracy == "relative":
            warnings.append(
                _issue(
                    "truedepth_relative",
                    "warning",
                    "/scaleReference/ext/trueDepthScan/depthDataAccuracy",
                    "TrueDepth scan has relative depth accuracy and cannot give scale",
                    "relative",
                )
            )
        if scan is not None:
            for index, frame in enumerate(scan.frames or []):
                for name, rel in (("depth", frame.depth), ("image", frame.image)):
                    if rel and rel not in listed:
                        errors.append(
                            _issue(
                                "truedepth_file_not_listed",
                                "error",
                                f"/scaleReference/ext/trueDepthScan/frames/{index}/{name}",
                                f"TrueDepth file {rel} not listed in files",
                                rel,
                            )
                        )
    identity = scale.meterIdentity
    if identity and identity.source == "on_device_text" and not (identity.make or identity.model or identity.rawText):
        warnings.append(
            _issue(
                "meter_identity_empty",
                "warning",
                "/scaleReference/meterIdentity/source",
                "meterIdentity says on_device_text but carries no text",
                "on_device_text",
            )
        )


def _close_up(label, pointer, close, purpose, angle_ok, angle_code, stills, errors, warnings) -> None:
    if close is None or not close.captured:
        return
    still = stills.get(close.still)
    if still is None:
        errors.append(
            _issue(
                "scale_still_unknown",
                "error",
                pointer + "/still",
                f"{label}.still {close.still} is not a still",
                close.still,
            )
        )
        return
    if still.purpose != purpose:
        warnings.append(
            _issue(
                "scale_purpose_mismatch",
                "warning",
                pointer + "/still",
                f"{label}.still {close.still} has purpose {still.purpose}, expected {purpose}",
                still.purpose,
            )
        )
    if not still.keyframe:
        warnings.append(
            _issue(
                "scale_no_keyframe_link",
                "warning",
                pointer + "/still",
                f"{label} {close.still} has no keyframe link",
                close.still,
            )
        )
    distance = close.arkitDistanceM
    if distance is not None and not _DISTANCE_M[0] <= float(distance) <= _DISTANCE_M[1]:
        warnings.append(
            _issue(
                "scale_distance",
                "warning",
                pointer + "/arkitDistanceM",
                f"{label} taken from {float(distance):.2f} m; about 1 m is expected",
                distance,
            )
        )
    angle = close.viewAngleDeg
    if angle is not None and not angle_ok(float(angle)):
        warnings.append(
            _issue(
                angle_code,
                "warning",
                pointer + "/viewAngleDeg",
                f"{label} viewing angle {float(angle):.0f} deg is outside the intended range",
                angle,
            )
        )
