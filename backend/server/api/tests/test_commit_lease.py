"""A busy file-index lease must not strand committed files unannounced.

ModalDictState merges the per-capture file index under a strict lease; a lease
that stays busy raises LeaseBusy and the commit route answers a retryable 503.
Whatever the failed request already committed must still reach
``files_committed`` and ``on_files_committed``, and whatever it did not commit
must stay pending so the retry commits and announces it.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import pytest

from server.api.tests.conftest import sha256_hex
from server.api.tests.test_ingest import _create, _file
from server.common.backends import state_modal_dict
from server.common.backends.state_modal_dict import ModalDictState
from server.common.lease import LeaseBusy

_BLOBS = {"stills/s1.jpg": b"jpeg-one", "stills/s2.jpg": b"jpeg-two"}


@pytest.fixture
def shared(client, monkeypatch):
    deps = client.app_state
    deps.state = ModalDictState()
    monkeypatch.setattr(state_modal_dict, "_INDEX_LEASE_WAIT_S", 0.05)
    announced: list[list[str]] = []
    monkeypatch.setattr(
        deps.hooks, "on_files_committed", lambda _capture_id, files: announced.append([f.path for f in files])
    )
    return deps, announced


def _upload_all(client, capture_id: str) -> None:
    files = [_file(path, data, meta={"still": f"s{index + 1}"}) for index, (path, data) in enumerate(_BLOBS.items())]
    registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": files})
    assert registered.status_code == 200, registered.text
    for item, data in zip(registered.json()["files"], _BLOBS.values(), strict=True):
        target = item["upload"]
        put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
        assert put.status_code == 200, put.text


def _commit(client, capture_id: str):
    return client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": sha256_hex(data)} for path, data in _BLOBS.items()]},
    )


def _committed_events(client, capture_id: str) -> list[str]:
    events = client.get(f"/v1/captures/{capture_id}/events").json()["events"]
    return sorted(path for e in events if e["type"] == "files_committed" for path in e["data"]["paths"])


def test_a_busy_lease_saves_nothing_and_the_retry_announces(client, shared):
    deps, announced = shared
    capture_id = _create(client, "packet-lease-busy")
    _upload_all(client, capture_id)
    # Another container holds a fresh lease on this capture's file index.
    deps.state.put_if_absent(f"lease:files:{capture_id}", {"id": "other", "at": time.time()})

    busy = _commit(client, capture_id)
    assert busy.status_code == 503, busy.text
    assert busy.json()["errors"][0]["code"] == "lease_busy"
    for path in _BLOBS:
        assert deps.state.get_file(capture_id, path).state == "pending", "nothing may commit without the lease"

    deps.state.delete_kv(f"lease:files:{capture_id}")
    retried = _commit(client, capture_id)
    assert retried.status_code == 200, retried.text
    assert _committed_events(client, capture_id) == sorted(_BLOBS)
    assert sorted(path for batch in announced for path in batch) == sorted(_BLOBS)
    assert sorted(rec.path for rec in deps.state.files(capture_id)) == sorted(_BLOBS)


def test_files_committed_before_a_busy_lease_are_still_announced(client, shared, monkeypatch):
    deps, announced = shared
    capture_id = _create(client, "packet-lease-partial")
    _upload_all(client, capture_id)

    real_with_lease = state_modal_dict.with_lease
    calls = {"n": 0}

    def second_commit_busy(state, name, fn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise LeaseBusy(f"lease {name!r} stayed busy")
        return real_with_lease(state, name, fn, **kwargs)

    monkeypatch.setattr(state_modal_dict, "with_lease", second_commit_busy)
    busy = _commit(client, capture_id)
    assert busy.status_code == 503, busy.text
    # The first file committed before the lease went busy: it is announced now,
    # because the retry will read it as already committed.
    assert _committed_events(client, capture_id) == ["stills/s1.jpg"]
    assert deps.state.get_file(capture_id, "stills/s2.jpg").state == "pending"

    monkeypatch.setattr(state_modal_dict, "with_lease", real_with_lease)
    retried = _commit(client, capture_id)
    assert retried.status_code == 200, retried.text
    assert _committed_events(client, capture_id) == sorted(_BLOBS)
    assert sorted(path for batch in announced for path in batch) == sorted(_BLOBS)
