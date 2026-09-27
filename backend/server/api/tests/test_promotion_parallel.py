"""Parallel promotion must not race the local volume index."""

from __future__ import annotations

from server.api.deps import save_extras
from server.api.keys import final_key, upload_key
from server.api.promotion import promote
from server.api.tests.test_ingest import _create
from server.contracts.jobs import FileRecord


def test_parallel_promotion_of_two_hundred_files(client) -> None:
    capture_id = _create(client, "packet-promote-parallel")
    deps = client.app_state
    deps.settings.HSS_INGEST_PROMOTE_CONCURRENCY = 32
    paths = []
    for index in range(200):
        path = f"stills/s{index:04d}.jpg"
        data = f"jpeg-{index}".encode()
        deps.storage.put_bytes(upload_key(capture_id, path), data, "image/jpeg")
        meta = deps.storage.head(upload_key(capture_id, path))
        deps.state.upsert_file(
            FileRecord(
                capture_id=capture_id,
                path=path,
                role="still",
                bytes=len(data),
                sha256="ab" * 32,
                md5=meta.md5_b64,
                content_type="image/jpeg",
                state="committed",
                generation=meta.generation,
            )
        )
        paths.append(path)
    save_extras(deps, capture_id, {"paths": paths})
    promote(deps, deps.state.get_capture(capture_id))
    for index, path in enumerate(paths):
        assert deps.storage.get_bytes(final_key(capture_id, path)) == f"jpeg-{index}".encode()
