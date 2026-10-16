"""Typed event emission. ``data`` is camelCase (SD-19)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from server.contracts._base import dump_json
from server.contracts.api import (
    CaptureCheckData,
    EventType,
    FailedData,
    HintData,
    StageData,
)
from server.contracts.jobs import EventRecord, StageName
from server.orchestrator.clock import Clock


def emit(
    state: Any,
    capture_id: str,
    event_type: EventType,
    payload: BaseModel,
    *,
    run_id: str | None = None,
    stage: StageName | None = None,
    clock: Clock | None = None,
) -> EventRecord:
    del clock
    return state.append_event(
        capture_id,
        event_type,
        dump_json(payload),
        run_id=run_id,
        stage=stage,
    )


def emit_stage(
    state: Any,
    capture_id: str,
    payload: StageData,
    *,
    clock: Clock | None = None,
) -> EventRecord:
    return emit(
        state,
        capture_id,
        "stage",
        payload,
        run_id=payload.runId,
        stage=payload.stage,
        clock=clock,
    )


def emit_failed(
    state: Any, capture_id: str, run_id: str, code: str, message: str, next_: str, *, clock: Clock
) -> EventRecord:
    return emit(
        state,
        capture_id,
        "failed",
        FailedData(code=code, message=message, next=next_),  # type: ignore[arg-type]
        run_id=run_id,
        clock=clock,
    )


def emit_hint(state: Any, capture_id: str, hint: HintData, *, clock: Clock) -> EventRecord:
    return emit(state, capture_id, "hint", hint, clock=clock)


def emit_capture_check(state: Any, capture_id: str, body: CaptureCheckData, *, clock: Clock) -> EventRecord:
    return emit(state, capture_id, "capture_check", body, clock=clock)
