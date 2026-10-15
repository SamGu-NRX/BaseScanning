"""Extra ingest cases so the suite covers the brief's error catalogue."""

from __future__ import annotations

import asyncio
import gzip
import json
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from server.api.routes.files import _path_ok
from server.api.tests.conftest import OTHER, REVIEWER, TOKEN, create_body, md5_b64, photo_packet, sha256_hex
from server.api.tests.test_ingest import _create, _file, _finalize, _put_commit
from server.contracts.jobs import RunRecord, StageRecord, run_id_for_capture


def test_content_type_invalid(client):
    capture_id = _create(client, "packet-ctype")
    body = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    body["contentType"] = "image/png"
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "content_type_invalid"


def test_bad_md5(client):
    capture_id = _create(client, "packet-md5")
    body = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    body["md5"] = "abc"
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.json()["errors"][0]["code"] == "bad_md5"


def test_path_invalid(client):
    capture_id = _create(client, "packet-path")
    body = {
        "path": "packet.json",
        "bytes": 3,
        "sha256": sha256_hex(b"abc"),
        "md5": md5_b64(b"abc"),
        "role": "other",
        "contentType": "application/json",
    }
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.json()["errors"][0]["code"] == "path_invalid"


@pytest.mark.parametrize("path", [".", "..", "./x.jpg", "a/./b.jpg", "stills/../s1.jpg"])
def test_dot_segments_rejected(path):
    assert _path_ok(path) is False
    assert _path_ok("stills/s1.jpg") is True


@pytest.mark.parametrize("path", [".", "..", "./stills/s1.jpg", "a/./b.jpg", "a/.."])
def test_dot_segments_are_path_invalid(client, path):
    capture_id = _create(client, "packet-dot-" + sha256_hex(path.encode())[:12])
    body = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    body["path"] = path
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["code"] == "schema"


def test_parent_segment_stays_schema(client):
    capture_id = _create(client, "packet-dot-parent")
    body = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    body["path"] = "stills/../s1.jpg"
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["code"] == "schema"


def test_path_duplicate(client):
    capture_id = _create(client, "packet-dup")
    item = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item, item]})
    assert response.json()["errors"][0]["code"] == "path_duplicate"


def test_meta_mismatch(client):
    capture_id = _create(client, "packet-meta")
    body = _file("stills/s1.jpg", b"abc", meta={"still": "s1"})
    body["meta"] = {
        "keyframe": {
            "id": "k1",
            "img": "stills/s1.jpg",
            "w": 2,
            "h": 2,
            "intrinsics": [1, 1, 1, 1],
            "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "timestamp": 0,
            "tracking": "normal",
            "epoch": "e1",
            "reason": "auto",
        }
    }
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
    assert response.json()["errors"][0]["code"] == "meta_mismatch"


def test_batch_too_small(client):
    capture_id = _create(client, "packet-batch")
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": []})
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "batch_too_large"


def test_too_many_open_captures(client):
    client.app_state.settings.HSS_INGEST_MAX_OPEN_CAPTURES = 1
    _create(client, "packet-open-1")
    response = client.post(
        "/v1/captures", json=create_body("packet-open-2"), headers={"Idempotency-Key": "packet-open-2"}
    )
    assert response.status_code == 429
    assert response.json()["errors"][0]["code"] == "too_many_open_captures"
    assert response.headers.get("retry-after")


@pytest.mark.parametrize(("tier", "flow"), [("photo_only", "photo"), ("arkit", "guided"), ("arkit_lidar", "guided")])
def test_flow_default(client, tier, flow):
    capture_id = _create(client, f"packet-flow-{tier}", tier=tier)
    assert client.get(f"/v1/captures/{capture_id}").json()["flow"] == flow


