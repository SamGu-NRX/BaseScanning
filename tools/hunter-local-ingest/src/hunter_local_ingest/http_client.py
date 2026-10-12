"""Direct HTTP adapter implementing the frozen interface (1.1.0).

Transport label: this is the client used for REAL_MIRROR_CPU checks when it
points at the real local mirror backend, and for FAKE_HUNTER checks when a
scenario fronts it with the fault proxy. The scenarios own the label.

Route map, bodies, headers and answer shapes derive from the published wire
behavior in ios/HouseScanKit/Sources/HouseScanKit/Upload/ (CaptureAPI.swift,
CaptureUploader.swift, ScopedCaptureHTTP.swift) on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807. Nothing here is copied from any
private source; expectations beyond this published contract are refined
only from sanitized local-run observations (traces/).

Published semantics implemented here (recon art_ZtPTds0y §2):
- Scope-checked destinations (ScopedCaptureHTTP.swift:110-163): https
  anywhere, plain http only to loopback (127.0.0.1, ::1, [::1],
  localhost); no userinfo, no query or fragment on the base.
- ``Authorization: Bearer <credential>`` rides capture-API steps only
  (:56); the credential is validated as an RFC 6750 b64token before use
  (:74-83). A storage request whose signed headers carry Authorization is
  refused, never sent (:65-68).
- A storage PUT sends exactly the signed header set — the client adds
  nothing, no default Content-Type (CaptureUploader.swift:522).
- Transient classification {transport error, 408, 429, 5xx except 501}
  with exponential backoff 1→30 s, honoring Retry-After capped at 120 s
  (:817, :45-48). A 3xx is returned as its own reply, never followed.
- A response body shorter than its declared Content-Length is a transport
  error, never silently accepted (bounded reads, :13-58).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.parse
from datetime import UTC, datetime

from .adapter import StepResult
from .observations import Observation

_JSON = "application/json"

# RFC 6750 b64token (ScopedCaptureHTTP.swift:74-83).
_B64TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~+/"
)

# Published retry ceiling for a Retry-After hint (CaptureUploader.swift:48).
_MAX_RETRY_AFTER_S = 120.0

# Loopback hosts allowed for plain http (ScopedCaptureHTTP.swift:160-163).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})

# Transient status vocabulary (CaptureUploader.swift:817): transport errors,
# 408, 429, and every 5xx except 501 are retried.
_NOT_TRANSIENT_5XX = {501}


class IncompleteResponseFraming(http.client.HTTPException):
    """The body ended before its declared Content-Length was satisfied."""


def parse_expires_at(value: str) -> float | None:
    """ISO-8601 ``expiresAt`` → epoch seconds, ±fractional seconds.

    Returns None when the value is absent or unparseable; callers decide
    whether that blocks the upload.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


