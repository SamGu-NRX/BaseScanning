"""result stage stub. WS-10 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "result"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-10 implements server.result.run")
