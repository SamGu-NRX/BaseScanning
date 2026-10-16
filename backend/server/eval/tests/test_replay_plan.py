"""Upload plan: order, variant rule, content types. The live API is not smoked."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from server.eval.replay import (
    PacketLayoutError,
    ReplayError,
    main,
    plan_uploads,
    replay,
    resolve_packet_file,
)


def _packet() -> dict:
    return {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "pkt-test-0001",
        "session": {"tier": "arkit", "deviceModel": "iPhone15,4", "systemVersion": "26.0", "appVersion": "0.4"},
        "epochs": [{"id": "e1"}, {"id": "e0"}],
        "keyframes": [
            {
                "id": "k2",
                "img": "keyframes/k2.jpg",
                "timestamp": 20.0,
                "epoch": "e1",
                "features": {"file": "keyframes/k2.features.bin"},
            },
            {
                "id": "k1",
                "img": "keyframes/k1.jpg",
                "timestamp": 5.0,
                "epoch": "e0",
                "depth": {"file": "keyframes/k1.depth.bin"},
            },
            {"id": "k0", "img": "keyframes/k0.jpg", "timestamp": 1.0, "epoch": "e0"},
        ],
        "stills": [
            {"id": "s-close", "purpose": "meter_close", "img": "stills/meter_close.jpg"},
            {"id": "s-panel", "purpose": "panel_nameplate", "img": "stills/panel.jpg"},
            {"id": "s-door", "purpose": "entry_door", "img": "stills/door.jpg"},
            {"id": "s-shared", "purpose": "meter_oblique", "img": "keyframes/k0.jpg"},
        ],
        "streams": {"motion": {"file": "streams/motion.csv.gz"}},
        "geometry": {"mesh": "geometry/mesh.ply"},
    }


def test_upload_order_and_roles():
    plan = plan_uploads(_packet(), rate=2.0)
    paths = [item.path for item in plan.files]
    assert paths[0] == "stills/meter_close.jpg"
    assert paths[1] == "stills/panel.jpg"
    # epochs are listed e1 then e0, so k2 (e1) precedes the e0 pair, which is in timestamp order
    assert paths.index("keyframes/k2.jpg") < paths.index("keyframes/k0.jpg") < paths.index("keyframes/k1.jpg")
    assert paths[paths.index("keyframes/k1.jpg") + 1] == "keyframes/k1.depth.bin"
    assert paths[paths.index("keyframes/k2.jpg") + 1] == "keyframes/k2.features.bin"
    assert "stills/door.jpg" in paths
    assert paths.count("keyframes/k0.jpg") == 1
    assert paths[-2] == "streams/motion.csv.gz"
    assert paths[-1] == "geometry/mesh.ply"
    assert "packet.json" not in paths
    by_path = {item.path: item for item in plan.files}
    assert by_path["keyframes/k0.jpg"].role == "keyframe"
    assert by_path["keyframes/k0.jpg"].content_type == "image/jpeg"
    assert by_path["keyframes/k0.jpg"].meta["keyframe"]["id"] == "k0"
    assert by_path["stills/meter_close.jpg"].meta == {"still": _packet()["stills"][0]}
    assert by_path["keyframes/k1.depth.bin"].content_type == "application/octet-stream"
    assert by_path["keyframes/k1.depth.bin"].meta is None
    assert by_path["streams/motion.csv.gz"].content_type == "application/gzip"
    assert plan.flow == "guided"
    assert plan.batch_interval_s == 2.0
    assert plan.capture_body["packetId"] == "pkt-test-0001"
    record = by_path["stills/panel.jpg"].register(b"abc")
    assert record["bytes"] == 3
    assert record["role"] == "still"
    assert len(record["md5"]) > 10
    assert len(record["sha256"]) == 64


def test_photo_only_flow_and_fast_rate():
    packet = _packet()
    packet["session"]["tier"] = "photo_only"
    plan = plan_uploads(packet, rate=0)
    assert plan.flow == "photo"
    assert plan.batch_interval_s == 0.0


def test_variant_less_folder_rejects_variant(tmp_path: Path):
    (tmp_path / "packet.json").write_text(json.dumps(_packet()))
    path, variant = resolve_packet_file(tmp_path, None)
    assert path.name == "packet.json"
    assert variant is None
    with pytest.raises(PacketLayoutError):
        resolve_packet_file(tmp_path, "arkit-lidar")


def test_variant_file(tmp_path: Path):
    (tmp_path / "packet.arkit-lidar.json").write_text(json.dumps(_packet()))
    path, variant = resolve_packet_file(tmp_path, "arkit-lidar")
    assert path.name == "packet.arkit-lidar.json"
    assert variant == "arkit-lidar"


def test_invalid_packet_is_exit_4(tmp_path: Path):
    target = tmp_path / "packet.json"
    target.write_text(json.dumps(_packet()))
    with pytest.raises(ReplayError) as caught:
        replay(target, "http://127.0.0.1:9", "unused")
    assert caught.value.code == 4
    assert "contract validation" in str(caught.value)


def test_dry_run_reads_a_tar_packet(tmp_path: Path, capsys):
    folder = tmp_path / "pkt"
    folder.mkdir()
    (folder / "packet.json").write_text(json.dumps(_packet()))
    archive = tmp_path / "packet.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(folder / "packet.json", arcname="packet.json")
    assert main(["--packet", str(archive), "--dry-run"]) == 0
    assert "pkt-test-0001" in capsys.readouterr().out


def test_missing_tar_packet_is_exit_2(tmp_path: Path, capsys):
    assert main(["--packet", str(tmp_path / "missing.tar"), "--dry-run"]) == 2
    assert "unrecognised packet path" in capsys.readouterr().err


def test_unreachable_api_is_exit_2(tmp_path: Path):
    source = Path("data/fixtures/packets/examples/example-packet.json")
    target = tmp_path / "packet.json"
    target.write_text(source.read_text())
    with pytest.raises(ReplayError) as caught:
        replay(target, "http://127.0.0.1:9", "unused")
    assert caught.value.code == 2
    assert "unreachable" in str(caught.value)
