"""scale stage stub. WS-05 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "scale"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-05 implements server.scale.run")
