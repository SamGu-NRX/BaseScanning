"""Copy listed uploads to captures/ once every listed file is committed."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from server.api.deps import Deps, extras, save_extras
from server.api.errors import fail
from server.api.events import emit
from server.api.keys import final_key, upload_key
from server.common.logging import log_event
from server.common.storage import PreconditionFailed, StorageUnavailable
from server.contracts.jobs import CaptureRecord


def promote(deps: Deps, record: CaptureRecord) -> None:
    extra = extras(deps, record.capture_id)
    paths = list(extra.get("paths") or [])
    if not paths or extra.get("promoted_at"):
        return
    started = time.perf_counter()
    files = []
    for path in paths:
        rec = deps.state.get_file(record.capture_id, path)
        if rec is None or rec.state != "committed" or not rec.generation:
            return
        files.append(rec)
    workers = max(1, deps.settings.HSS_INGEST_PROMOTE_CONCURRENCY)

    def one(rec):
        src = upload_key(record.capture_id, rec.path)
        dst = final_key(record.capture_id, rec.path)
        try:
            meta = deps.storage.copy(src, dst, if_generation_match=rec.generation)
            return rec, meta, None
        except PreconditionFailed:
            try:
                meta = deps.storage.head(src)
            except Exception as exc:
                raise StorageUnavailable(str(exc)) from exc
            size_only = rec.path in set(extra.get("size_only_paths") or [])
            md5_ok = size_only or meta.md5_b64 in (None, rec.md5)
            if meta.bytes == rec.bytes and md5_ok:
                meta = deps.storage.copy(src, dst, if_generation_match=None)
                return rec, meta, None
            return rec, None, "file_changed_after_commit"
        except Exception as exc:
            raise StorageUnavailable(str(exc)) from exc

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, files))
    except StorageUnavailable as exc:
        log_event("storage.error", op="copy", capture_id=record.capture_id, level="error")
        raise fail(503, "storage_unavailable", "/", "storage copy failed", **{"Retry-After": "5"}) from exc
    for rec, meta, problem in results:
        if problem:
            deps.state.set_status(record.capture_id, "failed")
            emit(
                deps,
                record.capture_id,
                "failed",
                {"code": problem, "message": "object changed after commit", "next": "none"},
            )
            return
        rec.generation = meta.generation
        rec.key = final_key(record.capture_id, rec.path)
        deps.state.upsert_file(rec)
    extra = extras(deps, record.capture_id)
    extra["promoted_at"] = deps.now().timestamp()
    save_extras(deps, record.capture_id, extra)
    log_event(
        "promotion.done",
        n=len(files),
        duration_ms=int((time.perf_counter() - started) * 1000),
        capture_id=record.capture_id,
    )
