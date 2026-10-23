"""Run one stage in-process. Failures are raised. Uploads happen only after success."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from server.contracts.jobs import StageContext, StageName, StageResult
from server.orchestrator import stage_selection
from server.orchestrator.artifacts import _safe_rel, existing_object, local_artifact
from server.orchestrator.clock import Clock
from server.orchestrator.graph import STAGES
from server.orchestrator.heartbeat import Heartbeat, StageSuperseded

RETRYABLE_TYPES = (RuntimeError, OSError, TimeoutError)

# Pinned contract, shared with burst14-bs-result-names-stand-ins. Copy verbatim:
# every stage's run publishes one marker at impl/<stage>.json recording which
# implementation ran. "stand-in" means a module under server.orchestrator.stubs ran.
IMPL_SCHEMA = "hss-stage-impl/v1"

# Fail fast on a bad HSS_ORCH_REAL_STAGES: every process that can run a stage
# imports this module, so import time is that process's startup. Unset or valid,
# this is one cached dict lookup; invalid, the refusals below raise here.
stage_selection.real_stages()


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, StageSuperseded):
        return False
    # A missing meter tap will not appear on a retry. SD-97: close on the first attempt.
    if getattr(exc, "code", None) == "no_meter_anchor":
        return False
    if isinstance(exc, RETRYABLE_TYPES):
        return True
    name = type(exc).__name__
    module = type(exc).__module__
    if module.startswith("torch") and name == "OutOfMemoryError":
        return True
    return module.startswith("modal.exception") or module.startswith("modal") and name.endswith("Error")


def stage_callable(stage: StageName, *, stubs: bool) -> Any:
    spec = STAGES[stage]
    # A stubs_exempt stage is deterministic CPU work (no GPU, network or credentials);
    # it runs its real implementation even on the stub path. That is the point: the
    # local stub journey exercises the real placement solver.
    # HSS_ORCH_REAL_STAGES (server/orchestrator/stage_selection.py) then names
    # individual stages that also run real under stubs; everything else keeps
    # today's behaviour exactly. The signature stays keyword-only in `stubs` so the
    # capture-placement runner's per-run selection override (#292) keeps working.
    if stubs and not spec.stubs_exempt and not stage_selection.is_real(stage):
        module = importlib.import_module(f"server.orchestrator.stubs.{stage}")
        return module.run
    module = importlib.import_module(f"server.{spec.module}")
    return getattr(module, spec.entry)


def run_stage(
    ctx: StageContext,
    *,
    stubs: bool,
    call_id: str = "",
    heartbeat_s: float = 0,
    clock: Clock | None = None,
) -> StageResult:
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    fn = stage_callable(ctx.stage, stubs=stubs)
    clock = clock or Clock()
    with Heartbeat(ctx.state, ctx.run_id, ctx.stage, call_id or "local", heartbeat_s, clock):
        result = fn(ctx)
    if not isinstance(result, StageResult):
        raise RuntimeError(f"{ctx.stage} returned {type(result).__name__}")
    stored = _publish(ctx, result)
    _publish_impl_marker(ctx, stored, fn)
    result.artifacts = stored
    return result


def _publish_impl_marker(ctx: StageContext, stored: dict[str, str], fn: Any) -> None:
    """Publish impl/<stage>.json: which implementation ran, under the pinned contract.

    The record describes the callable that actually executed — read off `fn`
    itself, not re-derived from the selection — so a per-run override (the
    capture-placement runner's selection, #292) is recorded as what it ran.
    Published only on success, like every other artifact of the stage.
    """

    module_name = getattr(fn, "__module__", "") or "unknown"
    function_name = getattr(fn, "__qualname__", "") or getattr(fn, "__name__", "") or "run"
    if "<" in function_name:  # a local wrapper; name the closest honest dotted path
        function_name = getattr(fn, "__name__", "run")
    key = f"impl/{ctx.stage}.json"
    storage_key = f"captures/{ctx.capture_id}/runs/{ctx.run_id}/{key}"
    body = {
        "schema": IMPL_SCHEMA,
        "stage": ctx.stage,
        "implementation": "stand-in" if module_name.startswith("server.orchestrator.stubs.") else "real",
        "callable": f"{module_name}.{function_name}",
    }
    ctx.storage.put_bytes(storage_key, (json.dumps(body, indent=2) + "\n").encode(), "application/json")
    stored[key] = storage_key


def _publish(ctx: StageContext, result: StageResult) -> dict[str, str]:
    stored: dict[str, str] = {}
    prefix = f"captures/{ctx.capture_id}/runs/{ctx.run_id}"
    root = ctx.work_dir.resolve()
    for key, rel in result.artifacts.items():
        if _safe_rel(key) is None:
            continue
        storage_key = f"{prefix}/{key}".rstrip("/")
        source = local_artifact(ctx.work_dir, rel)
        if source is not None and source.is_dir():
            wrote = False
            for child in source.rglob("*"):
                if not child.is_file():
                    continue
                resolved = child.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                suffix = child.relative_to(source).as_posix()
                ctx.storage.put_bytes(f"{storage_key}/{suffix}", resolved.read_bytes(), _ctype(resolved))
                wrote = True
            if not wrote:
                ctx.storage.put_bytes(f"{storage_key}/.keep", b"", "application/octet-stream")
            stored[key] = f"{storage_key}/.keep"
            continue
        if source is not None and source.is_file():
            ctx.storage.put_bytes(storage_key, source.read_bytes(), _ctype(source))
            stored[key] = storage_key
            continue
        # A stage may return a storage key (WS-08 does today) or a work-dir path it
        # has not written yet. Never replace an existing object with empty bytes.
        # A path outside the work dir is not a storage key; do not ask the backend.
        if _safe_rel(rel) is None:
            continue
        kept = existing_object(ctx.storage, rel, storage_key)
        if kept is not None:
            stored[key] = kept
    return stored


def _ctype(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".html":
        return "text/html"
    return "application/octet-stream"


def error_parts(exc: BaseException) -> tuple[str, str]:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code and " " not in code:
        text = str(exc)
        return code, text if text and text != code else code
    text = str(exc)
    if ":" in text:
        code, _, message = text.partition(":")
        code = code.strip()
        if code and " " not in code:
            return code, message.strip() or text
    return type(exc).__name__, text
