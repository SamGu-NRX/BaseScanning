"""String comparison and candidate ranking. Comparison rules are the B2 meter_eval ones."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re

KEYWORD = re.compile(r"^\s*(?:N[O0R]\.?|N°|№|#\s*:|S/?N|SERIAL\s*#?)\s*[.:#]?\s*", re.IGNORECASE)
SPEC = re.compile(
    r"\d\s*\(\s*\d+\s*\)|\d\s*V\b|HZ|\bKH\b|KH\s*\d|\bKT\b|\bTA\s*=?\s*\d|\bCL\s*\d|\d\s*CL\b|"
    r"\bFM\s*\d|FORM|TYPE|IMP|KWH|REV|U/|°C|AMP|VOLT|\bPHASE|\bWIRE|\d+/\d+|\bCA\s*\d",
    re.IGNORECASE,
)
CL = re.compile(r"\bCL\s*[-.:]?\s*(\d{2,3})\b|\b(\d{2,3})\s*CL\b", re.IGNORECASE)


def normalize(text: str) -> str:
    """Uppercase and keep only A-Z and 0-9."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_class(text: str) -> str:
    return re.sub(r"[^A-Z0-9()]", "", text.upper().replace("×", "X"))


def core(text: str) -> str:
    """Normalized text without a leading run of letters before a digit."""
    return re.sub(r"^[A-Z]+(?=\d)", "", normalize(text))


def digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def class_number(text: str | None) -> str | None:
    match = CL.search(text or "")
    return f"CL{match.group(1) or match.group(2)}" if match else None


def digest(normalized: str) -> str:
    """HMAC-SHA256 of a normalized meter number. Requires `METER_HMAC_KEY` (hex). Never logged."""
    raw = os.environ.get("METER_HMAC_KEY")
    if not raw:
        raise FileNotFoundError("METER_HMAC_KEY is unset; digests are not computed")
    return hmac.new(bytes.fromhex(raw), normalized.encode(), hashlib.sha256).hexdigest()


def length_ok(token_core: str) -> bool:
    return 6 <= len(token_core) <= 14 and digits(token_core) >= 5


def implausible_flags(value: str, token_core: str) -> list[str]:
    """Demote, never drop. All-zero cores and spec-like strings are implausible."""
    flags: list[str] = []
    if not length_ok(token_core) or SPEC.search(value) or SPEC.search(token_core) or set(token_core) <= {"0"}:
        flags.append("implausible_length")
    return flags


def ranked_values(answer: dict | None) -> list[tuple[str, str]]:
    """`(printed, core)` for meter_number then number_candidates, deduped on core."""
    if not answer:
        return []
    listed = [answer.get("meter_number")] + list(answer.get("number_candidates") or [])
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in listed:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        token = core(text)
        if not token or token in seen:
            continue
        seen.add(token)
        found.append((text, token))
    return found


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def share(hits: int, n: int) -> str:
    if n == 0:
        return "-"
    low, high = wilson(hits, n)
    return f"{hits}/{n} = {hits / n:.0%} ({low:.0%}-{high:.0%})"
