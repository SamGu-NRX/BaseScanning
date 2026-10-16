"""Register files and commit the bytes the client PUT to the signed URL."""

from __future__ import annotations

import asyncio
import base64
import posixpath
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.api.deps import extras, get_deps, isoformat, load_owned, require_principal
from server.api.errors import ApiFailure, fail
from server.api.events import emit
from server.api.finalize import maybe_finish_after_commit
from server.api.hooks import safe_hook
from server.api.keys import upload_key
from server.common.logging import log_event
from server.contracts.api import (
    CommitRequest,
    CommitResponse,
    CompleteMultipartRequest,
    FilesCommittedData,
    RegisteredFile,
    RegisterFilesRequest,
    RegisterFilesResponse,
    UploadTarget,
)
from server.contracts.jobs import CommittedFile, FileRecord

_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_META = {"keyframe": "keyframe", "still": "still", "truedepth": "trueDepthFrame"}


def _path_ok(path: str) -> bool:
    if not _PATH.match(path) or "//" in path or path.startswith("/") or path.endswith("/"):
        return False
    if any(part in {".", ".."} for part in path.split("/")):
        return False
    if posixpath.normpath(path) != path:
        return False
    return path != "packet.json" and not path.endswith("/packet.json")


def _md5_ok(value: str) -> bool:
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return False
    return len(raw) == 16


def _types(role: str, tier: str) -> set[str]:
    if role == "keyframe":
        return {"image/jpeg"}
    if role == "still":
        return {"image/jpeg", "image/heic"} if tier == "photo_only" else {"image/jpeg"}
    if role in {"depth", "confidence", "features", "truedepth", "mesh"}:
        return {"application/octet-stream"}
    if role == "stream":
        return {"application/gzip"}
    return {"image/jpeg", "image/heic", "application/octet-stream", "application/gzip", "application/json"}


def _meta_ok(role: str, meta) -> bool:
    if meta is None:
        return True
    dumped = meta if isinstance(meta, dict) else meta.model_dump(exclude_none=True)
    if role in _META:
        return isinstance(dumped, dict) and set(dumped) == {_META[role]}
    return False


def _sign(deps, capture_id: str, rec: FileRecord) -> UploadTarget:
    if rec.bytes > deps.settings.HSS_INGEST_MAX_SINGLE_PUT_BYTES:
        raise fail(501, "multipart_not_implemented", "/files", "multipart upload is not implemented in this PR")
    try:
        signed = deps.storage.signed_put_url(
            upload_key(capture_id, rec.path),
            rec.content_type,
            rec.md5,
            deps.settings.HSS_INGEST_URL_TTL_S,
        )
    except Exception as exc:
        log_event("storage.error", op="sign", capture_id=capture_id, level="error")
        raise fail(503, "storage_unavailable", "/", "could not sign an upload", **{"Retry-After": "5"}) from exc
    return UploadTarget(method="PUT", url=signed.url, headers=signed.headers, expiresAt=isoformat(signed.expires_at))


