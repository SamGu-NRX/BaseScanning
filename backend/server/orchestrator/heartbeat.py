"""Daemon heartbeat. A ``False`` return from the store means this call was superseded."""

from __future__ import annotations

import threading

from server.contracts.jobs import StageName
from server.orchestrator.clock import Clock


class StageSuperseded(RuntimeError):
    """This worker's call id was replaced. The attempt must abort."""


class Heartbeat:
    def __init__(
        self,
        state: object,
        run_id: str,
        stage: StageName,
        call_id: str,
        period_s: float,
        clock: Clock,
    ) -> None:
        self.state = state
        self.run_id = run_id
        self.stage = stage
        self.call_id = call_id
        self.period_s = period_s
        self.clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"hb-{stage}", daemon=True)

    def __enter__(self) -> Heartbeat:
        self.pulse("start")
        if self.period_s > 0:
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def pulse(self, substep: str) -> None:
        ok = self.state.heartbeat(self.run_id, self.stage, self.call_id, substep, self.clock.iso())  # type: ignore[attr-defined]
        if not ok:
            raise StageSuperseded("superseded")

    def _loop(self) -> None:
        while not self._stop.wait(self.period_s):
            try:
                self.pulse("beat")
            except StageSuperseded:
                return
