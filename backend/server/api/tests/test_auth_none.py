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
from server.orchestrator.clock import Clock
from server.orchestrator.config import load_orch
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.outcomes import Facts, decide
from server.orchestrator.services import Services, bind

_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "STORAGE_BACKEND": "volume",
        "STATE_BACKEND": "memory",
        "VOLUME_ROOT": str(tmp_path / "vol"),
        "CAPTURE_API_PUBLIC_URL": "http://testserver",
        "UPLOAD_URL_HMAC_SECRET": "h" * 32,
        "HSS_INGEST_HOOKS": "null",
        "GIT_SHA": "0123456789abcdef",
        "VISION_EXTERNAL_CALLS": "on",
        "SMP_ENVIRONMENT": "dev",
        "SMP_SERVICE_NAME": "capture-api",
    }
    values.update(overrides)
    return Settings(**values)


def test_missing_authorization_is_the_demo_device(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert settings.HSS_AUTH_MODE == "none"
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


def test_review_page_preview_ply_is_served_and_reviewer_gated(tmp_path) -> None:
    """The review page's relative fetch("preview.ply") must resolve through the API.

    Served at GET /v1/captures/{id}/review, the browser resolves the page's
    relative preview fetch to /v1/captures/{id}/preview.ply — PR #249 review
    finding 1: nothing served that, so the point cloud never drew.
    """

    ply_bytes = (
        b"ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
        b"property float x\nproperty float y\nproperty float z\nend_header\n"
    ) + b"\x00" * 12
    device_token = "d" * 40
    reviewer_token = "r" * 40

    def _boot(settings: Settings):
        app = create_app(settings=settings)
        return app, TestClient(app)

    def _stage_preview(app, capture_id: str) -> None:
        deps = app.state.deps
        html_key = f"captures/{capture_id}/result/review.html"
        ply_key = f"captures/{capture_id}/result/preview.ply"
        deps.storage.put_bytes(html_key, b"<p>review</p>", "text/html")
        deps.storage.put_bytes(ply_key, ply_bytes, "application/octet-stream")
        deps.state.put_run(
            RunRecord(
                run_id=f"run_{capture_id}",
                capture_id=capture_id,
                packet_sha256="c" * 64,
                status="done",
                branch="image",
                flow="photo",
                tier="photo_only",
                profile_default="C",
                created_at="2026-09-26T00:00:00.000Z",
                deadline_at="2026-09-26T01:00:00.000Z",
                artifacts={"result/review.html": html_key, "result/preview.ply": ply_key},
            )
        )
        deps.state.upsert_stage(
            StageRecord(
                run_id=f"run_{capture_id}",
                stage="result",
                status="done",
                attempt=1,
                heartbeat="2026-09-26T00:00:00.000Z",
            )
        )

    # Auth-none demo mode: the page and its relative preview fetch both resolve.
    app, client = _boot(_settings(tmp_path))
    created = client.post(
        "/v1/captures",
        json=create_body("packet-preview"),
        headers={"Idempotency-Key": "packet-preview"},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _stage_preview(app, capture_id)

    review = client.get(f"/v1/captures/{capture_id}/review")
    assert review.status_code == 200, review.text
    preview = client.get(f"/v1/captures/{capture_id}/preview.ply")
    assert preview.status_code == 200, preview.text
    assert preview.content == ply_bytes
    assert preview.headers["content-type"] == "application/octet-stream"

    # Static mode: the artifact is reviewer-gated, same as the page that links it.
    static_settings = _settings(
        tmp_path,
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"dev:{device_token},reviewer:rev:{reviewer_token}",
    )
    app, client = _boot(static_settings)
    created = client.post(
        "/v1/captures",
        json=create_body("packet-preview-static"),
        headers={"Idempotency-Key": "packet-preview-static", "Authorization": f"Bearer {device_token}"},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _stage_preview(app, capture_id)

    anonymous = client.get(f"/v1/captures/{capture_id}/preview.ply")
    assert anonymous.status_code == 401, anonymous.text
    device = client.get(
        f"/v1/captures/{capture_id}/preview.ply",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert device.status_code == 403, device.text
    reviewer = client.get(
        f"/v1/captures/{capture_id}/preview.ply",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert reviewer.status_code == 200, reviewer.text
    assert reviewer.content == ply_bytes


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


def test_configured_static_mode_requires_a_bearer(tmp_path) -> None:
    settings = _settings(tmp_path, HSS_AUTH_MODE="static")
    assert settings.HSS_AUTH_MODE == "static"
    app = create_app(settings=settings)
    client = TestClient(app)
    response = client.post(
        "/v1/captures",
        json=create_body("packet-static"),
        headers={"Idempotency-Key": "packet-static"},
    )
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthorized"


def test_result_reads_stage_artifact_when_the_run_lags(tmp_path) -> None:
    """A done result stage can land before the run row's artifact map."""

    app = create_app(settings=_settings(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/v1/captures",
        json=create_body("packet-lag"),
        headers={"Idempotency-Key": "packet-lag"},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    deps = app.state.deps
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps({"ready": True, "viewsNeeded": [], "memberActions": []}).encode(),
        "application/json",
    )
    deps.state.put_run(
        RunRecord(
            run_id="run_lag",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="running",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={},
        )
    )
    deps.state.upsert_stage(
        StageRecord(run_id="run_lag", stage="result", status="done", attempt=1, heartbeat="2026-09-26T00:00:01.000Z")
    )
    deps.state.put_blob(
        "stage_artifacts:run_lag:result",
        json.dumps({"result/result.json": result_key}).encode(),
    )
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    assert result.json()["runId"] == "run_lag"


def test_exhausted_run_serves_the_manual_result_without_a_result_stage(tmp_path) -> None:
    app = create_app(settings=_settings(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/v1/captures",
        json=create_body("packet-exhausted"),
        headers={"Idempotency-Key": "packet-exhausted"},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    deps = app.state.deps
    run = RunRecord(
        run_id="run_exhausted",
        capture_id=capture_id,
        packet_sha256="b" * 64,
        status="failed",
        branch="image",
        flow="photo",
        tier="photo_only",
        profile_default="C",
        created_at="2026-09-26T00:00:00.000Z",
        deadline_at="2026-09-26T01:00:00.000Z",
        artifacts={},
        spend_usd={},
    )
    deps.state.put_run(run)
    services = Services(
        settings=deps.settings,
        orch=load_orch(),
        state=deps.state,
        storage=deps.storage,
        clock=Clock(),
        executor=InlineExecutor(config=load_orch(), stubs=True),
        work_root=tmp_path / "work",
    )
    with bind(services):
        RunCoordinator(services)._close(
            run,
            decide(Facts(required_exhausted=True, exhausted_stage="scale", exhausted_code="timeout")),
            "scale exceeded its budget",
        )
    deps.state.set_status(capture_id, "manual_review")
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["runId"] == "run_exhausted"
    assert body["outcome"]["kind"] == "manual_review"
    assert "stage_exhausted:scale:timeout" in body["outcome"]["message"]
