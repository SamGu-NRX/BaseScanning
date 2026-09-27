"""Turn a v0.4 packet into a ValidationReport."""

from __future__ import annotations

import copy
import json
import math
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import numpy as np

from server.packet.conventions import tap_replay_error
from server.packet.exif_intrinsics import read_exif, resolve_still_intrinsics
from server.packet.images import check_headers, decode_all, images_usable, mark_undecodable, sniff_format, warn_heic
from server.packet.integrity import check_files, resolve_under
from server.packet.params import DEFAULT_PARAMS, PacketParams
from server.packet.poses import (
    add_flag,
    check_epochs,
    check_gravity_stream,
    check_intrinsics,
    check_motion,
    check_pose,
    check_resolution,
    rescale_highres,
    still_gravity,
)
from server.packet.purposes import KNOWN_PURPOSES, still_id_issue
from server.packet.report import Bag, PacketError, ValidationIssue, ValidationReport, log_issues, pointer
from server.packet.shape import schema_issues, scrub
from server.packet.streams import check_streams, gyro_at, load_gyro_series, motion_gravity


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _environment() -> str:
    """Prod only when the process sets SMP_ENVIRONMENT=prod.

    Settings defaults a missing value to prod so the privacy guard fails closed.
    Using that default here would reject every synthetic fixture. Deployed prod
    sets the variable; local and CI fixture runs leave it unset.
    """
    return "prod" if os.environ.get("SMP_ENVIRONMENT") == "prod" else "dev"


def _failed_report(stage: Literal["manifest", "full"], code: str, ptr: str, message: str) -> ValidationReport:
    """One issue and nothing else. Used when the document is not a packet we can walk."""
    return ValidationReport(
        packetId=None,
        formatVersion=None,
        tier=None,
        stage=stage,
        ok=False,
        errors=[ValidationIssue(code=code, level="error", pointer=ptr, message=message)],
        warnings=[],
        imagesUsable=False,
        timingsS={},
    )


def _record_schema(pk: dict, bag: Bag) -> dict:
    issues = schema_issues(pk)
    for code, ptr, message in issues:
        bag.error(code, ptr, message)
    if any(code == "schema" for code, _ptr, _message in issues):
        return scrub(pk)
    return pk


def _tier_rules(pk: dict, bag: Bag) -> None:
    session = _as_dict(pk.get("session"))
    tier = session.get("tier")
    ptr = pointer("session", "tier")
    if tier in {"arkit", "arkit_lidar"}:
        missing = []
        if not pk.get("epochs"):
            missing.append("epochs")
        if not pk.get("tracking"):
            missing.append("tracking")
        if not pk.get("keyframes"):
            missing.append("keyframes")
        if not session.get("worldAlignment"):
            missing.append("worldAlignment")
        if not session.get("videoFormat"):
            missing.append("videoFormat")
        sr = pk.get("scaleReference") or {}
        ext = sr.get("ext") or {}
        if "meterCloseUp" not in sr:
            missing.append("scaleReference.meterCloseUp")
        if not all(k in ext for k in ("method", "obliqueCloseUp", "doorViews")):
            missing.append("scaleReference.ext")
        streams = pk.get("streams") or {}
        if "imuRaw" not in streams or "arkitPoses" not in streams:
            missing.append("streams")
        if tier == "arkit_lidar" and session.get("lidarAvailable") is not True:
            missing.append("lidarAvailable")
        if missing and not any(issue.code == "tier_rule" for issue in bag.errors):
            bag.error("tier_rule", ptr, "tier " + str(tier) + " is missing " + ", ".join(missing))
    elif tier == "photo_only":
        if pk.get("keyframes"):
            if not any(issue.code == "tier_rule" for issue in bag.errors):
                bag.error("tier_rule", ptr, "photo_only packets cannot contain keyframes")
        elif not pk.get("stills") and not any(issue.code == "tier_rule" for issue in bag.errors):
            bag.error("tier_rule", ptr, "photo_only packets need at least one still")
    if session.get("worldAlignment") == "camera":
        bag.error(
            "world_alignment_camera", pointer("session", "worldAlignment"), "worldAlignment camera is not supported"
        )
    elif session.get("worldAlignment") == "gravityAndHeading":
        bag.warn("world_alignment_heading", pointer("session", "worldAlignment"), "gravityAndHeading is discouraged")
    if session.get("sceneDepthEnabled") and tier == "arkit":
        bag.warn(
            "depth_in_non_lidar_tier", pointer("session", "sceneDepthEnabled"), "scene depth is on for a non-LiDAR tier"
        )
    if tier == "arkit_lidar" and session.get("sceneDepthEnabled") is False:
        bag.warn(
            "lidar_available_not_enabled",
            pointer("session", "sceneDepthEnabled"),
            "LiDAR is available but scene depth is off",
        )
    frames = _as_list(pk.get("keyframes"))
    if session.get("lidarAvailable") is False and any(
        isinstance(frame, dict) and (frame.get("depth") or {}) for frame in frames
    ):
        bag.warn(
            "depth_without_lidar_flag",
            pointer("session", "lidarAvailable"),
            "depth files ignored because lidarAvailable is false",
        )


