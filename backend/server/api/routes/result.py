"""Serve the device result and the reviewer HTML once WS-10 has written them."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from server.api.deps import get_deps, load_owned, require_principal
from server.api.errors import fail
from server.common.logging import log_event
from server.contracts.result import ResultResponse
from server.orchestrator.stateio import get_json


def _latest(deps, capture_id: str):
    runs = deps.state.runs_for_capture(capture_id)
    if not runs:
        return None
    return max(runs, key=lambda item: item.created_at)


def _strip_hidden(value):
    """Drop device-facing references to verdicts and the internal result."""
    if isinstance(value, str):
        if "verdicts/" in value or "result.internal.json" in value:
            return None
        return value
    if isinstance(value, list):
        return [_strip_hidden(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_hidden(item) for key, item in value.items()}
    return value


def _artifacts(deps, run) -> dict:
    """Re-read the run and the result blob. A scan can observe stage=done first."""

    if run is None:
        return {}
    fresh = deps.state.get_run(run.run_id) or run
    artifacts = dict(fresh.artifacts or {})
    extra = get_json(deps.state, f"stage_artifacts:{fresh.run_id}:result") or {}
    if isinstance(extra, dict):
        artifacts.update({str(key): str(value) for key, value in extra.items() if value is not None})
    return artifacts


def register(app: FastAPI) -> None:
    @app.get("/v1/captures/{capture_id}/result")
    def get_result(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        record = load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        stage = deps.state.get_stage(run.run_id, "result") if run else None
        ready = stage is not None and stage.status == "done"
        artifacts = _artifacts(deps, run)
        key = artifacts.get("result/result.json")
        if key:
            ready = True
        if not ready:
            body = ResultResponse(
                runId=run.run_id if run else record.run_id,
                status=record.status,
                viewsNeeded=[],
                memberActions=[],
                outcome=None,
            )
            log_event("result.served", ready=False, capture_id=capture_id, run_id=body.runId)
            return JSONResponse(body.model_dump(mode="json"))
        if not key:
            raise fail(404, "result_not_ready", "/", "result is not ready")
        try:
            payload = json.loads(deps.storage.get_bytes(key))
        except Exception as exc:
            log_event("storage.error", op="get", capture_id=capture_id, level="error")
            raise fail(503, "storage_unavailable", "/", "could not read the result", **{"Retry-After": "5"}) from exc
        payload["status"] = record.status
        payload["runId"] = run.run_id
        profile = deps.settings.RULES_PROFILE_DEFAULT
        verdict_key = artifacts.get(f"verdicts/verdict.{profile}.json")
        if principal.reviewer and isinstance(verdict_key, str) and "result.internal.json" not in verdict_key:
            payload["verdictUrl"] = deps.storage.signed_get_url(verdict_key, deps.settings.HSS_INGEST_RESULT_URL_TTL_S)
        else:
            payload = _strip_hidden(payload)
            payload["verdictUrl"] = None
        preview_key = artifacts.get("result/preview.ply")
        if preview_key:
            payload["previewUrl"] = deps.storage.signed_get_url(preview_key, deps.settings.HSS_INGEST_RESULT_URL_TTL_S)
        payload["reviewUrl"] = f"/v1/captures/{capture_id}/review"
        try:
            ResultResponse.model_validate(payload)
        except ValidationError as exc:
            log_event(
                "result.schema_drift",
                locs=[list(err.get("loc") or []) for err in exc.errors()],
                capture_id=capture_id,
                run_id=run.run_id,
            )
        log_event("result.served", ready=True, capture_id=capture_id, run_id=run.run_id)
        return JSONResponse(payload)

    @app.get("/v1/captures/{capture_id}/review")
    def get_review(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        if deps.settings.HSS_AUTH_MODE != "none" and not principal.reviewer:
            raise fail(403, "forbidden", "/", "review is for reviewers")
        load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        key = _artifacts(deps, run).get("result/review.html") if run else None
        if not key:
            raise fail(404, "result_not_ready", "/", "review is not ready")
        try:
            html = deps.storage.get_bytes(key)
        except FileNotFoundError as exc:
            raise fail(404, "result_not_ready", "/", "review is not ready") from exc
        return HTMLResponse(html)
