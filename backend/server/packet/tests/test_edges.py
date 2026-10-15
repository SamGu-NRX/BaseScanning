"""Branches the threshold tests do not reach: stage entry, streams, poses, amendments."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from server.contracts.jobs import StageContext
from server.packet.capture_check import capture_check
from server.packet.conventions import (
    orthonormalize,
    pixel_ray_arkit,
    project_arkit,
    qvec_to_rotmat,
    rotmat_to_quat_xyzw,
    world_from_device,
)
from server.packet.normalize import merge_amendment, normalize_packet
from server.packet.params import PacketParams
from server.packet.poses import check_intrinsics, check_motion, check_pose, rescale_highres
from server.packet.report import Bag, PacketError
from server.packet.streams import check_streams, motion_gravity, nearest_gyro, read_stream
from server.packet.tests.factory import identity_pose, write_arkit
from server.packet.validate import validate_packet

PARAMS = PacketParams()


class _Ctx:
    def __init__(self, tmp_path: Path, storage, inputs=None):
        self.capture_id = "cap_test"
        self.run_id = "run_test"
        self.work_dir = tmp_path / "work"
        self.work_dir.mkdir(exist_ok=True)
        self.inputs = inputs or {}
        self.storage = storage
        self.flow = "guided"
        self.last = ""

    def heartbeat(self, name):
        self.last = name


class _Memory:
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.puts: dict[str, bytes] = {}

    def list_prefix(self, prefix):
        return [key for key in self.files if key.startswith(prefix)]

    def get_bytes(self, key):
        return self.files[key]

    def put_bytes(self, key, data, content_type):
        del content_type
        self.puts[key] = data


def test_run_happy_path_and_materialize(tmp_path: Path):
    from server.packet import run

    src = tmp_path / "src"
    write_arkit(src)
    files = {}
    for path in src.rglob("*"):
        if path.is_file():
            files[f"captures/cap_test/{path.relative_to(src).as_posix()}"] = path.read_bytes()
    ctx = _Ctx(tmp_path, _Memory(files))
    result = run(cast(StageContext, ctx))
    assert result.artifacts["packet/normalized.json"].endswith("normalized.json")
    assert result.metrics["normalize_s"] >= 0
    assert (ctx.work_dir / "normalized.json").is_file()


def test_run_missing_packet_and_local_put(tmp_path: Path):
    from server.packet import run

    class DiskOnly:
        def list_prefix(self, prefix):
            del prefix
            return []

        def get_bytes(self, key):
            raise FileNotFoundError(key)

    ctx = _Ctx(tmp_path, DiskOnly())
    with pytest.raises(PacketError) as caught:
        run(cast(StageContext, ctx))
    assert caught.value.code == "io"

    src = tmp_path / "cap"
    write_arkit(src)
    ctx = _Ctx(tmp_path, DiskOnly(), inputs={"packet_dir": str(src)})
    result = run(cast(StageContext, ctx))
    written = ctx.work_dir / "captures/cap_test/runs/run_test/packet/validation.json"
    assert written.is_file()
    assert "packet/capture_check.json" in result.artifacts


def test_photo_count_indexes_shot_list_purposes():
    packet = {
        "session": {"tier": "photo_only"},
        "keyframes": [],
        "stills": [
            {"id": "s1", "purpose": "meter_close"},
            {"id": "s2", "purpose": "wall"},
            {"id": "s3", "purpose": "ground"},
        ],
        "scaleReference": {"ext": {"doorOnMeterSide": False}},
    }
    check = capture_check(packet, mode="final", report_ok=True)
    photo = next(item for item in check.items if item.code == "photo_count")
    assert photo.status == "weak"
    assert photo.evidence["purposes"] == ["ground", "meter_close", "wall"]
    door = next(item for item in check.items if item.code == "door_view_meter_side")
    assert door.status == "not_applicable"
    cadence = next(item for item in check.items if item.code == "keyframe_cadence")
    assert cadence.status == "not_applicable"


def test_motion_stream_supplies_gyro_when_imu_is_absent(tmp_path: Path):
    path = tmp_path / "motion.csv.gz"
    rows = ["t,rotX,rotY,rotZ,gravX,gravY,gravZ", "100,0.2,0,0,0,-1,0"]
    with gzip.open(path, "wt") as handle:
        handle.write("\n".join(rows) + "\n")
    packet = {
        "streams": {"motion": {"file": "motion.csv.gz", "columns": rows[0].split(",")}},
        "files": [{"path": "motion.csv.gz"}],
    }
    rate = nearest_gyro(packet, tmp_path, 100.0, 0.05)
    assert rate == pytest.approx(0.2)
    grav = motion_gravity(packet, tmp_path)
    assert grav is not None and grav[0][0] == 100.0
    table = read_stream(packet, "motion", tmp_path, scale_to_g=2.0)
    assert table["gravY"][0] == pytest.approx(-2.0)
    assert nearest_gyro(packet, tmp_path, 50.0, 0.05) is None


def test_stream_header_empty_order_rate_and_units(tmp_path: Path):
    def write(name: str, lines: list[str]) -> None:
        with gzip.open(tmp_path / name, "wt") as handle:
            handle.write("\n".join(lines) + "\n")

    write("empty.csv.gz", ["t,ax,ay,az"])
    write("back.csv.gz", ["t,ax,ay,az", "2,0,-1,0", "1,0,-1,0"])
    write("slow.csv.gz", ["t,ax,ay,az", "0,0,-1,0", "10,0,-1,0"])
    write("imuRaw.csv.gz", ["t,ax,ay,az", "0,0,0,0", "1,0,0,0"])
    write("bad.csv.gz", ["not,a,header"])
    (tmp_path / "broken.csv.gz").write_bytes(b"not-gzip")
    packet = {
        "files": [
            {"path": name}
            for name in ("empty.csv.gz", "back.csv.gz", "slow.csv.gz", "imuRaw.csv.gz", "bad.csv.gz", "broken.csv.gz")
        ],
        "streams": {
            "empty": {"file": "empty.csv.gz", "columns": ["t", "ax", "ay", "az"], "rateHz": 10},
            "back": {"file": "back.csv.gz", "columns": ["t", "ax", "ay", "az"], "rateHz": 1},
            "slow": {"file": "slow.csv.gz", "columns": ["t", "ax", "ay", "az"], "rateHz": 100},
            "imuRaw": {"file": "imuRaw.csv.gz", "columns": ["t", "ax", "ay", "az"], "rateHz": 1},
            "bad": {"file": "bad.csv.gz", "columns": ["t", "ax", "ay", "az"], "rateHz": 1},
            "broken": {"file": "broken.csv.gz", "columns": ["t"], "rateHz": 1},
        },
        "keyframes": [],
    }
    bag = Bag()
    check_streams(packet, tmp_path, bag, PARAMS)
    codes = {issue.code for issue in bag.errors + bag.warnings}
    assert {
        "stream_empty",
        "stream_header",
        "stream_unreadable",
        "stream_time_order",
        "stream_rate_low",
        "imu_units_odd",
    } <= codes


def test_arkit_pose_gap_and_quat(tmp_path: Path):
    lines = ["t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw", "0,e1,normal,5,0,0,0,0,0,2"]
    path = tmp_path / "poses.csv"
    path.write_text("\n".join(lines) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    packet = {
        "files": [{"path": "poses.csv", "bytes": path.stat().st_size, "sha256": digest}],
        "streams": {
            "arkitPoses": {
                "file": "poses.csv",
                "columns": lines[0].split(","),
                "rateHz": 10,
            }
        },
        "keyframes": [{"epoch": "e1", "timestamp": 0.0, "pose": identity_pose()}],
    }
    bag = Bag()
    check_streams(packet, tmp_path, bag, PARAMS)
    codes = {issue.code for issue in bag.warnings}
    assert "arkit_pose_stream_gap" in codes
    assert "arkit_pose_stream_quat" in codes


def test_pose_and_intrinsics_edges():
    bag = Bag()
    check_pose(bag, 0, [1, 2, 3], PARAMS)
    assert any(issue.code == "pose_non_finite" for issue in bag.errors)
    bag = Bag()
    pose = identity_pose()
    pose[15] = 0
    check_pose(bag, 0, pose, PARAMS)
    assert any(issue.code == "pose_last_row" for issue in bag.errors)
    bag = Bag()
    check_pose(bag, 0, identity_pose(2000, 0, 0), PARAMS)
    assert any(issue.code == "pose_translation_absurd" for issue in bag.errors)
    bag = Bag()
    check_intrinsics(bag, 0, {"intrinsics": [1], "w": 10, "h": 10}, PARAMS)
    assert any(issue.code == "intrinsics_implausible" for issue in bag.errors)
    bag = Bag()
    check_intrinsics(bag, 0, {"intrinsics": [800, 700, 1200, 900], "w": 1920, "h": 1440}, PARAMS)
    codes = {issue.code for issue in bag.warnings}
    assert "focal_aspect" in codes
    assert "principal_point_offcentre" in codes
    bag = Bag()
    check_intrinsics(bag, 0, {"intrinsics": [346, 346, 200, 150], "w": 400, "h": 300}, PARAMS)
    assert any(issue.code == "low_resolution" for issue in bag.warnings)


def test_limited_tracking_pitch_and_rescale():
    frames = []
    for i in range(4):
        pose = identity_pose(i * 0.2, 0, 0)
        tracking = "limited" if i < 2 else "normal"
        frames.append(
            {
                "pose": pose,
                "timestamp": 100.0 + i,
                "tracking": tracking,
                "epoch": "e1",
                "intrinsics": [1000, 1000, 960, 720],
                "w": 1920,
                "h": 1440,
            }
        )
    # Tip the last camera up: rotate +y toward the world so pitch exceeds 30 degrees.
    tipped = np.eye(4)
    angle = np.radians(40)
    tipped[:3, :3] = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
    from server.packet.conventions import column_major

    frames[-1]["pose"] = column_major(tipped)
    bag = Bag()
    check_motion({"keyframes": frames, "epochs": [{"id": "e1", "startTime": 100}]}, bag, PARAMS)
    assert any(issue.code == "limited_tracking_share" for issue in bag.warnings)
    flags = ((frames[-1].get("ext") or {}).get("hss") or {}).get("flags") or []
    assert "pitched_up" in flags or "pitched_down" in flags
    packet = {
        "session": {"videoFormat": {"imageResolution": [1920, 1440]}},
        "keyframes": [
            {"intrinsics": [1440, 1440, 960, 720], "w": 1920, "h": 1440},
            {"highResolution": True, "intrinsics": [1440, 1440, 960, 720], "w": 4032, "h": 3024},
        ],
    }
    bag = Bag()
    rescale_highres(packet, bag, PARAMS)
    assert any(issue.code == "intrinsics_rescaled" for issue in bag.warnings)


def test_merge_amendment_prefixes_ids(tmp_path: Path):
    parent_manifest = write_arkit(tmp_path / "parent")
    child_manifest = write_arkit(tmp_path / "child")
    parent = normalize_packet(parent_manifest, flow="guided")
    child = normalize_packet(child_manifest, flow="guided")
    merged = merge_amendment(parent, child, "a1")
    ids = [frame["id"] for frame in merged._packet["keyframes"]]
    assert any(item.startswith("a1/") for item in ids)
    assert merged.derived.primaryEpoch == parent.derived.primaryEpoch


def test_quaternion_and_projection_helpers():
    matrix = qvec_to_rotmat(np.array([1.0, 0.0, 0.0, 0.0]))
    assert np.allclose(matrix, np.eye(3))
    quat = rotmat_to_quat_xyzw(matrix)
    assert abs(quat[3]) == pytest.approx(1.0)
    pose = np.eye(4)
    assert world_from_device(pose).shape == (3, 3)
    origin, direction = pixel_ray_arkit(pose, (100.0, 100.0, 50.0, 40.0), np.array([50.0, 40.0]))
    assert np.allclose(origin, 0)
    assert np.isfinite(direction).all()
    pixels, depth = project_arkit(pose, (100.0, 100.0, 50.0, 40.0), np.array([[0.0, 0.0, -2.0]]))
    assert pixels.shape == (1, 2)
    assert depth[0] == pytest.approx(2.0)
    reflected = np.diag([1.0, 1.0, -1.0])
    repaired = orthonormalize(reflected)
    assert np.linalg.det(repaired) > 0


def test_spin_warns(tmp_path: Path):
    manifest = write_arkit(tmp_path, n=2)
    packet = json.loads(manifest.read_text())
    spun = np.eye(4)
    spun[:3, :3] = qvec_to_rotmat(np.array([np.cos(np.radians(80)), 0.0, 0.0, np.sin(np.radians(80))]))
    from server.packet.conventions import column_major

    packet["keyframes"][1]["pose"] = column_major(spun)
    packet["keyframes"][1]["timestamp"] = 100.2
    bag = Bag()
    check_motion(packet, bag, PARAMS)
    assert any(issue.code == "pose_spin" for issue in bag.warnings)


def test_quaternion_sign_branches():
    for axis in ("x", "y", "z"):
        matrix = np.eye(3)
        if axis == "x":
            matrix = np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]])
        elif axis == "y":
            matrix = np.array([[0.0, 0, 1], [0, 1, 0], [-1, 0, 0]])
        else:
            matrix = np.diag([-1.0, -1.0, 1.0])
        quat = rotmat_to_quat_xyzw(matrix)
        assert quat.shape == (4,)


def test_exif_focal_decisions():
    from server.packet.exif_intrinsics import resolve_still_intrinsics

    still = {"w": 1920, "h": 1080, "intrinsicsSource": "arkit", "intrinsics": [1, 1, 1, 1]}
    assert resolve_still_intrinsics(still, {}, None, PARAMS)["keep"]
    linked = {"w": 100, "h": 80, "keyframe": "k", "exif": {"Make": "Android"}}
    decision = resolve_still_intrinsics(linked, {"k": {"intrinsics": [9, 9, 1, 1]}}, None, PARAMS)
    assert decision["intrinsicsSource"] == "arkit"
    assumed = resolve_still_intrinsics({"w": 1920, "h": 1080}, {}, None, PARAMS)
    assert assumed["intrinsicsAssumed"]
    mismatch = resolve_still_intrinsics(
        {"w": 1920, "h": 1080, "exif": {"FocalLenIn35mmFilm": 26, "Make": "Samsung"}},
        {},
        {"FocalLenIn35mmFilm": 50, "Make": "Samsung"},
        PARAMS,
    )
    assert any(code == "exif_mismatch" for code, _message in mismatch["warnings"])
    assert any(code == "non_apple_photo" for code, _message in mismatch["warnings"])
    wide = resolve_still_intrinsics({"w": 1920, "h": 1080, "exif": {"FocalLenIn35mmFilm": 9}}, {}, None, PARAMS)
    assert any(code == "exif_focal_out_of_range" for code, _message in wide["warnings"])
    kept = resolve_still_intrinsics(
        {
            "w": 1920,
            "h": 1080,
            "intrinsicsSource": "exif_focal35",
            "intrinsics": [1323.782, 1323.782, 960, 540],
            "exif": {"FocalLenIn35mmFilm": 26},
        },
        {},
        {"FocalLenIn35mmFilm": 26},
        PARAMS,
    )
    assert kept["keep"]
    replaced = resolve_still_intrinsics(
        {
            "w": 1920,
            "h": 1080,
            "intrinsicsSource": "exif_focal35",
            "intrinsics": [100, 100, 1, 1],
            "exif": {"FocalLenIn35mmFilm": 26},
        },
        {},
        None,
        PARAMS,
    )
    assert any(code == "intrinsics_recomputed" for code, _message in replaced["warnings"])


def test_scale_depth_taps_and_stage_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from server.packet.integrity import check_files, resolve_under
    from server.packet.poses import still_gravity
    from server.packet.validate import validate_and_normalize, validate_record

    assert resolve_under(tmp_path, "../x") is None
    assert resolve_under(tmp_path, "/abs") is None
    bag = Bag()
    check_files(
        {
            "files": [
                {"path": 1},
                {"path": "missing.jpg", "bytes": 1, "sha256": "abc"},
                {"path": "missing.jpg", "bytes": 1},
            ],
            "keyframes": [{"img": "missing.jpg"}],
            "mesh": {"file": "mesh.obj"},
        },
        tmp_path,
        bag,
        check_hashes=True,
        known_sha256=None,
    )
    codes = {issue.code for issue in bag.errors}
    assert (
        "path_unsafe" in codes and "file_missing" in codes and "file_duplicate" in codes and "file_not_listed" in codes
    )

    manifest = write_arkit(tmp_path / "pkt")
    packet = json.loads(manifest.read_text())
    packet["session"]["tier"] = "arkit_lidar"
    packet["session"]["lidarAvailable"] = True
    packet["session"]["sceneDepthEnabled"] = True
    packet["keyframes"][0]["highResolution"] = True
    packet["keyframes"][0]["depth"] = None
    packet["keyframes"][1]["depth"] = {"file": "depth/odd.bin", "confidenceFile": "depth/low.bin", "w": 2, "h": 2}
    packet["keyframes"][1]["features"] = {"file": "feat.bin", "count": 2}
    depth = tmp_path / "pkt" / "depth/odd.bin"
    depth.parent.mkdir()
    depth.write_bytes(np.array([np.nan, np.nan, 50.0, 50.0], dtype="<f4").tobytes())
    (tmp_path / "pkt" / "depth/low.bin").write_bytes(b"\x00\x00\x00\x00")
    (tmp_path / "pkt" / "feat.bin").write_bytes(b"\x00" * 10)
    for rel in ("depth/odd.bin", "depth/low.bin", "feat.bin"):
        blob = (tmp_path / "pkt" / rel).read_bytes()
        packet["files"].append(
            {"path": rel, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "role": "depth"}
        )
    packet["taps"] = [
        {
            "id": "t1",
            "keyframe": "k00000",
            "pixel": [9000, 1],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
            "hit": {"distance": 0.01},
        }
    ]
    packet["stills"][0]["keyframe"] = "missing"
    packet["stills"].append(
        {"id": "s2", "purpose": "eave", "img": "stills/s1.jpg", "w": 10, "h": 10, "keyframe": "k00000"}
    )
    packet["scaleReference"]["meterCloseUp"]["arkitDistanceM"] = 9
    packet["scaleReference"]["meterCloseUp"]["viewAngleDeg"] = 80
    packet["scaleReference"]["ext"]["doorViews"] = [
        {"frame": "nope", "box": [0, 0, 1, 1]},
        {"frame": "k00000", "box": [0, 0, 9000, 1]},
    ]
    packet["scaleReference"]["ext"]["doorOnMeterSide"] = True
    packet["scaleReference"]["ext"]["trueDepthScan"] = {
        "captured": True,
        "depthDataAccuracy": "relative",
        "frames": [{"w": 2, "h": 2, "depth": "missing-depth.bin", "image": "missing-image.bin"}],
    }
    packet["scaleReference"]["meterIdentity"] = {"source": "on_device_text"}
    manifest.write_text(json.dumps(packet))
    report = validate_packet(manifest, stage="full")
    found = {issue.code for issue in report.errors + report.warnings}
    assert "depth_missing_highres" in found
    assert "depth_values_odd" in found or "depth_confidence_all_low" in found
    assert "tap_pixel_outside" in found
    assert "still_unknown_keyframe" in found
    assert "unknown_purpose" in found
    assert "door_view_unknown_frame" in found
    assert "truedepth_relative" in found

    monkeypatch.setenv("SMP_ENVIRONMENT", "prod")
    packet["source"]["kind"] = "synthetic"
    manifest.write_text(json.dumps(packet))
    report = validate_packet(manifest, stage="manifest")
    assert any(issue.code == "synthetic_in_prod" for issue in report.errors)

    with pytest.raises(PacketError):
        validate_packet(tmp_path / "missing-packet.json")
    issues = validate_record("keyframe", {"pose": [1], "intrinsics": [1], "w": 10, "h": 10})
    assert issues
    normalized, full = validate_and_normalize(write_arkit(tmp_path / "round"))
    assert normalized.derived.flow == "guided"
    assert full.ok

    bag = Bag()
    still_gravity({"gravity": [0, 0, 0]}, PARAMS, bag, 0)
    assert any(issue.code == "gravity_magnitude" for issue in bag.warnings)
    still_gravity({"gravity": [0, -1, 0]}, PARAMS, Bag(), 0)


def test_tier_rules_quaternions_and_transcoded_stills(tmp_path: Path):
    from server.packet import run
    from server.packet.validate import _tier_rules, validate_manifest, validate_record

    bag = Bag()
    _tier_rules({"session": {"tier": "arkit", "sceneDepthEnabled": True, "worldAlignment": "gravityAndHeading"}}, bag)
    assert any(issue.code == "tier_rule" for issue in bag.errors)
    assert any(issue.code == "world_alignment_heading" for issue in bag.warnings)
    bag = Bag()
    _tier_rules({"session": {"tier": "photo_only"}, "keyframes": [{"id": "k"}]}, bag)
    assert any(issue.code == "tier_rule" for issue in bag.errors)
    bag = Bag()
    _tier_rules({"session": {"tier": "photo_only"}}, bag)
    assert any("still" in issue.message for issue in bag.errors)
    bag = Bag()
    _tier_rules(
        {
            "session": {
                "tier": "arkit_lidar",
                "lidarAvailable": False,
                "sceneDepthEnabled": False,
                "worldAlignment": "gravity",
                "videoFormat": {"imageResolution": [1920, 1440]},
            },
            "epochs": [{"id": "e"}],
            "tracking": [{"state": "normal"}],
            "keyframes": [{"depth": {"file": "d.bin"}}],
            "scaleReference": {"meterCloseUp": {}, "ext": {"method": "m", "obliqueCloseUp": {}, "doorViews": []}},
            "streams": {"imuRaw": {}, "arkitPoses": {}},
        },
        bag,
    )
    assert any(issue.code == "lidar_available_not_enabled" for issue in bag.warnings)
    assert any(issue.code == "depth_without_lidar_flag" for issue in bag.warnings)
    validate_manifest(
        {"formatVersion": "0.9", "session": {"tier": "photo_only"}, "stills": [{"id": "s", "w": 8, "h": 8}]}
    )
    issues = validate_record("still", {"purpose": "eave", "gravity": [0.0, -1.0, 0.0]})
    assert any(issue.code == "unknown_purpose" for issue in issues)
    rotmat_to_quat_xyzw(np.diag([1.0, -1.0, -1.0]))
    rotmat_to_quat_xyzw(np.diag([-1.0, 1.0, -1.0]))

    src = tmp_path / "src"
    write_arkit(src)
    stills = src / "normalized" / "stills"
    stills.mkdir(parents=True)
    (stills / "s1.jpg").write_bytes(b"\xff\xd8\xff")
    storage = _Memory()
    result = run(cast(StageContext, _Ctx(tmp_path, storage, inputs={"packet_dir": str(src)})))
    assert any(key.endswith("s1.jpg") for key in storage.puts)
    assert "packet/normalized.json" in result.artifacts

    files = {"captures/cap_test/runs/old.json": b"{}", "captures/cap_test/": b"", "other/nope": b"x"}
    for path in src.rglob("*"):
        if path.is_file() and "normalized" not in path.parts:
            files[f"captures/cap_test/{path.relative_to(src).as_posix()}"] = path.read_bytes()

    class Loose(_Memory):
        def list_prefix(self, prefix):
            del prefix
            return list(self.files)

    (tmp_path / "again").mkdir()
    run(cast(StageContext, _Ctx(tmp_path / "again", Loose(files))))
