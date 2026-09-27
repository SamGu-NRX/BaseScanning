"""ModalDictState keeps records in the mapping, and delete_capture removes them."""

from __future__ import annotations

from server.common.backends.state_modal_dict import ModalDictState, PrefixKv
from server.contracts.jobs import CaptureRecord, FileRecord, RunRecord, StageRecord


def _capture(capture_id: str = "cap_1") -> CaptureRecord:
    return CaptureRecord(
        capture_id=capture_id,
        packet_id="packet-1234",
        status="uploading",
        tier="arkit",
        flow="guided",
        device_model="iPhone",
        owner_principal="device",
        created_at="2026-09-26T00:00:00Z",
        finalize_by="2026-09-27T00:00:00Z",
    )


def _file(capture_id: str = "cap_1") -> FileRecord:
    return FileRecord(
        capture_id=capture_id,
        path="a.jpg",
        role="still",
        bytes=3,
        sha256="a" * 64,
        md5="b",
        content_type="image/jpeg",
        state="pending",
    )


def _run(capture_id: str = "cap_1", run_id: str = "run_abc") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        capture_id=capture_id,
        packet_sha256="a" * 64,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at="2026-09-26T00:00:00Z",
        deadline_at="2026-09-26T01:00:00Z",
        artifacts={},
    )


def test_modal_dict_state_survives_reconstruction() -> None:
    mapping: dict[str, object] = {}
    state = ModalDictState(mapping)
    state.put_capture(_capture())
    state.upsert_file(_file())
    state.append_event("cap_1", "files_committed", {"paths": ["a.jpg"], "committed": 1})
    state.put_run(_run())
    state.upsert_stage(
        StageRecord(run_id="run_abc", stage="poses", status="running", attempt=1, heartbeat="2026-09-26T00:00:00Z")
    )
    rebuilt = ModalDictState(mapping)
    assert rebuilt.get_capture("cap_1") is not None
    assert rebuilt.get_file("cap_1", "a.jpg") is not None
    assert rebuilt.events_after("cap_1", 0)[0].seq == 1
    assert rebuilt.get_run("run_abc") is not None
    assert rebuilt.get_stage("run_abc", "poses") is not None
    assert "capture:cap_1" in mapping
    assert "file:cap_1:a.jpg" in mapping
    assert mapping["files:cap_1"] == ["a.jpg"]


def test_prefix_isolates_two_states() -> None:
    shared = ModalDictState()
    left = ModalDictState(PrefixKv(shared.kv, "left/"))
    right = ModalDictState(PrefixKv(shared.kv, "right/"))
    left.put_capture(_capture())
    assert right.get_capture("cap_1") is None
    assert left.get_capture("cap_1") is not None
    assert "left/capture:cap_1" in shared.kv


def test_delete_capture_drops_keyed_records() -> None:
    state = ModalDictState()
    state.put_capture(_capture())
    state.put_capture(_capture("cap_2"))
    state.upsert_file(_file())
    state.append_event("cap_1", "hint", {"code": "meter_close", "message": "closer"})
    state.put_run(_run())
    state.upsert_stage(
        StageRecord(run_id="run_abc", stage="poses", status="running", attempt=1, heartbeat="2026-09-26T00:00:00Z")
    )
    state.put_kv("confirm:cap_1:panel", {"value": "sq"})
    state.put_kv("capturex:cap_1", {"device": "iPhone"})
    state.put_kv("idem:create:packet-1234", {"ref": "cap_1"})
    state.put_blob("cap_1/note", b"pii")
    state.delete_capture("cap_1")
    assert state.get_capture("cap_1") is None
    assert state.get_file("cap_1", "a.jpg") is None
    assert state.events_after("cap_1", 0) == []
    assert state.get_run("run_abc") is None
    assert state.get_stage("run_abc", "poses") is None
    assert state.get_kv("confirm:cap_1:panel") is None
    assert state.get_kv("capturex:cap_1") is None
    assert state.get_blob("cap_1/note") is None
    assert state.get_kv("tomb:cap_1") == {"capture_id": "cap_1"}
    assert state.get_kv("idem:create:packet-1234") == {"ref": "cap_1"}
    assert state.get_capture("cap_2") is not None
