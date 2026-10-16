"""FastAPI factory. `uv run uvicorn server.api.app:create_app --factory`."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from server.api.auth import authenticate, parse_tokens
from server.api.deps import Deps, default_id, utcnow
from server.api.errors import ApiFailure, error_response, fail, pointer_from_loc
from server.api.hooks import resolve_hooks
from server.api.limits import Limiter
from server.api.locks import CaptureLocks
from server.api.routes import (
    amendments,
    captures,
    confirmations,
    devices,
    events,
    files,
    finalize,
    health,
    result,
    uploads,
)
from server.common.config import Settings, load, resolve_state, resolve_storage
from server.common.lease import LeaseBusy
from server.common.logging import log_event, request_fields

_MB = 1024 * 1024


def _log_route(path: str) -> str:
    """Volume upload URLs are capability tokens. The access log keeps the route shape only."""
    if path.startswith("/v1/uploads/"):
        return "/v1/uploads/{token}"
    return path


def _needs_auth(request: Request) -> bool:
    path = request.url.path
    if path == "/v1/healthz":
        return False
    return not path.startswith("/v1/uploads/")


def _body_limit(request: Request) -> int:
    path = request.url.path
    if path.startswith("/v1/uploads/"):
        return 256 * _MB
    if path.endswith("/finalize"):
        if request.headers.get("content-encoding") == "gzip":
            return 8 * _MB
        return 48 * _MB
    return _MB


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _buffer_body(request: Request, limit: int) -> bytes | None:
    """Read the body. None means it is over the limit, including a chunked body with no Content-Length."""
    seen = 0
    chunks: list[bytes] = []
    while True:
        message = await request.receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body") or b""
        seen += len(chunk)
        if seen > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    body = b"".join(chunks)

    async def replay():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = replay
    return body


def create_app(
    settings: Settings | None = None,
    hooks=None,
    clock: Callable[[], datetime] | None = None,
    new_id: Callable[[str], str] | None = None,
) -> FastAPI:
    settings = settings or load()
    clock = clock or utcnow
    new_id = new_id or default_id
    state = resolve_state(settings)
    storage = resolve_storage(settings)
    deps = Deps(
        settings=settings,
        storage=storage,
        state=state,
        hooks=hooks or resolve_hooks(settings, state, storage),
        tokens=parse_tokens(settings.CAPTURE_API_TOKENS),
        locks=CaptureLocks(),
        clock=clock,
        new_id=new_id,
    )
    deps.limiter = Limiter(clock)
    app = FastAPI(title="capture-api")
    app.state.deps = deps
    # Uvicorn's access line repeats the raw path, including the volume upload token.
    logging.getLogger("uvicorn.access").disabled = True

    class _Access:
        """Access log and body cap without BaseHTTPMiddleware, which reads the body up front."""

        def __init__(self, app) -> None:
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            request = Request(scope, receive)
            request_id = request.headers.get("x-request-id") or new_id("req_")
            request.state.request_id = request_id
            fields = {
                "service.name": settings.SMP_SERVICE_NAME,
                "deployment.environment": settings.SMP_ENVIRONMENT,
                "product": settings.SMP_PRODUCT_ID,
                "git_sha": settings.GIT_SHA,
                "storage_backend": settings.STORAGE_BACKEND,
                "state_backend": settings.STATE_BACKEND,
                "request_id": request_id,
                "http.method": request.method,
                "http.route": _log_route(request.url.path),
            }
            token = request_fields.set(fields)
            started = time.perf_counter()
            status_code = 500
            downstream = receive

            async def send_wrapper(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                    headers = MutableHeaders(raw=message.setdefault("headers", []))
                    headers["X-Request-Id"] = request_id
                await send(message)

            async def send_now(response: JSONResponse):
                nonlocal status_code
                status_code = response.status_code
                response.headers["X-Request-Id"] = request_id
                await response(scope, receive, send)

            try:
                if _needs_auth(request):
                    try:
                        principal = authenticate(request.headers.get("authorization"), settings, deps.tokens)
                    except ApiFailure as failure:
                        log_event("auth.denied", reason="rejected")
                        await send_now(error_response(failure))
                        return
                    request.state.principal = principal
                    fields["principal"] = principal.label
                # Volume uploads check the HMAC token before any body byte is read.
                if request.method in {"POST", "PUT", "PATCH"} and not request.url.path.startswith("/v1/uploads/"):
                    limit = _body_limit(request)
                    declared = _content_length(request)
                    if declared is not None and declared > limit:
                        await send_now(error_response(fail(413, "payload_too_large", "/", "body is too large")))
                        return
                    body = await _buffer_body(request, limit)
                    if body is None:
                        await send_now(error_response(fail(413, "payload_too_large", "/", "body is too large")))
                        return
                    downstream = request._receive
                await self.app(scope, downstream, send_wrapper)
            finally:
                log_event(
                    "http.request",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    **{"http.status": status_code},
                )
                request_fields.reset(token)

    app.add_middleware(_Access)

    @app.exception_handler(ApiFailure)
    async def api_failure(_request: Request, failure: ApiFailure):
        return error_response(failure)

    @app.exception_handler(LeaseBusy)
    async def lease_busy(_request: Request, exc: LeaseBusy):
        # The file-index merge never ran, so the registration is safe to retry.
        return error_response(
            fail(503, "lease_busy", "/", "file index is busy; retry the registration", **{"Retry-After": "1"})
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        items = []
        for err in exc.errors():
            loc = tuple(err.get("loc") or ())
            code = "schema"
            pointer = pointer_from_loc(loc)
            if loc and loc[-1] == "role":
                code = "role_unknown"
            if "value_required" in str(err.get("msg") or ""):
                code = "value_required"
                pointer = "/value"
            if err.get("type") in {"too_short", "too_long"} and "files" in loc:
                code = "batch_too_large"
                pointer = "/files"
            items.append({"code": code, "pointer": pointer, "message": err.get("msg", "invalid")})
        return JSONResponse({"errors": items}, status_code=422)

    health.register(app)
    captures.register(app)
    files.register(app)
    finalize.register(app)
    events.register(app)
    result.register(app)
    amendments.register(app)
    confirmations.register(app)
    devices.register(app)
    uploads.register(app, settings)
    return app


__all__ = ["create_app"]
