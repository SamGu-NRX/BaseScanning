"""Sanitized wire observations for public receipts.

A public receipt may carry: HTTP status; the NAMES of the response body's
top-level fields; the body's byte count and sha256 digest; elapsed time; and
a small allowlisted subset of response values. It may never carry private
source, schemas, rules, raw logs, signed URLs, request headers, or response
values outside the allowlist.

The allowlist holds only what the published client contract reads and that
carries no private material: status words, contract codes, ids the backend
under test minted during this local run, and synthetic file paths. Both the
KEY and the VALUE are validated: a server-controlled string under an
allowed key that does not match that key's published shape is replaced
with ``<unsafe>``, so no raw server text reaches an artifact. Nested
objects (upload targets, per-event data) are reduced to names-only or
per-key validated; the signed upload URL and its headers are never
recorded anywhere.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re

from .sanitize import findings_in_text, redact_text

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

# Published vocabulary: lowercase contract words (statuses, error codes,
# event types, stages, outcome kinds, decisions).
_VOCAB_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")

# Backend-minted ids this run already owns (capture ids, run ids).
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# schema_version values ("0.4").
_VERSION_RE = re.compile(r"^[A-Za-z0-9.]{1,16}$")

# ISO-8601 instants the contract reports.
_TS_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?$"
)

# Synthetic file paths as the contract reports them: relative, alphanumeric
# segments, no dot-segments, no backslashes, no URI schemes.
_PATH_ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

# No published vocabulary string is longer than this.
_MAX_VALUE_CHARS = 80

# Cursor-like integer keys (bounded published values).
_INT_KEYS = frozenset({"etaS", "next", "seq"})

# Replacement for any value that fails its key's shape validation.
_UNSAFE = "<unsafe>"


def _validate_scalar(key: str, value: object) -> object:
    """Value-type gate: an allowed key carries only its published shape."""
    if isinstance(value, bool):
        return _UNSAFE
    if isinstance(value, int):
        if key in _INT_KEYS and -(2**31) <= value <= 2**31:
            return value
        return _UNSAFE
    if not isinstance(value, str) or len(value) > _MAX_VALUE_CHARS:
        return _UNSAFE
    if key in {"captureId", "runId"}:
        return value if _ID_RE.match(value) else _UNSAFE
    if key == "schema_version":
        return value if _VERSION_RE.match(value) else _UNSAFE
    if key == "at":
        return value if _TS_RE.match(value) else _UNSAFE
    if key in {"status", "code", "kind", "stage", "type", "decision", "next"}:
        # "next" is the events cursor (int, above) or a retry directive
        # word ("retry_finalize") — both published vocabulary shapes.
        return value if _VOCAB_RE.match(value) else _UNSAFE
    return _UNSAFE


def _validate_path_item(item: object) -> str:
    """Validate one entry of a contract path list (commit/missing)."""
    if not isinstance(item, str) or len(item) > 128 or ".." in item:
        return _UNSAFE
    if not _PATH_ITEM_RE.match(item):
        return _UNSAFE
    # Belt and suspenders: a path that looks like source, a URL, or any
    # other forbidden span never reaches a receipt either.
    if findings_in_text(item):
        return _UNSAFE
    return item


def _validate_event(event: object) -> object:
    """Reduce one event to validated identity/timing scalars only."""
    if not isinstance(event, dict):
        return "<non-dict>"
    safe: dict = {}
    for key, value in event.items():
        if key in _SAFE_SCALARS and not isinstance(value, (list, dict)):
            safe[key] = _validate_scalar(key, value)
    return safe


def sanitize_body(parsed: object) -> dict:
    """Reduce a parsed JSON body to its publicly recordable view."""
    if not isinstance(parsed, dict):
        return {}
    safe: dict = {}
    for key, value in parsed.items():
        if key in _FORBIDDEN_FIELDS:
            continue
        # A body KEY is server-controlled text like any other: a key that is
        # not a clean identifier shape (a signed-URL fragment, a smuggled
        # token) is not echoed. Two unsafe keys collapse — shape drift stays
        # observable, content does not.
        safe[_safe_key(key)] = _safe_value(key, value)
    return safe


def _safe_value(key: str, value: object) -> object:
    if key in _SAFE_SCALARS and not isinstance(value, (list, dict)):
        return _validate_scalar(key, value)
    if key in _SAFE_PATH_LISTS and isinstance(value, list):
        return [_validate_path_item(item) for item in value]
    if key == "events" and isinstance(value, list):
        return [_validate_event(event) for event in value]
    # Keep the field's NAME so schema drift is observable, drop its value.
    return "<name-only>"


_KEY_SHAPE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def _valid_key_text(name: object) -> bool:
    """A body/field name is recordable only as a clean bounded identifier."""
    return (
        isinstance(name, str)
        and len(name) <= 64
        and bool(_KEY_SHAPE_RE.fullmatch(name))
        and not findings_in_text(name)
    )


def _safe_key(key: object) -> str:
    """A validated body key, or a placeholder that carries no content."""
    return key if _valid_key_text(key) else "<name-only>"


def _validate_field_name(name: object) -> str:
    """Field names come from the parsed body — server-controlled text."""
    return name if _valid_key_text(name) else _UNSAFE


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
            "field_names": [_validate_field_name(n) for n in self.field_names],
            "body_sha256": self.body_sha256,
            "body_bytes": self.body_bytes,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.safe_values:
            out["safe_values"] = self.safe_values
        if self.detail:
            # Free text is caller- and server-influenced; it goes through the
            # same forbidden-span redaction as any artifact, then a length cap.
            out["detail"] = redact_text(self.detail)[:300]
        return out