def _count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _records(value: object) -> list[tuple[int, dict]]:
    if not isinstance(value, list):
        return []
    return [(index, item) for index, item in enumerate(value) if isinstance(item, dict)]


def _area_bytes(depth: dict, scale: int) -> int | None:
    try:
        return int(depth.get("w", 0)) * int(depth.get("h", 0)) * scale
    except (TypeError, ValueError):
        return None


def _file_error_paths(pk: dict, bag: Bag, codes: set[str]) -> set[str]:
    """Paths whose `files[]` row already has one of `codes`. Derived size checks skip these."""
    indexes: set[int] = set()
    for issue in bag.errors:
        if issue.code not in codes:
            continue
        parts = issue.pointer.split("/")
        if len(parts) >= 3 and parts[1] == "files" and parts[2].isdigit():
            indexes.add(int(parts[2]))
    files = _as_list(pk.get("files"))
    paths: set[str] = set()
    for index in indexes:
        if index < len(files) and isinstance(files[index], dict):
            rel = files[index].get("path")
            if isinstance(rel, str):
                paths.add(rel)
    return paths


def _declared_byte_sizes(pk: dict, bag: Bag) -> None:
    """Manifest stage: depth, confidence and features sizes against `files[].bytes` (WS-01 finalize)."""
    files = _as_list(pk.get("files"))
    listed: dict[str, tuple[int, dict]] = {}
    for index, entry in _records(files):
        rel = entry.get("path")
        if isinstance(rel, str):
            listed.setdefault(rel, (index, entry))
    for _index, frame in _records(pk.get("keyframes")):
        depth = frame.get("depth")
        if isinstance(depth, dict):
            _expect_declared(listed, depth.get("file"), _area_bytes(depth, 4), "depth_size", bag)
            if depth.get("confidenceFile"):
                _expect_declared(listed, depth.get("confidenceFile"), _area_bytes(depth, 1), "confidence_size", bag)
        features = frame.get("features")
        if isinstance(features, dict) and features.get("file"):
            try:
                expect = 20 * int(features.get("count", 0))
            except (TypeError, ValueError):
                continue
            _expect_declared(listed, features.get("file"), expect, "features_size", bag)


def _expect_declared(listed: dict[str, tuple[int, dict]], rel: object, expect: int | None, code: str, bag: Bag) -> None:
    if not isinstance(rel, str) or expect is None:
        return
    found = listed.get(rel)
    if found is None:
        return
    index, entry = found
    try:
        raw_bytes = entry.get("bytes")
        if isinstance(raw_bytes, bool) or not isinstance(raw_bytes, (int, float, str)):
            return
        declared = int(raw_bytes)
    except (TypeError, ValueError):
        return
    if declared != expect:
        bag.error(code, pointer("files", index, "bytes"), f"{rel} lists {declared} bytes, expected {expect}")


