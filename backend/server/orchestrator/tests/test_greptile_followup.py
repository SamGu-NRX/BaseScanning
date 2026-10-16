"""Regressions from the Greptile apex review of PR #247.

Each test names the finding it pins: amendment packet hydration, confirmation
persistence, per-attempt timeout budgets, and the run's own rules profile.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from server.contracts.api import ConfirmationRequest
from server.contracts.jobs import FileRecord, RunRecord, StageRecord
from server.orchestrator.api import on_confirmation
from server.orchestrator.artifacts import overlay_stage_artifacts
from server.orchestrator.clock import Clock
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.events import emit_failed
from server.orchestrator.services import bind
from server.orchestrator.stubs._common import scene_document
from server.orchestrator.tests.conftest import build_services
from server.packet.integrity import check_files
from server.packet.report import Bag
from server.reads.types import ConfirmBlock, MeterRead, Reads


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
    for upstream in ("validate", "poses", "objects", "reads", "scale", "dense", "coverage", "scene", "objects_3d", "placement"):
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
