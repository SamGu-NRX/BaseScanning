"""The events long-poll must observe events the orchestrator writes to state.

PR #286's device run held the app's second events poll for its full 20,022 ms
although the orchestrator had finished within a second: poll_events only wakes
on deps.wake, which server.api.events.emit sets, while the orchestrator's
server.orchestrator.events.emit writes straight to state storage and never
touches the waiter. These tests drive the real app over uvicorn on loopback
and use only the orchestrator path to append the event a waiter is waiting on.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest
import uvicorn

from server.api.app import create_app
from server.common.config import Settings
from server.contracts.api import FailedData
from server.orchestrator import events as orchestrator_events

TOKEN = "d" * 40


def _settings(tmp_path) -> Settings:
    return Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_SERVICE_NAME="capture-api",
    )


@pytest.fixture
def server(tmp_path):
    """The real app served by uvicorn on loopback; yields (base_url, deps)."""

    app = create_app(settings=_settings(tmp_path))
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            pytest.fail("uvicorn never started")
        time.sleep(0.01)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}", app.state.deps
    server.should_exit = True
    thread.join(timeout=10)


def _create_capture(base_url: str, packet_id: str = "packet-orch-wake-0001") -> str:
    body = {
        "packetId": packet_id,
        "formatVersion": "0.4",
        "tier": "photo_only",
        "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        "flow": "photo",
    }
    response = httpx.post(
        f"{base_url}/v1/captures",
        json=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": packet_id},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    return response.json()["captureId"]


def test_long_poll_returns_within_one_poll_interval_of_an_orchestrator_event(server):
    """wait=20 returns in under 2 s once the orchestrator appends an event."""

    base_url, deps = server
    capture_id = _create_capture(base_url)

    result: dict = {}

    def poll():
        started = time.perf_counter()
        response = httpx.get(
            f"{base_url}/v1/captures/{capture_id}/events",
            params={"after": 0, "wait": 20},
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
        result["elapsed"] = time.perf_counter() - started
        result["response"] = response

    # The orchestrator may only emit once the server has read an empty feed,
    # so the event cannot land before the poll parks: an emit that arrives
    # earlier is picked up by the first store read of any implementation, and
    # the test would pass without exercising the wait at all. Wrap the state's
    # events_after to signal when a read has come back empty.
    empty_feed = threading.Event()
    original_events_after = deps.state.events_after

    def spy_events_after(capture_id: str, seq: int, limit: int = 100):
        rows = original_events_after(capture_id, seq, limit=limit)
        if not rows:
            empty_feed.set()
        return rows

    deps.state.events_after = spy_events_after

    client = threading.Thread(target=poll)
    client.start()
    try:
        assert empty_feed.wait(timeout=10), "the poll never reached the empty-feed wait"
        record = orchestrator_events.emit(
            deps.state,
            capture_id,
            "failed",
            FailedData(code="processing_failed", message="orchestrator blew up", next="manual_review"),
            run_id="run-orch-wake-1",
        )
    finally:
        client.join(timeout=30)
        assert not client.is_alive(), "the long-poll client never returned"

    assert result["response"].status_code == 200, result["response"].text
    body = result["response"].json()
    assert [event["type"] for event in body["events"]] == ["failed"]
    assert body["events"][0]["seq"] == record.seq
    assert body["next"] == record.seq
    assert result["elapsed"] < 2.0, (
        f"the poll held for {result['elapsed']:.3f} s although the orchestrator event landed within one poll interval"
    )


def test_a_slow_state_read_does_not_stall_other_requests_on_the_same_app(server):
    """poll_events reads the store off the event loop, so one slow read cannot park the app.

    With ModalDictState every events_after is a synchronous remote read. Reading
    it on the event loop blocks every other request for the length of the read,
    roughly 40 times per quiet 20 s poll (Greptile P2, PR #290). The parked
    poll's first read gates on a threading.Event here; a wait=0 request for a
    second capture must return while that gate is still shut.
    """

    base_url, deps = server
    parked_capture = _create_capture(base_url, "packet-orch-wake-0002")
    probe_capture = _create_capture(base_url, "packet-orch-wake-0003")

    entered = threading.Event()
    release = threading.Event()
    original_events_after = deps.state.events_after

    def gated_events_after(capture_id: str, seq: int, limit: int = 100):
        if capture_id == parked_capture:
            entered.set()
            release.wait(timeout=4.0)
        return original_events_after(capture_id, seq, limit=limit)

    deps.state.events_after = gated_events_after

    def poll():
        httpx.get(
            f"{base_url}/v1/captures/{parked_capture}/events",
            params={"after": 0, "wait": 3},
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )

    poller = threading.Thread(target=poll)
    poller.start()
    try:
        assert entered.wait(timeout=10), "the long-poll never reached its first store read"
        started = time.perf_counter()
        response = httpx.get(
            f"{base_url}/v1/captures/{probe_capture}/events",
            params={"after": 0, "wait": 0},
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
        probe_elapsed = time.perf_counter() - started
    finally:
        release.set()
        poller.join(timeout=30)

    assert response.status_code == 200, response.text
    assert probe_elapsed < 1.5, (
        f"a wait=0 request took {probe_elapsed:.3f} s while another poll's store read was in flight: "
        "poll_events is reading the state store on the event loop"
    )


def test_wait_zero_returns_immediately_when_the_feed_is_empty(server):
    """wait=0 stays an immediate empty-page read, with no parked wait."""

    base_url, _ = server
    capture_id = _create_capture(base_url)
    started = time.perf_counter()
    response = httpx.get(
        f"{base_url}/v1/captures/{capture_id}/events",
        params={"after": 0, "wait": 0},
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=10,
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert response.json() == {"status": "uploading", "next": 0, "events": []}
    assert elapsed < 1.0, f"wait=0 took {elapsed:.3f} s"
