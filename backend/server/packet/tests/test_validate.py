"""Thresholds, corrupt cases, HEIC, amendments and the capture checklist."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image

from server.contracts.jobs import StageContext
from server.packet.capture_check import capture_check, partial_from_metas
from server.packet.conventions import column_major, orthonormalize, tap_replay_error
from server.packet.exif_intrinsics import K_from_focal35
from server.packet.normalize import merge_amendment, normalize_packet
from server.packet.params import PacketParams
from server.packet.poses import check_intrinsics, check_pose
from server.packet.report import Bag, PacketError
from server.packet.tests.corrupt import corrupt_case
from server.packet.tests.factory import identity_pose, write_arkit
from server.packet.validate import validate_and_normalize, validate_packet, validate_record

PARAMS = PacketParams()
EXAMPLE = Path(__file__).resolve().parents[3] / "data/fixtures/packets/examples/example-packet.json"


def _pose_case(ortho_scale: float) -> list[float]:
    matrix = np.eye(4)
    matrix[0, 0] = ortho_scale
    return column_major(matrix)


def test_rotation_just_inside_warns_and_just_outside_errors():
    bag = Bag()
    # 1e-4 is the warning edge. A 1e-4 scale on one diagonal exceeds the warn and stays under the error.
    check_pose(bag, 0, _pose_case(1 + 1e-4), PARAMS)
    assert [issue.code for issue in bag.errors] == []
    assert any(issue.code == "pose_rotation_drift" for issue in bag.warnings)
    bag = Bag()
    check_pose(bag, 0, _pose_case(1.1), PARAMS)
    assert any(issue.code == "pose_not_rigid" for issue in bag.errors)


@pytest.mark.parametrize(
    ("fx", "expect"),
    [
        (1920 / (2 * math.tan(math.radians(45) / 2)), "inside"),
        (1920 / (2 * math.tan(math.radians(100) / 2)), "inside"),
        (1920 / (2 * math.tan(math.radians(30) / 2)), "warn"),
        (1920 / (2 * math.tan(math.radians(120) / 2)), "warn"),
        (1920 / (2 * math.tan(math.radians(19) / 2)), "error"),
        (1920 / (2 * math.tan(math.radians(151) / 2)), "error"),
    ],
)
def test_fov_bands(fx, expect):
    bag = Bag()
    frame = {"intrinsics": [fx, fx, 960, 720], "w": 1920, "h": 1440}
    check_intrinsics(bag, 0, frame, PARAMS)
    codes = [issue.code for issue in bag.errors + bag.warnings]
    if expect == "error":
        assert "intrinsics_implausible" in codes
    elif expect == "warn":
        assert "fov_unusual" in codes
        assert "intrinsics_implausible" not in codes
    else:
        assert "fov_unusual" not in codes
        assert "intrinsics_implausible" not in codes


def test_jump_just_inside_and_outside(tmp_path: Path):
    manifest = write_arkit(tmp_path, n=2)
    pk = json.loads(manifest.read_text())
    # 0.2 s stays above the 0.1 s floor in binary floats. 0.59 m is under 3 m/s; 0.61 m is over.
    pk["keyframes"][1]["timestamp"] = 100.2
    pk["keyframes"][1]["pose"] = identity_pose(0.59, 0, 0)
    slow = tmp_path / "slow.json"
    slow.write_text(json.dumps(pk))
    report = validate_packet(slow, root=tmp_path, stage="manifest")
    assert "pose_jump" not in {issue.code for issue in report.warnings}
    pk["keyframes"][1]["pose"] = identity_pose(0.61, 0, 0)
    fast = tmp_path / "fast.json"
    fast.write_text(json.dumps(pk))
    report = validate_packet(fast, root=tmp_path, stage="manifest")
    assert "pose_jump" in {issue.code for issue in report.warnings}


def test_factory_packet_is_ok(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    report = validate_packet(manifest, stage="full")
    assert report.ok, [(issue.code, issue.message) for issue in report.errors]


def test_gravity_angles(tmp_path: Path):
    from server.packet.conventions import DEVICE_FROM_CAMERA
    from server.packet.poses import check_gravity_stream

    frames = [{"pose": identity_pose(), "timestamp": 1.0}]
    # Identity camera: g_pose = (I @ DEVICE_FROM_CAMERA.T).T @ (0,-1,0)
    g_pose = (np.eye(3) @ DEVICE_FROM_CAMERA.T).T @ np.array([0.0, -1.0, 0.0])
    tilted = _rotate_about_z(g_pose, 2.9)
    bag = Bag()
    check_gravity_stream({"keyframes": frames}, [(1.0, tilted)], bag, PARAMS)
    assert bag.warnings == [] and bag.errors == []
    bag = Bag()
    check_gravity_stream({"keyframes": frames}, [(1.0, _rotate_about_z(g_pose, 3.1))], bag, PARAMS)
    assert any(issue.code == "gravity_mismatch" for issue in bag.warnings)
    bag = Bag()
    # One frame cannot make 100 matches: the matcher keeps the nearest row only.
    many = []
    packet_frames = []
    for i in range(100):
        packet_frames.append({"pose": identity_pose(), "timestamp": float(i)})
        many.append((float(i), _rotate_about_z(g_pose, 15.1)))
    check_gravity_stream({"keyframes": packet_frames}, many, bag, PARAMS)
    assert any(issue.code == "gravity_conventions" for issue in bag.errors)


def _rotate_about_z(vector: np.ndarray, degrees: float) -> np.ndarray:
    # g_pose for an identity camera is the device x axis, so an x-rotation does not move it.
    a = math.radians(degrees)
    rot = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return rot @ vector


def test_imu_ms2_rescale(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    import gzip

    path = tmp_path / "streams/imuRaw.csv.gz"
    lines = ["t,ax,ay,az,gx,gy,gz"]
    for i in range(5):
        lines.append(f"{i * 0.01},0,{-9.80665},0,0,0,0")
    with gzip.open(path, "wt") as handle:
        handle.write("\n".join(lines) + "\n")
    data = path.read_bytes()
    import hashlib

    pk = json.loads(manifest.read_text())
    for entry in pk["files"]:
        if entry["path"].endswith("imuRaw.csv.gz"):
            entry["bytes"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="full")
    assert "imu_units_m_s2" in {issue.code for issue in report.warnings}
    packet, _report = validate_and_normalize(manifest)
    assert packet.derived.streams["imuRaw"].scaleToG == pytest.approx(1 / 9.80665)


@pytest.mark.parametrize(
    ("distance", "angle", "codes"),
    [
        (0.3, 30, []),
        (3.0, 30, []),
        (0.29, 30, ["scale_distance"]),
        (3.01, 30, ["scale_distance"]),
        (1.0, 30.1, ["scale_head_on_angle"]),
        (1.0, 15, []),
        (1.0, 50, []),
        (1.0, 14.9, ["scale_oblique_angle"]),
        (1.0, 50.1, ["scale_oblique_angle"]),
    ],
)
def test_close_up_bands(tmp_path: Path, distance, angle, codes):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    if "scale_oblique_angle" in codes or (
        angle in (15, 50, 14.9, 50.1) and "scale_head_on_angle" not in codes and distance == 1.0
    ):
        pk["stills"].append(
            {
                "id": "s2",
                "purpose": "meter_oblique",
                "img": "stills/s1.jpg",
                "w": 1920,
                "h": 1440,
                "timestamp": 102,
                "orientation": 1,
                "keyframe": "k00000",
            }
        )
        pk["scaleReference"]["ext"]["obliqueCloseUp"] = {
            "captured": True,
            "still": "s2",
            "arkitDistanceM": distance,
            "viewAngleDeg": angle,
        }
    else:
        pk["scaleReference"]["meterCloseUp"]["arkitDistanceM"] = distance
        pk["scaleReference"]["meterCloseUp"]["viewAngleDeg"] = angle
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="manifest")
    found = {issue.code for issue in report.warnings + report.errors}
    for code in codes:
        assert code in found


@pytest.mark.parametrize(
    ("f35", "level", "code"),
    [
        (22, "ok", None),
        (30, "ok", None),
        (21.9, "warning", "non_wide_lens"),
        (30.1, "warning", "non_wide_lens"),
        (10, "warning", "non_wide_lens"),
        (200, "warning", "non_wide_lens"),
        (9.9, "error", "exif_focal_out_of_range"),
        (200.1, "error", "exif_focal_out_of_range"),
    ],
)
def test_f35_bands(f35, level, code):
    from server.packet.exif_intrinsics import resolve_still_intrinsics

    still = {"id": "s", "w": 1920, "h": 1080, "exif": {"FocalLenIn35mmFilm": f35, "Make": "Apple"}}
    decision = resolve_still_intrinsics(still, {}, {"FocalLenIn35mmFilm": f35, "Make": "Apple"}, PARAMS)
    codes = [item[0] for item in decision["warnings"]]
    if level == "ok":
        assert "non_wide_lens" not in codes and "exif_focal_out_of_range" not in codes
    elif level == "error":
        assert code in codes
    else:
        assert code in codes
        assert "exif_focal_out_of_range" not in codes
    if f35 == 26:
        assert decision["intrinsics"][0] == pytest.approx(1323.782, abs=0.01)


def test_k_from_focal_26mm():
    fx, fy, cx, cy = K_from_focal35(26, 1920, 1080)
    assert fx == fy == 1323.782
    assert cx == 960 and cy == 540


def test_partial_meter_close_and_unknown_tap():
    pk = json.loads(EXAMPLE.read_text())
    metas = [{"keyframe": frame} for frame in pk["keyframes"]] + [{"still": still} for still in pk["stills"]]
    partial = partial_from_metas(pk["session"], metas)
    check = capture_check(partial, mode="partial")
    by_code = {item.code: item.status for item in check.items}
    assert by_code["meter_close"] == "present"
    assert by_code["meter_tap"] == "unknown"


def test_example_meter_frames_and_checklist(tmp_path: Path):
    report = validate_packet(EXAMPLE, stage="manifest")
    assert report.ok
    packet = normalize_packet(EXAMPLE, flow="guided", report=report)
    assert packet.derived.meterFrames.model_dump() == {"close": "s1", "oblique": "s2", "tapKeyframe": "k00002"}
    check = capture_check(packet, mode="final", report_ok=True)
    present = {item.code for item in check.items if item.status == "present"}
    assert {"meter_close", "meter_oblique", "meter_tap", "door_view_meter_side"} <= present


def test_merge_amendment_prefixes_ids(tmp_path: Path):
    parent = normalize_packet(write_arkit(tmp_path / "parent"), flow="guided")
    child = normalize_packet(
        write_arkit(tmp_path / "child"),
        flow="guided",
        decoded={("keyframe", 0): False},
    )
    merged = merge_amendment(parent, child, "a1")
    ids = [frame["id"] for frame in merged._packet["keyframes"]]
    assert any(i.startswith("a1/") for i in ids)
    assert merged.derived.primaryEpoch == parent.derived.primaryEpoch
    assert child.derived.stats.imagesDecodable == parent.derived.stats.imagesDecodable - 1
    assert merged.derived.stats.imagesDecodable == (
        parent.derived.stats.imagesDecodable + child.derived.stats.imagesDecodable
    )


def test_round_trip(tmp_path: Path):
    packet = normalize_packet(write_arkit(tmp_path / "src"), flow="guided")
    path = tmp_path / "normalized.json"
    packet.to_json(path)
    loaded = type(packet).from_json(path, tmp_path)
    again = tmp_path / "again.json"
    loaded.to_json(again)
    assert json.loads(path.read_text()) == json.loads(again.read_text())


def test_heic_orientation(tmp_path: Path):
    import pillow_heif

    pillow_heif.register_heif_opener()
    image = Image.new("RGB", (32, 16), (10, 20, 30))
    exif = Image.Exif()
    exif[274] = 6
    jpeg = tmp_path / "src.jpg"
    image.save(jpeg, exif=exif)
    heic = tmp_path / "stills/s1.heic"
    heic.parent.mkdir()
    pillow_heif.from_pillow(Image.open(jpeg)).save(heic, quality=50)
    root = tmp_path / "pkt"
    root.mkdir()
    target = root / "stills/s1.heic"
    target.parent.mkdir()
    target.write_bytes(heic.read_bytes())
    data = target.read_bytes()
    opened = Image.open(target)
    w, h = opened.size
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "pkt-heic-0001",
        "createdAt": "2026-09-26T18:00:00Z",
        "units": {"length": "meters", "angle": "degrees", "time": "s", "image": "px"},
        "conventions": {
            "world": "arkit",
            "camera": "arkit",
            "pose": "column-major",
            "intrinsics": "px",
            "pixel": "top-left",
        },
        "source": {"kind": "device"},
        "session": {
            "id": "s",
            "startedAt": "2026-09-26T18:00:00Z",
            "startedAtUptime": 0,
            "appVersion": "0",
            "deviceModel": "iPhone",
            "systemVersion": "18",
            "tier": "photo_only",
            "lidarAvailable": False,
            "sceneDepthEnabled": False,
        },
        "keyframes": [],
        "stills": [
            {
                "id": "s1",
                "purpose": "house_overview",
                "img": "stills/s1.heic",
                "w": 32,
                "h": 16,
                "timestamp": 1,
                "orientation": 6,
                "exif": {"Make": "Apple", "FocalLenIn35mmFilm": 26, "Orientation": 6},
            }
        ],
        "files": [
            {"path": "stills/s1.heic", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "role": "still"}
        ],
    }
    manifest = root / "packet.json"
    manifest.write_text(json.dumps(packet))
    report = validate_packet(manifest, stage="full")
    assert report.ok, [(i.code, i.message) for i in report.errors]
    normalized = normalize_packet(manifest, flow="photo", report=report)
    still = normalized._packet["stills"][0]
    assert still["img"] == "normalized/stills/s1.jpg"
    assert still["orientation"] == 1
    assert (still["w"], still["h"]) == (w, h)
    assert still["ext"]["hss"]["format"] == "heic->jpeg"
    assert still["ext"]["hss"]["originalImg"] == "stills/s1.heic"
    warning_codes = {issue.code for issue in report.warnings}
    assert "heic_transcoded" in warning_codes
    assert "heic_orientation_applied" in warning_codes
    assert "orientation_mismatch" not in warning_codes
    assert any(entry.get("role") == "compat" for entry in normalized._packet["files"])
    assert normalized.derived.notes
    out = tmp_path / "norm.json"
    normalized.to_json(out)
    body = json.loads(out.read_text())
    assert "hss" in body["ext"]


def _write_imu(root: Path, pk: dict, rows: list[str]) -> None:
    import gzip
    import hashlib

    path = root / "streams/imuRaw.csv.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("\n".join(rows) + "\n")
    blob = path.read_bytes()
    for entry in pk["files"]:
        if str(entry["path"]).endswith("imuRaw.csv.gz"):
            entry["bytes"] = len(blob)
            entry["sha256"] = hashlib.sha256(blob).hexdigest()


def _keyframe_flags(normalized, index: int = 0) -> list[str]:
    frame = normalized._packet["keyframes"][index]
    return list(((frame.get("ext") or {}).get("hss") or {}).get("flags") or [])


def test_motion_blur_iso_and_brightness(tmp_path: Path):
    """Brief §4 step 15: blur above 4 px, ISO above 200, BrightnessValue below 3. Flags, not warnings."""
    fx = 1440.0
    duration = 1.0 / 60.0
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["keyframes"][0]["exposure"] = {"duration": duration, "iso": 200}
    pk["keyframes"][0]["exif"] = {"BrightnessValue": 3}
    omega_inside = 3.99 / (duration * fx)
    _write_imu(tmp_path, pk, ["t,ax,ay,az,gx,gy,gz", f"100,0,-1,0,{omega_inside},0,0"])
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="full")
    assert report.ok, [(issue.code, issue.message) for issue in report.errors]
    codes = {issue.code for issue in report.warnings}
    assert "frames_blurry" not in codes
    assert "frames_dark" not in codes
    normalized = normalize_packet(manifest, flow="guided", report=report)
    flags = _keyframe_flags(normalized)
    assert "motion_blur" not in flags
    assert "high_iso" not in flags
    assert "low_light" not in flags
    assert normalized.derived.stats.blurredShare == 0

    omega_outside = 4.01 / (duration * fx)
    pk = json.loads(manifest.read_text())
    pk["keyframes"][0]["exposure"] = {"duration": duration, "iso": 201}
    pk["keyframes"][0]["exif"] = {"BrightnessValue": 2.99}
    _write_imu(tmp_path, pk, ["t,ax,ay,az,gx,gy,gz", f"100,0,-1,0,{omega_outside},0,0"])
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="full")
    codes = {issue.code for issue in report.warnings}
    assert "frames_blurry" not in codes
    assert "frames_dark" not in codes
    normalized = normalize_packet(manifest, flow="guided", report=report)
    flags = _keyframe_flags(normalized)
    assert "motion_blur" in flags
    assert "high_iso" in flags
    assert "low_light" in flags
    assert "slow_exposure" not in flags
    assert normalized.derived.stats.blurredShare == 0.5
    assert normalized.derived.stats.highIsoShare > 0
    check = capture_check(normalized, mode="final", report_ok=report.ok)
    tracking = next(item for item in check.items if item.code == "tracking_quality")
    assert tracking.status == "weak"
    assert tracking.evidence["blurredShare"] == 0.5


def test_cadence_and_light_thresholds_match_brief():
    assert PARAMS.median_spacing_m == 0.6
    assert PARAMS.blur_px_max == 4
    assert PARAMS.iso_high == 200
    assert PARAMS.brightness_dark == 3
    assert PARAMS.blurred_share_max == 0.30
    assert PARAMS.photo_count_min == 40


def test_corrupt_codes(tmp_path: Path):
    manifest = write_arkit(tmp_path / "base", n=2)
    cases = {
        "sha256_mismatch": "/files",
        "tap_unknown_keyframe": "/taps/0/keyframe",
        "unknown_epoch": "/keyframes/0/epoch",
        "pose_not_rigid": "/keyframes/0/pose",
        "stream_header": "/streams/imuRaw/columns",
        "file_not_listed": "/keyframes/0/img",
        "image_format": "/keyframes/0/img",
        "scale_still_unknown": "/scaleReference/meterCloseUp/still",
        "tier_rule": "/session/tier",
        "world_alignment_camera": "/session/worldAlignment",
    }
    # tap_unknown needs a tap.
    pk = json.loads(manifest.read_text())
    pk["taps"] = [
        {
            "id": "t1",
            "time": 100,
            "label": "meter",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
            "epoch": "e1",
        }
    ]
    manifest.write_text(json.dumps(pk))
    for case, prefix in cases.items():
        dest = corrupt_case(manifest, case, tmp_path / case)
        report = validate_packet(dest, stage="full")
        error_codes = [issue.code for issue in report.errors]
        assert error_codes == [case], (case, error_codes)
        assert report.errors[0].pointer.startswith(prefix), (case, report.errors[0].pointer)


def test_keyframe_timestamp_conflict(tmp_path: Path):
    manifest = write_arkit(tmp_path, n=2)
    dest = corrupt_case(manifest, "keyframe_timestamp_conflict", tmp_path / "bad")
    report = validate_packet(dest, stage="full")
    assert [issue.code for issue in report.errors] == ["keyframe_timestamp_conflict"]


def test_tap_epoch_mismatch_when_both_epochs_exist(tmp_path: Path):
    manifest = write_arkit(tmp_path, n=1)
    pk = json.loads(manifest.read_text())
    pk["epochs"].append({"id": "e2", "startTime": 200.0, "reason": "sessionStart"})
    pk["taps"] = [
        {
            "id": "t1",
            "time": 100,
            "label": "meter",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
            "epoch": "e2",
        }
    ]
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="manifest")
    assert [issue.code for issue in report.errors] == ["tap_epoch_mismatch"]


def test_check_packet_ok_matches_on_shared_checks():
    """SD-64: agree with check_packet only on the cases it handles.

    ok matches on clean packets. check_packet error codes are a subset of WS-02's.
    Tap replay, depth, streams, poses, image format and file listing stay on the
    ValidationReport; WS-00 does not implement those checks.
    """
    from server.contracts.packet import Packet
    from server.contracts.validation import check_packet

    manifests = list((EXAMPLE.parent).glob("example-packet*.json"))
    fixtures = Path(".fixtures/packets")
    if fixtures.is_dir():
        manifests.extend(path for path in fixtures.glob("*/packet.*.json") if path.is_file())
    assert manifests
    for manifest in manifests:
        model = Packet.model_validate(json.loads(manifest.read_text()))
        theirs = check_packet(model, None)
        ours = validate_packet(manifest, stage="manifest")
        assert theirs.ok == ours.ok, manifest.name
        assert {issue.code for issue in theirs.errors} <= {issue.code for issue in ours.errors}


def test_tap_replay_shift(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["taps"] = [
        {
            "id": "t1",
            "time": 100,
            "label": "meter",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
            "epoch": "e1",
        }
    ]
    manifest.write_text(json.dumps(pk))
    dest = corrupt_case(manifest, "tap_replay", tmp_path / "shift")
    report = validate_packet(dest, stage="full")
    assert [issue.code for issue in report.errors] == ["tap_replay"]
    assert report.errors[0].pointer == "/taps/0"
    assert "differs by" in report.errors[0].message


def test_normalize_records_tap_replay_error(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["taps"] = [
        {
            "id": "t1",
            "time": 100,
            "label": "meter",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "rayOrigin": [1, 0, 0],
            "rayDirection": [0, 0, -1],
            "epoch": "e1",
        }
    ]
    manifest.write_text(json.dumps(pk))
    normalized = normalize_packet(manifest, flow="guided")
    tap = normalized._packet["taps"][0]
    frame = next(item for item in normalized._packet["keyframes"] if item["id"] == "k00000")
    recorded = tap["ext"]["hss"]["replayError"]
    assert recorded == pytest.approx(tap_replay_error(frame, tap))
    assert recorded > 1e-3


def test_normalize_skips_unreplayable_taps(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["taps"] = [
        {
            "id": "missing-frame",
            "time": 100,
            "label": "meter",
            "keyframe": "no-such-frame",
            "pixel": [960, 720],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
            "epoch": "e1",
        },
        {
            "id": "missing-ray",
            "time": 100,
            "label": "meter",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "epoch": "e1",
        },
    ]
    manifest.write_text(json.dumps(pk))
    normalized = normalize_packet(manifest, flow="guided")
    for tap in normalized._packet["taps"]:
        assert "replayError" not in ((tap.get("ext") or {}).get("hss") or {})
    packet, report = validate_and_normalize(manifest)
    assert packet.derived.stats.taps == 2
    assert any(issue.code == "tap_unknown_keyframe" for issue in report.errors)


def test_validate_record_does_not_resolve_links():
    issues = validate_record("still", {"id": "s", "purpose": "meter_close", "keyframe": "missing", "w": 10, "h": 10})
    assert not any(issue.code == "still_unknown_keyframe" for issue in issues)


def test_messages_do_not_echo_raw_text(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["scaleReference"]["meterIdentity"] = {"source": "on_device_text", "rawText": "SECRET-NUMBER"}
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="manifest")
    blob = json.dumps(report.model_dump(mode="json"))
    assert "SECRET-NUMBER" not in blob


def test_svd_repairs_drift():
    matrix = np.eye(3)
    matrix[0, 0] = 1 + 5e-4
    repaired = orthonormalize(matrix)
    assert np.allclose(repaired.T @ repaired, np.eye(3), atol=1e-8)
    assert abs(np.linalg.det(repaired) - 1) < 1e-8


def test_format_version_and_camera_alignment(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["formatVersion"] = "0.3"
    pk["session"]["worldAlignment"] = "camera"
    manifest.write_text(json.dumps(pk))
    report = validate_packet(manifest, stage="manifest")
    codes = {issue.code for issue in report.errors}
    assert "format_version_unsupported" in codes
    assert "world_alignment_camera" in codes


def test_run_stage_raises_when_invalid(tmp_path: Path):
    from server.packet import run

    manifest = write_arkit(tmp_path / "cap")
    pk = json.loads(manifest.read_text())
    pk["session"]["worldAlignment"] = "camera"
    manifest.write_text(json.dumps(pk))
    stored = {}

    class Storage:
        def put_bytes(self, key, data, content_type):
            stored[key] = data

        def list_prefix(self, prefix):
            return []

        def get_bytes(self, key):
            return b""

    class Ctx:
        capture_id = "cap_test"
        run_id = "run_test"
        work_dir = tmp_path
        inputs = {"packet_dir": str(tmp_path / "cap")}
        storage = Storage()
        flow = "guided"

        def heartbeat(self, name):
            self.last = name

    ctx = Ctx()
    with pytest.raises(PacketError) as caught:
        run(cast(StageContext, ctx))
    assert caught.value.code == "invalid"
    assert any(key.endswith("validation.json") for key in stored)


def test_depth_size_corrupt(tmp_path: Path):
    manifest = write_arkit(tmp_path / "base", n=1)
    pk = json.loads(manifest.read_text())
    pk["session"]["tier"] = "arkit_lidar"
    pk["session"]["lidarAvailable"] = True
    pk["session"]["sceneDepthEnabled"] = True
    depth = tmp_path / "base" / "depth/d0.bin"
    depth.parent.mkdir()
    payload = np.full(8, 1.5, dtype="<f4").tobytes()
    depth.write_bytes(payload)
    conf = tmp_path / "base" / "depth/c0.bin"
    conf.write_bytes(b"\x02" * 8)

    pk["keyframes"][0]["depth"] = {"file": "depth/d0.bin", "confidenceFile": "depth/c0.bin", "w": 4, "h": 2}
    for rel, blob, role in (("depth/d0.bin", payload, "depth"), ("depth/c0.bin", conf.read_bytes(), "confidence")):
        pk["files"].append({"path": rel, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "role": role})
    manifest.write_text(json.dumps(pk))
    dest = corrupt_case(manifest, "depth_size", tmp_path / "bad")
    report = validate_packet(dest, stage="full")
    assert [issue.code for issue in report.errors] == ["depth_size"]
    assert report.errors[0].pointer.endswith("/depth/file")


def test_file_size_mismatch(tmp_path: Path):
    """The brief's case truncates a real features file. That must be the only error."""
    manifest = write_arkit(tmp_path / "base")
    pk = json.loads(manifest.read_text())
    rel = "features/f0.bin"
    blob = b"\x00" * 40
    path = manifest.parent / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    pk["keyframes"][0]["features"] = {"file": rel, "count": 2}
    pk["files"].append(
        {"path": rel, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "role": "features"}
    )
    manifest.write_text(json.dumps(pk))
    dest = corrupt_case(manifest, "file_size_mismatch", tmp_path / "bad")
    report = validate_packet(dest, stage="full")
    assert [issue.code for issue in report.errors] == ["file_size_mismatch"]


def test_copy_does_not_mutate_source_pose():
    values = _pose_case(1.1)
    before = copy.deepcopy(values)
    bag = Bag()
    check_pose(bag, 0, values, PARAMS)
    assert values == before
