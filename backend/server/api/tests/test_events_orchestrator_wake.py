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


def _create_capture(base_url: str) -> str:
    body = {
        "packetId": "packet-orch-wake-0001",
        "formatVersion": "0.4",
        "tier": "photo_only",
        "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        "flow": "photo",
    }
    response = httpx.post(
        f"{base_url}/v1/captures",
        json=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "packet-orch-wake-0001"},
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

    client = threading.Thread(target=poll)
    client.start()
    try:
        time.sleep(0.3)  # the poll is parked in poll_events before the event lands
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
