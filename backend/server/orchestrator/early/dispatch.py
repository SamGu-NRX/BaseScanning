"""Fan committed files out to the three early jobs. A path is claimed once."""

from __future__ import annotations

from server.contracts.jobs import CommittedFile
from server.orchestrator.early.capture_check import on_commit as capture_check_on_commit
from server.orchestrator.early.qa import on_commit as qa_on_commit
from server.orchestrator.early.reads import on_commit as reads_on_commit
from server.orchestrator.services import Services


def dispatch(services: Services, capture_id: str, files: list[CommittedFile]) -> None:
    fresh: list[CommittedFile] = []
    for item in files:
        claimed = services.state.put_if_absent(  # type: ignore[attr-defined]
            f"early-worker:{capture_id}:{item.path}",
            {"at": services.clock.iso(), "key": item.key},
        )
        if claimed:
            fresh.append(item)
    if not fresh:
        return
    reads_on_commit(services, capture_id, fresh)
    qa_on_commit(services, capture_id, fresh)
    capture_check_on_commit(services, capture_id, fresh)
