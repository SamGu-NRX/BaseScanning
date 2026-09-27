"""objects stage stub. WS-07 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "objects"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-07 implements server.objects.run")


def run_3d(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-07 implements server.objects.run_3d")