def _depth_and_features(pk: dict, root: Path, bag: Bag, params: PacketParams, stage: str) -> None:
    session = _as_dict(pk.get("session"))
    lidar_off = session.get("lidarAvailable") is False
    enabled = bool(session.get("sceneDepthEnabled"))
    bad_files = _file_error_paths(pk, bag, {"file_size_mismatch", "sha256_mismatch"})
    for i, frame in _records(pk.get("keyframes")):
        depth = frame.get("depth")
        if not lidar_off and enabled and not depth:
            code = "depth_missing_highres" if frame.get("highResolution") else "depth_missing"
            level = "warning" if frame.get("highResolution") else "error"
            bag.add(level, code, pointer("keyframes", i, "depth"), "keyframe has no depth")  # type: ignore[arg-type]
        if stage == "full" and not lidar_off and isinstance(depth, dict):
            depth_file = depth.get("file")
            expect_depth = _area_bytes(depth, 4)
            if isinstance(depth_file, str) and depth_file not in bad_files and expect_depth is not None:
                _sized(
                    root,
                    depth_file,
                    expect_depth,
                    "depth_size",
                    pointer("keyframes", i, "depth", "file"),
                    bag,
                )
            confidence = depth.get("confidenceFile")
            expect_confidence = _area_bytes(depth, 1)
            if isinstance(confidence, str) and confidence not in bad_files and expect_confidence is not None:
                _sized(
                    root,
                    confidence,
                    expect_confidence,
                    "confidence_size",
                    pointer("keyframes", i, "depth", "confidenceFile"),
                    bag,
                )
        if stage != "full":
            continue
        features = frame.get("features") or {}
        feature_file = features.get("file") if isinstance(features, dict) else None
        if isinstance(feature_file, str) and feature_file not in bad_files:
            try:
                expect = 20 * int(features.get("count", 0))
            except (TypeError, ValueError):
                bag.error("schema", pointer("keyframes", i, "features", "count"), "features count is not an integer")
            else:
                _sized(
                    root,
                    feature_file,
                    expect,
                    "features_size",
                    pointer("keyframes", i, "features", "file"),
                    bag,
                )
    if stage != "full" or lidar_off:
        return
    _depth_values(pk, root, bag, params)


def _sized(root: Path, rel: str | None, expect: int, code: str, ptr: str, bag: Bag) -> None:
    if not rel:
        return
    full = resolve_under(root, rel)
    if full is None or not full.is_file():
        return
    size = full.stat().st_size
    if size != expect:
        bag.error(code, ptr, f"{rel} is {size} bytes, expected {expect}")


def _depth_values(pk: dict, root: Path, bag: Bag, params: PacketParams) -> None:
    frames = [
        frame
        for _index, frame in _records(pk.get("keyframes"))
        if isinstance(frame.get("depth"), dict) and frame["depth"].get("file")
    ]
    odd = False
    all_low = False
    checked = 0
    for frame in frames[:: max(params.depth_sample_stride, 1)]:
        depth = frame["depth"]
        dpath = resolve_under(root, depth["file"])
        if dpath is None or not dpath.is_file():
            continue
        values = np.fromfile(dpath, dtype="<f4")
        if not len(values):
            continue
        checked += 1
        finite = np.isfinite(values)
        if finite.mean() < params.depth_finite_share:
            odd = True
        elif len(values[finite]):
            median = float(np.median(values[finite]))
            if not (params.depth_median_m[0] <= median <= params.depth_median_m[1]):
                odd = True
        cfile = depth.get("confidenceFile")
        if cfile:
            cpath = resolve_under(root, cfile)
            if cpath is not None and cpath.is_file():
                conf = np.fromfile(cpath, dtype="u1")
                if len(conf) and int(conf.max()) < 1:
                    all_low = True
    if odd:
        bag.warn("depth_values_odd", pointer("keyframes"), "depth values are not in the LiDAR range")
    if all_low and checked:
        bag.warn("depth_confidence_all_low", pointer("keyframes"), "sampled confidence is all low")


