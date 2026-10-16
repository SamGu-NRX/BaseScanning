"""Escaped background finalize failures stay visible and retryable."""

from __future__ import annotations

import pytest

from server.api.deps import drain_finalize, extras
from server.api.tests.conftest import photo_packet
from server.api.tests.test_ingest import _create, _finalize, _put_commit


def _ready_capture(client, packet_id: str):
    """A capture with every packet file committed, ready for a full finalize."""
    capture_id = _create(client, packet_id)
    blob = b"jpeg"
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob)])
    return capture_id, packet


def test_failed_run_start_stays_retryable(client, monkeypatch):
    capture_id, packet = _ready_capture(client, "packet-hook-fail")
    deps = client.app_state

    def boom(*args, **kwargs):
        raise RuntimeError("modal start_run failed")

    monkeypatch.setattr(deps.hooks, "on_finalized", boom)
    client.hold_finalize = True
    try:
        response, _raw = _finalize(client, capture_id, packet)
        assert response.status_code == 202, response.text
        # finish_ready raises ApiFailure(503); _work_once handles it through the
        # normal background failure path, so the drain itself completes.
        drain_finalize(deps)
    finally:
        client.hold_finalize = False

    # The handoff failed, so the run must not be marked started.
    assert not extras(deps, capture_id).get("run_started")
    assert extras(deps, capture_id).get("started_run_id") is None
    events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    failures = [e for e in events if e["type"] == "failed" and e["data"].get("code") == "run_start_failed"]
    assert failures and failures[0]["data"]["next"] == "retry_finalize"
    assert deps.state.get_capture(capture_id).status == "uploading"

    # With the hook healthy again, a later finalize starts the run.
    monkeypatch.undo()
    second, _raw = _finalize(client, capture_id, packet)
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "processing"
    assert extras(deps, capture_id).get("run_started") is True


def test_background_exception_fails_retryably(client, monkeypatch):
    capture_id, packet = _ready_capture(client, "packet-bg-fail")
    deps = client.app_state

    def offline(_key):
        raise RuntimeError("volume offline")

    monkeypatch.setattr(deps.storage, "get_bytes", offline)
    client.hold_finalize = True
    try:
        response, _raw = _finalize(client, capture_id, packet)
        assert response.status_code == 202, response.text
        with pytest.raises(RuntimeError):
            drain_finalize(deps)
    finally:
        client.hold_finalize = False

    # The storage error must surface as a failure event and a retryable status,
    # not as a capture stuck on processing with the exception on a dead Future.
    events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    failures = [e for e in events if e["type"] == "failed" and e["data"].get("code") == "finalize_failed"]
    assert failures and failures[0]["data"]["next"] == "retry_finalize"
    assert deps.state.get_capture(capture_id).status == "uploading"

    # Storage healthy again: a later finalize completes the handoff.
    monkeypatch.undo()
    second, _raw = _finalize(client, capture_id, packet)
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "processing"
