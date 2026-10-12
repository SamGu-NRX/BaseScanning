"""What one HTTP exchange may contribute to a public receipt.

An ``Observation`` records the status, a digest of the body, sizes, timing,
how many attempts the step took, the body's JSON shape and which published
field names it carried. The parsed body rides along in memory so the
contract decoders (contract.py) can judge it, and is never serialized.

Earlier versions copied response values into the receipt under an
allowlist of key names. That let any shape-valid server string through
(an id, a path, an unexpected key), because a word-shaped secret matches
the same patterns an honest id does. Values now reach a receipt only
through the decoders' public views, which keep published enum words and
run-declared paths and reduce everything else to digests.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import socket
import ssl

from .contract import body_shape, parse_body, published_field_names

# Transport failures, named by kind. The exception's message is never kept:
# it can quote a URL or a host.
TRANSPORT_ERRORS = frozenset(
    {
        "transport:timeout",
        "transport:connection-refused",
        "transport:connection-reset",
        "transport:remote-disconnected",
        "transport:incomplete-read",
        "transport:bad-status-line",
        "transport:tls",
        "transport:other",
    }
)


def transport_error(exc: BaseException) -> str:
    """Map an exception from http.client/socket to a fixed category."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "transport:timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "transport:connection-refused"
    if isinstance(exc, http.client.RemoteDisconnected):
        return "transport:remote-disconnected"
    if isinstance(exc, ConnectionResetError | BrokenPipeError):
        return "transport:connection-reset"
    if isinstance(exc, http.client.IncompleteRead):
        return "transport:incomplete-read"
    if isinstance(exc, http.client.BadStatusLine):
        return "transport:bad-status-line"
    if isinstance(exc, ssl.SSLError):
        return "transport:tls"
    return "transport:other"


@dataclasses.dataclass(frozen=True)
class Observation:
    """One HTTP answer. Serialize with ``public()``; ``parsed`` stays in memory."""

    http_status: int | None
    body_sha256: str | None = None
    body_bytes: int = 0
    elapsed_ms: int | None = None
    attempts: int = 1
    shape: str = "empty"
    field_names: tuple[str, ...] = ()
    parsed: object = dataclasses.field(default=None, repr=False, compare=False)

    @classmethod
    def from_http(
        cls,
        status: int,
        body: bytes,
        elapsed_ms: int,
        attempts: int = 1,
    ) -> Observation:
        parsed = parse_body(body)
        return cls(
            http_status=status,
            body_sha256=hashlib.sha256(body).hexdigest() if body else None,
            body_bytes=len(body),
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            shape=body_shape(parsed),
            field_names=published_field_names(parsed),
            parsed=parsed,
        )

    def public(self) -> dict:
        return {
            "http_status": self.http_status,
            # a digest prefix is the audit trail; the bytes never are
            "body_sha256": self.body_sha256[:12] if self.body_sha256 else None,
            "body_bytes": self.body_bytes,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
            "shape": self.shape,
            "fields": list(self.field_names),
        }
