"""JSON blobs on a `Storage` and an optional local cache directory for offline replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from server.reads.types import ReaderAnswer


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_prefix(self, prefix: str) -> list[str]: ...


class MemoryStorage:
    """Dict-backed storage for tests and the local CLI."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects

    def head(self, key: str) -> dict:
        return {"bytes": len(self.objects[key])}

    def list_prefix(self, prefix: str) -> list[str]:
        return sorted(key for key in self.objects if key.startswith(prefix))


def put_json(storage: Storage, key: str, obj: object) -> None:
    storage.put_bytes(key, json.dumps(obj).encode(), "application/json")


def get_json(storage: Storage, key: str) -> dict:
    return json.loads(storage.get_bytes(key))


def cache_key(capture_id: str, sha256: str, model: str, prompt_id: str, max_side_px: int) -> str:
    return (
        f"captures/{capture_id}/early/reads/vlm-cache/{sha256}/{model}.{prompt_id.replace('/', '_')}.{max_side_px}.json"
    )


def still_key(capture_id: str, still_id: str) -> str:
    return f"captures/{capture_id}/early/reads/{still_id}.json"


def identity_key(capture_id: str) -> str:
    return f"captures/{capture_id}/early/meter_identity.json"


def reads_key(capture_id: str, run_id: str) -> str:
    return f"captures/{capture_id}/runs/{run_id}/reads/reads.json"


def read_cached(storage: Storage, key: str) -> ReaderAnswer | None:
    if not storage.exists(key):
        return None
    answer = ReaderAnswer.model_validate(get_json(storage, key))
    return answer.model_copy(update={"cached": True, "cost_usd": 0.0})


def write_cached(storage: Storage, key: str, answer: ReaderAnswer) -> None:
    put_json(storage, key, answer.model_dump(mode="json"))


def local_cache_path(cache_dir: str, sha256: str, model: str, prompt_id: str, max_side_px: int) -> Path:
    name = f"{model}.{prompt_id.replace('/', '_')}.{max_side_px}.json"
    return Path(cache_dir) / sha256 / name


def read_local_cache(path: Path) -> ReaderAnswer | None:
    if not path.exists():
        return None
    answer = ReaderAnswer.model_validate(json.loads(path.read_text()))
    return answer.model_copy(update={"cached": True, "cost_usd": 0.0})


def write_local_cache(path: Path, answer: ReaderAnswer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer.model_dump(mode="json")))
