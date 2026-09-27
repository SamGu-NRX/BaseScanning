"""The API's nested file metadata must reach the early workers as image metadata."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from server.contracts.api import RegisterFileMeta
from server.contracts.jobs import CommittedFile
from server.orchestrator.early.dispatch import dispatch
from server.orchestrator.stateio import get_json
from server.orchestrator.tests.conftest import build_services
from server.reads.types import StillResult


@pytest.mark.parametrize("role", ["still", "keyframe"])
@pytest.mark.parametrize("nested", [True, False])
def test_dispatch_preserves_image_identity_and_purpose(tmp_path, monkeypatch, role, nested):
    services, _, _ = build_services(tmp_path, "memory")
    services.settings.VISION_EXTERNAL_CALLS = "on"
    if role == "keyframe":
        fixture = Path(__file__).resolve().parents[3] / "data/fixtures/packets/mini-arkit/packet.json"
        record = json.loads(fixture.read_text())["keyframes"][0]
        record.update(id="meter-shot", img="keyframes/different-name.jpg", purpose="meter_close")
    else:
        record = {
            "id": "meter-shot",
            "img": "stills/different-name.jpg",
            "purpose": "meter_close",
            "w": 16,
            "h": 16,
            "timestamp": 0,
            "orientation": 1,
            "keyframe": "k_reference",
        }
    meta = RegisterFileMeta.model_validate({role: record}).model_dump(mode="json") if nested else record
    image = BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
    raw = image.getvalue()
    item = CommittedFile(
        path=record["img"],
        role=role,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        generation="1",
        key=f"uploads/cap_test/{record['img']}",
        meta=meta,
    )
    before = item.model_dump(mode="json")
    services.storage.put_bytes(item.key, raw, "image/jpeg")
    calls = []

    def reader(still, storage, cfg):
        calls.append(still)
        return StillResult(still=still), []

    monkeypatch.setattr("server.orchestrator.early.reads.read_still", reader)
    dispatch(services, "cap_test", [item])
    dispatch(services, "cap_test", [item])

    assert len(calls) == 1
    assert calls[0].still_id == record["id"]
    assert calls[0].purpose == "meter_close"
    assert calls[0].path == item.key
    assert item.model_dump(mode="json") == before
    frames = get_json(services.state, "capture_check_frames:cap_test")
    assert len(frames) == 1
    assert frames[0]["id"] == record["id"]
    assert frames[0]["purpose"] == "meter_close"
    events = services.state.events_after("cap_test", 0, limit=100)
    assert any(event.type == "hint" and event.data["code"] == "frames_blurry" for event in events)
