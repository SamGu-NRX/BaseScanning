"""Concurrent appends on MemoryState keep a gap-free sequence."""

from __future__ import annotations

import threading

import pytest

from server.common.backends import state_memory
from server.common.backends.state_memory import MemoryState

_APPENDS_PER_PRODUCER = 40
_ROUNDS = 5


def _emit(
    state: MemoryState,
    capture_id: str,
    seqs: list[int],
    errors: list[Exception],
    guard: threading.Lock,
) -> None:
    try:
        for _ in range(_APPENDS_PER_PRODUCER):
            rec = state.append_event(capture_id, "hint", {"code": "frames_blurry", "message": "m"})
            rec.data["code"] = "mutated"
            with guard:
                seqs.append(rec.seq)
    except Exception as exc:
        with guard:
            errors.append(exc)


def _run_round(
    monkeypatch: pytest.MonkeyPatch,
    real_event_record: type,
    capture_id: str,
) -> tuple[list[int], list[Exception], MemoryState]:
    state = MemoryState()
    barrier = threading.Barrier(2)

    def gated_event_record(**kwargs: object):
        # Pre-fix, both producers reach event construction unlocked, meet at the
        # barrier and read the same tail. With the event lock held, the gate is
        # a no-op and the producers never meet.
        lock = getattr(state, "_event_lock", None)
        if lock is None or not lock.locked():
            barrier.wait(timeout=30)
        return real_event_record(**kwargs)

    monkeypatch.setattr(state_memory, "EventRecord", gated_event_record)
    seqs: list[int] = []
    errors: list[Exception] = []
    guard = threading.Lock()
    threads = [threading.Thread(target=_emit, args=(state, capture_id, seqs, errors, guard)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    monkeypatch.undo()
    return seqs, errors, state


def test_append_event_two_producers_stay_contiguous_unique_and_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    capture_id = "cap_seq2"
    total = 2 * _APPENDS_PER_PRODUCER
    real_event_record = state_memory.EventRecord
    for _round in range(_ROUNDS):
        seqs, errors, state = _run_round(monkeypatch, real_event_record, capture_id)
        assert errors == []
        ordered = sorted(seqs)
        assert ordered == list(range(1, total + 1))
        assert len(set(ordered)) == total
        assert [rec.seq for rec in state.events_after(capture_id, 0, limit=total + 10)] == list(range(1, total + 1))
        assert [rec.seq for rec in state.events_after(capture_id, 40)] == list(range(41, total + 1))
        assert [rec.seq for rec in state.events_after(capture_id, 40, limit=5)] == [41, 42, 43, 44, 45]
        stored = state.events_after(capture_id, 0, limit=1)[0]
        assert stored.data["code"] == "frames_blurry"
