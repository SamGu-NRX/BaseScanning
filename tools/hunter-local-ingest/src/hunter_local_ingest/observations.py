"""Sanitized wire observations for public receipts.

A public receipt may carry: HTTP status; the NAMES of the response body's
top-level fields; the body's byte count and sha256 digest; elapsed time; and
a small allowlisted subset of response values. It may never carry private
source, schemas, rules, raw logs, signed URLs, request headers, or response
values outside the allowlist.

The allowlist holds only what the published client contract reads and that
carries no private material: status words, contract codes, ids the backend
under test minted during this local run, and synthetic file paths. Nested
objects (upload targets, per-event data) are reduced to names-only; the
signed upload URL and its headers are never recorded anywhere.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

# Scalar response fields whose published meaning is client-facing status or
# identity minted by the backend under test during this local run.
_SAFE_SCALARS = frozenset(
    {
        "status",
        "captureId",
        "runId",
        "etaS",
        "next",
        "seq",
        "type",
        "at",
        "code",
        "kind",
        "stage",
        "schema_version",
        "decision",
    }
)

# Lists of synthetic file paths the contract reports on (commit/missing).
_SAFE_PATH_LISTS = frozenset({"committed", "notFound", "mismatch", "missing"})

# Body fields that must never appear in any receipt, in any form.
_FORBIDDEN_FIELDS = frozenset({"url", "headers", "Authorization", "authorization"})


def sanitize_body(parsed: object) -> dict:
    """Reduce a parsed JSON body to its publicly recordable view."""
    if not isinstance(parsed, dict):
        return {}
    safe: dict = {}
    for key, value in parsed.items():
        if key in _FORBIDDEN_FIELDS:
            continue
        if key in _SAFE_SCALARS and isinstance(value, (str, int, float)) and not isinstance(
            value, bool
        ):
            safe[key] = value
        elif key in _SAFE_PATH_LISTS and isinstance(value, list):
            safe[key] = [
                item if isinstance(item, str) else "<non-string>" for item in value
            ]
        elif key == "events" and isinstance(value, list):
            # Event summaries: identity and timing only, never homeowner text.
            safe[key] = [
                {k: v for k, v in event.items() if k in _SAFE_SCALARS}
                if isinstance(event, dict)
                else "<non-dict>"
                for event in value
            ]
        else:
            # Keep the field's NAME so schema drift is observable, drop its value.
            safe[key] = "<name-only>"
    return safe


@dataclasses.dataclass(frozen=True)
class Observation:
    """One sanitized HTTP observation. Serialize with as_json()."""

    http_status: int | None = None
    field_names: tuple[str, ...] = ()
    safe_values: dict | None = None
    body_sha256: str | None = None
    body_bytes: int = 0
    elapsed_ms: int | None = None
    detail: str | None = None

    @classmethod
    def from_http(
        cls,
        status: int | None,
        body: bytes | None,
        elapsed_ms: int | None,
        detail: str | None = None,
    ) -> Observation:
        if body is None:
            return cls(http_status=status, elapsed_ms=elapsed_ms, detail=detail)
        field_names: tuple[str, ...] = ()
        safe: dict = {}
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                field_names = tuple(sorted(parsed.keys()))
                safe = sanitize_body(parsed)
        except (UnicodeDecodeError, json.JSONDecodeError):
            field_names = ()
        return cls(
            http_status=status,
            field_names=field_names,
            safe_values=safe or None,
            body_sha256=hashlib.sha256(body).hexdigest(),
            body_bytes=len(body),
            elapsed_ms=elapsed_ms,
            detail=detail,
        )

    def as_json(self) -> dict:
        out: dict = {
            "http_status": self.http_status,
            "field_names": list(self.field_names),
            "body_sha256": self.body_sha256,
            "body_bytes": self.body_bytes,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.safe_values:
            out["safe_values"] = self.safe_values
        if self.detail:
            out["detail"] = self.detail
        return out
