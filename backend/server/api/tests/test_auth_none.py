"""Demo stack: HSS_AUTH_MODE=none. No bearer. One device principal."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.auth import Principal, authenticate
from server.api.replay import run_replay
from server.api.tests.conftest import create_body
from server.common.config import Settings
from server.contracts.jobs import RunRecord, StageRecord

_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")


def _settings(tmp_path) -> Settings:
    return Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_AUTH_MODE="none",
        HSS_INGEST_HOOKS="null",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_ENVIRONMENT="dev",
        SMP_SERVICE_NAME="capture-api",
    )


def test_missing_authorization_is_the_demo_device(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert authenticate(None, settings, {}) == Principal(label="demo", reviewer=False)
    presented = authenticate("Bearer not-used", settings, {})
    assert presented.label == "demo"
    assert presented.reviewer is False

    app = create_app(settings=settings)
    client = TestClient(app)
    created = client.post(
        "/v1/captures",
        json=create_body("packet-demo"),
        headers={"Idempotency-Key": "packet-demo"},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    record = app.state.deps.state.get_capture(capture_id)
    assert record is not None
    assert record.owner_principal == "demo"

    deps = app.state.deps
    html_key = f"captures/{capture_id}/result/review.html"
    result_key = f"captures/{capture_id}/result/result.json"
    verdict_key = f"captures/{capture_id}/verdicts/verdict.C.json"
    deps.storage.put_bytes(html_key, b"<p>decision: reject</p>", "text/html")
    deps.storage.put_bytes(
        result_key,
        json.dumps({"ready": True, "verdictUrl": "verdicts/verdict.C.json"}).encode(),
        "application/json",
    )
    deps.storage.put_bytes(verdict_key, b'{"outcome":"fail"}', "application/json")
    deps.state.put_run(
        RunRecord(
            run_id="run_demo",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={
                "result/result.json": result_key,
                "result/review.html": html_key,
                "verdicts/verdict.C.json": verdict_key,
            },
        )
    )
    deps.state.upsert_stage(
        StageRecord(run_id="run_demo", stage="result", status="done", attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )

    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    assert result.json()["verdictUrl"] is None
    assert "fail" not in result.text

    review = client.get(f"/v1/captures/{capture_id}/review")
    assert review.status_code == 200, review.text
    assert b"decision: reject" in review.content


def test_replay_mini_arkit_without_a_token(tmp_path) -> None:
    app = create_app(settings=_settings(tmp_path))
    client = TestClient(app)
    summary = run_replay(
        packet_path=_PACKET,
        api="http://testserver/v1",
        until="processing",
        client=client,
    )
    assert summary["exit"] == 0, summary
    assert summary["committed"] == 11
    assert summary["listed"] == 11
    assert summary["status"] == "processing"
