"""Accept packet.json, or an amendment packet."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import get_deps, load_owned, require_principal
from server.api.errors import fail
from server.api.finalize import accept_packet, inflate
from server.api.ids import require_contract_id
from server.contracts.api import AmendmentRequest


def _latest_run(deps, capture_id: str) -> str | None:
    runs = deps.state.runs_for_capture(capture_id)
    if not runs:
        return None
    return max(runs, key=lambda item: item.created_at).run_id


def register(app: FastAPI) -> None:
    @app.post("/v1/captures/{capture_id}/finalize")
    async def finalize_capture(capture_id: str, request: Request):
        return await _finalize(capture_id, request, amendment_id=None)

    @app.post("/v1/captures/{capture_id}/amendments/{amendment_id}/finalize")
    async def finalize_amendment(capture_id: str, amendment_id: str, request: Request):
        return await _finalize(capture_id, request, amendment_id=amendment_id)


async def _finalize(capture_id: str, request: Request, amendment_id: str | None):
    principal = require_principal(request)
    deps = get_deps(request)
    deps.charge(principal.label, "finalize", 10)
    async with deps.locks.acquire(capture_id):
        record = load_owned(deps, capture_id, principal, write=True)
        if record.status == "expired":
            raise fail(409, "capture_expired", "/", "finalize window has passed")
        if (
            amendment_id is None
            and record.status in {"complete", "failed", "needs_views", "manual_review"}
            and not record.packet_sha256
        ):
            raise fail(409, "invalid_state", "/", "capture cannot be finalized")
        if amendment_id is not None:
            require_contract_id(amendment_id, owner=AmendmentRequest, field_name="amendmentId", pointer="/amendmentId")
            amend = deps.state.get_kv(f"amend:{capture_id}:{amendment_id}")
            if not amend:
                raise fail(404, "amendment_not_found", "/", "amendment not found")
        key = request.headers.get("idempotency-key")
        if amendment_id is None and key != record.packet_id:
            raise fail(
                409,
                "packet_id_mismatch",
                "/headers/idempotency-key",
                "Idempotency-Key must equal the capture packet id",
            )
        raw = await request.body()
        inflated = inflate(raw, request.headers.get("content-encoding"))
        declared = request.headers.get("x-packet-sha256")
        parent = _latest_run(deps, capture_id) if amendment_id else None
        payload = accept_packet(
            deps,
            record,
            inflated,
            amendment_id=amendment_id,
            parent_run_id=parent,
            declared_sha=declared,
        )
        return JSONResponse(payload, status_code=202)
