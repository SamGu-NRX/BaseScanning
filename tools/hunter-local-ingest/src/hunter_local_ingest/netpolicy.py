"""Which URLs the harness may talk to, and which hosts count as this machine.

One policy serves every caller: the API base the runner accepts, the
signed storage URLs it will PUT to, the endpoint label a manifest may
record, and the URLs the privacy screen lets through. Before this module
the screen used ``host.startswith("127.")``, so
``http://127.0.0.1.evil.com`` passed it while the manifest code rejected
the same host.

The rule is the published client's (``CaptureAPIScope.allows`` in
HouseScanKit/Upload/ScopedCaptureHTTP.swift): https anywhere, plain http
only to ``127.0.0.1``, ``::1`` or ``localhost``, matched exactly after
parsing. Other loopback spellings (``127.0.0.2``, ``foo.localhost``) are
refused, as the client refuses them.
"""

from __future__ import annotations

import dataclasses
import urllib.parse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class EndpointError(ValueError):
    """An API base the harness refuses. ``code`` names the failed check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_loopback_host(host: str | None) -> bool:
    """True only for the exact hosts the published client treats as local."""
    if not host:
        return False
    return host.lower().strip("[]") in LOOPBACK_HOSTS


def _split(url: str) -> urllib.parse.SplitResult | None:
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return None
    return parts


def _plain_path(path: str) -> bool:
    """ScopedCaptureHTTP.isPlainPath: no dot segments, no encoded separators."""
    lowered = path.lower()
    if any(code in lowered for code in ("%2e", "%2f", "%5c")) or "\\" in path:
        return False
    return not any(segment in {".", ".."} for segment in path.split("/"))


@dataclasses.dataclass(frozen=True)
class ApiBase:
    """A parsed capture-API base URL the harness may send API requests to."""

    scheme: str
    host: str
    port: int
    base_path: str

    @property
    def label(self) -> str:
        """``host:port`` for a loopback base; a placeholder otherwise."""
        if not is_loopback_host(self.host):
            return "<redacted:endpoint>"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def route(self, path: str) -> str:
        return f"{self.base_path}/{path.lstrip('/')}"

    def in_scope(self, url: str) -> bool:
        """CaptureAPIScope.problem(with:) is nil: same origin, under the base path."""
        parts = _split(url)
        if parts is None or parts.username is not None or parts.password is not None:
            return False
        scheme = parts.scheme.lower()
        port = parts.port or (443 if scheme == "https" else 80)
        if scheme != self.scheme or (parts.hostname or "").lower() != self.host:
            return False
        if port != self.port or not _plain_path(parts.path):
            return False
        return parts.path == self.base_path or parts.path.startswith(self.base_path + "/")


def parse_api_base(url: str) -> ApiBase:
    """Accept a base the published client would give a credential to, or raise."""
    parts = _split(url)
    if parts is None or not parts.scheme or not parts.hostname:
        raise EndpointError("base_invalid")
    if parts.username is not None or parts.password is not None:
        raise EndpointError("base_userinfo")
    if parts.query or parts.fragment:
        raise EndpointError("base_query")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if not allows(scheme, host):
        raise EndpointError("base_insecure")
    if not _plain_path(parts.path):
        raise EndpointError("base_path")
    path = parts.path[:-1] if parts.path.endswith("/") else parts.path
    return ApiBase(
        scheme=scheme,
        host=host,
        port=parts.port or (443 if scheme == "https" else 80),
        base_path=path,
    )


def allows(scheme: str, host: str) -> bool:
    """CaptureAPIScope.allows under ``.loopbackHTTP``."""
    if scheme == "https":
        return True
    return scheme == "http" and is_loopback_host(host)


def upload_url_problem(url: str) -> str | None:
    """CaptureUploader.uploadURLProblem: None when a photo may go to ``url``."""
    parts = _split(url)
    if parts is None or not parts.scheme or not parts.hostname:
        return "upload_url_invalid"
    if not allows(parts.scheme.lower(), parts.hostname.lower()):
        return "upload_url_insecure"
    return None
