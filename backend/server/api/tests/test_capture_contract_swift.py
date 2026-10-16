"""The wire contract the iOS capture uploader reads (`CaptureAPI.swift`).

Every request goes through `create_app()` over HTTP so middleware (bearer check,
body caps, error shaping) is on the path, and every assertion names a field the
Swift client decodes: the `CreateResponse`, `RegisterResponse`, `CommitResponse`,
`FinalizeResponse`, `EventsResponse` and result-polling keys, the
`{"errors": [{code, pointer, message}]}` error body `CaptureAPI.errorCodes`
reads, the bearer requirement, create idempotency, and the terminal statuses.
One documented exception: the create response's `eventsUrl` is not in the
production `CreateResponse` struct — the uploader builds the events URL from the
capture id — so it is pinned here as server behavior, decoded in Swift only by
the Tests-target loopback double (`LoopbackCaptureAPI.swift`).
Memory state and local volume storage only.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from server.api.tests.conftest import create_body, md5_b64, photo_packet, sha256_hex

BLOB = b"\xff\xd8\xff\xe9fakejpeg"
PATH = "stills/one.jpg"
SECOND_PATH = "stills/two.jpg"


def _anon(client) -> TestClient:
    """A client with no Authorization header, as the middleware sees an anon request."""
    return TestClient(client.app)


def _create(client, packet_id: str = "packet-0001") -> dict:
    response = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert response.status_code == 201, response.text
    return response.json()


def _register(client, capture_id: str, path: str = PATH, data: bytes = BLOB, role: str = "still") -> dict:
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": path,
                    "bytes": len(data),
                    "sha256": sha256_hex(data),
                    "md5": md5_b64(data),
                    "role": role,
                    "contentType": "image/jpeg" if role == "still" else "application/octet-stream",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _put(client, registered: dict, path: str = PATH, data: bytes = BLOB) -> None:
    target = registered["files"][0]["upload"]
    put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
    assert put.status_code == 200, put.text


def _commit(client, capture_id: str, paths: list[str], data: bytes = BLOB) -> dict:
    response = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": sha256_hex(data)} for path in paths]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _finalize(client, capture_id: str, packet: dict, packet_id: str, raw: bytes | None = None):
    body = raw if raw is not None else json.dumps(packet).encode()
    return client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=body,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": sha256_hex(body),
        },
    )


def _set_status(client, capture_id: str, status: str) -> None:
    """Drive a capture to a terminal status in the memory state, as time or the run would."""
    record = client.app_state.state.get_capture(capture_id)
    assert record is not None
    record.status = status
    client.app_state.state.put_capture(record)


# --- create -----------------------------------------------------------------


def test_create_response_keys_match_swift_create_response(client):
    body = _create(client)
    # CreateResponse decodes captureId, status, upload, finalizeBy. eventsUrl is NOT in the
    # struct: the uploader builds the events URL itself (CaptureUploader.pollEvents) and only
    # the Tests-target loopback double decodes it. Pinned here because the server does return it.
    assert set(body) >= {"captureId", "status", "upload", "eventsUrl", "finalizeBy"}
    assert body["captureId"].startswith("cap_")
    assert body["status"] == "uploading"  # not in CaptureAPI.terminalStatuses
    limits = body["upload"]
    # Limits decodes maxSinglePutBytes/urlTtlS/maxFiles/maxBatch; partBytes is extra, ignored.
    assert set(limits) >= {"maxSinglePutBytes", "urlTtlS", "maxFiles", "maxBatch"}
    assert isinstance(limits["maxSinglePutBytes"], int) and limits["maxSinglePutBytes"] > 0
    assert body["eventsUrl"] == f"/v1/captures/{body['captureId']}/events"
    assert isinstance(body["finalizeBy"], str) and body["finalizeBy"].endswith("Z")


def test_create_requires_bearer_token(client):
    response = _anon(client).post(
        "/v1/captures", json=create_body("packet-0001"), headers={"Idempotency-Key": "packet-0001"}
    )
    assert response.status_code == 401
    item = response.json()["errors"][0]
    assert item["code"] == "unauthorized"  # CaptureAPI.errorCodes reads this
    assert item["pointer"] == "/headers/authorization"
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="capture-api"'


def test_create_requires_idempotency_key(client):
    response = client.post("/v1/captures", json=create_body("packet-0001"))
    assert response.status_code == 422
    item = response.json()["errors"][0]
    assert item["code"] == "idempotency_key_missing"
    assert item["pointer"] == "/headers/idempotency-key"


def test_create_idempotency_key_must_equal_packet_id(client):
    response = client.post("/v1/captures", json=create_body("packet-0001"), headers={"Idempotency-Key": "packet-other"})
    assert response.status_code == 409
    item = response.json()["errors"][0]
    assert item["code"] == "idempotency_conflict"
    assert item["pointer"] == "/headers/idempotency-key"


def test_create_replay_with_same_body_returns_same_capture(client):
    first = _create(client, "packet-0001")
    replay = client.post("/v1/captures", json=create_body("packet-0001"), headers={"Idempotency-Key": "packet-0001"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["captureId"] == first["captureId"]
    assert replay.json()["status"] == "uploading"


def test_create_replay_with_different_body_conflicts(client):
    _create(client, "packet-0001")
    replay = client.post(
        "/v1/captures",
        json=create_body("packet-0001", tier="arkit_lidar"),
        headers={"Idempotency-Key": "packet-0001"},
    )
    assert replay.status_code == 409
    assert replay.json()["errors"][0]["code"] == "idempotency_conflict"


def test_create_rejects_other_format_versions(client):
    response = client.post(
        "/v1/captures",
        json=create_body("packet-0001", formatVersion="0.3"),
        headers={"Idempotency-Key": "packet-0001"},
    )
    assert response.status_code == 422
    item = response.json()["errors"][0]
    assert item["code"] == "format_version_unsupported"
    assert item["pointer"] == "/formatVersion"


# --- register ---------------------------------------------------------------


def test_register_response_shape_matches_swift_register_response(client):
    capture_id = _create(client)["captureId"]
    body = _register(client, capture_id)
    assert set(body) == {"files"}
    entry = body["files"][0]
    assert set(entry) >= {"path", "state", "upload"}
    assert entry["path"] == PATH
    assert entry["state"] == "pending"  # RegisteredFile.state
    target = entry["upload"]
    assert target is not None
    assert set(target) >= {"method", "url", "headers", "expiresAt"}  # UploadTarget
    assert target["method"] == "PUT"
    assert "/v1/uploads/" in target["url"]
    assert isinstance(target["headers"], dict)


def test_signed_put_needs_no_bearer_and_bad_digest_fails(client):
    capture_id = _create(client)["captureId"]
    registered = _register(client, capture_id)
    target = registered["files"][0]["upload"]
    url = urlparse(target["url"]).path

    anon = _anon(client)
    put = anon.put(url, content=BLOB, headers=target["headers"])
    assert put.status_code == 200  # the HMAC token is the only credential

    headers = dict(target["headers"])
    headers["Content-MD5"] = md5_b64(b"other bytes")
    put = anon.put(url, content=BLOB, headers=headers)
    assert put.status_code == 400
    item = put.json()["errors"][0]
    assert item["code"] == "bad_digest"  # ErrorItem.code the uploader surfaces
    assert item["pointer"] == "/headers/content-md5"


def test_register_error_codes_the_uploader_reads(client):
    capture_id = _create(client)["captureId"]
    # All three paths clear the shared RelPath shape so the route's own checks run;
    # a path like "../escape.jpg" is rejected earlier as a schema error.
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": "packet.json",
                    "bytes": 4,
                    "sha256": sha256_hex(BLOB),
                    "md5": md5_b64(BLOB),
                    "role": "still",
                    "contentType": "image/jpeg",
                },
                {
                    "path": "ok.jpg",
                    "bytes": 4,
                    "sha256": sha256_hex(BLOB),
                    "md5": "not-base64",
                    "role": "still",
                    "contentType": "image/jpeg",
                },
                {
                    "path": "type.jpg",
                    "bytes": 4,
                    "sha256": sha256_hex(BLOB),
                    "md5": md5_b64(BLOB),
                    "role": "still",
                    "contentType": "video/quicktime",
                },
            ]
        },
    )
    assert response.status_code == 422
    codes = [item["code"] for item in response.json()["errors"]]
    pointers = [item["pointer"] for item in response.json()["errors"]]
    assert "path_invalid" in codes
    assert "bad_md5" in codes
    assert "content_type_invalid" in codes
    assert all(pointer.startswith("/files/") for pointer in pointers)


def test_register_path_outside_the_relpath_shape_is_a_schema_error(client):
    """`../escape.jpg` fails the shared path contract before the route's own rule."""
    capture_id = _create(client)["captureId"]
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": "../escape.jpg",
                    "bytes": 4,
                    "sha256": sha256_hex(BLOB),
                    "md5": md5_b64(BLOB),
                    "role": "still",
                    "contentType": "image/jpeg",
                }
            ]
        },
    )
    assert response.status_code == 422
    item = response.json()["errors"][0]
    assert item["code"] == "schema"


