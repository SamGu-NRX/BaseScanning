"""modal.Dict state. The modal package is imported only when this backend is constructed."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import get_args

from server.common.backends.state_memory import MemoryState
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
_APPEND_GUARD = threading.Lock()
_APPEND_LOCKS: dict[int, threading.Lock] = {}


def _append_lock(mapping: object) -> threading.Lock:
    """One lock per backing map, so two states on the same dict share it."""
    ident = id(mapping)
    with _APPEND_GUARD:
        lock = _APPEND_LOCKS.get(ident)
        if lock is None:
            lock = threading.Lock()
            _APPEND_LOCKS[ident] = lock
        return lock


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _segments(key: str) -> set[str]:
    return {part for part in key.replace("/", ":").split(":") if part}


class PrefixKv:
    """Key prefix over a modal.Dict (or a plain dict) so two workers can share one map."""

    def __init__(self, inner, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix

    def _full(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key, default=None):
        try:
            value = self._inner.get(self._full(key))
        except KeyError:
            return default
        if value is None and self._full(key) not in self._inner:
            return default
        return default if value is None else value

    def __setitem__(self, key, value) -> None:
        self._inner[self._full(key)] = value

    def __contains__(self, key) -> bool:
        return self.get(key) is not None

    def pop(self, key, default=None):
        full = self._full(key)
        pop = getattr(self._inner, "pop", None)
        if pop is None:
            return default
        try:
            return pop(full)
        except KeyError:
            return default

    def keys(self):
        return [str(key)[len(self._prefix) :] for key in self._inner.keys() if str(key).startswith(self._prefix)]


class _DictAdapter:
    """Presents a modal.Dict with the get/put/pop surface MemoryState does not need.

    ModalDictState stores JSON documents under the same keys as MemoryState. The
    modal import stays inside ``from_name`` so unit tests run without Modal.
    """

    def __init__(self, mapping) -> None:
        self._mapping = mapping

    def get(self, key: str):
        try:
            return self._mapping.get(key)
        except KeyError:
            return None

    def put(self, key: str, value, *, skip_if_exists: bool = False) -> bool:
        if skip_if_exists:
            put = getattr(self._mapping, "put", None)
            if put is not None:
                try:
                    put(key, value, skip_if_exists=True)
                    return self.get(key) == value or key in self._mapping
                except TypeError:
                    pass
            if self.get(key) is not None:
                return False
        if hasattr(self._mapping, "__setitem__"):
            self._mapping[key] = value
        else:
            self._mapping.put(key, value)
        return True


class _RemoteMap(dict):
    """dict surface over modal.Dict. ``items`` uses ``keys()`` when the remote has it."""

    def __init__(self, mapping) -> None:
        super().__init__()
        self._mapping = mapping

    def get(self, key, default=None):
        try:
            value = self._mapping.get(key)
        except Exception:
            value = None
        if value is None and not self._local_has(key):
            return default
        return default if value is None else value

    def _local_has(self, key) -> bool:
        try:
            return key in self._mapping
        except Exception:
            return False

    def __setitem__(self, key, value) -> None:
        self._mapping[key] = value

    def __contains__(self, key) -> bool:
        return self.get(key) is not None

    def pop(self, key, default=None):
        value = self.get(key, default)
        if value is default and not self._local_has(key):
            return default
        pop = getattr(self._mapping, "pop", None)
        if pop is not None:
            try:
                pop(key)
                return value
            except Exception:
                pass
        try:
            del self._mapping[key]
        except Exception:
            pass
        return value

    def setdefault(self, key, default):
        current = self.get(key)
        if current is not None:
            return current
        self[key] = default
        return default

    def items(self):
        keys_fn = getattr(self._mapping, "keys", None)
        keys = list(keys_fn()) if keys_fn else []
        return [(key, self.get(key)) for key in keys]


class ModalDictState(MemoryState):
    """Capture, file, event, run, and stage records live in the Dict, not the process.

    ``mapping`` is the raw dict (or a :class:`PrefixKv`). A second process that
    opens the same mapping sees the same records. ``from_name`` is the production
    constructor and does not import modal until it is called.
    """

    def __init__(self, mapping=None) -> None:
        super().__init__()
        if mapping is None:
            self.kv = self._kv
        else:
            self.kv = mapping
            self._kv = _RemoteMap(mapping)

    @classmethod
    def from_name(
        cls, name: str, *, create_if_missing: bool = True, environment_name: str | None = None
    ) -> ModalDictState:
        import modal

        kwargs: dict = {"create_if_missing": create_if_missing}
        if environment_name is not None:
            kwargs["environment_name"] = environment_name
        return cls(modal.Dict.from_name(name, **kwargs))

    def _model(self, key: str, model):
        value = self._kv.get(key)
        if not isinstance(value, dict):
            return None
        return model.model_validate(value).model_copy(deep=True)

    def _items(self):
        return list(self._kv.items())

    def get_capture(self, capture_id: str) -> CaptureRecord | None:
        return self._model(f"capture:{capture_id}", CaptureRecord)

    def put_capture(self, rec: CaptureRecord) -> None:
        self._kv[f"capture:{rec.capture_id}"] = rec.model_dump(mode="json")

    def set_status(self, capture_id: str, status: CaptureStatus) -> None:
        current = self.get_capture(capture_id)
        if current is None:
            raise KeyError(capture_id)
        self.put_capture(current.model_copy(update={"status": status}))

    def list_open_captures(self) -> list[CaptureRecord]:
        records: list[CaptureRecord] = []
        for key, value in self._items():
            if not str(key).startswith("capture:") or not isinstance(value, dict):
                continue
            rec = CaptureRecord.model_validate(value)
            if rec.status in _OPEN:
                records.append(rec.model_copy(deep=True))
        return records

    def delete_capture(self, capture_id: str) -> None:
        """Drop the capture and every record keyed by it. The tomb is the only survivor."""
        run_ids = {rec.run_id for rec in self.runs_for_capture(capture_id)}
        tokens = {capture_id, *run_ids}
        tomb = f"tomb:{capture_id}"
        for key, _value in self._items():
            text = str(key)
            if text == tomb:
                continue
            if _segments(text) & tokens:
                self._kv.pop(key, None)
        self._kv[tomb] = {"capture_id": capture_id}

    def upsert_file(self, rec: FileRecord) -> None:
        self._kv[f"file:{rec.capture_id}:{rec.path}"] = rec.model_dump(mode="json")
        paths = set(self._kv.get(f"files:{rec.capture_id}") or [])
        paths.add(rec.path)
        self._kv[f"files:{rec.capture_id}"] = sorted(paths)

    def get_file(self, capture_id: str, path: str) -> FileRecord | None:
        return self._model(f"file:{capture_id}:{path}", FileRecord)

    def files(self, capture_id: str) -> list[FileRecord]:
        records: list[FileRecord] = []
        for path in self._kv.get(f"files:{capture_id}") or []:
            rec = self.get_file(capture_id, path)
            if rec is not None:
                records.append(rec)
        return records

    def _backing(self) -> object:
        remote = getattr(self._kv, "_mapping", None)
        return self._kv if remote is None else remote

    def _claim_event(self, backing: object, key: str, payload: dict) -> bool:
        """Claim one sequence slot. A remote map uses skip_if_exists; a dict uses the append lock."""
        put = getattr(backing, "put", None)
        if callable(put) and not isinstance(backing, dict):
            try:
                put(key, payload, skip_if_exists=True)
            except TypeError:
                put = None
            else:
                current = self._kv.get(key)
                return current == payload
        if self._kv.get(key) is not None:
            return False
        self._kv[key] = payload
        return True

    def append_event(
        self,
        capture_id: str,
        type: EventType,
        data: dict,
        *,
        run_id: str | None = None,
        stage: StageName | None = None,
    ) -> EventRecord:
        backing = self._backing()
        lock = _append_lock(backing)
        for _attempt in range(10_000):
            with lock:
                seq = 1
                while self._kv.get(f"event:{capture_id}:{seq}") is not None:
                    seq += 1
                rec = EventRecord(
                    capture_id=capture_id,
                    seq=seq,
                    type=type,
                    at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    data=dict(data),
                    run_id=run_id,
                    stage=stage,
                )
                payload = rec.model_dump(mode="json")
                if self._claim_event(backing, f"event:{capture_id}:{seq}", payload):
                    return rec.model_copy(deep=True)
        raise RuntimeError("could not append event")

    def events_after(self, capture_id: str, seq: int, limit: int = 100) -> list[EventRecord]:
        if self._kv.get(f"event:{capture_id}:1") is not None:
            rows = []
            nxt = seq + 1
            while len(rows) < limit:
                raw = self._kv.get(f"event:{capture_id}:{nxt}")
                if not isinstance(raw, dict):
                    break
                rows.append(EventRecord.model_validate(raw).model_copy(deep=True))
                nxt += 1
            return rows
        rows = []
        for item in self._kv.get(f"events:{capture_id}") or []:
            rec = EventRecord.model_validate(item)
            if rec.seq > seq:
                rows.append(rec.model_copy(deep=True))
        return rows[:limit]

    def put_run(self, rec: RunRecord) -> None:
        self._kv[f"run:{rec.run_id}"] = rec.model_dump(mode="json")

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._model(f"run:{run_id}", RunRecord)

    def runs_for_capture(self, capture_id: str) -> list[RunRecord]:
        records: list[RunRecord] = []
        for key, value in self._items():
            if not str(key).startswith("run:") or not isinstance(value, dict):
                continue
            rec = RunRecord.model_validate(value)
            if rec.capture_id == capture_id:
                records.append(rec.model_copy(deep=True))
        return records

    def list_runs(self, *, status: set[StageStatus], started_before: datetime | None = None) -> list[RunRecord]:
        found: list[RunRecord] = []
        for key, value in self._items():
            if not str(key).startswith("run:") or not isinstance(value, dict):
                continue
            rec = RunRecord.model_validate(value)
            if rec.status not in status:
                continue
            if started_before is not None:
                if rec.started_at is None or _when(rec.started_at) >= started_before:
                    continue
            found.append(rec.model_copy(deep=True))
        return found

    def upsert_stage(self, rec: StageRecord) -> None:
        self._kv[f"stage:{rec.run_id}:{rec.stage}"] = rec.model_dump(mode="json")

    def get_stage(self, run_id: str, stage: StageName) -> StageRecord | None:
        return self._model(f"stage:{run_id}:{stage}", StageRecord)

    def stages(self, run_id: str) -> list[StageRecord]:
        """Point-read the eleven stage keys. A full Dict scan is a multi-second RPC per tick."""

        records: list[StageRecord] = []
        for name in get_args(StageName):
            rec = self.get_stage(run_id, name)
            if rec is not None:
                records.append(rec)
        return sorted(records, key=lambda rec: rec.stage)

    def heartbeat(self, run_id: str, stage: StageName, call_id: str, substep: str, at: str) -> bool:
        rec = self.get_stage(run_id, stage)
        if rec is None:
            return False
        if rec.worker_call_id not in (None, call_id):
            return False
        self.upsert_stage(rec.model_copy(update={"heartbeat": at, "worker_call_id": call_id, "substep": substep}))
        return True

    def stale_stages(self, older_than_s: float) -> list[StageRecord]:
        now = datetime.now(UTC)
        stale: list[StageRecord] = []
        for key, value in self._items():
            if not str(key).startswith("stage:") or not isinstance(value, dict):
                continue
            rec = StageRecord.model_validate(value)
            if rec.status != "running":
                continue
            age = (now - _when(rec.heartbeat)).total_seconds()
            if age > older_than_s:
                stale.append(rec.model_copy(deep=True))
        return stale

    def put_if_absent(self, key: str, value: dict) -> bool:
        """Insert once across processes. Modal Dict ``skip_if_exists`` is the atomic claim.

        MemoryState's check-then-assign is one process. Two capture-api containers
        sharing this dict must not both win the same idempotency key.
        """

        payload = dict(value)
        backing = self._backing()
        put = getattr(backing, "put", None)
        if callable(put) and not isinstance(backing, dict):
            try:
                # False means the key already existed, including when the stored
                # value is identical. A matching read is not a second win.
                inserted = put(key, payload, skip_if_exists=True)
            except TypeError:
                pass
            else:
                return bool(inserted)
        if self._kv.get(key) is not None:
            return False
        self._kv[key] = payload
        return True

    def put_blob(self, key: str, value: bytes) -> None:
        self._kv[f"blob:{key}"] = value

    def get_blob(self, key: str) -> bytes | None:
        value = self._kv.get(f"blob:{key}")
        if value is None:
            return None
        return bytes(value)

    def list_blobs(self, prefix: str) -> list[str]:
        found = []
        for key, _value in self._items():
            text = str(key)
            if text.startswith("blob:") and text[len("blob:") :].startswith(prefix):
                found.append(text[len("blob:") :])
        return sorted(found)

    def delete_blob(self, key: str) -> None:
        self._kv.pop(f"blob:{key}", None)

    def add_spend(self, scope: str, usd: float) -> float:
        total = float(self._kv.get(f"spend:{scope}") or 0) + usd
        self._kv[f"spend:{scope}"] = total
        return total

    def get_spend(self, scope: str) -> float:
        value = self._kv.get(f"spend:{scope}")
        return 0.0 if value is None else float(value)
