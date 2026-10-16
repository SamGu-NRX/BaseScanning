"""Idempotency keys are sha256 of canonical JSON. The stored body is what a replay returns."""

from __future__ import annotations

import hashlib
import json


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()
