"""The client adapter interface, version 2.0.0.

2.0.0 changed two signatures from 1.0.0. ``create`` takes the encoded body
bytes, because the published client encodes the create body once and
sends those exact bytes on every retry (CaptureUploader.start). ``upload``
dropped ``capture_id`` and ``path``, which 1.0.0 accepted and ignored.

Public client code and public scenarios code against this seam, never
against one backend's implementation.

Derivation: the published wire behavior in
ios/HouseScanKit/Sources/HouseScanKit/Upload/ (CaptureAPI.swift,
CaptureUploader.swift, ScopedCaptureHTTP.swift) on alpha
263827efaa5d600ecd7ed0443b6e7dd05fbef807. Route paths, bodies, headers and
answer types come from that public source, not from any private repository.

Published semantics carried over deliberately:
- No redirects: a 3xx is returned as its own reply, never followed.
- HTTP error statuses come back as observations; transport failures and
  client-side refusals come back as a StepResult ``error`` category.
- ``Idempotency-Key: <packetId>`` on create and finalize;
  ``X-Packet-Sha256`` on finalize.
- A storage PUT carries exactly the headers the register answer signed.
  It never carries the API's credential, and a signed set that holds
  ``Authorization`` is refused before anything is sent
  (``auth_storage_authorization``).
- A transient answer (no answer, 408, 429, or 5xx other than 501) is
  retried with the same body a bounded number of times, as
  ``CaptureUploader.isTransient`` defines it.

New steps arrive as new methods with a bumped INTERFACE_VERSION, never as
edits to existing method signatures.
"""

from __future__ import annotations

import dataclasses
import typing

from .observations import Observation

INTERFACE_VERSION = "2.0.0"

# Refusals the client makes without sending anything.
CLIENT_REFUSALS = frozenset(
    {
        "auth_storage_authorization",
        "auth_destination_refused",
        "upload_url_invalid",
        "upload_url_insecure",
    }
)


@dataclasses.dataclass(frozen=True)
class StepResult:
    """One adapter step: an observation, or an error category with no answer."""

    step: str
    observation: Observation | None = None
    error: str | None = None
    attempts: int = 1

    @property
    def http_status(self) -> int | None:
        return self.observation.http_status if self.observation else None

    @property
    def parsed(self) -> object:
        return self.observation.parsed if self.observation else None

    def public(self) -> dict:
        if self.observation is not None:
            return self.observation.public()
        return {"error": self.error, "attempts": self.attempts}


@typing.runtime_checkable
class HunterIngestAdapter(typing.Protocol):
    """One capture's lifecycle against the capture API, over HTTP."""

    def health(self) -> StepResult: ...

    def create(self, body: bytes, idempotency_key: str) -> StepResult: ...

    def register(self, capture_id: str, files: list[dict]) -> StepResult: ...

    def upload(self, url: str, body: bytes, headers: dict[str, str]) -> StepResult: ...

    def commit(self, capture_id: str, files: list[dict]) -> StepResult: ...

    def finalize(self, capture_id: str, packet: bytes, idempotency_key: str) -> StepResult: ...

    def events(self, capture_id: str, after: int, wait_s: int) -> StepResult: ...

    def result(self, capture_id: str) -> StepResult: ...
