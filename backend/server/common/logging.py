"""One JSON line per event. Secrets are never fields on this record."""

from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

KEYS = (
    "ts",
    "level",
    "event",
    "msg",
    "service.name",
    "deployment.environment",
    "product",
    "git_sha",
    "request_id",
    "capture_id",
    "run_id",
    "stage",
    "attempt",
    "gpu",
    "duration_ms",
    "vlm_provider",
    "vlm_model",
    "tokens_in",
    "tokens_out",
    "vlm_cost_usd",
    "disagreement",
    "error_code",
    "http.method",
    "http.route",
    "http.status",
    "principal",
    "packet_id",
    "amendment_id",
    "storage_backend",
    "state_backend",
)


request_fields: ContextVar[dict[str, Any] | None] = ContextVar("request_fields", default=None)


def log_event(event: str, *, level: str = "info", msg: str = "", **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = dict.fromkeys(KEYS)
    payload["ts"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload["level"] = level
    payload["event"] = event
    payload["msg"] = msg
    ctx = request_fields.get() or {}
    for key, value in {**ctx, **fields}.items():
        if key in payload:
            payload[key] = value
    sys.stdout.write(json.dumps(payload, default=str, sort_keys=True) + "\n")
    return payload
