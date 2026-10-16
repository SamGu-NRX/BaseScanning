"""Stage-record recovery claims are atomic: a record another coordinator
materialized and started inside the race window is returned untouched, never
reset to pending with its attempt and worker identity stripped."""

from __future__ import annotations

import threading

from server.common.backends.state_memory import MemoryState
from server.common.backends.state_modal_dict import ModalDictState
from server.contracts.jobs import StageRecord


def _rec(run_id: str, stage: str, worker: str | None = None) -> StageRecord:
    return StageRecord(
        run_id=run_id,
        stage=stage,
        status="pending",
        attempt=1,
        heartbeat="2026-10-16T00:00:00Z",
        worker_call_id=worker,
    )


def test_memory_claim_returns_the_existing_record_untouched() -> None:
    state = MemoryState()
    first = state.create_stage_if_absent(_rec("run", "poses", worker="left"))
    assert first.worker_call_id == "left"

    second = state.create_stage_if_absent(_rec("run", "poses", worker="right"))

    assert second.worker_call_id == "left", "the loser must read back the winner"
    stored = state.get_stage("run", "poses")
    assert stored.worker_call_id == "left" and stored.attempt == 1


def test_memory_claim_inserts_when_absent() -> None:
    state = MemoryState()
    created = state.create_stage_if_absent(_rec("run", "poses"))
    assert created.status == "pending" and created.attempt == 1
    assert state.get_stage("run", "poses") == created


def test_modal_claim_two_states_share_one_map() -> None:
    mapping: dict[str, object] = {}
    left, right = ModalDictState(mapping), ModalDictState(mapping)
    winner = left.create_stage_if_absent(_rec("run", "poses", worker="left"))
    loser = right.create_stage_if_absent(_rec("run", "poses", worker="right"))
    assert winner.worker_call_id == "left"
    assert loser.worker_call_id == "left", "the loser must read back the winner"
    assert right.get_stage("run", "poses").worker_call_id == "left"


def test_modal_claim_many_threads_one_winner() -> None:
    state = ModalDictState()
    results: list[str] = []
    guard = threading.Lock()

    def claim(worker: str) -> None:
        rec = state.create_stage_if_absent(_rec("run", "poses", worker=worker))
        with guard:
            results.append(rec.worker_call_id)

    threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1, f"every claimant must read back the same winner: {results}"
    stored = state.get_stage("run", "poses")
    assert stored.worker_call_id == results[0] and stored.attempt == 1
