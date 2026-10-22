"""Volume-backend signed uploads. The HMAC token is the only credential."""

from __future__ import annotations

import asyncio
import base64
import hashlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from server.api.deps import get_deps
from server.api.errors import fail
from server.common.backends.storage_volume import read_token
from server.common.logging import log_event


class _TooLarge(Exception):
    pass


def routes_enabled(settings) -> bool:
    """Signed upload URLs are HMAC tokens, so only a deployment with a secret serves them.

    Volume deployments reject a missing secret at startup (`resolve_storage`).
    Outside volume storage the routes are disabled outright: tokens are never
    minted there and an empty-key verifier would accept forgeries. A test that
    wants the routes sets UPLOAD_URL_HMAC_SECRET to a throwaway value.
    """

    return bool(settings.UPLOAD_URL_HMAC_SECRET)


def _secret(deps) -> bytes:
    raw = deps.settings.UPLOAD_URL_HMAC_SECRET or ""
    if not raw:
        # Fail closed even if the routes were registered another way: an empty
        # HMAC key would let anyone who knows a storage key forge a token.
        raise fail(
            503,
            "uploads_disabled",
            "/",
            "upload token routes are disabled: UPLOAD_URL_HMAC_SECRET is not configured",
        )
    return raw.encode()


def _payload(token: str, deps) -> dict:
    payload = read_token(token, _secret(deps))
    if payload is None:
        raise fail(403, "token_invalid", "/", "upload token is not valid")
    if payload.get("_expired"):
        raise fail(410, "token_expired", "/", "upload token has expired")
    return payload


def _registered_bound(deps, key: str) -> int:
    if not key.startswith("uploads/"):
        raise fail(403, "token_invalid", "/", "upload token key is not an upload")
    capture_id, sep, path = key[len("uploads/") :].partition("/")
    if not sep or not capture_id or not path:
        raise fail(403, "token_invalid", "/", "upload token key is not an upload")
    record = deps.state.get_file(capture_id, path)
    if record is None:
        raise fail(404, "capture_not_found", "/", "upload is not registered")
    return int(record.bytes)


def _capture_id_for_upload(key: str) -> str:
    """Return the capture owning an upload object after token validation."""

    capture_id, separator, _path = key[len("uploads/") :].partition("/")
    if not separator or not capture_id:
        raise fail(403, "token_invalid", "/", "upload token key is not an upload")
    return capture_id


def register(app: FastAPI, settings) -> None:
    if not routes_enabled(settings):
        log_event(
            "api.startup",
            msg=("upload token routes disabled: UPLOAD_URL_HMAC_SECRET is not configured"),
        )
        return

    @app.put("/v1/uploads/{token}")
    async def put_upload(token: str, request: Request):
        deps = get_deps(request)
        payload = _payload(token, deps)
        if payload.get("op") not in {None, "put"}:
            raise fail(403, "token_invalid", "/", "upload token is not a PUT")
        content_type = request.headers.get("content-type", "")
        content_md5 = request.headers.get("content-md5", "")
        if content_type != payload.get("ct") or content_md5 != payload.get("md5"):
            return JSONResponse(
                {
                    "errors": [
                        {
                            "code": "bad_digest",
                            "pointer": "/headers/content-md5",
                            "message": "Content-Type or Content-MD5 does not match the token",
                        }
                    ]
                },
                status_code=400,
            )
        bound = _registered_bound(deps, str(payload.get("key") or ""))
        if bound > deps.settings.HSS_INGEST_MAX_SINGLE_PUT_BYTES:
            raise fail(413, "payload_too_large", "/", "object exceeds the single-put cap")
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length != bound:
                return JSONResponse(
                    {
                        "errors": [
                            {
                                "code": "bad_digest",
                                "pointer": "/headers/content-length",
                                "message": "Content-Length does not match the registered object",
                            }
                        ]
                    },
                    status_code=400,
                )
        try:
            await asyncio.wait_for(deps.upload_slots.acquire(), timeout=0.05)
        except TimeoutError:
            raise fail(429, "rate_limited", "/", "too many uploads", **{"Retry-After": "1"}) from None
        tmp = None
        try:
            tmp = deps.storage.prepare_tmp(payload["key"])
            hasher = hashlib.md5()
            size = 0
            with tmp.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    if size + len(chunk) > bound:
                        raise _TooLarge
                    hasher.update(chunk)
                    handle.write(chunk)
                    size += len(chunk)
            digest = base64.b64encode(hasher.digest()).decode()
            if digest != payload.get("md5") or size != bound:
                deps.storage.discard_tmp(tmp)
                return Response(content=b"BadDigest", status_code=400, media_type="text/plain")
            # DELETE holds the same finish lock while removing object storage and
            # publishing the capture tombstone. If it wins this race, refuse the
            # completed temp instead of recreating bytes under the deleted capture.
            capture_id = _capture_id_for_upload(str(payload["key"]))
            fence = getattr(deps.storage, "capture_fence", None)
            if fence is None:
                deps.storage.discard_tmp(tmp)
                raise fail(503, "storage_unavailable", "/", "storage has no shared capture fence")
            with fence(capture_id):
                if deps.storage.is_capture_deleted(capture_id) or deps.state.get_kv(f"tomb:{capture_id}"):
                    deps.storage.discard_tmp(tmp)
                    raise fail(410, "capture_deleted", "/", "capture was deleted")
                deps.storage.commit_tmp(payload["key"], tmp, content_type, digest, size)
            return Response(status_code=200)
        except _TooLarge:
            if tmp is not None:
                deps.storage.discard_tmp(tmp)
            return Response(content=b"BadDigest", status_code=400, media_type="text/plain")
        except ClientDisconnect:
            # The phone dropped the connection mid-body — it does this when a
            # retried PUT opens while the old attempt still drains. Nothing is
            # committed and only this attempt's temp file goes away, so the
            # retry starts clean on its own per-attempt temp.
            if tmp is not None:
                deps.storage.discard_tmp(tmp)
            return Response(content=b"BadDigest", status_code=400, media_type="text/plain")
        finally:
            deps.upload_slots.release()

    @app.get("/v1/uploads/{token}")
    def get_upload(token: str, request: Request):
        deps = get_deps(request)
        payload = _payload(token, deps)
        if payload.get("op") != "get":
            raise fail(403, "token_invalid", "/", "upload token is not a GET")
        try:
            data = deps.storage.get_bytes(payload["key"])
        except FileNotFoundError as exc:
            raise fail(404, "capture_not_found", "/", "object not found") from exc
        return Response(content=data, media_type="application/octet-stream")
