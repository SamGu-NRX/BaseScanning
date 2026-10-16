"""One state protocol. No brief subclasses it."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from server.contracts.jobs import (
    CaptureRecord,
    CaptureStatus,
    EventRecord,
    EventType,
    FileRecord,
    RunRecord,
    StageName,
    StageRecord,
    StageStatus,
)


class StateStore(Protocol):
    def get_capture(self, capture_id: str) -> CaptureRecord | None: ...

    def put_capture(self, rec: CaptureRecord) -> None: ...

    def set_status(self, capture_id: str, status: CaptureStatus) -> None: ...

    def list_open_captures(self) -> list[CaptureRecord]: ...

    def open_capture_ids(self, owner: str) -> list[str]:
        """Ids of this principal's captures still in ``uploading`` or ``awaiting_files``."""
        ...

    def delete_capture(self, capture_id: str) -> None:
        """Erase the capture and every state record keyed by it.

        Drops the capture, its file records, confirmations and other extras,
        events, runs, and those runs' stage records. Leaves ``tomb:{capture_id}``
        and is idempotent. Object bytes are not this method's job.
        """
        ...

    def upsert_file(self, rec: FileRecord) -> None: ...

    def get_file(self, capture_id: str, path: str) -> FileRecord | None: ...

    def files(self, capture_id: str) -> list[FileRecord]: ...

    def append_event(
        self,
        capture_id: str,
        type: EventType,
        data: dict,
        *,
        run_id: str | None = None,
        stage: StageName | None = None,
    ) -> EventRecord: ...

    def events_after(self, capture_id: str, seq: int, limit: int = 100) -> list[EventRecord]: ...

    def put_run(self, rec: RunRecord) -> None: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def runs_for_capture(self, capture_id: str) -> list[RunRecord]: ...

    def list_runs(self, *, status: set[StageStatus], started_before: datetime | None = None) -> list[RunRecord]: ...

    def upsert_stage(self, rec: StageRecord) -> None: ...

    def create_stage_if_absent(self, rec: StageRecord) -> StageRecord:
        """Atomic insert-if-absent: recovery must never reset a record another
        coordinator materialized and started first. Returns the record now stored."""

    def get_stage(self, run_id: str, stage: StageName) -> StageRecord | None: ...

    def stages(self, run_id: str) -> list[StageRecord]: ...

    def heartbeat(self, run_id: str, stage: StageName, call_id: str, substep: str, at: str) -> bool: ...

    def stale_stages(self, older_than_s: float) -> list[StageRecord]: ...

    def put_if_absent(self, key: str, value: dict) -> bool: ...

    def get_kv(self, key: str) -> dict | None: ...

    def put_kv(self, key: str, value: dict) -> None: ...

    def delete_kv(self, key: str) -> None: ...

    def put_blob(self, key: str, value: bytes) -> None: ...

    def get_blob(self, key: str) -> bytes | None: ...

    def list_blobs(self, prefix: str) -> list[str]: ...

    def delete_blob(self, key: str) -> None: ...

    def add_spend(self, scope: str, usd: float) -> float: ...

    def get_spend(self, scope: str) -> float: ...
