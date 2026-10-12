"""The published capture-API contract: vocabularies and strict response decoders.

Source of truth, read-only: ios/HouseScanKit/Sources/HouseScanKit on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807 —
Upload/CaptureAPI.swift (request and response types, terminal statuses),
Upload/CaptureUploader.swift (what the client reads and how it reacts),
CaptureResult/CaptureResult.swift (the device result), and
Processing/FixtureCaptureHTTP.swift (the public in-process API the package
tests run against).

Each decoder returns two things:

- ``data``: typed values for the scenario's PASS/FAIL decision. Never
  serialized.
- ``public``: what a receipt may carry. Published enum words appear as
  themselves; any other string becomes ``unrecognized:<digest>``. Server
  identifiers (captureId, runId) become ``id:<digest>``. A path appears only
  when the run itself declared it; any other becomes
  ``unexpected-path:<digest>``. Unknown keys become a count and one digest.
  Homeowner text (messages, prompts, reasons) and URLs are never kept.

A digest is the first 12 hex characters of SHA-256. It lets a reader see
that two lines carry the same value without the value itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math

from .netpolicy import upload_url_problem

FORMAT_VERSION = "0.4"

# CaptureResult.Status: every capture status the app knows.
CAPTURE_STATUSES = frozenset(
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
# CaptureAPI.terminalStatuses.
TERMINAL_STATUSES = frozenset({"complete", "manual_review", "failed", "expired", "needs_views"})
# CaptureAPI.statusesWithoutOutcome.
STATUSES_WITHOUT_OUTCOME = frozenset({"failed", "expired"})
# CaptureResult.OutcomeKind.
OUTCOME_KINDS = frozenset({"eligible", "needs_more_photos", "not_eligible", "manual_review"})
# Packet04.Role and Packet04.Tier.
ROLES = frozenset({"keyframe", "depth", "confidence", "features", "still", "stream", "other"})
TIERS = frozenset({"arkit", "arkit_lidar"})
# RegisteredFile.state: the client branches only on "committed"; the public
# fixture answers "pending" otherwise.
REGISTER_STATES = frozenset({"pending", "committed"})
# Event types the client reads (verdict_ready, failed).
EVENT_TYPES = frozenset({"verdict_ready", "failed"})
# EventData.next directive the client follows.
EVENT_DIRECTIVES = frozenset({"retry_finalize"})
# Error codes the client branches on, plus ScopedCaptureHTTPError codes.
ERROR_CODES = frozenset(
    {
        "capture_not_found",
        "result_not_ready",
        "auth_destination_refused",
        "auth_credential_unavailable",
        "auth_credential_malformed",
        "auth_storage_authorization",
    }
)
# Codes this package's own fakes and proxy emit. Prefixed so they can never
# be mistaken for a contract code.
HARNESS_CODES = frozenset(
    {
        "harness_fault",
        "harness_origin_unreachable",
        "harness_no_route",
        "harness_schema",
        "harness_packet_id_mismatch",
        "harness_forced_500",
    }
)
HEALTH_STATUSES = frozenset({"ok"})

# --- violation vocabulary ---------------------------------------------------
#
# A violation names a check and a published field, never a value. Every
# string a decoder can emit is enumerated here so the export validator can
# accept exactly this set.

_FIELDS = (
    "body",
    "status",
    "captureId",
    "upload",
    "finalizeBy",
    "files",
    "path",
    "state",
    "headers",
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
    "viewsNeeded",
    "memberActions",
    "outcome",
    "kind",
    "criteria",
    "reasons",
    "errors",
    "code",
    "profile",
    "message",
    "arkitEpoch",
    "recommendedPlacement",
    "boxArkitWorld",
    "prompt",
    "referenceCrop",
    "previewUrl",
    "reviewUrl",
)
_KINDS = ("missing", "type", "enum", "range", "duplicate", "unexpected-path", "not-acknowledged")
VIOLATIONS = frozenset(f"{kind}:{field}" for kind in _KINDS for field in _FIELDS) | frozenset(
    {
        "shape:not-json",
        "shape:not-object",
        "shape:empty",
        "upload_url_invalid",
        "upload_url_insecure",
        "upload_method",
        "order:next",
        "order:seq",
        "verdict-for-another-run",
    }
)


def violation(kind: str, field: str) -> str:
    text = f"{kind}:{field}"
    if text not in VIOLATIONS:
        raise ValueError(f"unregistered violation {text}")
    return text


# --- public-value helpers -----------------------------------------------------


def digest12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def pub_id(value: object) -> str:
    """A server identifier as a digest; a non-string becomes ``unrecognized``."""
    if not isinstance(value, str):
        return f"unrecognized:{digest12(type(value).__name__)}"
    return f"id:{digest12(value)}"


def pub_enum(value: object, vocabulary: frozenset[str]) -> str:
    if isinstance(value, str) and value in vocabulary:
        return value
    return f"unrecognized:{digest12(json.dumps(value, sort_keys=True, default=str))}"


def pub_path(value: object, declared: frozenset[str]) -> str:
    if isinstance(value, str) and value in declared:
        return value
    return f"unexpected-path:{digest12(json.dumps(value, default=str))}"


def pub_unknown_keys(obj: dict, known: frozenset[str]) -> dict | None:
    extra = sorted(str(k) for k in obj if k not in known)
    if not extra:
        return None
    return {"count": len(extra), "digest": f"unexpected-keys:{digest12(chr(0).join(extra))}"}


_MAX_INT = 2**53


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -_MAX_INT < value < _MAX_INT


@dataclasses.dataclass(frozen=True)
class Decoded:
    """A decoded answer. ``ok`` means it has the published shape."""

    violations: tuple[str, ...]
    public: dict
    data: dict = dataclasses.field(default_factory=dict, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return not self.violations


class _Collector:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, kind: str, field: str) -> None:
        self.raw(violation(kind, field))

    def raw(self, text: str) -> None:
        if text not in VIOLATIONS:
            raise ValueError(f"unregistered violation {text}")
        if text not in self.items:
            self.items.append(text)


# Sentinels for bodies that are not a JSON value.
NOT_JSON = object()
EMPTY = object()


def parse_body(body: bytes | None) -> object:
    """JSON-decode a response body, or return a sentinel for empty / non-JSON."""
    if not body:
        return EMPTY
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return NOT_JSON


def body_shape(parsed: object) -> str:
    if parsed is EMPTY:
        return "empty"
    if parsed is NOT_JSON:
        return "not-json"
    return "object" if isinstance(parsed, dict) else "json-non-object"


def _object(parsed: object, out: _Collector) -> dict | None:
    if parsed is NOT_JSON:
        out.raw("shape:not-json")
        return None
    if parsed is EMPTY:
        out.raw("shape:empty")
        return None
    if not isinstance(parsed, dict):
        out.raw("shape:not-object")
        return None
    return parsed


def _str(obj: dict, key: str, out: _Collector, *, required: bool = True) -> str | None:
    if key not in obj or obj[key] is None:
        if required:
            out.add("missing", key)
        return None
    value = obj[key]
    if not isinstance(value, str):
        out.add("type", key)
        return None
    return value


def _str_list(obj: dict, key: str, out: _Collector) -> list[str] | None:
    if key not in obj:
        out.add("missing", key)
        return None
    value = obj[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        out.add("type", key)
        return None
    return value


# --- decoders, one per published response type --------------------------------


def decode_errors(parsed: object) -> Decoded:
    """CaptureAPI.ErrorBody: ``{"errors": [{"code": ...}]}``. Messages never kept."""
    out = _Collector()
    obj = _object(parsed, out)
    codes: list[str] = []
    public: dict = {}
    if obj is not None:
        errors = obj.get("errors")
        if not isinstance(errors, list):
            out.add("missing" if "errors" not in obj else "type", "errors")
        else:
            for item in errors[:20]:
                code = item.get("code") if isinstance(item, dict) else None
                if not isinstance(code, str):
                    out.add("type", "code")
                    continue
                codes.append(code)
            public["codes"] = [pub_enum(c, ERROR_CODES | HARNESS_CODES) for c in codes]
    return Decoded(tuple(out.items), public, {"codes": codes})


def decode_health(parsed: object) -> Decoded:
    """GET /healthz as the public integration-viewer client reads it."""
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    status = None
    if obj is not None:
        status = _str(obj, "status", out)
        if status is not None:
            public["status"] = pub_enum(status, HEALTH_STATUSES)
            if status not in HEALTH_STATUSES:
                out.add("enum", "status")
    return Decoded(tuple(out.items), public, {"status": status})


_CREATE_KEYS = frozenset({"captureId", "status", "upload", "finalizeBy"})
_LIMIT_KEYS = frozenset({"maxSinglePutBytes", "urlTtlS", "maxFiles", "maxBatch"})


def decode_create(parsed: object) -> Decoded:
    """CaptureAPI.CreateResponse."""
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    data: dict = {"captureId": None, "status": None, "maxBatch": None}
    if obj is not None:
        capture_id = _str(obj, "captureId", out)
        if capture_id is not None and not capture_id:
            out.add("range", "captureId")
            capture_id = None
        status = _str(obj, "status", out)
        if status is not None and status not in CAPTURE_STATUSES:
            out.add("enum", "status")
        limits = obj.get("upload")
        max_batch = None
        if limits is not None:
            if not isinstance(limits, dict):
                out.add("type", "upload")
            else:
                for key in _LIMIT_KEYS & limits.keys():
                    value = limits[key]
                    if value is not None and not (is_int(value) and value >= 0):
                        out.add("type", "upload")
                if is_int(limits.get("maxBatch")) and limits["maxBatch"] > 0:
                    max_batch = limits["maxBatch"]
                extra = pub_unknown_keys(limits, _LIMIT_KEYS)
                if extra:
                    public["upload_unexpected_keys"] = extra
        finalize_by = obj.get("finalizeBy")
        if finalize_by is not None and not isinstance(finalize_by, str):
            out.add("type", "finalizeBy")
        public["captureId"] = pub_id(obj.get("captureId"))
        if status is not None:
            public["status"] = pub_enum(status, CAPTURE_STATUSES)
        if max_batch is not None:
            public["maxBatch"] = max_batch
        extra = pub_unknown_keys(obj, _CREATE_KEYS)
        if extra:
            public["unexpected_keys"] = extra
        data = {"captureId": capture_id, "status": status, "maxBatch": max_batch}
    return Decoded(tuple(out.items), public, data)


_REGISTERED_KEYS = frozenset({"path", "state", "upload"})
_TARGET_KEYS = frozenset({"method", "url", "headers", "expiresAt"})


def decode_register(parsed: object, requested: list[str], declared: frozenset[str]) -> Decoded:
    """CaptureAPI.RegisterResponse, matched to the request by exact path.

    The client applies an answer only to a path it asked about and takes
    an upload target only from ``method == "PUT"`` with a URL that passes
    ``uploadURLProblem``. A path answered twice, a path nobody asked
    about, or a requested path left out is a violation here; answer order
    carries no meaning.
    """
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    targets: dict[str, dict] = {}
    committed: list[str] = []
    asked = set(requested)
    visible = declared & asked
    if obj is not None:
        files = obj.get("files")
        if not isinstance(files, list):
            out.add("missing" if "files" not in obj else "type", "files")
            files = []
        seen: set[str] = set()
        rows: list[dict] = []
        for entry in files:
            if not isinstance(entry, dict):
                out.add("type", "files")
                continue
            path = entry.get("path")
            row: dict = {"path": pub_path(path, visible)}
            rows.append(row)
            if not isinstance(path, str):
                out.add("type", "path")
                continue
            duplicate = path in seen
            seen.add(path)
            if path not in asked:
                out.add("unexpected-path", "files")
            elif duplicate:
                out.add("duplicate", "files")
            state = _str(entry, "state", out)
            row["state"] = pub_enum(state, REGISTER_STATES) if state is not None else None
            extra = pub_unknown_keys(entry, _REGISTERED_KEYS)
            if extra:
                row["unexpected_keys"] = extra
            if state == "committed":
                if path in asked and not duplicate:
                    committed.append(path)
                continue
            target = entry.get("upload")
            if not isinstance(target, dict):
                out.raw("upload_method")
                continue
            method = target.get("method")
            url = target.get("url")
            headers = target.get("headers")
            if headers is None:
                headers = {}
            headers_ok = isinstance(headers, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            )
            if not headers_ok:
                out.add("type", "headers")
            expires = target.get("expiresAt")
            if expires is not None and not isinstance(expires, str):
                out.add("type", "expiresAt")
            url_problem = upload_url_problem(url) if isinstance(url, str) else "upload_url_invalid"
            upload_row = {
                "method": pub_enum(method, frozenset({"PUT"})),
                "url_present": isinstance(url, str),
                "url_policy": url_problem or "ok",
                "signed_header_count": len(headers) if headers_ok else None,
                "signs_auth_header": bool(
                    headers_ok and any(k.lower() == "authorization" for k in headers)
                ),
                "signed_content_type": bool(
                    headers_ok and any(k.lower() == "content-type" for k in headers)
                ),
            }
            extra = pub_unknown_keys(target, _TARGET_KEYS)
            if extra:
                upload_row["unexpected_keys"] = extra
            row["upload"] = upload_row
            if method != "PUT":
                out.raw("upload_method")
            elif url_problem:
                out.raw(url_problem)
            elif headers_ok and path in asked and not duplicate:
                targets[path] = {"url": url, "headers": dict(headers)}
        for path in requested:
            if path not in seen:
                out.add("not-acknowledged", "files")
        public["files"] = rows[:64]
        if len(rows) > 64:
            public["files_total"] = len(rows)
        extra = pub_unknown_keys(obj, frozenset({"files"}))
        if extra:
            public["unexpected_keys"] = extra
    return Decoded(tuple(out.items), public, {"targets": targets, "committed": committed})


_COMMIT_KEYS = frozenset({"committed", "notFound", "mismatch"})


def decode_commit(parsed: object, requested: list[str], declared: frozenset[str]) -> Decoded:
    """CaptureAPI.CommitResponse. All three lists are required by the type."""
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    data: dict = {"committed": [], "notFound": [], "mismatch": []}
    asked = set(requested)
    if obj is not None:
        for key in ("committed", "notFound", "mismatch"):
            values = _str_list(obj, key, out)
            if values is None:
                continue
            if len(set(values)) != len(values):
                out.add("duplicate", key)
            if any(v not in asked for v in values):
                out.add("unexpected-path", key)
            data[key] = values
            public[key] = [pub_path(v, declared & asked) for v in values[:64]]
        extra = pub_unknown_keys(obj, _COMMIT_KEYS)
        if extra:
            public["unexpected_keys"] = extra
    return Decoded(tuple(out.items), public, data)


_FINALIZE_KEYS = frozenset({"status", "missing", "runId", "etaS"})


def decode_finalize(parsed: object, declared: frozenset[str]) -> Decoded:
    """CaptureAPI.FinalizeResponse: status, missing and runId are required."""
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    data: dict = {"status": None, "missing": None, "runId": None}
    if obj is not None:
        status = _str(obj, "status", out)
        if status is not None and status not in CAPTURE_STATUSES:
            out.add("enum", "status")
        missing = _str_list(obj, "missing", out)
        run_id = _str(obj, "runId", out)
        if run_id is not None and not run_id:
            out.add("range", "runId")
            run_id = None
        eta = obj.get("etaS")
        if eta is not None and not (is_int(eta) and eta >= 0):
            out.add("type", "etaS")
        if status is not None:
            public["status"] = pub_enum(status, CAPTURE_STATUSES)
        if missing is not None:
            public["missing"] = [pub_path(p, declared) for p in missing[:64]]
        public["runId"] = pub_id(obj.get("runId"))
        if is_int(eta) and eta >= 0:
            public["etaS"] = eta
        extra = pub_unknown_keys(obj, _FINALIZE_KEYS)
        if extra:
            public["unexpected_keys"] = extra
        data = {"status": status, "missing": missing, "runId": run_id}
    return Decoded(tuple(out.items), public, data)


_EVENTS_KEYS = frozenset({"status", "next", "events"})
_EVENT_KEYS = frozenset({"seq", "type", "at", "data"})
_EVENT_DATA_KEYS = frozenset({"code", "kind", "stage", "status", "runId", "next"})


def decode_events(parsed: object, *, after: int, run_id: str | None) -> Decoded:
    """CaptureAPI.EventsResponse, plus the ordering the client relies on.

    ``next`` never moves the cursor back, each event's ``seq`` is past
    ``after`` and at most ``next``, and a ``verdict_ready`` naming a run
    names this capture's run.
    """
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    data: dict = {"status": None, "next": None, "retry_finalize": False, "events": 0}
    if obj is not None:
        status = _str(obj, "status", out)
        if status is not None and status not in CAPTURE_STATUSES:
            out.add("enum", "status")
        nxt = obj.get("next")
        if "next" not in obj:
            out.add("missing", "next")
            nxt = None
        elif not is_int(nxt) or nxt < 0:
            out.add("type", "next")
            nxt = None
        elif nxt < after:
            out.raw("order:next")
        events = obj.get("events")
        if not isinstance(events, list):
            out.add("missing" if "events" not in obj else "type", "events")
            events = []
        rows: list[dict] = []
        for event in events:
            if not isinstance(event, dict):
                out.add("type", "events")
                continue
            seq = event.get("seq")
            etype = event.get("type")
            row: dict = {}
            if not is_int(seq):
                out.add("missing" if "seq" not in event else "type", "seq")
            else:
                row["seq"] = seq
                if seq <= after or (nxt is not None and seq > nxt):
                    out.raw("order:seq")
            if not isinstance(etype, str):
                out.add("missing" if "type" not in event else "type", "type")
            row["type"] = pub_enum(etype, EVENT_TYPES)
            if not isinstance(event.get("at"), str):
                out.add("missing" if "at" not in event else "type", "at")
            payload = event.get("data")
            if payload is not None and not isinstance(payload, dict):
                out.add("type", "data")
                payload = None
            if payload:
                for key in _EVENT_DATA_KEYS:
                    if payload.get(key) is not None and not isinstance(payload[key], str):
                        out.add("type", "data")
                pdata: dict = {}
                if "code" in payload:
                    pdata["code"] = pub_enum(payload["code"], ERROR_CODES | HARNESS_CODES)
                if "kind" in payload:
                    pdata["kind"] = pub_enum(payload["kind"], OUTCOME_KINDS)
                if "stage" in payload:
                    pdata["stage"] = pub_enum(payload["stage"], frozenset())
                if "status" in payload:
                    pdata["status"] = pub_enum(payload["status"], CAPTURE_STATUSES)
                if "runId" in payload:
                    pdata["runId"] = pub_id(payload["runId"])
                    pdata["runId_matches"] = payload["runId"] == run_id
                if "next" in payload:
                    pdata["next"] = pub_enum(payload["next"], EVENT_DIRECTIVES)
                extra = pub_unknown_keys(payload, _EVENT_DATA_KEYS)
                if extra:
                    pdata["unexpected_keys"] = extra
                row["data"] = pdata
                if etype == "verdict_ready" and payload.get("runId") is not None:
                    if payload.get("runId") != run_id:
                        out.raw("verdict-for-another-run")
                if etype == "failed" and payload.get("next") == "retry_finalize":
                    data["retry_finalize"] = True
            extra = pub_unknown_keys(event, _EVENT_KEYS)
            if extra:
                row["unexpected_keys"] = extra
            rows.append(row)
        if status is not None:
            public["status"] = pub_enum(status, CAPTURE_STATUSES)
        if nxt is not None:
            public["next"] = nxt
        public["events"] = rows[:50]
        if len(rows) > 50:
            public["events_total"] = len(rows)
        extra = pub_unknown_keys(obj, _EVENTS_KEYS)
        if extra:
            public["unexpected_keys"] = extra
        data.update({"status": status, "next": nxt, "events": len(rows)})
    return Decoded(tuple(out.items), public, data)


# CaptureResult.swift, field by field: (kind, required). A kind is "str",
# "num" (Swift Double: a finite JSON number, never a bool), "[str]", "[num]",
# a nested spec dict, or ("list", spec). JSONDecoder rejects a required field
# that is missing or null, accepts a missing or null optional, and rejects
# any present value of the wrong type. Unknown keys are ignored, as in Swift.
_PROMPT = {"title": ("str", True), "body": ("str", True)}
_REFERENCE_CROP = {"image": ("str", True), "boxPx": ("[num]", True)}
_VIEW = {
    "id": ("str", True),
    "kind": ("str", True),
    "criteria": ("[str]", True),
    "why": ("str", True),
    "promptId": ("str", True),
    "prompt": (_PROMPT, True),
    "band": ("str", False),
    "wallId": ("str", False),
    "target": ("str", False),
    "referenceCrop": (_REFERENCE_CROP, False),
}
_REASON = {
    "criterion": ("str", True),
    "text": ("str", True),
    "measuredFt": ("num", False),
    "plusMinusFt": ("num", False),
    "thresholdFt": ("num", False),
    "comparison": ("str", False),
}
# BoxArkitWorld's counts are checked only when the app asks for AR, so a
# decode accepts any list of numbers here.
_BOX = {"pose": ("[num]", True), "size": ("[num]", True)}
_PLACEMENT = {
    "wallId": ("str", True),
    "startSM": ("num", True),
    "confidence": ("num", True),
    "boxArkitWorld": (_BOX, False),
}
_OUTCOME = {
    "kind": ("str", True),
    "profile": ("str", True),
    "message": ("str", True),
    "viewsNeeded": (("list", _VIEW), True),
    "reasons": (("list", _REASON), True),
    "arkitEpoch": ("str", False),
    "recommendedPlacement": (_PLACEMENT, False),
}
_CRITERION = {
    "id": ("str", True),
    "outcome": ("str", True),
    "unsureCause": ("str", False),
    "measuredFt": ("num", False),
    "plusMinusFt": ("num", False),
    "thresholdFt": ("num", False),
    "coverage": ("str", False),
}
_RESULT = {
    "runId": ("str", False),
    "status": ("str", True),
    "viewsNeeded": (("list", _VIEW), True),
    "memberActions": ("[str]", True),
    "outcome": (_OUTCOME, False),
    "criteria": (("list", _CRITERION), False),
    "previewUrl": ("str", False),
    "reviewUrl": ("str", False),
}
_RESULT_KEYS = frozenset(_RESULT)
_OUTCOME_KEYS = frozenset(_OUTCOME)
_FIELD_SET = frozenset(_FIELDS)


def _swift_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -_MAX_INT < value < _MAX_INT
    return isinstance(value, float) and math.isfinite(value)


def _check_value(value: object, kind, label: str, out: _Collector) -> None:
    """Record ``type:<label>`` when ``value`` is not a ``kind`` (never None here)."""
    if kind == "str":
        ok = isinstance(value, str)
    elif kind == "num":
        ok = _swift_number(value)
    elif kind == "[str]":
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    elif kind == "[num]":
        ok = isinstance(value, list) and all(_swift_number(v) for v in value)
    elif isinstance(kind, tuple):
        ok = isinstance(value, list)
        if ok:
            for item in value:
                _check_object(item, kind[1], label, out)
    else:
        _check_object(value, kind, label, out)
        return
    if not ok:
        out.add("type", label)


def _check_object(value: object, spec: dict, label: str, out: _Collector) -> None:
    if not isinstance(value, dict):
        out.add("type", label)
        return
    for key, (kind, required) in spec.items():
        field = key if key in _FIELD_SET else label
        if value.get(key) is None:
            if required:
                out.add("missing", field)
            continue
        _check_value(value[key], kind, field, out)


def decode_result(parsed: object) -> Decoded:
    """CaptureResult.Response, decoded as strictly as the app's JSONDecoder.

    Every published field that is present must have its Swift type, nested
    ones included (``_RESULT``). On top of that the harness requires the
    status and outcome kind to be published words. The public view keeps
    the status, the outcome kind, counts and the run id as a digest; never
    the homeowner message, prompts, reasons, placement or signed URLs.
    """
    out = _Collector()
    obj = _object(parsed, out)
    public: dict = {}
    data: dict = {"runId": None, "status": None, "outcome_present": False, "kind": None}
    if obj is None:
        return Decoded(tuple(out.items), public, data)
    _check_object(obj, _RESULT, "body", out)
    run_id = obj.get("runId") if isinstance(obj.get("runId"), str) else None
    status = obj.get("status") if isinstance(obj.get("status"), str) else None
    if status is not None and status not in CAPTURE_STATUSES:
        out.add("enum", "status")
    outcome = obj.get("outcome")
    kind = None
    if isinstance(outcome, dict):
        kind = outcome.get("kind") if isinstance(outcome.get("kind"), str) else None
        if kind is not None and kind not in OUTCOME_KINDS:
            out.add("enum", "kind")
        reasons = outcome.get("reasons")
        public["outcome"] = {
            "kind": pub_enum(kind, OUTCOME_KINDS),
            "reasons": len(reasons) if isinstance(reasons, list) else None,
        }
        extra = pub_unknown_keys(outcome, _OUTCOME_KEYS)
        if extra:
            public["outcome"]["unexpected_keys"] = extra
    else:
        public["outcome"] = None
    if status is not None:
        public["status"] = pub_enum(status, CAPTURE_STATUSES)
    public["runId"] = pub_id(run_id) if run_id is not None else None
    for key in ("viewsNeeded", "memberActions", "criteria"):
        value = obj.get(key)
        public[key] = len(value) if isinstance(value, list) else None
    public["has_preview_url"] = isinstance(obj.get("previewUrl"), str)
    extra = pub_unknown_keys(obj, _RESULT_KEYS)
    if extra:
        public["unexpected_keys"] = extra
    data = {
        "runId": run_id,
        "status": status,
        "outcome_present": outcome is not None,
        "kind": kind,
    }
    return Decoded(tuple(out.items), public, data)


# Every top-level published response field, for the frozen-trace comparison.
PUBLISHED_FIELDS = frozenset(
    _CREATE_KEYS | {"files"} | _COMMIT_KEYS | _FINALIZE_KEYS | _EVENTS_KEYS | _RESULT_KEYS
) | frozenset({"errors"})


def published_field_names(parsed: object) -> tuple[str, ...]:
    """Top-level field names a receipt may list: published names only."""
    if not isinstance(parsed, dict):
        return ()
    return tuple(sorted(k for k in parsed if k in PUBLISHED_FIELDS))
