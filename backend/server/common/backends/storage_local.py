"""Local filesystem storage. Signed URLs are file:// paths. The root is never the repo."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.common.storage import ObjectMeta, SignedUrl

_UPLOAD_ID = re.compile(r"^up-[0-9]+$")


class LocalFsStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / ".hss-meta.json"
        self._uploads = self.root / ".uploads"
        self._meta: dict[str, ObjectMeta] = {}
        self._next_id = 1
        self._lock = threading.RLock()
        self._load()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        with self._lock:
            self._meta[key] = ObjectMeta(
                bytes=len(data),
                md5_b64=base64.b64encode(hashlib.md5(data).digest()).decode(),
                generation=str(self._next_id),
                content_type=content_type,
            )
            self._next_id += 1
            self._save()

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def head(self, key: str) -> ObjectMeta:
        with self._lock:
            meta = self._meta.get(key)
            if meta is None or not self.exists(key):
                raise FileNotFoundError(key)
            return meta

    def signed_put_url(self, key: str, content_type: str, md5_b64: str, ttl_s: int) -> SignedUrl:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return SignedUrl(
            url=path.as_uri(),
            headers={"Content-Type": content_type, "Content-MD5": md5_b64},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
        )

    def signed_get_url(self, key: str, ttl_s: int) -> str:
        del ttl_s
        return self._path(key).as_uri()

    def list_prefix(self, prefix: str) -> list[str]:
        with self._lock:
            indexed = [key for key in self._meta if key.startswith(prefix)]
        live = {key for key in indexed if self.exists(key)}
        return sorted(set(self._walk_prefix(prefix)) | live)

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = set(self.list_prefix(prefix))
            keys.update(key for key in self._meta if key.startswith(prefix))
        for key in sorted(keys):
            self.delete(key)
        return len(keys)

    def prepare_tmp(self, key: str) -> Path:
        """One temp file per PUT attempt, not one per key.

        The per-key `{key}.uploading` name collides exactly when the phone
        retries a dropped PUT on the same signed URL while the first attempt's
        handler can still be draining its body: the retry's open truncated the
        file under the first attempt's handle, and the dropped attempt's
        cleanup unlinked the retry's temp, so the retry's commit_tmp failed
        with a 500 on valid bytes. A unique name under `.uploads/` — a prefix
        `list_prefix` already excludes — gives every attempt its own complete
        bytes; the last commit wins, which is the replay rule. Parking them in
        `.uploads/singles` also puts them where the sweeper ages stranded
        files out. VolumeStorage PUTs go through the same handler and inherit
        this naming, so the sweep covers both backends.
        """

        self._uploads.mkdir(parents=True, exist_ok=True)
        singles = self._uploads / "singles"
        singles.mkdir(parents=True, exist_ok=True)
        return singles / f"{uuid.uuid4().hex}.uploading"

    def discard_tmp(self, tmp: Path) -> None:
        if tmp.is_file():
            tmp.unlink()

    def commit_tmp(self, key: str, tmp: Path, content_type: str, md5_b64: str, nbytes: int) -> None:
        # The rename and the metadata write happen under one lock. Replacing the
        # final file before taking the lock lets two commits for one key
        # interleave as A-rename, B-rename, B-metadata, A-metadata: the volume
        # ends up with B's bytes while head() reports A's digest and size, and
        # files:commit then marks a valid upload as a mismatch. Under the lock
        # every commit is atomic — the last committer wins the bytes and the
        # metadata together, which is the replay rule.
        final = self._path(key)
        with self._lock:
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(final)
            self._meta[key] = ObjectMeta(
                bytes=nbytes,
                md5_b64=md5_b64,
                generation=str(self._next_id),
                content_type=content_type,
            )
            self._next_id += 1
            self._save()

    def _walk_prefix(self, prefix: str) -> list[str]:
        """Keys under `prefix` from the filesystem, including objects missing from this process's index."""
        if not prefix or prefix.startswith("/") or ".." in Path(prefix).parts:
            raise ValueError("bad key")
        root = self.root
        target = (root / prefix).resolve()
        if target != root and root not in target.parents:
            raise ValueError("bad key")
        if prefix.endswith("/"):
            walk = target if target.is_dir() else None
        elif target.is_file():
            return [target.relative_to(root).as_posix()]
        elif target.is_dir():
            walk = target
        else:
            walk = target.parent if target.parent.is_dir() else None
        if walk is None or not walk.is_dir():
            return []
        if walk != root and root not in walk.parents:
            return []
        found: list[str] = []
        for file in walk.rglob("*"):
            if not file.is_file() or file.name == ".hss-meta.json":
                continue
            relative = file.relative_to(root)
            if ".uploads" in relative.parts:
                continue
            key = relative.as_posix()
            if key.startswith(prefix):
                found.append(key)
        return found

    def copy(self, src: str, dst: str, *, if_generation_match: str | None = None) -> ObjectMeta:
        meta = self.head(src)
        if if_generation_match is not None and meta.generation != if_generation_match:
            raise ValueError("generation mismatch")
        self.put_bytes(dst, self.get_bytes(src), meta.content_type)
        return self.head(dst)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        with self._lock:
            existed = path.is_file()
            if existed:
                path.unlink()
            self._meta.pop(key, None)
            self._save()
            return existed

    def initiate_multipart(self, key: str, content_type: str) -> str:
        with self._lock:
            upload_id = f"up-{self._next_id}"
            self._next_id += 1
            folder = self._uploads / upload_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "meta.json").write_text(json.dumps({"key": key, "content_type": content_type}))
            self._save()
            return upload_id

    def signed_part_url(self, key: str, upload_id: str, part_number: int, ttl_s: int) -> SignedUrl:
        del key
        path = self._upload_dir(upload_id) / f"{part_number:06d}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return SignedUrl(url=path.as_uri(), headers={}, expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s))

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> ObjectMeta:
        folder = self._upload_dir(upload_id)
        meta = json.loads((folder / "meta.json").read_text())
        if meta["key"] != key:
            raise ValueError("upload key mismatch")
        blob = b"".join((folder / f"{number:06d}").read_bytes() for number, _etag in sorted(parts))
        self.put_bytes(key, blob, meta["content_type"])
        self.abort_multipart(key, upload_id)
        return self.head(key)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        del key
        folder = self._upload_dir(upload_id)
        if folder.is_dir():
            for child in folder.iterdir():
                child.unlink()
            folder.rmdir()

    def _upload_dir(self, upload_id: str) -> Path:
        if _UPLOAD_ID.fullmatch(upload_id) is None:
            raise ValueError("bad upload id")
        path = (self._uploads / upload_id).resolve()
        if self._uploads not in path.parents:
            raise ValueError("bad upload id")
        return path

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("bad key")
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("bad key")
        return path

    def _load(self) -> None:
        if not self._index_path.is_file():
            return
        raw = json.loads(self._index_path.read_text())
        self._next_id = int(raw["next_id"])
        self._meta = {key: ObjectMeta(**item) for key, item in raw["objects"].items()}

    def _save(self) -> None:
        with self._lock:
            payload = {
                "next_id": self._next_id,
                "objects": {
                    key: {
                        "bytes": meta.bytes,
                        "md5_b64": meta.md5_b64,
                        "generation": meta.generation,
                        "content_type": meta.content_type,
                    }
                    for key, meta in self._meta.items()
                },
            }
            self._index_path.write_text(json.dumps(payload, sort_keys=True))
