"""One-tap meter confirmation. Reviewers supersede; a device conflict stays 409. The event never carries value."""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import get_deps, isoformat, load_owned, require_principal
from server.api.errors import fail
from server.api.events import emit
from server.api.hooks import safe_hook
from server.api.idempotency import fingerprint
from server.api.keys import packet_key
from server.common.logging import log_event
from server.contracts.api import ConfirmationData, ConfirmationRequest, ConfirmationResponse

_VALUE = re.compile(r"^[A-Za-z0-9 ./-]{1,32}$")
_OPEN = {"uploading", "awaiting_files", "processing", "needs_views", "manual_review"}


def _images(deps, capture_id: str) -> set[str]:
    found: set[str] = set()
    for rec in deps.state.files(capture_id):
        meta = rec.meta or {}
        for key in ("still", "keyframe"):
            value = meta.get(key)
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, dict) and isinstance(value.get("id"), str):
                found.add(value["id"])
    key = packet_key(capture_id)
    try:
        if deps.storage.exists(key):
            packet = json.loads(deps.storage.get_bytes(key))
            for still in packet.get("stills") or []:
                if still.get("id"):
                    found.add(still["id"])
            for frame in packet.get("keyframes") or []:
                if frame.get("id"):
                    found.add(frame["id"])
    except Exception:
        pass
    return found


def _public(row: dict) -> dict:
    return ConfirmationResponse(
        confirmationId=row["confirmationId"],
        fieldId=row["fieldId"],
        imageId=row["imageId"],
        kind=row["kind"],
        by=row["by"],
        receivedAt=row["receivedAt"],
        seq=row["seq"],
    ).model_dump(mode="json")


def register(app: FastAPI) -> None:
    @app.post("/v1/captures/{capture_id}/confirmations")
    async def create_confirmation(capture_id: str, body: ConfirmationRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "confirm", 30)
        if body.kind != "none" and not body.value:
            raise fail(422, "value_required", "/value", "value is required unless kind is none")
        if body.value is not None and not _VALUE.match(body.value):
            raise fail(422, "schema", "/value", "value does not match the allowed pattern")
        async with deps.locks.acquire(capture_id):
            record = load_owned(deps, capture_id, principal)
            if record.status not in _OPEN:
                raise fail(409, "capture_closed", "/", "capture is not accepting confirmations")
            if body.imageId not in _images(deps, capture_id):
                raise fail(422, "image_unknown", "/imageId", "imageId is not a still or keyframe on this capture")
            by = "reviewer" if principal.reviewer else "device"
            digest = fingerprint(
                {"fieldId": body.fieldId, "kind": body.kind, "value": body.value, "imageId": body.imageId}
            )
            slot = f"confirm:{capture_id}:{body.fieldId}"
            existing = deps.state.get_kv(slot)
            if existing and existing.get("fingerprint") == digest:
                return JSONResponse(_public(existing), status_code=200)
            if existing and existing.get("fingerprint") != digest and by == "device":
                raise fail(409, "confirmation_conflict", "/", "this field already has a different answer")
            confirmation_id = deps.new_id("cfm_")
            event = ConfirmationData(
                confirmationId=confirmation_id,
                fieldId=body.fieldId,
                imageId=body.imageId,
                kind=body.kind,
                by=by,
            )
            stored_event = emit(deps, capture_id, "confirmation", event.model_dump(mode="json"))
            row = {
                "fingerprint": digest,
                "confirmationId": confirmation_id,
                "fieldId": body.fieldId,
                "imageId": body.imageId,
                "kind": body.kind,
                "by": by,
                "value": body.value,
                "receivedAt": isoformat(deps.now()),
                "seq": stored_event.seq,
            }
            deps.state.put_kv(slot, row)
            listing = deps.state.get_kv(f"confirmix:{capture_id}") or {"fields": []}
            if body.fieldId not in listing["fields"]:
                listing["fields"].append(body.fieldId)
            deps.state.put_kv(f"confirmix:{capture_id}", listing)
            safe_hook("on_confirmation", deps.hooks.on_confirmation, capture_id, body, confirmation_id, by=by)
            log_event(
                "confirmation.received",
                field_id=body.fieldId,
                image_id=body.imageId,
                kind=body.kind,
                capture_id=capture_id,
                principal=principal.label,
            )
            return JSONResponse(_public(row), status_code=201)

    @app.get("/v1/captures/{capture_id}/confirmations")
    def list_confirmations(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        load_owned(deps, capture_id, principal)
        listing = deps.state.get_kv(f"confirmix:{capture_id}") or {"fields": []}
        rows = []
        for field_id in listing["fields"]:
            row = deps.state.get_kv(f"confirm:{capture_id}:{field_id}")
            if row:
                shown = _public(row)
                shown["value"] = row.get("value")
                rows.append(shown)
        return JSONResponse({"confirmations": rows})
