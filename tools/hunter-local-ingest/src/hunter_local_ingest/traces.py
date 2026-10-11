"""Frozen expected traces and their comparison.

traces/expected-lifecycle-v1.jsonl is the public, versioned expectation
set for the standard synthetic plan against a contract-conformant local
backend. Lines are JSON objects:

  {"id": "lifecycle.create", "http_status_class": "2xx",
   "field_names": ["captureId", "status"], "provisional": false}

Semantics (deliberately subset, not equality):
  - http_status_class: the observed status must fall in the class
    ("2xx", "4xx", or an exact "200" style value).
  - field_names: observed response top-level field names must CONTAIN
    these. Extra observed fields are fine — the frozen trace pins the
    published minimum, not the backend's full shape.
  - provisional: true marks expectations that await confirmation from
    the first real-backend baseline; they compare but are reported as
    advisory mismatches, never hard failures.

New backend contract versions get a new file (v2, …); v1 is never edited.
"""

from __future__ import annotations

import json
from pathlib import Path

from .modes import Status, Transport


def _status_class_ok(observed: int, expected: str) -> bool:
    if expected.isdigit():
        return observed == int(expected)
    if expected.endswith("xx"):
        low = int(expected[:-2]) * 100
        return low <= observed < low + 100
    return False


def compare_trace(
    checks: list,
    trace_path: Path,
    transport: Transport,
) -> list[str]:
    """Diff a run's checks against the frozen trace.

    Returns mismatch strings; advisory (provisional-line) mismatches are
    prefixed ``advisory:`` so callers can separate them from hard failures.
    """
    expected_lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    problems: list[str] = []
    by_id = {c.id: c for c in checks if c.transport is transport}

    for exp in expected_lines:
        check_id = exp["id"]
        advisory = bool(exp.get("provisional"))
        prefix = "advisory: " if advisory else ""
        check = by_id.get(check_id)
        if check is None:
            problems.append(f"{prefix}trace: {check_id} missing from run")
            continue
        if check.status is not Status.PASS:
            if check.status is Status.FAIL:
                problems.append(
                    f"{prefix}trace: {check_id} FAILs in run; trace expects success"
                )
            continue
        obs = check.observation
        if obs is None:
            if exp.get("http_status_class") is not None or exp.get("field_names") is not None:
                problems.append(f"{prefix}trace: {check_id} has no observation to compare")
            continue
        status_class = exp.get("http_status_class")
        if status_class is not None and (
            obs.http_status is None or not _status_class_ok(obs.http_status, str(status_class))
        ):
            problems.append(
                f"{prefix}trace: {check_id} http {obs.http_status} not in class {status_class}"
            )
        if exp.get("field_names") is not None:
            missing = set(exp["field_names"]) - set(obs.field_names)
            if missing:
                problems.append(
                    f"{prefix}trace: {check_id} response fields missing {sorted(missing)}"
                )
    return problems