def _taps(pk: dict, bag: Bag, params: PacketParams) -> float | None:
    frames = {frame.get("id"): frame for _index, frame in _records(pk.get("keyframes"))}
    worst = None
    for i, tap in _records(pk.get("taps")):
        frame = frames.get(tap.get("keyframe"))
        if frame is None:
            bag.error("tap_unknown_keyframe", pointer("taps", i, "keyframe"), f"unknown keyframe {tap.get('keyframe')}")
            continue
        try:
            w, h = int(frame.get("w", 0)), int(frame.get("h", 0))
        except (TypeError, ValueError):
            continue
        pixel = tap.get("pixel") or [0, 0]
        try:
            px, py = float(pixel[0]), float(pixel[1])
        except (TypeError, ValueError, IndexError):
            bag.error("schema", pointer("taps", i, "pixel"), "tap pixel is not two numbers")
            continue
        if not (0 <= px <= w and 0 <= py <= h):
            bag.error("tap_pixel_outside", pointer("taps", i, "pixel"), "tap pixel is outside the keyframe")
        try:
            err = tap_replay_error(frame, tap)
        except Exception:
            bag.error("tap_replay", pointer("taps", i), "tap ray could not be replayed")
            continue
        if not math.isfinite(err):
            bag.error("tap_replay", pointer("taps", i), "ray is not finite")
            continue
        ext = dict(tap.get("ext") or {})
        hss = dict(ext.get("hss") or {})
        hss["replayError"] = err
        hss["epoch"] = tap.get("epoch") or frame.get("epoch")
        ext["hss"] = hss
        tap["ext"] = ext
        worst = err if worst is None else max(worst, err)
        if err > params.tap_replay:
            bag.error("tap_replay", pointer("taps", i), f"ray differs by {err:.2e}")
        hit = _as_dict(tap.get("hit"))
        distance = hit.get("distance")
        try:
            distance_f = float(distance) if distance is not None else None
        except (TypeError, ValueError):
            distance_f = None
        if distance_f is not None and not (params.tap_hit_m[0] <= distance_f <= params.tap_hit_m[1]):
            bag.warn("tap_hit_distance", pointer("taps", i, "hit", "distance"), f"hit distance {distance}")
    return worst


def _stills(pk: dict, bag: Bag) -> None:
    frames = {frame.get("id"): frame for _index, frame in _records(pk.get("keyframes"))}
    for i, still in _records(pk.get("stills")):
        issue = still_id_issue(still.get("id"))
        if issue is not None:
            bag.error("path_unsafe", pointer("stills", i, "id"), issue)
        purpose = still.get("purpose")
        if isinstance(purpose, str) and purpose not in KNOWN_PURPOSES:
            bag.warn("unknown_purpose", pointer("stills", i, "purpose"), f"purpose {purpose} is treated as other")
        link = still.get("keyframe")
        if not link:
            continue
        frame = frames.get(link)
        if frame is None:
            bag.error("still_unknown_keyframe", pointer("stills", i, "keyframe"), f"unknown keyframe {link}")
            continue
        try:
            still_size = (int(still.get("w", 0)), int(still.get("h", 0)))
            frame_size = (int(frame.get("w", 0)), int(frame.get("h", 0)))
        except (TypeError, ValueError):
            bag.error("schema", pointer("stills", i, "w"), "still size is not an integer")
            continue
        if still_size != frame_size:
            bag.error("still_keyframe_size_mismatch", pointer("stills", i, "w"), "still and keyframe sizes differ")


