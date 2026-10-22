"""Deliver final events only for immutable published results."""

from __future__ import annotations

import json
from typing import Any

from server.orchestrator.corrections import latest_publication


def emit_latest_publication(state: Any, storage: Any, run_id: str, settings: Any) -> None:
    publication = latest_publication(state, run_id)
    if publication is None:
        return
    capture_id = publication["captureId"]
    after = 0
    while page := state.events_after(capture_id, after):
        for event in page:
            version = event.data.get("_resultVersion")
            if event.run_id == run_id and version is not None:
                prior = state.get_kv(f"result_version:{run_id}:{version}")
                # Preserve the existing retry contract: unchanged correction
                # coverage does not emit another final. A new answer always does.
                if (
                    prior is not None
                    and prior["correctionIds"] == publication["correctionIds"]
                    and public_final_event(state, event) is not None
                ):
                    return
        after = page[-1].seq
    # No lease can fence append_event. A duplicate emitter is harmless, and the
    # event reader filters superseded versions without changing the device data.
    # A crash before this append is recovered from the publication by the sweep.
    for event_type, data in publication["events"]:
        state.append_event(
            capture_id, event_type, {**data, "_resultVersion": publication["version"]}, run_id=run_id, stage="result"
        )
    state.set_status(capture_id, publication["status"])
    if publication.get("deliverWebhook"):
        from server.contracts.result import ResultResponse
        from server.result.compose import ComposeResult, _deliver
        from server.result.config import load_config

        capture = state.get_capture(capture_id)
        if capture is not None:
            artifacts = publication["artifacts"]
            response = ResultResponse.model_validate_json(storage.get_bytes(artifacts["result/result.json"]))
            internal = json.loads(storage.get_bytes(artifacts["result/result.internal.json"]))
            composed = ComposeResult(response, internal, publication["events"], capture_id, run_id)
            _deliver(composed, capture, load_config(settings))


def public_final_event(state: Any, event: Any) -> tuple[str, dict] | None:
    """Strip internal coverage and suppress stale or duplicate final events."""
    version = event.data.get("_resultVersion")
    if version is None:
        return event.type, event.data
    latest = latest_publication(state, event.run_id)
    if latest is None:
        return None
    # Keep finals that were already in history before a recomposition. Only
    # suppress an old emitter that appended beyond a successor's watermark.
    for number in range(version + 1, latest["version"] + 1):
        successor = state.get_kv(f"result_version:{event.run_id}:{number}")
        if successor is not None and event.seq > successor["afterEventSeq"]:
            return None
    after = 0
    while page := state.events_after(event.capture_id, after):
        if any(
            prior.seq < event.seq and prior.run_id == event.run_id and prior.data.get("_resultVersion") == version
            for prior in page
        ):
            return None
        if page[-1].seq >= event.seq:
            break
        after = page[-1].seq
    data = {key: value for key, value in event.data.items() if key != "_resultVersion"}
    return event.type, data
