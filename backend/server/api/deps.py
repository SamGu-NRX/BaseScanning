"""Request-scoped dependencies. Tests pass clock and new_id; nothing else is random."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import Request

from server.api.auth import Principal, authenticate
from server.api.errors import fail
from server.api.limits import Limiter
from server.api.locks import CaptureLocks
from server.common.config import Settings
from server.common.logging import log_event
from server.contracts.jobs import CaptureRecord


def utcnow() -> datetime:
    return datetime.now(UTC)


def isoformat(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def default_id(prefix: str) -> str:
    from ulid import ULID

    return prefix + str(ULID())


@dataclass
class Deps:
    settings: Settings
    storage: object
    state: object
    hooks: object
    tokens: dict
    locks: CaptureLocks
    create_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: dict = field(default_factory=dict)
    limiter: Limiter | None = None
    polls: dict = field(default_factory=dict)
    clock: Callable[[], datetime] = utcnow
    new_id: Callable[[str], str] = default_id
    upload_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))

    def now(self) -> datetime:
        return self.clock()

    def charge(self, principal: str, bucket: str, capacity: float, cost: float = 1) -> None:
        assert self.limiter is not None
        allowed, retry = self.limiter.allow(principal, bucket, capacity, 60, cost)
        if not allowed:
            log_event("rate.limited", route=bucket, principal=principal)
            raise fail(429, "rate_limited", "/", "rate limit", **{"Retry-After": str(retry)})


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def require_principal(request: Request) -> Principal:
    existing = getattr(request.state, "principal", None)
    if existing is not None:
        return existing
    deps = get_deps(request)
    try:
        principal = authenticate(request.headers.get("authorization"), deps.settings, deps.tokens)
    except Exception:
        log_event("auth.denied", reason="rejected")
        raise
    request.state.principal = principal
    return principal


def extras(deps: Deps, capture_id: str) -> dict:
    return deps.state.get_kv(f"capturex:{capture_id}") or {}


def save_extras(deps: Deps, capture_id: str, payload: dict) -> None:
    deps.state.put_kv(f"capturex:{capture_id}", payload)


def load_owned(deps: Deps, capture_id: str, principal: Principal, *, write: bool = False) -> CaptureRecord:
    tomb = deps.state.get_kv(f"tomb:{capture_id}")
    if tomb:
        raise fail(410, "capture_deleted", "/", "capture was deleted")
    record = deps.state.get_capture(capture_id)
    if record is None:
        raise fail(404, "capture_not_found", "/", "capture not found")
    if record.owner_principal != principal.label and (write or not principal.reviewer):
        raise fail(403, "forbidden", "/", "capture belongs to another principal")
    return record


def window_end(deps: Deps) -> str:
    return isoformat(deps.now() + timedelta(hours=deps.settings.HSS_INGEST_FINALIZE_WINDOW_H))
