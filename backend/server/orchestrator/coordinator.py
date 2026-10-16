"""Single writer of run and stage transitions."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from server.common.logging import log_event
from server.contracts.api import StageData
from server.contracts.jobs import (
    OPTIONAL_STAGES,
    RunRecord,
    StageName,
    StageRecord,
    StageResult,
    StageStatus,
)
from server.eval.sceneio import validate as validate_document
from server.orchestrator.artifacts import overlay_stage_artifacts
from server.orchestrator.clock import parse_iso
from server.orchestrator.events import emit_failed, emit_stage
from server.orchestrator.executor import CoordinatorDied, InlineExecutor
from server.orchestrator.graph import STAGE_NAMES, STAGES, gpu_for_attempt, ready_stages
from server.orchestrator.manual_result import publish_manual_result
from server.orchestrator.modal_spawn import (
    ModalPending,
    cancel_modal,
    collect_modal,
    current_call_id,
    launch_modal,
    sync_volume,
)
from server.orchestrator.outcomes import Decision, Facts, decide
from server.orchestrator.runner import error_parts, is_retryable
from server.orchestrator.runner import run_stage as execute_stage
from server.orchestrator.services import Services
from server.orchestrator.spend import attempt_usd, gpu_spawn_blocked, vlm_budget_usd
from server.orchestrator.stateio import get_json, put_json

_FINAL = {"verdict_ready", "retake_request", "failed"}
# Capture status that matches a composed outcome. Views needed is needs_views.
_KIND_STATUS = {
    "eligible": "complete",
    "needs_more_photos": "needs_views",
    "not_eligible": "complete",
    "manual_review": "manual_review",
}
_VLM_BUCKET = {
    "reads": "vlm_reads",
    "objects": "vlm_objects",
    "objects_3d": "vlm_objects",
    "criteria": "vlm_c3",
}


def _log_handoff(run_id: str, stage: str, msg: str, started: float) -> None:
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    log_event("handoff", msg=msg, run_id=run_id, stage=stage, duration_ms=round(elapsed_ms, 1))


class RunControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_call_id: str | None = None
    coordinator_heartbeat_at: str | None = None
    coordinator_respawns: int = 0
    cancel_requested: bool = False
    prewarmed: bool = False
    final_event_seq: int | None = None
    merge_done: bool = False
    merge_objects_s: float | None = None
    vlm_stopped: bool = False
    no_meter_model: bool = False


class RunCoordinator:
    def __init__(self, services: Services) -> None:
        self.s = services

    def execute_named_stage(self, run_id: str, stage: str, attempt: int, *, gpu: str) -> dict[str, str]:
        """Run one stage attempt and mark it done. Modal's ``Pipeline.run_stage`` calls this."""

        self._apply_hooks(run_id)
        run = self._run(run_id)
        record = self.s.state.get_stage(run_id, stage)  # type: ignore[attr-defined]
        if record is None:
            raise KeyError(stage)
        call_id = current_call_id() or record.worker_call_id or f"modal-{stage}-{attempt}"
        chosen_gpu = record.gpu or (gpu if STAGES[stage].where == "gpu" else None)  # type: ignore[index]
        record = record.model_copy(
            update={
                "attempt": attempt,
                "gpu": chosen_gpu,
                "worker_call_id": call_id,
                "status": "running",
            }
        )
        self.s.state.upsert_stage(record)  # type: ignore[attr-defined]
        started = time.perf_counter()
        sync_volume("reload")
        reload_ms = (time.perf_counter() - started) * 1000.0
        _log_handoff(run_id, stage, f"worker_start reload_ms={reload_ms:.0f}", started)
        if self.s.orch.test_kill_coordinator_after == stage:
            self._hold_for_new_coordinator(run_id, record)
        code = _injected_failure(self.s.orch.test_fail_stage, stage, attempt)
        if code:
            raise RuntimeError(f"{code}: injected failure")
        if _hangs(self.s.orch.test_hang_stage, stage, attempt):
            time.sleep(400)
            return {}
        hydrated = time.perf_counter()
        context = self.s.stage_context(run, record, call_id)
        hydrate_ms = (time.perf_counter() - hydrated) * 1000.0
        ran = time.perf_counter()
        result = execute_stage(
            context,
            stubs=self.s.orch.stage_stubs,
            call_id=call_id,
            heartbeat_s=self.s.orch.heartbeat_s,
        )
        stage_ms = (time.perf_counter() - ran) * 1000.0
        committed = time.perf_counter()
        sync_volume("commit")
        commit_ms = (time.perf_counter() - committed) * 1000.0
        _log_handoff(
            run_id,
            stage,
            (
                f"worker_done reload_ms={reload_ms:.0f} hydrate_ms={hydrate_ms:.0f} "
                f"stage_ms={stage_ms:.0f} commit_ms={commit_ms:.0f}"
            ),
            started,
        )
        fresh = self.s.state.get_stage(run_id, stage) or record
        self._mark_done(run, fresh, result)
        return result.artifacts

    def drive(self, run_id: str, *, max_ticks: int | None = None) -> StageStatus:
        self._apply_hooks(run_id)
        if max_ticks is None:
            max_ticks = 400 if self.s.orch.modal_remote else 40
        last = ""
        status = "running"
        for _ in range(max_ticks):
            status = self.tick(run_id)
            if status not in {"pending", "running"}:
                return status
            snapshot = self._snapshot(run_id)
            if snapshot == last:
                if not self._modal_inflight(run_id) or self.s.orch.coordinator_loop_s <= 0:
                    return status
                # Near the stale bound, a 1s sleep plus a slow poll can step over 180–185s.
                pause = 0.2 if self._near_stale(run_id) else self.s.orch.coordinator_loop_s
                time.sleep(pause)
                continue
            last = snapshot
        return status

    def tick(self, run_id: str) -> StageStatus:
        run = self._run(run_id)
        if run.status not in {"pending", "running"}:
            return run.status
        control = self._control(run_id)
        control.coordinator_heartbeat_at = self.s.clock.iso()
        control.coordinator_call_id = control.coordinator_call_id or f"coord-{run_id}"
        self._save_control(run_id, control)
        if run.status == "pending":
            run.status = "running"
            run.started_at = run.started_at or self.s.clock.iso()
            self.s.state.put_run(run)

        if control.cancel_requested:
            self._cancel_running(run_id)
            run.status = "skipped"
            self.s.state.put_run(run)
            return run.status

        if self._past_deadline(run):
            self._close(run, decide(Facts(hard_stop=True)), "hard stop")
            return run.status

        polled = time.perf_counter()
        self._poll(run)
        poll_ms = (time.perf_counter() - polled) * 1000.0
        run = self._run(run_id)
        if run.status not in {"pending", "running"}:
            return run.status
        self._sweep_stale(run)
        self._sweep_timeouts(run)
        run = self._run(run_id)
        if run.status not in {"pending", "running"}:
            return run.status

        control = self._control(run_id)
        merged = time.perf_counter()
        self._merge_if_ready(run, control)
        merge_ms = (time.perf_counter() - merged) * 1000.0
        spawned_at = time.perf_counter()
        spawned = self._spawn_ready(run, control)
        spawn_ms = (time.perf_counter() - spawned_at) * 1000.0
        kill = self.s.orch.test_kill_coordinator_after
        if kill and kill in spawned:
            self.s.state.put_run(run)
            raise CoordinatorDied(kill)
        poll2 = time.perf_counter()
        self._poll(run)
        poll2_ms = (time.perf_counter() - poll2) * 1000.0
        if self.s.orch.modal_remote and (spawned or poll_ms + merge_ms + spawn_ms + poll2_ms >= 1000):
            _log_handoff(
                run_id,
                spawned[0] if spawned else "-",
                (
                    f"tick poll_ms={poll_ms:.0f} merge_ms={merge_ms:.0f} "
                    f"spawn_ms={spawn_ms:.0f} poll2_ms={poll2_ms:.0f} "
                    f"spawned={','.join(spawned) or '-'}"
                ),
                polled,
            )
        run = self._run(run_id)
        if run.status in {"pending", "running"}:
            self._maybe_finish(run)
            run = self._run(run_id)
        self.s.state.put_run(run)
        return run.status

    def _spawn_ready(self, run: RunRecord, control: RunControl) -> list[str]:
        spawned: list[str] = []
        self._materialize_missing_records(run)
        status = self._status_map(run.run_id)
        names = ready_stages(status)
        if "criteria" in names and not control.merge_done:
            names = [name for name in names if name != "criteria"]
        for name in names:
            fresh = self._run(run.run_id)
            if fresh.status not in {"pending", "running"}:
                break
            if self._past_deadline(fresh):
                self._close(fresh, decide(Facts(hard_stop=True)), "hard stop")
                break
            if self._spawn_one(fresh, name):
                spawned.append(name)
        return spawned

    def _spawn_one(self, run: RunRecord, name: StageName) -> bool:
        spec = STAGES[name]
        rec = self.s.state.get_stage(run.run_id, name)
        if rec is None or rec.status != "pending":
            return False
        attempt = rec.failures + rec.respawns + 1
        gpu = gpu_for_attempt(spec, attempt)
        if gpu and gpu_spawn_blocked(self.s.state.get_spend("total"), self.s.orch):
            self._close(run, decide(Facts(spend_cap=True)), "modal spend cap")
            return False
        call_id = f"call-{name}-{attempt}-{uuid.uuid4().hex[:8]}"
        rec.status = "running"
        rec.attempt = attempt
        rec.gpu = gpu
        rec.worker_call_id = call_id
        rec.started_at = self.s.clock.iso()
        rec.heartbeat = self.s.clock.iso()
        rec.error = None
        rec.error_code = None
        self.s.state.upsert_stage(rec)
        emit_stage(
            self.s.state,
            run.capture_id,
            StageData(stage=name, status="running", attempt=attempt, runId=run.run_id, gpu=gpu),
            clock=self.s.clock,
        )
        # State writes can outlast the deadline. Check again immediately before launch.
        if self._past_deadline(self._run(run.run_id)):
            self._close(self._run(run.run_id), decide(Facts(hard_stop=True)), "hard stop")
            return False
        remote_id = launch_modal(self.s, run.run_id, name, attempt, gpu)
        if remote_id:
            fresh = self.s.state.get_stage(run.run_id, name)
            if fresh is not None and fresh.status == "running":
                fresh.worker_call_id = remote_id
                self.s.state.upsert_stage(fresh)
            return True
        ctx = self.s.stage_context(run, rec, call_id)
        if self._past_deadline(self._run(run.run_id)):
            self._close(self._run(run.run_id), decide(Facts(hard_stop=True)), "hard stop")
            return False
        self._executor().spawn(call_id, ctx)
        return True

    def _poll(self, run: RunRecord) -> None:
        executor = self._executor()
        for rec in self._rows(run.run_id, refresh=True):
            if rec.status != "running" or not rec.worker_call_id:
                continue
            fresh = self.s.state.get_stage(run.run_id, rec.stage)
            if fresh is None or fresh.worker_call_id != rec.worker_call_id or fresh.status != "running":
                continue
            if rec.worker_call_id in executor.calls:
                call = executor.poll(rec.worker_call_id)
                if call.outcome == "running":
                    continue
                if call.outcome == "done" and call.result is not None:
                    self._mark_done(run, fresh, call.result)
                elif call.error is not None:
                    self._mark_error(run, fresh, call.error)
                continue
            if not self.s.orch.modal_remote:
                continue
            try:
                payload = collect_modal(rec.worker_call_id)
            except ModalPending:
                continue
            except Exception as exc:
                latest = self.s.state.get_stage(run.run_id, rec.stage) or fresh
                if latest.status == "running":
                    self._mark_error(run, latest, exc)
                continue
            latest = self.s.state.get_stage(run.run_id, rec.stage) or fresh
            if latest.status != "running":
                continue
            result = _coerce_result(payload)
            if result is not None:
                self._mark_done(run, latest, result)

    def _mark_done(self, run: RunRecord, rec: StageRecord, result: Any) -> None:
        fresh = self.s.state.get_stage(run.run_id, rec.stage)
        if fresh is None or fresh.status != "running":
            return
        if fresh.worker_call_id and rec.worker_call_id and fresh.worker_call_id != rec.worker_call_id:
            return
        rec = fresh
        rec.error = None
        rec.error_code = None
        if rec.stage == "criteria":
            try:
                _validate_verdicts(self.s, result)
            except ValueError as exc:
                self._fail_artifact(run, rec, "criteria", exc)
                return
        spec = STAGES[rec.stage]
        rec.status = "done"
        rec.finished_at = self.s.clock.iso()
        metrics = dict(result.metrics)
        metrics["failures"] = float(rec.failures)
        metrics["respawns"] = float(rec.respawns)
        rec.metrics = metrics
        rec.artifact_uri = next(iter(result.artifacts.values()), None)
        # Publish before done is visible. A downstream worker can observe done
        # before this container's put_run, and must still be able to hydrate.
        put_json(self.s.state, f"stage_artifacts:{run.run_id}:{rec.stage}", dict(result.artifacts))
        self.s.state.upsert_stage(rec)
        self._remember_stage(rec)
        run.artifacts.update(result.artifacts)
        duration = float(metrics.get("duration_s", 0.0))
        usd = attempt_usd(duration, rec.gpu)
        bucket = "gpu" if spec.where == "gpu" else "cpu"
        run.spend_usd[bucket] = run.spend_usd.get(bucket, 0.0) + usd
        self.s.state.add_spend(bucket, usd)
        self.s.state.add_spend("total", usd)
        day = self.s.clock.now().date().isoformat()
        self.s.state.add_spend(f"day:{day}", usd)
        if spec.where == "gpu":
            self.s.state.add_spend(f"gpu:day:{day}", usd)
            put_json(self.s.state, "gpu_last_call_at", self.s.clock.iso())
        vlm = float(metrics.get("vlm_cost_usd", 0.0))
        if vlm:
            key = _VLM_BUCKET.get(rec.stage, "vlm_objects")
            run.spend_usd[key] = run.spend_usd.get(key, 0.0) + vlm
            self.s.state.add_spend(key, vlm)
            self.s.state.add_spend("total", vlm)
        self.s.state.put_run(run)
        emit_stage(
            self.s.state,
            run.capture_id,
            StageData(
                stage=rec.stage,
                status="done",
                attempt=rec.attempt,
                runId=run.run_id,
                gpu=rec.gpu,
                durationS=duration,
            ),
            clock=self.s.clock,
        )
        spent = _vlm_spent(run)
        if rec.stage == "poses":
            self._note_no_meter(run)
        if vlm_budget_usd(spent, self.s.orch) == 0.0 and spent > 0:
            self._close(run, decide(Facts(vlm_budget_exhausted=True)), "vlm hard cap")

    def _mark_error(self, run: RunRecord, rec: StageRecord, exc: BaseException) -> None:
        from server.orchestrator.runner import StageSuperseded

        if isinstance(exc, StageSuperseded):
            return
        spec = STAGES[rec.stage]
        rec.failures += 1
        code, message = error_parts(exc)
        rec.error_code = code
        rec.error = f"{code}: {message}"
        retry = rec.failures < spec.max_attempts and is_retryable(exc)
        if retry:
            rec.status = "pending"
            rec.worker_call_id = None
            self.s.state.upsert_stage(rec)
            self._remember_stage(rec)
            return
        rec.status = "failed"
        rec.finished_at = self.s.clock.iso()
        self.s.state.upsert_stage(rec)
        self._remember_stage(rec)
        emit_stage(
            self.s.state,
            run.capture_id,
            StageData(
                stage=rec.stage,
                status="failed",
                attempt=rec.attempt,
                runId=run.run_id,
                gpu=rec.gpu,
                errorCode=code,
            ),
            clock=self.s.clock,
        )
        if code == "no_meter_anchor":
            self._close(
                run,
                Decision(
                    5,
                    "manual_review",
                    "meter_tap_missing",
                    "failed",
                    "manual_review",
                    "manual_review",
                ),
                message,
            )
            return
        if rec.stage == "validate":
            self._close(
                run,
                decide(Facts(validate_failed=True, images_usable=_images_usable(self.s, run))),
                message,
            )
            return
        if spec.optional or rec.stage in OPTIONAL_STAGES:
            self._degrade(run, rec)
            return
        self._close(
            run,
            decide(Facts(required_exhausted=True, exhausted_stage=rec.stage, exhausted_code=code)),
            message,
        )

    def _sweep_stale(self, run: RunRecord) -> None:
        now = self.s.clock.now()
        for rec in self._rows(run.run_id):
            if rec.status != "running" or not rec.heartbeat:
                continue
            age = (now - parse_iso(rec.heartbeat)).total_seconds()
            if age <= self.s.orch.stale_s:
                continue
            if rec.worker_call_id:
                self._cancel_call(rec.worker_call_id)
            rec.respawns += 1
            rec.status = "stale"
            self.s.state.upsert_stage(rec)
            if rec.respawns <= self.s.orch.max_respawns:
                rec.status = "pending"
                rec.worker_call_id = None
                rec.metrics = {"respawns": float(rec.respawns), "failures": float(rec.failures)}
                self.s.state.upsert_stage(rec)
                continue
            rec.status = "failed"
            rec.error_code = f"stage_stale:{rec.stage}"
            rec.error = f"stage_stale:{rec.stage}: heartbeat older than {self.s.orch.stale_s}s"
            rec.finished_at = self.s.clock.iso()
            self.s.state.upsert_stage(rec)
            self._close(run, decide(Facts(stale_stage=rec.stage)), rec.error)

    def _sweep_timeouts(self, run: RunRecord) -> None:
        now = self.s.clock.now()
        for rec in list(self._rows(run.run_id)):
            # started_at is the spawn. Hydration and the Modal queue sit in
            # front of entered_at, and a 60s stage budget must not include them.
            if rec.status != "running" or not rec.entered_at:
                continue
            spec = STAGES[rec.stage]
            elapsed = (now - parse_iso(rec.entered_at)).total_seconds()
            if elapsed <= spec.timeout_s:
                continue
            if rec.worker_call_id:
                self._cancel_call(rec.worker_call_id)
            self._mark_error(run, rec, TimeoutError(f"timeout: {rec.stage} exceeded {spec.timeout_s}s"))

    def _overlay_artifacts(self, run: RunRecord) -> RunRecord:
        return overlay_stage_artifacts(self.s.state, run)

    def _merge_if_ready(self, run: RunRecord, control: RunControl) -> None:
        if control.merge_done:
            return
        scene = self.s.state.get_stage(run.run_id, "scene")
        objects_3d = self.s.state.get_stage(run.run_id, "objects_3d")
        if scene is None or objects_3d is None:
            return
        if scene.status != "done" or objects_3d.status not in {
            "done",
            "failed",
            "skipped",
        }:
            return
        # Overlay only once both sides are finished. Doing it every tick is
        # eleven blob reads and pushes the stale cancel past 185s.
        run = self._overlay_artifacts(self._run(run.run_id))
        if objects_3d.status == "done":
            sync_volume("reload")
            started = time.perf_counter()
            try:
                _merge_objects(self.s, run)
            except Exception as exc:
                control.merge_objects_s = time.perf_counter() - started
                control.merge_done = True
                self._save_control(run.run_id, control)
                self._close(run, decide(Facts(artifact_invalid="scene")), str(exc))
                return
            control.merge_objects_s = time.perf_counter() - started
        control.merge_done = True
        self._save_control(run.run_id, control)

    def _maybe_finish(self, run: RunRecord) -> None:
        stages = self._rows(run.run_id)
        if any(rec.status == "running" for rec in stages):
            return
        control = self._control(run.run_id)
        status = self._status_map(run.run_id)
        pending_ready = ready_stages(status)
        if "criteria" in pending_ready and not control.merge_done:
            pending_ready = [name for name in pending_ready if name != "criteria"]
        if pending_ready:
            return
        result = self.s.state.get_stage(run.run_id, "result")
        if result is None or result.status != "done":
            return
        self._write_run_files(run)
        self._apply_success(run)

    def _apply_success(self, run: RunRecord) -> None:
        composed = self._composed_outcome(run)
        if self._control(run.run_id).no_meter_model:
            decision = decide(Facts(no_meter_model=True))
            self._finish_with_result(run, decision, composed)
            self._verify_final_event(run)
            return
        if run.degraded:
            decision = decide(Facts(degraded=tuple(stage for stage in run.degraded)))
            self._finish_with_result(run, decision, composed)
            self._verify_final_event(run)
            return
        verdict = _read_json(self.s, self._overlay_artifacts(run), "verdicts/verdict.C.json")
        site = (verdict or {}).get("site") or {}
        decision_name = str(site.get("decision") or "manual_review")
        views = list((verdict or {}).get("views_needed") or [])
        decision = decide(
            Facts(
                site_decision=decision_name,
                views_needed=bool(views),
                show_reject_to_homeowner=self.s.settings.SHOW_REJECT_TO_HOMEOWNER,
            )
        )
        self._apply_decision(run, decision, decision.outcome_kind or "", emit=False)
        self._verify_final_event(run)

    def _finish_with_result(self, run: RunRecord, decision: Decision, composed: tuple[str, str] | None) -> None:
        """Keep the failure code, and the capture status that matches the composed outcome."""
        kind = decision.outcome_kind
        status = decision.capture_status
        if composed is not None:
            kind, status = composed
        run.outcome_kind = kind
        run.failure_code = decision.failure_code
        run.status = "done"
        run.finished_at = run.finished_at or self.s.clock.iso()
        self.s.state.set_status(run.capture_id, status)
        self.s.state.put_run(run)

    def _composed_outcome(self, run: RunRecord) -> tuple[str, str] | None:
        fresh = self._overlay_artifacts(run)
        key = fresh.artifacts.get("result/result.json")
        if not isinstance(key, str) or not key:
            return None
        try:
            payload = json.loads(self.s.storage.get_bytes(key))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        outcome = payload.get("outcome") or {}
        if not isinstance(outcome, dict):
            return None
        kind = outcome.get("kind")
        status = _KIND_STATUS.get(kind)
        if status is None or not isinstance(kind, str):
            return None
        return kind, status

    def _close(self, run: RunRecord, decision: Decision, message: str) -> None:
        self._cancel_running(run.run_id)
        self._write_manual_result(run, decision.failure_code or message)
        self._apply_decision(run, decision, message, emit=decision.event == "failed")
        run.status = "failed"
        run.finished_at = self.s.clock.iso()
        self._write_run_files(run)
        self.s.state.put_run(run)

    def _apply_decision(self, run: RunRecord, decision: Decision, message: str, *, emit: bool) -> None:
        run.outcome_kind = decision.outcome_kind
        run.failure_code = decision.failure_code
        if decision.continue_pipeline:
            self.s.state.put_run(run)
            return
        run.status = "done" if decision.capture_status != "failed" else "failed"
        if decision.event == "failed":
            run.status = "failed"
        run.finished_at = run.finished_at or self.s.clock.iso()
        self.s.state.set_status(run.capture_id, decision.capture_status)
        self.s.state.put_run(run)
        if emit and decision.failure_code and decision.next:
            control = self._control(run.run_id)
            if control.final_event_seq is None:
                event = emit_failed(
                    self.s.state,
                    run.capture_id,
                    run.run_id,
                    decision.failure_code,
                    message,
                    decision.next,
                    clock=self.s.clock,
                )
                control.final_event_seq = event.seq
                self._save_control(run.run_id, control)

    def _verify_final_event(self, run: RunRecord) -> None:
        events = self.s.state.events_after(run.capture_id, 0, limit=500)
        finals = [event for event in events if event.type in _FINAL and event.run_id == run.run_id]
        control = self._control(run.run_id)
        if len(finals) == 1:
            control.final_event_seq = finals[0].seq
            self._save_control(run.run_id, control)
            return
        if control.final_event_seq is not None:
            return
        event = emit_failed(
            self.s.state,
            run.capture_id,
            run.run_id,
            "result_no_event",
            "result finished without exactly one final event",
            "manual_review",
            clock=self.s.clock,
        )
        control.final_event_seq = event.seq
        self._save_control(run.run_id, control)
        self.s.state.set_status(run.capture_id, "manual_review")

    def _degrade(self, run: RunRecord, rec: StageRecord) -> None:
        if rec.stage not in run.degraded:
            run.degraded.append(rec.stage)
        self.s.state.put_run(run)

    def _cancel_running(self, run_id: str) -> None:
        for rec in self.s.state.stages(run_id):
            if rec.status == "running" and rec.worker_call_id:
                self._cancel_call(rec.worker_call_id)
                rec.status = "skipped"
                rec.finished_at = self.s.clock.iso()
                self.s.state.upsert_stage(rec)

    def _write_manual_result(self, run: RunRecord, reason: str) -> None:
        if run.artifacts.get("result/result.json"):
            return
        stage = self.s.state.get_stage(run.run_id, "result")
        if stage is not None and stage.status == "done":
            return
        key = publish_manual_result(self.s, run, reason)
        run.artifacts["result/result.json"] = key
        put_json(self.s.state, f"stage_artifacts:{run.run_id}:result", {"result/result.json": key})

    def _write_run_files(self, run: RunRecord) -> None:
        timings: dict[str, Any] = {}
        for rec in self.s.state.stages(run.run_id):
            metrics = rec.metrics or {}
            timings[rec.stage] = {
                "started_at": rec.started_at,
                "finished_at": rec.finished_at,
                "duration_s": metrics.get("duration_s"),
                "enter_s": metrics.get("enter_s"),
                "attempt": rec.attempt,
                "gpu": rec.gpu,
                "failures": rec.failures,
                "respawns": rec.respawns,
            }
        timings["merge_objects_s"] = self._control(run.run_id).merge_objects_s
        prefix = f"captures/{run.capture_id}/runs/{run.run_id}/run"
        self._put_json(f"{prefix}/timings.json", timings)
        self._put_json(f"{prefix}/cost.json", {"spend_usd": run.spend_usd})
        self._put_json(
            f"{prefix}/stages.json", [rec.model_dump(mode="json") for rec in self.s.state.stages(run.run_id)]
        )
        run.artifacts["run/timings.json"] = f"{prefix}/timings.json"
        run.artifacts["run/cost.json"] = f"{prefix}/cost.json"
        run.artifacts["run/stages.json"] = f"{prefix}/stages.json"
        self.s.state.put_run(run)
        sync_volume("commit")

    def _put_json(self, key: str, body: Any) -> None:
        self.s.storage.put_bytes(key, json.dumps(body).encode(), "application/json")

    def _past_deadline(self, run: RunRecord) -> bool:
        return self.s.clock.now() >= parse_iso(run.deadline_at)

    def _rows(self, run_id: str, *, refresh: bool = False) -> list[StageRecord]:
        """Stage rows for this tick. A refresh is one point-read of the eleven keys."""

        cache = getattr(self, "_rows_cache", None)
        if refresh or cache is None or cache[0] != run_id:
            rows = list(self.s.state.stages(run_id))
            self._rows_cache = (run_id, rows)
            return rows
        return cache[1]

    def _remember_stage(self, rec: StageRecord) -> None:
        rows = self._rows(rec.run_id)
        for index, row in enumerate(rows):
            if row.stage == rec.stage:
                rows[index] = rec
                return
        rows.append(rec)

    def _status_map(self, run_id: str) -> dict[StageName, StageStatus]:
        found = {rec.stage: rec.status for rec in self._rows(run_id)}
        return {name: found.get(name, "pending") for name in STAGE_NAMES}

    def _materialize_missing_records(self, run: RunRecord) -> None:
        """Recovery for runs written by older builds: a run can lack stage records
        entirely (records materialize as the API schedules stages). The status map
        treats a missing record as pending, but _spawn_one refuses to start a stage
        with no record — so such a run hangs forever between ticks. Idempotently
        create only the missing pending records: existing records keep their
        attempts, failures and artifacts, and terminal runs never reach here
        because tick returns before spawning.
        """
        now = self.s.clock.iso()
        for name in STAGE_NAMES:
            if self.s.state.get_stage(run.run_id, name) is None:
                self.s.state.upsert_stage(
                    StageRecord(run_id=run.run_id, stage=name, status="pending", attempt=1, heartbeat=now)
                )

    def _snapshot(self, run_id: str) -> str:
        parts = [f"{rec.stage}:{rec.status}:{rec.attempt}:{rec.failures}:{rec.respawns}" for rec in self._rows(run_id)]
        run = self._run(run_id)
        return run.status + "|" + ",".join(parts)

    def _run(self, run_id: str) -> RunRecord:
        run = self.s.state.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _control(self, run_id: str) -> RunControl:
        raw = get_json(self.s.state, f"run_control:{run_id}")
        if not raw:
            return RunControl()
        return RunControl.model_validate(raw)

    def _save_control(self, run_id: str, control: RunControl) -> None:
        put_json(self.s.state, f"run_control:{run_id}", control.model_dump(mode="json"))

    def _executor(self) -> InlineExecutor:
        return self.s.executor

    def _cancel_call(self, call_id: str) -> None:
        executor = self._executor()
        executor.cancel(call_id)
        if call_id in executor.calls or not self.s.orch.modal_remote:
            return
        cancel_modal(call_id)

    def _near_stale(self, run_id: str) -> bool:
        now = self.s.clock.now()
        for rec in self._rows(run_id):
            if rec.status != "running" or not rec.heartbeat:
                continue
            age = (now - parse_iso(rec.heartbeat)).total_seconds()
            if age >= self.s.orch.stale_s - 10:
                return True
        return False

    def _modal_inflight(self, run_id: str) -> bool:
        if not self.s.orch.modal_remote:
            return False
        executor = self._executor()
        for rec in self._rows(run_id):
            if rec.status == "running" and rec.worker_call_id and rec.worker_call_id not in executor.calls:
                return True
        return False

    def _apply_hooks(self, run_id: str) -> None:
        raw = get_json(self.s.state, f"run_hooks:{run_id}") or {}
        if not raw:
            return
        if "modal_remote" in raw:
            self.s.orch.modal_remote = bool(raw["modal_remote"])
        if "stage_stubs" in raw:
            self.s.orch.stage_stubs = bool(raw["stage_stubs"])
        for name in ("test_hang_stage", "test_fail_stage", "test_kill_coordinator_after"):
            if name in raw:
                setattr(self.s.orch, name, str(raw[name] or ""))

    def _hold_for_new_coordinator(self, run_id: str, record: StageRecord) -> None:
        """Stay in the call until a sweeper has re-spawned the coordinator."""

        deadline = time.monotonic() + 500
        while time.monotonic() < deadline:
            raw = get_json(self.s.state, f"run_control:{run_id}") or {}
            if int(raw.get("coordinator_respawns") or 0) >= 1:
                return
            record.heartbeat = self.s.clock.iso()
            self.s.state.upsert_stage(record)
            time.sleep(2)

    def _fail_artifact(self, run: RunRecord, rec: StageRecord, kind: str, exc: BaseException) -> None:
        rec.status = "failed"
        rec.finished_at = self.s.clock.iso()
        rec.error_code = f"artifact_invalid:{kind}"
        rec.error = str(exc)
        self.s.state.upsert_stage(rec)
        emit_stage(
            self.s.state,
            run.capture_id,
            StageData(
                stage=rec.stage,
                status="failed",
                attempt=rec.attempt,
                runId=run.run_id,
                gpu=rec.gpu,
                errorCode=rec.error_code,
            ),
            clock=self.s.clock,
        )
        self._close(run, decide(Facts(artifact_invalid=kind)), str(exc))

    def _note_no_meter(self, run: RunRecord) -> None:
        report = _read_json(self.s, run, "poses/registration_report.json")
        if not report or report.get("status") != "no_meter_model":
            return
        control = self._control(run.run_id)
        control.no_meter_model = True
        self._save_control(run.run_id, control)


