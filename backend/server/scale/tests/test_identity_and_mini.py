import json
from pathlib import Path

import numpy as np
import pytest

from server.scale.meter_table import MeterTable, TableMatch, TableMiss
from server.scale.params import ScaleParams
from server.scale.reads_view import IdentityMiss, MeterIdentity, load_meter_identity
from server.scale.run import run_scale
from server.scale.tests.fixtures.build_identity_fixtures import write_all

SEED = Path(__file__).resolve().parent / "fixtures" / "meter_models.seed.v1.json"
READS = Path(__file__).resolve().parent / "fixtures" / "reads"


def _identity(path: Path) -> MeterIdentity:
    payload = json.loads(path.read_text())
    record = payload["meter_identity"]
    meter = payload.get("meter") or {}
    return MeterIdentity(
        record.get("manufacturer"),
        record.get("model_family"),
        meter.get("form"),
        record.get("model_evidence"),
        float(record["model_confidence"]),
        bool(record["agree"]),
        list(record["images"]),
        str(record["cover_material"]),
        str(record["socket_type"]),
        record.get("ansi_socket_meter"),
        record.get("base_flange_visible"),
        None,
        "reads",
    )


def test_replayed_reads_match_centron_only():
    write_all(READS)
    table = MeterTable.load(SEED)
    centron = {}
    for path in sorted(READS.glob("m*.reads.json")):
        found = table.lookup(_identity(path))
        if isinstance(found, TableMatch) and "centron" in found.model_id:
            centron[path.stem.split(".")[0]] = found
        else:
            assert isinstance(found, TableMiss)
            assert found.code == "meter_model_not_in_table"
    assert set(centron) == {"m28", "m39"}
    weights = sorted(weight for _diameter, weight in centron["m28"].cover_hypotheses)
    assert weights == pytest.approx([0.5, 0.5])
    poly = next(item for item in centron["m39"].cover_hypotheses if item[0] == pytest.approx(0.1598))
    assert poly[1] == pytest.approx(0.9)


def _poses(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cameras = []
    for index in range(4):
        matrix = np.eye(4)
        matrix[:3, 3] = (0.2 * index, 0.0, 0.0)
        cameras.append(
            {
                "image": f"k{index}",
                "keyframe_id": f"k{index}",
                "pose": matrix.T.reshape(-1).tolist(),
                "intrinsics": [40, 40, 32, 24],
                "w": 64,
                "h": 48,
            }
        )
    (directory / "cameras.json").write_text(json.dumps(cameras))


def _packet(captured: bool = True) -> dict:
    frames = []
    for index in range(4):
        pose = np.eye(4)
        pose[0, 3] = 0.2 * index
        frames.append({"id": f"k{index}", "w": 64, "h": 48, "intrinsics": [40, 40, 32, 24], "pose": pose.tolist()})
    return {
        "session": {"tier": "arkit", "deviceModel": "iPhone14,5", "lidarAvailable": False},
        "keyframes": frames,
        "stills": [
            {"id": "s1", "purpose": "meter_close", "img": "s1.jpg", "w": 64, "h": 48},
            {"id": "s2", "purpose": "meter_oblique", "img": "s2.jpg", "w": 64, "h": 48},
        ],
        "scaleReference": {"meterCloseUp": {"captured": captured}, "meterIdentity": {}},
        "taps": [{"label": "meter"}],
    }


def _run(tmp_path: Path, name: str, packet: dict, reads: Path | None, report: dict):
    poses = tmp_path / name / "poses"
    _poses(poses)
    (poses / "registration_report.json").write_text(json.dumps(report))
    packet_dir = tmp_path / name / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(json.dumps(packet))
    return run_scale(
        packet_dir,
        packet_dir,
        poses,
        tmp_path / name / "scale",
        params=ScaleParams(table_path=SEED),
        reads_json=reads,
    )


def test_identity_misses_request_another_close_up(tmp_path: Path):
    write_all(READS)
    report = {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0}
    expected = {
        "disabled.reads.json": "meter_model_unread",
        "no_identity.reads.json": "meter_model_unread",
        "low_confidence.reads.json": "meter_identity_low_confidence",
    }
    for filename, detail in expected.items():
        block = _run(tmp_path, filename, _packet(), READS / filename, report)
        assert block.reason == "scale_unresolved"
        assert block.reason_detail == detail
        assert block.views_needed[0].id == "vn-scale-1"
        assert block.views_needed[0].kind == "close_up"
        assert block.member_actions == ["retake_meter_closeups"]
        assert block.status != "verified"


def test_agree_false_and_on_device_stay_below_verified(tmp_path: Path):
    write_all(READS)
    report = {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0}
    printed = _run(tmp_path, "printed", _packet(), READS / "agree_false_printed.reads.json", report)
    assert printed.reason_detail == "meter_closeup_unregistered"
    assert printed.status != "verified"
    loaded = load_meter_identity(READS / "agree_false_printed.reads.json", {}, min_confidence=0.5)
    assert isinstance(loaded, MeterIdentity) and loaded.agree is False
    packet = json.loads((READS / "on_device_only.packet.json").read_text())
    packet.update(_packet())
    packet["scaleReference"] = json.loads((READS / "on_device_only.packet.json").read_text())["scaleReference"]
    device = _run(tmp_path, "device", packet, None, report)
    assert device.reason_detail == "meter_closeup_unregistered"
    assert device.status != "verified"
    identity = load_meter_identity(None, packet)
    assert isinstance(identity, MeterIdentity)
    assert identity.source == "on_device_text"
    assert not isinstance(identity, IdentityMiss)


def test_mini_packet_json_requests_a_retake_on_flat_pixels(tmp_path: Path):
    write_all(READS)
    packet = _packet()
    report = {
        "source_frame": "arkit_world:e1",
        "closeups": {
            "meter_close": {"frame": "k0", "registered": True},
            "meter_oblique": {"frame": "k1", "registered": True},
        },
        "meter": {"source": "closeup"},
        "lidar_frames": 0,
    }
    missing = _run(tmp_path, "missing-pixels", packet, READS / "m28.reads.json", report)
    assert missing.reason_detail == "scale_reference_edge_not_found"
    assert missing.views_needed[0].id == "vn-scale-1"
    assert missing.member_actions == ["retake_meter_closeups"]
    unregistered = _run(
        tmp_path,
        "unregistered",
        packet,
        READS / "m28.reads.json",
        {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0},
    )
    assert unregistered.reason_detail == "meter_closeup_unregistered"
    assert unregistered.views_needed[0].id == "vn-scale-1"


def test_two_runs_match_apart_from_timings(tmp_path: Path):
    report = {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0}
    first = _run(tmp_path, "a", _packet(False), None, report)
    second = _run(tmp_path, "b", _packet(False), None, report)
    left = first.model_dump()
    right = second.model_dump()
    left.pop("timings_s")
    right.pop("timings_s")
    assert left == right
