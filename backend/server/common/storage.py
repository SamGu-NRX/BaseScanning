"""Object storage protocol. WS-01 adds the cloud backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class PreconditionFailed(Exception):
    """The source generation no longer matches the committed one."""


class StorageUnavailable(Exception):
    """A storage call failed. The API turns this into 503 storage_unavailable."""


@dataclass(frozen=True)
class ObjectMeta:
    bytes: int
    md5_b64: str | None
    generation: str
    content_type: str


@dataclass(frozen=True)
class SignedUrl:
    url: str
    headers: dict[str, str]
    expires_at: datetime


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def head(self, key: str) -> ObjectMeta: ...

    def signed_put_url(self, key: str, content_type: str, md5_b64: str, ttl_s: int) -> SignedUrl: ...

    def signed_get_url(self, key: str, ttl_s: int) -> str: ...

    def list_prefix(self, prefix: str) -> list[str]: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def copy(self, src: str, dst: str, *, if_generation_match: str | None = None) -> ObjectMeta: ...

    def delete(self, key: str) -> bool: ...

    def initiate_multipart(self, key: str, content_type: str) -> str: ...

    def signed_part_url(self, key: str, upload_id: str, part_number: int, ttl_s: int) -> SignedUrl: ...

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> ObjectMeta: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...
