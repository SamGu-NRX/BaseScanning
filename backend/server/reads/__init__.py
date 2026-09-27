"""reads stage stub. WS-08 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "reads"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-08 implements server.reads.run")
