"""State contract on the memory backend and the Modal Dict backend."""

from __future__ import annotations

import threading

import pytest

from server.contracts.jobs import (
    CaptureRecord,
    RunRecord,
    StageRecord,
)
from server.orchestrator.tests.conftest import open_state


@pytest.mark.parametrize("backend", ["memory", "modal_dict"])
def test_run_roundtrip_and_spend(backend: str) -> None:
    state = open_state(backend)
    state.put_capture(
        CaptureRecord(
            capture_id="cap_a",
            packet_id="packet-a",
            status="processing",
            tier="arkit",
            flow="guided",
            device_model="iPhone",
            owner_principal="t",
            created_at="2026-09-26T00:00:00+00:00",
            finalize_by="2026-09-27T00:00:00+00:00",
        )
    )
    state.set_status("cap_a", "manual_review")
    assert state.get_capture("cap_a").status == "manual_review"
    run = RunRecord(
        run_id="run_abc",
        capture_id="cap_a",
        packet_sha256="a" * 64,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at="2026-09-26T00:00:00+00:00",
        deadline_at="2026-09-26T00:20:00+00:00",
        artifacts={},
    )
    state.put_run(run)
    assert state.get_run("run_abc").capture_id == "cap_a"
    assert state.runs_for_capture("cap_a")[0].run_id == "run_abc"
    assert state.add_spend("total", 1.25) == 1.25
    assert state.add_spend("total", 0.25) == 1.5
    assert state.put_if_absent("once", {"ok": True}) is True
    assert state.put_if_absent("once", {"ok": False}) is False


@pytest.mark.parametrize("backend", ["memory", "modal_dict"])
def test_seq_strictly_increasing(backend: str) -> None:
    state = open_state(backend)
    seqs = [state.append_event("cap_seq", "hint", {"code": "frames_blurry", "message": "m"}).seq for _ in range(40)]
    assert seqs == list(range(1, 41))


def test_seq_strictly_increasing_under_eight_emitters() -> None:
    """The dict backend locks ``seq``. ``MemoryState`` does not, so this stays on ``modal_dict``."""
    state = open_state("modal_dict")
    seqs: list[int] = []
    lock = threading.Lock()

    def emit() -> None:
        for _ in range(20):
            rec = state.append_event("cap_seq8", "hint", {"code": "frames_blurry", "message": "m"})
            with lock:
                seqs.append(rec.seq)

    threads = [threading.Thread(target=emit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ordered = sorted(seqs)
    assert len(ordered) == 160
    assert ordered == sorted(set(ordered))
    assert all(ordered[i + 1] > ordered[i] for i in range(len(ordered) - 1))


@pytest.mark.parametrize("backend", ["memory", "modal_dict"])
def test_heartbeat_superseded(backend: str) -> None:
    state = open_state(backend)
    state.put_run(
        RunRecord(
            run_id="run_h",
            capture_id="cap_h",
            packet_sha256="b" * 64,
            status="running",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00+00:00",
            deadline_at="2026-09-26T00:20:00+00:00",
            artifacts={},
        )
    )
    rec = StageRecord(
        run_id="run_h",
        stage="poses",
        status="running",
        attempt=1,
        heartbeat="2026-09-26T00:00:00+00:00",
        worker_call_id="call-a",
    )
    state.upsert_stage(rec)
    assert state.heartbeat("run_h", "poses", "call-a", "sfm", "2026-09-26T00:00:01+00:00") is True
    assert state.heartbeat("run_h", "poses", "call-b", "sfm", "2026-09-26T00:00:02+00:00") is False
    assert state.get_stage("run_h", "poses").substep == "sfm"
