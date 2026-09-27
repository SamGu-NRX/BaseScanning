"""Behaviour the WS-01 done-when table names."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse

import httpx
import jwt
import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from server.api.auth import authenticate
from server.api.errors import ApiFailure
from server.api.hooks import OrchestratorHooks
from server.api.tests.conftest import (
    CONVENTIONS,
    OTHER,
    REVIEWER,
    TOKEN,
    create_body,
    md5_b64,
    photo_packet,
    sha256_hex,
)
from server.common.config import Settings
from server.common.geometry import from_column_major, pixel_ray_arkit
from server.contracts.api import ConfirmationRequest
from server.contracts.jobs import RunRecord

ROUTES = [
    ("POST", "/v1/captures"),
    ("GET", "/v1/captures/cap_x"),
    ("POST", "/v1/captures/cap_x/files"),
    ("POST", "/v1/captures/cap_x/files:commit"),
    ("POST", "/v1/captures/cap_x/files:complete"),
    ("POST", "/v1/captures/cap_x/finalize"),
    ("GET", "/v1/captures/cap_x/events"),
    ("GET", "/v1/captures/cap_x/result"),
    ("GET", "/v1/captures/cap_x/review"),
    ("POST", "/v1/captures/cap_x/amendments"),
    ("POST", "/v1/captures/cap_x/amendments/amd/finalize"),
    ("POST", "/v1/captures/cap_x/confirmations"),
    ("GET", "/v1/captures/cap_x/confirmations"),
    ("POST", "/v1/devices"),
    ("DELETE", "/v1/captures/cap_x"),
]


def _anon(client) -> TestClient:
    return TestClient(client.app)


def _create(client, packet_id: str, **overrides) -> str:
    response = client.post(
        "/v1/captures", json=create_body(packet_id, **overrides), headers={"Idempotency-Key": packet_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["captureId"]


def _file(path: str, data: bytes, role: str = "still", meta: dict | None = None) -> dict:
    body = {
        "path": path,
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "md5": md5_b64(data),
        "role": role,
        "contentType": "image/jpeg" if role in {"still", "keyframe"} else "application/octet-stream",
    }
    if isinstance(meta, dict) and isinstance(meta.get("still"), str):
        meta = {
            "still": {
                "id": meta["still"],
                "purpose": "meter_close",
                "img": path,
                "w": 2,
                "h": 2,
                "timestamp": 0,
                "orientation": 1,
            }
        }
    if meta is not None:
        body["meta"] = meta
    return body


def _put_commit(client, capture_id: str, path: str, data: bytes, role: str = "still", meta: dict | None = None):
    registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [_file(path, data, role, meta)]})
    assert registered.status_code == 200, registered.text
    item = registered.json()["files"][0]
    if item["state"] == "pending":
        target = item["upload"]
        put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
        assert put.status_code == 200, put.text
    committed = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": sha256_hex(data)}]},
    )
    assert committed.status_code == 200, committed.text
    return committed.json()


def _finalize(client, capture_id: str, packet: dict, packet_id: str | None = None):
    raw = json.dumps(packet).encode()
    packet_id = packet_id or packet["packetId"]
    return client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={"Idempotency-Key": packet_id, "Content-Type": "application/json", "X-Packet-Sha256": sha256_hex(raw)},
    ), raw


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_auth_matrix(client, method, path, capsys):
    response = _anon(client).request(method, path)
    print(f"{method} {path} {response.status_code}")
    assert response.status_code == 401, response.text
    assert response.json()["errors"][0]["code"] == "unauthorized"
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_missing_idempotency_key_and_format(client):
    missing = client.post("/v1/captures", json=create_body())
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["code"] == "idempotency_key_missing"
    old = client.post("/v1/captures", json=create_body(formatVersion="0.3"), headers={"Idempotency-Key": "packet-0001"})
    assert old.status_code == 422
    assert old.json()["errors"][0]["code"] == "format_version_unsupported"
    assert old.json()["errors"][0]["pointer"] == "/formatVersion"


def test_idempotency_conflict(client):
    packet_id = "packet-conflict"
    assert (
        client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id}).status_code
        == 201
    )
    other = client.post(
        "/v1/captures",
        json=create_body(packet_id, tier="arkit"),
        headers={"Idempotency-Key": packet_id},
    )
    assert other.status_code == 409
    assert other.json()["errors"][0]["code"] == "idempotency_conflict"


def test_keyframe_image_rejected(client):
    capture_id = _create(client, "packet-role")
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("a.jpg", b"abc") | {"role": "keyframe_image"}]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["code"] == "role_unknown"
    assert response.json()["errors"][0]["pointer"] == "/files/0/role"


def test_tamper_then_reregister(client):
    capture_id = _create(client, "packet-tamper")
    data = b"jpeg-bytes"
    path = "stills/s1.jpg"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file(path, data, meta={"still": "s1"})]},
    )
    assert registered.status_code == 200, registered.text
    deps = client.app_state
    from server.api.keys import upload_key

    deps.storage.put_bytes(upload_key(capture_id, path), b"jpeg-bytf", "image/jpeg")
    bad = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": sha256_hex(data)}]},
    )
    assert bad.json()["mismatch"] == [path]
    again = _put_commit(client, capture_id, path, data, meta={"still": "s1"})
    assert path in again["committed"]
    assert path not in again["mismatch"]


def test_commit_unknown_path(client):
    capture_id = _create(client, "packet-missing-file")
    response = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": "nope.jpg", "sha256": "a" * 64}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["notFound"] == ["nope.jpg"]


def test_sha256_conflict_on_committed(client):
    capture_id = _create(client, "packet-sha-conflict")
    data = b"same-bytes"
    path = "stills/s1.jpg"
    _put_commit(client, capture_id, path, data, meta={"still": "s1"})
    other = hashlib.sha256(b"other").hexdigest()
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file(path, data, meta={"still": "s1"}) | {"sha256": other}]},
    )
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "sha256_conflict"


def test_early_finalize_then_last_commit(client):
    packet_id = "packet-early"
    capture_id = _create(client, packet_id)
    first, second = b"one", b"two"
    _put_commit(client, capture_id, "stills/s1.jpg", first, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", first), ("stills/s2.jpg", second)])
    early, _raw = _finalize(client, capture_id, packet)
    assert early.status_code == 202, early.text
    assert early.json()["status"] == "awaiting_files"
    assert early.json()["missing"] == ["stills/s2.jpg"]
    _put_commit(client, capture_id, "stills/s2.jpg", second, meta={"still": "s2"})
    status = client.get(f"/v1/captures/{capture_id}")
    assert status.json()["status"] == "processing"


def test_finalize_twice_and_conflict(client):
    packet_id = "packet-twice"
    capture_id = _create(client, packet_id)
    blob = b"only"
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob)])
    first, raw = _finalize(client, capture_id, packet)
    second, _raw = _finalize(client, capture_id, packet)
    assert first.status_code == 202 and second.status_code == 202
    assert first.content == second.content
    packet["createdAt"] = "2026-09-26T00:00:01.000Z"
    changed = json.dumps(packet).encode()
    conflict = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=changed,
        headers={"Idempotency-Key": packet_id, "Content-Type": "application/json"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["errors"][0]["code"] == "packet_sha_conflict"
    assert raw


def _arkit(
    packet_id: str, *, epochs=None, tier="arkit", files=None, keyframes=None, taps=None, parent=None, depth_bytes=None
):
    pose = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    intrinsics = [10.0, 10.0, 5.0, 5.0]
    pixel = [5.0, 5.0]
    origin, direction = pixel_ray_arkit(from_column_major(pose), intrinsics, np.asarray(pixel))
    frame = {
        "id": "k1",
        "img": "keyframes/k1.jpg",
        "w": 4,
        "h": 4,
        "intrinsics": intrinsics,
        "pose": pose,
        "timestamp": 0,
        "tracking": "normal",
        "epoch": "e1",
        "reason": "auto",
    }
    if depth_bytes is not None:
        frame["depth"] = {"file": "depth/k1.bin", "w": 2, "h": 2}
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": packet_id,
        "createdAt": "2026-09-26T00:00:00.000Z",
        "units": {"length": "meters", "angle": "degrees", "time": "seconds", "image": "pixels"},
        "conventions": CONVENTIONS,
        "source": {"kind": "synthetic"},
        "session": {
            "id": "sess",
            "startedAt": "2026-09-26T00:00:00.000Z",
            "startedAtUptime": 0.0,
            "appVersion": "1",
            "deviceModel": "iPhone",
            "systemVersion": "18",
            "tier": tier,
            "lidarAvailable": tier == "arkit_lidar",
            "sceneDepthEnabled": False,
            "worldAlignment": "gravity",
            "videoFormat": {"imageResolution": [4, 4]},
        },
        "epochs": [{"id": "e1", "startTime": 0, "reason": "sessionStart"}] if epochs is None else epochs,
        "tracking": [{"time": 0, "state": "normal"}],
        "keyframes": keyframes if keyframes is not None else [frame],
        "streams": {
            "imuRaw": {"file": "streams/imu.csv.gz", "columns": ["t"], "rateHz": 100},
            "arkitPoses": {"file": "streams/poses.csv.gz", "columns": ["t"], "rateHz": 30},
        },
        "scaleReference": {
            "meterCloseUp": {"captured": False},
            "ext": {"method": "arkit", "obliqueCloseUp": {"captured": False}, "doorViews": []},
        },
        "files": files
        or [
            {"path": "keyframes/k1.jpg", "bytes": 4, "sha256": sha256_hex(b"jpeg"), "role": "keyframe"},
        ],
    }
    if taps is not None:
        packet["taps"] = taps
    else:
        packet["taps"] = [
            {
                "id": "t1",
                "time": 0,
                "label": "reference_point",
                "keyframe": "k1",
                "pixel": pixel,
                "rayOrigin": [float(v) for v in origin],
                "rayDirection": [float(v) for v in direction],
            }
        ]
    if parent:
        packet["parentPacketId"] = parent
    return packet, frame


def _codes(response):
    return [(item["code"], item["pointer"]) for item in response.json()["errors"]]


def test_finalize_schema_and_tier_rule(client):
    capture_id = _create(client, "packet-schema", tier="arkit")
    packet, _frame = _arkit("packet-schema")
    packet["session"]["tier"] = "nope"
    response, _raw = _finalize(client, capture_id, packet)
    assert response.status_code == 422
    assert ("schema", "/session/tier") in _codes(response)
    packet, _frame = _arkit("packet-schema", epochs=[])
    response, _raw = _finalize(client, capture_id, packet)
    assert ("tier_rule", "/session/tier") in _codes(response)


def test_finalize_hash_and_size(client):
    packet_id = "packet-hash"
    capture_id = _create(client, packet_id)
    blobs = [(f"stills/s{i}.jpg", f"img-{i}".encode()) for i in range(4)]
    for index, (path, data) in enumerate(blobs):
        _put_commit(client, capture_id, path, data, meta={"still": f"s{index + 1}"})
    packet = photo_packet(packet_id, blobs)
    packet["files"][3]["sha256"] = "b" * 64
    response, _raw = _finalize(client, capture_id, packet)
    assert ("sha256_mismatch", "/files/3/sha256") in _codes(response)
    packet = photo_packet(packet_id, blobs)
    packet["files"][3]["bytes"] = blobs[3][1].__len__() + 4
    response, _raw = _finalize(client, capture_id, packet)
    assert ("file_size_mismatch", "/files/3/bytes") in _codes(response)


def test_tap_replay_and_depth_and_parent_and_header(client):
    capture_id = _create(client, "packet-tap", tier="arkit")
    packet, _frame = _arkit("packet-tap")
    packet["taps"][0]["pixel"] = [10.0, 5.0]
    response, _raw = _finalize(client, capture_id, packet)
    assert response.status_code == 202, response.text
    assert ("tap_replay", "/taps/0") in _failed_events(client, capture_id)
    assert client.get(f"/v1/captures/{capture_id}").json()["status"] == "failed"
    capture_id = _create(client, "packet-parent", tier="arkit")
    packet, _frame = _arkit("packet-parent", parent="parent-packet")
    response, _raw = _finalize(client, capture_id, packet)
    assert ("parent_unexpected", "/parentPacketId") in _codes(response)
    packet, _frame = _arkit("packet-parent")
    raw = json.dumps(packet).encode()
    bad = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={"Idempotency-Key": "packet-parent", "Content-Type": "application/json", "X-Packet-Sha256": "c" * 64},
    )
    assert ("packet_sha_mismatch", "/headers/x-packet-sha256") in _codes(bad)
    depth = b"0123456789ab"  # 12 bytes, not 2*2*4
    capture_id = _create(client, "packet-depth", tier="arkit_lidar")
    _put_commit(client, capture_id, "depth/k1.bin", depth, role="depth")
    packet, _frame = _arkit(
        "packet-depth",
        tier="arkit_lidar",
        depth_bytes=len(depth),
        files=[{"path": "depth/k1.bin", "bytes": len(depth), "sha256": sha256_hex(depth), "role": "depth"}],
    )
    response, _raw = _finalize(client, capture_id, packet)
    assert response.status_code == 202, response.text
    failed = _failed_events(client, capture_id)
    assert any(code == "depth_size" and str(pointer).endswith("/bytes") for code, pointer in failed)


def _failed_events(client, capture_id: str) -> list[tuple[str, str]]:
    events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    return [
        (event["data"].get("code"), event["data"].get("pointer", "")) for event in events if event["type"] == "failed"
    ]


def test_etag_forbidden_and_delete(client):
    capture_id = _create(client, "packet-etag")
    first = client.get(f"/v1/captures/{capture_id}")
    assert first.status_code == 200
    tag = first.headers["etag"]
    cached = client.get(f"/v1/captures/{capture_id}", headers={"If-None-Match": tag})
    assert cached.status_code == 304
    assert cached.content == b""
    other = TestClient(client.app)
    other.headers["Authorization"] = f"Bearer {OTHER}"
    forbidden = other.get(f"/v1/captures/{capture_id}")
    assert forbidden.status_code == 403
    assert forbidden.json()["errors"][0]["code"] == "forbidden"
    assert client.delete(f"/v1/captures/{capture_id}").status_code == 204
    assert client.delete(f"/v1/captures/{capture_id}").status_code == 204
    gone = client.get(f"/v1/captures/{capture_id}")
    assert gone.status_code == 410
    replay = client.post("/v1/captures", json=create_body("packet-etag"), headers={"Idempotency-Key": "packet-etag"})
    assert replay.status_code == 410


def test_rate_limit_delete(client):
    codes = [client.delete(f"/v1/captures/cap_missing_{i}").status_code for i in range(11)]
    assert codes[:10] == [204] * 10
    assert codes[10] == 429
    blocked = client.delete("/v1/captures/cap_missing_11")
    assert blocked.headers.get("retry-after")


def test_bad_digest_and_multipart(client):
    capture_id = _create(client, "packet-digest")
    data = b"abcdef"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", data, meta={"still": "s1"})]},
    )
    target = registered.json()["files"][0]["upload"]
    put = client.put(urlparse(target["url"]).path, content=b"abcdeg", headers=target["headers"])
    assert put.status_code == 400
    assert put.content == b"BadDigest"
    client.app_state.settings.HSS_INGEST_MAX_SINGLE_PUT_BYTES = 4
    big = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/big.jpg", b"12345", meta={"still": "big"})]},
    )
    assert big.status_code == 501
    assert big.json()["errors"][0]["code"] == "multipart_not_implemented"


def test_result_minimal_and_devices(client):
    capture_id = _create(client, "packet-result")
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200
    assert result.json()["status"] == "uploading"
    assert result.json()["viewsNeeded"] == []
    assert result.json()["memberActions"] == []
    assert result.json()["outcome"] is None
    review = client.get(f"/v1/captures/{capture_id}/review")
    assert review.status_code == 403
    assert review.json()["errors"][0]["code"] == "forbidden"
    device = client.post("/v1/devices", json={"token": "t" * 16, "platform": "ios"})
    assert device.status_code == 204
    assert client.post("/v1/devices", json={"token": "t" * 16, "platform": "ios"}).status_code == 204


def test_confirmations(client):
    capture_id = _create(client, "packet-confirm")
    _put_commit(client, capture_id, "stills/s1.jpg", b"meter", meta={"still": "s1"})
    body = {
        "fieldId": "meter_number",
        "kind": "confirmed",
        "value": "ABC123",
        "imageId": "s1",
        "clientAt": "2026-09-26T00:00:00.000Z",
    }
    created = client.post(f"/v1/captures/{capture_id}/confirmations", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["by"] == "device"
    replay = client.post(f"/v1/captures/{capture_id}/confirmations", json=body)
    assert replay.status_code == 200
    assert replay.json()["confirmationId"] == created.json()["confirmationId"]
    clash = client.post(f"/v1/captures/{capture_id}/confirmations", json=body | {"value": "ZZZ"})
    assert clash.status_code == 409
    assert clash.json()["errors"][0]["code"] == "confirmation_conflict"
    reviewer = TestClient(client.app)
    reviewer.headers["Authorization"] = f"Bearer {REVIEWER}"
    superseded = reviewer.post(
        f"/v1/captures/{capture_id}/confirmations", json=body | {"kind": "corrected", "value": "TYPED"}
    )
    assert superseded.status_code == 201, superseded.text
    assert superseded.json()["by"] == "reviewer"
    events = client.get(f"/v1/captures/{capture_id}/events")
    assert all("value" not in event["data"] for event in events.json()["events"])
    assert any(event["data"].get("by") == "device" for event in events.json()["events"])
    missing_value = client.post(
        f"/v1/captures/{capture_id}/confirmations",
        json=body | {"fieldId": "panel_brand", "value": None},
    )
    assert missing_value.status_code == 422
    assert missing_value.json()["errors"][0]["code"] == "value_required"
    unknown = client.post(
        f"/v1/captures/{capture_id}/confirmations", json=body | {"fieldId": "main_breaker_amps", "imageId": "nope"}
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == "image_unknown"
    client.app_state.state.set_status(capture_id, "complete")
    closed = client.post(f"/v1/captures/{capture_id}/confirmations", json=body | {"fieldId": "panel_brand"})
    assert closed.status_code == 409
    assert closed.json()["errors"][0]["code"] == "capture_closed"


def test_jwt_mode_local_rsa(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    settings = Settings(
        HSS_AUTH_MODE="jwt",
        HSS_AUTH_JWKS_URL="https://idp.test/jwks",
        HSS_AUTH_ISSUER="https://idp.test",
        HSS_AUTH_AUDIENCE="capture-api",
        VISION_EXTERNAL_CALLS="on",
    )

    class _Signing:
        def __init__(self, material: bytes) -> None:
            self.key = material

    class _Jwks:
        def __init__(self, _url: str) -> None:
            self.url = _url

        def get_signing_key_from_jwt(self, _token: str) -> _Signing:
            return _Signing(public_pem)

    monkeypatch.setattr("server.api.auth.PyJWKClient", _Jwks)
    device = jwt.encode(
        {"sub": "device-9", "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    principal = authenticate(f"Bearer {device}", settings, {})
    assert principal.label == "device-9"
    assert principal.reviewer is False
    reviewer_token = jwt.encode(
        {"sub": "reviewer-1", "role": "reviewer", "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    assert authenticate(f"Bearer {reviewer_token}", settings, {}).reviewer is True
    forged = jwt.encode(
        {"sub": "device-9", "aud": "capture-api", "iss": "https://idp.test"},
        other_pem,
        algorithm="RS256",
    )
    with pytest.raises(ApiFailure) as caught:
        authenticate(f"Bearer {forged}", settings, {})
    assert caught.value.status == 401


def test_confirmation_hook_passes_actor(monkeypatch):
    captured = {}

    def fake(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)

    import server.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "on_confirmation", fake)
    confirmed = ConfirmationRequest(
        fieldId="meter_number",
        kind="confirmed",
        value="ABC",
        imageId="s1",
        clientAt="2026-09-26T00:00:00.000Z",
    )
    OrchestratorHooks().on_confirmation("cap", confirmed, "cfm", by="device")
    assert captured["args"][:3] == ("cap", confirmed, "cfm")
    assert captured["by"] == "device"


def test_amendments(client):
    packet_id = "packet-amend"
    capture_id = _create(client, packet_id)
    processing = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": "amd-0001", "answers": ["v1"]},
        headers={"Idempotency-Key": "amd-0001"},
    )
    assert processing.status_code == 409
    assert processing.json()["errors"][0]["code"] == "invalid_state"
    deps = client.app_state
    deps.state.set_status(capture_id, "needs_views")
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(
        result_key,
        json.dumps(
            {
                "viewsNeeded": [{"id": "v1"}],
                "memberActions": ["retake_meter_closeups"],
                "outcome": {"viewsNeeded": [{"id": "v1"}]},
            }
        ).encode(),
        "application/json",
    )
    deps.state.put_run(
        RunRecord(
            run_id="run_parent",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={"result/result.json": result_key},
        )
    )
    opened = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": "amd-0001", "answers": ["v1"]},
        headers={"Idempotency-Key": "amd-0001"},
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["pathPrefix"] == "amendments/amd-0001/"
    again = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": "amd-0002", "answers": ["v1"]},
        headers={"Idempotency-Key": "amd-0002"},
    )
    assert again.status_code == 409
    assert again.json()["errors"][0]["code"] == "amendment_open"
    blob = b"extra-still"
    path = "amendments/amd-0001/stills/s1.jpg"
    _put_commit(client, capture_id, path, blob, meta={"still": "s9"})
    amendment = photo_packet("packet-amend-child", [("stills/s1.jpg", blob)])
    amendment["parentPacketId"] = "other-packet"
    raw = json.dumps(amendment).encode()
    mismatch = client.post(
        f"/v1/captures/{capture_id}/amendments/amd-0001/finalize",
        content=raw,
        headers={"Content-Type": "application/json", "X-Packet-Sha256": sha256_hex(raw)},
    )
    assert ("parent_mismatch", "/parentPacketId") in _codes(mismatch)
    amendment["parentPacketId"] = packet_id
    raw = json.dumps(amendment).encode()
    done = client.post(
        f"/v1/captures/{capture_id}/amendments/amd-0001/finalize",
        content=raw,
        headers={"Content-Type": "application/json", "X-Packet-Sha256": sha256_hex(raw)},
    )
    assert done.status_code == 202, done.text
    assert done.json()["status"] == "processing"


def test_events_timing(client):
    capture_id = _create(client, "packet-events")
    app = client.app

    async def empty_poll():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            started = time.perf_counter()
            response = await http.get(
                f"/v1/captures/{capture_id}/events",
                params={"after": 0, "wait": 2},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            return response, time.perf_counter() - started

    response, elapsed = asyncio.run(empty_poll())
    assert response.status_code == 200, response.text
    assert response.json()["events"] == []
    assert response.json()["next"] == 0
    assert 1.5 <= elapsed <= 4.0

    async def woken():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            task = asyncio.create_task(
                http.get(
                    f"/v1/captures/{capture_id}/events",
                    params={"after": 0, "wait": 5},
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
            )
            await asyncio.sleep(0.2)
            client.app_state.state.append_event(
                capture_id,
                "failed",
                {"code": "capture_expired", "message": "window", "next": "none"},
            )
            waiter = client.app_state.wake.get(capture_id)
            if waiter is not None:
                waiter.set()
            started = time.perf_counter()
            result = await task
            return result, time.perf_counter() - started

    woken_response, delta = asyncio.run(woken())
    assert woken_response.status_code == 200, woken_response.text
    assert woken_response.json()["events"]
    assert delta < 0.6


def test_too_many_polls(client):
    capture_id = _create(client, "packet-polls")
    app = client.app

    async def burst():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            tasks = [
                asyncio.create_task(
                    http.get(
                        f"/v1/captures/{capture_id}/events",
                        params={"after": 0, "wait": 1},
                        headers={"Authorization": f"Bearer {TOKEN}"},
                    )
                )
                for _ in range(9)
            ]
            return await asyncio.gather(*tasks)

    responses = asyncio.run(burst())
    assert 429 in {item.status_code for item in responses}
    assert any(item.json()["errors"][0]["code"] == "too_many_polls" for item in responses if item.status_code == 429)


def test_logs_omit_secrets(client, capsys):
    client.get("/v1/healthz")
    text = capsys.readouterr().out
    assert "Bearer " not in text
    assert "X-Goog-Signature" not in text
    assert "serviceAddress" not in text
    assert "rawText" not in text
    lines = [json.loads(line) for line in text.splitlines() if "http.request" in line]
    assert lines
    assert "request_id" in lines[-1]
    assert "http.status" in lines[-1]