def register(app: FastAPI) -> None:
    @app.post("/v1/captures/{capture_id}/files")
    async def register_files(capture_id: str, body: RegisterFilesRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "files_req", 120)
        deps.charge(principal.label, "files_n", 3000, cost=len(body.files))
        async with deps.locks.acquire(capture_id):
            record = load_owned(deps, capture_id, principal, write=True)
            extra = extras(deps, capture_id)
            prefix = extra.get("open_amendment")
            paths = [item.path for item in body.files]
            amend_ok = bool(prefix) and all(path.startswith(prefix) for path in paths)
            normal_ok = record.status in {"uploading", "awaiting_files"} and not prefix
            if not normal_ok and not amend_ok:
                raise fail(409, "invalid_state", "/", "capture is not accepting files")
            if len(body.files) > deps.settings.HSS_INGEST_MAX_BATCH:
                raise fail(422, "batch_too_large", "/files", "batch is larger than the maximum")
            errors: list[dict] = []
            seen: set[str] = set()
            for index, item in enumerate(body.files):
                if item.path in seen:
                    errors.append(
                        {
                            "code": "path_duplicate",
                            "pointer": f"/files/{index}/path",
                            "message": "path is repeated in this batch",
                        }
                    )
                seen.add(item.path)
                if not _path_ok(item.path):
                    errors.append(
                        {"code": "path_invalid", "pointer": f"/files/{index}/path", "message": "path is not allowed"}
                    )
                if not _md5_ok(item.md5):
                    errors.append(
                        {
                            "code": "bad_md5",
                            "pointer": f"/files/{index}/md5",
                            "message": "md5 must be base64 of 16 bytes",
                        }
                    )
                if item.contentType not in _types(item.role, record.tier):
                    errors.append(
                        {
                            "code": "content_type_invalid",
                            "pointer": f"/files/{index}/contentType",
                            "message": "content type is not allowed for this role",
                        }
                    )
                if not _meta_ok(item.role, item.meta):
                    errors.append(
                        {
                            "code": "meta_mismatch",
                            "pointer": f"/files/{index}/meta",
                            "message": "meta does not match the role",
                        }
                    )
                if item.bytes > deps.settings.HSS_INGEST_MAX_FILE_BYTES:
                    errors.append(
                        {
                            "code": "file_too_large",
                            "pointer": f"/files/{index}/bytes",
                            "message": "file exceeds the per-file limit",
                        }
                    )
                if (
                    extra.get("accepted")
                    and item.path not in set(extra.get("paths") or [])
                    and not (prefix and item.path.startswith(prefix))
                ):
                    errors.append(
                        {
                            "code": "not_in_manifest",
                            "pointer": f"/files/{index}/path",
                            "message": "path is not in the accepted packet",
                        }
                    )
                spec = (extra.get("digests") or {}).get(item.path)
                if spec and item.sha256 != spec["sha256"]:
                    errors.append(
                        {
                            "code": "sha256_mismatch",
                            "pointer": f"/files/{index}/sha256",
                            "message": "sha256 does not match the accepted packet",
                        }
                    )
                elif spec and item.bytes != spec["bytes"]:
                    errors.append(
                        {
                            "code": "file_size_mismatch",
                            "pointer": f"/files/{index}/bytes",
                            "message": "size does not match the accepted packet",
                        }
                    )
            conflicts = [
                item for item in errors if item["code"] in {"not_in_manifest", "sha256_mismatch", "file_size_mismatch"}
            ]
            if conflicts:
                raise ApiFailure(409, conflicts)
            if errors:
                raise ApiFailure(422, errors)
            existing = {rec.path: rec for rec in deps.state.files(capture_id)}
            projected = dict(existing)
            total_bytes = 0
            for item in body.files:
                previous = projected.get(item.path)
                if previous and previous.state == "committed" and previous.sha256 != item.sha256:
                    errors.append(
                        {
                            "code": "sha256_conflict",
                            "pointer": "/files",
                            "message": "committed file already has a different sha256",
                        }
                    )
                projected[item.path] = item
            for rec in projected.values():
                total_bytes += rec.bytes if isinstance(rec, FileRecord) else rec.bytes
            if len(projected) > deps.settings.HSS_INGEST_MAX_FILES:
                errors.append({"code": "too_many_files", "pointer": "/files", "message": "capture has too many files"})
            if total_bytes > deps.settings.HSS_INGEST_MAX_CAPTURE_BYTES:
                errors.append(
                    {"code": "capture_too_large", "pointer": "/files", "message": "capture exceeds the byte budget"}
                )
            conflicts = [item for item in errors if item["code"] == "sha256_conflict"]
            if conflicts:
                raise ApiFailure(409, conflicts)
            if errors:
                raise ApiFailure(422, errors)
            for item in body.files:
                if item.bytes > deps.settings.HSS_INGEST_MAX_SINGLE_PUT_BYTES:
                    raise fail(
                        501, "multipart_not_implemented", "/files", "multipart upload is not implemented in this PR"
                    )
            prepared: list[FileRecord | None] = []
            for item in body.files:
                previous = existing.get(item.path)
                if previous and previous.sha256 == item.sha256 and previous.state == "committed":
                    prepared.append(None)
                    continue
                rec = FileRecord(
                    capture_id=capture_id,
                    path=item.path,
                    role=item.role,
                    bytes=item.bytes,
                    sha256=item.sha256,
                    md5=item.md5,
                    content_type=item.contentType,
                    state="pending",
                    key=upload_key(capture_id, item.path),
                    meta=None if item.meta is None else item.meta.model_dump(mode="json"),
                )
                prepared.append(rec)
            sem = asyncio.Semaphore(deps.settings.HSS_INGEST_SIGN_CONCURRENCY)

            async def sign_one(rec: FileRecord) -> UploadTarget:
                async with sem:
                    return await asyncio.to_thread(_sign, deps, capture_id, rec)

            signed = await asyncio.gather(*[sign_one(rec) for rec in prepared if rec is not None])
            cursor = 0
            out: list[RegisteredFile] = []
            for rec in prepared:
                if rec is None:
                    path = body.files[len(out)].path
                    out.append(RegisteredFile(path=path, state="committed", upload=None))
                    continue
                deps.state.upsert_file(rec)
                target = signed[cursor]
                cursor += 1
                out.append(RegisteredFile(path=rec.path, state="pending", upload=target))
            log_event(
                "files.registered",
                n=len(out),
                bytes=sum(item.bytes for item in body.files),
                multipart=0,
                capture_id=capture_id,
            )
            return JSONResponse(RegisterFilesResponse(files=out).model_dump(mode="json"))

    @app.post("/v1/captures/{capture_id}/files:commit")
    async def commit_files(capture_id: str, body: CommitRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "commit", 120)
        async with deps.locks.acquire(capture_id):
            record = load_owned(deps, capture_id, principal, write=True)
            if record.status in {"complete", "expired", "failed"}:
                raise fail(409, "invalid_state", "/", "capture is not accepting commits")
            for index, item in enumerate(body.files):
                rec = deps.state.get_file(capture_id, item.path)
                if rec is not None and rec.sha256 != item.sha256:
                    raise fail(
                        409, "sha256_conflict", f"/files/{index}/sha256", "sha256 does not match the registered file"
                    )
            committed: list[str] = []
            not_found: list[str] = []
            mismatch: list[str] = []
            newly: list[FileRecord] = []
            extra = extras(deps, capture_id)
            size_only = set(extra.get("size_only_paths") or [])
            # A failure partway through the batch (a busy index lease, a storage
            # head error) must still announce the files already committed: the
            # retry reads them as committed and would never announce them.
            try:
                for item in body.files:
                    rec = deps.state.get_file(capture_id, item.path)
                    if rec is None:
                        not_found.append(item.path)
                        continue
                    if rec.state == "committed":
                        committed.append(item.path)
                        continue
                    spec = (extra.get("digests") or {}).get(item.path)
                    if spec and (rec.sha256 != spec["sha256"] or rec.bytes != spec["bytes"]):
                        mismatch.append(item.path)
                        continue
                    try:
                        meta = deps.storage.head(upload_key(capture_id, item.path))
                    except FileNotFoundError:
                        not_found.append(item.path)
                        continue
                    except Exception as exc:
                        log_event("storage.error", op="head", capture_id=capture_id, level="error")
                        raise fail(
                            503, "storage_unavailable", "/", "storage head failed", **{"Retry-After": "5"}
                        ) from exc
                    md5_bad = item.path not in size_only and meta.md5_b64 not in (None, rec.md5)
                    if meta.bytes != rec.bytes or md5_bad:
                        rec.state = "mismatch"
                        deps.state.upsert_file(rec)
                        mismatch.append(item.path)
                        continue
                    rec.state = "committed"
                    rec.generation = meta.generation
                    rec.key = upload_key(capture_id, item.path)
                    rec.committed_at = isoformat(deps.now())
                    deps.state.upsert_file(rec)
                    committed.append(item.path)
                    newly.append(rec)
            finally:
                if newly:
                    _announce_committed(deps, capture_id, newly)
            maybe_finish_after_commit(deps, deps.state.get_capture(capture_id) or record)
            log_event(
                "files.committed", n=len(newly), mismatch=len(mismatch), not_found=len(not_found), capture_id=capture_id
            )
            return JSONResponse(
                CommitResponse(committed=committed, notFound=not_found, mismatch=mismatch).model_dump(mode="json")
            )

    @app.post("/v1/captures/{capture_id}/files:complete")
    async def complete_multipart(capture_id: str, body: CompleteMultipartRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        async with deps.locks.acquire(capture_id):
            load_owned(deps, capture_id, principal, write=True)
            raise fail(501, "multipart_not_implemented", "/", "multipart upload is not implemented in this PR")


def _announce_committed(deps, capture_id: str, newly: list[FileRecord]) -> None:
    listed = (extras(deps, capture_id).get("accepted") or {}).get("listed")
    payload = FilesCommittedData(paths=[rec.path for rec in newly], committed=len(newly), listed=listed).model_dump(
        mode="json"
    )
    emit(deps, capture_id, "files_committed", payload)
    hooked = [
        CommittedFile(
            path=rec.path,
            role=rec.role,
            sha256=rec.sha256,
            bytes=rec.bytes,
            generation=rec.generation or "",
            key=rec.key or upload_key(capture_id, rec.path),
            meta=rec.meta,
        )
        for rec in newly
    ]
    safe_hook("on_files_committed", deps.hooks.on_files_committed, capture_id, hooked)
