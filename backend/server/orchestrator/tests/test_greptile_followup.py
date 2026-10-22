"""Regressions from the Greptile apex review of PR #247.

Each test names the finding it pins: amendment packet hydration, confirmation
persistence, per-attempt timeout budgets, and the run's own rules profile.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from server.contracts.api import ConfirmationRequest
from server.common.lease import LeaseBusy
from server.contracts.jobs import FileRecord, RunRecord, StageRecord
from server.orchestrator.api import on_confirmation
from server.orchestrator.artifacts import overlay_stage_artifacts
from server.orchestrator.clock import Clock
from server.orchestrator.coordinator import RunCoordinator, pending_correction_key
from server.orchestrator.events import emit_failed
from server.orchestrator.services import bind
from server.orchestrator.stubs._common import scene_document
from server.orchestrator.tests.conftest import build_services
from server.packet.integrity import check_files
from server.packet.report import Bag
from server.reads.types import ConfirmBlock, MeterRead, PanelBundle, PanelRead, Reads


def _run(services, run_id: str, **overrides: object) -> RunRecord:
    fields: dict[str, object] = {
        "run_id": run_id,
        "capture_id": "cap_test",
        "packet_sha256": "a" * 64,
        "status": "running",
        "branch": "image",
        "flow": "guided",
        "tier": "arkit",
        "profile_default": "C",
        "created_at": services.clock.iso(),
        "deadline_at": (services.clock.now() + timedelta(minutes=20)).isoformat(),
        "artifacts": {},
    }
    fields.update(overrides)
    return RunRecord(**fields)  # type: ignore[arg-type]


def test_amendment_run_hydrates_its_own_packet(tmp_path: Path) -> None:
    """Finding: artifacts.py:202 — an amendment run read the base packet."""
    services, _uri, _digest = build_services(tmp_path, "memory")
    amend_key = "captures/cap_test/amendments/amd-1/packet.json"
    amend_packet = {
        "keyframes": [{"id": "door-2"}],
        "stills": [],
        "files": [{"path": "extra/photo.jpg", "sha256": "ab" * 32, "bytes": 4}],
    }
    services.storage.put_bytes(amend_key, json.dumps(amend_packet).encode(), "application/json")
    services.storage.put_bytes("captures/cap_test/amendments/amd-1/extra/photo.jpg", b"jpeg", "image/jpeg")
    run = _run(services, "run_amend", packet_uri=amend_key)
    rec = StageRecord(run_id=run.run_id, stage="poses", status="running", attempt=1, heartbeat=services.clock.iso())
    with bind(services):
        ctx = services.stage_context(run, rec, "call")
    packet_dir = Path(ctx.inputs["packet_dir"])
    # The amendment manifest, not captures/cap_test/packet.json.
    assert json.loads((packet_dir / "packet.json").read_text()) == amend_packet
    # Manifest-listed files resolve under the amendment prefix.
    assert (packet_dir / "extra" / "photo.jpg").read_bytes() == b"jpeg"


def test_amendment_same_name_still_hydrates_amendment_bytes(tmp_path: Path) -> None:
    """Finding: _packet_file_jobs — a committed base file claimed the relative path first.

    An amendment that re-captures a base filename (stills/s1.jpg) with different
    bytes must hydrate its own copy under the amendment prefix. Under the
    pre-fix derivation the committed base record entered the seen-set first and
    the base bytes landed at packet_dir/stills/s1.jpg, so the real validator
    would reject the size/sha256 mismatch against the amendment manifest.
    """
    services, _uri, _digest = build_services(tmp_path, "memory")
    base_blob = b"base-still-bytes"
    amend_blob = b"amendment-still-bytes-different"
    services.storage.put_bytes("captures/cap_test/stills/s1.jpg", base_blob, "image/jpeg")
    services.state.upsert_file(
        FileRecord(
            capture_id="cap_test",
            path="stills/s1.jpg",
            role="still",
            bytes=len(base_blob),
            sha256=hashlib.sha256(base_blob).hexdigest(),
            md5=base64.b64encode(hashlib.md5(base_blob).digest()).decode(),
            content_type="image/jpeg",
            state="committed",
            key="captures/cap_test/stills/s1.jpg",
        )
    )
    amend_key = "captures/cap_test/amendments/amd-2/packet.json"
    amend_packet = {
        "keyframes": [],
        "stills": [],
        "files": [
            {"path": "stills/s1.jpg", "sha256": hashlib.sha256(amend_blob).hexdigest(), "bytes": len(amend_blob)}
        ],
    }
    services.storage.put_bytes(amend_key, json.dumps(amend_packet).encode(), "application/json")
    services.storage.put_bytes("captures/cap_test/amendments/amd-2/stills/s1.jpg", amend_blob, "image/jpeg")
    run = _run(services, "run_amend_2", packet_uri=amend_key)
    rec = StageRecord(run_id=run.run_id, stage="poses", status="running", attempt=1, heartbeat=services.clock.iso())
    with bind(services):
        ctx = services.stage_context(run, rec, "call")
    packet_dir = Path(ctx.inputs["packet_dir"])
    # The amendment's bytes — not the committed base file's — sit at the manifest path.
    assert (packet_dir / "stills" / "s1.jpg").read_bytes() == amend_blob
    # And the real packet validator agrees with the amendment manifest.
    bag = Bag()
    check_files(amend_packet, packet_dir, bag, check_hashes=True, known_sha256=None)
    assert bag.errors == []


def test_confirmation_updates_the_reads_record(tmp_path: Path) -> None:
    """Finding: api.py:173 — confirm() got a capture_id string, not a Reads."""
    services, _uri, _digest = build_services(tmp_path, "memory")
    key = "captures/cap_test/runs/run_reads/reads/reads.json"
    reads = Reads(
        capture_id="cap_test",
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(key, reads.model_dump_json().encode(), "application/json")
    scene_key = "captures/cap_test/runs/run_reads/scene/scene.json"
    services.storage.put_bytes(
        scene_key, json.dumps(scene_document("cap_test"), sort_keys=True).encode(), "application/json"
    )
    objects_key = "captures/cap_test/runs/run_reads/objects/objects_3d.json"
    services.storage.put_bytes(
        objects_key, json.dumps({"format": "hss-objects-3d/v0", "objects": []}).encode(), "application/json"
    )
    run = _run(
        services,
        "run_reads",
        status="done",
        artifacts={"reads/reads.json": key, "scene/scene.json": scene_key, "objects/objects_3d.json": objects_key},
    )
    services.state.put_run(run)
    request = ConfirmationRequest(
        fieldId="meter_number",
        kind="corrected",
        value="12345",
        imageId="s1",
        clientAt="2026-10-15T00:00:00.000Z",
    )
    with bind(services):
        on_confirmation("cap_test", request, "cfm_1", by="device")
    stored = Reads.model_validate(json.loads(services.storage.get_bytes(key)))
    assert stored.meter is not None
    assert stored.meter.value == "12345"
    assert stored.meter.status == "auto_fill"
    assert stored.confirm is not None
    assert stored.confirm.answer == "12345"
    # The recompose preserves the upstream work as terminal records at attempt 1
    # — the re-run reads stage that once threatened the answer never executes.
    rows = {rec.stage: rec for rec in services.state.stages("run_reads")}
    for upstream in (
        "validate",
        "poses",
        "objects",
        "reads",
        "scale",
        "dense",
        "coverage",
        "scene",
        "objects_3d",
        "placement",
    ):
        assert rows[upstream].status == "done", upstream
        assert rows[upstream].attempt == 1, upstream
    assert rows["criteria"].status == "done"
    assert rows["result"].status == "done"
    assert services.state.get_run("run_reads").status == "done"
    # The served result is rebuilt from the corrected record, not stale.
    stored_run = overlay_stage_artifacts(services.state, services.state.get_run("run_reads"))
    served = json.loads(services.storage.get_bytes(stored_run.artifacts["result/result.json"]))
    assert served["meter"]["value"] == "12345"


def test_correction_on_a_legacy_run_without_scene_artifact_fails_loudly(tmp_path: Path) -> None:
    """Finding: api.py:171 (PR #277 Greptile P1) — the old guard kept the run
    closed, so a correction on a finished run missing stage records reported
    success while GET /result kept serving the previous answer.

    When the rebuild cannot run — here the run predates the scene artifact, so
    the coordinator-owned merge has nothing to merge — the run must fail loudly
    instead of quietly staying done with a stale served result.
    """
    services, _uri, _digest = build_services(tmp_path, "memory")
    key = "captures/cap_test/runs/run_noscene/reads/reads.json"
    reads = Reads(
        capture_id="cap_test",
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(key, reads.model_dump_json().encode(), "application/json")
    run = _run(services, "run_noscene", status="done", artifacts={"reads/reads.json": key})
    services.state.put_run(run)
    request = ConfirmationRequest(
        fieldId="meter_number",
        kind="corrected",
        value="12345",
        imageId="s1",
        clientAt="2026-10-15T00:00:00.000Z",
    )
    with bind(services):
        on_confirmation("cap_test", request, "cfm_3", by="device")
    # The answer itself is still persisted either way.
    stored_reads = Reads.model_validate(json.loads(services.storage.get_bytes(key)))
    assert stored_reads.meter is not None
    assert stored_reads.meter.value == "12345"
    # And the stale-result silence is gone: the run reports its failed rebuild.
    stored = services.state.get_run("run_noscene")
    assert stored.status == "failed"
    assert stored.failure_code == "artifact_invalid:scene"


def test_new_attempt_clears_entered_at(tmp_path: Path) -> None:
    """Finding: coordinator.py:266 — a retry inherited the timed-out budget."""
    services, _uri, _digest = build_services(tmp_path, "memory")
    # No worker runs, so the persisted attempt-start record is observable.
    services.executor.spawn = lambda call_id, ctx: None  # type: ignore[method-assign]
    run = _run(services, "run_retry")
    services.state.put_run(run)
    old = services.clock.iso()
    services.state.upsert_stage(
        StageRecord(
            run_id=run.run_id,
            stage="validate",
            status="pending",
            attempt=1,
            failures=1,
            heartbeat=old,
            entered_at=old,
            error_code="timeout",
            error="timeout: validate",
        )
    )
    with bind(services):
        assert RunCoordinator(services)._spawn_one(run, "validate")
    rec = services.state.get_stage(run.run_id, "validate")
    assert rec.status == "running"
    # The timeout sweep measures from entered_at; a stale value would cancel
    # the replacement worker before it ever entered the stage.
    assert rec.entered_at is None
    assert rec.error_code is None
    with bind(services):
        RunCoordinator(services)._sweep_timeouts(run)
    assert services.state.get_stage(run.run_id, "validate").status == "running"


def test_success_reads_the_runs_profile_verdict(tmp_path: Path) -> None:
    """Finding: coordinator.py:571 — success always read verdict.C.json."""
    services, _uri, _digest = build_services(tmp_path, "memory")
    key = "captures/cap_test/runs/run_prof/verdicts/verdict.A.json"
    verdict = {"site": {"decision": "manual_review"}, "views_needed": ["door-2"]}
    services.storage.put_bytes(key, json.dumps(verdict).encode(), "application/json")
    run = _run(services, "run_prof", profile_default="A", artifacts={"verdicts/verdict.A.json": key})
    services.state.put_run(run)
    # One final event already exists, so the close does not rewrite the status.
    emit_failed(
        services.state,
        "cap_test",
        run.run_id,
        "seed",
        "seed",
        "manual_review",
        clock=Clock(),
    )
    with bind(services):
        RunCoordinator(services)._apply_success(run)
    # A-profile verdict asks for more views; verdict.C.json is absent, so the
    # old hard-coded read fell through to manual_review and blocked amendments.
    assert services.state.get_capture("cap_test").status == "needs_views"


def test_correction_during_live_result_stage_recomposes_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding: orchestrator/api.py:169 (PR #293 Greptile P1) — a live run was left
    alone on the assumption its result stage had not consumed the corrected reads.

    A confirmation can arrive after the result stage hydrated its inputs but
    before it finishes uploading. The hook saved the corrected reads and
    returned: the run finished done with the pre-correction meter, and a
    replayed confirmation short-circuits upstream, so nothing recomposed. The
    hook must record a pending correction on the live run, and the coordinator
    must consume it before marking the run done — resetting criteria and result
    once, the same reset the done-run path uses.
    """

    import threading

    import server.orchestrator.stubs.result as result_stub
    from server.orchestrator.api import start_run
    from server.orchestrator.coordinator import pending_correction_key
    from server.orchestrator.stateio import get_json

    services, uri, digest = build_services(tmp_path, "memory", coordinator_loop_s=0.05)

    entered = threading.Event()
    release = threading.Event()
    original_result_run = result_stub.run
    real_meter = result_stub._stub_meter

    def gated_run(ctx):
        # The stage has consumed its hydrated, pre-correction reads copy.
        real_meter(ctx)
        entered.set()
        assert release.wait(30)
        return original_result_run(ctx)

    monkeypatch.setattr(result_stub, "run", gated_run)

    errors: list[BaseException] = []

    def drive() -> None:
        try:
            with bind(services):
                start_run("cap_test", digest, uri)
        except BaseException as exc:  # surfaced after the join below
            errors.append(exc)

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()
    assert entered.wait(60), "result stage never reached its gate"

    request = ConfirmationRequest(
        fieldId="meter_number",
        kind="corrected",
        value="12345",
        imageId="s1",
        clientAt="2026-10-15T00:00:00.000Z",
    )
    # The correction and a replayed confirmation both land while the result
    # stage holds its half-composed inputs. One pending marker must survive.
    with bind(services):
        on_confirmation("cap_test", request, "cfm_live", by="device")
        on_confirmation("cap_test", request, "cfm_live", by="device")
    release.set()
    worker.join(60)
    assert not errors, errors
    assert not worker.is_alive(), "run never finished after the gate released"

    (run,) = services.state.runs_for_capture("cap_test")
    assert run.status == "done"
    # The recomposed pass records attempt 1 again (the coordinator recomputes
    # spawn attempts from failures+respawns), so "recomposed exactly once" is
    # read from the event log: the original pass plus one re-run of each stage
    # — the replayed confirmation is the same answer and must not add a third.
    events = services.state.events_after("cap_test", 0, limit=500)
    for stage in ("criteria", "result"):
        started = [
            event
            for event in events
            if event.type == "stage" and event.data.get("stage") == stage and event.data.get("status") == "running"
        ]
        assert len(started) == 2, [(e.type, e.data) for e in started]
    finals = [event for event in events if event.type in {"verdict_ready", "retake_request", "failed"}]
    assert [event.type for event in finals] == ["verdict_ready", "verdict_ready"]
    assert not any(event.data.get("code") == "result_no_event" for event in events if event.type == "failed")
    # The pending marker is consumed, not left for a later finish to re-reset.
    assert get_json(services.state, pending_correction_key(run.run_id)) is None
    # The served result carries the corrected value, not the pre-correction meter.
    served_run = overlay_stage_artifacts(services.state, run)
    served = json.loads(services.storage.get_bytes(served_run.artifacts["result/result.json"]))
    assert served["meter"]["value"] == "12345"
    assert services.state.get_capture("cap_test").status == "manual_review"


def test_pending_correction_survives_a_failed_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reset failure must leave the durable marker for the next tick."""

    import server.orchestrator.coordinator as coordinator_module

    services, _uri, _digest = build_services(tmp_path, "memory")
    run = _run(services, "run_reset_failure")
    services.state.put_run(run)
    key = pending_correction_key(run.run_id)
    services.state.put_blob(key, b'{"captureId":"cap_test"}')

    def fail_reset(*_args, **_kwargs):
        raise OSError("state write interrupted")

    monkeypatch.setattr(coordinator_module, "reset_criteria_and_result", fail_reset)
    with pytest.raises(OSError, match="state write interrupted"):
        with bind(services):
            RunCoordinator(services)._consume_pending_correction(run)
    assert services.state.get_blob(key) is not None


def test_completion_and_confirmation_have_one_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmation racing completion is converted into a recompose, never a stale marker."""

    import threading

    import server.orchestrator.api as api_module

    services, _uri, _digest = build_services(tmp_path, "memory")
    reads_key = "captures/cap_test/runs/run_race/reads/reads.json"
    reads = Reads(
        capture_id="cap_test",
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(reads_key, reads.model_dump_json().encode(), "application/json")
    run = _run(
        services,
        "run_race",
        artifacts={"reads/reads.json": reads_key},
    )
    services.state.put_run(run)
    from server.orchestrator.graph import STAGE_NAMES

    for stage in STAGE_NAMES:
        services.state.upsert_stage(
            StageRecord(run_id=run.run_id, stage=stage, status="done", attempt=1, heartbeat=services.clock.iso())
        )
    services.state.put_blob(
        f"run_control:{run.run_id}",
        b'{"merge_done":true,"coordinator_heartbeat_at":null,"coordinator_respawns":0}',
    )

    completion_entered = threading.Event()
    confirmation_waiting = threading.Event()
    recompose_called = threading.Event()

    def write_files(_self, _run):
        completion_entered.set()
        assert confirmation_waiting.wait(5)

    def apply_success(_self, current):
        current.status = "done"
        services.state.put_run(current)

    monkeypatch.setattr(RunCoordinator, "_write_run_files", write_files)
    monkeypatch.setattr(RunCoordinator, "_apply_success", apply_success)

    real_api_lease = api_module.with_correction_lease

    def confirmation_lease(service, run_id, fn):
        confirmation_waiting.set()
        return real_api_lease(service, run_id, fn)

    monkeypatch.setattr(api_module, "with_correction_lease", confirmation_lease)
    monkeypatch.setattr(api_module, "_recompose_after_correction", lambda *_args: recompose_called.set())

    errors: list[BaseException] = []

    def finish() -> None:
        try:
            with bind(services):
                RunCoordinator(services)._maybe_finish(run)
        except BaseException as exc:
            errors.append(exc)

    finish_thread = threading.Thread(target=finish)
    finish_thread.start()
    assert completion_entered.wait(5)

    request = ConfirmationRequest(
        fieldId="meter_number",
        kind="corrected",
        value="12345",
        imageId="s1",
        clientAt="2026-10-15T00:00:00.000Z",
    )

    def confirm() -> None:
        try:
            with bind(services):
                on_confirmation("cap_test", request, "cfm_race", by="device")
        except BaseException as exc:
            errors.append(exc)

    confirm_thread = threading.Thread(target=confirm)
    confirm_thread.start()
    assert confirmation_waiting.wait(5)
    finish_thread.join(5)
    confirm_thread.join(5)
    assert not finish_thread.is_alive() and not confirm_thread.is_alive()
    assert not errors, errors
    assert recompose_called.is_set()
    assert services.state.get_blob(pending_correction_key(run.run_id)) is None


def test_confirmation_busy_leaves_a_durable_marker(tmp_path: Path) -> None:
    """LeaseBusy cannot acknowledge a correction without recovery state."""
    services, _uri, _digest = build_services(tmp_path, "memory")
    reads_key = "captures/cap_test/runs/run_busy/reads/reads.json"
    reads = Reads(
        capture_id="cap_test",
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(reads_key, reads.model_dump_json().encode(), "application/json")
    run = _run(services, "run_busy", artifacts={"reads/reads.json": reads_key})
    services.state.put_run(run)
    services.state.put_if_absent("lease:correction:run_busy", {"id": "other", "at": services.clock.now().timestamp()})
    request = ConfirmationRequest(
        fieldId="meter_number",
        kind="corrected",
        value="12345",
        imageId="s1",
        clientAt="2026-10-15T00:00:00.000Z",
    )
    with pytest.raises(LeaseBusy):
        with bind(services):
            on_confirmation("cap_test", request, "cfm_busy", by="device")
    assert services.state.get_blob(pending_correction_key(run.run_id, "cfm_busy")) is not None
    stored = Reads.model_validate(json.loads(services.storage.get_bytes(reads_key)))
    assert stored.meter.value == "00000"


def test_concurrent_corrections_merge_different_fields(tmp_path: Path) -> None:
    """The correction lease serializes read-modify-write across fields."""
    import threading

    services, _uri, _digest = build_services(tmp_path, "memory")
    reads_key = "captures/cap_test/runs/run_fields/reads/reads.json"
    reads = Reads(
        capture_id="cap_test",
        status="complete",
        meter=MeterRead(status="auto_fill", value="00000", core="00000", confidence=0.5),
        panel=PanelBundle(merged=PanelRead(brand="old", main_breaker_amps=100)),
        confirm=ConfirmBlock(candidates=["00000"]),
    )
    services.storage.put_bytes(reads_key, reads.model_dump_json().encode(), "application/json")
    services.state.put_run(_run(services, "run_fields", artifacts={"reads/reads.json": reads_key}))
    requests = [
        ConfirmationRequest(
            fieldId="meter_number",
            kind="corrected",
            value="12345",
            imageId="s1",
            clientAt="2026-10-15T00:00:00.000Z",
        ),
        ConfirmationRequest(
            fieldId="panel_brand",
            kind="corrected",
            value="new",
            imageId="s1",
            clientAt="2026-10-15T00:00:00.000Z",
        ),
    ]
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def submit(index: int) -> None:
        try:
            barrier.wait()
            with bind(services):
                on_confirmation("cap_test", requests[index], f"cfm_field_{index}", by="device")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    stored = Reads.model_validate(json.loads(services.storage.get_bytes(reads_key)))
    assert stored.meter.value == "12345"
    assert stored.panel.merged.brand == "new"
