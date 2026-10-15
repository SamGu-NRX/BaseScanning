"""WS-08 writes early/meter_identity.json as a bare MeterIdentity dump."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import StageContext
from server.reads.types import MeterIdentity as ReadsMeterIdentity
from server.scale import run as stage_run
from server.scale.params import ScaleParams
from server.scale.reads_view import IdentityMiss, load_meter_identity

ROOT = Path(__file__).resolve().parents[2]
READS = ROOT / "server/scale/tests/fixtures/reads/m28.reads.json"
TABLE = ROOT / "data/meter-models/meter_models.v1.json"


def _centron_dump() -> dict:
    return ReadsMeterIdentity(
        manufacturer="Itron",
        model_family="Itron CENTRON",
        model_evidence="printed",
        model_confidence=0.92,
        cover_material="unknown",
        ansi_socket_meter=True,
        base_flange_visible=True,
        socket_type="ring",
        agree=True,
        images=["s1"],
    ).model_dump(mode="json")


def test_bare_dump_and_wrapped_reads_both_resolve(tmp_path: Path):
    dump = _centron_dump()
    assert "meter_identity" not in dump
    assert dump["model_family"] == "Itron CENTRON"
    early = tmp_path / "meter_identity.json"
    early.write_text(json.dumps(dump))
    loaded = load_meter_identity(None, {"scaleReference": {}}, early_json=early)
    assert not isinstance(loaded, IdentityMiss)
    assert loaded.model_family == "Itron CENTRON"
    assert loaded.source == "reads"
    wrapped = load_meter_identity(READS, {"scaleReference": {}})
    assert not isinstance(wrapped, IdentityMiss)
    assert wrapped.model_family == "Itron CENTRON"
    assert wrapped.form == "2S"


def test_execute_reads_a_ws08_early_dump_from_the_work_dir(tmp_path: Path):
    packet = json.loads((ROOT / "data/fixtures/packets/mini-arkit/packet.json").read_text())
    packet["scaleReference"]["meterIdentity"] = {}
    packet_dir = tmp_path / "mini-arkit"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(json.dumps(packet))
    work = tmp_path / "work"
    poses = work / "poses"
    poses.mkdir(parents=True)
    cameras = [
        {
            "image": frame["id"],
            "keyframe_id": frame["id"],
            "pose": frame["pose"],
            "intrinsics": frame["intrinsics"],
            "w": frame["w"],
            "h": frame["h"],
        }
        for frame in packet["keyframes"]
    ]
    (poses / "cameras.json").write_text(json.dumps(cameras))
    (poses / "registration_report.json").write_text(
        json.dumps(
            {
                "source_frame": "arkit_world:e1",
                "closeups": {
                    "meter_close": {"frame": "k00003", "registered": True},
                    "meter_oblique": {"frame": "k00004", "registered": True},
                },
                "meter": {"source": "closeup"},
                "lidar_frames": 0,
            }
        )
    )
    early = work / "early" / "meter_identity.json"
    early.parent.mkdir(parents=True)
    early.write_text(json.dumps(_centron_dump()))
    ctx = StageContext(
        capture_id="c",
        run_id="r",
        stage="scale",
        attempt=1,
        branch="image",
        flow="photo",
        tier="arkit",
        work_dir=work,
        inputs={"packet_dir": str(packet_dir), "run_control": "{}"},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=type("Settings", (), {"scale": ScaleParams(table_path=TABLE)})(),
    )
    stage_run(ctx)
    block = json.loads((work / "scale" / "scale.json").read_text())
    assert block["table_entry"] == "itron-centron-c1s-poly"
    assert block["reason_detail"] == "scale_reference_edge_not_found"