def test_chunked_body_over_cap_is_413(client):
    sent = {"n": 0}

    async def receive():
        sent["n"] += 1
        if sent["n"] == 1:
            return {"type": "http.request", "body": b"{" * (1024 * 1024 + 8), "more_body": False}
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/captures",
        "raw_path": b"/v1/captures",
        "query_string": b"",
        "headers": [
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    asyncio.run(client.app(scope, receive, send))
    start = next(item for item in messages if item["type"] == "http.response.start")
    assert start["status"] == 413


def test_other_principal_does_not_receive_capture_id(client):
    capture_id = _create(client, "packet-owned-by-device")
    other = TestClient(client.app)
    other.headers["Authorization"] = f"Bearer {OTHER}"
    response = other.post(
        "/v1/captures",
        json=create_body("packet-owned-by-device"),
        headers={"Idempotency-Key": "packet-owned-by-device"},
    )
    assert response.status_code == 409, response.text
    assert capture_id not in response.text
    assert response.json()["errors"][0]["code"] == "idempotency_conflict"


def test_reviewer_can_read_and_cannot_delete_or_register(client):
    capture_id = _create(client, "packet-reviewer-scope")
    reviewer = TestClient(client.app)
    reviewer.headers["Authorization"] = f"Bearer {REVIEWER}"
    assert reviewer.get(f"/v1/captures/{capture_id}").status_code == 200
    deleted = reviewer.delete(f"/v1/captures/{capture_id}")
    assert deleted.status_code == 403, deleted.text
    assert client.get(f"/v1/captures/{capture_id}").status_code == 200
    registered = reviewer.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", b"abc", meta={"still": "s1"})]},
    )
    assert registered.status_code == 403, registered.text


def test_unequal_idempotency_key(client):
    response = client.post(
        "/v1/captures", json=create_body("packet-unequal"), headers={"Idempotency-Key": "not-the-packet"}
    )
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "idempotency_conflict"


def test_packet_id_mismatch_header(client):
    capture_id = _create(client, "packet-id-header")
    blob = b"abc"
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})
    packet = photo_packet("packet-id-header", [("stills/s1.jpg", blob)])
    raw = json.dumps(packet).encode()
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={"Idempotency-Key": "something-else", "Content-Type": "application/json"},
    )
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "packet_id_mismatch"


def _early_then_flaky_commit(client, monkeypatch):
    from server.api.errors import fail
    from server.api.promotion import promote as real_promote

    packet_id = "packet-promote-retry"
    capture_id = _create(client, packet_id)
    blob = b"jpeg"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s1.jpg", blob, meta={"still": "s1"})]},
    )
    assert registered.status_code == 200, registered.text
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob)])
    early, _raw = _finalize(client, capture_id, packet)
    assert early.status_code == 202, early.text
    assert early.json()["status"] == "awaiting_files"
    target = registered.json()["files"][0]["upload"]
    put = client.put(urlparse(target["url"]).path, content=blob, headers=target["headers"])
    assert put.status_code == 200, put.text
    calls = {"n": 0}

    def flaky(deps, record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fail(503, "storage_unavailable", "/", "storage copy failed")
        return real_promote(deps, record)

    monkeypatch.setattr("server.api.finalize.promote", flaky)
    committed = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": "stills/s1.jpg", "sha256": sha256_hex(blob)}]},
    )
    assert committed.status_code == 200, committed.text
    failed = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    storage_failed = [
        event for event in failed if event["type"] == "failed" and event["data"].get("code") == "storage_unavailable"
    ]
    assert storage_failed
    assert storage_failed[0]["data"]["next"] == "retry_finalize"
    assert client.get(f"/v1/captures/{capture_id}").json()["status"] == "awaiting_files"
    return capture_id, packet


def test_promotion_failure_retries_on_second_finalize(client, monkeypatch):
    capture_id, packet = _early_then_flaky_commit(client, monkeypatch)
    second, _raw = _finalize(client, capture_id, packet)
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "processing"


