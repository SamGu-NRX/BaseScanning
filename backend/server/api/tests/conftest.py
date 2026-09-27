"""Shared client for the ingest API tests."""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.deps import drain_finalize
from server.common.config import Settings

TOKEN = "d" * 40
OTHER = "e" * 40
REVIEWER = "f" * 40


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN},other:{OTHER},reviewer:{REVIEWER}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    test = ApiClient(app)
    test.headers["Authorization"] = f"Bearer {TOKEN}"
    test.app_state = app.state.deps
    return test


class ApiClient(TestClient):
    """Join finalize work after each request so status assertions stay deterministic.

    `hold_finalize` leaves the background thread running so a test can measure the
    response time without waiting on promotion or start_run.
    """

    hold_finalize = False

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        deps = getattr(self, "app_state", None)
        if deps is not None and not self.hold_finalize:
            drain_finalize(deps)
        return response


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def create_body(packet_id: str = "packet-0001", **overrides) -> dict:
    body = {
        "packetId": packet_id,
        "formatVersion": "0.4",
        "tier": "photo_only",
        "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
    }
    body.update(overrides)
    body.setdefault("flow", "photo" if body["tier"] == "photo_only" else "guided")
    return body


CONVENTIONS = {
    "world": "ARKit gravity, y up",
    "camera": "ARKit camera, looks along -z",
    "pose": "arkit-column-major",
    "intrinsics": "[fx, fy, cx, cy]",
    "pixel": "[u, v] top-left, v down",
}


def photo_packet(packet_id: str, blobs: list[tuple[str, bytes]]) -> dict:
    stills = []
    files = []
    for index, (path, data) in enumerate(blobs):
        stills.append(
            {
                "id": f"s{index + 1}",
                "purpose": "meter_surround" if index == 0 else "house_overview",
                "img": path,
                "w": 2,
                "h": 2,
                "timestamp": 0,
                "orientation": 1,
            }
        )
        files.append({"path": path, "bytes": len(data), "sha256": sha256_hex(data), "role": "still"})
    return {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": packet_id,
        "createdAt": "2026-09-26T00:00:00.000Z",
        "units": {"length": "meters", "angle": "degrees", "time": "seconds", "image": "pixels"},
        "conventions": CONVENTIONS,
        "keyframes": [],
        "source": {"kind": "synthetic"},
        "session": {
            "id": "sess",
            "startedAt": "2026-09-26T00:00:00.000Z",
            "startedAtUptime": 0.0,
            "appVersion": "1",
            "deviceModel": "iPhone",
            "systemVersion": "18",
            "tier": "photo_only",
            "lidarAvailable": False,
            "sceneDepthEnabled": False,
        },
        "stills": stills,
        "files": files,
    }
