"""Spawn a stage on Modal when this process is configured to, otherwise stay inline.

Imported by the coordinator. Modal itself is imported only when a remote spawn
is actually requested, so unit tests and ``run-local`` never need a token.
"""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from server.orchestrator.graph import STAGES
from server.orchestrator.services import Services

# One in-flight poll per call. FunctionGetOutputs can sit for its grace period
# even when timeout is 0; a second poll waits on the same future instead of
# stacking RPCs, and the coordinator tick is never blocked for more than a second.
_COLLECT_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hss-modal-collect")
_COLLECT_INFLIGHT: dict[str, Future[object]] = {}


class ModalPending(Exception):
    """The Modal call is still running. ``FunctionCall.get(timeout=0)`` raised."""


def launch_modal(services: Services, run_id: str, stage: str, attempt: int, gpu: str | None) -> str | None:
    """Object id of the Modal call, or None when the caller should run the stage inline.

    The worker hydrates the packet and prior artifacts. This call passes ids only.
    """

    from server.orchestrator.modal_functions import modal_available

    if not services.orch.modal_remote or not modal_available():
        return None
    return _remote_spawn(run_id, stage, attempt, gpu)


def _remote_spawn(run_id: str, stage: str, attempt: int, gpu: str | None) -> str:
    """Look up the deployed function. A local ``App`` imported in the container is not running."""

    import modal

    from server.common.deploy_switch import modal_app_name

    app_name = modal_app_name()
    if STAGES[stage].where == "gpu":  # type: ignore[index]
        pipeline = modal.Cls.from_name(app_name, "Pipeline")
        call = pipeline.with_options(gpu=gpu or "L4")().run_stage.spawn(run_id, stage, attempt)
    else:
        call = modal.Function.from_name(app_name, "run_cpu_stage").spawn(run_id, stage, attempt)
    return str(call.object_id)


def _collect_once(call_id: str) -> object:
    import modal

    try:
        return modal.FunctionCall.from_id(call_id).get(timeout=0)
    except modal.exception.TimeoutError as exc:
        # FunctionTimeoutError is a real deadline. A bare poll uses the builtin.
        if type(exc) is not modal.exception.TimeoutError:
            raise
        raise ModalPending(call_id) from exc
    except TimeoutError as exc:
        # get(timeout=0) raises the builtin when the call is still running.
        raise ModalPending(call_id) from exc


def collect_modal(call_id: str) -> object:
    """Result of a recorded call. Raises ``ModalPending`` when it is not finished.

    The wait is capped at 0.4s so a hanging worker cannot stall the hard-stop
    or stale-heartbeat check inside ``FunctionCall.get``.
    """

    inflight = _COLLECT_INFLIGHT.get(call_id)
    if inflight is not None and inflight.done():
        _COLLECT_INFLIGHT.pop(call_id, None)
        return inflight.result()
    if inflight is None:
        inflight = _COLLECT_POOL.submit(_collect_once, call_id)
        _COLLECT_INFLIGHT[call_id] = inflight
    try:
        return inflight.result(timeout=0.4)
    except FutureTimeout as exc:
        raise ModalPending(call_id) from exc


def current_call_id() -> str | None:
    """Modal's id for this container, or None outside a call."""

    import modal

    try:
        call_id = modal.current_function_call_id()
    except Exception:
        return None
    if not call_id:
        return None
    return str(call_id)


def sync_volume(action: str) -> None:
    """Reload or commit the shared volume from inside a Modal container."""

    if not os.environ.get("MODAL_TASK_ID"):
        return
    from server.orchestrator.modal_functions import volume

    if volume is None:
        return
    try:
        getattr(volume, action)()
    except Exception:
        return


def cancel_modal(call_id: str) -> None:
    """Cancel a recorded call. A missing or already-finished call is ignored."""

    import modal

    try:
        modal.FunctionCall.from_id(call_id).cancel()
    except Exception:
        return
