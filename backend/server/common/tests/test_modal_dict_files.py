"""Concurrent upsert_file writers keep every index entry."""

from __future__ import annotations

import threading
import time

from server.common.backends.state_modal_dict import ModalDictState
from server.contracts.jobs import FileRecord


class _ReadGate:
    """Hold each concurrent pair of readers until both have entered.

    A thread arriving alone waits briefly and proceeds (the leased, serialized
    path); two threads arriving together are released with the same snapshot,
    which is exactly the stale-read interleaving the index merge must survive.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._waiting = 0
        self.enabled = True

    def hold(self) -> None:
        if not self.enabled:
            return
        with self._cond:
            self._waiting += 1
            self._cond.notify_all()
            if self._waiting >= 2:
                self._cond.notify_all()
                return
            deadline = time.monotonic() + 0.05
            while self._waiting < 2 and time.monotonic() < deadline:
                self._cond.wait(deadline - time.monotonic())
            self._waiting = max(0, self._waiting - 1)


class _FakeModalDict:
    """modal.Dict stand-in: point get, write-through setitem, atomic skip_if_exists put."""

    def __init__(self) -> None:
        self.store: dict = {}
        self._put_lock = threading.Lock()
        self.gate = _ReadGate()

    def get(self, key):
        # Capture the snapshot BEFORE the GIL is released by the gate hold: the
        # unfixed read-modify-write has no GIL release between read and write, so
        # a hold placed after the read would never produce a stale snapshot.
        value = self.store.get(key)
        if key.startswith("files:"):
            self.gate.hold()
        return value

    def __contains__(self, key) -> bool:
        return key in self.store

    def __setitem__(self, key, value) -> None:
        self.store[key] = value

    def __delitem__(self, key) -> None:
        del self.store[key]

    def pop(self, key, default=None):
        if key not in self.store:
            return default
        value = self.store[key]
        del self.store[key]
        return value

    def put(self, key, value, skip_if_exists: bool = False) -> bool:
        with self._put_lock:
            if skip_if_exists:
                if key in self.store:
                    return False
                self.store[key] = value
                return True
            self.store[key] = value
            return True

    def keys(self):
        return list(self.store.keys())


def _file(capture_id: str, path: str) -> FileRecord:
    return FileRecord(
        capture_id=capture_id,
        path=path,
        role="still",
        bytes=3,
        sha256="a" * 64,
        md5="b",
        content_type="image/jpeg",
        state="committed",
    )


def test_two_concurrent_writers_keep_every_index_entry() -> None:
    mapping = _FakeModalDict()
    left = ModalDictState(mapping)
    right = ModalDictState(mapping)
    start = threading.Barrier(2)

    def register(state: ModalDictState, tag: str) -> None:
        start.wait()
        for index in range(20):
            state.upsert_file(_file("cap_1", f"{tag}/s{index}.jpg"))

    threads = [
        threading.Thread(target=register, args=(left, "left")),
        threading.Thread(target=register, args=(right, "right")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    mapping.gate.enabled = False
    paths = [rec.path for rec in left.files("cap_1")]
    expected = {f"{tag}/s{index}.jpg" for tag in ("left", "right") for index in range(20)}
    assert set(paths) == expected
    assert len(paths) == 40
