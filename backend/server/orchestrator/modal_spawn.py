"""Spawn a stage on Modal when this process is configured to, otherwise stay inline.

Imported by the coordinator. Modal itself is imported only when a remote spawn
is actually requested, so unit tests and ``run-local`` never need a token.
"""

from __future__ import annotations

from server.contracts.jobs import StageContext
from server.orchestrator.graph import STAGES
from server.orchestrator.services import Services


def launch_modal(services: Services, ctx: StageContext, gpu: str | None) -> bool:
    """True when the stage was handed to Modal. False means the caller runs it inline."""

    from server.orchestrator.modal_functions import modal_available

    if services.orch.stage_stubs or not modal_available():
        return False
    _remote_spawn(ctx, gpu)
    return True


def _remote_spawn(ctx: StageContext, gpu: str | None) -> None:
    from server.orchestrator.modal_functions import Pipeline, run_cpu_stage

    if STAGES[ctx.stage].where == "gpu":
        Pipeline.with_options(gpu=gpu or "L4")().run_stage.spawn(ctx.run_id, ctx.stage, ctx.attempt)
        return
    run_cpu_stage.spawn(ctx.run_id, ctx.stage, ctx.attempt)
