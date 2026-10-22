"""Synthetic app-capture generator: determinism, ground truth, and real-pipeline validation.

The generator renders app-shaped packets whose true geometry is exact. These
tests pin the properties that make the packets usable as fixtures:

- the real ``server.packet`` validator accepts the rendered packets;
- the packed tap replays through the packet's own conventions onto the meter;
- the depth image at the tap pixel agrees with the packed hit distance;
- ground truth matches the scene spec, including hazard clearances;
- re-rendering the same spec and seed reproduces the bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.gen_app_capture import DEPTH_H, DEPTH_W, Scene, generate_packet, load_spec
from server.common.geometry import pixel_ray_arkit
from server.packet.conventions import from_column_major

SPEC_DIR = Path(__file__).parent / "fixtures" / "app_capture_synth"
SCENES = ("clear-wall", "gas-left")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for scene in SCENES:
        spec = load_spec(SPEC_DIR / f"{scene}.json")
        target = tmp_path_factory.mktemp(scene)
        generate_packet(spec, target)
        out[scene] = target
    return out


def _packet(rendered, scene: str) -> dict:
    return json.loads((rendered[scene] / "packet.json").read_text())


def _ground_truth(rendered, scene: str) -> dict:
    return json.loads((rendered[scene] / "ground_truth.json").read_text())


def test_packet_layout_matches_fixture_contract(rendered):
    """The generator emits the mini-arkit-lidar layout the app and server share."""
    root = rendered["clear-wall"]
    packet = _packet(rendered, "clear-wall")
    assert packet["format"] == "house-capture-packet"
    assert packet["formatVersion"] == "0.4"
    assert packet["source"]["kind"] == "synthetic"
    assert len(packet["keyframes"]) == 32
    assert len(packet["stills"]) == 2
    assert packet["streams"]["motion"]["file"] == "streams/motion.csv.gz"
    assert packet["streams"]["arkitPoses"]["file"] == "streams/arkitPoses.csv.gz"
    frame = packet["keyframes"][0]
    assert (root / frame["img"]).is_file()
    assert (root / frame["depth"]["file"]).stat().st_size == 256 * 192 * 4
    assert (root / frame["depth"]["confidenceFile"]).stat().st_size == 256 * 192
    # iOS fixture manifests reference media relative to the packet directory the same way.
    assert all(
        entry["path"] in {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()} for entry in packet["files"]
    )


def test_validator_reports_ok(rendered):
    """The real server.packet validator and normalizer pass on the generated packets."""
    from server.packet.validate import validate_and_normalize

    for scene in SCENES:
        packet, report = validate_and_normalize(rendered[scene] / "packet.json")
        assert report.ok, f"{scene}: {report.errors}"
        warning_codes = {issue.code for issue in report.warnings}
        assert "synthetic_source" in warning_codes
        assert warning_codes <= {"synthetic_source", "pose_jump", "arkit_pose_stream_gap"}
        assert report.maxTapReplayError < 1e-6


def test_repeated_generation_is_byte_identical(tmp_path, rendered):
    """Same spec and seed in, same bytes out — gz timestamps, ids, JPEGs included."""
    spec = load_spec(SPEC_DIR / "clear-wall.json")
    second = tmp_path / "again"
    generate_packet(spec, second)
    first = rendered["clear-wall"]
    for path in sorted(first.rglob("*")):
        if not path.is_file():
            continue
        other = second / path.relative_to(first)
        assert other.is_file(), f"missing {path.relative_to(first)}"
        assert path.read_bytes() == other.read_bytes(), f"bytes differ: {path.relative_to(first)}"


def test_ground_truth_matches_scene_spec(rendered):
    gt = _ground_truth(rendered, "gas-left")
    assert gt["scene"] == "gas-left"
    # Wall plane at z = -2.0, camera on the +z side.
    assert gt["wall"]["plane"]["point"][2] == -2.0
    assert gt["wall"]["plane"]["normal"] == [0.0, 0.0, 1.0]
    # Meter centre: spec x = 2.0, centreHeight = 1.2, camera height = 1.45.
    meter = gt["meter"]
    assert meter["centre"][0] == 2.0
    assert meter["centre"][1] == round(-1.45 + 1.2, 6)
    assert meter["frontFaceZ"] == round(-2.0 + 0.18, 6)
    # The gas meter sits 0.6 m to the left; edges nearly touch at 0.2 m.
    assert gt["hazards"][0]["kind"] == "gas_meter"
    assert gt["hazards"][0]["centre"][0] == 1.4
    assert gt["clearances"]["meterCentreToGasCentreM"] == pytest.approx(0.6)
    assert gt["clearances"]["meterEdgeToGasEdgeM"] == pytest.approx(0.2)


def test_clear_wall_has_no_hazards(rendered):
    gt = _ground_truth(rendered, "clear-wall")
    assert gt["hazards"] == []
    assert "meterCentreToGasCentreM" not in gt["clearances"]
    # The two scenes differ in hazards, so they must differ in clearances.
    assert _ground_truth(rendered, "gas-left")["clearances"] != gt["clearances"]


def test_tap_replays_onto_meter_front_face(rendered):
    packet = _packet(rendered, "clear-wall")
    gt = _ground_truth(rendered, "clear-wall")
    tap = packet["taps"][0]
    frame = next(f for f in packet["keyframes"] if f["id"] == tap["keyframe"])
    pose = from_column_major(frame["pose"])
    origin, direction = pixel_ray_arkit(pose, frame["intrinsics"], np.asarray(tap["pixel"]))
    replay = np.asarray(origin) + float(
        np.dot(direction, np.asarray(tap["hit"]["position"]) - np.asarray(origin))
    ) * np.asarray(direction)
    # The replayed ray lands on the meter's front face, at the packed hit point.
    assert replay[2] == pytest.approx(gt["meter"]["frontFaceZ"], abs=1e-6)
    assert np.linalg.norm(replay - np.asarray(tap["hit"]["position"])) < 1e-4
    assert gt["capture"]["tap"]["hitDistanceM"] == pytest.approx(tap["hit"]["distance"], abs=1e-4)


def test_tap_hits_depth_at_the_same_distance(tmp_path, rendered):
    """Casting the packed tap ray against the scene reproduces the packed distance."""
    packet = _packet(rendered, "clear-wall")
    gt = _ground_truth(rendered, "clear-wall")
    spec = load_spec(SPEC_DIR / "clear-wall.json")
    scene = Scene(spec)
    tap = packet["taps"][0]
    frame = next(f for f in packet["keyframes"] if f["id"] == tap["keyframe"])
    pose = from_column_major(frame["pose"])
    origin, direction = pixel_ray_arkit(pose, frame["intrinsics"], np.asarray(tap["pixel"]))
    from scripts.gen_app_capture import cast_scene  # local import: module-scoped renderer

    origin_grid = np.broadcast_to(np.asarray(origin), (1, 1, 3))
    dir_grid = np.broadcast_to(np.asarray(direction), (1, 1, 3))
    t, _normal, surface = cast_scene(scene, origin_grid, dir_grid)
    assert surface[0, 0] == 3, "tap ray should hit the meter box"
    assert float(t[0, 0]) == pytest.approx(tap["hit"]["distance"], abs=5e-4)
    assert gt["meter"]["aabb"]["lo"][0] <= float(t[0, 0]) * direction[0] + origin[0] <= gt["meter"]["aabb"]["hi"][0]


def test_depth_pixels_agree_with_geometry(rendered):
    """The depth image stores camera-axis depth, with zero for missing rays."""
    packet = _packet(rendered, "clear-wall")
    tap = packet["taps"][0]
    frame = next(f for f in packet["keyframes"] if f["id"] == tap["keyframe"])
    depth = np.fromfile(rendered["clear-wall"] / frame["depth"]["file"], dtype="<f4").reshape(DEPTH_H, DEPTH_W)
    u, v = tap["pixel"]
    du = min(DEPTH_W - 1, max(0, int(u * DEPTH_W / 640)))
    dv = min(DEPTH_H - 1, max(0, int(v * DEPTH_H / 480)))
    patch = depth[max(0, dv - 1) : dv + 2, max(0, du - 1) : du + 2]
    assert np.isfinite(depth).all() and np.all(depth >= 0)
    confidence = np.fromfile(rendered["clear-wall"] / frame["depth"]["confidenceFile"], dtype=np.uint8).reshape(
        DEPTH_H, DEPTH_W
    )
    assert int(confidence.max()) <= 2, "ARConfidenceLevel only permits 0, 1, and 2"
    finite = patch[patch > 0]
    assert finite.size, "depth around the tap should be valid"
    nearest = float(finite[np.argmin(np.abs(finite - tap["hit"]["distance"]))])
    pose = from_column_major(frame["pose"])
    _origin, ray = pixel_ray_arkit(pose, frame["intrinsics"], np.asarray(tap["pixel"]))
    camera_ray = pose[:3, :3].T @ ray
    expected_axis_depth = tap["hit"]["distance"] * -float(camera_ray[2])
    assert nearest == pytest.approx(expected_axis_depth, abs=0.02)


def test_features_use_world_points_and_uint64_ids(rendered):
    packet = _packet(rendered, "clear-wall")
    root = rendered["clear-wall"]
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("id", "<u8")])
    frame = packet["keyframes"][0]
    records = np.fromfile(root / frame["features"]["file"], dtype=dtype)
    assert len(records) == frame["features"]["count"]
    assert len(np.unique(records["id"])) == len(records)
    assert np.all(records["id"] != 0)
    assert np.all((records["x"] >= -1.1) & (records["x"] <= 5.1))
    assert np.all((records["y"] >= -1.5) & (records["y"] <= 1.4))
    assert np.all((records["z"] >= -2.2) & (records["z"] <= -1.7))


def test_closeup_stills_link_to_their_own_keyframes(rendered):
    packet = _packet(rendered, "clear-wall")
    frames = {frame["id"]: frame for frame in packet["keyframes"]}
    assert {frame.get("purpose") for frame in packet["keyframes"]} >= {"meter_close", "meter_oblique"}
    for still in packet["stills"]:
        linked = frames[still["keyframe"]]
        assert linked["purpose"] == still["purpose"]
        assert (rendered["clear-wall"] / linked["img"]).is_file()


def test_keyframe_times_sit_on_the_30hz_grid(rendered):
    """Keyframe timestamps must have exact arkitPoses rows (3 Hz divides 30 Hz)."""
    packet = _packet(rendered, "clear-wall")
    for frame in packet["keyframes"]:
        remainder = (frame["timestamp"] - packet["session"]["startedAtUptime"]) * 30.0
        # Timestamps are stored to 4 decimals, so a 1/3 s step rounds off the exact
        # grid by up to 0.0001 * 30 = 0.003 of a frame.
        assert abs(remainder - round(remainder)) < 0.005
        assert packet["epochs"][0]["id"] == frame["epoch"]


def test_stills_face_the_meter_within_bounds(rendered):
    packet = _packet(rendered, "clear-wall")
    close = packet["scaleReference"]["meterCloseUp"]
    oblique = packet["scaleReference"]["ext"]["obliqueCloseUp"]
    assert close["captured"] and close["viewAngleDeg"] <= 30.0
    assert 15.0 <= oblique["viewAngleDeg"] <= 50.0
    assert 0.3 <= close["arkitDistanceM"] <= 3.0
    assert 0.3 <= oblique["arkitDistanceM"] <= 3.0
    assert oblique["side"] == "left"


def test_cli_smoke(tmp_path, monkeypatch):
    import sys

    from scripts.gen_app_capture import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gen_app_capture.py",
            "--spec",
            str(SPEC_DIR / "clear-wall.json"),
            "--out",
            str(tmp_path / "cli"),
        ],
    )
    assert main() == 0
    assert (tmp_path / "cli" / "packet.json").is_file()
    assert (tmp_path / "cli" / "ground_truth.json").is_file()
