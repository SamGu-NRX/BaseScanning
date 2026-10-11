"""Run receipts: run-manifest.json, checks.jsonl, summary.json.

Every run writes into a fresh, never-overwritten directory. A replay that
lands on an existing directory name is refused, never merged into it.

Check-line contract (one JSON object per line in checks.jsonl):
  id         stable check id, e.g. "lifecycle.create"
  transport  REAL_MIRROR_CPU | FAKE_HUNTER | GPU_STUB
  oracle     what decides correctness, and its public basis
  expected   the expectation under that oracle
  observed   what actually happened (sanitized)
  status     PASS | FAIL | BLOCKED | NOT_RUN
  evidence   path to the receipt of the step that decided it, if any
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .modes import Status, Transport

RUN_ARTIFACTS = ("run-manifest.json", "checks.jsonl", "summary.json")


class ChecksSink:
    """The one sink interface scenarios and vocabulary helpers write to.

    Anything with ``.append(line_dict)`` is a sink; this class is the
    receipt-backed implementation the runner uses. It validates the enums
    and writes each line as it is recorded, so a crashed run still leaves
    every check that decided before the crash.
    """

    def __init__(self, writer: ChecksWriter) -> None:
        self._writer = writer
        self.checks: list[Check] = []

    def append(self, line: dict) -> Check:
        check = Check(
            id=line["id"],
            transport=Transport(line["transport"]),
            oracle=line["oracle"],
            expected=line["expected"],
            observed=line["observed"],
            status=Status(line["status"]),
            evidence=line.get("evidence"),
            observation=line.get("observation"),
        )
        self.checks.append(check)
        self._writer.write(check)
        return check


@dataclasses.dataclass
class Check:
    id: str
    transport: Transport
    oracle: str
    expected: str
    observed: str
    status: Status
    evidence: str | None = None
    # In-process only: the raw Observation behind `observed`. Never serialized.
    observation: object | None = dataclasses.field(
        default=None, repr=False, compare=False
    )

    def as_json(self) -> dict:
        out = {
            "id": self.id,
            "transport": self.transport.value,
            "oracle": self.oracle,
            "expected": self.expected,
            "observed": self.observed,
            "status": self.status.value,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        if self.evidence:
            out["evidence"] = self.evidence
        return out


def fresh_run_dir(root: Path, label: str) -> Path:
    """A fresh run directory; replay never overwrites an existing one."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"{label}-{stamp}"
    if run_dir.exists():
        raise SystemExit(
            f"refusing to overwrite existing run directory {run_dir}; replay into a new --out"
        )
    run_dir.mkdir(parents=True)
    return run_dir


def write_run_manifest(
    run_dir: Path,
    *,
    endpoint: str,
    backend_ref: str | None,
    backend_mode: str,
    client_ref: str,
    adapter_interface: str,
    fixture_identity: str,
    seed: int,
    commands: list[str],
    label: str,
    origin_note: str,
) -> None:
    manifest = {
        "label": label,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "endpoint": endpoint,
        "backend_ref": backend_ref,
        "backend_mode": backend_mode,
        "client_ref": client_ref,
        "adapter_interface": adapter_interface,
        "fixture_identity": fixture_identity,
        "seed": seed,
        "commands": commands,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
        },
        "origin_note": origin_note,
        "sanitization": (
            "receipts carry statuses, field names, digests, timings, synthetic ids and "
            "allowlisted scalar values only; no private source, schemas, specs, raw logs, "
            "signed URLs or credentials"
        ),
    }
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class ChecksWriter:
    """Append-only checks.jsonl."""

    def __init__(self, run_dir: Path) -> None:
        self._path = run_dir / "checks.jsonl"
        self._handle = self._path.open("a", encoding="utf-8")

    def write(self, check: Check) -> None:
        self._handle.write(json.dumps(check.as_json(), sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def write_summary(
    run_dir: Path,
    checks: list[Check],
    *,
    lead_questions: list[str],
    blocked: list[str],
    notes: list[str] | None = None,
) -> None:
    counts: dict[str, dict[str, int]] = {}
    for check in checks:
        counts.setdefault(check.transport.value, {})
        counts[check.transport.value][check.status.value] = (
            counts[check.transport.value].get(check.status.value, 0) + 1
        )
    summary = {
        "finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(checks),
        "counts_by_transport": counts,
        "lead_questions": lead_questions,
        "blocked": blocked,
        "notes": notes or [],
        "wall_clock_note": (
            "elapsed_ms fields in observations are per step; "
            "run wall time is the manifest-to-summary span"
        ),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
