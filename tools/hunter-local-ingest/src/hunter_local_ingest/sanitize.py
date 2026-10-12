"""Artifact privacy screening and command redaction.

Two layers keep private material out of receipts. The first is
construction: export.py validates every artifact value against a typed
schema before it is written, so a value reaches disk only if it is a
published enum word, a digest, a run-declared path, a bounded number or a
constant from this package. This module is the second layer, a pattern
screen that runs over the written files after the summary exists.

The screen cannot recognize a bare word-shaped secret (``CANARY-abc123``
looks like an honest id), which is why the first layer exists. Findings
name a file, a line and a category; they never echo the matched text.

  screen_artifact(path) -> list[str]
      ``"<file>:<line>: <category>"`` per finding; empty means clean. A
      missing file raises FileNotFoundError; undecodable bytes are a finding.

  redact_argv(argv, known) -> list[str]
      The one boundary for recording a command. It works on the argv list,
      never on a joined shell string. Values of this program's own flags
      are rendered by type; every other value becomes ``<value>``, and a
      credential-named flag's value becomes ``***``.

  safe_failure(argv, category) -> str
      A one-line CLI error naming a fixed category and the redacted argv.

Forbidden categories: bearer tokens, credential key/value assignments,
URL userinfo, signed-capability URLs, non-loopback endpoints, private
hostnames, key material (PEM/AWS/GitHub/JWT shapes), source snippets (source
path:line refs, tracebacks, private tree names, home-dir paths), and
non-ASCII/control characters.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path

from .netpolicy import EndpointError, is_loopback_host, parse_api_base

MAX_FINDINGS = 200

# Absolute URLs. Trailing quote/comma/paren classes are excluded so JSON
# string values and prose delimiters do not become part of the URL. The
# closing bracket is allowed: bracketed IPv6 hosts (http://[::1]:8000) need it.
_URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s'\"<>\\),;]+")

# Query/fragment parameters that mark a URL as a signed capability.
_SIGNED_QUERY_PARAMS = frozenset(
    {
        "sig",
        "signature",
        "x-signature",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-date",
        "x-goog-signature",
        "x-expires",
        "x-synthetic-signature",
        "expires",
        "expiresat",
        "expiry",
        "googleaccessid",
        "uploadcache",
        "mac",
        "hmac",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "sv",
        "se",
        "srt",
        "sigv",
    }
)

_CREDENTIAL_FLAG_NAMES = frozenset(
    {
        "api-key",
        "apikey",
        "api-secret",
        "token",
        "access-token",
        "refresh-token",
        "secret",
        "client-secret",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "authorization",
        "auth-token",
        "bearer",
    }
)

# category, compiled pattern, capture group whose span is the secret
# (None = whole match is the span).
_NON_URL_PATTERNS: list[tuple[str, re.Pattern[str], int | None]] = [
    (
        "credential-kv",
        re.compile(
            r"(?i)[\"']?(?:api[-_]?key|api[-_]?secret|access[-_]?token|"
            r"refresh[-_]?token|client[-_]?secret|secret|password|passwd|"
            r"passphrase|token|credentials?|authorization|"
            r"(?:x[-_])?[a-z0-9-]*signature|(?:x[-_])?auth[-_]?token)"
            r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;}&<>\\]{4,})"
        ),
        1,
    ),
    (
        "credential-kv",
        re.compile(
            r"(?i)(?:^|[\s'\"])(?:--)?(?:api[-_]?key|access[-_]?token|"
            r"auth[-_]?token|bearer[-_]?token|api[-_]?token|secret[-_]?key|"
            r"password|passwd)\b\s+"
            r"[\"']?([^\s\"',;}&<>\\]{4,})"
        ),
        1,
    ),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[a-z0-9][a-z0-9._~+/=-]{7,}"), None),
    ("key-material", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), None),
    ("key-material", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), None),
    ("key-material", re.compile(r"\bgh[pousr]_[A-Za-z0-9_-]{20,}\b"), None),
    (
        "key-material",
        re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{6,})?\b"),
        None,
    ),
    (
        "private-hostname",
        re.compile(
            r"(?i)\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r"|169\.254\.\d{1,3}\.\d{1,3})(?::\d+)?\b"
            r"|\b(?:[a-z0-9-]+\.)*[a-z0-9-]+\.(?:internal|local)(?::\d+)?\b"
        ),
        None,
    ),
    (
        "source-snippet",
        re.compile(r"\b[\w./@-]+\.(?:py|swift|ts|tsx|js|mjs|go|rs):\d+\b"),
        None,
    ),
    ("source-snippet", re.compile(r"Traceback \(most recent call last\)"), None),
    (
        "source-snippet",
        re.compile(r"\b(?:house-scanning-server|private-mirror|hss-baseline)\b"),
        None,
    ),
    ("source-snippet", re.compile(r"(?<![\w.])/(?:Users|home)/[\w.-]+"), None),
]

# Priority when spans overlap: a more specific category wins the span.
_SPAN_PRIORITY = {
    "url-userinfo": 0,
    "signed-url": 1,
    "key-material": 2,
    "bearer-token": 3,
    "credential-kv": 4,
    "private-endpoint": 5,
    "private-hostname": 6,
    "source-snippet": 7,
    "non-ascii-or-control": 8,
}

_CONTROL_RE = re.compile(r"[^\x09\x0a\x0d\x20-\x7e]")

_JSON_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})|\\[nrt\"\\/]")

_SIMPLE_ESCAPES = {"\\n": " ", "\\r": " ", "\\t": " ", '\\"': '"', "\\\\": "\\", "\\/": "/"}


def _unfold(line: str) -> str:
    """Decode JSON/JSONL escape forms so they cannot hide forbidden text."""

    def repl(match: re.Match[str]) -> str:
        u, x, simple = match.group(1), match.group(2), match.group(0)
        if u is not None:
            return chr(int(u, 16))
        if x is not None:
            return chr(int(x, 16))
        return _SIMPLE_ESCAPES.get(simple, simple)

    return _JSON_ESCAPE_RE.sub(repl, line)


def _classify_url(url: str) -> str | None:
    """Classify one absolute URL; None means it may appear in a receipt."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "private-endpoint"  # fail closed on malformed URLs
    if (parts.scheme or "").lower() not in {"http", "https", "ws", "wss"}:
        return None
    if "@" in parts.netloc:
        return "url-userinfo"
    tail = parts.query + ("&" + parts.fragment if parts.fragment else "")
    params = urllib.parse.parse_qsl(tail, keep_blank_values=True)
    if any(name.lower() in _SIGNED_QUERY_PARAMS for name, _ in params):
        return "signed-url"
    # The same exact-host rule as endpoint validation (netpolicy): a
    # lookalike such as 127.0.0.1.evil.com is not loopback here either.
    if not is_loopback_host(host):
        return "private-endpoint"
    return None


