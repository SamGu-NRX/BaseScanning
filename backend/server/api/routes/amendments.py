"""One open amendment while the capture needs more views or a manual review."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import extras, get_deps, isoformat, load_owned, require_principal, save_extras
from server.api.errors import fail
from server.api.idempotency import fingerprint
from server.api.ids import require_contract_id
from server.common.logging import log_event
from server.contracts.api import AmendmentRequest, AmendmentResponse


def _answer_ids(deps, capture_id: str) -> set[str]:
    runs = deps.state.runs_for_capture(capture_id)
    if not runs:
        return set()
    run = max(runs, key=lambda item: item.created_at)
    key = (run.artifacts or {}).get("result/result.json")
    if not key:
        return set()
    try:
        payload = json.loads(deps.storage.get_bytes(key))
    except Exception:
        return set()
    found: set[str] = set()
    for view in payload.get("viewsNeeded") or []:
        if isinstance(view, dict) and view.get("id"):
            found.add(str(view["id"]))
    outcome = payload.get("outcome") or {}
    for view in outcome.get("viewsNeeded") or []:
        if isinstance(view, dict) and view.get("id"):
            found.add(str(view["id"]))
    for action in payload.get("memberActions") or []:
        found.add(str(action))
    return found


def register(app: FastAPI) -> None:
    @app.post("/v1/captures/{capture_id}/amendments")
    async def create_amendment(capture_id: str, body: AmendmentRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "amend", 30)
        require_contract_id(body.amendmentId, owner=AmendmentRequest, field_name="amendmentId", pointer="/amendmentId")
        key = request.headers.get("idempotency-key")
        if key != body.amendmentId:
            raise fail(
                409, "idempotency_conflict", "/headers/idempotency-key", "Idempotency-Key must equal amendmentId"
            )
        async with deps.locks.acquire(capture_id):
            record = load_owned(deps, capture_id, principal, write=True)
            digest = fingerprint(body.model_dump(mode="json"))
            idem_key = f"idem:amend:{capture_id}:{body.amendmentId}"
            existing = deps.state.get_kv(idem_key)
            if existing:
                if existing.get("fingerprint") != digest:
                    raise fail(
                        409, "idempotency_conflict", "/", "this amendmentId was already used with a different body"
                    )
                return JSONResponse(existing["body"], status_code=200)
            if record.status not in {"needs_views", "manual_review"}:
                raise fail(409, "invalid_state", "/", "amendments are only accepted from needs_views or manual_review")
            extra = extras(deps, capture_id)
            if extra.get("open_amendment"):
                raise fail(409, "amendment_open", "/", "an amendment is already open")
            allowed = _answer_ids(deps, capture_id)
            unknown = []
            for index, answer in enumerate(body.answers):
                if answer not in allowed:
                    unknown.append(
                        {
                            "code": "answers_unknown",
                            "pointer": f"/answers/{index}",
                            "message": "answer is not a needed view or member action",
                        }
                    )
            if unknown:
                from server.api.errors import ApiFailure

                raise ApiFailure(422, unknown)
            prefix = f"amendments/{body.amendmentId}/"
            deps.state.put_kv(
                f"amend:{capture_id}:{body.amendmentId}",
                {"answers": body.answers, "status": "open", "created_at": isoformat(deps.now())},
            )
            extra["open_amendment"] = prefix
            save_extras(deps, capture_id, extra)
            rendered = AmendmentResponse(amendmentId=body.amendmentId, pathPrefix=prefix).model_dump(mode="json")
            deps.state.put_kv(idem_key, {"fingerprint": digest, "ref": body.amendmentId, "body": rendered})
            log_event(
                "amendment.created", answers_n=len(body.answers), capture_id=capture_id, amendment_id=body.amendmentId
            )
            return JSONResponse(rendered, status_code=201)
