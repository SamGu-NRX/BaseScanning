"""Liveness. No bearer token. 503 in the last five minutes of a WIF identity token."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import get_deps, isoformat
from server.common.backends.gcp_auth import token_expiry
from server.common.logging import log_event
from server.contracts.api import HealthResponse


def register(app: FastAPI) -> None:
    @app.get("/v1/healthz")
    def healthz(request: Request) -> JSONResponse:
        deps = get_deps(request)
        expires = None
        token = os.environ.get("MODAL_IDENTITY_TOKEN")
        if deps.settings.GCP_AUTH_MODE == "wif" and token:
            moment = token_expiry(token)
            if moment is not None:
                expires = isoformat(moment)
                log_event("wif.token", expires_at=expires)
                if moment - datetime.now(UTC) <= timedelta(minutes=5):
                    body = HealthResponse(
                        status="degraded",
                        storage=deps.settings.STORAGE_BACKEND,
                        state=deps.settings.STATE_BACKEND,
                        wifTokenExpiresAt=expires,
                        version=deps.settings.GIT_SHA[:12],
                    )
                    return JSONResponse(body.model_dump(mode="json"), status_code=503)
        body = HealthResponse(
            status="ok",
            storage=deps.settings.STORAGE_BACKEND,
            state=deps.settings.STATE_BACKEND,
            wifTokenExpiresAt=expires,
            version=deps.settings.GIT_SHA[:12],
        )
        return JSONResponse(body.model_dump(mode="json"))