def check_scale_reference(pk: dict, bag: Bag, root: Path | None, params: PacketParams) -> None:
    sr = pk.get("scaleReference")
    if not sr:
        return
    if not isinstance(sr, dict):
        bag.error("schema", pointer("scaleReference"), "scaleReference must be an object")
        return
    stills = {s.get("id"): s for s in pk.get("stills") or []}
    frames = {f.get("id"): f for f in pk.get("keyframes") or []}
    _close(
        bag,
        "meterCloseUp",
        sr.get("meterCloseUp"),
        "meter_close",
        lambda a: a <= params.head_on_max_deg,
        stills,
        params,
        "scale_head_on_angle",
    )
    ext = sr.get("ext") or {}
    if not isinstance(ext, dict):
        bag.error("schema", pointer("scaleReference", "ext"), "scaleReference.ext must be an object")
        ext = {}
    _close(
        bag,
        "obliqueCloseUp",
        ext.get("obliqueCloseUp"),
        "meter_oblique",
        lambda a: params.oblique_deg[0] <= a <= params.oblique_deg[1],
        stills,
        params,
        "scale_oblique_angle",
    )
    identity = sr.get("meterIdentity", {})
    if not isinstance(identity, dict):
        bag.error("schema", pointer("scaleReference", "meterIdentity"), "meterIdentity must be an object")
        identity = {}
    if identity.get("source") == "on_device_text" and not (
        identity.get("make") or identity.get("model") or identity.get("rawText")
    ):
        bag.warn("meter_identity_empty", pointer("scaleReference", "meterIdentity"), "meter identity has no text")
    known = {**frames, **stills}
    door_views = ext.get("doorViews") if isinstance(ext, dict) else None
    if not isinstance(door_views, list):
        if door_views is not None:
            bag.error("schema", pointer("scaleReference", "ext", "doorViews"), "doorViews must be an array")
        door_views = []
    for i, view in enumerate(door_views):
        if not isinstance(view, dict):
            bag.error("schema", pointer("scaleReference", "ext", "doorViews", i), "door view must be an object")
            continue
        frame = known.get(view.get("frame"))
        if frame is None:
            bag.error(
                "door_view_unknown_frame",
                pointer("scaleReference", "ext", "doorViews", i, "frame"),
                "door view frame is unknown",
            )
            continue
        if "box" not in view:
            continue
        box = view["box"]
        try:
            u0, v0, u1, v1 = (float(v) for v in box)
            w, h = int(frame["w"]), int(frame["h"])
        except (TypeError, ValueError):
            bag.error(
                "schema", pointer("scaleReference", "ext", "doorViews", i, "box"), "door box must be four numbers"
            )
            continue
        if not (0 <= u0 < u1 <= w and 0 <= v0 < v1 <= h):
            bag.error(
                "door_box_outside",
                pointer("scaleReference", "ext", "doorViews", i, "box"),
                "door box is outside the frame",
            )
    if ext.get("doorOnMeterSide") and not ext.get("doorViews"):
        bag.warn(
            "door_on_meter_side_no_view",
            pointer("scaleReference", "ext", "doorOnMeterSide"),
            "door on the meter side has no view",
        )
    scan = ext.get("trueDepthScan", {})
    if not isinstance(scan, dict):
        bag.error("schema", pointer("scaleReference", "ext", "trueDepthScan"), "trueDepthScan must be an object")
        scan = {}
    if scan.get("captured") and scan.get("depthDataAccuracy") == "relative":
        bag.warn(
            "truedepth_relative",
            pointer("scaleReference", "ext", "trueDepthScan", "depthDataAccuracy"),
            "TrueDepth accuracy is relative",
        )
    listed = {entry.get("path") for entry in pk.get("files") or [] if isinstance(entry, dict)}
    for i, fr in enumerate(scan.get("frames") or []):
        if not isinstance(fr, dict):
            bag.error(
                "schema",
                pointer("scaleReference", "ext", "trueDepthScan", "frames", i),
                "TrueDepth frame must be an object",
            )
            continue
        for field in ("depth", "image"):
            rel = fr.get(field)
            if rel and rel not in listed:
                bag.error(
                    "truedepth_file_not_listed",
                    pointer("scaleReference", "ext", "trueDepthScan", "frames", i, field),
                    "TrueDepth file is not listed",
                )
        if root is not None and fr.get("depth"):
            full = resolve_under(root, fr["depth"])
            try:
                expect = int(fr["w"]) * int(fr["h"]) * 4
            except (KeyError, TypeError, ValueError):
                continue
            if full is not None and full.is_file() and full.stat().st_size != expect:
                bag.error(
                    "truedepth_depth_size",
                    pointer("scaleReference", "ext", "trueDepthScan", "frames", i, "depth"),
                    "TrueDepth depth is not w*h*4 bytes",
                )


def _close(bag, label, block, purpose, angle_ok, stills, params: PacketParams, angle_code: str) -> None:
    if not isinstance(block, dict) or not block.get("captured"):
        return
    still = stills.get(block.get("still"))
    ptr = pointer(
        "scaleReference",
        label if label == "meterCloseUp" else "ext",
        *(() if label == "meterCloseUp" else ("obliqueCloseUp",)),
        "still",
    )
    if label != "meterCloseUp":
        ptr = pointer("scaleReference", "ext", "obliqueCloseUp", "still")
    else:
        ptr = pointer("scaleReference", "meterCloseUp", "still")
    if still is None:
        bag.error("scale_still_unknown", ptr, f"{label} still is not a still")
        return
    if still.get("purpose") != purpose:
        bag.warn("scale_purpose_mismatch", ptr, f"{label} purpose is {still.get('purpose')}")
    if not still.get("keyframe"):
        bag.warn("scale_no_keyframe_link", ptr, f"{label} has no keyframe link")
    distance = block.get("arkitDistanceM")
    try:
        distance_f = float(distance) if distance is not None else None
    except (TypeError, ValueError):
        distance_f = None
    if distance_f is not None and not (params.close_up_m[0] <= distance_f <= params.close_up_m[1]):
        if label == "meterCloseUp":
            distance_ptr = pointer("scaleReference", "meterCloseUp", "arkitDistanceM")
        else:
            distance_ptr = pointer("scaleReference", "ext", "obliqueCloseUp", "arkitDistanceM")
        bag.warn("scale_distance", distance_ptr, f"{label} distance {distance}")
    angle = block.get("viewAngleDeg")
    try:
        angle_f = float(angle) if angle is not None else None
    except (TypeError, ValueError):
        angle_f = None
    if angle_f is not None and not angle_ok(angle_f):
        bag.warn(angle_code, ptr, f"{label} angle {angle} deg")


