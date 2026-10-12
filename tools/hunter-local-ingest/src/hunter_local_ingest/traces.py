"""Frozen expected traces, loaded from the installed package, and their comparison.

``traces/expected-lifecycle-v2.jsonl`` is the current expectation, written
from the published contract: each line names a check, the status it must
end with, the HTTP status class of the answer that decided it, and the
published top-level fields that answer must carry (subset semantics:
extra fields are fine).

``expected-lifecycle-v1.jsonl`` is kept unedited as history. It expected a
``schema_version`` field on the result, which the published
CaptureResult.Response does not have, and a health route the client never
calls; v2 replaces it. A new contract version gets a new file.

Traces are package data, read through ``importlib.resources``, so a
non-editable install finds them inside the installed package.
"""

from __future__ import annotations

import dataclasses
import json
from importlib import resources

from .modes import Status, Transport

CURRENT_TRACE = "expected-lifecycle-v2.jsonl"


def load_trace(name: str = CURRENT_TRACE) -> list[dict]:
    text = (
        resources.files("hunter_local_ingest")
        .joinpath("traces")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _status_class_ok(observed: int, expected: str) -> bool:
    if expected.isdigit():
        return observed == int(expected)
    if expected.endswith("xx") and expected[:-2].isdigit():
        low = int(expected[:-2]) * 100
        return low <= observed < low + 100
    raise ValueError("trace http_status_class must be like 2xx or 404")


@dataclasses.dataclass(frozen=True)
class TraceComparison:
    compared: int
    # (check id, disagreement kind) — kinds are export.DISAGREEMENTS
    disagreements: tuple[tuple[str, str], ...]
    # (check id, status) for traced checks that were BLOCKED or NOT_RUN
    not_comparable: tuple[tuple[str, str], ...]

    @property
    def status(self) -> Status:
        if self.disagreements:
            return Status.FAIL
        if self.not_comparable:
            return Status.BLOCKED
        return Status.PASS

    def public(self) -> dict:
        return {
            "compared": self.compared,
            "disagreements": [{"id": i, "kind": k} for i, k in self.disagreements],
            "not_comparable": [{"id": i, "status": s} for i, s in self.not_comparable],
        }


def compare_trace(checks: list, expected: list[dict], transport: Transport) -> TraceComparison:
    """Diff a run's checks against a frozen trace.

    A traced check that is missing or FAILs is a disagreement. One that is
    BLOCKED or NOT_RUN is reported as not comparable, so a run that never
    reached a step can never read as agreeing with the trace.
    """
    by_id = {c.id: c for c in checks if c.transport is transport}
    disagreements: list[tuple[str, str]] = []
    not_comparable: list[tuple[str, str]] = []
    compared = 0
    for line in expected:
        check_id = line["id"]
        check = by_id.get(check_id)
        if check is None:
            disagreements.append((check_id, "missing-from-run"))
            continue
        if check.status in (Status.BLOCKED, Status.NOT_RUN):
            not_comparable.append((check_id, check.status.value))
            continue
        compared += 1
        if check.status is not Status(line.get("status", "PASS")):
            disagreements.append((check_id, "status-fail"))
            continue
        obs = check.observation
        needs_obs = line.get("http_status_class") is not None or line.get("field_names") is not None
        if obs is None:
            if needs_obs:
                disagreements.append((check_id, "no-observation"))
            continue
        status_class = line.get("http_status_class")
        if status_class is not None and (
            obs.http_status is None or not _status_class_ok(obs.http_status, str(status_class))
        ):
            disagreements.append((check_id, "http-class"))
        fields = line.get("field_names")
        if fields is not None and not set(fields) <= set(obs.field_names):
            disagreements.append((check_id, "fields-missing"))
    return TraceComparison(compared, tuple(disagreements), tuple(not_comparable))
