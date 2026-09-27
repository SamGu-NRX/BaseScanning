"""scene stage stub. WS-06 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "scene"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-06 implements server.scene.run")
