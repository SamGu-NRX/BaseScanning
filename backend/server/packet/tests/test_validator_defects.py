"""Validator findings from PR 37: paths, schema, crashes, and checks that did not fire."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pillow_heif
import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from server.packet.images import transcode_heic
from server.packet.normalize import normalize_packet
from server.packet.params import PacketParams
from server.packet.streams import read_table
from server.packet.tests.factory import identity_pose, write_arkit
from server.packet.validate import validate_and_normalize, validate_manifest, validate_packet
from server.packet.validate_legacy import validate_packet as validate_legacy

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "normalized-packet.v0.schema.json"


def _save(manifest: Path, pk: dict) -> None:
    manifest.write_text(json.dumps(pk), encoding="utf-8")


def _codes(report, level: str = "errors") -> set[str]:
    return {issue.code for issue in getattr(report, level)}


def _photo(root: Path, *, still_id: str, name: str, data: bytes, exif: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "stills" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    still = {
        "id": still_id,
        "purpose": "context",
        "img": f"stills/{name}",
        "w": 32,
        "h": 16,
        "timestamp": 1,
        "orientation": 1,
    }
    if exif is not None:
        still["exif"] = exif
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "pkt-defect-photo",
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
        "stills": [still],
        "files": [
            {
                "path": f"stills/{name}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "role": "still",
            }
        ],
    }
    manifest = root / "packet.json"
    _save(manifest, packet)
    return manifest


def _heic_bytes(tmp_path: Path) -> bytes:
    pillow_heif.register_heif_opener()
    image = Image.new("RGB", (32, 16), (10, 20, 30))
    jpeg_path = tmp_path / "plain.jpg"
    image.save(jpeg_path, quality=50)
    heic = tmp_path / "plain.heic"
    pillow_heif.from_pillow(Image.open(jpeg_path)).save(heic, quality=50)
    return heic.read_bytes()


def test_heic_still_id_cannot_escape_packet_root(tmp_path: Path):
    root = tmp_path / "pkt"
    manifest = _photo(
        root,
        still_id="../../../x",
        name="s1.heic",
        data=_heic_bytes(tmp_path),
        exif={"Make": "Apple", "FocalLenIn35mmFilm": 26, "Orientation": 1},
    )
    normalize_packet(manifest, flow="photo")
    assert not (root.parent / "x.jpg").exists()
    assert not (root / "normalized" / "stills" / "x.jpg").exists()


def test_committed_normalized_schema_validates_a_packet(tmp_path: Path):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert "DepthDerived" in schema["$defs"]
    packet = normalize_packet(write_arkit(tmp_path / "src"), flow="guided")
    out = tmp_path / "normalized.json"
    packet.to_json(out)
    Draft202012Validator(schema).validate(json.loads(out.read_text(encoding="utf-8")))


def test_string_width_returns_schema(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["keyframes"][0]["w"] = "wide"
    _save(manifest, pk)
    report = validate_packet(manifest)
    assert "schema" in _codes(report)


def test_short_door_box_returns_schema(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["scaleReference"]["ext"]["doorViews"][0]["box"] = [1, 2]
    report = validate_packet(pk, root=manifest.parent, stage="manifest")
    assert "schema" in _codes(report)


def test_meter_identity_list_returns_schema(tmp_path: Path):
    pk = json.loads(write_arkit(tmp_path).read_text())
    pk["scaleReference"]["meterIdentity"] = ["acme"]
    report = validate_manifest(pk)
    assert "schema" in _codes(report)


def test_door_views_string_returns_schema(tmp_path: Path):
    pk = json.loads(write_arkit(tmp_path).read_text())
    pk["scaleReference"]["ext"]["doorViews"] = "side"
    report = validate_manifest(pk)
    assert "schema" in _codes(report)


def test_imu_stream_is_read_once_per_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = write_arkit(tmp_path, n=6)
    calls: list[str] = []
    real = read_table

    def wrapped(path: Path):
        calls.append(Path(path).name)
        return real(path)

    monkeypatch.setattr("server.packet.streams.read_table", wrapped)
    validate_packet(manifest)
    assert calls.count("imuRaw.csv.gz") == 2


def test_nan_tap_ray_is_rejected(tmp_path: Path):
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
            "rayDirection": [float("nan"), 0, -1],
            "epoch": "e1",
        }
    ]
    _save(manifest, pk)
    report = validate_packet(manifest, stage="manifest")
    assert "tap_replay" in _codes(report)
    assert report.maxTapReplayError != 0.0


def test_features_size_on_arkit_without_depth(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    blob = tmp_path / "features" / "k0.bin"
    blob.parent.mkdir()
    blob.write_bytes(b"\x00" * 4)
    pk = json.loads(manifest.read_text())
    pk["keyframes"][0]["features"] = {"file": "features/k0.bin", "count": 1}
    _save(manifest, pk)
    report = validate_packet(manifest)
    match = [issue for issue in report.errors if issue.code == "features_size"]
    assert len(match) == 1
    assert match[0].pointer == "/keyframes/0/features/file"


def test_corrupt_heic_normalizes_without_crashing(tmp_path: Path):
    payload = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    root = tmp_path / "pkt"
    manifest = _photo(root, still_id="s1", name="s1.heic", data=payload, exif=None)
    report = validate_packet(manifest)
    assert "image_undecodable" in _codes(report)
    normalize_packet(manifest, flow="photo", report=report)
    transcode_heic(json.loads(manifest.read_text()), root, PacketParams())


def test_undecodable_jpeg_clears_decodable_flag(tmp_path: Path):
    root = tmp_path / "src"
    manifest = write_arkit(root, n=2)
    rel = "keyframes/k00000.jpg"
    data = b"\xff\xd8\xff" + b"\x00" * 32
    (root / rel).write_bytes(data)
    pk = json.loads(manifest.read_text())
    for entry in pk["files"]:
        if entry["path"] == rel:
            entry["bytes"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
    _save(manifest, pk)
    packet, _report = validate_and_normalize(manifest)
    frame = packet._packet["keyframes"][0]
    assert frame["ext"]["hss"]["decodable"] is False
    total = len(packet._packet["keyframes"]) + len(packet._packet["stills"])
    assert packet.derived.stats.imagesDecodable == total - 1


def test_missing_exif_warns_on_the_still(tmp_path: Path):
    image = Image.new("RGB", (32, 16), (1, 2, 3))
    path = tmp_path / "plain.jpg"
    image.save(path, quality=80)
    manifest = _photo(tmp_path / "pkt", still_id="s1", name="s1.jpg", data=path.read_bytes(), exif=None)
    report = validate_packet(manifest)
    missing = [issue for issue in report.warnings if issue.code == "exif_missing"]
    assert len(missing) == 1
    assert missing[0].pointer == "/stills/0"


def test_highres_intrinsics_are_rescaled_before_the_fov_check(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    frame = dict(pk["keyframes"][0])
    frame["id"] = "hi"
    frame["w"] = 4032
    frame["h"] = 3024
    frame["intrinsics"] = [1440.0, 1440.0, 960.0, 720.0]
    frame["timestamp"] = 110.0
    frame["highResolution"] = True
    pk["keyframes"].append(frame)
    report = validate_packet(pk, root=manifest.parent, stage="manifest")
    warnings = _codes(report, "warnings")
    assert "intrinsics_rescaled" in warnings
    assert "fov_unusual" not in warnings
    assert "principal_point_offcentre" not in warnings


def test_unknown_purpose_without_keyframe(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["stills"][0]["purpose"] = "eave"
    pk["stills"][0].pop("keyframe", None)
    report = validate_packet(pk, root=manifest.parent, stage="manifest")
    match = [issue for issue in report.warnings if issue.code == "unknown_purpose"]
    assert len(match) == 1
    assert match[0].pointer == "/stills/0/purpose"


def test_oblique_distance_pointer(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    pk = json.loads(manifest.read_text())
    pk["scaleReference"]["ext"]["obliqueCloseUp"] = {
        "captured": True,
        "still": "s1",
        "arkitDistanceM": 9.0,
        "viewAngleDeg": 30,
    }
    report = validate_packet(pk, root=manifest.parent, stage="manifest")
    pointers = [issue.pointer for issue in report.warnings if issue.code == "scale_distance"]
    assert pointers == ["/scaleReference/ext/obliqueCloseUp/arkitDistanceM"]


def _legacy_base(tmp_path: Path) -> tuple[Path, dict]:
    manifest = write_arkit(tmp_path / "legacy")
    return manifest, json.loads(manifest.read_text())


def test_validate_legacy_accepts_the_factory_packet(tmp_path: Path):
    manifest, _pk = _legacy_base(tmp_path)
    report = validate_legacy(manifest)
    assert report["ok"], report["errors"]


def test_validate_legacy_rejects_bad_files_and_geometry(tmp_path: Path):
    manifest, pk = _legacy_base(tmp_path)
    root = manifest.parent
    gone = root / pk["files"][0]["path"]
    gone.unlink()
    missing = validate_legacy(manifest)
    assert any("missing" in item for item in missing["errors"])
    gone.write_bytes(b"x")
    pk["files"][0]["bytes"] = 99
    pk["files"][0]["sha256"] = "0" * 64
    _save(manifest, pk)
    sized = validate_legacy(manifest)
    assert any("bytes" in item for item in sized["errors"])

    manifest, pk = _legacy_base(tmp_path / "hash")
    root = manifest.parent
    rel = pk["files"][0]["path"]
    target = root / rel
    target.write_bytes(target.read_bytes() + b"z")
    pk["files"][0]["bytes"] = target.stat().st_size
    _save(manifest, pk)
    hashed = validate_legacy(manifest)
    assert any("sha256" in item for item in hashed["errors"])

    manifest, pk = _legacy_base(tmp_path / "pose")
    pk["keyframes"][0]["pose"] = [1.0]
    _save(manifest, pk)
    assert any("pose" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "nan")
    pk["keyframes"][0]["pose"] = [float("nan")] * 16
    _save(manifest, pk)
    assert any("non-finite" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "ortho")
    pose = identity_pose()
    pose[0] = 2.0
    pk["keyframes"][0]["pose"] = pose
    _save(manifest, pk)
    assert any("orthonormal" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "k")
    pk["keyframes"][0]["intrinsics"] = [-1, 1440, 960, 720]
    _save(manifest, pk)
    assert any("intrinsics" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "epoch")
    pk["keyframes"][0]["epoch"] = "missing"
    _save(manifest, pk)
    assert any("unknown epoch" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "time")
    pk["keyframes"][1]["timestamp"] = pk["keyframes"][0]["timestamp"]
    _save(manifest, pk)
    timed = validate_legacy(manifest)
    assert any("timestamp" in item for item in timed["warnings"])

    manifest, pk = _legacy_base(tmp_path / "depth")
    depth = manifest.parent / "depth" / "d0.bin"
    depth.parent.mkdir()
    depth.write_bytes(b"\x00" * 8)
    conf = manifest.parent / "depth" / "c0.bin"
    conf.write_bytes(b"\x00" * 2)
    pk["keyframes"][0]["depth"] = {"file": "depth/d0.bin", "w": 2, "h": 2, "confidenceFile": "depth/c0.bin"}
    _save(manifest, pk)
    depths = validate_legacy(manifest)
    assert any("w*h*4" in item for item in depths["errors"])
    assert any("w*h bytes" in item for item in depths["errors"])
    assert any("not listed" in item for item in depths["errors"])

    manifest, pk = _legacy_base(tmp_path / "photo")
    pk["session"]["tier"] = "photo_only"
    pk["stills"] = []
    _save(manifest, pk)
    assert any("stills" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "tap")
    pk["taps"] = [
        {
            "id": "t-missing",
            "keyframe": "nope",
            "pixel": [1, 1],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [0, 0, -1],
        }
    ]
    _save(manifest, pk)
    assert any("unknown keyframe" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "replay")
    pk["taps"] = [
        {
            "id": "t-replay",
            "keyframe": "k00000",
            "pixel": [960, 720],
            "rayOrigin": [0, 0, 0],
            "rayDirection": [1, 0, 0],
        }
    ]
    _save(manifest, pk)
    assert any("ray replay" in item for item in validate_legacy(manifest)["errors"])

    manifest, pk = _legacy_base(tmp_path / "still")
    pk["stills"][0]["img"] = "stills/unlisted.jpg"
    _save(manifest, pk)
    assert any("not listed" in item for item in validate_legacy(manifest)["errors"])

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "packet.json").write_text(
        json.dumps({"format": "nope", "formatVersion": "1.0", "session": {"tier": "lidar"}, "files": []}),
        encoding="utf-8",
    )
    bare_report = validate_legacy(bare / "packet.json")
    assert any(item.startswith("format:") for item in bare_report["errors"])
    assert any("formatVersion" in item for item in bare_report["errors"])
    assert any("session.tier" in item for item in bare_report["errors"])

    empty = tmp_path / "arkit-empty"
    empty.mkdir()
    (empty / "packet.json").write_text(
        json.dumps(
            {
                "format": "house-capture-packet",
                "formatVersion": "0.4",
                "session": {"tier": "arkit"},
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    empty_report = validate_legacy(empty / "packet.json")
    assert any(item.startswith("epochs:") for item in empty_report["errors"])
    assert any(item.startswith("keyframes:") for item in empty_report["errors"])
