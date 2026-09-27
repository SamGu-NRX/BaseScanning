"""criteria stage stub. WS-09 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "criteria"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-09 implements server.criteria.run")
