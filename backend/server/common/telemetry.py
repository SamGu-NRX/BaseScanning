"""One JSON line per event. Builds the WS-00 log key set; never a second logger."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from server.common.logging import KEYS as _LOG_KEYS

_SECRET_FIELDS = frozenset(
    {
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "CAPTURE_API_TOKENS",
        "UPLOAD_URL_HMAC_SECRET",
        "WEBHOOK_HMAC_SECRET",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "R2_SECRET_ACCESS_KEY",
        "APNS_KEY_P8",
    }
)

_CORE_KEYS = (
    "ts",
    "level",
    "event",
    "msg",
    "service.name",
    "deployment.environment",
    "product",
    "git_sha",
)

# logging.KEYS plus the cost_summary numbers this module emits.
_ALLOW = frozenset(_LOG_KEYS) | frozenset(
    {
        "gpu_s",
        "gpu_usd",
        "total_usd",
        "time_to_verdict_s",
    }
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def _scrub(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if "X-Goog-Signature" in value or "Bearer " in value or _JWT.search(value):
        return "[redacted]"
    return value


def _redact(key: str, value: Any) -> Any:
    if key in _SECRET_FIELDS or key not in _ALLOW:
        return None
    return _scrub(value)


def log_event(event: str, **fields: Any) -> None:
    """Write one JSON object to stdout. Secret field names are redacted."""
    payload: dict[str, Any] = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": fields.pop("level", "info"),
        "event": event,
        "msg": _scrub(fields.pop("msg", "")),
        "service.name": fields.pop("service.name", os.environ.get("SMP_SERVICE_NAME", "")),
        "deployment.environment": fields.pop("deployment.environment", os.environ.get("SMP_ENVIRONMENT", "")),
        "product": fields.pop("product", os.environ.get("SMP_PRODUCT_ID", "house-scanning")),
        "git_sha": fields.pop("git_sha", os.environ.get("GIT_SHA", "")),
    }
    for key, value in fields.items():
        redacted = _redact(key, value)
        if redacted is None:
            continue
        payload[key] = redacted
    for key in _CORE_KEYS:
        payload.setdefault(key, "")
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


class StageTimer:
    """Emit stage_started / stage_finished, or stage_failed with error_code."""

    def __init__(self, stage: str, capture_id: str, run_id: str, attempt: int) -> None:
        self.stage = stage
        self.capture_id = capture_id
        self.run_id = run_id
        self.attempt = attempt
        self._started = 0.0

    def _base(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "capture_id": self.capture_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
        }

    def __enter__(self) -> StageTimer:
        self._started = time.perf_counter()
        log_event("stage_started", **self._base())
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, _tb: object) -> bool:
        duration_ms = int((time.perf_counter() - self._started) * 1000)
        if exc_type is None:
            log_event("stage_finished", duration_ms=duration_ms, **self._base())
            return False
        error_code = getattr(exc, "error_code", None) or exc_type.__name__
        log_event("stage_failed", duration_ms=duration_ms, error_code=str(error_code), **self._base())
        return False


class VlmCostMeter:
    """Accumulate vlm_call events for a cost_summary."""

    def __init__(self) -> None:
        self.gpu_s = 0.0
        self.gpu_usd = 0.0
        self.vlm_usd = 0.0
        self.time_to_verdict_s = 0.0

    def add(self, provider: str, model: str, tokens_in: int, tokens_out: int, usd: float) -> None:
        self.vlm_usd += usd
        log_event(
            "vlm_call",
            vlm_provider=provider,
            vlm_model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            vlm_cost_usd=usd,
        )

    def cost_summary(self) -> dict[str, float]:
        total = self.gpu_usd + self.vlm_usd
        summary = {
            "gpu_s": self.gpu_s,
            "gpu_usd": self.gpu_usd,
            "vlm_usd": self.vlm_usd,
            "total_usd": total,
            "time_to_verdict_s": self.time_to_verdict_s,
        }
        log_event("cost_summary", **summary)
        return summary


@contextmanager
def stage_timer(stage: str, capture_id: str, run_id: str, attempt: int) -> Iterator[StageTimer]:
    with StageTimer(stage, capture_id, run_id, attempt) as timer:
        yield timer
