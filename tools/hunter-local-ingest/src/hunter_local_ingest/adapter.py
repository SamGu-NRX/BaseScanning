"""The frozen client adapter interface, version 1.0.0.

Public client code and public scenarios code against this seam, never
against one backend's implementation.

Derivation: the published wire behavior in
ios/HouseScanKit/Sources/HouseScanKit/Upload/ (CaptureAPI.swift,
CaptureUploader.swift, ScopedCaptureHTTP.swift) on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807. Route paths, bodies, headers and
answer types come from that public source, not from any private repository.

Published semantics carried over deliberately:
- No redirects: a 3xx is returned as its own reply, never followed.
- HTTP error statuses come back as observations; only transport failures
  raise (and are reported as StepResult errors, not exceptions).
- ``Idempotency-Key: <packetId>`` on create and on finalize;
  ``X-Packet-Sha256`` on finalize.
- A storage PUT goes out with exactly the headers the register answer
  signed, and never carries the API's own credentials.
- Bounded same-body retries, only where the published uploader retries
  (commit, finalize, on read timeout or 503).

The interface is frozen: new steps arrive as new methods with a bumped
INTERFACE_VERSION, never as edits to existing method signatures.
"""

from __future__ import annotations

import dataclasses
import typing

from .observations import Observation

INTERFACE_VERSION = "1.0.0"


@dataclasses.dataclass(frozen=True)
class StepResult:
    """One adapter step's outcome: an observation, or a transport error.

    ``context`` carries transient working values between steps (e.g. the
    signed upload target the register answer granted). It is NEVER
    serialized: as_json() omits it, and receipts carry only observations.
    """

    step: str
    ok: bool
    observation: Observation | None = None
    error: str | None = None
    context: dict | None = None

    @property
    def http_status(self) -> int | None:
        return self.observation.http_status if self.observation else None

    def as_json(self) -> dict:
        out: dict = {"step": self.step, "ok": self.ok}
        if self.observation:
            out["observation"] = self.observation.as_json()
        if self.error:
            out["error"] = self.error
        return out


@typing.runtime_checkable
class HunterIngestAdapter(typing.Protocol):
    """One capture's lifecycle against the capture API, over HTTP.

    Implementations ship in this package: the direct HTTP client (the real
    local mirror backend) and the fault-proxy wrapper (labeled
    FAKE_HUNTER). Each step returns a StepResult; scenarios decide
    PASS/FAIL from observations plus the published contract.
    """

    def health(self) -> StepResult: ...

    def create(self, request: dict, idempotency_key: str) -> StepResult: ...

    def register(self, capture_id: str, files: list[dict]) -> StepResult: ...

    def upload(
        self,
        capture_id: str,
        path: str,
        body: bytes,
        url: str,
        headers: dict[str, str] | None,
    ) -> StepResult: ...

    def commit(self, capture_id: str, files: list[dict]) -> StepResult: ...

    def finalize(self, capture_id: str, packet: bytes, idempotency_key: str) -> StepResult: ...

    def events(self, capture_id: str, after: int, wait_s: int) -> StepResult: ...

    def result(self, capture_id: str) -> StepResult: ...