def _vlm_spent(run: RunRecord) -> float:
    return sum(float(run.spend_usd.get(key, 0.0)) for key in ("vlm_reads", "vlm_objects", "vlm_c3"))


def _read_json(services: Services, run: RunRecord, artifact: str) -> dict[str, Any] | None:
    if services.orch.modal_remote:
        sync_volume("reload")
    key = run.artifacts.get(artifact)
    if not key or not services.storage.exists(key):
        return None
    return json.loads(services.storage.get_bytes(key))


def _merge_objects(services: Services, run: RunRecord) -> None:
    """Call WS-06 when it is importable. Otherwise keep the stub scene bytes."""
    scene_key = run.artifacts.get("scene/scene.json")
    objects_key = run.artifacts.get("objects/objects_3d.json")
    if not scene_key:
        raise RuntimeError("artifact_invalid:scene missing")
    try:
        from server.scene import merge_objects
    except ImportError:
        merge_objects = None
    data = services.storage.get_bytes(scene_key)
    if merge_objects is not None:
        work = services.work_root / run.run_id / "merge"
        work.mkdir(parents=True, exist_ok=True)
        scene_path = work / "scene.json"
        scene_path.write_bytes(data)
        objects_path = work / "objects_3d.json"
        if objects_key:
            objects_path.write_bytes(services.storage.get_bytes(objects_key))
        merge_objects(scene_path, objects_path)
        data = scene_path.read_bytes()
        services.storage.put_bytes(scene_key, data, "application/json")
    _validate_scene(data)


