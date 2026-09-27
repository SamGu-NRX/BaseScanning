"""Round-2 gates: no crashes, safe still ids, declared depth sizes, linear files[]."""

from __future__ import annotations

import copy
import gzip
import json
import time
from pathlib import Path
from typing import Any

import pytest

from server.packet.cli import main
from server.packet.normalize import normalize_packet
from server.packet.purposes import KNOWN_PURPOSES
from server.packet.report import PacketError, ValidationReport
from server.packet.tests.factory import write_arkit
from server.packet.validate import validate_and_normalize, validate_manifest, validate_packet

EXAMPLE = Path(__file__).resolve().parents[3] / "data/fixtures/packets/examples/example-packet.json"
_WRONG: tuple[object, ...] = (None, True, False, 0, 1, -1, 1.5, 1e308, "x", "", [], {})


def _field_paths(node: object, prefix: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    found: list[tuple[object, ...]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = prefix + (key,)
            found.append(path)
            found.extend(_field_paths(value, path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            path = prefix + (index,)
            found.append(path)
            found.extend(_field_paths(value, path))
    return found


def _assign(root: dict, path: tuple[object, ...], value: object) -> None:
    node: object = root
    for key in path[:-1]:
        node = node[key]  # type: ignore[index]
    node[path[-1]] = value  # type: ignore[index]


def test_wrong_type_fuzz_does_not_crash():
    """One wrong-typed value per field. The validator's sweep was 12 x 299 = 3588."""
    original = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    paths = _field_paths(original)
    assert len(paths) * len(_WRONG) >= 3588
    crashes: list[str] = []
    for path in paths:
        for value in _WRONG:
            packet = copy.deepcopy(original)
            _assign(packet, path, value)
            try:
                report = validate_manifest(packet)
            except Exception as exc:  # noqa: BLE001 — the gate is that nothing escapes
                crashes.append(f"{path}={value!r} {type(exc).__name__}: {exc}")
                continue
            assert isinstance(report, ValidationReport)
    assert crashes == []


def test_normalize_wrong_types_do_not_crash(tmp_path: Path):
    """validate_and_normalize is the path the validator fuzzed after validate_manifest."""
    original = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    manifest = tmp_path / "packet.json"
    crashes: list[str] = []
    targets: list[tuple[object, ...]] = [(key,) for key in original]
    targets.extend([("keyframes", 0, "pose"), ("files", 0, "bytes"), ("stills", 0, "gravity"), ("session",)])
    for path in targets:
        for value in _WRONG:
            packet = copy.deepcopy(original)
            _assign(packet, path, value)
            manifest.write_text(json.dumps(packet), encoding="utf-8")
            try:
                validate_and_normalize(manifest)
            except Exception as exc:  # noqa: BLE001
                crashes.append(f"{path}={value!r} {type(exc).__name__}: {exc}")
    assert crashes == []


def test_synthetic_packets_warn_unless_the_process_is_prod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = write_arkit(tmp_path)
    packet = json.loads(manifest.read_text(encoding="utf-8"))
    packet["source"]["kind"] = "synthetic"
    manifest.write_text(json.dumps(packet), encoding="utf-8")
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    report = validate_packet(manifest, stage="manifest")
    assert "synthetic_in_prod" not in {issue.code for issue in report.errors}
    assert any(issue.code == "synthetic_source" for issue in report.warnings)
    monkeypatch.setenv("SMP_ENVIRONMENT", "prod")
    report = validate_packet(manifest, stage="manifest")
    assert any(issue.code == "synthetic_in_prod" for issue in report.errors)


def test_normalize_survives_an_internal_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(_pk: dict) -> list[dict]:
        raise RuntimeError("boom")

    monkeypatch.setattr("server.packet.normalize.epoch_motion", boom)
    packet = normalize_packet(write_arkit(tmp_path))
    assert packet.derived.branch == "image"
    assert packet.derived.stats.keyframes == 0


def test_normalize_rejects_unreadable_documents(tmp_path: Path):
    path = tmp_path / "packet.json"
    path.write_bytes(b"\xff")
    with pytest.raises(PacketError) as caught:
        normalize_packet(path)
    assert caught.value.code == "io"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(PacketError) as caught:
        normalize_packet(path)
    assert caught.value.code == "invalid"


def test_non_object_json_is_one_schema_error(tmp_path: Path):
    for body in (b"[1]", b'"x"', b"null"):
        path = tmp_path / "packet.json"
        path.write_bytes(body)
        report = validate_packet(path, stage="manifest")
        assert [issue.code for issue in report.errors] == ["schema"]
        assert report.errors[0].pointer == "/"
        assert main(["validate", str(path), "--manifest-only"]) == 1


def test_invalid_utf8_is_one_io_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = tmp_path / "packet.json"
    path.write_bytes(b"\xff\xfe not utf-8")
    assert main(["validate", str(path)]) == 2
    assert capsys.readouterr().err.strip() == "io"


def test_deep_nesting_is_one_schema_error(tmp_path: Path):
    path = tmp_path / "packet.json"
    path.write_bytes(b"[" * 20000 + b"0" + b"]" * 20000)
    report = validate_packet(path, stage="manifest")
    assert [issue.code for issue in report.errors] == ["schema"]
    assert main(["validate", str(path), "--manifest-only"]) == 1


def test_unsafe_still_ids_do_not_escape(tmp_path: Path):
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    root = tmp_path / "pkt"
    stills = root / "stills"
    stills.mkdir(parents=True)
    image = Image.new("RGB", (8, 8), (1, 2, 3))
    heic = stills / "s1.heic"
    pillow_heif.from_pillow(image).save(heic, quality=50)
    data = heic.read_bytes()
    packet: dict[str, Any] = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "pkt-still-id",
        "createdAt": "2026-09-26T18:00:00Z",
        "units": {"length": "meters", "angle": "degrees", "time": "s", "image": "px"},
        "conventions": {"world": "arkit", "camera": "arkit", "pose": "column-major"},
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
                "id": "sub/dir",
                "purpose": "house_overview",
                "img": "stills/s1.heic",
                "w": 8,
                "h": 8,
                "timestamp": 1,
                "orientation": 1,
            }
        ],
        "files": [
            {"path": "stills/s1.heic", "bytes": len(data), "sha256": "ab" * 32, "role": "still"},
        ],
    }
    for still_id in ("sub/dir", "a\x00b", "x" * 300, "..", "/tmp/x"):
        packet["stills"][0]["id"] = still_id
        manifest = root / "packet.json"
        manifest.write_text(json.dumps(packet), encoding="utf-8")
        report = validate_packet(manifest, stage="full")
        assert any(issue.code == "path_unsafe" and issue.pointer == "/stills/0/id" for issue in report.errors)
        assert "heic_transcoded" not in {issue.code for issue in report.warnings}
        normalize_packet(manifest, flow="photo", report=report)
        escaped = list(root.rglob("*.jpg"))
        assert escaped == []
        assert not (root / "normalized").exists()


def test_spec_purposes_match_the_capture_schema():
    assert "house_overview" in KNOWN_PURPOSES
    assert "meter_left" in KNOWN_PURPOSES
    assert "meter_right" in KNOWN_PURPOSES
    assert "adjacent_wall" in KNOWN_PURPOSES
    assert "behind_fence" in KNOWN_PURPOSES
    assert KNOWN_PURPOSES.isdisjoint({"door", "garage", "context", "wall", "ground"})
    packet = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    packet["stills"][0]["purpose"] = "house_overview"
    report = validate_manifest(packet)
    assert "unknown_purpose" not in {issue.code for issue in report.warnings}
    packet["stills"][0]["purpose"] = "door"
    report = validate_manifest(packet)
    assert any(issue.code == "unknown_purpose" for issue in report.warnings)


def test_manifest_depth_size_uses_declared_bytes(tmp_path: Path):
    manifest = write_arkit(tmp_path, tier="arkit_lidar")
    packet = json.loads(manifest.read_text(encoding="utf-8"))
    packet["keyframes"][0]["depth"] = {"file": "depth/k0.bin", "w": 2, "h": 2}
    packet["files"].append({"path": "depth/k0.bin", "bytes": 12, "sha256": "ab" * 32, "role": "depth"})
    report = validate_manifest(packet)
    matches = [issue for issue in report.errors if issue.code == "depth_size"]
    assert len(matches) == 1
    assert matches[0].pointer.endswith("/bytes")


def test_malformed_stream_cell_warns_on_a_real_pointer(tmp_path: Path):
    manifest = write_arkit(tmp_path)
    packet = json.loads(manifest.read_text(encoding="utf-8"))
    path = tmp_path / "streams/imuRaw.csv.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("t,ax,ay,az,gx,gy,gz\n0,nope,0,0,0,0,0\n")
    blob = path.read_bytes()
    for entry in packet["files"]:
        if entry["path"].endswith("imuRaw.csv.gz"):
            entry["bytes"] = len(blob)
            entry["sha256"] = "ab" * 32
    manifest.write_text(json.dumps(packet), encoding="utf-8")
    report = validate_packet(manifest, stage="full", check_hashes=False)
    warnings = [issue for issue in report.warnings if issue.code == "stream_cell"]
    assert len(warnings) == 1
    assert warnings[0].pointer == "/streams/imuRaw/file"
    cursor: object = packet
    for part in warnings[0].pointer.strip("/").split("/"):
        assert isinstance(cursor, dict)
        cursor = cursor[part]


def test_many_missing_files_stay_linear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.packet.validate.log_issues", lambda *_args, **_kwargs: None)
    manifest = write_arkit(tmp_path)
    packet = json.loads(manifest.read_text(encoding="utf-8"))
    packet["files"].extend(
        {"path": f"missing/{index}.bin", "bytes": 1, "sha256": "0" * 64, "role": "other"} for index in range(200_000)
    )
    manifest.write_text(json.dumps(packet), encoding="utf-8")
    started = time.perf_counter()
    report = validate_packet(manifest, stage="full", check_hashes=False)
    elapsed = time.perf_counter() - started
    # 200k missing files used to run for more than 10 minutes (quadratic pointer scan).
    assert elapsed < 60
    assert sum(issue.code == "file_missing" for issue in report.errors) >= 200_000
