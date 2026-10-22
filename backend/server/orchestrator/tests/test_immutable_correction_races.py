"""Force races at active journal, lease, and publication boundaries."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread

import pytest

from server.api.hooks import safe_hook
from server.api.routes.result import _artifacts
from server.common.lease import LeaseBusy, with_lease
from server.contracts.api import ConfirmationRequest
from server.contracts.jobs import StageRecord
from server.orchestrator.api import on_confirmation, start_run
from server.orchestrator.artifacts import overlay_stage_artifacts
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.corrections import correction_snapshot, pending_corrections
from server.orchestrator.runner import run_stage
from server.orchestrator.services import bind
from server.orchestrator.stateio import put_json
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.conftest import build_services
from server.reads.types import ConfirmBlock, MeterRead, PanelBundle, PanelRead, Reads


def setup_run(tmp_path):
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    run = services.state.get_run(run.run_id)
    assert run.status == "done"
    key = overlay_stage_artifacts(services.state, run).artifacts["reads/reads.json"]
    base = Reads(
        capture_id=run.capture_id,
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        panel=PanelBundle(merged=PanelRead(brand="old", main_breaker_amps=100)),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(key, base.model_dump_json().encode(), "application/json")
    run.status = "running"
    services.state.put_run(run)
    return services, run, key


def request(field="meter_number", value="12345"):
    return ConfirmationRequest(
        fieldId=field, kind="corrected", value=value, imageId="k0", clientAt="2026-10-15T00:00:00.000Z"
    )


def accept(services, row, identifier):
    with bind(services):
        on_confirmation("cap_test", row, identifier, by="device")


def served(services, run):
    artifacts = _artifacts(services, run)
    return json.loads(services.storage.get_bytes(artifacts["result/result.json"]))


def compose_context(services, run, stage, label):
    rec = StageRecord(run_id=run.run_id, stage=stage, status="done", attempt=1, heartbeat=services.clock.iso())
    # Execute both callbacks as an already admitted worker. Worker-ID checks are
    # advisory too and are not the invariant under test here.
    services.state.upsert_stage(rec)
    return services.stage_context(run, rec, label)


def prepare_result(services, run, label):
    criteria = compose_context(services, run, "criteria", label)
    output = run_stage(criteria, stubs=True)
    put_json(services.state, f"stage_artifacts:{run.run_id}:criteria", output.artifacts)
    return compose_context(services, run, "result", label)


def test_busy_acceptance_is_recovered_after_completion(tmp_path, monkeypatch):
    import server.orchestrator.api as api

    services, run, _ = setup_run(tmp_path)
    run.status = "done"
    services.state.put_run(run)
    lease_attempts = []

    def busy(*_args):
        lease_attempts.append(run.run_id)
        assert [row["confirmationId"] for row in correction_snapshot(services.state, run)] == ["busy"]
        assert pending_corrections(services.state, run)
        raise LeaseBusy("deterministically occupied")

    with monkeypatch.context() as patch:
        patch.setattr(api, "with_correction_lease", busy)
        with bind(services):
            safe_hook("on_confirmation", on_confirmation, run.capture_id, request(), "busy", by="device", strict=True)
    assert lease_attempts == [run.run_id], "the done-run recomposition lease was never attempted"
    assert pending_corrections(services.state, run)
    sweep(services)
    assert served(services, run)["meter"]["value"] == "12345"
    assert not pending_corrections(services.state, run)


@pytest.mark.parametrize("after_insert", [False, True])
def test_crash_at_journal_insert_never_acknowledges_an_unstored_answer(tmp_path, monkeypatch, after_insert):
    services, run, reads_key = setup_run(tmp_path)
    original = services.state.put_if_absent
    faults = []
    acknowledged = []

    def crash(key, row):
        if key.startswith("capture_correction:"):
            faults.append(key)
            if after_insert:
                assert original(key, row)
            raise OSError("process crashed at journal insert")
        return original(key, row)

    with monkeypatch.context() as patch:
        patch.setattr(services.state, "put_if_absent", crash)
        with pytest.raises(OSError, match="journal insert"), bind(services):
            safe_hook("on_confirmation", on_confirmation, run.capture_id, request(), "crash", by="device", strict=True)
            acknowledged.append(True)
    assert faults == [f"capture_correction:{run.capture_id}:1"]
    assert acknowledged == []
    assert bool(services.state.get_kv(f"capture_correction:{run.capture_id}:1")) is after_insert
    if after_insert:
        # Recovery does not require the unacknowledged client to retry.
        run.status = "done"
        services.state.put_run(run)
        sweep(services)
        assert served(services, run)["meter"]["value"] == "12345"
    accept(services, request(), "crash")
    run.status = "done"
    services.state.put_run(run)
    sweep(services)
    assert served(services, run)["meter"]["value"] == "12345"
    assert [row["confirmationId"] for row in correction_snapshot(services.state, run)] == ["crash"]
    assert Reads.model_validate_json(services.storage.get_bytes(reads_key)).meter.value == "00000"


def test_two_workers_cannot_lose_different_field_corrections(tmp_path, monkeypatch):
    import server.orchestrator.stubs.criteria as criteria

    services, run, _ = setup_run(tmp_path)
    original_insert = services.state.put_if_absent
    same_empty_slot = Barrier(2)
    attempts = []
    results = []

    def contend_for_journal_slot(key, row):
        if key == f"capture_correction:{run.capture_id}:1":
            attempts.append(row["confirmationId"])
            same_empty_slot.wait(timeout=5)
            inserted = original_insert(key, row)
            results.append(inserted)
            return inserted
        return original_insert(key, row)

    with monkeypatch.context() as patch:
        patch.setattr(services.state, "put_if_absent", contend_for_journal_slot)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(accept, services, row, identifier)
                for row, identifier in ((request(), "meter"), (request("panel_brand", "new"), "panel"))
            ]
            for future in futures:
                future.result(timeout=10)
    assert sorted(attempts) == ["meter", "panel"], "both workers must reach the immutable insertion boundary"
    assert sorted(results) == [False, True], "one insertion must lose contention and retry"
    assert {row["confirmationId"] for row in correction_snapshot(services.state, run)} == {"meter", "panel"}
    hydrated = []
    original_criteria = criteria.run

    def observe(ctx):
        hydrated.append(Reads.model_validate_json((ctx.work_dir / "reads/reads.json").read_bytes()))
        return original_criteria(ctx)

    monkeypatch.setattr(criteria, "run", observe)
    RunCoordinator(services).drive(run.run_id)
    assert hydrated
    assert hydrated[-1].meter.value == "12345"
    assert hydrated[-1].panel.merged.brand == "new"
    assert served(services, run)["meter"]["value"] == "12345"


def test_stale_release_and_two_composers_preserve_every_published_version(tmp_path, monkeypatch):
    services, run, _ = setup_run(tmp_path)
    accept(services, request(), "meter")
    stale_context = prepare_result(services, run, "stale")
    successor_entered = Event()
    successor_deleted = Event()
    errors = []
    actual_delete = services.state.delete_kv
    key = "lease:publication-test"
    published_bytes = {}
    actual_write = services.storage.put_bytes

    def immutable_write(storage_key, data, content_type):
        if "/result/" in storage_key:
            assert storage_key not in published_bytes, "a result object was overwritten"
            published_bytes[storage_key] = data
        actual_write(storage_key, data, content_type)

    monkeypatch.setattr(services.storage, "put_bytes", immutable_write)

    def successor():
        def compose():
            successor_entered.set()
            assert successor_deleted.wait(5)
            accept(services, request("panel_brand", "new"), "panel")
            run_stage(prepare_result(services, run, "successor"), stubs=True)

        try:
            with_lease(services.state, "publication-test", compose, strict=True, wait_s=0)
        except RuntimeError as exc:
            # The advisory helper may report the deliberately deleted lease.
            if type(exc).__name__ != "LeaseLost":
                errors.append(exc)
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=successor)
    deleted_successor = []

    def release_after_takeover(lease_key):
        if lease_key == key and not deleted_successor:
            # Release has already read the old token. Expiry removes it and
            # lets a successor claim before the delayed delete reaches storage.
            actual_delete(key)
            worker.start()
            assert successor_entered.wait(5)
            assert services.state.get_kv(key) is not None
            deleted_successor.append(True)
            actual_delete(key)
            successor_deleted.set()
            return
        actual_delete(lease_key)

    monkeypatch.setattr(services.state, "delete_kv", release_after_takeover)
    with_lease(services.state, "publication-test", lambda: None, strict=True, wait_s=0)
    worker.join(10)
    assert not worker.is_alive()
    assert errors == []
    assert deleted_successor == [True]
    before = {key: services.storage.get_bytes(key) for key in published_bytes}
    # The stale composer resumes after its successor has published both fields.
    run_stage(stale_context, stubs=True)
    assert served(services, run)["meter"]["value"] == "12345"
    selected = _artifacts(services, run)
    snapshot_key = selected.get("verdicts/correction-inputs.json")
    assert snapshot_key is not None, "the served result has no correction coverage record"
    snapshot = json.loads(services.storage.get_bytes(snapshot_key))
    assert snapshot["reads"]["panel"]["merged"]["brand"] == "new"
    assert snapshot["correctionIds"] == ["meter", "panel"]
    assert all(services.storage.get_bytes(key) == data for key, data in before.items())


def test_crash_after_upload_before_publication_keeps_answer_pending(tmp_path, monkeypatch):
    services, run, _ = setup_run(tmp_path)
    accept(services, request(), "crash")
    context = prepare_result(services, run, "interrupted")
    original = services.state.put_if_absent
    events_before = len(services.state.events_after(run.capture_id, 0, limit=500))

    def crash(key, row):
        if key.startswith("result_version:"):
            raise OSError("crash before atomic publication")
        return original(key, row)

    with monkeypatch.context() as patch:
        patch.setattr(services.state, "put_if_absent", crash)
        with pytest.raises(OSError, match="atomic publication"):
            run_stage(context, stubs=True)
    assert len(services.state.events_after(run.capture_id, 0, limit=500)) == events_before
    RunCoordinator(services).drive(run.run_id)
    assert served(services, run)["meter"]["value"] == "12345"
    events = services.state.events_after(run.capture_id, 0, limit=500)
    assert any(event.type == "verdict_ready" for event in events[events_before:])
