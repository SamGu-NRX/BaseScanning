"""dense stage stub. WS-06 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "dense"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-06 implements server.dense.run")
