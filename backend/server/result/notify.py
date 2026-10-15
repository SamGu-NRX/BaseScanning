"""Event payloads, APNs body, webhook signature, and the retry schedule.

The stage attempts delivery once. ``retry_schedule`` is the 24 h plan a worker
follows; sleeping that long inside the stage would miss the run deadline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Protocol

import httpx

from server.contracts.api import RetakeRequestData, VerdictReadyData


class PostClient(Protocol):
    def post(self, url: str, content: bytes, headers: dict[str, str]) -> object: ...


def verdict_ready(run_id: str, kind: str) -> dict:
    return VerdictReadyData(runId=run_id, kind=kind).model_dump(mode="json")


def retake_request(run_id: str, view_ids: list[str], member_actions: list[str]) -> dict:
    return RetakeRequestData(runId=run_id, viewsNeeded=view_ids, memberActions=member_actions).model_dump(mode="json")


def apns_payload(capture_id: str, event_type: str) -> dict:
    return {
        "aps": {"alert": {"title": "Your site check is ready"}},
        "captureId": capture_id,
        "type": event_type,
    }


def webhook_body(
    *,
    event_id: str,
    capture_id: str,
    order_ref: str | None,
    run_id: str,
    decision: str,
    verdict_url: str | None,
) -> dict:
    return {
        "id": event_id,
        "type": "verdict.ready",
        "captureId": capture_id,
        "orderRef": order_ref,
        "runId": run_id,
        "decision": decision,
        "verdictUrl": verdict_url,
    }


def body_bytes(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signature_header(secret: str, timestamp: int, payload: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def signature_ok(secret: str, header: str, payload: bytes) -> bool:
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def retry_schedule(window_s: float = 86400.0, cap: float = 3600.0) -> list[float]:
    """``[1, 2, 4, …]`` doubling, capped at ``cap``, until ``window_s``."""

    out: list[float] = []
    delay = 1.0
    total = 0.0
    while total < window_s - 1e-9:
        step = min(delay, cap, window_s - total)
        out.append(step)
        total += step
        delay = min(delay * 2.0, cap)
    return out


def post_webhook(
    body: dict,
    *,
    url: str,
    secret: str,
    timestamp: int,
    client: PostClient | None = None,
    sent: set[str] | None = None,
) -> bool:
    """POST once. Returns False when ``body['id']`` was already sent.

    A status of 400 or higher raises ``RuntimeError`` and does not record the
    id, so a later attempt can retry. Clients that omit ``status_code`` count
    as success (the unit-test double).
    """

    seen = sent if sent is not None else set()
    if body["id"] in seen:
        return False
    payload = body_bytes(body)
    headers = {
        "Content-Type": "application/json",
        "X-Capture-Signature": signature_header(secret, timestamp, payload),
    }
    http = client or httpx.Client(timeout=10.0)
    own = client is None
    try:
        response = http.post(url, content=payload, headers=headers)
        code = getattr(response, "status_code", None)
        if code is not None and int(code) >= 400:
            raise RuntimeError(f"webhook status {code}")
    finally:
        if own:
            http.close()
    seen.add(body["id"])
    return True
