"""Old API workers' acknowledged slots survive immutable reads publication."""

from __future__ import annotations

import pytest

from server.orchestrator.api import start_run
from server.orchestrator.corrections import correction_snapshot, latest_publication, pending_corrections
from server.orchestrator.runner import run_stage
from server.orchestrator.services import bind
from server.orchestrator.stateio import put_json
from server.orchestrator.tests.conftest import build_services
from server.orchestrator.tests.test_immutable_correction_races import (
    accept,
    compose_context,
    prepare_result,
    request,
    served,
    setup_run,
)
from server.reads.types import Reads


def legacy_slot(services, identifier="legacy-answer"):
    services.state.put_kv("confirmix:cap_test", {"fields": ["meter_number"]})
    services.state.put_kv(
        "confirm:cap_test:meter_number",
        {
            "confirmationId": identifier,
            "fieldId": "meter_number",
            "imageId": "k0",
            "kind": "corrected",
            "value": "12345",
            "by": "device",
            "seq": 1,
            "receivedAt": "2026-10-15T00:00:00Z",
        },
    )


@pytest.mark.parametrize("real_reads", [False, True])
def test_legacy_answer_at_stage_start_reaches_result_exactly_once(tmp_path, monkeypatch, real_reads):
    import server.orchestrator.runner as runner
    import server.reads as reads

    services, run, base_key = setup_run(tmp_path)
    base = Reads.model_validate_json(services.storage.get_bytes(base_key))
    legacy_slot(services)
    original = runner.stage_callable
    if real_reads:
        monkeypatch.setattr(reads, "build_reads", lambda *_args: base.model_copy(deep=True))
        monkeypatch.setattr(
            runner, "stage_callable", lambda stage, **kw: reads.run if stage == "reads" else original(stage, **kw)
        )
    for attempt in ("first", "retry"):
        output = run_stage(compose_context(services, run, "reads", attempt), stubs=True)
        put_json(services.state, f"stage_artifacts:{run.run_id}:reads", output.artifacts)
    run_stage(prepare_result(services, run, "result"), stubs=True)
    assert served(services, run)["meter"]["value"] == "12345"
    assert latest_publication(services.state, run.run_id)["correctionIds"] == ["legacy-answer"]
    assert len(correction_snapshot(services.state, run)) == 1
    assert services.state.get_kv("capture_correction:cap_test:2") is None


def test_legacy_answer_before_run_creation_is_adopted(tmp_path):
    services, uri, digest = build_services(tmp_path, "memory")
    legacy_slot(services)
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert served(services, run)["meter"]["value"] == "12345"
    assert latest_publication(services.state, run.run_id)["correctionIds"] == ["legacy-answer"]
    assert not pending_corrections(services.state, run)


def test_interrupted_legacy_import_is_recovered_without_duplicate(tmp_path, monkeypatch):
    services, run, _ = setup_run(tmp_path)
    legacy_slot(services)
    original = services.state.put_if_absent
    claimed = []

    def crash(key, row):
        if key.startswith("claim:"):
            claimed.append(key)
            raise OSError("crashed before legacy assignment")
        return original(key, row)

    with monkeypatch.context() as patch:
        patch.setattr(services.state, "put_if_absent", crash)
        with pytest.raises(OSError, match="legacy assignment"):
            run_stage(compose_context(services, run, "reads", "crash"), stubs=True)
    assert claimed
    run_stage(compose_context(services, run, "reads", "retry"), stubs=True)
    run_stage(prepare_result(services, run, "result"), stubs=True)
    assert served(services, run)["meter"]["value"] == "12345"
    assert latest_publication(services.state, run.run_id)["correctionIds"] == ["legacy-answer"]
    assert services.state.get_kv("capture_correction:cap_test:2") is None


def test_import_does_not_retarget_a_modern_confirmation(tmp_path):
    services, run, _ = setup_run(tmp_path)
    accept(services, request(), "modern-answer")
    legacy_slot(services, "modern-answer")
    run_stage(compose_context(services, run, "reads", "import"), stubs=True)
    run_stage(prepare_result(services, run, "result"), stubs=True)
    assert latest_publication(services.state, run.run_id)["correctionIds"] == ["modern-answer"]
    accepted = services.state.get_kv("capture_correction:cap_test:1")
    assert "targetRunId" not in accepted
    assert services.state.get_kv("claim:cap_test:modern-answer:1")["runId"] == run.run_id
    assert not accepted.get("legacy")
    assert services.state.get_kv("capture_correction:cap_test:2") is None
