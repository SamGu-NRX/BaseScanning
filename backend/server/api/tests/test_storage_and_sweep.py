"""Storage contract, GCS signing, promotion, and the volume sweeper."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from server.api.keys import final_key, upload_key
from server.api.sweeper import _sweep_volume, ingest_sweep
from server.api.tests.conftest import create_body, photo_packet, sha256_hex
from server.common.backends.storage_gcs import GcsStorage
from server.common.backends.storage_local import LocalFsStorage
from server.common.backends.storage_volume import VolumeStorage
from server.common.storage import PreconditionFailed
from server.contracts.jobs import StageRecord


def _roundtrip(storage):
    storage.put_bytes("captures/c/a.bin", b"hello", "application/octet-stream")
    assert storage.get_bytes("captures/c/a.bin") == b"hello"
    meta = storage.head("captures/c/a.bin")
    assert meta.bytes == 5
    copied = storage.copy("captures/c/a.bin", "captures/c/b.bin", if_generation_match=meta.generation)
    assert copied.generation != meta.generation or storage.get_bytes("captures/c/b.bin") == b"hello"
    try:
        storage.copy("captures/c/a.bin", "captures/c/c.bin", if_generation_match="nope")
        raised = False
    except (PreconditionFailed, ValueError):
        raised = True
    assert raised
    assert storage.delete("captures/c/b.bin") is True
    assert storage.exists("captures/c/a.bin")
    signed = storage.signed_put_url("uploads/c/a.bin", "text/plain", "md5", 60)
    assert signed.url
    assert storage.signed_get_url("captures/c/a.bin", 60)


def test_local_and_volume_contract(tmp_path):
    _roundtrip(LocalFsStorage(tmp_path / "local"))
    volume = VolumeStorage(tmp_path / "vol", "http://testserver", b"secret")
    _roundtrip(volume)
    put = volume.signed_put_url("uploads/c/a.bin", "text/plain", "md5==", 60)
    token = put.url.rsplit("/", 1)[-1]
    from server.common.backends.storage_volume import read_token

    payload = read_token(token, b"secret")
    assert payload["op"] == "put"
    assert payload["key"] == "uploads/c/a.bin"


def test_gcs_v4_signing_kwargs():
    class Creds:
        valid = True
        token = "ya29.test"

    class Blob:
        def generate_signed_url(self, **kwargs):
            self.kwargs = kwargs
            return "https://storage.example/signed"

    blob = Blob()

    class Bucket:
        def blob(self, _key):
            return blob

    class Client:
        def bucket(self, _name):
            return Bucket()

    storage = GcsStorage(
        "hss-captures-dev", Creds(), "hss-runtime-dev@example.iam.gserviceaccount.com", client=Client()
    )
    signed = storage.signed_put_url("uploads/c/a.jpg", "image/jpeg", "md5value==", 3600)
    assert signed.url.startswith("https://")
    assert blob.kwargs["version"] == "v4"
    assert blob.kwargs["method"] == "PUT"
    assert blob.kwargs["content_md5"] == "md5value=="
    assert blob.kwargs["content_type"] == "image/jpeg"
    assert blob.kwargs["service_account_email"] == "hss-runtime-dev@example.iam.gserviceaccount.com"
    assert blob.kwargs["access_token"] == "ya29.test"
    assert blob.kwargs["expiration"] == timedelta(seconds=3600)
    assert isinstance(signed.expires_at, datetime)


def test_promotion_copies_listed_only(client):
    packet_id = "packet-promote"
    created = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    capture_id = created.json()["captureId"]
    listed, extra = b"listed-file", b"extra-file"
    from server.api.tests.test_ingest import _put_commit

    _put_commit(client, capture_id, "stills/s1.jpg", listed, meta={"still": "s1"})
    _put_commit(client, capture_id, "stills/extra.jpg", extra, meta={"still": "extra"})
    before = client.app_state.state.get_file(capture_id, "stills/s1.jpg").generation
    packet = photo_packet(packet_id, [("stills/s1.jpg", listed)])
    raw = json.dumps(packet).encode()
    done = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={"Idempotency-Key": packet_id, "Content-Type": "application/json", "X-Packet-Sha256": sha256_hex(raw)},
    )
    assert done.status_code == 202, done.text
    record = client.app_state.state.get_file(capture_id, "stills/s1.jpg")
    assert record.key == final_key(capture_id, "stills/s1.jpg")
    assert record.generation != before
    assert client.app_state.storage.exists(final_key(capture_id, "stills/s1.jpg"))
    assert client.app_state.storage.exists(upload_key(capture_id, "stills/s1.jpg"))
    assert client.app_state.storage.exists(upload_key(capture_id, "stills/extra.jpg"))
    assert not client.app_state.storage.exists(final_key(capture_id, "stills/extra.jpg"))


def test_volume_sweeper_ages_uploads(client, tmp_path):
    root = client.app_state.settings.VOLUME_ROOT
    uploads = os.path.join(root, "uploads", "cap_old")
    os.makedirs(uploads, exist_ok=True)
    old = os.path.join(uploads, "old.bin")
    fresh = os.path.join(uploads, "fresh.bin")
    open(old, "wb").write(b"old")
    open(fresh, "wb").write(b"new")
    old_time = time.time() - 8 * 86400
    fresh_time = time.time() - 6 * 86400
    os.utime(old, (old_time, old_time))
    os.utime(fresh, (fresh_time, fresh_time))
    parts = os.path.join(root, "uploads", "cap_old", "blob.bin.parts")
    os.makedirs(parts, exist_ok=True)
    os.utime(parts, (time.time() - 2 * 86400, time.time() - 2 * 86400))
    capture = client.app_state.state.get_capture(
        client.post(
            "/v1/captures", json=create_body("packet-sweep"), headers={"Idempotency-Key": "packet-sweep"}
        ).json()["captureId"]
    )
    capture.finalize_by = "2000-01-01T00:00:00.000Z"
    capture.status = "uploading"
    client.app_state.state.put_capture(capture)
    ingest_sweep(client.app_state)
    assert not os.path.exists(old)
    assert os.path.exists(fresh)
    assert not os.path.exists(parts)
    assert client.app_state.state.get_capture(capture.capture_id).status == "expired"
    _ = urlparse
    _ = StageRecord
    _ = tmp_path


def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


class _GatedLock:
    """Lets every acquire through except the first commit's, which it holds.

    The hold happens before the real lock is taken, so the held writer blocks
    nothing else. Reentrant acquires from the committing thread itself pass
    through untouched.
    """

    def __init__(self, real: threading.RLock, gate: _HoldFirstCommitter) -> None:
        self._real = real
        self._gate = gate
        self._owner: int | None = None
        self._depth = 0

    def acquire(self, *args, **kwargs) -> bool:
        me = threading.get_ident()
        if self._depth and self._owner == me:
            self._depth += 1
            return self._real.acquire(*args, **kwargs)
        gate = self._gate
        if getattr(gate.tl, "committing", None) == gate.key and not gate.held.is_set():
            gate.held.set()
            deadline = time.monotonic() + gate.timeout
            while gate.completed == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
        got = self._real.acquire(*args, **kwargs)
        if got:
            self._owner = me
            self._depth += 1
        return got

    def release(self) -> None:
        self._depth -= 1
        if not self._depth:
            self._owner = None
        self._real.release()

    def __enter__(self) -> _GatedLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class _HoldFirstCommitter:
    """Deterministic scheduler for two overlapping commits on one key.

    The first committer is held at the metadata lock until a second commit has
    fully completed — the interleave an old in-flight upload racing a
    re-register produces when commits run on more than one thread. Holding the
    first committer *after* its final-file replace but *before* its metadata
    write is only possible when the replace sits outside the lock; once the
    two are serialized, the hold lands before the replace and both writers
    commit atomically.
    """

    def __init__(self, key: str, timeout: float = 10.0) -> None:
        self.key = key
        self.timeout = timeout
        self.completed = 0
        self.held = threading.Event()
        self.tl = threading.local()

    def install(self, storage) -> None:
        original = type(storage).commit_tmp

        def commit_tmp(key, tmp, content_type, md5_b64, nbytes, _storage=storage, _original=original):
            self.tl.committing = key
            try:
                _original(_storage, key, tmp, content_type, md5_b64, nbytes)
            finally:
                if key == self.key:
                    self.completed += 1
                self.tl.committing = None

        storage.commit_tmp = commit_tmp
        storage._lock = _GatedLock(storage._lock, self)


def test_local_sweeper_ages_stale_single_attempt_temps(tmp_path):
    """LocalFsStorage mints the same per-attempt temporaries as VolumeStorage —
    one file under .uploads/singles per PUT attempt, outside the uploads walk —
    so the sweeper's `*.uploading` pattern covers both backends. The old
    per-key `{key}.uploading` name sat beside the object under root and gave
    two overlapping attempts a single shared file."""
    root = tmp_path / "local"
    storage = LocalFsStorage(root)
    key = "uploads/cap/local.jpg"
    first = storage.prepare_tmp(key)
    second = storage.prepare_tmp(key)
    assert first.parent == root / ".uploads" / "singles", "a local temp must land where the sweeper sweeps"
    assert first.name.endswith(".uploading") and second.name.endswith(".uploading")
    assert first != second, "two attempts for one key must not share a temp file"
    assert storage.list_prefix("uploads/") == [], "a temp file must not list as an object"

    stale = root / ".uploads" / "singles" / "killed-attempt.uploading"
    stale.write_bytes(b"process died between prepare_tmp and commit_tmp")
    stale_time = time.time() - 8 * 86400
    os.utime(stale, (stale_time, stale_time))
    deleted = _sweep_volume(root)
    assert deleted >= 1, "the sweep must count the stale local temporary"
    assert not stale.exists(), "a stale local per-attempt temporary must be swept"
    assert first.exists() and second.exists(), "a fresh per-attempt temporary must survive"


def test_volume_commit_tmp_leaves_bytes_and_metadata_agreeing_under_two_writers(tmp_path):
    """Two overlapping commits for one key must not mix bytes and metadata.

    The volume path hands every PUT attempt its own temp file, so an old
    upload still in flight and a re-registered upload can both reach
    commit_tmp. Whichever commits last must win the bytes AND the metadata
    together; head() reporting one writer's digest and size while the volume
    holds the other's bytes makes files:commit mark a valid upload as a
    mismatch.
    """
    storage = VolumeStorage(tmp_path / "vol", "http://testserver", b"secret")
    key = "uploads/cap/f.jpg"
    old = b"old upload bytes still in flight"
    new = b"re-registered replacement bytes!"
    gate = _HoldFirstCommitter(key)
    gate.install(storage)

    def commit(data: bytes) -> None:
        tmp = storage.prepare_tmp(key)
        tmp.write_bytes(data)
        storage.commit_tmp(key, tmp, "image/jpeg", _md5_b64(data), len(data))

    first = threading.Thread(target=commit, args=(old,))
    first.start()
    assert gate.held.wait(5), "the first committer never reached the metadata lock"
    second = threading.Thread(target=commit, args=(new,))
    second.start()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive(), "a committer deadlocked"

    stored = storage.get_bytes(key)
    meta = storage.head(key)
    assert stored in (old, new)
    assert meta.bytes == len(stored)
    assert meta.md5_b64 == _md5_b64(stored), "head() reports metadata from another writer than the committed bytes"


def test_volume_sweeper_ages_stale_single_attempt_temps(client):
    """Per-attempt upload temporaries live outside the uploads walk — in
    .uploads/singles — so the sweeper must visit them there. A process killed
    between prepare_tmp and commit_tmp otherwise strands the file forever."""
    root = client.app_state.settings.VOLUME_ROOT
    singles = os.path.join(root, ".uploads", "singles")
    os.makedirs(singles, exist_ok=True)
    stale = os.path.join(singles, "stale-attempt.uploading")
    live = os.path.join(singles, "live-attempt.uploading")
    open(stale, "wb").write(b"abandoned attempt")
    open(live, "wb").write(b"attempt still settling")
    stale_time = time.time() - 8 * 86400
    live_time = time.time() - 6 * 86400
    os.utime(stale, (stale_time, stale_time))
    os.utime(live, (live_time, live_time))
    ingest_sweep(client.app_state)
    assert not os.path.exists(stale), "a stale per-attempt temporary must be swept"
    assert os.path.exists(live), "a fresh per-attempt temporary must survive"
