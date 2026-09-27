"""Create, read, and delete a capture."""

from __future__ import annotations

import hashlib
import json

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from server.api.deps import get_deps, isoformat, load_owned, require_principal, save_extras, window_end
from server.api.errors import fail
from server.api.hooks import safe_hook
from server.api.idempotency import fingerprint
from server.api.ids import require_contract_id
from server.common.logging import log_event
from server.contracts.api import CreateCaptureRequest, CreateCaptureResponse, UploadLimits
from server.contracts.jobs import CaptureRecord


def _body_bytes(payload: dict) -> bytes:
    return JSONResponse(payload).body


def _etag(payload: dict) -> str:
    return 'W/"' + hashlib.sha256(_body_bytes(payload)).hexdigest()[:16] + '"'


def _limits(deps) -> UploadLimits:
    settings = deps.settings
    return UploadLimits(
        maxSinglePutBytes=settings.HSS_INGEST_MAX_SINGLE_PUT_BYTES,
        partBytes=settings.HSS_INGEST_PART_BYTES,
        urlTtlS=settings.HSS_INGEST_URL_TTL_S,
        maxFiles=settings.HSS_INGEST_MAX_FILES,
        maxBatch=settings.HSS_INGEST_MAX_BATCH,
    )


def _render(record: CaptureRecord, deps, body: dict | None) -> dict:
    if body is not None:
        return body
    return CreateCaptureResponse(
        captureId=record.capture_id,
        status="uploading",
        upload=_limits(deps),
        eventsUrl=f"/v1/captures/{record.capture_id}/events",
        finalizeBy=record.finalize_by,
    ).model_dump(mode="json")


def _existing_create(deps, existing: dict, principal, digest: str) -> JSONResponse:
    if existing.get("tomb"):
        raise fail(410, "capture_deleted", "/", "capture was deleted")
    if existing.get("owner") and existing.get("owner") != principal.label:
        raise fail(409, "idempotency_conflict", "/", "this packetId belongs to another principal")
    if existing.get("fingerprint") != digest:
        raise fail(409, "idempotency_conflict", "/", "this packetId was already used with a different body")
    ref = existing.get("ref")
    if not isinstance(ref, str) or deps.state.get_capture(ref) is None or not existing.get("body"):
        raise fail(409, "idempotency_conflict", "/", "this packetId has no capture record")
    return JSONResponse(existing["body"], status_code=200)


