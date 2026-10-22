"""A correction must not reopen another run's delivered result."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from server.api.events import poll_events
from server.orchestrator.api import start_run
from server.orchestrator.corrections import correction_snapshot, latest_publication, pending_corrections
from server.orchestrator.services import bind, packet_sha256
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.test_immutable_correction_races import accept, request, served, setup_run


def next_run(services, number):
    key = f"captures/cap_test/amendments/test-{number}/packet.json"
    packet = json.loads(services.storage.get_bytes("captures/cap_test/packet.json"))
    packet["testRevision"] = number
    raw = json.dumps(packet).encode()
    services.storage.put_bytes(key, raw, "application/json")
    with bind(services):
        return start_run("cap_test", packet_sha256(raw), key)


def public_events(services, run):
    deps = SimpleNamespace(state=services.state, polls={}, wake={})
    capture = services.state.get_capture(run.capture_id)
    response = asyncio.run(poll_events(deps, capture, 0, 0))
    run_seqs = {
        event.seq for event in services.state.events_after(run.capture_id, 0, limit=500) if event.run_id == run.run_id
    }
    return [event.model_dump(mode="json") for event in response.events if event.seq in run_seqs]


def test_correcting_newest_run_does_not_rewrite_delivered_history(tmp_path):
    services, first, _ = setup_run(tmp_path)
    first.status = "done"
    services.state.put_run(first)
    before_result = served(services, first)
    before_publication = latest_publication(services.state, first.run_id)
    before_events = public_events(services, first)
    second = next_run(services, 2)
    assert second.status == "done"
    accept(services, request(), "only-second")
    sweep(services)
    assert served(services, second)["meter"]["value"] == "12345"
    assert served(services, first) == before_result
    assert latest_publication(services.state, first.run_id) == before_publication
    assert public_events(services, first) == before_events
    assert correction_snapshot(services.state, first) == []


def test_failed_run_does_not_own_or_retry_a_later_answer(tmp_path):
    services, failed, _ = setup_run(tmp_path)
    failed.status = "failed"
    services.state.put_run(failed)
    before_events = public_events(services, failed)
    accept(services, request(), "after-failure")
    assert not pending_corrections(services.state, failed)
    sweep(services)
    assert services.state.get_run(failed.run_id).status == "failed"
    assert public_events(services, failed) == before_events
    assert correction_snapshot(services.state, failed) == []


def test_answer_after_failure_is_copied_into_exactly_the_next_run(tmp_path):
    services, failed, _ = setup_run(tmp_path)
    failed.status = "failed"
    services.state.put_run(failed)
    accept(services, request(), "pre-run")
    second = next_run(services, 2)
    assert served(services, second)["meter"]["value"] == "12345"
    assert [row["confirmationId"] for row in correction_snapshot(services.state, second)] == ["pre-run"]
    third = next_run(services, 3)
    assert correction_snapshot(services.state, third) == []
    assert not pending_corrections(services.state, failed)


def test_retry_does_not_move_an_answer_to_a_newer_run(tmp_path):
    services, first, _ = setup_run(tmp_path)
    accept(services, request(), "stable-target")
    second = next_run(services, 2)
    accept(services, request(), "stable-target")
    assert [row["confirmationId"] for row in correction_snapshot(services.state, first)] == ["stable-target"]
    assert correction_snapshot(services.state, second) == []