class _Endpoint:
    """Parsed base URL: scheme, host, port, and the path prefix (…/v1).

    Scope-checked at construction (ScopedCaptureHTTP.swift:110-155,
    :160-163): https anywhere; plain http only to loopback; no userinfo,
    query or fragment.
    """

    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"endpoint must be an http(s) URL: {base_url!r}")
        if parsed.username or parsed.password:
            raise ValueError(
                "endpoint must not embed userinfo (ScopedCaptureHTTP.swift:129)"
            )
        if parsed.query or parsed.fragment:
            raise ValueError(
                "endpoint must not carry a query or fragment"
                " (ScopedCaptureHTTP.swift:129)"
            )
        if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
            raise ValueError(
                "plain-http endpoints must be loopback"
                " (127.0.0.1, ::1, [::1], localhost); use https elsewhere"
                " (ScopedCaptureHTTP.swift:160-163)"
            )
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.prefix = parsed.path.rstrip("/")

    def route(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def destination_is_allowed(self, url: str) -> bool:
        """Scope check for a non-base destination (a signed storage URL)."""
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            return False
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
            return False
        return True


def _one_request(
    scheme: str,
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout_s: float,
) -> tuple[int, bytes, int, str | None, str | None]:
    """One request, redirects never followed (http.client does not follow)."""
    started = time.perf_counter()
    if scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout_s
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        declared = response.headers.get("Content-Length")
        retry_after = response.headers.get("Retry-After")
    finally:
        conn.close()
    # Bounded reads: a body shorter than its declared length is a framing
    # failure, not a short reply (CaptureUploader.swift:13-58).
    if declared is not None and declared.isdigit():
        if len(payload) < int(declared):
            raise IncompleteResponseFraming(
                f"declared {declared} bytes, read {len(payload)}"
            )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return status, payload, elapsed_ms, declared, retry_after


def _validate_b64token(credential: str) -> str:
    if not credential or not set(credential) <= _B64TOKEN_CHARS:
        raise ValueError(
            "credential must be an RFC 6750 b64token"
            " (letters, digits, -._~+/ with optional = padding)"
        )
    return credential


def _transient(status: int) -> bool:
    if status in _NOT_TRANSIENT_5XX:
        return False
    return status in (0, 408, 429) or (500 <= status <= 599)


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds-form Retry-After, capped at the published 120 s ceiling."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_S)


class DirectHttpClient:
    """The adapter the public runner uses against a configurable endpoint.

    ``credential`` is optional. When set it must be an RFC 6750 b64token;
    it rides capture-API requests as ``Authorization: Bearer …`` and is
    never attached to a storage PUT (ScopedCaptureHTTP.swift:56, :65-68).
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 30.0,
        max_same_body_retries: int = 2,
        retry_backoff_s: float = 1.0,
        credential: str | None = None,
        max_transient_retries: int = 3,
        retry_delay_cap_s: float = 30.0,
    ) -> None:
        self._ep = _Endpoint(base_url)
        self._timeout_s = timeout_s
        self._max_same_body_retries = max(0, max_same_body_retries)
        self._retry_backoff_s = max(0.0, retry_backoff_s)
        self._credential = _validate_b64token(credential) if credential else None
        self._max_transient_retries = max(0, max_transient_retries)
        self._retry_delay_cap_s = max(0.0, retry_delay_cap_s)

    # -- plumbing ---------------------------------------------------------

    def _api_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": _JSON}
        if self._credential:
            headers["Authorization"] = f"Bearer {self._credential}"
        if extra:
            headers.update(extra)
        return headers

    def _sleep_for_retry(self, attempt: int, advised: float | None) -> None:
        if advised is not None:
            time.sleep(min(advised, self._retry_delay_cap_s))
            return
        time.sleep(min(self._retry_backoff_s * (2**attempt), self._retry_delay_cap_s))

    def _call(
        self,
        step: str,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout_s: float | None = None,
        same_body_retries: int = 0,
        context_builder: callable | None = None,
    ) -> StepResult:
        """One step with published transient classification.

        Every step retries {transport error, 408, 429, 5xx≠501} with
        exponential backoff honoring Retry-After (CaptureUploader.swift:817,
        :45-48); commit/finalize keep at least their same-body retry bound.
        ``context_builder(status, payload_bytes) -> dict | None`` stashes
        transient working values, never serialized.
        """
        host = self._ep.host
        port = self._ep.port
        scheme = self._ep.scheme
        path = url
        if url.lower().startswith(("http://", "https://")):
            if not self._ep.destination_is_allowed(url):
                return StepResult(
                    step=step,
                    ok=False,
                    error=(
                        "upload_url_scope: destination fails the published"
                        " scope check (https anywhere, plain http loopback"
                        " only, no userinfo/query/fragment)"
                    ),
                )
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or host
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            scheme = parsed.scheme
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        attempts_left = max(same_body_retries, self._max_transient_retries)
        last_error = "unreachable"
        advised_last: float | None = None
        for attempt in range(1 + attempts_left):
            try:
                status, payload, elapsed_ms, _declared, retry_after_raw = _one_request(
                    scheme, host, port, method, path, body, headers, timeout
                )
            except (
                TimeoutError,
                OSError,
                http.client.HTTPException,
            ) as exc:
                last_error = (
                    f"transport after {attempt + 1} attempt(s): {type(exc).__name__}"
                )
                if attempt < attempts_left:
                    self._sleep_for_retry(attempt, None)
                continue
            advised = _parse_retry_after(retry_after_raw)
            if _transient(status) and attempt < attempts_left:
                if advised is not None:
                    advised_last = advised
                self._sleep_for_retry(attempt, advised)
                continue
            detail = f"attempt {attempt + 1}" if attempt else None
            if advised_last is not None:
                detail = (
                    f"{detail}; retry-after {advised_last:g}s"
                    if detail
                    else f"retry-after {advised_last:g}s"
                )
            return StepResult(
                step=step,
                ok=True,
                observation=Observation.from_http(status, payload, elapsed_ms, detail),
                context=context_builder(status, payload) if context_builder else None,
                retry_after_s=advised_last,
            )
        return StepResult(step=step, ok=False, error=last_error)

    @staticmethod
    def _json_bytes(payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # -- frozen interface --------------------------------------------------

    def health(self) -> StepResult:
        return self._call(
            "health", "GET", self._ep.route("healthz"), None, self._api_headers()
        )

    def create(self, request: dict, idempotency_key: str) -> StepResult:
        headers = self._api_headers(
            {"Content-Type": _JSON, "Idempotency-Key": idempotency_key}
        )

        def stash_create(status: int, payload: bytes) -> dict | None:
            if not (200 <= status < 300):
                return None
            answer = _parse_json(payload)
            if not isinstance(answer, dict):
                return None
            context: dict = {}
            if isinstance(answer.get("captureId"), str):
                context["capture_id"] = answer["captureId"]
            limits = answer.get("upload")
            if isinstance(limits, dict):
                context["create_limits"] = {
                    key: limits[key]
                    for key in ("maxSinglePutBytes", "urlTtlS", "maxFiles", "maxBatch")
                    if isinstance(limits.get(key), (int, float))
                    and not isinstance(limits.get(key), bool)
                }
            return context

        return self._call(
            "create",
            "POST",
            self._ep.route("captures"),
            self._json_bytes(request),
            headers,
            context_builder=stash_create,
        )

    def register(self, capture_id: str, files: list[dict]) -> StepResult:
        body = self._json_bytes({"files": files})

        def stash_targets(status: int, payload: bytes) -> dict | None:
            """Key the signed targets BY PATH (CaptureUploader.swift:460).

            ``register_targets`` maps path → {state, url?, headers?,
            method?, expires_at?}. ``upload_targets`` is a transitional
            descriptor-ordered view for the pre-rewrite lifecycle; entries
            still come from the path-keyed map.
            """
            if not (200 <= status < 300):
                return None
            answer = _parse_json(payload)
            entries = answer.get("files") if isinstance(answer, dict) else None
            if not isinstance(entries, list):
                return None
            by_path: dict[str, dict] = {}
            for entry in entries:
                if not (isinstance(entry, dict) and isinstance(entry.get("path"), str)):
                    continue
                record: dict = {}
                if isinstance(entry.get("state"), str):
                    record["state"] = entry["state"]
                upload = entry.get("upload")
                if isinstance(upload, dict):
                    if isinstance(upload.get("method"), str):
                        record["method"] = upload["method"]
                    if isinstance(upload.get("url"), str):
                        record["url"] = upload["url"]
                    if isinstance(upload.get("headers"), dict):
                        record["headers"] = dict(upload["headers"])
                    if isinstance(upload.get("expiresAt"), str):
                        record["expires_at"] = upload["expiresAt"]
                by_path[entry["path"]] = record
            context = {"register_targets": by_path}
            context["upload_targets"] = [
                by_path.get(descriptor.get("path"), {})
                for descriptor in files
                if isinstance(descriptor, dict)
            ]
            return context

        return self._call(
            "register",
            "POST",
            self._ep.route(f"captures/{capture_id}/files"),
            body,
            self._api_headers({"Content-Type": _JSON}),
            context_builder=stash_targets,
        )

    def upload(
        self,
        capture_id: str,
        path: str,
        body: bytes,
        url: str,
        headers: dict[str, str] | None,
    ) -> StepResult:
        del capture_id, path  # identity is carried by the signed URL
        signed = dict(headers or {})
        # Refuse, never send: a storage request carrying Authorization
        # (ScopedCaptureHTTP.swift:65-68, code auth_storage_authorization).
        if any(name.lower() == "authorization" for name in signed):
            return StepResult(
                step="upload",
                ok=False,
                error=(
                    "auth_storage_authorization: the signed header set carries"
                    " Authorization; storage PUTs never carry the API credential"
                ),
            )
        # Exactly the headers the server signed; the client adds nothing —
        # no default Content-Type (CaptureUploader.swift:522).
        return self._call("upload", "PUT", url, body, signed)

    def commit(self, capture_id: str, files: list[dict]) -> StepResult:
        body = self._json_bytes({"files": files})

        def stash_membership(status: int, payload: bytes) -> dict | None:
            if not (200 <= status < 300):
                return None
            answer = _parse_json(payload)
            if not isinstance(answer, dict):
                return None
            return {
                "commit_membership": {
                    key: [
                        item for item in answer.get(key, []) if isinstance(item, str)
                    ]
                    for key in ("committed", "notFound", "mismatch")
                }
            }

        return self._call(
            "commit",
            "POST",
            self._ep.route(f"captures/{capture_id}/files:commit"),
            body,
            self._api_headers({"Content-Type": _JSON}),
            same_body_retries=self._max_same_body_retries,
            context_builder=stash_membership,
        )

    def finalize(self, capture_id: str, packet: bytes, idempotency_key: str) -> StepResult:
        headers = self._api_headers(
            {
                "Content-Type": _JSON,
                "Idempotency-Key": idempotency_key,
                "X-Packet-Sha256": hashlib.sha256(packet).hexdigest(),
            }
        )

        def stash_finalize(status: int, payload: bytes) -> dict | None:
            if not (200 <= status < 300):
                return None
            answer = _parse_json(payload)
            if not isinstance(answer, dict):
                return None
            context: dict = {}
            if isinstance(answer.get("runId"), str):
                context["finalize_run_id"] = answer["runId"]
            if isinstance(answer.get("status"), str):
                context["finalize_status"] = answer["status"]
            return context

        return self._call(
            "finalize",
            "POST",
            self._ep.route(f"captures/{capture_id}/finalize"),
            packet,
            headers,
            same_body_retries=self._max_same_body_retries,
            context_builder=stash_finalize,
        )

    def events(self, capture_id: str, after: int, wait_s: int) -> StepResult:
        path = f"captures/{capture_id}/events?after={after}&wait={wait_s}"

        def stash_events(status: int, payload: bytes) -> dict | None:
            if not (200 <= status < 300):
                return None
            answer = _parse_json(payload)
            if not isinstance(answer, dict):
                return None
            view: dict = {}
            if isinstance(answer.get("next"), int):
                view["cursor"] = answer["next"]
            if isinstance(answer.get("status"), str):
                view["status"] = answer["status"]
            events = answer.get("events")
            run_ids: list[str] = []
            verdict_run_ids: list[str | None] = []
            if isinstance(events, list):
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    data = event.get("data")
                    if isinstance(data, dict) and isinstance(data.get("runId"), str):
                        run_ids.append(data["runId"])
                    # verdict_ready events bind data.runId nil-or-equal to
                    # the finalize's runId (CaptureUploader.swift:624).
                    if event.get("type") == "verdict_ready":
                        verdict_run_ids.append(
                            data.get("runId") if isinstance(data, dict) else None
                        )
            view["event_run_ids"] = run_ids
            view["verdict_ready_run_ids"] = verdict_run_ids
            return {"events_view": view}

        return self._call(
            "events",
            "GET",
            self._ep.route(path),
            None,
            self._api_headers(),
            timeout_s=wait_s + 30.0,
            context_builder=stash_events,
        )

    def result(self, capture_id: str) -> StepResult:
        def stash_result(status: int, payload: bytes) -> dict | None:
            context: dict = {"error_codes": _error_codes(payload)}
            if not (200 <= status < 300):
                return context
            answer = _parse_json(payload)
            if not isinstance(answer, dict):
                context["result_view"] = None
                return context
            view: dict = {}
            if isinstance(answer.get("runId"), str):
                view["run_id"] = answer["runId"]
            if isinstance(answer.get("status"), str):
                view["status"] = answer["status"]
            outcome = answer.get("outcome")
            view["outcome_present"] = outcome is not None
            view["outcome_kind"] = (
                outcome.get("kind")
                if isinstance(outcome, dict) and isinstance(outcome.get("kind"), str)
                else None
            )
            context["result_view"] = view
            return context

        return self._call(
            "result",
            "GET",
            self._ep.route(f"captures/{capture_id}/result"),
            None,
            self._api_headers(),
            context_builder=stash_result,
        )


def _parse_json(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _error_codes(payload: bytes) -> list[str]:
    answer = _parse_json(payload)
    if not isinstance(answer, dict):
        return []
    errors = answer.get("errors")
    if not isinstance(errors, list):
        return []
    return [
        item["code"]
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ]
