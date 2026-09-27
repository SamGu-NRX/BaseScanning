"""An amendment gets its own validation, promotion, and run after its parent."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from server.api.deps import extras
from server.api.keys import final_key
from server.api.tests.conftest import photo_packet, sha256_hex
from server.api.tests.test_ingest import _create, _finalize, _put_commit
from server.contracts.jobs import RunRecord

_BLOB = b"synthetic-amendment-image"
_PATH = "stills/s1.jpg"
_AID = "amd-followup"


@pytest.fixture
def started_parent(client, monkeypatch):
    hook = Mock()
    monkeypatch.setattr(client.app_state.hooks, "on_finalized", hook)
    packet_id = "packet-started-parent"
    capture_id = _create(client, packet_id)
    _put_commit(client, capture_id, _PATH, _BLOB)
    response, _ = _finalize(client, capture_id, photo_packet(packet_id, [(_PATH, _BLOB)]))
    assert response.status_code == 202
    assert hook.call_count == 1
    parent_run = response.json()["runId"]
    deps = client.app_state
    assert extras(deps, capture_id)["started_run_id"] == parent_run
    deps.state.set_status(capture_id, "needs_views")
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(result_key, json.dumps({"viewsNeeded": [{"id": "v1"}]}).encode(), "application/json")
    deps.state.put_run(
        RunRecord(
            run_id=parent_run,
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={"result/result.json": result_key},
        )
    )
    opened = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": _AID, "answers": ["v1"]},
        headers={"Idempotency-Key": _AID},
    )
    assert opened.status_code == 201
    packet = photo_packet("packet-followup", [(_PATH, _BLOB)])
    packet["parentPacketId"] = packet_id
    return capture_id, parent_run, packet, hook


def _finalize_child(client, capture_id, packet):
    raw = json.dumps(packet).encode()
    return client.post(
        f"/v1/captures/{capture_id}/amendments/{_AID}/finalize",
        content=raw,
        headers={"Content-Type": "application/json", "X-Packet-Sha256": sha256_hex(raw)},
    )


@pytest.mark.parametrize("commit_first", [True, False])
def test_amendment_starts_and_promotes_after_parent(client, started_parent, commit_first):
    capture_id, parent_run, packet, hook = started_parent
    path = f"amendments/{_AID}/{_PATH}"
    if commit_first:
        _put_commit(client, capture_id, path, _BLOB)
    response = _finalize_child(client, capture_id, packet)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == ("processing" if commit_first else "awaiting_files")
    if not commit_first:
        assert hook.call_count == 1
        _put_commit(client, capture_id, path, _BLOB)
    assert hook.call_count == 2
    assert hook.call_args.kwargs["parent_run_id"] == parent_run
    deps = client.app_state
    assert deps.storage.get_bytes(final_key(capture_id, path)) == _BLOB
    assert deps.state.get_file(capture_id, path).key == final_key(capture_id, path)
    assert extras(deps, capture_id)["started_run_id"] == response.json()["runId"]
    assert deps.state.get_kv(f"amend:{capture_id}:{_AID}")["status"] == "finalized"
    assert extras(deps, capture_id)["open_amendment"] is None
    replay = _finalize_child(client, capture_id, packet)
    assert replay.status_code == 202
    assert hook.call_count == 2


def test_amendment_manifest_is_validated_again(client, started_parent):
    capture_id, _parent_run, packet, hook = started_parent
    _put_commit(client, capture_id, f"amendments/{_AID}/{_PATH}", _BLOB)
    packet["stills"][0]["keyframe"] = "missing-keyframe"
    response = _finalize_child(client, capture_id, packet)
    assert response.status_code == 202
    assert client.app_state.state.get_capture(capture_id).status == "failed"
    assert hook.call_count == 1
    assert extras(client.app_state, capture_id)["last_422"]
