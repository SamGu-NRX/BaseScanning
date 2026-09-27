"""LocalFsStorage against the storage protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.common.backends.storage_local import LocalFsStorage


def test_put_get_copy_and_prefix(tmp_path: Path) -> None:
    store = LocalFsStorage(tmp_path)
    store.put_bytes("captures/c1/a.jpg", b"aaa", "image/jpeg")
    store.put_bytes("captures/c1/b.jpg", b"bbbb", "image/jpeg")
    assert store.get_bytes("captures/c1/a.jpg") == b"aaa"
    meta = store.head("captures/c1/a.jpg")
    assert meta.bytes == 3 and meta.md5_b64
    copied = store.copy("captures/c1/a.jpg", "captures/c1/c.jpg", if_generation_match=meta.generation)
    assert store.get_bytes("captures/c1/c.jpg") == b"aaa"
    assert copied.generation != meta.generation
    assert store.list_prefix("captures/c1/") == ["captures/c1/a.jpg", "captures/c1/b.jpg", "captures/c1/c.jpg"]
    assert store.delete_prefix("captures/c1/") == 3
    assert store.exists("captures/c1/a.jpg") is False


def test_signed_url_is_file_uri(tmp_path: Path) -> None:
    store = LocalFsStorage(tmp_path)
    signed = store.signed_put_url("uploads/c1/a.jpg", "image/jpeg", "md5", 60)
    assert signed.url.startswith("file://")
    assert signed.headers["Content-Type"] == "image/jpeg"
    assert store.signed_get_url("uploads/c1/a.jpg", 60).startswith("file://")


def test_multipart_round_trip(tmp_path: Path) -> None:
    store = LocalFsStorage(tmp_path)
    upload_id = store.initiate_multipart("captures/c1/big.bin", "application/octet-stream")
    part = store.signed_part_url("captures/c1/big.bin", upload_id, 1, 60)
    Path(part.url.removeprefix("file://")).write_bytes(b"xyz")
    meta = store.complete_multipart("captures/c1/big.bin", upload_id, [(1, "etag")])
    assert store.get_bytes("captures/c1/big.bin") == b"xyz"
    assert meta.bytes == 3


def test_multipart_upload_id_is_minted_form(tmp_path: Path) -> None:
    root = tmp_path / "root"
    store = LocalFsStorage(root)
    store.initiate_multipart("captures/c1/big.bin", "application/octet-stream")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep")
    with pytest.raises(ValueError):
        store.abort_multipart("k", "../../outside")
    assert victim.read_text() == "keep"
    assert outside.is_dir()
    with pytest.raises(ValueError):
        store.signed_part_url("k", "../../outside", 1, 60)
    with pytest.raises(ValueError):
        store.complete_multipart("k", "not-an-id", [(1, "etag")])
