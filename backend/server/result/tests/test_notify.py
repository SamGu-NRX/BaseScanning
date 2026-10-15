"""Event data, the APNs literal, the webhook signature, and the retry schedule."""

import json

import pytest

from server.contracts.api import RetakeRequestData, VerdictReadyData
from server.result.notify import (
    apns_payload,
    post_webhook,
    retry_schedule,
    signature_header,
    signature_ok,
    webhook_body,
)


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, url, content, headers):
        self.calls.append((url, content, headers))


def test_apns_literal_has_no_decision():
    body = apns_payload("cap_test", "verdict_ready")
    assert body == {
        "aps": {"alert": {"title": "Your site check is ready"}},
        "captureId": "cap_test",
        "type": "verdict_ready",
    }
    assert "decision" not in body
    assert "address" not in json_blob(body)


def test_signature_round_trip():
    payload = b'{"id":"evt_1"}'
    header = signature_header("secret", 10, payload)
    assert header.startswith("t=10,v1=")
    assert signature_ok("secret", header, payload)
    assert not signature_ok("other", header, payload)
    assert not signature_ok("secret", "t=10,v1=00", payload)


def test_retry_schedule_covers_a_day():
    steps = retry_schedule()
    assert steps[0] == 1
    assert steps[1] == 2
    assert steps[2] == 4
    assert max(steps) == 3600
    assert abs(sum(steps) - 86400) < 1e-6
    assert steps[-1] <= 3600


def test_webhook_idempotent_on_id():
    client = _Client()
    body = webhook_body(
        event_id="evt_run_ok",
        capture_id="cap_test",
        order_ref="base-order-0",
        run_id="run_ok",
        decision="reject",
        verdict_url="verdict.C.json",
    )
    VerdictReadyData.model_validate({"runId": "run_ok", "kind": "manual_review"})
    RetakeRequestData.model_validate({"runId": "run_ok", "viewsNeeded": ["vn-ground"], "memberActions": []})
    seen: set[str] = set()
    assert post_webhook(body, url="https://example.invalid/hook", secret="s", timestamp=1, client=client, sent=seen)
    assert not post_webhook(body, url="https://example.invalid/hook", secret="s", timestamp=1, client=client, sent=seen)
    assert len(client.calls) == 1
    _url, content, headers = client.calls[0]
    assert signature_ok("s", headers["X-Capture-Signature"], content)


class _Coded:
    def __init__(self, code: int):
        self.status_code = code


class _CodedClient:
    def __init__(self, code: int):
        self.code = code
        self.calls = 0

    def post(self, url, content, headers):
        self.calls += 1
        return _Coded(self.code)


def _hook_body() -> dict:
    return webhook_body(
        event_id="evt_run_ok",
        capture_id="cap_test",
        order_ref="base-order-0",
        run_id="run_ok",
        decision="pass",
        verdict_url="verdict.C.json",
    )


def test_webhook_http_error_is_not_recorded():
    client = _CodedClient(503)
    body = _hook_body()
    seen: set[str] = set()
    with pytest.raises(RuntimeError, match="503"):
        post_webhook(body, url="https://example.invalid/hook", secret="s", timestamp=1, client=client, sent=seen)
    assert body["id"] not in seen
    assert client.calls == 1


def test_webhook_ok_status_is_recorded():
    client = _CodedClient(204)
    body = _hook_body()
    seen: set[str] = set()
    assert post_webhook(body, url="https://example.invalid/hook", secret="s", timestamp=1, client=client, sent=seen)
    assert body["id"] in seen


def json_blob(body: dict) -> str:
    return json.dumps(body)
