"""Finalize must answer before promotion, manifest validation, and start_run."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server.api.deps import drain_finalize
from server.api.tests.conftest import sha256_hex
from server.api.tests.test_ingest import _create, _finalize, _put_commit

_EXAMPLE = Path(__file__).resolve().parents[3] / "data/fixtures/packets/examples/example-packet-photo-only.json"


def test_example_photo_finalize_returns_within_three_seconds(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked(_packet):
        started.set()
        assert release.wait(timeout=15)
        return []

    monkeypatch.setattr("server.api.finalize.manifest_errors", blocked)
    packet = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    blob = b"example-photo-still"
    packet["files"][0]["bytes"] = len(blob)
    packet["files"][0]["sha256"] = sha256_hex(blob)
    capture_id = _create(client, packet["packetId"], tier="photo_only")
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})

    client.hold_finalize = True
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_finalize, client, capture_id, packet)
    try:
        started_at = time.perf_counter()
        response, _raw = fut.result(timeout=3)
        elapsed = time.perf_counter() - started_at
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "processing"
        assert elapsed < 3, elapsed
        assert started.wait(timeout=2)
        events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
        assert any(
            event["type"] == "stage"
            and event["data"].get("stage") == "validate"
            and event["data"].get("status") == "running"
            for event in events
        )
        assert client.get(f"/v1/captures/{capture_id}").json()["status"] == "processing"
    finally:
        release.set()
        client.hold_finalize = False
        fut.result(timeout=15)
        pool.shutdown(wait=True)
        drain_finalize(client.app_state)
