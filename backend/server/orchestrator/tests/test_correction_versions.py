"""The immutable sequences remain safe without exclusive lease ownership."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server.common.backends.state_memory import MemoryState
from server.common.backends.state_modal_dict import ModalDictState
from server.common.tests.test_modal_dict_files import _FakeModalDict
from server.orchestrator.corrections import (
    correction_snapshot,
    latest_publication,
    pending_corrections,
    publish_version,
    record_correction,
)
from server.orchestrator.tests.conftest import build_services
from server.orchestrator.tests.test_greptile_followup import _run


@pytest.fixture(params=["memory", "modal"])
def state(request):
    if request.param == "memory":
        return MemoryState()
    return ModalDictState(_FakeModalDict())


def correction(identifier, field="meter_number", value="12345"):
    return {
        "confirmationId": identifier,
        "fieldId": field,
        "kind": "corrected",
        "value": value,
        "imageId": "s1",
        "by": "device",
    }


def test_competing_appenders_retry_the_same_empty_slot(state, monkeypatch):
    gate = Barrier(2)
    original = state.put_if_absent

    def insert(key, value):
        if key == "capture_correction:cap_test:1":
            gate.wait(timeout=5)
        return original(key, value)

    monkeypatch.setattr(state, "put_if_absent", insert)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(record_correction, state, "cap_test", correction(name)) for name in ("a", "b")]
        assert sorted(f.result(timeout=5) for f in futures) == [1, 2]
    assert {state.get_kv(f"capture_correction:cap_test:{n}")["confirmationId"] for n in (1, 2)} == {"a", "b"}
    assert state.get_kv("capture_correction:cap_test:3") is None


def test_replay_is_idempotent_and_reused_identity_fails(state):
    row = correction("a")
    assert record_correction(state, "cap_test", row) == 1
    assert record_correction(state, "cap_test", row) == 1
    with pytest.raises(ValueError, match="reused"):
        record_correction(state, "cap_test", correction("a", value="67890"))


def test_old_composer_cannot_append_after_newer_coverage(state):
    newest = publish_version(state, "run_test", {"correctionIds": ["a", "b"], "artifacts": {"result": "new"}})
    assert publish_version(state, "run_test", {"correctionIds": ["a"], "artifacts": {"result": "old"}}) == newest
    assert latest_publication(state, "run_test") == newest
    assert state.get_kv("result_version:run_test:1") == newest
    assert state.get_kv("result_version:run_test:2") is None


def test_crash_between_capture_and_run_record_is_recoverable(tmp_path, state, monkeypatch):
    services, _, _ = build_services(tmp_path, "memory")
    run = _run(services, "run_test")
    original = state.put_if_absent

    def crash(key, value):
        if key.startswith("run_correction:"):
            raise OSError("process stopped before run copy")
        return original(key, value)

    monkeypatch.setattr(state, "put_if_absent", crash)
    with pytest.raises(OSError, match="process stopped"):
        record_correction(state, run.capture_id, correction("a"), run)
    monkeypatch.setattr(state, "put_if_absent", original)
    assert pending_corrections(state, run)
    assert correction_snapshot(state, run) == [correction("a")]
    publish_version(state, run.run_id, {"correctionIds": ["a"], "artifacts": {}})
    assert not pending_corrections(state, run)