def findings_in_text(text: str) -> list[tuple[str, int, int]]:
    """Non-overlapping (category, start, end) spans of forbidden material."""
    candidates: list[tuple[str, int, int]] = []
    for match in _URL_RE.finditer(text):
        category = _classify_url(match.group(0))
        if category is not None:
            candidates.append((category, match.start(), match.end()))
    # Mask URL spans so non-URL patterns cannot double-match inside them.
    work = list(text)
    for _category, start, end in candidates:
        for i in range(start, end):
            work[i] = "\x00"
    work = "".join(work)
    for category, pattern, group in _NON_URL_PATTERNS:
        for match in pattern.finditer(work):
            start, end = match.span(group) if group is not None else match.span()
            if start != end:
                candidates.append((category, start, end))
    for match in _CONTROL_RE.finditer(work):
        candidates.append(("non-ascii-or-control", match.start(), match.end()))
    candidates.sort(key=lambda c: (_SPAN_PRIORITY[c[0]], c[1], -(c[2] - c[1])))
    accepted: list[tuple[str, int, int]] = []
    for category, start, end in candidates:
        if all(end <= s or start >= e for _c, s, e in accepted):
            accepted.append((category, start, end))
    accepted.sort(key=lambda c: c[1])
    return accepted


def redact_text(text: str) -> str:
    """Replace every forbidden span with a non-leaking placeholder."""
    spans = findings_in_text(text)
    if not spans:
        return text
    out: list[str] = []
    cursor = 0
    for category, start, end in spans:
        out.append(text[cursor:start])
        out.append(f"<redacted:{category}>")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def screen_artifact(path: str) -> list[str]:
    """Screen one artifact file. Empty list = clean; missing file raises."""
    p = Path(path)
    try:
        text = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return [f"{p.name}: undecodable-or-binary (not utf-8)"]
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        categories = sorted({c for c, _s, _e in findings_in_text(_unfold(line))})
        for category in categories:
            findings.append(f"{p.name}:{lineno}: {category}")
        if len(findings) >= MAX_FINDINGS:
            findings.append(f"{p.name}: findings truncated at {MAX_FINDINGS}")
            break
    return findings


