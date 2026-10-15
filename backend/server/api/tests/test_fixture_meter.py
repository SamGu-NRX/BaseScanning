"""Capture-level meter pixels are stored only when SMP_ENVIRONMENT is dev."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.tests.conftest import TOKEN, ApiClient, create_body
from server.common.config import Settings


def _client(tmp_path, environment: str) -> ApiClient:
    settings = Settings(
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
        SMP_ENVIRONMENT=environment,
    )
    app = create_app(settings=settings)
    test = ApiClient(app)
    test.headers["Authorization"] = f"Bearer {TOKEN}"
    test.app_state = app.state.deps
    return test


def _fixture_body(packet_id: str = "packet-0001") -> dict:
    return create_body(
        packet_id,
        fixtureMeter={"meterFrames": "k00023:989.573,712.173", "anchorLabel": "reference_point"},
    )


def test_prod_create_accepts_and_drops_fixture_meter(client, monkeypatch) -> None:
    # The shared client follows SMP_ENVIRONMENT, which is dev in this suite.
    monkeypatch.setattr(client.app_state.settings, "SMP_ENVIRONMENT", "prod")
    packet_id = "packet-meter-prod"
    created = client.post(
        "/v1/captures",
        json=_fixture_body(packet_id),
        headers={"Idempotency-Key": packet_id},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    extra = client.app_state.state.get_kv(f"capturex:{capture_id}")
    assert "fixture_meter" not in extra
    again = client.post(
        "/v1/captures",
        json=_fixture_body(packet_id),
        headers={"Idempotency-Key": packet_id},
    )
    assert again.status_code == 200
    assert again.json()["captureId"] == capture_id


def test_dev_create_stores_fixture_meter(tmp_path) -> None:
    dev = _client(tmp_path, "dev")
    packet_id = "packet-meter-dev"
    created = dev.post("/v1/captures", json=_fixture_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    extra = dev.app_state.state.get_kv(f"capturex:{capture_id}")
    assert extra["fixture_meter"] == {
        "meter_frames": "k00023:989.573,712.173",
        "anchor_label": "reference_point",
    }


def test_malformed_fixture_meter_is_422(client) -> None:
    packet_id = "packet-meter-bad"
    created = client.post(
        "/v1/captures",
        json=create_body(packet_id, fixtureMeter={"meterFrames": "not-pixels"}),
        headers={"Idempotency-Key": packet_id},
    )
    assert created.status_code == 422, created.text
    assert created.json()["errors"][0]["pointer"] == "/fixtureMeter/meterFrames"


def test_client_without_fixture_meter_is_unchanged(client) -> None:
    bare = TestClient(client.app)
    health = bare.get("/v1/healthz")
    assert health.status_code == 200
    created = client.post("/v1/captures", json=create_body(), headers={"Idempotency-Key": "packet-0001"})
    assert created.status_code == 201, created.text
