"""One real capture journey against the committed ``mini-arkit`` packet fixture.

The test starts ``create_app()`` with memory state, local volume storage, static
auth and a test HMAC secret, then sends the same sequence the iOS app sends
(``CaptureAPI``): create the capture, register files, PUT bytes to the signed
upload URLs, commit, finalize ``packet.json``, and read events. It asserts what
the device can observe: persisted bytes, hashes, capture status and what the
server does when a file never arrived. Everything runs in-process over ASGI
with no cloud service; the packet used here is a private copy of the committed
fixture under a temporary directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.deps import drain_finalize
from server.api.keys import final_key, packet_key, upload_key
from server.api.tests.conftest import TOKEN, ApiClient
from server.common.config import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/packets/mini-arkit"


@pytest.fixture
def journey(tmp_path):
    """The app under test plus a private copy of the committed packet fixture."""
    root = tmp_path / "packet"
    shutil.copytree(FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"device:{TOKEN}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="null",
        VISION_EXTERNAL_CALLS="on",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    client = ApiClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    client.app_state = app.state.deps
    return root, packet, client, app


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _sealed_files(root: Path, packet: dict) -> list[dict]:
    """Register bodies with the hashes the app computes from the sealed files."""
    files = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        role = entry.get("role", "still")
        files.append(
            {
                "path": entry["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
                "role": role,
                "contentType": _content_type(role),
            }
        )
    return files


def _create_capture(client: ApiClient, packet: dict) -> str:
    session = packet["session"]
    created = client.post(
        "/v1/captures",
        json={
            "packetId": packet["packetId"],
            "formatVersion": packet["formatVersion"],
            "tier": session["tier"],
            "flow": "guided",
            "device": {
                "model": session["deviceModel"],
                "systemVersion": session["systemVersion"],
                "appVersion": session["appVersion"],
            },
        },
        headers={"Idempotency-Key": packet["packetId"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "uploading"
    return body["captureId"]


def _register_upload_commit(
    client: ApiClient, app, capture_id: str, root: Path, files: list[dict], skip: str | None = None
) -> None:
    """The app's upload loop: register, PUT bytes to the signed URL, commit."""
    for item in files:
        registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
        assert registered.status_code == 200, registered.text
        target = registered.json()["files"][0]
        assert target["path"] == item["path"]
        if target["path"] == skip:
            continue
        assert target["state"] == "pending", target
        upload = target["upload"]
        assert upload["method"] == "PUT" and upload["url"].startswith("http://testserver/v1/uploads/")
        # Signed URLs carry their own capability; the app sends no bearer there.
        put = TestClient(app).put(
            upload["url"],
            content=(root / item["path"]).read_bytes(),
            headers=upload.get("headers") or {},
        )
        assert put.status_code == 200, put.text
    commit = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]},
    )
    assert commit.status_code == 200, commit.text
    body = commit.json()
    expected = [item["path"] for item in files if item["path"] != skip]
    assert body["committed"] == expected
    assert body["notFound"] == ([] if skip is None else [skip])
    assert body["mismatch"] == []


def _finalize(client: ApiClient, capture_id: str, root: Path, packet: dict):
    raw = (root / "packet.json").read_bytes()
    accepted = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet["packetId"],
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert accepted.status_code == 202, accepted.text
    return accepted.json()


def test_mini_arkit_packet_upload_commit_finalize_and_events(journey) -> None:
    root, packet, client, app = journey
    capture_id = _create_capture(client, packet)
    _register_upload_commit(client, app, capture_id, root, _sealed_files(root, packet))

    payload = _finalize(client, capture_id, root, packet)
    assert payload["missing"] == []
    assert payload["runId"]
    drain_finalize(client.app_state)

    status = client.get(f"/v1/captures/{capture_id}").json()
    assert status["status"] == "processing"
    assert status["filesCommitted"] == len(packet["files"])

    # Persisted bytes: every file moved from uploads/ to captures/, plus packet.json.
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        assert app.state.deps.storage.get_bytes(upload_key(capture_id, entry["path"])) is not None
        assert app.state.deps.storage.get_bytes(final_key(capture_id, entry["path"])) == data
        stored = app.state.deps.state.get_file(capture_id, entry["path"])
        assert stored is not None and stored.sha256 == hashlib.sha256(data).hexdigest()
    assert app.state.deps.storage.get_bytes(packet_key(capture_id)) == (root / "packet.json").read_bytes()

    # The device observes the same journey as events.
    events = client.get(f"/v1/captures/{capture_id}/events").json()
    assert events["status"] == "processing"
    types = [event["type"] for event in events["events"]]
    assert [event["seq"] for event in events["events"]] == sorted(event["seq"] for event in events["events"])
    assert "files_committed" in types
    stage_events = [event for event in events["events"] if event["type"] == "stage"]
    assert any(
        (event.get("data") or {}).get("stage") == "validate"
        and (event.get("data") or {}).get("runId") == payload["runId"]
        for event in stage_events
    ), stage_events
    incremental = client.get(f"/v1/captures/{capture_id}/events", params={"after": events["next"]}).json()
    assert incremental["events"] == []


def test_finalize_reports_files_that_never_uploaded(journey) -> None:
    root, packet, client, app = journey
    capture_id = _create_capture(client, packet)
    files = _sealed_files(root, packet)
    skipped = files[0]["path"]

    _register_upload_commit(client, app, capture_id, root, files, skip=skipped)

    payload = _finalize(client, capture_id, root, packet)
    assert payload["status"] == "awaiting_files"
    assert payload["missing"] == [skipped]

    status = client.get(f"/v1/captures/{capture_id}").json()
    assert status["status"] == "awaiting_files"
    assert status["filesCommitted"] == len(packet["files"]) - 1
    # The uploaded files are stored; the skipped one never landed.
    for entry in packet["files"][1:]:
        data = (root / entry["path"]).read_bytes()
        assert app.state.deps.storage.get_bytes(upload_key(capture_id, entry["path"])) == data
    with pytest.raises(FileNotFoundError):
        app.state.deps.storage.get_bytes(upload_key(capture_id, skipped))
