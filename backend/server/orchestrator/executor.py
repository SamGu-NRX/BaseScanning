"""In-process stage executor. Modal spawn lives in ``modal_functions``; tests use this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from server.contracts.jobs import StageContext, StageName, StageResult
from server.orchestrator.config import OrchConfig
from server.orchestrator.heartbeat import StageSuperseded
from server.orchestrator.runner import run_stage

OutcomeKind = Literal["running", "done", "error"]


@dataclass
class Call:
    call_id: str
    stage: StageName
    attempt: int
    ctx: StageContext | None = None
    outcome: OutcomeKind = "running"
    result: StageResult | None = None
    error: BaseException | None = None
    hanging: bool = False
    held: bool = False
    cancelled: bool = False


@dataclass
class InlineExecutor:
    """Runs stages on the caller thread, except hangs and the kill-hook hold."""

    config: OrchConfig
    stubs: bool
    calls: dict[str, Call] = field(default_factory=dict)

    def spawn(self, call_id: str, ctx: StageContext) -> str:
        call = Call(call_id=call_id, stage=ctx.stage, attempt=ctx.attempt, ctx=ctx)
        self.calls[call_id] = call
        if self._hangs(ctx.stage, ctx.attempt):
            call.hanging = True
            return call_id
        if self._held(ctx.stage):
            call.held = True
            return call_id
        self._finish(call)
        return call_id

    def poll(self, call_id: str) -> Call:
        call = self.calls[call_id]
        if call.hanging:
            if call.cancelled:
                call.hanging = False
                call.outcome = "error"
                call.error = StageSuperseded("superseded")
            return call
        if call.held:
            call.held = False
            self._finish(call)
        return call

    def cancel(self, call_id: str) -> None:
        call = self.calls.get(call_id)
        if call is not None:
            call.cancelled = True

    def alive(self, call_id: str) -> bool:
        call = self.calls.get(call_id)
        return bool(call and (call.hanging or call.held) and not call.cancelled and call.outcome == "running")

    def _finish(self, call: Call) -> None:
        if call.ctx is None:
            return
        code = self._fail_code(call.stage, call.attempt)
        if code:
            call.outcome = "error"
            call.error = RuntimeError(f"{code}: injected failure")
            return
        try:
            call.result = run_stage(
                call.ctx,
                stubs=self.stubs,
                call_id=call.call_id,
                heartbeat_s=self.config.heartbeat_s,
            )
            call.outcome = "done"
        except Exception as exc:  # stage failures are data, not coordinator crashes
            call.outcome = "error"
            call.error = exc

    def _hangs(self, stage: StageName, attempt: int) -> bool:
        parsed = _stage_attempts(self.config.test_hang_stage)
        return parsed is not None and parsed[0] == stage and attempt in parsed[1]

    def _held(self, stage: StageName) -> bool:
        target = self.config.test_kill_coordinator_after
        return bool(target) and target == stage

    def _fail_code(self, stage: StageName, attempt: int) -> str | None:
        spec = self.config.test_fail_stage
        if not spec:
            return None
        parts = spec.split(":")
        if len(parts) != 3:
            return None
        name, code, count = parts
        if name == stage and attempt <= int(count):
            return code
        return None


def _stage_attempts(spec: str) -> tuple[str, set[int]] | None:
    if not spec or ":" not in spec:
        return None
    name, rest = spec.split(":", 1)
    return name, {int(part) for part in rest.split(",") if part}


class CoordinatorDied(RuntimeError):
    """In-process stand-in for ``os._exit`` after ``HSS_ORCH_TEST_KILL_COORDINATOR_AFTER``."""
