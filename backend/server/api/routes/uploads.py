"""Volume-backend signed uploads. The HMAC token is the only credential."""

from __future__ import annotations

import asyncio
import base64
import hashlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from server.api.deps import get_deps
from server.api.errors import fail
from server.common.backends.storage_volume import read_token


class _TooLarge(Exception):
    pass


def _secret(deps) -> bytes:
    raw = deps.settings.UPLOAD_URL_HMAC_SECRET or ""
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


def register(app: FastAPI) -> None:
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
            deps.storage.commit_tmp(payload["key"], tmp, content_type, digest, size)
            return Response(status_code=200)
        except _TooLarge:
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
