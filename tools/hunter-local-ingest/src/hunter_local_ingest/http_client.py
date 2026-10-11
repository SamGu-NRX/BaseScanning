"""Direct HTTP adapter implementing the frozen interface.

Transport label: this is the client used for REAL_MIRROR_CPU checks when it
points at the real local mirror backend, and for FAKE_HUNTER checks when a
scenario fronts it with the fault proxy. The scenarios own the label.

Route map, bodies, headers and answer shapes derive from the published wire
behavior in ios/HouseScanKit/Sources/HouseScanKit/Upload/ (CaptureAPI.swift,
CaptureUploader.swift, ScopedCaptureHTTP.swift) on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807. Nothing here is copied from any
private source; expectations beyond this published contract are refined
only from sanitized local-run observations (traces/).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.parse

from .adapter import StepResult
from .observations import Observation

_JSON = "application/json"


class _Endpoint:
    """Parsed base URL: scheme, host, port, and the path prefix (…/v1)."""

    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"endpoint must be an http(s) URL: {base_url!r}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.prefix = parsed.path.rstrip("/")

    def route(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"


def _one_request(
    scheme: str,
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout_s: float,
) -> tuple[int, bytes, int]:
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
    finally:
        conn.close()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return status, payload, elapsed_ms


class DirectHttpClient:
    """The adapter the public runner uses against a configurable endpoint."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 30.0,
        max_same_body_retries: int = 2,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self._ep = _Endpoint(base_url)
        self._timeout_s = timeout_s
        self._max_same_body_retries = max(0, max_same_body_retries)
        self._retry_backoff_s = retry_backoff_s

    # -- plumbing ---------------------------------------------------------

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
        """One step; same-body retries only where the published uploader retries.

        ``context_builder(status, payload_bytes) -> dict | None`` lets a step
        stash transient working values (never serialized) for the next step.
        """
        host = self._ep.host
        port = self._ep.port
        scheme = self._ep.scheme
        path = url
        if url.lower().startswith(("http://", "https://")):
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or host
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            scheme = parsed.scheme
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        attempts_left = max(same_body_retries, 0)
        last_error = "unreachable"
        for attempt in range(1 + attempts_left):
            try:
                status, payload, elapsed_ms = _one_request(
                    scheme, host, port, method, path, body, headers, timeout
                )
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                last_error = f"transport after {attempt + 1} attempt(s): {type(exc).__name__}"
                if attempt < attempts_left:
                    time.sleep(self._retry_backoff_s)
                continue
            detail = f"attempt {attempt + 1}" if attempt else None
            if status == 503 and attempt < attempts_left:
                # Published uploader behavior: retry the same body on 503.
                time.sleep(self._retry_backoff_s)
                continue
            return StepResult(
                step=step,
                ok=True,
                observation=Observation.from_http(status, payload, elapsed_ms, detail),
                context=context_builder(status, payload) if context_builder else None,
            )
        return StepResult(step=step, ok=False, error=last_error)

    @staticmethod
    def _json_bytes(payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # -- frozen interface --------------------------------------------------

    def health(self) -> StepResult:
        return self._call("health", "GET", self._ep.route("healthz"), None, {"Accept": _JSON})

    def create(self, request: dict, idempotency_key: str) -> StepResult:
        headers = {
            "Content-Type": _JSON,
            "Accept": _JSON,
            "Idempotency-Key": idempotency_key,
        }
        return self._call(
            "create", "POST", self._ep.route("captures"), self._json_bytes(request), headers
        )

    def register(self, capture_id: str, files: list[dict]) -> StepResult:
        headers = {"Content-Type": _JSON, "Accept": _JSON}
        body = self._json_bytes({"files": files})

        def stash_targets(status: int, payload: bytes) -> dict | None:
            """Pull the signed upload targets into transient step context.

            The receipt never records them (observations.py drops nested
            values); the upload step needs the target it was granted.
            """
            if not (200 <= status < 300):
                return None
            try:
                answer = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            entries = answer.get("files") if isinstance(answer, dict) else None
            if not isinstance(entries, list):
                return None
            targets = []
            for entry in entries:
                upload = entry.get("upload") if isinstance(entry, dict) else None
                if isinstance(upload, dict) and isinstance(upload.get("url"), str):
                    targets.append(
                        {"url": upload["url"], "headers": upload.get("headers") or {}}
                    )
                else:
                    targets.append({})
            return {"upload_targets": targets}

        return self._call(
            "register",
            "POST",
            self._ep.route(f"captures/{capture_id}/files"),
            body,
            headers,
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
        del capture_id  # identity is carried by the signed URL
        # Exactly the headers the server signed; never the API's own credentials.
        send_headers = dict(headers or {})
        send_headers.setdefault("Content-Type", "application/octet-stream")
        return self._call("upload", "PUT", url, body, send_headers)

    def commit(self, capture_id: str, files: list[dict]) -> StepResult:
        headers = {"Content-Type": _JSON, "Accept": _JSON}
        body = self._json_bytes({"files": files})
        return self._call(
            "commit",
            "POST",
            self._ep.route(f"captures/{capture_id}/files:commit"),
            body,
            headers,
            same_body_retries=self._max_same_body_retries,
        )

    def finalize(self, capture_id: str, packet: bytes, idempotency_key: str) -> StepResult:
        headers = {
            "Content-Type": _JSON,
            "Accept": _JSON,
            "Idempotency-Key": idempotency_key,
            "X-Packet-Sha256": hashlib.sha256(packet).hexdigest(),
        }
        return self._call(
            "finalize",
            "POST",
            self._ep.route(f"captures/{capture_id}/finalize"),
            packet,
            headers,
            same_body_retries=self._max_same_body_retries,
        )

    def events(self, capture_id: str, after: int, wait_s: int) -> StepResult:
        path = f"captures/{capture_id}/events?after={after}&wait={wait_s}"
        return self._call(
            "events",
            "GET",
            self._ep.route(path),
            None,
            {"Accept": _JSON},
            timeout_s=wait_s + 30.0,
        )

    def result(self, capture_id: str) -> StepResult:
        return self._call(
            "result",
            "GET",
            self._ep.route(f"captures/{capture_id}/result"),
            None,
            {"Accept": _JSON},
        )
