"""Publication crash recovery preserves the existing public event shape."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from server.api.events import poll_events
from server.orchestrator.corrections import latest_publication, pending_corrections, publish_version
from server.orchestrator.publication import emit_latest_publication
from server.orchestrator.runner import run_stage
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.test_immutable_correction_races import (
    accept,
    prepare_result,
    request,
    served,
    setup_run,
)


def test_crash_after_publication_before_final_event_is_recovered(tmp_path, monkeypatch):
    import server.orchestrator.publication as publication

    services, run, _ = setup_run(tmp_path)
    accept(services, request(), "answer")
    context = prepare_result(services, run, "published-no-event")
    previous = services.state.events_after(run.capture_id, 0, limit=500)

    def crash(*_args):
        raise OSError("crash before final event")

    with monkeypatch.context() as patch:
        patch.setattr(publication, "emit_latest_publication", crash)
        with pytest.raises(OSError, match="before final event"):
            run_stage(context, stubs=True)
    assert not pending_corrections(services.state, run)
    assert served(services, run)["meter"]["value"] == "12345"
    assert services.state.events_after(run.capture_id, 0, limit=500) == previous
    run.status = "done"
    services.state.put_run(run)
    sweep(services)
    finals = services.state.events_after(run.capture_id, previous[-1].seq, limit=500)
    assert [event.type for event in finals] == ["verdict_ready"]
    sweep(services)
    assert services.state.events_after(run.capture_id, previous[-1].seq, limit=500) == finals


def test_late_old_event_and_duplicate_new_event_are_not_exposed(tmp_path, monkeypatch):
    services, run, _ = setup_run(tmp_path)
    old = latest_publication(services.state, run.run_id)
    after = services.state.events_after(run.capture_id, 0, limit=500)[-1].seq
    new = publish_version(services.state, run.run_id, {**old, "correctionIds": ["a"]})
    emit_latest_publication(services.state, services.storage, run.run_id, services.settings)
    # Model two emitters racing the event-log read and an older emitter whose
    # append reaches the store only after the newer publication's final event.
    for version in (new, old):
        event_type, data = version["events"][0]
        services.state.append_event(
            run.capture_id,
            event_type,
            {**data, "_resultVersion": version["version"]},
            run_id=run.run_id,
            stage="result",
        )
    deps = SimpleNamespace(state=services.state, polls={}, wake={})
    capture = services.state.get_capture(run.capture_id)
    response = asyncio.run(poll_events(deps, capture, after, 0))
    assert len(response.events) == 1
    assert response.events[0].data == new["events"][0][1]
    assert "_resultVersion" not in response.model_dump_json()
    assert response.next == services.state.events_after(run.capture_id, 0, limit=500)[-1].seq


def test_replayed_answer_may_have_a_new_client_timestamp(tmp_path):
    services, run, _ = setup_run(tmp_path)
    first = request()
    accept(services, first, "retry")
    accept(services, first.model_copy(update={"clientAt": "2026-10-16T00:00:00.000Z"}), "retry")
    assert services.state.get_kv(f"capture_correction:{run.capture_id}:1")["confirmationId"] == "retry"
    assert services.state.get_kv(f"capture_correction:{run.capture_id}:2") is None
