"""Direct HTTP adapter implementing the client interface.

Route map, bodies, headers and retry rules come from the published wire
behavior in ios/HouseScanKit/Sources/HouseScanKit/Upload/ (CaptureAPI.swift,
CaptureUploader.swift, ScopedCaptureHTTP.swift) on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807. The transport label (REAL_API or
FAKE_HUNTER) belongs to the scenario that drives this client, not to the
client.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
import urllib.parse

from .adapter import StepResult
from .netpolicy import ApiBase, EndpointError, parse_api_base, upload_url_problem
from .observations import Observation, transport_error

_JSON = "application/json"

# RFC 6750 b64token, as ScopedCaptureHTTP.isBearerToken checks it, matched
# against the WHOLE token: ``re.match`` with ``$`` would also accept a token
# followed by a newline, which could end or add a header.
_BEARER_RE = re.compile(r"[A-Za-z0-9\-._~+/]+=*")

# The only place a capture-API credential is read from, and only when the
# operator passes --api-token-env. A secret manager should put it into the
# runner's process environment; a literal assignment in a shell would land in
# history. Nothing here hides it from the same user or root, who can read a
# process's environment.
API_TOKEN_ENV = "HUNTER_API_TOKEN"


def child_env() -> dict[str, str]:
    """This process's environment for a child (restart command, git), minus
    the API credential: no child needs it, so none inherits it."""
    return {k: v for k, v in os.environ.items() if k != API_TOKEN_ENV}


# CaptureUploader.Policy.delay caps a server's Retry-After at 120 s.
RETRY_AFTER_CAP_S = 120.0


def is_transient(status: int) -> bool:
    """CaptureUploader.isTransient: worth the same request again."""
    return status in (0, 408, 429) or (500 <= status < 600 and status != 501)


def _connection(scheme: str, host: str, port: int, timeout_s: float) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout_s)
    return http.client.HTTPConnection(host, port, timeout=timeout_s)


def _one_request(
    scheme: str,
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout_s: float,
) -> tuple[int, bytes, float | None, int]:
    """One request. http.client never follows redirects; a 3xx comes back as is."""
    started = time.perf_counter()
    conn = _connection(scheme, host, port, timeout_s)
    try:
        # skip_accept_encoding: send no header the caller did not choose.
        conn.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        payload = response.read()
        retry_after = _retry_after(response.getheader("Retry-After"))
        status = response.status
    finally:
        conn.close()
    return status, payload, retry_after, int((time.perf_counter() - started) * 1000)


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    if seconds is None or not (seconds >= 0):  # rejects NaN as well
        return None
    return min(seconds, RETRY_AFTER_CAP_S)


class DirectHttpClient:
    """The adapter the runner uses against a configured API base.

    ``api_token``, when given, is sent as ``Authorization: Bearer`` on API
    routes only. Every API route is built from the base, so it is in scope
    by construction; a storage PUT never gets the token, even when storage
    shares the API's origin, and nothing in a response can retarget it.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
        api_token: str | None = None,
    ) -> None:
        self._base: ApiBase = parse_api_base(base_url)
        if api_token is not None and not _BEARER_RE.fullmatch(api_token):
            raise EndpointError("auth_credential_malformed")
        self._token = api_token
        self._timeout_s = timeout_s
        self._max_retries = max(0, max_retries)
        self._retry_backoff_s = max(0.0, retry_backoff_s)

    @property
    def base(self) -> ApiBase:
        return self._base

    # -- plumbing ---------------------------------------------------------

    def _send(
        self,
        step: str,
        method: str,
        scheme: str,
        host: str,
        port: int,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout_s: float | None = None,
    ) -> StepResult:
        """Bounded same-body retries on a transient answer or no answer."""
        timeout = self._timeout_s if timeout_s is None else timeout_s
        last_error = "transport:other"
        for attempt in range(1, self._max_retries + 2):
            try:
                status, payload, retry_after, elapsed = _one_request(
                    scheme, host, port, method, path, body, headers, timeout
                )
            except (OSError, http.client.HTTPException) as exc:
                last_error = transport_error(exc)
                if attempt <= self._max_retries:
                    time.sleep(self._retry_backoff_s)
                    continue
                return StepResult(step=step, error=last_error, attempts=attempt)
            if is_transient(status) and attempt <= self._max_retries:
                time.sleep(retry_after if retry_after is not None else self._retry_backoff_s)
                continue
            return StepResult(
                step=step,
                observation=Observation.from_http(status, payload, elapsed, attempts=attempt),
                attempts=attempt,
            )
        return StepResult(step=step, error=last_error, attempts=self._max_retries + 1)

    def _api(
        self,
        step: str,
        method: str,
        route: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout_s: float | None = None,
    ) -> StepResult:
        base = self._base
        path = base.route(route)
        # ScopedCaptureHTTP.send checks the final URL before it asks for the
        # credential: a server-minted id such as "../x" becomes an encoded
        # separator or a dot segment here, and nothing is sent.
        host = f"[{base.host}]" if ":" in base.host else base.host
        if not base.in_scope(f"{base.scheme}://{host}:{base.port}{path}"):
            return StepResult(step=step, error="auth_destination_refused", attempts=0)
        sent = dict(headers)
        if self._token is not None:
            sent["Authorization"] = f"Bearer {self._token}"
        return self._send(
            step,
            method,
            base.scheme,
            base.host,
            base.port,
            path,
            body,
            sent,
            timeout_s,
        )

    @staticmethod
    def _json_headers() -> dict[str, str]:
        return {"Content-Type": _JSON, "Accept": _JSON}

    # -- interface ----------------------------------------------------------

    def health(self) -> StepResult:
        return self._api("health", "GET", "healthz", None, {"Accept": _JSON})

    def create(self, body: bytes, idempotency_key: str) -> StepResult:
        headers = {**self._json_headers(), "Idempotency-Key": idempotency_key}
        return self._api("create", "POST", "captures", body, headers)

    def register(self, capture_id: str, files: list[dict]) -> StepResult:
        body = _json_bytes({"files": files})
        route = f"captures/{_segment(capture_id)}/files"
        return self._api("register", "POST", route, body, self._json_headers())

    def upload(self, url: str, body: bytes, headers: dict[str, str]) -> StepResult:
        problem = upload_url_problem(url)
        if problem:
            return StepResult(step="upload", error=problem, attempts=0)
        if any(name.lower() == "authorization" for name in headers):
            return StepResult(step="upload", error="auth_storage_authorization", attempts=0)
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        port = parts.port or (443 if scheme == "https" else 80)
        # Exactly the headers the server signed; nothing added.
        return self._send(
            "upload", "PUT", scheme, parts.hostname or "", port, path, body, dict(headers)
        )

    def commit(self, capture_id: str, files: list[dict]) -> StepResult:
        body = _json_bytes({"files": files})
        route = f"captures/{_segment(capture_id)}/files:commit"
        return self._api("commit", "POST", route, body, self._json_headers())

    def finalize(self, capture_id: str, packet: bytes, idempotency_key: str) -> StepResult:
        headers = {
            **self._json_headers(),
            "Idempotency-Key": idempotency_key,
            "X-Packet-Sha256": hashlib.sha256(packet).hexdigest(),
        }
        route = f"captures/{_segment(capture_id)}/finalize"
        return self._api("finalize", "POST", route, packet, headers)

    def events(self, capture_id: str, after: int, wait_s: int) -> StepResult:
        route = f"captures/{_segment(capture_id)}/events?after={int(after)}&wait={int(wait_s)}"
        # CaptureUploader.pollEvents: the request may be held for `wait` seconds.
        return self._api("events", "GET", route, None, {"Accept": _JSON}, timeout_s=wait_s + 30.0)

    def result(self, capture_id: str) -> StepResult:
        route = f"captures/{_segment(capture_id)}/result"
        return self._api("result", "GET", route, None, {"Accept": _JSON})


def _segment(value: str) -> str:
    """A server-minted id as one path segment; it can never add a segment."""
    return urllib.parse.quote(value, safe="")


def _json_bytes(payload: dict) -> bytes:
    # The published client's JSONEncoder output is compact.
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
