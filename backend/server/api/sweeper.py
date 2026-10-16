"""Expire captures past the finalize window, and age out volume uploads."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from server.api.deps import Deps, isoformat, utcnow
from server.api.events import emit
from server.common.config import Settings, load, resolve_state, resolve_storage
from server.common.logging import log_event


def _build(settings: Settings) -> Deps:
    from server.api.hooks import NullHooks
    from server.api.locks import CaptureLocks

    return Deps(
        settings=settings,
        storage=resolve_storage(settings),
        state=resolve_state(settings),
        hooks=NullHooks(),
        tokens={},
        locks=CaptureLocks(),
        clock=utcnow,
    )


def ingest_sweep(deps: Deps | None = None) -> dict:
    deps = deps or _build(load())
    now = isoformat(deps.now())
    expired = 0
    for record in list(deps.state.list_open_captures()):
        if record.finalize_by >= now:
            continue
        deps.state.set_status(record.capture_id, "expired")
        emit(
            deps,
            record.capture_id,
            "failed",
            {"code": "capture_expired", "message": "finalize window of 24 h passed", "next": "none"},
        )
        deps.state.delete_kv(f"open:{record.capture_id}")
        log_event("capture.expired", capture_id=record.capture_id)
        expired += 1
    deleted = 0
    if deps.settings.STORAGE_BACKEND in {"volume", "local"}:
        deleted = _sweep_volume(Path(deps.settings.VOLUME_ROOT))
    log_event("sweep.done", expired=expired, deleted_files=deleted)
    return {"expired": expired, "deleted_files": deleted}


def _sweep_volume(root: Path) -> int:
    now = time.time()
    deleted = 0
    uploads = root / "uploads"
    if uploads.exists():
        for path in uploads.rglob("*"):
            if path.is_file() and path.stat().st_mtime < now - 7 * 86400:
                path.unlink()
                deleted += 1
    # VolumeStorage.prepare_tmp parks one temporary per PUT attempt under
    # .uploads/singles, outside the uploads walk above. Sweep them on the same
    # age rule, so a process killed between prepare_tmp and commit_tmp strands
    # nothing in the volume.
    singles = root / ".uploads" / "singles"
    if singles.exists():
        for path in singles.glob("*.uploading"):
            if path.is_file() and path.stat().st_mtime < now - 7 * 86400:
                path.unlink()
                deleted += 1
    for path in root.rglob("*"):
        if path.is_dir() and path.name.endswith(".parts") and path.stat().st_mtime < now - 86400:
            shutil.rmtree(path, ignore_errors=True)
            deleted += 1
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.parse_args()
    ingest_sweep()


if __name__ == "__main__":
    main()