def register(app: FastAPI) -> None:
    @app.post("/v1/captures")
    async def create_capture(request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "create", 30)
        try:
            incoming = await request.json()
        except json.JSONDecodeError as exc:
            raise fail(422, "schema", "/", "body is not json") from exc
        if isinstance(incoming, dict) and not incoming.get("flow"):
            incoming = dict(incoming)
            incoming["flow"] = "photo" if incoming.get("tier") == "photo_only" else "guided"
        try:
            body = CreateCaptureRequest.model_validate(incoming)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        require_contract_id(body.packetId, owner=CreateCaptureRequest, field_name="packetId", pointer="/packetId")
        key = request.headers.get("idempotency-key")
        if not key:
            raise fail(422, "idempotency_key_missing", "/headers/idempotency-key", "Idempotency-Key is required")
        if key != body.packetId:
            raise fail(409, "idempotency_conflict", "/headers/idempotency-key", "Idempotency-Key must equal packetId")
        if body.formatVersion != "0.4":
            raise fail(422, "format_version_unsupported", "/formatVersion", "only formatVersion 0.4 is accepted")
        flow = body.flow or ("photo" if body.tier == "photo_only" else "guided")
        dumped = body.model_dump(mode="json")
        dumped["flow"] = flow
        digest = fingerprint(dumped)
        idem_key = f"idem:create:{body.packetId}"
        async with deps.create_lock:
            existing = deps.state.get_kv(idem_key)
            if existing:
                return _existing_create(deps, existing, principal, digest)
            open_count = sum(1 for rec in deps.state.list_open_captures() if rec.owner_principal == principal.label)
            if open_count >= deps.settings.HSS_INGEST_MAX_OPEN_CAPTURES:
                raise fail(429, "too_many_open_captures", "/", "too many open captures", **{"Retry-After": "60"})
            capture_id = deps.new_id("cap_")
            created = isoformat(deps.now())
            record = CaptureRecord(
                capture_id=capture_id,
                packet_id=body.packetId,
                status="uploading",
                tier=body.tier,
                flow=flow,
                device_model=body.device.model,
                order_ref=body.orderRef,
                owner_principal=principal.label,
                created_at=created,
                finalize_by=window_end(deps),
            )
            rendered = _render(record, deps, None)
            deps.state.put_capture(record)
            save_extras(
                deps,
                capture_id,
                {
                    "device": body.device.model_dump(mode="json"),
                    "expected": body.expected.model_dump(mode="json") if body.expected else None,
                },
            )
            deps.state.put_kv(f"open:{capture_id}", {"finalize_by": record.finalize_by})
            if not deps.state.put_if_absent(
                idem_key, {"fingerprint": digest, "ref": capture_id, "body": rendered, "owner": principal.label}
            ):
                deps.state.delete_capture(capture_id)
                return _existing_create(deps, deps.state.get_kv(idem_key) or {}, principal, digest)
            expected = body.expected.keyframes if body.expected else None
            safe_hook(
                "on_capture_created",
                deps.hooks.on_capture_created,
                capture_id,
                tier=body.tier,
                expected_keyframes=expected,
            )
            log_event(
                "capture.created",
                tier=body.tier,
                flow=flow,
                expected_keyframes=expected,
                capture_id=capture_id,
                packet_id=body.packetId,
                principal=principal.label,
            )
            return JSONResponse(rendered, status_code=201)

    @app.get("/v1/captures/{capture_id}")
    def get_capture(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        record = load_owned(deps, capture_id, principal)
        files = deps.state.files(capture_id)
        payload = {
            "captureId": record.capture_id,
            "packetId": record.packet_id,
            "status": record.status,
            "tier": record.tier,
            "flow": record.flow,
            "createdAt": record.created_at,
            "finalizeBy": record.finalize_by,
            "filesRegistered": len(files),
            "filesCommitted": sum(1 for item in files if item.state == "committed"),
            "runId": record.run_id,
        }
        tag = _etag(payload)
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return JSONResponse(payload, headers={"ETag": tag})

    @app.delete("/v1/captures/{capture_id}")
    async def delete_capture(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "delete", 10)
        async with deps.locks.acquire(capture_id), deps.locks.finish_async(capture_id):
            tomb = deps.state.get_kv(f"tomb:{capture_id}")
            record = deps.state.get_capture(capture_id)
            if record is None:
                return Response(status_code=204)
            if record.owner_principal != principal.label:
                raise fail(403, "forbidden", "/", "capture belongs to another principal")
            if tomb:
                return Response(status_code=204)
            safe_hook("cancel_capture", deps.hooks.cancel_capture, capture_id)
            deleted = 0
            run_ids = [item.run_id for item in deps.state.runs_for_capture(capture_id)]
            prefixes = [
                f"uploads/{capture_id}/",
                f"captures/{capture_id}/",
                f"work/{capture_id}/",
                *[f"work/{run_id}/" for run_id in run_ids],
            ]
            try:
                for prefix in prefixes:
                    deleted += deps.storage.delete_prefix(prefix)
            except Exception as exc:
                log_event("storage.error", op="delete_prefix", capture_id=capture_id, level="error")
                raise fail(503, "storage_unavailable", "/", "storage delete failed", **{"Retry-After": "5"}) from exc
            packet_id = record.packet_id
            deps.state.delete_capture(capture_id)
            deps.state.put_kv(f"tomb:{capture_id}", {"deleted_at": isoformat(deps.now()), "packet_id": packet_id})
            idem = deps.state.get_kv(f"idem:create:{packet_id}")
            if idem:
                idem["tomb"] = True
                idem["ref"] = f"tomb:{capture_id}"
                deps.state.put_kv(f"idem:create:{packet_id}", idem)
            log_event("capture.deleted", objects_deleted=deleted, capture_id=capture_id, principal=principal.label)
            return Response(status_code=204)
