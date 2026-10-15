"""A terminal result document when the result stage never runs."""

from __future__ import annotations

import json

from server.contracts.jobs import RunRecord
from server.contracts.result import Outcome, ResultResponse, SiteSummary
from server.orchestrator.services import Services

_PROFILES = {"A", "B", "C", "S"}


def publish_manual_result(services: Services, run: RunRecord, reason: str) -> str:
    """Write ``result/result.json`` with ``manual_review`` and the failure reason."""

    profile = run.profile_default if run.profile_default in _PROFILES else "C"
    text = reason or "manual_review"
    response = ResultResponse(
        runId=run.run_id,
        status="manual_review",
        viewsNeeded=[],
        memberActions=[],
        outcome=Outcome(
            kind="manual_review",
            profile=profile,  # type: ignore[arg-type]
            message=text,
            viewsNeeded=[],
            reasons=[],
        ),
        site=SiteSummary(decision="manual_review", reasons=[text], profile=profile),  # type: ignore[arg-type]
    )
    key = f"captures/{run.capture_id}/runs/{run.run_id}/result/result.json"
    services.storage.put_bytes(key, json.dumps(response.model_dump(mode="json")).encode(), "application/json")  # type: ignore[attr-defined]
    return key
