"""Accepted confirmations the reads record must carry.

The confirmations route persists every accepted answer as a per-field slot
(``confirm:{capture_id}:{field_id}``, listed in ``confirmix:{capture_id}``) as
soon as it passes the conflict and authorization checks. A confirmation that
arrives before ``reads/reads.json`` exists is applied here, when the reads stage
creates its record, so an early answer reaches the result without the client
retrying. The route's fingerprint check keeps a replayed confirmation from being
applied twice as a new answer; re-applying one accepted slot is idempotent.
"""

from __future__ import annotations

from server.common.logging import log_event
from server.reads.policy import confirm
from server.reads.types import Reads

_BY = frozenset({"device", "reviewer"})
_FIELDS = frozenset({"meter_number", "panel_brand", "main_breaker_amps"})


def pending_slots(state: object, capture_id: str) -> list[dict]:
    """The accepted per-field confirmation slots persisted for this capture."""

    if state is None:
        return []
    listing = state.get_kv(f"confirmix:{capture_id}")  # type: ignore[attr-defined]
    rows: list[dict] = []
    for field_id in (listing or {}).get("fields") or []:
        row = state.get_kv(f"confirm:{capture_id}:{field_id}")  # type: ignore[attr-defined]
        if isinstance(row, dict) and row.get("kind") in {"confirmed", "corrected", "none"}:
            rows.append(row)
    return rows


def apply_pending(state: object, capture_id: str, reads: Reads) -> Reads:
    """Apply each accepted confirmation slot once, preserving answer and authorization."""

    for row in pending_slots(state, capture_id):
        by = row.get("by") if row.get("by") in _BY else "device"
        field_id = row.get("fieldId") if row.get("fieldId") in _FIELDS else None
        if field_id is None:
            continue
        kind = row.get("kind")
        try:
            reads = confirm(
                reads,
                still_id=str(row.get("imageId") or ""),
                choice=row.get("value") if kind == "confirmed" else None,
                typed=row.get("value") if kind == "corrected" else None,
                by=by,  # type: ignore[arg-type]
                field_id=field_id,  # type: ignore[arg-type]
            )
        except Exception as exc:
            # A stale slot that no longer fits the record must not fail the stage.
            log_event(
                "reads.pending_confirmation.skipped",
                capture_id=capture_id,
                field_id=str(row.get("fieldId")),
                error=type(exc).__name__,
            )
    return reads


__all__ = ["apply_pending", "pending_slots"]
