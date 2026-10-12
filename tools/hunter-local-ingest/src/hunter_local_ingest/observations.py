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
from collections.abc import Iterable

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

# Published contract vocabulary, from the recon contract map of the
# HouseScanKit sources (CaptureResult.swift, CaptureAPI.swift,
# CaptureUploader.swift). A value under one of these keys is retained
# verbatim ONLY when it is a member of the key's published enumeration —
# syntactic shape is not evidence that a server-controlled string is
# public. Keys whose vocabulary the published sources do not enumerate
# (kind, stage) carry an empty set: their values reduce unless the run
# bound them as expected.
_PUBLISHED_STATUS = frozenset(
    {
        "uploading",
        "awaiting_files",
        "processing",
        "needs_views",
        "complete",
        "manual_review",
        "failed",
        "expired",
    }
)
_PUBLISHED_CODE = frozenset(
    {
        "result_not_ready",
        "capture_not_found",
        "upload_method_unsupported",
        "upload_method_missing",
        "bad_digest",
        "not_acknowledged",
        "result_unreadable",
        "result_for_another_run",
        "auth_storage_authorization",
        "auth_credential_malformed",
        "auth_credential_unavailable",
    }
)
_PUBLISHED_EVENT_TYPE = frozenset({"verdict_ready", "failed"})
_PUBLISHED_DECISION = frozenset(
    {"eligible", "needs_more_photos", "not_eligible", "manual_review"}
)
_PUBLISHED_NEXT_DIRECTIVE = frozenset({"retry_finalize"})
_PUBLISHED_VERSIONS = frozenset({"0.4"})

_KEY_VOCABULARY = {
    "status": _PUBLISHED_STATUS,
    "code": _PUBLISHED_CODE,
    "type": _PUBLISHED_EVENT_TYPE,
    "decision": _PUBLISHED_DECISION,
    "kind": frozenset(),
    "stage": frozenset(),
    "next": _PUBLISHED_NEXT_DIRECTIVE,
}

# Published response field names — the keys the published client decodes.
# A body key that is not here reduces to a bounded digest even when it is
# a clean identifier. (url/headers live in _FORBIDDEN_FIELDS and are
# dropped before names are considered.)
_KNOWN_FIELD_NAMES = frozenset(
    {
        "captureId",
        "status",
        "upload",
        "finalizeBy",
        "maxSinglePutBytes",
        "urlTtlS",
        "maxFiles",
        "maxBatch",
        "files",
        "path",
        "state",
        "method",
        "expiresAt",
        "committed",
        "notFound",
        "mismatch",
        "missing",
        "runId",
        "etaS",
        "next",
        "events",
        "seq",
        "type",
        "at",
        "data",
        "code",
        "kind",
        "stage",
        "viewsNeeded",
        "memberActions",
        "outcome",
        "criteria",
        "decision",
        "errors",
        "pointer",
        "message",
        "schema_version",
    }
)

# Run-owned public expected values: ids and paths this run minted itself
# (from the plan/fixture) and expects the backend to echo back. Binding is
# per-run state, set via bind_expected(); it never holds server text.
_EXPECTED: set[str] = set()


def bind_expected(values: Iterable[str]) -> None:
    """Bind this run's own expected ids/paths so echoes stay verbatim."""
    for value in values:
        if isinstance(value, str) and value and len(value) <= 128:
            _EXPECTED.add(value)


def reset_expected() -> None:
    """Clear the run-bound expected registry (tests, new-run setup)."""
    _EXPECTED.clear()


def _bounded_digest(value: str) -> str:
    """A bounded, content-derived stand-in for a non-public string."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

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
    """Value gate: an allowed key carries only published or run-bound text.

    Membership in the key's published enumeration — or equality with a
    value this run bound as expected — is what makes a server-controlled
    string public. Anything else reduces to a bounded digest, so drift
    stays observable without retaining payload text.
    """
    if isinstance(value, bool):
        return _UNSAFE
    if isinstance(value, int):
        if key in _INT_KEYS and -(2**31) <= value <= 2**31:
            return value
        return _UNSAFE
    if not isinstance(value, str) or len(value) > _MAX_VALUE_CHARS:
        return _UNSAFE
    if value in _EXPECTED:
        return value
    if key in {"captureId", "runId"}:
        return _bounded_digest(value)
    if key == "schema_version":
        return value if value in _PUBLISHED_VERSIONS else _bounded_digest(value)
    if key == "at":
        return value if _TS_RE.match(value) else _bounded_digest(value)
    vocabulary = _KEY_VOCABULARY.get(key)
    if vocabulary is None:
        return _UNSAFE
    return value if value in vocabulary else _bounded_digest(value)


def _validate_path_item(item: object) -> str:
    """Validate one entry of a contract path list (commit/missing).

    A path is retained verbatim only when the run bound it as expected
    (the plan's own synthetic paths); anything else reduces to a bounded
    digest so the commit report stays observable without echoing
    server-controlled path text.
    """
    if not isinstance(item, str) or len(item) > 128 or ".." in item:
        return _UNSAFE
    if not _PATH_ITEM_RE.match(item):
        return _UNSAFE
    return item if item in _EXPECTED else _bounded_digest(item)


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
        # A body KEY is server-controlled text like any other: published
        # names stay raw, everything else reduces (bounded digest for clean
        # identifiers, placeholder otherwise) — drift stays observable,
        # key text does not.
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


def _safe_field_name(name: object) -> str:
    """A recordable field name.

    Published response names stay raw; an unexpected (but clean) name
    reduces to a bounded digest so schema drift stays observable without
    echoing server-controlled key text. Names that are not clean
    identifiers collapse to the name-only placeholder.
    """
    if isinstance(name, str) and _valid_key_text(name):
        if name in _KNOWN_FIELD_NAMES:
            return name
        return f"field:{_bounded_digest(name)}"
    return "<name-only>"


def _safe_key(key: object) -> str:
    """A recordable body key, reduced by the field-name policy."""
    return _safe_field_name(key)


def _validate_field_name(name: object) -> str:
    """Field names come from the parsed body — server-controlled text."""
    return _safe_field_name(name)


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
