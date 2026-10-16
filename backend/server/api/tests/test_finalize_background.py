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

    # A local fake of any orchestrator start_run failure — Modal is the
    # production transport; this suite pins the retryable handoff contract.
    def boom(*args, **kwargs):
        raise RuntimeError("orchestrator start_run failed")

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


def test_escaped_failure_publishes_before_releasing_the_worker_slot(client, monkeypatch):
    """Finding: _guard — the slot was released before the failure was published.

    A finalize retry that lands in the gap sees a free slot, publishes its
    success, and is then overwritten by the older worker's failure — the healed
    capture resets to uploading behind a failure event for a run that started.
    With publication and the run_started check serialized under the finish lock,
    the older worker observes the retry's success and defers; either order ends
    consistent. This pins the ordering half: at the moment the failure is
    published, this worker still owns the slot.
    """
    import server.api.finalize as finalize_module

    capture_id, packet = _ready_capture(client, "packet-bg-order")
    deps = client.app_state

    def offline(_key):
        raise RuntimeError("volume offline")

    publication_slot_state: list[bool] = []
    original_failure_path = finalize_module._on_background_failure

    def probing_failure_path(*args, **kwargs):
        publication_slot_state.append(finalize_module._slots[capture_id].inflight)
        return original_failure_path(*args, **kwargs)

    monkeypatch.setattr(deps.storage, "get_bytes", offline)
    monkeypatch.setattr(finalize_module, "_on_background_failure", probing_failure_path)
    client.hold_finalize = True
    try:
        response, _raw = _finalize(client, capture_id, packet)
        assert response.status_code == 202, response.text
        with pytest.raises(RuntimeError):
            drain_finalize(deps)
    finally:
        client.hold_finalize = False

    # One storage exception publishes one failure, while this worker still holds
    # the slot: a retry that starts after the slot frees sees the failure already
    # published (or the run_started flag of a healed retry), never both.
    assert publication_slot_state == [True]
    assert finalize_module._slots[capture_id].inflight is False
    events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    assert len([e for e in events if e["type"] == "failed" and e["data"].get("code") == "finalize_failed"]) == 1
    assert deps.state.get_capture(capture_id).status == "uploading"

    # And the healed retry still lands one healthy run.
    monkeypatch.undo()
    second, _raw = _finalize(client, capture_id, packet)
    assert second.status_code == 202, second.text
    assert extras(deps, capture_id).get("run_started") is True
    assert second.json()["status"] == "processing"


def test_failure_publication_error_releases_the_worker_slot(auto_client, monkeypatch):
    """Finding: _guard (PR #293 Greptile P1) — the slot was cleared and the future
    settled only after the failure publication. A publication that itself raises —
    a transient state outage makes put_capture fail — exited the thread with the
    slot busy and the future unsettled: later finalize retries answered
    processing forever and no worker ever started again. The failure write stays
    under the lock; the slot release and the future settlement always run.
    """
    import server.api.finalize as finalize_module
    from server.api.tests.test_hooks_orchestrator import (
        PACKET,
        PACKET_ID,
        _upload_and_commit,
    )
    from server.api.tests.test_hooks_orchestrator import (
        _finalize as _finalize_packet,
    )

    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _upload_and_commit(client, capture_id)

    deps = client.app_state

    def offline(_key):
        raise RuntimeError("volume offline")

    def failing_publication(*args, **kwargs):
        raise RuntimeError("state outage: failure write raised")

    original_get_bytes = deps.storage.get_bytes
    original_publication = finalize_module._on_background_failure
    # The finalize work and its failure publication both hit the outage. Both
    # seams are swapped by hand and restored before the healed retry:
    # monkeypatch.undo would also revert the fixture's env settings
    # (HSS_ORCH_STAGE_STUBS), which the retry's handoff still needs.
    deps.storage.get_bytes = offline
    finalize_module._on_background_failure = failing_publication
    client.hold_finalize = True
    try:
        body = _finalize_packet(client, capture_id)
        assert body["status"] == "processing", body
        # The worker's original error is what the Future carries, publication
        # failure or not.
        with pytest.raises(RuntimeError):
            drain_finalize(deps)
    finally:
        client.hold_finalize = False
        deps.storage.get_bytes = original_get_bytes
        finalize_module._on_background_failure = original_publication

    # The slot is free even though the publication raised.
    assert finalize_module._slots[capture_id].inflight is False

    # A finalize retry starts a new worker and the capture reaches a terminal
    # state, instead of sitting on processing with no worker alive.
    body = _finalize_packet(client, capture_id)
    assert body["status"] == "processing"
    assert deps.state.get_capture(capture_id).status == "manual_review"
    run = deps.state.get_run(body["runId"])
    assert run is not None and run.status == "done"
