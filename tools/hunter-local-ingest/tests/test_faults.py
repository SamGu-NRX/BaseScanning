"""Fault-oracle tests: new proxy kinds, their gates, and wire behavior.

These prove the fault proxy actually applies each new fault (truncated
response framing, expired signed-URL answer, canned unknown-capture
answer, retry_finalize event injection) and that the after/recover_after
gates release it — the wire behavior the self-check fault rounds and the
mutation controls are built on in later milestones.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

from hunter_local_ingest.faultproxy import (
    _inject_retry_finalize,
    _truncate_response_payload,
    serve,
)
from hunter_local_ingest.selfcheck import _FakeState, build_fake_handler


@pytest.fixture()
def fake_origin():
    """The self-check plumbing fake on a loopback port."""
    holder = {"base": ""}
    state = _FakeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_fake_handler(holder))
    server.state = state  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    holder["base"] = f"http://{host}:{port}"
    yield holder["base"], state
    server.shutdown()


def _open(url: str) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlparse(url)
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)


def _create_capture(url: str) -> str:
    conn = _open(url)
    conn.request(
        "POST", "/captures", body=b"{}", headers={"Content-Type": "application/json"}
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    assert resp.status == 201
    return payload["captureId"]


def _register(origin_url: str, cap_id: str, paths: list[str]) -> None:
    body = json.dumps(
        {
            "files": [
                {"path": p, "sha256": "0" * 64, "bytes": 32, "role": "other"}
                for p in paths
            ]
        }
    ).encode("utf-8")
    conn = _open(origin_url)
    conn.request(
        "POST",
        f"/captures/{cap_id}/files",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200


# -- truncated response framing ---------------------------------------------


def test_truncate_response_framing_is_surfaced_as_a_read_failure(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "truncate_response", "bytes": 8}})
    try:
        cap_id = _create_capture(origin)
        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            f"/captures/{cap_id}/files:commit",
            body=json.dumps({"files": []}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        with pytest.raises((http.client.IncompleteRead, ConnectionError, OSError)):
            resp.read()
        conn.close()
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()


def test_truncate_response_payload_declares_more_than_it_writes():
    written, declared = _truncate_response_payload(b"x" * 100, {"bytes": 8})
    assert len(written) == 92
    assert declared == 100

    # an empty upstream payload still produces a framing mismatch
    empty_written, empty_declared = _truncate_response_payload(b"", {"bytes": 8})
    assert empty_written == b""
    assert empty_declared == 8


# -- expired signed-URL answer -----------------------------------------------


def test_expired_url_fault_then_recovery(fake_origin):
    origin, state = fake_origin
    proxy = serve(origin, {"upload": {"kind": "expired_url", "recover_after": 1}})
    try:
        cap_id = _create_capture(origin)
        _register(origin, cap_id, ["synthetic/selfcheck0.syn"])

        conn = _open(proxy.base_url)
        conn.request("PUT", f"/storage/{cap_id}/0", body=b"x" * 32)
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        assert resp.status == 403
        assert payload["errors"][0]["code"] == "harness_url_expired"

        # recovery: the second PUT passes through to the origin and lands
        conn = _open(proxy.base_url)
        conn.request("PUT", f"/storage/{cap_id}/0", body=b"x" * 32)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 200
        assert state.storage[f"{cap_id}/synthetic/selfcheck0.syn"] == b"x" * 32
        assert proxy.injected_counts.get("upload") == 1
    finally:
        proxy.stop()


# -- canned unknown-capture answer -------------------------------------------


def test_capture_not_found_canned_answer_uses_the_published_code(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "capture_not_found"}})
    try:
        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            "/captures/cap-0001/files:commit",
            body=json.dumps({"files": []}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        assert resp.status == 404
        assert payload["errors"][0]["code"] == "capture_not_found"
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()


def test_capture_not_found_can_target_any_capture_step(fake_origin):
    origin, _state = fake_origin
    faults = {
        "create": {"kind": "capture_not_found"},
        "register": {"kind": "capture_not_found"},
        "finalize": {"kind": "capture_not_found"},
    }
    proxy = serve(origin, faults)
    try:
        conn = _open(proxy.base_url)
        conn.request(
            "POST", "/captures", body=b"{}", headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404

        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            "/captures/cap-0001/files",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404
    finally:
        proxy.stop()


# -- retry_finalize event injection ------------------------------------------


def test_retry_finalize_injection_then_recovery(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"events": {"kind": "retry_finalize", "recover_after": 1}})
    try:
        cap_id = _create_capture(origin)

        conn = _open(proxy.base_url)
        conn.request("GET", f"/captures/{cap_id}/events")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        injected = [e for e in body["events"] if e["type"] == "failed"]
        assert len(injected) == 1
        assert injected[0]["data"] == {"next": "retry_finalize"}
        assert body["next"] >= injected[0]["seq"]

        # recovery: the next poll is the clean upstream answer
        conn = _open(proxy.base_url)
        conn.request("GET", f"/captures/{cap_id}/events")
        resp = conn.getresponse()
        clean = json.loads(resp.read())
        conn.close()
        assert all(e["type"] != "failed" for e in clean["events"])
        assert proxy.injected_counts.get("events") == 1
    finally:
        proxy.stop()


def test_inject_retry_finalize_ignores_bodies_that_carry_no_events():
    assert _inject_retry_finalize(b'{"status": "ok"}') is None
    assert _inject_retry_finalize(b"not json at all") is None


# -- gating ------------------------------------------------------------------


def test_after_gate_releases_early_hits(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "capture_not_found", "after": 2}})
    try:
        cap_id = _create_capture(origin)
        statuses = []
        for _ in range(3):
            conn = _open(proxy.base_url)
            conn.request(
                "POST",
                f"/captures/{cap_id}/files:commit",
                body=json.dumps({"files": []}),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()
            statuses.append(resp.status)
        assert statuses == [200, 200, 404]
        assert proxy.hit_counts["commit"] == 3
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()
