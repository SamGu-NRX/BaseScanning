"""In-process state. One capture's event seq is strictly increasing."""

from __future__ import annotations

from datetime import UTC, datetime

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

_OPEN = frozenset({"uploading", "awaiting_files"})


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _segments(key: str) -> set[str]:
    return {part for part in key.replace("/", ":").split(":") if part}


def _drop_keyed(store: dict, tokens: set[str], *, keep: set[str] | None = None) -> None:
    kept = keep or set()
    for key in list(store):
        if key in kept:
            continue
        if _segments(key) & tokens:
            store.pop(key, None)


class MemoryState:
    def __init__(self) -> None:
        self._captures: dict[str, CaptureRecord] = {}
        self._files: dict[tuple[str, str], FileRecord] = {}
        self._events: dict[str, list[EventRecord]] = {}
        self._runs: dict[str, RunRecord] = {}
        self._stages: dict[tuple[str, str], StageRecord] = {}
        self._kv: dict[str, dict] = {}
        self._blobs: dict[str, bytes] = {}
        self._spend: dict[str, float] = {}

    def get_capture(self, capture_id: str) -> CaptureRecord | None:
        rec = self._captures.get(capture_id)
        return None if rec is None else rec.model_copy(deep=True)

    def put_capture(self, rec: CaptureRecord) -> None:
        self._captures[rec.capture_id] = rec.model_copy(deep=True)

    def set_status(self, capture_id: str, status: CaptureStatus) -> None:
        current = self._captures[capture_id]
        self._captures[capture_id] = current.model_copy(update={"status": status})

    def list_open_captures(self) -> list[CaptureRecord]:
        return [rec.model_copy(deep=True) for rec in self._captures.values() if rec.status in _OPEN]

    def delete_capture(self, capture_id: str) -> None:
        """Remove the capture and everything keyed by it, then keep the tomb."""

        run_ids = {run_id for run_id, rec in self._runs.items() if rec.capture_id == capture_id}
        self._captures.pop(capture_id, None)
        self._files = {key: rec for key, rec in self._files.items() if key[0] != capture_id}
        self._events.pop(capture_id, None)
        for run_id in run_ids:
            self._runs.pop(run_id, None)
        self._stages = {key: rec for key, rec in self._stages.items() if key[0] not in run_ids}
        tokens = {capture_id, *run_ids}
        tomb = f"tomb:{capture_id}"
        _drop_keyed(self._kv, tokens, keep={tomb})
        _drop_keyed(self._blobs, tokens)
        self._kv[tomb] = {"capture_id": capture_id}

    def upsert_file(self, rec: FileRecord) -> None:
        self._files[(rec.capture_id, rec.path)] = rec.model_copy(deep=True)

    def get_file(self, capture_id: str, path: str) -> FileRecord | None:
        rec = self._files.get((capture_id, path))
        return None if rec is None else rec.model_copy(deep=True)

    def files(self, capture_id: str) -> list[FileRecord]:
        return [rec.model_copy(deep=True) for (cid, _), rec in sorted(self._files.items()) if cid == capture_id]

    def append_event(
        self,
        capture_id: str,
        type: EventType,
        data: dict,
        *,
        run_id: str | None = None,
        stage: StageName | None = None,
    ) -> EventRecord:
        prior = self._events.get(capture_id, [])
        seq = (prior[-1].seq + 1) if prior else 1
        rec = EventRecord(
            capture_id=capture_id,
            seq=seq,
            type=type,
            at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            data=dict(data),
            run_id=run_id,
            stage=stage,
        )
        self._events.setdefault(capture_id, []).append(rec)
        return rec.model_copy(deep=True)

    def events_after(self, capture_id: str, seq: int, limit: int = 100) -> list[EventRecord]:
        rows = [rec for rec in self._events.get(capture_id, []) if rec.seq > seq]
        return [rec.model_copy(deep=True) for rec in rows[:limit]]

    def put_run(self, rec: RunRecord) -> None:
        self._runs[rec.run_id] = rec.model_copy(deep=True)

    def get_run(self, run_id: str) -> RunRecord | None:
        rec = self._runs.get(run_id)
        return None if rec is None else rec.model_copy(deep=True)

    def runs_for_capture(self, capture_id: str) -> list[RunRecord]:
        return [rec.model_copy(deep=True) for rec in self._runs.values() if rec.capture_id == capture_id]

    def list_runs(self, *, status: set[StageStatus], started_before: datetime | None = None) -> list[RunRecord]:
        found: list[RunRecord] = []
        for rec in self._runs.values():
            if rec.status not in status:
                continue
            if started_before is not None:
                if rec.started_at is None or _when(rec.started_at) >= started_before:
                    continue
            found.append(rec.model_copy(deep=True))
        return found

    def upsert_stage(self, rec: StageRecord) -> None:
        self._stages[(rec.run_id, rec.stage)] = rec.model_copy(deep=True)

    def get_stage(self, run_id: str, stage: StageName) -> StageRecord | None:
        rec = self._stages.get((run_id, stage))
        return None if rec is None else rec.model_copy(deep=True)

    def stages(self, run_id: str) -> list[StageRecord]:
        return [rec.model_copy(deep=True) for (rid, _), rec in sorted(self._stages.items()) if rid == run_id]

    def heartbeat(self, run_id: str, stage: StageName, call_id: str, substep: str, at: str) -> bool:
        rec = self._stages.get((run_id, stage))
        if rec is None:
            return False
        if rec.worker_call_id not in (None, call_id):
            return False
        self._stages[(run_id, stage)] = rec.model_copy(
            update={"heartbeat": at, "worker_call_id": call_id, "substep": substep}
        )
        return True

    def stale_stages(self, older_than_s: float) -> list[StageRecord]:
        now = datetime.now(UTC)
        stale: list[StageRecord] = []
        for rec in self._stages.values():
            if rec.status != "running":
                continue
            age = (now - _when(rec.heartbeat)).total_seconds()
            if age > older_than_s:
                stale.append(rec.model_copy(deep=True))
        return stale

    def put_if_absent(self, key: str, value: dict) -> bool:
        if key in self._kv:
            return False
        self._kv[key] = dict(value)
        return True

    def get_kv(self, key: str) -> dict | None:
        value = self._kv.get(key)
        return None if value is None else dict(value)

    def put_kv(self, key: str, value: dict) -> None:
        self._kv[key] = dict(value)

    def delete_kv(self, key: str) -> None:
        self._kv.pop(key, None)

    def put_blob(self, key: str, value: bytes) -> None:
        self._blobs[key] = value

    def get_blob(self, key: str) -> bytes | None:
        return self._blobs.get(key)

    def list_blobs(self, prefix: str) -> list[str]:
        return sorted(key for key in self._blobs if key.startswith(prefix))

    def delete_blob(self, key: str) -> None:
        self._blobs.pop(key, None)

    def add_spend(self, scope: str, usd: float) -> float:
        self._spend[scope] = self._spend.get(scope, 0.0) + usd
        return self._spend[scope]

    def get_spend(self, scope: str) -> float:
        return self._spend.get(scope, 0.0)