def _exposure_seconds(frame: dict) -> float | None:
    exposure = _as_dict(frame.get("exposure"))
    exif = _as_dict(frame.get("exif"))
    duration = exposure.get("duration", exif.get("ExposureTime"))
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def _iso_value(frame: dict) -> float | None:
    exposure = frame.get("exposure") or {}
    exif = frame.get("exif") or {}
    iso = exposure.get("iso")
    if iso is None:
        ratings = exif.get("ISOSpeedRatings")
        if ratings is None:
            ratings = exif.get("PhotographicSensitivity")
        if isinstance(ratings, list):
            iso = ratings[0] if ratings else None
        else:
            iso = ratings
    if iso is None:
        return None
    try:
        return float(iso)
    except (TypeError, ValueError):
        return None


def apply_quality_flags(pk: dict, root: Path | None, params: PacketParams) -> None:
    """Brief §4 step 15. These are frame flags. WS-03 turns the shares into hints."""
    series = load_gyro_series(pk, root) if root is not None else None
    for frame in pk.get("keyframes") or []:
        _flag_exposure_and_light(frame, params)
        _flag_motion_blur(frame, series, params)
    for still in pk.get("stills") or []:
        _flag_exposure_and_light(still, params)


def _flag_exposure_and_light(frame: dict, params: PacketParams) -> None:
    duration = _exposure_seconds(frame)
    if duration is not None and duration > params.slow_exposure_s:
        add_flag(frame, "slow_exposure")
    iso = _iso_value(frame)
    if iso is not None and iso > params.iso_high:
        add_flag(frame, "high_iso")
    brightness = (frame.get("exif") or {}).get("BrightnessValue")
    if brightness is None:
        return
    try:
        if float(brightness) < params.brightness_dark:
            add_flag(frame, "low_light")
    except (TypeError, ValueError):
        return


def _flag_motion_blur(frame: dict, series: tuple[np.ndarray, np.ndarray] | None, params: PacketParams) -> None:
    exposure = frame.get("exposure") or {}
    duration = exposure.get("duration")
    if duration is None:
        return
    try:
        duration_s = float(duration)
        fx = float(frame["intrinsics"][0])
        timestamp = float(frame.get("timestamp", 0.0))
    except (KeyError, TypeError, ValueError, IndexError):
        return
    gyro = gyro_at(series, timestamp, params.gravity_match_s)
    if gyro is None or fx <= 0:
        return
    if duration_s * gyro * fx > params.blur_px_max:
        add_flag(frame, "motion_blur")


def _quality(pk: dict, root: Path | None, bag: Bag, params: PacketParams, _stage: str) -> None:
    apply_quality_flags(pk, root, params)
    n_images = len(pk.get("keyframes") or []) + len(pk.get("stills") or [])
    if n_images < params.few_images:
        bag.warn("few_images", pointer("keyframes"), f"packet has {n_images} images")


def _orientation(pk: dict, root: Path, bag: Bag) -> None:
    for i, still in enumerate(pk.get("stills") or []):
        rel = still.get("img")
        if not isinstance(rel, str):
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file():
            continue
        with full.open("rb") as handle:
            header = handle.read(32)
        if sniff_format(header) != "jpeg":
            continue
        try:
            exif, _gps = read_exif(full)
            tag = exif.get("Orientation")
            if tag is None:
                continue
            if int(tag) != int(still.get("orientation", tag)):
                bag.warn(
                    "orientation_mismatch",
                    pointer("stills", i, "orientation"),
                    "EXIF orientation differs from the packet; packet wins",
                )
        except Exception:
            continue


