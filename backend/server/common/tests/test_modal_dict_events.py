"""Concurrent appends on ModalDictState keep a gap-free sequence."""

from __future__ import annotations

import threading

from server.common.backends.state_modal_dict import ModalDictState


def _emit(state: ModalDictState, seqs: list[int], guard: threading.Lock) -> None:
    for _ in range(20):
        rec = state.append_event("cap_seq8", "hint", {"code": "frames_blurry", "message": "m"})
        with guard:
            seqs.append(rec.seq)


def _run_once(state: ModalDictState) -> None:
    seqs: list[int] = []
    guard = threading.Lock()
    threads = [threading.Thread(target=_emit, args=(state, seqs, guard)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ordered = sorted(seqs)
    assert ordered == list(range(1, 161))
    assert len(set(ordered)) == 160
    assert [rec.seq for rec in state.events_after("cap_seq8", 0, limit=200)] == list(range(1, 161))


def test_append_event_eight_threads_ten_runs() -> None:
    for _run in range(10):
        _run_once(ModalDictState())


def test_append_event_two_states_share_one_map() -> None:
    mapping: dict[str, object] = {}
    left = ModalDictState(mapping)
    right = ModalDictState(mapping)
    seqs: list[int] = []
    guard = threading.Lock()
    threads = [threading.Thread(target=_emit, args=(state, seqs, guard)) for state in (left, right) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(seqs) == list(range(1, 161))
    assert [rec.seq for rec in right.events_after("cap_seq8", 0, limit=200)] == list(range(1, 161))
