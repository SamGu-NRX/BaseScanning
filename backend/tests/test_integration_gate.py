"""The integration gate: one committed packet through the whole configured stack.

Every other journey test on this branch runs the app with ``HSS_INGEST_HOOKS="null"``,
so the packet stops at ``processing`` and no test proves the last leg — the
configured local hook that turns a finalized packet into a run, a verdict and a
readable result. This file is that leg, end to end, over the committed
``mini-arkit`` packet fixture and the documented dev settings (SD-73):
``HSS_INGEST_HOOKS=auto`` with orchestrator stage stubs, memory state and local
volume storage — no Modal, no GPU, no network, no credentials.

One test walks the device's own sequence (``CaptureAPI``): create the capture,
register files, PUT bytes to the signed upload URLs, commit, finalize
``packet.json``, then poll events and read the result back. It asserts a useful
terminal result: the run id is the deterministic hash of this capture and these
packet bytes, the event feed ends with exactly one terminal ``verdict_ready``,
the capture lands on a terminal status equal to the served outcome's kind, and
``GET /result`` returns that outcome with the finalize run id. Stub verdicts
always land on ``manual_review``, so the exact value is asserted and documents
the deterministic path rather than a hope.
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
from server.common.config import Settings
from server.contracts.jobs import run_id_for_capture

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/packets/mini-arkit"

TERMINAL_STATUSES = {"complete", "manual_review", "failed", "expired"}
TERMINAL_EVENTS = {"verdict_ready", "retake_request", "failed"}

# The documented dev setting (SD-73): the real hook chain with stubbed stages.
# The orchestrator service template is built from the process environment
# (`server.orchestrator.services.current`), so these must be env, not only
# Settings fields — the finalize handoff runs on a background thread.
TEMPLATE_ENV = {
    "HSS_ORCH_STAGE_STUBS": "1",
    # Prewarm only asks Modal for warm GPU pools; with the Modal SDK installed
    # but no token its background spawn task throws an unretrieved AuthError.
    # The documented SD-11 knob keeps the gate no-cloud.
    "HSS_ORCH_PREWARM": "0",
    "VISION_EXTERNAL_CALLS": "off",
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "volume",
    "SMP_ENVIRONMENT": "dev",
}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """The app with the configured local hook, plus a private copy of the fixture."""
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
    root = tmp_path / "packet"
    shutil.copytree(FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {'d' * 40}"
    return root, packet, client, app


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def test_integration_gate_committed_packet_to_terminal_result(gate) -> None:
    root, packet, client, app = gate

    # --- create -----------------------------------------------------------
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
    capture_id = body["captureId"]
    assert body["status"] == "uploading"

    # --- register + upload + commit ----------------------------------------
    registered_paths = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        item = {
            "path": entry["path"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
            "role": entry.get("role", "still"),
            "contentType": _content_type(entry.get("role", "still")),
        }
        registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
        assert registered.status_code == 200, registered.text
        target = registered.json()["files"][0]
        assert target["state"] == "pending"
        upload = target["upload"]
        assert upload["method"] == "PUT" and upload["url"].startswith("http://testserver/v1/uploads/")
        # The signed URL is its own capability: no bearer header on the PUT.
        put = TestClient(app).put(upload["url"], content=data, headers=upload.get("headers") or {})
        assert put.status_code == 200, put.text
        registered_paths.append({"path": item["path"], "sha256": item["sha256"]})

    commit = client.post(f"/v1/captures/{capture_id}/files:commit", json={"files": registered_paths})
    assert commit.status_code == 200, commit.text
    committed = commit.json()
    assert committed["committed"] == [item["path"] for item in registered_paths]
    assert committed["notFound"] == [] and committed["mismatch"] == []

    # --- finalize (the configured local hook takes over) -------------------
    raw = (root / "packet.json").read_bytes()
    finalize = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet["packetId"],
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert finalize.status_code == 202, finalize.text
    accepted = finalize.json()
    assert accepted["status"] == "processing" and accepted["missing"] == []
    # The run id is not a random allocation: this capture plus these packet bytes.
    assert accepted["runId"] == run_id_for_capture(capture_id, raw)
    drain_finalize(app.state.deps)

    # --- event polling ------------------------------------------------------
    events: list[dict] = []
    after = 0
    for _ in range(50):
        page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
        events.extend(page["events"])
        if page["next"] == after:
            break
        after = page["next"]
    else:
        pytest.fail("event feed never stopped moving")

    seqs = [event["seq"] for event in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert any(event["type"] == "files_committed" for event in events)
    stage_events = [event for event in events if event["type"] == "stage"]
    assert {event["data"]["stage"] for event in stage_events} == {
        "validate",
        "poses",
        "objects",
        "reads",
        "scale",
        "dense",
        "coverage",
        "scene",
        "objects_3d",
        "placement",
        "criteria",
        "result",
    }
    terminals = [event for event in events if event["type"] in TERMINAL_EVENTS]
    assert len(terminals) == 1
    assert terminals[0]["type"] == "verdict_ready"
    assert terminals[0]["data"]["runId"] == accepted["runId"]
    assert terminals[0]["data"]["kind"] == "manual_review"
    # After the verdict only its own stage bookkeeping follows; nothing re-decides.
    assert all(event["type"] == "stage" for event in events[seqs.index(terminals[0]["seq"]) + 1 :])

    # --- the terminal result the device reads -------------------------------
    status = client.get(f"/v1/captures/{capture_id}").json()
    assert status["status"] in TERMINAL_STATUSES, status

    served = client.get(f"/v1/captures/{capture_id}/result")
    assert served.status_code == 200, served.text
    result = served.json()
    assert result["runId"] == accepted["runId"]
    assert result["status"] == status["status"] == "manual_review"
    assert result["outcome"] is not None and result["outcome"]["kind"] == "manual_review"
    assert isinstance(result["viewsNeeded"], list) and isinstance(result["memberActions"], list)
    assert result["reviewUrl"] == f"/v1/captures/{capture_id}/review"
    # A device principal never sees the reviewer verdict blob.
    assert result["verdictUrl"] is None
