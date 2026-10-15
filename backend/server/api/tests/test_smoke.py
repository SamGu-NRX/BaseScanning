from __future__ import annotations

import json
from urllib.parse import urlparse

from server.api.tests.conftest import create_body, photo_packet, sha256_hex
from server.api.tests.test_ingest import _file
from server.contracts.jobs import run_id_for_capture


def _create(client, packet_id="packet-0001"):
    response = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert response.status_code == 201, response.text
    return response.json()["captureId"]


def test_health_and_create(client):
    bare = client.app  # type: ignore[attr-defined]
    health = client.get("/v1/healthz", headers={"Authorization": ""})
    # TestClient merges default headers; call the underlying app via a fresh client.
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    health = anon.get("/v1/healthz")
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "ok"
    assert health.json()["storage"] == "volume"
    capture = client.post("/v1/captures", json=create_body(), headers={"Idempotency-Key": "packet-0001"})
    assert capture.status_code == 201, capture.text
    again = client.post("/v1/captures", json=create_body(), headers={"Idempotency-Key": "packet-0001"})
    assert again.status_code == 200
    assert again.content == capture.content
    _ = bare
    _ = json


def test_register_commit_finalize(client):
    packet_id = "packet-photo-1"
    blob = b"\xff\xd8\xff\xd9"
    capture_id = _create(client, packet_id)
    digest = sha256_hex(blob)
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", blob, meta={"still": "s1"})]},
    )
    assert registered.status_code == 200, registered.text
    target = registered.json()["files"][0]["upload"]
    put = client.put(urlparse(target["url"]).path, content=blob, headers=target["headers"])
    assert put.status_code == 200, put.text
    committed = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": "stills/s1.jpg", "sha256": digest}]},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["committed"] == ["stills/s1.jpg"]
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob)])
    raw = json.dumps(packet).encode()
    finalized = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": sha256_hex(raw),
        },
    )
    assert finalized.status_code == 202, finalized.text
    body = finalized.json()
    assert body["status"] == "processing"
    assert body["missing"] == []
    assert body["runId"] == run_id_for_capture(capture_id, raw)
