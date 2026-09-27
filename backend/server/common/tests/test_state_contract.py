"""MemoryState against the state protocol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.common.backends.state_memory import MemoryState
from server.contracts.jobs import CaptureRecord, FileRecord, RunRecord, StageRecord


def _capture(status: str = "uploading") -> CaptureRecord:
    return CaptureRecord(
        capture_id="cap_1",
        packet_id="packet-1234",
        status=status,  # type: ignore[arg-type]
        tier="arkit",
        flow="guided",
        device_model="iPhone",
        owner_principal="device",
        created_at="2026-09-26T00:00:00Z",
        finalize_by="2026-09-27T00:00:00Z",
    )


def test_open_captures_events_and_tomb() -> None:
    state = MemoryState()
    state.put_capture(_capture())
    state.put_capture(_capture().model_copy(update={"capture_id": "cap_2", "status": "processing"}))
    assert [rec.capture_id for rec in state.list_open_captures()] == ["cap_1"]
    state.upsert_file(
        FileRecord(
            capture_id="cap_1",
            path="a.jpg",
            role="still",
            bytes=3,
            sha256="a" * 64,
            md5="b",
            content_type="image/jpeg",
            state="pending",
        )
    )
    assert state.get_file("cap_1", "a.jpg") is not None
    first = state.append_event("cap_1", "files_committed", {"paths": ["a.jpg"], "committed": 1})
    second = state.append_event("cap_1", "hint", {"code": "meter_close", "message": "closer"})
    assert first.seq == 1 and second.seq == 2
    assert [rec.seq for rec in state.events_after("cap_1", 1)] == [2]
    assert state.put_if_absent("idem:1", {"ok": True}) is True
    assert state.put_if_absent("idem:1", {"ok": False}) is False
    state.delete_capture("cap_1")
    assert state.get_capture("cap_1") is None
    assert state.get_kv("tomb:cap_1") == {"capture_id": "cap_1"}


def test_runs_heartbeat_and_spend() -> None:
    state = MemoryState()
    started = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.put_run(
        RunRecord(
            run_id="run_abc",
            capture_id="cap_1",
            packet_sha256="a" * 64,
            status="running",
            branch="image",
            flow="guided",
            tier="arkit",
            profile_default="C",
            created_at="2026-09-26T00:00:00Z",
            deadline_at="2026-09-26T01:00:00Z",
            started_at=started,
            artifacts={},
        )
    )
    old = (datetime.now(UTC) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.upsert_stage(StageRecord(run_id="run_abc", stage="poses", status="running", attempt=1, heartbeat=old))
    assert state.heartbeat("run_abc", "poses", "call-1", "match", "2026-09-26T00:10:00Z") is True
    assert state.heartbeat("run_abc", "poses", "call-2", "match", "2026-09-26T00:11:00Z") is False
    assert state.get_stage("run_abc", "poses").substep == "match"  # type: ignore[union-attr]
    assert state.add_spend("total", 0.1) == 0.1
    assert state.get_spend("total") == 0.1
    assert state.list_runs(status={"running"}, started_before=datetime.now(UTC))


def test_stale_stages_are_running_only() -> None:
    state = MemoryState()
    old = (datetime.now(UTC) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.upsert_stage(StageRecord(run_id="run_abc", stage="poses", status="running", attempt=1, heartbeat=old))
    state.upsert_stage(StageRecord(run_id="run_abc", stage="coverage", status="pending", attempt=1, heartbeat=old))
    state.upsert_stage(StageRecord(run_id="run_abc", stage="scale", status="done", attempt=1, heartbeat=old))
    state.upsert_stage(StageRecord(run_id="run_abc", stage="dense", status="failed", attempt=1, heartbeat=old))
    state.upsert_stage(StageRecord(run_id="run_abc", stage="scene", status="skipped", attempt=1, heartbeat=old))
    assert [rec.stage for rec in state.stale_stages(30)] == ["poses"]
