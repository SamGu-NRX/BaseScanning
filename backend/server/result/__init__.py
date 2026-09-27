"""Result stage. ``run(ctx)`` composes the device payload for one capture run."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult

STAGE = "result"


def run(ctx: StageContext) -> StageResult:
    # Imported on call so `python -m server.result.compose` is not already loaded.
    from server.result.compose import run as compose_run

    return compose_run(ctx)


__all__ = ["STAGE", "run"]
