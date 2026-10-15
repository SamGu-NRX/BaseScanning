"""mini-arkit stills reuse keyframe images. Replay meta follows the file role."""

from __future__ import annotations

import json
from pathlib import Path

from server.api.replay import _meta, run_replay
from server.api.tests.conftest import TOKEN

_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")


def test_keyframe_role_does_not_take_still_meta() -> None:
    packet = json.loads(_PACKET.read_text(encoding="utf-8"))
    entry = next(item for item in packet["files"] if item["path"] == "keyframes/k00003.jpg")
    assert entry["role"] == "keyframe"
    meta = _meta(packet, entry)
    assert meta is not None
    assert set(meta) == {"keyframe"}
    assert meta["keyframe"]["id"] == "k00003"


def test_mini_arkit_replay_reaches_processing(client) -> None:
    summary = run_replay(
        packet_path=_PACKET,
        api="http://testserver/v1",
        token=TOKEN,
        until="processing",
        client=client,
    )
    assert summary["exit"] == 0, summary
    assert summary["committed"] == 11
    assert summary["listed"] == 11
    assert summary["status"] == "processing"