# --- commit -----------------------------------------------------------------


def test_commit_response_keys_match_swift_commit_response(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    body = _commit(client, capture_id, [PATH])
    # CommitResponse decodes committed, notFound, mismatch.
    assert set(body) == {"committed", "notFound", "mismatch"}
    assert body["committed"] == [PATH]
    assert body["notFound"] == []
    assert body["mismatch"] == []
    # Committing again is safe: the file is already committed.
    again = _commit(client, capture_id, [PATH])
    assert again["committed"] == [PATH]


def test_commit_unregistered_path_lands_in_not_found(client):
    capture_id = _create(client)["captureId"]
    body = _commit(client, capture_id, [PATH])
    assert body["committed"] == []
    assert body["notFound"] == [PATH]
    assert body["mismatch"] == []


def test_commit_sha256_conflict(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    other = b"different bytes"
    response = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": PATH, "sha256": sha256_hex(other)}]},
    )
    assert response.status_code == 409
    item = response.json()["errors"][0]
    assert item["code"] == "sha256_conflict"
    assert item["pointer"] == "/files/0/sha256"


# --- finalize ---------------------------------------------------------------


def test_finalize_complete_capture_returns_processing_shape(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    response = _finalize(client, capture_id, packet, "packet-0001")
    assert response.status_code == 202, response.text
    body = response.json()
    # FinalizeResponse decodes status, missing, runId, etaS.
    assert set(body) >= {"status", "missing", "runId", "etaS"}
    assert body["status"] == "processing"  # FinalizeResponse.status literal
    assert body["missing"] == []
    assert isinstance(body["runId"], str) and body["runId"]
    assert isinstance(body["etaS"], int) and body["etaS"] >= 0


def test_finalize_reports_missing_files_as_awaiting_files(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    packet = photo_packet("packet-0001", [(PATH, BLOB), (SECOND_PATH, b"twobytes")])
    response = _finalize(client, capture_id, packet, "packet-0001")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "awaiting_files"
    assert body["missing"] == [SECOND_PATH]


def test_finalize_idempotency_key_must_equal_packet_id(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    response = _finalize(client, capture_id, packet, "packet-other")
    assert response.status_code == 409
    item = response.json()["errors"][0]
    assert item["code"] == "packet_id_mismatch"
    assert item["pointer"] == "/headers/idempotency-key"


def test_finalize_packet_sha_header_mismatch(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    raw = json.dumps(photo_packet("packet-0001", [(PATH, BLOB)])).encode()
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": "packet-0001",
            "Content-Type": "application/json",
            "X-Packet-Sha256": sha256_hex(b"tampered"),
        },
    )
    assert response.status_code == 422
    item = response.json()["errors"][0]
    assert item["code"] == "packet_sha_mismatch"
    assert item["pointer"] == "/headers/x-packet-sha256"


def test_finalize_same_packet_twice_is_accepted(client):
    """The uploader retries finalize; the same packet bytes must re-accept."""
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    first = _finalize(client, capture_id, packet, "packet-0001")
    assert first.status_code == 202
    second = _finalize(client, capture_id, packet, "packet-0001")
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "processing"
    assert second.json()["runId"] == first.json()["runId"]


def test_finalize_on_expired_capture_is_rejected(client):
    capture_id = _create(client)["captureId"]
    _set_status(client, capture_id, "expired")
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    response = _finalize(client, capture_id, packet, "packet-0001")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "capture_expired"


def test_finalize_on_finished_capture_without_packet_is_rejected(client):
    capture_id = _create(client)["captureId"]
    _set_status(client, capture_id, "complete")
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    response = _finalize(client, capture_id, packet, "packet-0001")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "invalid_state"


# --- events -----------------------------------------------------------------


def test_events_shape_and_cursor_match_swift_events_response(client):
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])

    response = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    # EventsResponse decodes status, next, events.
    assert set(body) == {"status", "next", "events"}
    assert body["status"] == "uploading"  # not terminal yet
    event = body["events"][0]
    # Event decodes seq, type, at, data.
    assert set(event) >= {"seq", "type", "at", "data"}
    assert event["seq"] == 1
    assert event["type"] == "files_committed"
    assert body["next"] == event["seq"]

    # Polling from the cursor returns no events and a cursor that does not move.
    response = client.get(f"/v1/captures/{capture_id}/events", params={"after": body["next"]})
    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["next"] == body["next"]


def test_events_reject_negative_after(client):
    capture_id = _create(client)["captureId"]
    response = client.get(f"/v1/captures/{capture_id}/events", params={"after": -1})
    assert response.status_code == 422
    item = response.json()["errors"][0]
    assert item["code"] == "schema"
    assert item["pointer"] == "/query/after"


def test_events_requires_bearer_token(client):
    capture_id = _create(client)["captureId"]
    response = _anon(client).get(f"/v1/captures/{capture_id}/events")
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthorized"


# --- result polling ---------------------------------------------------------


def test_result_polling_before_the_result_exists(client):
    capture_id = _create(client)["captureId"]
    response = client.get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 200
    body = response.json()
    # The poller reads runId, status, viewsNeeded, memberActions, outcome.
    assert set(body) >= {"runId", "status", "viewsNeeded", "memberActions", "outcome"}
    assert body["status"] == "uploading"
    assert body["viewsNeeded"] == []
    assert body["memberActions"] == []
    assert body["outcome"] is None


def test_failed_capture_polls_terminal_without_an_outcome(client):
    """`failed` is in CaptureAPI.terminalStatuses and statusesWithoutOutcome."""
    capture_id = _create(client)["captureId"]
    _set_status(client, capture_id, "failed")

    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "failed"
    assert body["outcome"] is None
    assert body["viewsNeeded"] == []

    events = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    assert events["status"] == "failed"  # EventsResponse.status carries the terminal status


def test_result_requires_bearer_token(client):
    capture_id = _create(client)["captureId"]
    response = _anon(client).get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthorized"


# --- full wire path ---------------------------------------------------------


def test_full_lifecycle_wire_shapes(client):
    """Create, register, PUT, commit, finalize, poll — the uploader's whole walk."""
    anon = _anon(client)
    created = client.post("/v1/captures", json=create_body("packet-0001"), headers={"Idempotency-Key": "packet-0001"})
    assert created.status_code == 201
    capture_id = created.json()["captureId"]

    registered = _register(client, capture_id)
    target = registered["files"][0]["upload"]
    assert anon.put(urlparse(target["url"]).path, content=BLOB, headers=target["headers"]).status_code == 200
    assert _commit(client, capture_id, [PATH])["committed"] == [PATH]

    finalized = _finalize(client, capture_id, photo_packet("packet-0001", [(PATH, BLOB)]), "packet-0001")
    assert finalized.status_code == 202
    assert finalized.json()["status"] == "processing"

    events = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    assert events["status"] == "processing"
    # The commit emits files_committed; a started run appends stage events after it.
    assert "files_committed" in [event["type"] for event in events["events"]]
    for event in events["events"]:
        assert set(event) >= {"seq", "type", "at", "data"}

    result = client.get(f"/v1/captures/{capture_id}/result").json()
    assert result["status"] == "processing"
    assert result["outcome"] is None
