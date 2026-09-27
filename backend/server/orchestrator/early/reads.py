"""Meter and panel reads at file commit. Skipped when photo egress is off."""

from __future__ import annotations

from server.contracts.jobs import CommittedFile
from server.orchestrator.privacy import photo_egress_allowed
from server.orchestrator.services import Services
from server.orchestrator.stateio import put_json
from server.reads.config import ReadsConfig
from server.reads.types import StillRef
from server.reads.worker import read_still

_PURPOSES = {"meter_close", "meter_oblique", "panel_close", "panel_open", "main_disconnect"}
_MAX_PER_CAPTURE = 6


def on_commit(services: Services, capture_id: str, files: list[CommittedFile]) -> None:
    if not photo_egress_allowed(
        environment=services.settings.SMP_ENVIRONMENT,
        vision=services.settings.VISION_EXTERNAL_CALLS,
    ):
        return
    used = len(services.state.list_blobs(f"early:{capture_id}:reads:"))  # type: ignore[attr-defined]
    for item in files:
        purpose = str((item.meta or {}).get("purpose") or "")
        if purpose not in _PURPOSES or used >= _MAX_PER_CAPTURE:
            continue
        started_at = services.clock.iso()
        key = f"early:{capture_id}:reads:{item.path}"
        body = {
            "meter_number": None,
            "cost_usd": 0.0,
            "still_id": item.path,
            "purpose": purpose,
            "started_at": started_at,
        }
        put_json(services.state, key, body)
        try:
            body.update(_read(services, capture_id, item, purpose))
        except Exception as exc:
            body["error"] = f"{type(exc).__name__}: {exc}"
            put_json(services.state, key, body)
            used += 1
            continue
        body["started_at"] = started_at
        body["purpose"] = purpose
        put_json(services.state, key, body)
        if purpose == "meter_close":
            put_json(services.state, f"early:{capture_id}:meter_identity", {"meter_number": body.get("meter_number")})
        used += 1


def _read(services: Services, capture_id: str, item: CommittedFile, purpose: str) -> dict:
    still = StillRef(
        capture_id=capture_id,
        still_id=item.path,
        path=item.key or item.path,
        sha256=item.sha256,
        purpose=purpose,
    )
    result, _hints = read_still(still, services.storage, ReadsConfig.from_env(services.settings))
    meter = result.meter
    return {
        "meter_number": None if meter is None else meter.value,
        "cost_usd": result.cost_usd,
        "still_id": still.still_id,
    }
