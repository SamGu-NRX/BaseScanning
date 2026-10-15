"""Result lookups read the capture's run index, not every Dict key."""

from __future__ import annotations

from server.common.backends.state_modal_dict import ModalDictState
from server.common.tests.test_modal_dict_persist import _capture, _run


class _NoScan(dict):
    def keys(self):
        raise AssertionError("full dict scan")


def test_runs_for_capture_point_reads_after_put() -> None:
    state = ModalDictState(_NoScan())
    state.put_run(_run("cap_a", "run_a"))
    state.put_run(_run("cap_b", "run_b"))
    found = state.runs_for_capture("cap_a")
    assert [rec.run_id for rec in found] == ["run_a"]


def test_legacy_capture_is_indexed_once() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    mapping["run:run_old"] = _run("cap_old", "run_old").model_dump(mode="json")
    assert "capture_runs:cap_old" not in mapping
    found = state.runs_for_capture("cap_old")
    assert [rec.run_id for rec in found] == ["run_old"]
    assert mapping["capture_runs:cap_old"] == ["run_old"]
    scans = {"n": 0}
    real_items = state._items

    def counting():
        scans["n"] += 1
        return real_items()

    state._items = counting  # type: ignore[method-assign]
    again = state.runs_for_capture("cap_old")
    assert [rec.run_id for rec in again] == ["run_old"]
    assert scans["n"] == 0


def test_status_update_does_not_hide_a_legacy_run() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    mapping["capture:cap_old"] = _capture("cap_old").model_dump(mode="json")
    mapping["run:run_old"] = _run("cap_old", "run_old").model_dump(mode="json")
    state.set_status("cap_old", "processing")
    assert "capture_runs:cap_old" not in mapping
    found = state.runs_for_capture("cap_old")
    assert [rec.run_id for rec in found] == ["run_old"]


def test_empty_index_without_a_trust_marker_is_rebuilt() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    mapping["run:run_old"] = _run("cap_old", "run_old").model_dump(mode="json")
    mapping["capture_runs:cap_old"] = []
    found = state.runs_for_capture("cap_old")
    assert [rec.run_id for rec in found] == ["run_old"]
    assert mapping["capture_runs_trusted:cap_old"] is True


def test_new_capture_lookup_does_not_scan() -> None:
    state = ModalDictState(_NoScan())
    state.put_capture(_capture("cap_new"))
    assert state.runs_for_capture("cap_new") == []


def test_open_captures_are_a_point_read() -> None:
    state = ModalDictState({})
    state.put_capture(_capture("cap_open"))
    state.put_capture(_capture("cap_other").model_copy(update={"owner_principal": "other"}))
    assert state.open_capture_ids("device") == ["cap_open"]
    scans = {"n": 0}
    real_items = state._items

    def counting():
        scans["n"] += 1
        return real_items()

    state._items = counting  # type: ignore[method-assign]
    assert state.open_capture_ids("device") == ["cap_open"]
    state.set_status("cap_open", "processing")
    assert state.open_capture_ids("device") == []
    assert scans["n"] == 0


def test_legacy_open_captures_are_indexed_once() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    mapping["capture:cap_old"] = _capture("cap_old").model_dump(mode="json")
    mapping["capture:cap_done"] = _capture("cap_done").model_copy(update={"status": "complete"}).model_dump(mode="json")
    assert state.open_capture_ids("device") == ["cap_old"]
    assert mapping["open_backfill:device"] is True
    scans = {"n": 0}
    real_items = state._items

    def counting():
        scans["n"] += 1
        return real_items()

    state._items = counting  # type: ignore[method-assign]
    assert state.open_capture_ids("device") == ["cap_old"]
    assert scans["n"] == 0
    assert state.open_capture_ids("nobody") == []
    assert scans["n"] == 1
    assert state.open_capture_ids("nobody") == []
    assert scans["n"] == 1


def test_premature_open_list_still_backfills() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    mapping["open_by_owner:device"] = ["cap_partial"]
    mapping["capture:cap_old"] = _capture("cap_old").model_dump(mode="json")
    assert set(state.open_capture_ids("device")) == {"cap_partial", "cap_old"}
    scans = {"n": 0}
    real_items = state._items

    def counting():
        scans["n"] += 1
        return real_items()

    state._items = counting  # type: ignore[method-assign]
    assert set(state.open_capture_ids("device")) == {"cap_partial", "cap_old"}
    assert scans["n"] == 0


def test_concurrent_creates_keep_every_open_id() -> None:
    import threading

    class _Atomic:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._data: dict[str, object] = {}

        def get(self, key: str, default: object = None) -> object:
            with self._lock:
                return self._data.get(key, default)

        def __setitem__(self, key: str, value: object) -> None:
            with self._lock:
                self._data[key] = value

        def __contains__(self, key: object) -> bool:
            with self._lock:
                return key in self._data

        def pop(self, key: str, default: object = None) -> object:
            with self._lock:
                return self._data.pop(key, default)

        def keys(self) -> list[str]:
            with self._lock:
                return list(self._data)

        def put(self, key: str, value: object, skip_if_exists: bool = False) -> bool:
            with self._lock:
                if skip_if_exists and key in self._data:
                    return False
                self._data[key] = value
                return True

    mapping = _Atomic()
    states = [ModalDictState(mapping), ModalDictState(mapping)]
    barrier = threading.Barrier(8)

    def create(index: int) -> None:
        barrier.wait(timeout=5)
        states[index % 2].put_capture(_capture(f"cap_{index}"))

    threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert set(states[0].open_capture_ids("device")) == {f"cap_{index}" for index in range(8)}
