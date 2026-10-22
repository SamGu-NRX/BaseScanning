"""Append-only ownership generations for accepted corrections.

Only a failed owner permits another generation. An owner that fails immediately
after insertion therefore cannot strand the answer or erase a successor claim.
"""

from __future__ import annotations

from typing import Any

from server.contracts.jobs import RunRecord


def latest_claim(state: Any, capture_id: str, identifier: str) -> dict | None:
    prefix = f"claim:{capture_id}:{identifier}"
    generation = 1
    latest = None
    while (row := state.get_kv(f"{prefix}:{generation}")) is not None:
        latest = row
        generation += 1
    return latest


def claim_answer(state: Any, run: RunRecord, identifier: str) -> dict:
    """One contender wins each generation, even if both saw no current owner."""
    prefix = f"claim:{run.capture_id}:{identifier}"
    generation = 1
    while True:
        key = f"{prefix}:{generation}"
        owner = state.get_kv(key)
        if owner is None:
            candidate = {"runId": run.run_id, "generation": generation}
            if state.put_if_absent(key, candidate):
                return candidate
            continue
        previous = state.get_run(owner["runId"])
        if previous is None or previous.status != "failed":
            return latest_claim(state, run.capture_id, identifier) or owner
        generation += 1


def claim_is_pending(state: Any, capture_id: str, identifier: str) -> bool:
    owner = latest_claim(state, capture_id, identifier)
    if owner is None:
        return True
    run = state.get_run(owner["runId"])
    return run is None or run.status == "failed"
