"""Client-level contract tests for the direct HTTP adapter (interface 1.1.0).

The stub origin here is synthetic loopback plumbing that RECORDS every
request it receives and replays a scripted list of replies, so each test
can assert exactly what the client put on the wire against the published
contract (recon art_ZtPTds0y §2: CaptureAPI.swift, CaptureUploader.swift,
ScopedCaptureHTTP.swift @ alpha 263827ef).

Scope: client transport behavior only. Scenario-level oracle decisions
live with the scenarios (and their own conforming stubs).
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hunter_local_ingest.http_client import DirectHttpClient

BASE = "http://127.0.0.1"  # client-facing base; the port is filled in
BASE_HOST = "127.0.0.1"  # bare loopback host for the stub bind


class _NoReverseDnsServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the getfqdn reverse lookup.

    socketserver's server_bind resolves the bound address to a canonical
    name; in sandboxes without a reverse zone that raises gaierror for the
    numeric loopback address. Recording the literal host is enough here.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class _Stub:
    """Scripted replies in order + a record of every request received."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.script: list[tuple[int, dict[str, str], bytes]] = []
        self.lock = threading.Lock()

    def push(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.script.append((status, headers, body))

    def pop(self) -> tuple[int, dict[str, str], bytes]:
        with self.lock:
            if self.script:
                return self.script.pop(0)
        return 500, {}, b'{"errors": [{"code": "stub_script_empty"}]}'

    def record(self, request: dict) -> None:
        with self.lock:
            self.seen.append(request)


def _build_stub_handler(stub: _Stub):
    class StubHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # silence
            pass

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _serve(self, method: str) -> None:
            body = self._read_body()
            stub.record(
                {
                    "method": method,
                    "path": self.path,
                    "headers": {k: v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            status, headers, payload = stub.pop()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._serve("PUT")

    return StubHandler


@pytest.fixture()
def stub_server():
    """A loopback stub origin: yields (base_url, stub)."""
    stub = _Stub()
    server = _NoReverseDnsServer((BASE_HOST, 0), _build_stub_handler(stub))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"{BASE}:{server.server_address[1]}", stub
    server.shutdown()
    thread.join(timeout=5)


def _fast_client(base_url: str, **kwargs) -> DirectHttpClient:
    """A client with no sleeping: zero backoff base, zero advised-delay cap.

    The REPORTED retry_after_s stays the published 120 s-capped parse; only
    the sleep is collapsed for the suite.
    """
    kwargs.setdefault("retry_backoff_s", 0.0)
    kwargs.setdefault("retry_delay_cap_s", 0.0)
    kwargs.setdefault("max_transient_retries", 2)
    kwargs.setdefault("timeout_s", 5.0)
    return DirectHttpClient(base_url, **kwargs)


def _register_target(stub_server, path: str = "a.syn", headers: dict | None = None):
    """Register one file against a scripted target and return the target."""
    base, stub = stub_server
    put_url = f"{base}/storage/cap-1/0"
    headers_json = json.dumps(headers or {"X-Sig": "v1"}, separators=(",", ":"))
    stub.push(
        200,
        {},
        b'{"files": [{"path": "' + path.encode() + b'", "state": "awaiting_upload", '
        b'"upload": {"method": "PUT", "url": "'
        + put_url.encode()
        + b'", "headers": '
        + headers_json.encode()
        + b"}}]}",
    )
    client = _fast_client(base)
    reg = client.register("cap-1", [{"path": path, "sha256": "x"}])
    assert reg.ok
    return client, reg.context["register_targets"][path]


class TestEndpointScope:
    """Scope-checked base URL (ScopedCaptureHTTP.swift:110-155, :160-163)."""

    def test_plain_http_non_loopback_is_refused(self):
        with pytest.raises(ValueError, match="loopback"):
            DirectHttpClient("http://example.com/v1")

    def test_userinfo_is_refused(self):
        with pytest.raises(ValueError, match="userinfo"):
            DirectHttpClient("http://user:pass@127.0.0.1:8000/v1")

    def test_query_in_base_is_refused(self):
        with pytest.raises(ValueError, match="query"):
            DirectHttpClient("http://127.0.0.1:8000/v1?x=1")

    def test_loopback_http_is_accepted(self):
        client = DirectHttpClient("http://127.0.0.1:8000/v1")
        assert client._ep.port == 8000  # noqa: SLF001 — seam test

    def test_credential_must_be_b64token(self):
        with pytest.raises(ValueError, match="b64token"):
            _fast_client("http://127.0.0.1:8000/v1", credential="not a token!")


class TestCredentialScoping:
    """Bearer on the six capture-API steps, never on storage
    (ScopedCaptureHTTP.swift:56, :65-68)."""

    def test_bearer_rides_capture_api_steps(self, stub_server):
        base, stub = stub_server
        stub.push(201, {}, b'{"captureId": "cap-1", "status": "capturing"}')
        stub.push(200, {}, b'{"files": [{"path": "a", "state": "committed"}]}')
        client = _fast_client(base, credential="abc-123")
        res = client.create({"packetId": "p"}, "p")
        assert res.ok and res.context["capture_id"] == "cap-1"
        res = client.register("cap-1", [{"path": "a", "sha256": "x"}])
        assert res.ok
        assert stub.seen[0]["headers"]["Authorization"] == "Bearer abc-123"
        assert stub.seen[1]["headers"]["Authorization"] == "Bearer abc-123"

    def test_storage_put_never_carries_the_credential(self, stub_server):
        client, target = _register_target(stub_server)
        # a credentialed client reuses the same signed target: the PUT must
        # still not carry the API credential
        base, stub = stub_server
        stub.push(200, {}, b"")
        credentialed = _fast_client(base, credential="abc-123")
        res = credentialed.upload(
            "cap-1", "a.syn", b"bytes", target["url"], target["headers"]
        )
        assert res.ok
        put = next(r for r in stub.seen if r["method"] == "PUT")
        assert put["headers"].get("Authorization") is None
        assert put["headers"]["X-Sig"] == "v1"

    def test_storage_put_sends_exactly_the_signed_headers(self, stub_server):
        client, target = _register_target(
            stub_server, headers={"X-Synthetic-Signature": "sig-1"}
        )
        base, stub = stub_server
        stub.push(200, {}, b"")
        res = client.upload("cap-1", "a.syn", b"bytes", target["url"], target["headers"])
        assert res.ok
        put = next(r for r in stub.seen if r["method"] == "PUT")
        # No default Content-Type (CaptureUploader.swift:522): the client
        # adds NOTHING the server did not sign.
        assert "Content-Type" not in put["headers"]
        assert put["headers"]["X-Synthetic-Signature"] == "sig-1"

    def test_signed_storage_authorization_is_refused_not_sent(self, stub_server):
        base, stub = stub_server
        client = _fast_client(base, credential="abc-123")
        res = client.upload(
            "cap-1", "a.syn", b"bytes", f"{base}/storage/x", {"Authorization": "Bearer x"}
        )
        assert not res.ok
        assert res.error.startswith("auth_storage_authorization")
        assert stub.seen == [], "a refused request must never reach the wire"

    def test_plain_http_non_loopback_storage_url_is_refused(self, stub_server):
        _base, stub = stub_server
        client = _fast_client("http://127.0.0.1:1/v1")
        res = client.upload(
            "cap-1", "a.syn", b"bytes", "http://10.0.0.5:9000/storage/x", {}
        )
        assert not res.ok
        assert res.error.startswith("upload_url_scope")
        assert stub.seen == []


class TestTransientClasses:
    """{0, 408, 429, 5xx≠501} retry; Retry-After honored, capped, reported
    (CaptureUploader.swift:817, :45-48)."""

    def test_429_retries_and_reports_retry_after(self, stub_server):
        base, stub = stub_server
        stub.push(429, {"Retry-After": "3"}, b'{"errors": [{"code": "slow_down"}]}')
        stub.push(201, {}, b'{"captureId": "cap-1", "status": "capturing"}')
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.ok and res.http_status == 201
        assert res.retry_after_s == 3.0
        assert len(stub.seen) == 2

    def test_501_is_never_retried(self, stub_server):
        base, stub = stub_server
        stub.push(501, {}, b'{"errors": [{"code": "no"}]}')
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.ok and res.http_status == 501
        assert len(stub.seen) == 1

    def test_408_retries(self, stub_server):
        base, stub = stub_server
        stub.push(408, {}, b"")
        stub.push(201, {}, b'{"captureId": "cap-1"}')
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.ok and res.http_status == 201
        assert len(stub.seen) == 2

    def test_transport_errors_retry_with_attempt_count(self):
        # a bound-then-closed port refuses deterministically
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        dead = DirectHttpClient(
            f"http://127.0.0.1:{dead_port}/v1",
            timeout_s=1.0,
            retry_backoff_s=0.0,
            retry_delay_cap_s=0.0,
            max_transient_retries=2,
        )
        res = dead.create({"packetId": "p"}, "p")
        assert not res.ok
        assert "transport after 3 attempt(s)" in res.error

    def test_retry_after_report_is_capped_at_published_120(self, stub_server):
        base, stub = stub_server
        stub.push(429, {"Retry-After": "500"}, b"")
        stub.push(201, {}, b'{"captureId": "cap-1"}')
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.ok
        assert res.retry_after_s == 120.0

    def test_incomplete_framing_is_a_transport_error(self):
        """A body shorter than its declared Content-Length is never silently
        accepted (bounded reads, CaptureUploader.swift:13-58)."""
        # one-shot raw origin: declares 100 bytes, sends 10, closes
        gate = socket.socket()
        gate.bind(("127.0.0.1", 0))
        port = gate.getsockname()[1]
        gate.listen(1)
        head = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 10

        def serve_lie():
            conn, _ = gate.accept()
            try:
                conn.recv(65536)
                conn.sendall(head)
            finally:
                conn.close()

        threading.Thread(target=serve_lie, daemon=True).start()
        client = DirectHttpClient(
            f"http://127.0.0.1:{port}/v1",
            timeout_s=5.0,
            retry_backoff_s=0.0,
            retry_delay_cap_s=0.0,
            max_transient_retries=0,
        )
        res = client.result("cap-1")
        assert not res.ok
        # A truncated response is never silently accepted: http.client's
        # IncompleteRead (short body before close) or the framing check
        # surfaces it as a transport error, never a 2xx reply.
        assert "Incomplete" in res.error

    def test_redirect_is_returned_never_followed(self, stub_server):
        base, stub = stub_server
        stub.push(302, {"Location": f"{base}/elsewhere"}, b"")
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.ok and res.http_status == 302
        assert len(stub.seen) == 1, "a 3xx is its own reply; never followed"


class TestContextViews:
    """In-process step context the scenarios consume (never serialized)."""

    def test_create_stashes_capture_id_and_limits(self, stub_server):
        base, stub = stub_server
        stub.push(
            201,
            {},
            b'{"captureId": "cap-1", "status": "capturing",'
            b' "upload": {"maxBatch": 7, "urlTtlS": 900, "maxSinglePutBytes": 5}}',
        )
        client = _fast_client(base)
        res = client.create({"packetId": "p"}, "p")
        assert res.context["capture_id"] == "cap-1"
        assert res.context["create_limits"] == {
            "maxBatch": 7,
            "urlTtlS": 900,
            "maxSinglePutBytes": 5,
        }

    def test_register_targets_are_keyed_by_path(self, stub_server):
        base, stub = stub_server
        put_url = f"{base}/storage/cap-1/0"
        stub.push(
            200,
            {},
            b'{"files": ['
            b'{"path": "a.syn", "state": "awaiting_upload", "upload": '
            b'{"method": "PUT", "url": "'
            + put_url.encode()
            + b'", "headers": {"X-Sig": "v"}, "expiresAt": "2026-10-12T00:00:00Z"}},'
            b'{"path": "b.syn", "state": "committed"}]}',
        )
        client = _fast_client(base)
        descs = [{"path": "a.syn", "sha256": "x"}, {"path": "b.syn", "sha256": "y"}]
        res = client.register("cap-1", descs)
        targets = res.context["register_targets"]
        assert targets["a.syn"]["url"] == put_url
        assert targets["a.syn"]["expires_at"] == "2026-10-12T00:00:00Z"
        assert targets["b.syn"]["state"] == "committed"
        assert "url" not in targets["b.syn"]
        # transitional descriptor-ordered view matches by path, not position
        legacy = res.context["upload_targets"]
        assert legacy[0]["url"] == put_url
        assert legacy[1]["state"] == "committed"

    def test_commit_membership_context(self, stub_server):
        base, stub = stub_server
        stub.push(
            200,
            {},
            b'{"committed": ["a.syn"], "notFound": ["gone.syn"], "mismatch": ["b.syn"]}',
        )
        client = _fast_client(base)
        res = client.commit("cap-1", [{"path": "a.syn", "sha256": "x"}])
        assert res.context["commit_membership"] == {
            "committed": ["a.syn"],
            "notFound": ["gone.syn"],
            "mismatch": ["b.syn"],
        }

    def test_finalize_run_id_context(self, stub_server):
        base, stub = stub_server
        stub.push(202, {}, b'{"status": "processing", "runId": "run-9", "missing": []}')
        client = _fast_client(base)
        res = client.finalize("cap-1", b"{}", "pkt-1")
        assert res.context["finalize_run_id"] == "run-9"
        assert res.context["finalize_status"] == "processing"

    def test_events_view_and_request_params(self, stub_server):
        base, stub = stub_server
        stub.push(
            200,
            {},
            b'{"status": "processing", "next": 4, "events": ['
            b'{"seq": 1, "type": "state", "data": {"runId": "run-9"}},'
            b'{"seq": 2, "type": "verdict_ready"}]}',
        )
        client = _fast_client(base)
        res = client.events("cap-1", 3, 20)
        view = res.context["events_view"]
        assert view["cursor"] == 4
        assert view["status"] == "processing"
        assert view["event_run_ids"] == ["run-9"]
        assert "after=3&wait=20" in stub.seen[0]["path"]

    def test_result_view_carries_outcome_kind(self, stub_server):
        base, stub = stub_server
        stub.push(
            200,
            {},
            b'{"runId": "run-9", "status": "complete",'
            b' "outcome": {"kind": "needs_more_photos"}}',
        )
        client = _fast_client(base)
        res = client.result("cap-1")
        view = res.context["result_view"]
        assert view == {
            "run_id": "run-9",
            "status": "complete",
            "outcome_present": True,
            "outcome_kind": "needs_more_photos",
        }

    def test_result_view_null_outcome(self, stub_server):
        base, stub = stub_server
        stub.push(200, {}, b'{"runId": "run-9", "status": "failed", "outcome": null}')
        client = _fast_client(base)
        view = client.result("cap-1").context["result_view"]
        assert view["outcome_present"] is False
        assert view["outcome_kind"] is None
        assert view["status"] == "failed"

    def test_result_view_non_dict_outcome_has_no_kind(self, stub_server):
        base, stub = stub_server
        stub.push(200, {}, b'{"runId": "run-9", "status": "complete", "outcome": "x"}')
        client = _fast_client(base)
        view = client.result("cap-1").context["result_view"]
        assert view["outcome_present"] is True
        assert view["outcome_kind"] is None

    def test_error_codes_stash_on_404(self, stub_server):
        base, stub = stub_server
        stub.push(404, {}, b'{"errors": [{"code": "result_not_ready"}]}')
        client = _fast_client(base)
        res = client.result("cap-1")
        assert res.context["error_codes"] == ["result_not_ready"]

    def test_malformed_json_body_is_survived(self, stub_server):
        base, stub = stub_server
        stub.push(200, {}, b"<html>not json</html>")
        client = _fast_client(base)
        res = client.result("cap-1")
        assert res.ok
        assert res.context.get("result_view") is None
        assert res.observation.field_names == ()


class TestPublishedVocabulary:
    """Published terminal statuses and the RegisterFile descriptor set."""

    def test_terminal_statuses_are_the_published_five(self):
        from hunter_local_ingest.scenarios import TERMINAL_STATUSES

        assert TERMINAL_STATUSES == frozenset(
            {"complete", "manual_review", "failed", "expired", "needs_views"}
        )

    def test_role_vocabulary_has_no_mesh(self):
        from hunter_local_ingest.scenarios import ROLE_VOCABULARY

        assert ROLE_VOCABULARY == frozenset(
            {"keyframe", "depth", "confidence", "features", "still", "stream", "other"}
        )
        assert "mesh" not in ROLE_VOCABULARY

    def test_descriptors_carry_md5_and_content_type(self):
        import base64
        import hashlib as _h

        from hunter_local_ingest.scenarios import CapturePlan

        content = b"synthetic-bytes"
        plan = CapturePlan(
            packet_id="pkt-1",
            create_request={},
            files=[("synthetic/a.syn", content)],
            roles={},
            packet=b"{}",
        )
        (descriptor,) = plan.file_descriptors
        assert descriptor["path"] == "synthetic/a.syn"
        assert descriptor["sha256"] == _h.sha256(content).hexdigest()
        assert descriptor["md5"] == base64.b64encode(_h.md5(content).digest()).decode(
            "ascii"
        )
        assert descriptor["contentType"] == "application/octet-stream"
        assert descriptor["role"] == "keyframe"  # published default, no mesh
        assert descriptor["bytes"] == len(content)

    def test_plan_roles_are_kept_when_published(self):
        from hunter_local_ingest.scenarios import CapturePlan

        plan = CapturePlan(
            packet_id="pkt-1",
            create_request={},
            files=[("synthetic/a.syn", b"x")],
            roles={"synthetic/a.syn": "depth"},
            packet=b"{}",
        )
        assert plan.file_descriptors[0]["role"] == "depth"


class TestRegisterRoundOracle:
    """Register answers match BY PATH (CaptureUploader.swift:460)."""

    def _descs(self, *paths):
        return [{"path": p} for p in paths]

    def test_targets_matched_by_path_not_position(self):
        from hunter_local_ingest.scenarios import register_round

        answer = {
            "b.syn": {"state": "awaiting_upload", "method": "PUT", "url": "u2"},
            "a.syn": {"state": "awaiting_upload", "method": "PUT", "url": "u1"},
        }
        round_plan = register_round(self._descs("a.syn", "b.syn"), answer)
        assert set(round_plan.to_upload) == {"a.syn", "b.syn"}
        assert round_plan.to_upload["a.syn"]["url"] == "u1"
        assert round_plan.to_upload["b.syn"]["url"] == "u2"
        assert round_plan.unacknowledged == []

    def test_committed_state_short_circuits(self):
        from hunter_local_ingest.scenarios import register_round

        answer = {"a.syn": {"state": "committed"}}
        round_plan = register_round(self._descs("a.syn"), answer)
        assert round_plan.already_committed == ["a.syn"]
        assert round_plan.to_upload == {}
        assert round_plan.unacknowledged == []

    def test_missing_target_is_unacknowledged(self):
        from hunter_local_ingest.scenarios import register_round

        round_plan = register_round(self._descs("a.syn", "b.syn"), {})
        assert round_plan.unacknowledged == ["a.syn", "b.syn"]

    def test_method_refusals(self):
        from hunter_local_ingest.scenarios import register_round

        answer = {
            "a.syn": {"state": "awaiting_upload", "method": "POST", "url": "u"},
            "b.syn": {"state": "awaiting_upload", "method": "PUT"},
            "c.syn": {"state": "awaiting_upload"},
        }
        round_plan = register_round(self._descs("a.syn", "b.syn", "c.syn"), answer)
        assert ("a.syn", "upload_method_not_put") in round_plan.method_refusals
        assert ("b.syn", "upload_method_missing_url") in round_plan.method_refusals
        assert ("c.syn", "upload_method_missing_url") in round_plan.method_refusals
        assert round_plan.to_upload == {}

    def test_answer_paths_outside_the_plan_are_flagged(self):
        from hunter_local_ingest.scenarios import register_round

        answer = {
            "a.syn": {"state": "awaiting_upload", "method": "PUT", "url": "u"},
            "ghost.syn": {"state": "awaiting_upload", "method": "PUT", "url": "g"},
        }
        round_plan = register_round(self._descs("a.syn"), answer)
        assert round_plan.duplicated == ["ghost.syn"]
        assert round_plan.to_upload == {"a.syn": answer["a.syn"]}


class TestStrictCommitOracle:
    """Partial commits FAIL (recon §3k; CaptureUploader.swift:578-582)."""

    def test_full_commit_passes(self):
        from hunter_local_ingest.scenarios import strict_commit_verdict

        ok, detail = strict_commit_verdict(
            ["a.syn", "b.syn"],
            {"committed": ["a.syn", "b.syn"], "notFound": [], "mismatch": []},
        )
        assert ok and "all 2" in detail

    def test_partial_commit_fails_naming_offenders(self):
        from hunter_local_ingest.scenarios import strict_commit_verdict

        ok, detail = strict_commit_verdict(
            ["a.syn", "b.syn", "c.syn"],
            {"committed": ["a.syn"], "notFound": ["c.syn"], "mismatch": ["b.syn"]},
        )
        assert not ok
        assert "not committed: ['b.syn', 'c.syn']" in detail
        assert "notFound: ['c.syn']" in detail
        assert "mismatch: ['b.syn']" in detail

    def test_unexpected_committed_paths_fail(self):
        from hunter_local_ingest.scenarios import strict_commit_verdict

        ok, detail = strict_commit_verdict(
            ["a.syn"], {"committed": ["a.syn", "ghost.syn"], "notFound": [], "mismatch": []}
        )
        assert not ok
        assert "ghost.syn" in detail

    def test_missing_membership_keys_are_empty_not_errors(self):
        from hunter_local_ingest.scenarios import strict_commit_verdict

        ok, _detail = strict_commit_verdict(["a.syn"], {"committed": ["a.syn"]})
        assert ok


class TestVerdictEventBinding:
    """verdict_ready binds data.runId nil-or-equal (CaptureUploader.swift:624)."""

    def test_nil_or_equal_bind_pass(self):
        from hunter_local_ingest.scenarios import verdict_events_binding

        assert verdict_events_binding([None, "run-9"], "run-9") == []

    def test_foreign_run_fails(self):
        from hunter_local_ingest.scenarios import verdict_events_binding

        violations = verdict_events_binding(["run-9", "other-run"], "run-9")
        assert len(violations) == 1
        assert "other-run" in violations[0]

    def test_foreign_run_without_finalize_run_id_fails(self):
        from hunter_local_ingest.scenarios import verdict_events_binding

        assert verdict_events_binding(["run-9"], None) != []
