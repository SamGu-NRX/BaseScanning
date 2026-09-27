"""Meter and panel reads at file commit. Skipped when photo egress is off."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.api import HintData
from server.contracts.jobs import CommittedFile
from server.orchestrator.events import emit_hint
from server.orchestrator.privacy import photo_egress_allowed
from server.orchestrator.services import Services
from server.orchestrator.stateio import put_json
from server.reads.cache import still_key
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
        still_id = _bare_still_id(item)
        key = f"early:{capture_id}:reads:{item.path}"
        body = {
            "meter_number": None,
            "cost_usd": 0.0,
            "still_id": still_id,
            "purpose": purpose,
            "started_at": started_at,
        }
        put_json(services.state, key, body)
        _put_read(services, capture_id, still_id, body)
        try:
            read, hints = _read(services, capture_id, item, still_id, purpose)
        except Exception as exc:
            body["error"] = f"{type(exc).__name__}: {exc}"
            put_json(services.state, key, body)
            _put_read(services, capture_id, still_id, body)
            used += 1
            continue
        for hint in hints:
            emit_hint(services.state, capture_id, hint, clock=services.clock)
        body.update(read)
        body["started_at"] = started_at
        body["purpose"] = purpose
        body["hints"] = [hint.model_dump(mode="json") for hint in hints]
        put_json(services.state, key, body)
        _put_read(services, capture_id, still_id, body)
        if purpose == "meter_close":
            put_json(services.state, f"early:{capture_id}:meter_identity", {"meter_number": body.get("meter_number")})
        used += 1


def _bare_still_id(item: CommittedFile) -> str:
    """WS-00 §3.9 names the blob by the still id, not the committed path."""

    meta = item.meta or {}
    raw = meta.get("id")
    if isinstance(raw, str) and _segment(raw):
        return raw
    stem = Path(item.path).stem
    if _segment(stem):
        return stem
    return Path(item.path).name


def _segment(value: str) -> bool:
    return bool(value) and "/" not in value and value not in {".", ".."}


def _read(
    services: Services, capture_id: str, item: CommittedFile, still_id: str, purpose: str
) -> tuple[dict, list[HintData]]:
    still = StillRef(
        capture_id=capture_id,
        still_id=still_id,
        path=item.key or item.path,
        sha256=item.sha256,
        purpose=purpose,
    )
    result, hints = read_still(still, services.storage, ReadsConfig.from_env(services.settings))
    meter = result.meter
    return {
        "meter_number": None if meter is None else meter.value,
        "cost_usd": result.cost_usd,
        "still_id": still.still_id,
    }, hints


def _put_read(services: Services, capture_id: str, still_id: str, body: dict) -> None:
    services.storage.put_bytes(  # type: ignore[attr-defined]
        still_key(capture_id, still_id),
        json.dumps(body).encode(),
        "application/json",
    )
