"""DELETE stays final when background validation or publication is still running."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import httpx
import pytest

from server.api.deps import drain_finalize
from server.api.tests.conftest import photo_packet
from server.api.tests.test_ingest import _create, _finalize, _put_commit
from server.common.storage import StorageUnavailable
from server.contracts.validation import ValidationIssue


def _capture(client):
    packet_id = "packet-delete-pending"
    capture_id = _create(client, packet_id)
    blob = b"synthetic-small-image"
    _put_commit(client, capture_id, "stills/s1.jpg", blob)
    return capture_id, photo_packet(packet_id, [("stills/s1.jpg", blob)])


def _assert_erased(client, capture_id):
    deps = client.app_state
    assert deps.state.get_capture(capture_id) is None
    assert deps.state.get_kv(f"capturex:{capture_id}") is None
    assert deps.state.files(capture_id) == []
    assert deps.state.events_after(capture_id, 0) == []
    assert deps.state.runs_for_capture(capture_id) == []
    for prefix in (f"uploads/{capture_id}/", f"captures/{capture_id}/", f"work/{capture_id}/"):
        assert deps.storage.list_prefix(prefix) == []
    assert client.get(f"/v1/captures/{capture_id}").status_code == 410


@pytest.mark.parametrize("invalid", [False, True])
def test_delete_during_manifest_validation_does_not_resurrect(client, monkeypatch, invalid):
    capture_id, packet = _capture(client)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def validate(_packet):
        entered.set()
        assert release.wait(10)
        if invalid:
            return [ValidationIssue(code="synthetic_invalid", level="error", pointer="/", message="invalid fixture")]
        return []

    monkeypatch.setattr("server.api.finalize.manifest_errors", validate)
    client.app_state.hooks.on_finalized = lambda *a, **kw: calls.append("finalized")
    client.hold_finalize = True
    try:
        response, _ = _finalize(client, capture_id, packet)
        assert response.status_code == 202
        assert entered.wait(3)
        # Deletion must not wait for pure manifest validation to finish.
        assert client.delete(f"/v1/captures/{capture_id}").status_code == 204
        _assert_erased(client, capture_id)
    finally:
        release.set()
        drain_finalize(client.app_state)
        client.hold_finalize = False
    _assert_erased(client, capture_id)
    assert calls == []
    assert client.delete(f"/v1/captures/{capture_id}").status_code == 204


@pytest.mark.parametrize("phase", ["copy", "copy_failure", "hook"])
def test_delete_waits_for_inflight_writes_then_cleans_them(client, monkeypatch, phase):
    capture_id, packet = _capture(client)
    deps = client.app_state
    entered, release = threading.Event(), threading.Event()
    calls = []
    original_copy = deps.storage.copy

    def pause():
        entered.set()
        assert release.wait(10)

    def copy(*args, **kwargs):
        pause()
        if phase == "copy_failure":
            raise StorageUnavailable("synthetic copy failure")
        return original_copy(*args, **kwargs)

    def finalized(*args, **kwargs):
        calls.append("finalized")
        if phase == "hook":
            pause()
        # A synchronous hook may update capture state before returning.
        deps.state.set_status(capture_id, "manual_review")

    def cancel(*args, **kwargs):
        calls.append("cancelled")

    monkeypatch.setattr("server.api.finalize.manifest_errors", lambda _packet: [])
    if phase != "hook":
        monkeypatch.setattr(deps.storage, "copy", copy)
    deps.hooks.on_finalized = finalized
    deps.hooks.cancel_capture = cancel
    client.hold_finalize = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_delete = None
        try:
            response, _ = _finalize(client, capture_id, packet)
            assert response.status_code == 202
            assert entered.wait(3)
            pending_delete = pool.submit(client.delete, f"/v1/captures/{capture_id}")
            with pytest.raises(TimeoutError):
                pending_delete.result(timeout=0.1)
        finally:
            release.set()
            if pending_delete is not None:
                assert pending_delete.result(timeout=5).status_code == 204
            drain_finalize(deps)
            client.hold_finalize = False
    _assert_erased(client, capture_id)
    assert calls == (["cancelled"] if phase == "copy_failure" else ["finalized", "cancelled"])


def test_cancelled_delete_wait_does_not_block_later_requests(client):
    capture_id, _packet = _capture(client)
    deps = client.app_state

    async def probe():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=client.headers) as api:
            with deps.locks.finish_sync(capture_id):
                pending = asyncio.create_task(api.delete(f"/v1/captures/{capture_id}"))
                try:
                    await asyncio.sleep(0.05)
                    assert not pending.done()
                    health = await asyncio.wait_for(api.get("/v1/healthz"), timeout=1)
                    assert health.status_code == 200
                finally:
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
            deleted = await asyncio.wait_for(api.delete(f"/v1/captures/{capture_id}"), timeout=1)
            assert deleted.status_code == 204

    asyncio.run(probe())
    _assert_erased(client, capture_id)