# Flag-name fragments that mark a value as a credential, after lowercasing
# and turning "_" into "-". Substring match, so --github-token and
# --client-secret count without listing every spelling.
_CREDENTIAL_FRAGMENTS = (
    "token",
    "secret",
    "passw",
    "passphrase",
    "credential",
    "authorization",
    "auth",
    "api-key",
    "apikey",
    "bearer",
    "cookie",
    "private-key",
    "signature",
)

_FLAG_RE = re.compile(r"^(--?)([A-Za-z][A-Za-z0-9_-]{0,47})(=(.*))?$", re.DOTALL)
_REF_RE = re.compile(r"^[0-9a-f]{7,40}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_INT_RE = re.compile(r"^-?[0-9]{1,9}$")
_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")

REDACTED_VALUE = "<value>"
REDACTED_SECRET = "***"


def is_credential_flag(name: str) -> bool:
    normalized = name.lower().replace("_", "-")
    return normalized in _CREDENTIAL_FLAG_NAMES or any(
        fragment in normalized for fragment in _CREDENTIAL_FRAGMENTS
    )


def _render_known(kind: str, value: str) -> str:
    """Render one value of this program's own flag by its declared type."""
    if kind == "endpoint":
        try:
            return parse_api_base(value).label
        except EndpointError:
            return "<invalid>"
    if kind.startswith("enum:"):
        return value if value in kind[5:].split("|") else "<invalid>"
    if kind == "int":
        return value if _INT_RE.fullmatch(value) else "<invalid>"
    if kind == "float":
        try:
            number = float(value)
        except ValueError:
            return "<invalid>"
        return repr(number) if math.isfinite(number) else "<invalid>"
    if kind == "ref":
        return value if _REF_RE.fullmatch(value) else "<invalid>"
    if kind == "label":
        return value if _LABEL_RE.fullmatch(value) else "<invalid>"
    if kind == "path":
        return "<path>"
    if kind == "argv-json":
        return "<argv>"
    raise ValueError(f"unknown flag kind {kind!r}")


def redact_argv(argv: Sequence[str], known: Mapping[str, str] | None = None) -> list[str]:
    """Render a command for a receipt, one argv element at a time.

    ``known`` maps this program's flags to a value kind ("flag" for a flag
    that takes no value). Its values are rendered by type: an endpoint as
    its loopback label, a ref only as hex, a path never. Everything else is
    foreign: a flag name is kept when it has a plain flag shape, and every
    value -- separated, ``=``-joined, quoted into one element -- becomes
    ``<value>``, or ``***`` after a credential-named flag. Nothing is split
    on spaces, so a quoted ``"--token X"`` element is one value.
    """
    known = known or {}
    out: list[str] = []
    # How to render the next element: a known kind, "secret", or None.
    pending: str | None = None
    for token in argv:
        if pending is not None:
            out.append(REDACTED_SECRET if pending == "secret" else _render_known(pending, token))
            pending = None
            continue
        if token == "--":
            out.append(token)
            continue
        match = _FLAG_RE.fullmatch(token)
        if match is None:
            out.append(REDACTED_VALUE)
            continue
        dashes, name, joined, inline = match.groups()
        flag = f"{dashes}{name}"
        secret = is_credential_flag(name)
        # A known flag that takes no value (such as --api-token-env, which
        # names an environment variable) consumes nothing, whatever its name
        # suggests; any value glued onto a credential-named flag is still ***.
        takes_no_value = known.get(flag) == "flag"
        kind = None if secret and not takes_no_value else known.get(flag)
        if takes_no_value and not joined:
            out.append(flag)
            continue
        if joined is not None:
            if secret:
                out.append(f"{flag}={REDACTED_SECRET}")
            elif kind is not None and kind != "flag":
                out.append(f"{flag}={_render_known(kind, inline)}")
            else:
                out.append(f"{flag}={REDACTED_VALUE}")
            continue
        out.append(flag)
        if secret:
            pending = "secret"
        elif kind == "flag":
            pending = None
        elif kind is not None:
            pending = kind
        # An unknown flag may or may not take a value; the next element,
        # if it is not a flag, is rendered as a foreign value anyway.
    return out


def safe_failure(argv: Sequence[str], category: str, known: Mapping[str, str] | None = None) -> str:
    """One-line CLI failure output: a fixed category and the redacted argv.

    Exception messages and argparse's own text never pass through here;
    they can quote the value that caused the failure.
    """
    shown = category if _CATEGORY_RE.fullmatch(category) else "error"
    return f"error: {shown} (argv: {' '.join(redact_argv(argv, known))})"
