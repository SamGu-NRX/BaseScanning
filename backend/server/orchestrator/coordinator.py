"""Single writer of run and stage transitions."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from server.contracts.api import StageData
from server.contracts.jobs import (
    OPTIONAL_STAGES,
    RunRecord,
    StageName,
    StageRecord,
    StageStatus,
)
from server.eval.sceneio import validate as validate_document
from server.orchestrator.clock import parse_iso
from server.orchestrator.events import emit_failed, emit_stage
from server.orchestrator.executor import CoordinatorDied, InlineExecutor
from server.orchestrator.graph import STAGE_NAMES, STAGES, gpu_for_attempt, ready_stages
from server.orchestrator.modal_spawn import launch_modal
from server.orchestrator.outcomes import Decision, Facts, decide
from server.orchestrator.runner import error_parts, is_retryable
from server.orchestrator.runner import run_stage as execute_stage
from server.orchestrator.services import Services
from server.orchestrator.spend import attempt_usd, gpu_spawn_blocked, vlm_budget_usd
from server.orchestrator.stateio import get_json, put_json

_FINAL = {"verdict_ready", "retake_request", "failed"}
_VLM_BUCKET = {
    "reads": "vlm_reads",
    "objects": "vlm_objects",
    "objects_3d": "vlm_objects",
    "criteria": "vlm_c3",
}


class RunControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_call_id: str | None = None
    coordinator_heartbeat_at: str | None = None
    coordinator_respawns: int = 0
    cancel_requested: bool = False
    prewarmed: bool = False
    final_event_seq: int | None = None
    merge_done: bool = False
    vlm_stopped: bool = False
    no_meter_model: bool = False


class RunCoordinator:
    def __init__(self, services: Services) -> None:
        self.s = services

    def execute_named_stage(self, run_id: str, stage: str, attempt: int, *, gpu: str) -> dict[str, str]:
        """Run one stage attempt on this process. Modal's ``Pipeline.run_stage`` calls this."""

        run = self._run(run_id)
        record = self.s.state.get_stage(run_id, stage)  # type: ignore[attr-defined]
        if record is None:
            raise KeyError(stage)
        call_id = f"modal-{stage}-{attempt}"
        record = record.model_copy(update={"attempt": attempt, "gpu": gpu, "worker_call_id": call_id})
        self.s.state.upsert_stage(record)  # type: ignore[attr-defined]
        context = self.s.stage_context(run, record, call_id)
        result = execute_stage(context, stubs=self.s.orch.stage_stubs, call_id=call_id)
        return result.artifacts

    def drive(self, run_id: str, *, max_ticks: int = 40) -> StageStatus:
        last = ""
        status = "running"
        for _ in range(max_ticks):
            status = self.tick(run_id)
            if status not in {"pending", "running"}:
                return status
            snapshot = self._snapshot(run_id)
            if snapshot == last:
                return status
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

        if control.cancel_requested:
            self._cancel_running(run_id)
            run.status = "skipped"
            self.s.state.put_run(run)
            return run.status

        if self._past_deadline(run):
            self._close(run, decide(Facts(hard_stop=True)), "hard stop")
            return run.status

        self._poll(run)
        run = self._run(run_id)
        if run.status not in {"pending", "running"}:
            return run.status
        self._sweep_stale(run)
        self._sweep_timeouts(run)
        run = self._run(run_id)
        if run.status not in {"pending", "running"}:
            return run.status

        control = self._control(run_id)
        self._merge_if_ready(run, control)
        spawned = self._spawn_ready(run, control)
        kill = self.s.orch.test_kill_coordinator_after
        if kill and kill in spawned:
            self.s.state.put_run(run)
            raise CoordinatorDied(kill)
        self._poll(run)
        run = self._run(run_id)
        if run.status in {"pending", "running"}:
            self._maybe_finish(run)
            run = self._run(run_id)
        self.s.state.put_run(run)
        return run.status

    def _spawn_ready(self, run: RunRecord, control: RunControl) -> list[str]:
        spawned: list[str] = []
        status = self._status_map(run.run_id)
        names = ready_stages(status)
        if "criteria" in names and not control.merge_done:
            names = [name for name in names if name != "criteria"]
        for name in names:
            if self._run(run.run_id).status not in {"pending", "running"}:
                break
            if self._spawn_one(run, name):
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
        ctx = self.s.stage_context(run, rec, call_id)
        if not launch_modal(self.s, ctx, gpu):
            self._executor().spawn(call_id, ctx)
        return True

    def _poll(self, run: RunRecord) -> None:
        executor = self._executor()
        for rec in self.s.state.stages(run.run_id):
            if rec.status != "running" or not rec.worker_call_id:
                continue
            if rec.worker_call_id not in executor.calls:
                continue
            call = executor.poll(rec.worker_call_id)
            fresh = self.s.state.get_stage(run.run_id, rec.stage)
            if fresh is None or fresh.worker_call_id != rec.worker_call_id:
                continue
            if call.outcome == "running":
                continue
            if call.outcome == "done" and call.result is not None:
                self._mark_done(run, fresh, call.result)
            elif call.error is not None:
                self._mark_error(run, fresh, call.error)

    def _mark_done(self, run: RunRecord, rec: StageRecord, result: Any) -> None:
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
        self.s.state.upsert_stage(rec)
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
            return
        rec.status = "failed"
        rec.finished_at = self.s.clock.iso()
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
                errorCode=code,
            ),
            clock=self.s.clock,
        )
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
        for rec in self.s.state.stages(run.run_id):
            if rec.status != "running" or not rec.heartbeat:
                continue
            age = (now - parse_iso(rec.heartbeat)).total_seconds()
            if age <= self.s.orch.stale_s:
                continue
            if rec.worker_call_id:
                self._executor().cancel(rec.worker_call_id)
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
        for rec in list(self.s.state.stages(run.run_id)):
            if rec.status != "running" or not rec.started_at:
                continue
            spec = STAGES[rec.stage]
            elapsed = (now - parse_iso(rec.started_at)).total_seconds()
            if elapsed <= spec.timeout_s:
                continue
            if rec.worker_call_id:
                self._executor().cancel(rec.worker_call_id)
            self._mark_error(run, rec, TimeoutError(f"timeout: {rec.stage} exceeded {spec.timeout_s}s"))

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
        if objects_3d.status == "done":
            try:
                _merge_objects(self.s, run)
            except Exception as exc:
                control.merge_done = True
                self._save_control(run.run_id, control)
                self._close(run, decide(Facts(artifact_invalid="scene")), str(exc))
                return
        control.merge_done = True
        self._save_control(run.run_id, control)

    def _maybe_finish(self, run: RunRecord) -> None:
        stages = self.s.state.stages(run.run_id)
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
        if self._control(run.run_id).no_meter_model:
            decision = decide(Facts(no_meter_model=True))
            run.outcome_kind = decision.outcome_kind
            run.failure_code = decision.failure_code
            run.status = "done"
            run.finished_at = run.finished_at or self.s.clock.iso()
            self.s.state.set_status(run.capture_id, decision.capture_status)
            self.s.state.put_run(run)
            return
        if run.degraded:
            decision = decide(Facts(degraded=tuple(stage for stage in run.degraded)))
            self._apply_decision(run, decision, "degraded optional stage", emit=False)
            return
        verdict = _read_json(self.s, run, "verdicts/verdict.C.json")
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

    def _close(self, run: RunRecord, decision: Decision, message: str) -> None:
        self._cancel_running(run.run_id)
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
        executor = self._executor()
        for rec in self.s.state.stages(run_id):
            if rec.status == "running" and rec.worker_call_id:
                executor.cancel(rec.worker_call_id)
                rec.status = "skipped"
                rec.finished_at = self.s.clock.iso()
                self.s.state.upsert_stage(rec)

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

    def _put_json(self, key: str, body: Any) -> None:
        self.s.storage.put_bytes(key, json.dumps(body).encode(), "application/json")

    def _past_deadline(self, run: RunRecord) -> bool:
        return self.s.clock.now() >= parse_iso(run.deadline_at)

    def _status_map(self, run_id: str) -> dict[StageName, StageStatus]:
        found = {rec.stage: rec.status for rec in self.s.state.stages(run_id)}
        return {name: found.get(name, "pending") for name in STAGE_NAMES}

    def _snapshot(self, run_id: str) -> str:
        parts = [
            f"{rec.stage}:{rec.status}:{rec.attempt}:{rec.failures}:{rec.respawns}"
            for rec in self.s.state.stages(run_id)
        ]
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