def _validate_scene(data: bytes) -> None:
    validate_document(json.loads(data))


def _validate_verdicts(services: Services, result: Any) -> None:
    artifacts = getattr(result, "artifacts", None) or {}
    for name, key in artifacts.items():
        if not (str(name).startswith("verdicts/verdict.") and str(name).endswith(".json")):
            continue
        if not services.storage.exists(key):  # type: ignore[attr-defined]
            raise ValueError(f"artifact_invalid:criteria missing {name}")
        validate_document(json.loads(services.storage.get_bytes(key)))  # type: ignore[attr-defined]


def _coerce_result(payload: object) -> StageResult | None:
    if isinstance(payload, StageResult):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("artifacts"), dict):
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        return StageResult(artifacts=payload["artifacts"], metrics=metrics, warnings=warnings)
    return None


def _hangs(spec: str, stage: str, attempt: int) -> bool:
    if not spec or ":" not in spec:
        return False
    name, rest = spec.split(":", 1)
    try:
        attempts = {int(part) for part in rest.split(",") if part}
    except ValueError:
        return False
    return name == stage and attempt in attempts


def _injected_failure(spec: str, stage: str, attempt: int) -> str | None:
    if not spec:
        return None
    parts = spec.split(":")
    if len(parts) != 3:
        return None
    name, code, count = parts
    try:
        limit = int(count)
    except ValueError:
        return None
    if name == stage and attempt <= limit:
        return code
    return None


def _images_usable(services: Services, run: RunRecord) -> bool:
    doc = _read_json(services, run, "packet/validation.json")
    if isinstance(doc, dict) and "imagesUsable" in doc:
        return bool(doc["imagesUsable"])
    key = f"captures/{run.capture_id}/packet.json"
    if services.storage.exists(key):  # type: ignore[attr-defined]
        packet = json.loads(services.storage.get_bytes(key))  # type: ignore[attr-defined]
        if isinstance(packet, dict) and "imagesUsable" in packet:
            return bool(packet["imagesUsable"])
    return True
