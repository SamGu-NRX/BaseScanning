"""The FAKE_HUNTER fault round and the proxy's framing.

Each fault check is shown to pass with the real client and to fail with a
client that ignores the condition, so a green fault row means the client
reacted, not that the check cannot fail.
"""

from __future__ import annotations

import http.client
import json
import urllib.parse

import pytest

from hunter_local_ingest.faultproxy import FaultProxy, serve, step_for
from hunter_local_ingest.http_client import DirectHttpClient
from hunter_local_ingest.scenarios.faults import run_fault_round

FAULT_IDS = (
    "fault.retry-transient",
    "fault.no-retry-501",
    "fault.abort",
    "fault.truncated-response",
)


def _round(api, plan, factory):
    sink: list = []
    run_fault_round(
        api.base_url,
        plan,
        sink,
        client_factory=factory,
        result_budget_s=5.0,
        poll_interval_s=0.01,
        events_wait_s=0,
    )
    return {line["id"]: line for line in sink}


def _factory(url, **kw):
    kw.setdefault("retry_backoff_s", 0.0)
    return DirectHttpClient(url, **kw)


def test_fault_round_passes_with_the_published_retry_rules(fake, plan):
    with fake() as api:
        rows = _round(api, plan, _factory)
        packets = set(api.state.by_packet)
    for check_id in FAULT_IDS:
        assert rows[check_id]["status"] == "PASS", rows[check_id]
        assert rows[check_id]["transport"] == "FAKE_HUNTER"
    assert rows["fault.lifecycle.result"]["status"] == "PASS"
    assert rows["fault.retry-transient"]["observed"]["proxy_hits"] == 2
    assert (
        rows["fault.truncated-response"]["observed"]["http"]["error"] == "transport:incomplete-read"
    )
    # every fault ran under a packet id of its own; the baseline id was never used
    assert plan.packet_id not in packets
    assert {f"{plan.packet_id}-fh", f"{plan.packet_id}-f-retry"} <= packets


def test_a_client_that_never_retries_fails_the_retry_check(fake, plan):
    def no_retries(url, **kw):
        return DirectHttpClient(url, max_retries=0, retry_backoff_s=0.0)

    with fake() as api:
        rows = _round(api, plan, no_retries)
    assert rows["fault.retry-transient"]["status"] == "FAIL"
    assert rows["fault.abort"]["status"] == "FAIL"  # it expects exactly one retry


def test_a_client_that_retries_501_fails_the_no_retry_check(fake, plan, monkeypatch):
    from hunter_local_ingest import http_client

    monkeypatch.setattr(http_client, "is_transient", lambda status: status >= 500)
    with fake() as api:
        rows = _round(api, plan, _factory)
    assert rows["fault.no-retry-501"]["status"] == "FAIL"


def test_retry_after_is_honoured_and_capped():
    from hunter_local_ingest.http_client import RETRY_AFTER_CAP_S, _retry_after

    assert _retry_after("0") == 0.0
    assert _retry_after("7.5") == 7.5
    assert _retry_after("99999") == RETRY_AFTER_CAP_S
    assert _retry_after("nan") is None and _retry_after("-1") is None and _retry_after("x") is None


def test_truncated_request_keeps_its_own_length(fake):
    """The proxy re-frames a truncated body, so the origin answers at once."""
    with fake() as api:
        proxy = serve(api.base_url, {"create": {"kind": "truncate_request", "bytes": 3}})
        try:
            port = urllib.parse.urlsplit(proxy.base_url).port
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            body = json.dumps({"packetId": "synthetic-000001"}).encode()
            conn.request(
                "POST",
                "/v1/captures",
                body=body,
                headers={"Content-Type": "application/json", "Idempotency-Key": "synthetic-000001"},
            )
            response = conn.getresponse()
            response.read()
            conn.close()
        finally:
            proxy.stop()
    assert response.status == 422  # the cut JSON is refused, not waited on


def test_fault_times_bounds_the_hits(fake):
    with fake() as api:
        proxy = serve(api.base_url, {"health": {"kind": "status", "code": 503, "times": 1}})
        try:
            client = DirectHttpClient(proxy.base_url, max_retries=0)
            first, second = client.health(), client.health()
        finally:
            proxy.stop()
    assert first.http_status == 503 and second.http_status == 200


def test_https_origin_gets_a_tls_connection():
    proxy = FaultProxy("https://api.example/v1", {})
    try:
        conn = proxy.origin_connection()
        assert isinstance(conn, http.client.HTTPSConnection)
        assert (conn.host, conn.port) == ("api.example", 443)
    finally:
        proxy._server.server_close()


def test_proxy_refuses_plain_http_to_a_non_loopback_origin():
    from hunter_local_ingest.netpolicy import EndpointError

    with pytest.raises(EndpointError):
        FaultProxy("http://api.example/v1", {})


def test_step_routing():
    assert step_for("GET", "/v1/healthz") == "health"
    assert step_for("POST", "/v1/captures") == "create"
    assert step_for("POST", "/v1/captures/c/files") == "register"
    assert step_for("PUT", "/upload/t") == "upload"
    assert step_for("POST", "/v1/captures/c/files:commit") == "commit"
    assert step_for("POST", "/v1/captures/c/finalize") == "finalize"
    assert step_for("GET", "/v1/captures/c/events?after=0") == "events"
    assert step_for("GET", "/v1/captures/c/result") == "result"
    assert step_for("GET", "/nonsense") == "unknown"
