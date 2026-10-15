"""Long-poll for capture events."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import get_deps, load_owned, require_principal
from server.api.errors import fail
from server.api.events import poll_events


def register(app: FastAPI) -> None:
    @app.get("/v1/captures/{capture_id}/events")
    async def get_events(capture_id: str, request: Request, after: int = 0, wait: int = 0):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        if after < 0:
            raise fail(422, "schema", "/query/after", "after must be >= 0")
        wait_s = min(max(wait, 0), deps.settings.HSS_INGEST_EVENTS_MAX_WAIT_S)
        record = load_owned(deps, capture_id, principal)
        body = await poll_events(deps, record, after, wait_s)
        return JSONResponse(body.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