def test_repeat_commit_retries_promotion(client, monkeypatch):
    capture_id, _packet = _early_then_flaky_commit(client, monkeypatch)
    again = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": "stills/s1.jpg", "sha256": sha256_hex(b"jpeg")}]},
    )
    assert again.status_code == 200, again.text
    assert client.get(f"/v1/captures/{capture_id}").json()["status"] == "processing"


def test_gzip_finalize(client):
    packet_id = "packet-gzip"
    capture_id = _create(client, packet_id)
    blob = b"gzip-me"
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob)])
    raw = json.dumps(packet).encode()
    compressed = gzip.compress(raw)
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=compressed,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Packet-Sha256": sha256_hex(raw),
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["runId"] == run_id_for_capture(capture_id, raw)


def test_not_in_manifest(client):
    packet_id = "packet-manifest"
    capture_id = _create(client, packet_id)
    blob = b"listed"
    _put_commit(client, capture_id, "stills/s1.jpg", blob, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", blob), ("stills/s2.jpg", b"later")])
    response, _raw = _finalize(client, capture_id, packet)
    assert response.status_code == 202
    assert response.json()["status"] == "awaiting_files"
    extra = _file("stills/nope.jpg", b"abc", meta={"still": "nope"})
    rejected = client.post(f"/v1/captures/{capture_id}/files", json={"files": [extra]})
    assert rejected.status_code == 409
    assert rejected.json()["errors"][0]["code"] == "not_in_manifest"


def test_other_principal_events(client):
    capture_id = _create(client, "packet-other-events")
    other = TestClient(client.app)
    other.headers["Authorization"] = f"Bearer {OTHER}"
    response = other.get(f"/v1/captures/{capture_id}/events")
    assert response.status_code == 403


def test_confirmations_list_includes_value(client):
    capture_id = _create(client, "packet-confirm-list")
    _put_commit(client, capture_id, "stills/s1.jpg", b"meter", meta={"still": "s1"})
    body = {
        "fieldId": "meter_number",
        "kind": "confirmed",
        "value": "ABC123",
        "imageId": "s1",
        "clientAt": "2026-09-26T00:00:00.000Z",
    }
    assert client.post(f"/v1/captures/{capture_id}/confirmations", json=body).status_code == 201
    listed = client.get(f"/v1/captures/{capture_id}/confirmations")
    assert listed.json()["confirmations"][0]["value"] == "ABC123"


def test_value_pattern(client):
    capture_id = _create(client, "packet-value-pattern")
    _put_commit(client, capture_id, "stills/s1.jpg", b"meter", meta={"still": "s1"})
    body = {
        "fieldId": "meter_number",
        "kind": "confirmed",
        "value": "bad value!",
        "imageId": "s1",
        "clientAt": "2026-09-26T00:00:00.000Z",
    }
    response = client.post(f"/v1/captures/{capture_id}/confirmations", json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["pointer"] == "/value"


def test_answers_unknown(client):
    capture_id = _create(client, "packet-answers")
    client.app_state.state.set_status(capture_id, "needs_views")
    response = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": "amd-0001", "answers": ["not-a-view"]},
        headers={"Idempotency-Key": "amd-0001"},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "answers_unknown"
    assert response.json()["errors"][0]["pointer"] == "/answers/0"


def test_files_complete_is_501(client):
    capture_id = _create(client, "packet-complete")
    response = client.post(
        f"/v1/captures/{capture_id}/files:complete",
        json={"path": "stills/s1.jpg", "uploadId": "u", "parts": [{"n": 1, "etag": "abc"}]},
    )
    assert response.status_code == 501


def test_review_html_and_schema_drift(client):
    capture_id = _create(client, "packet-review")
    deps = client.app_state
    html_key = f"captures/{capture_id}/result/review.html"
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(html_key, b"<p>decision: reject</p>", "text/html")
    deps.storage.put_bytes(
        result_key, json.dumps({"status": "complete", "extraField": True}).encode(), "application/json"
    )
    deps.state.put_run(
        RunRecord(
            run_id="run_review",
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={"result/result.json": result_key, "result/review.html": html_key},
        )
    )
    deps.state.upsert_stage(
        StageRecord(
            run_id="run_review",
            stage="result",
            status="done",
            attempt=1,
            heartbeat="2026-09-26T00:00:00.000Z",
        )
    )
    hidden = client.get(f"/v1/captures/{capture_id}/review")
    assert hidden.status_code == 403
    assert b"reject" not in hidden.content
    reviewer = TestClient(client.app)
    reviewer.headers["Authorization"] = f"Bearer {REVIEWER}"
    review = reviewer.get(f"/v1/captures/{capture_id}/review")
    assert review.status_code == 200
    assert b"decision: reject" in review.content
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200
    assert result.json()["extraField"] is True
    assert result.json()["status"] == "uploading"


def test_health_version(client):
    anon = TestClient(client.app)
    body = anon.get("/v1/healthz").json()
    assert body["version"] == "0123456789ab"
    assert body["state"] == "memory"


def test_import_without_modal():
    import server.api as api

    assert callable(api.create_app)


def test_delete_forbidden(client):
    capture_id = _create(client, "packet-delete-forbidden")
    other = TestClient(client.app)
    other.headers["Authorization"] = f"Bearer {OTHER}"
    response = other.delete(f"/v1/captures/{capture_id}")
    assert response.status_code == 403
    _ = TOKEN


def test_late_register_must_match_accepted_packet(client):
    packet_id = "packet-late-hash"
    capture_id = _create(client, packet_id)
    first, second = b"one", b"two"
    _put_commit(client, capture_id, "stills/s1.jpg", first, meta={"still": "s1"})
    packet = photo_packet(packet_id, [("stills/s1.jpg", first), ("stills/s2.jpg", second)])
    early, _raw = _finalize(client, capture_id, packet)
    assert early.status_code == 202, early.text
    assert early.json()["status"] == "awaiting_files"
    wrong = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s2.jpg", b"nope", meta={"still": "s2"})]},
    )
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["errors"][0]["code"] == "sha256_mismatch"
    assert client.app_state.state.get_capture(capture_id).status == "awaiting_files"
    matched = client.post(
        f"/v1/captures/{capture_id}/files",
        json={"files": [_file("stills/s2.jpg", second, meta={"still": "s2"})]},
    )
    assert matched.status_code == 200, matched.text


def test_replay_without_record_hides_capture_id(client):
    packet_id = "packet-missing-record"
    capture_id = _create(client, packet_id)
    client.app_state.state._captures.pop(capture_id)
    response = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "idempotency_conflict"
    assert capture_id not in response.text


def test_concurrent_creates_share_one_capture(client):
    app = client.app
    packet_id = "packet-race-create"
    body = create_body(packet_id)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

            async def once():
                return await http.post(
                    "/v1/captures",
                    json=body,
                    headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": packet_id},
                )

            return await asyncio.gather(once(), once())

    first, second = asyncio.run(scenario())
    assert {first.status_code, second.status_code} == {200, 201}
    assert first.json()["captureId"] == second.json()["captureId"]
    got = client.get(f"/v1/captures/{first.json()['captureId']}")
    assert got.status_code == 200, got.text


def test_delete_waits_for_capture_lock(client):
    capture_id = _create(client, "packet-del-lock")
    deps = client.app_state
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with deps.locks.acquire(capture_id):
            started.set()
            await release.wait()

    async def scenario():
        holder = asyncio.create_task(hold())
        await started.wait()
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            pending = asyncio.create_task(
                http.delete(
                    f"/v1/captures/{capture_id}",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
            )
            await asyncio.sleep(0.05)
            assert not pending.done()
            release.set()
            response = await pending
        await holder
        return response

    response = asyncio.run(scenario())
    assert response.status_code == 204, response.text
