"""validate stage stub. WS-02 fills this package."""

from server.contracts.jobs import StageContext, StageResult

STAGE = "validate"


def run(ctx: StageContext) -> StageResult:
    raise NotImplementedError("WS-02 implements server.packet.run")
