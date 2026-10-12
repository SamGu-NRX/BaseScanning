"""Artifact privacy screening: the single owner of forbidden patterns.

Every receipt artifact (run-manifest.json, checks.jsonl, summary.json, CLI
failure output) must be safe for public export. This module is the one place
that defines what may never appear in one, and owns the two operations the
rest of the harness consumes.

Sibling seam (child C wires these from runner.py):

  screen_artifact(path) -> list[str]
      Screen one artifact file. Returns finding strings
      ``"<file>:<line>: <category>"``; an empty list means clean. A missing
      file raises FileNotFoundError; undecodable content is a finding.

  redact_command(argv) -> str
      Render a command line safe to record in a receipt: credential-named
      flag values become ``***``, URL values lose userinfo/query material,
      and any remaining forbidden span is replaced by ``<redacted:...>``.

Receipts-layer support (used by receipts.py; not part of the sibling seam):

  redact_text(text) -> str
      Replace every forbidden span in free text with ``<redacted:category>``.

Forbidden categories: bearer tokens, credential key/value assignments,
URL userinfo, signed-capability URLs, non-loopback endpoints, private
hostnames, key material (PEM/AWS/GitHub/JWT shapes), source snippets (source
path:line refs, tracebacks, private tree names, home-dir paths), and
non-ASCII/control characters.

What the screen deliberately does NOT catch: an unstructured string with no
credential, URL, hostname, or source anchor (for example a bare word-shaped
secret with no assignment separator). Such strings must never reach an
artifact at all: observations.py validates the shape of every recorded value,
so the screen is the second line of defense, not a substitute. Findings
never echo matched material -- only category, file, and line.
"""

from __future__ import annotations

import re
import shlex
import urllib.parse
from pathlib import Path

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
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{6,})?\b"
        ),
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


def _is_loopback_host(host: str) -> bool:
    host = (host or "").lower().strip("[]")
    return (
        host in {"127.0.0.1", "::1", "localhost"}
        or host.startswith("127.")
        or host.endswith(".localhost")
    )


def _classify_url(url: str) -> str | None:
    """Classify one absolute URL; None means it may appear in a receipt."""
    try:
        parts = urllib.parse.urlsplit(url)
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
    host = (parts.hostname or "").lower()
    if not _is_loopback_host(host):
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


def safe_failure(argv: list[str], message: str) -> str:
    """One-line CLI failure output, safe for stdout.

    The runner prints exception summaries on failure; both the command and
    the message are operator/server-influenced free text, so both pass the
    same redaction as every artifact.
    """
    return f"{redact_command(argv)}: {redact_text(message)}"


def _flag_split(token: str) -> tuple[str | None, str, str]:
    """Split ``--flag=value``; returns (normalized_name, eq, inline_value)."""
    if not token.startswith("--"):
        return None, "", ""
    name_part, eq, inline = token[2:].partition("=")
    return name_part.lower().replace("_", "-"), eq, inline


def _safe_token(token: str) -> str:
    """Redact one non-flag command token (URLs, interior flag-value pairs)."""
    if not token:
        return token
    return redact_text(token)


def redact_command(argv: list[str]) -> str:
    """Render a command line safe for receipts.

    Credential-named flag values become ``***`` (both ``--flag=value`` and
    ``--flag value`` forms, case-insensitive); URL tokens lose userinfo,
    signed-query material, and non-loopback endpoints; everything else goes
    through the same forbidden-span redaction as free text.
    """
    parts: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        name, eq, _inline = _flag_split(token)
        if name is not None and name in _CREDENTIAL_FLAG_NAMES:
            original_name = token[2:].partition("=")[0]
            if eq:
                parts.append(f"--{original_name}=***")
            else:
                parts.append(token)
                if i + 1 < len(argv):
                    parts.append("***")
                    i += 1
        else:
            parts.append(_safe_token(token))
        i += 1
    return " ".join(shlex.quote(part) if _needs_quote(part) else part for part in parts)


def _needs_quote(part: str) -> bool:
    # Parts containing redaction placeholders are already safe; never
    # shell-quote them.
    if "***" in part or "<redacted:" in part:
        return False
    return bool(part) and not re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", part)
