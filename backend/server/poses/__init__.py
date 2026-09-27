"""poses stage stub. WS-04 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "poses"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-04 implements server.poses.run")
