"""Portable regressions for creation windows and failed correction owners."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local

import pytest

from server.orchestrator.api import start_run
from server.orchestrator.corrections import (
    correction_snapshot,
    latest_publication,
    record_correction,
    seed_run_corrections,
)
from server.orchestrator.services import bind
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.conftest import build_services
from server.orchestrator.tests.test_correction_run_scope import next_run, public_events
from server.orchestrator.tests.test_greptile_followup import _run
from server.orchestrator.tests.test_immutable_correction_races import accept, request, served, setup_run


def answer(identifier="answer"):
    return {"confirmationId": identifier, "by": "device", **request().model_dump(mode="json", exclude={"clientAt"})}


def start(services, uri, digest):
    with bind(services):
        return start_run("cap_test", digest, uri)


def assert_incorporated_once(services, run, identifier):
    assert served(services, run)["meter"]["value"] == "12345"
    assert latest_publication(services.state, run.run_id)["correctionIds"] == [identifier]
    assert [row["confirmationId"] for row in correction_snapshot(services.state, run)] == [identifier]
    assert services.state.get_kv("capture_correction:cap_test:2") is None


@pytest.mark.parametrize("delivered_predecessor", [False, True])
def test_confirmation_between_seed_and_run_write_reaches_new_run(tmp_path, monkeypatch, delivered_predecessor):
    import server.orchestrator.api as api

    if delivered_predecessor:
        services, old, _ = setup_run(tmp_path)
        old.status = "done"
        services.state.put_run(old)
        before = served(services, old)
        events = public_events(services, old)
    else:
        services, uri, digest = build_services(tmp_path, "memory")
    seeded = Event()
    resume = Event()
    original = api.seed_run_corrections

    def stop_after_seed(state, run):
        original(state, run)
        seeded.set()
        assert resume.wait(10)

    monkeypatch.setattr(api, "seed_run_corrections", stop_after_seed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = (
            pool.submit(next_run, services, 2) if delivered_predecessor else pool.submit(start, services, uri, digest)
        )
        try:
            assert seeded.wait(10), "run creation never reached the seed boundary"
            accept(services, request(), "between")
        finally:
            resume.set()
        run = future.result(timeout=20)
    assert_incorporated_once(services, run, "between")
    if delivered_predecessor:
        sweep(services)
        assert served(services, old) == before
        assert public_events(services, old) == events
        assert correction_snapshot(services.state, old) == []


def test_confirmation_while_run_is_pending_reaches_that_run(tmp_path, monkeypatch):
    services, uri, digest = build_services(tmp_path, "memory")
    pending = Event()
    resume = Event()
    original = services.state.put_run

    def stop_after_pending_write(run):
        original(run)
        if run.status == "pending" and not pending.is_set():
            pending.set()
            assert resume.wait(10)

    monkeypatch.setattr(services.state, "put_run", stop_after_pending_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(start, services, uri, digest)
        try:
            assert pending.wait(10), "run never reached its durable pending state"
            accept(services, request(), "pending")
        finally:
            resume.set()
        run = future.result(timeout=20)
    assert_incorporated_once(services, run, "pending")


@pytest.mark.parametrize("failure_boundary", ["journal_insert", "after_ack"])
def test_running_owner_failure_cannot_strand_an_answer(tmp_path, monkeypatch, failure_boundary):
    services, failed, _ = setup_run(tmp_path)
    accepted = Event()
    resume = Event()
    original = services.state.put_if_absent

    def pause_after_insert(key, row):
        inserted = original(key, row)
        if failure_boundary == "journal_insert" and key == "capture_correction:cap_test:1":
            accepted.set()
            assert resume.wait(10)
        return inserted

    def submit():
        accept(services, request(), "survives-failure")
        if failure_boundary == "after_ack":
            accepted.set()
            assert resume.wait(10)

    monkeypatch.setattr(services.state, "put_if_absent", pause_after_insert)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(submit)
        try:
            assert accepted.wait(10), "acceptance never reached the selected boundary"
            failed.status = "failed"
            services.state.put_run(failed)
        finally:
            resume.set()
        future.result(timeout=20)
    following = next_run(services, 2)
    assert_incorporated_once(services, following, "survives-failure")
    assert services.state.get_run(failed.run_id).status == "failed"
    if failure_boundary == "after_ack":
        assert services.state.get_kv("claim:cap_test:survives-failure:1")["runId"] == failed.run_id
        assert services.state.get_kv("claim:cap_test:survives-failure:2")["runId"] == following.run_id


def test_two_runs_racing_to_take_over_have_one_owner(tmp_path, monkeypatch):
    services, _, _ = build_services(tmp_path, "memory")
    state = services.state
    previous = _run(services, "run_previous", created_at="2026-10-01T00:00:00Z")
    state.put_run(previous)
    record_correction(state, "cap_test", answer())
    seed_run_corrections(state, previous)
    previous.status = "failed"
    state.put_run(previous)
    contenders = [_run(services, name) for name in ("run_b", "run_c")]
    for run in contenders:
        state.put_run(run)
    observed = local()
    original_runs = state.runs_for_capture
    original_insert = state.put_if_absent
    gate = Barrier(2)
    attempts = []

    def observed_newest(capture_id):
        # Each creator observed itself as newest before the other became visible.
        return [observed.run] if hasattr(observed, "run") else original_runs(capture_id)

    def contend(key, row):
        if key == "claim:cap_test:answer:2":
            attempts.append(row["runId"])
            gate.wait(timeout=5)
        return original_insert(key, row)

    def seed(run):
        observed.run = run
        seed_run_corrections(state, run)

    with monkeypatch.context() as patch:
        patch.setattr(state, "runs_for_capture", observed_newest)
        patch.setattr(state, "put_if_absent", contend)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(seed, run) for run in contenders]
            for future in futures:
                future.result(timeout=10)
    snapshots = [correction_snapshot(state, run) for run in contenders]
    assert sorted(len(rows) for rows in snapshots) == [0, 1]
    assert sorted(attempts) == ["run_b", "run_c"], "both contenders must reach generation two"
    assert state.get_kv("claim:cap_test:answer:1")["runId"] == previous.run_id
    assert state.get_kv("claim:cap_test:answer:3") is None


def test_sweeper_never_assigns_unowned_answer_to_delivered_predecessor(tmp_path):
    services, old, _ = setup_run(tmp_path)
    old.status = "done"
    services.state.put_run(old)
    before = served(services, old)
    events = public_events(services, old)
    newest = _run(services, "run_newest", created_at="2099-01-01T00:00:00Z")
    services.state.put_run(newest)
    record_correction(services.state, "cap_test", answer())
    sweep(services)
    assert served(services, old) == before
    assert public_events(services, old) == events
    assert correction_snapshot(services.state, old) == []
    assert [row["confirmationId"] for row in correction_snapshot(services.state, newest)] == ["answer"]
