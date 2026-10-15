"""Storage contract, GCS signing, promotion, and the volume sweeper."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from server.api.keys import final_key, upload_key
from server.api.sweeper import ingest_sweep
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