def _exif_warnings(pk: dict, root: Path | None, bag: Bag, params: PacketParams) -> None:
    if (pk.get("session") or {}).get("tier") != "photo_only":
        return
    keyframes = {frame.get("id"): frame for frame in pk.get("keyframes") or []}
    for i, still in enumerate(pk.get("stills") or []):
        file_exif = None
        if root is not None and isinstance(still.get("img"), str):
            full = resolve_under(root, still["img"])
            if full is not None and full.is_file():
                try:
                    file_exif, _gps = read_exif(full)
                except Exception:
                    file_exif = None
        decision = resolve_still_intrinsics(still, keyframes, file_exif, params)
        for code, message in decision["warnings"]:
            if code == "exif_focal_out_of_range":
                bag.error(code, pointer("stills", i, "exif", "FocalLenIn35mmFilm"), message)
            elif code in {"exif_missing", "intrinsics_assumed"}:
                bag.warn(code, pointer("stills", i), message)
            else:
                bag.warn(code, pointer("stills", i, "intrinsics"), message)


def _load_document(manifest: Path | dict, root: Path | None) -> tuple[object, Path]:
    """Read a packet document. Raises PacketError only for missing or undecodable files."""
    if isinstance(manifest, dict):
        if root is None:
            raise PacketError("root_missing", message="dict manifest needs root")
        try:
            return copy.deepcopy(manifest), root
        except RecursionError:
            return None, root
    if not manifest.is_file():
        raise PacketError("io", message=f"cannot read {manifest}")
    root = manifest.parent if root is None else root
    try:
        raw = manifest.read_bytes()
    except OSError as exc:
        raise PacketError("io", message=f"cannot read {manifest}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketError("io", message="packet.json is not UTF-8") from exc
    try:
        return json.loads(text), root
    except RecursionError:
        return None, root
    except json.JSONDecodeError as exc:
        raise PacketError("io", message="packet.json is not JSON") from exc


def validate_packet(
    manifest: Path | dict,
    *,
    root: Path | None = None,
    stage: Literal["manifest", "full"] = "full",
    check_hashes: bool = True,
    known_sha256: Mapping[str, str] | None = None,
    params: PacketParams = DEFAULT_PARAMS,
) -> ValidationReport:
    started = time.perf_counter()
    pk, root = _load_document(manifest, root)
    if root is None or not root.exists():
        raise PacketError("root_missing", message="packet root does not exist")
    if pk is None:
        return _failed_report(stage, "schema", "/", "packet is nested too deeply")
    if not isinstance(pk, dict):
        return _failed_report(stage, "schema", "/", "packet must be an object")
    try:
        return _validate_dict(
            pk,
            root,
            stage=stage,
            check_hashes=check_hashes,
            known_sha256=known_sha256,
            params=params,
            started=started,
        )
    except RecursionError:
        return _failed_report(stage, "schema", "/", "packet is nested too deeply")
    except Exception:
        return _failed_report(stage, "schema", "/", "packet could not be checked")


def _validate_dict(
    pk: dict,
    root: Path,
    *,
    stage: Literal["manifest", "full"],
    check_hashes: bool,
    known_sha256: Mapping[str, str] | None,
    params: PacketParams,
    started: float,
) -> ValidationReport:
    bag = Bag()
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    if pk.get("formatVersion") != params.format_version:
        bag.error(
            "format_version_unsupported",
            pointer("formatVersion"),
            f"formatVersion {pk.get('formatVersion')} is not {params.format_version}",
        )
    pk = _record_schema(pk, bag)
    _tier_rules(pk, bag)
    source = pk.get("source")
    kind = source.get("kind") if isinstance(source, dict) else None
    if kind == "synthetic":
        if _environment() == "prod":
            bag.error("synthetic_in_prod", pointer("source", "kind"), "synthetic packets are not evidence")
        else:
            bag.warn("synthetic_source", pointer("source", "kind"), "synthetic packet")
    timings["schema"] = time.perf_counter() - t0
    check_resolution(pk, bag)
    rescale_highres(pk, bag, params)
    for i, frame in enumerate(pk.get("keyframes") or []):
        check_pose(bag, i, frame.get("pose"), params)
        check_intrinsics(bag, i, frame, params)
    check_epochs(pk, bag, params)
    check_motion(pk, bag, params)
    _taps_error = _taps(pk, bag, params)
    _stills(pk, bag)
    if stage == "manifest":
        _declared_byte_sizes(pk, bag)
    check_scale_reference(pk, bag, root if stage == "full" else None, params)
    hashed = 0
    decoded: dict = {}
    if stage == "full":
        t1 = time.perf_counter()
        hashed = check_files(pk, root, bag, check_hashes=check_hashes, known_sha256=known_sha256)
        listed = {}
        for entry in pk.get("files") or []:
            if entry.get("path") not in listed:
                listed[entry.get("path")] = entry
        check_headers(pk, root, bag, listed)
        _depth_and_features(pk, root, bag, params, stage)
        check_streams(pk, root, bag, params)
        check_gravity_stream(pk, motion_gravity(pk, root), bag, params)
        _orientation(pk, root, bag)
        warn_heic(pk, root, bag)
        decoded = decode_all(pk, root, listed)
        mark_undecodable(pk, root, bag, decoded)
        timings["files"] = time.perf_counter() - t1
    _exif_warnings(pk, root if stage == "full" else None, bag, params)
    _quality(pk, root if stage == "full" else None, bag, params, stage)
    for i, still in enumerate(pk.get("stills") or []):
        if (pk.get("session") or {}).get("tier") == "photo_only":
            still_gravity(still, params, bag, i)
    usable, n_decodable = images_usable(decoded, params) if stage == "full" else (True, 0)
    timings["total"] = time.perf_counter() - started
    session = _as_dict(pk.get("session"))
    tier = session.get("tier")
    if tier not in {"arkit_lidar", "arkit", "photo_only"}:
        tier = None
    packet_id = pk.get("packetId")
    replay = _taps_error if isinstance(_taps_error, (int, float)) and math.isfinite(_taps_error) else None
    report = ValidationReport(
        packetId=packet_id if isinstance(packet_id, str) else None,
        formatVersion=pk.get("formatVersion") if isinstance(pk.get("formatVersion"), str) else None,
        tier=tier,
        stage=stage,
        ok=bag.ok,
        errors=bag.errors,
        warnings=bag.warnings,
        keyframes=_count(pk.get("keyframes")),
        stills=_count(pk.get("stills")),
        taps=_count(pk.get("taps")),
        epochs=_count(pk.get("epochs")),
        files=_count(pk.get("files")),
        filesHashed=hashed,
        maxTapReplayError=replay,
        imagesUsable=usable,
        imagesDecodable=n_decodable,
        timingsS=timings,
    )
    log_issues(report, packet_id if isinstance(packet_id, str) else None)
    return report


def validate_manifest(pk: dict) -> ValidationReport:
    """Stage `manifest`. Files are not required to be on disk. Never raises."""
    try:
        if not isinstance(pk, dict):
            return _failed_report("manifest", "schema", "/", "packet must be an object")
        root = Path(".")
        # A dict has no parent. Use a directory that exists so root_missing does not fire,
        # and skip file checks because stage is manifest.
        return validate_packet(pk, root=root if root.exists() else Path("/"), stage="manifest", check_hashes=False)
    except Exception:
        return _failed_report("manifest", "schema", "/", "packet could not be checked")


def validate_record(
    kind: Literal["keyframe", "still"], record: dict, session: dict | None = None
) -> list[ValidationIssue]:
    """Steps 3–4, or the still checks of step 12, for one meta record. No link resolution."""
    bag = Bag()
    params = PacketParams()
    if kind == "keyframe":
        check_pose(bag, 0, record.get("pose"), params)
        check_intrinsics(bag, 0, record, params)
        _quality({"keyframes": [record], "stills": [], "session": session or {}}, None, bag, params, "manifest")
    else:
        purpose = record.get("purpose")
        if isinstance(purpose, str) and purpose not in KNOWN_PURPOSES:
            bag.warn("unknown_purpose", pointer("stills", 0, "purpose"), f"purpose {purpose} is treated as other")
        if record.get("gravity") is not None:
            still_gravity(record, params, bag, 0)
    return bag.errors + bag.warnings


def validate_and_normalize(manifest: Path, root: Path | None = None, *, flow=None):
    report = validate_packet(manifest, root=root, stage="full")
    from server.packet.normalize import normalize_packet

    packet = normalize_packet(manifest, flow=flow, report=report)
    return packet, report
