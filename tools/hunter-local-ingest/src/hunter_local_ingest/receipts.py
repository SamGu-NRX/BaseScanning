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

Privacy and integrity hardening (artifact-level):
  - the manifest records the endpoint as a loopback host[:port] label only,
    never the raw URL (userinfo, query or token never reach disk), and every
    free-text manifest field is redacted;
  - free-text check fields pass the forbidden-span redaction at write time;
  - write_summary screens all three artifacts after writing them and records
    the outcome in privacy-screen.json (failed-run receipts survive, sealed,
    never rewritten to look clean);
  - every artifact's digest is chained into receipts-chain.jsonl at write/
    close time and files are sealed read-only at summary time; a second
    writer to the same run dir is detected by verify_run_integrity().
"""

from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import os
import platform
import sys
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from .modes import Status, Transport
from .sanitize import redact_text, screen_artifact

RUN_ARTIFACTS = ("run-manifest.json", "checks.jsonl", "summary.json")
CHAIN_FILE = "privacy-chain.jsonl"
SCREEN_FILE = "privacy-screen.json"

# Mode for receipt files once sealed.
_SEALED_MODE = 0o444


def _redacted_or_none(value: str | None) -> str | None:
    """Redact an optional free-text manifest field; None passes through."""
    return redact_text(value) if value is not None else None


def _safe_endpoint_label(endpoint: str) -> str:
    """Host[:port] of a loopback endpoint, or a redaction placeholder.

    The raw endpoint never enters a receipt: userinfo, query strings and
    non-loopback hostnames are all excluded. Loopback is decided by
    ipaddress (a lookalike hostname like 127.0.0.1.evil.com is excluded).
    """
    try:
        parts = urllib.parse.urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return "<redacted:endpoint>"
    host = (parts.hostname or "").lower()
    recordable = host == "localhost" or host.endswith(".localhost")
    if not recordable:
        try:
            recordable = ipaddress.ip_address(host).is_loopback
        except ValueError:
            recordable = False
    if not host or not recordable:
        return "<redacted:endpoint>"
    return f"{host}:{port}" if port else host


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
            # observed is data-driven (scenario strings, server-influenced
            # detail); it passes the same redaction as every artifact.
            "observed": redact_text(self.observed),
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


def _chain_append(run_dir: Path, name: str) -> bool:
    """Append one digest-chain entry for ``name``; False if already sealed.

    The chain links every artifact digest to the previous entry, so removing
    or rewriting any earlier record breaks every later link. Post-seal the
    chain file is read-only; write_summary has already chained the final
    bytes of every artifact by then, so a late close() call is a no-op by
    protocol, not an error to swallow silently.
    """
    path = run_dir / name
    if not path.exists():
        return False
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    chain_path = run_dir / CHAIN_FILE
    if chain_path.exists() and not os.access(chain_path, os.W_OK):
        return False
    prev = ""
    if chain_path.exists():
        lines = chain_path.read_text(encoding="utf-8").splitlines()
        if lines:
            prev = str(json.loads(lines[-1])["chain_sha256"])
    entry = {
        "artifact": name,
        "sha256": digest,
        "bytes": len(data),
        "chain_sha256": hashlib.sha256((prev + name + digest).encode()).hexdigest(),
        "at": utc_now_iso(),
    }
    with chain_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return True


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
        "label": redact_text(label),
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        # host/label only — never the raw URL (userinfo/query/token excluded)
        "endpoint": _safe_endpoint_label(endpoint),
        "backend_ref": _redacted_or_none(backend_ref),
        "backend_mode": redact_text(backend_mode),
        "client_ref": _redacted_or_none(client_ref),
        "adapter_interface": redact_text(adapter_interface),
        "fixture_identity": redact_text(fixture_identity),
        "seed": seed,
        # the caller's command lines, screened through the same redaction as
        # every artifact (credential flag values, URL material, endpoints)
        "commands": [redact_text(command) for command in commands],
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
        },
        "origin_note": redact_text(origin_note),
        "sanitization": (
            "receipts carry statuses, field names, digests, timings, synthetic ids and "
            "allowlisted scalar values only; no private source, schemas, specs, raw logs, "
            "signed URLs or credentials"
        ),
    }
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _chain_append(run_dir, "run-manifest.json")


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
        _chain_append(self._path.parent, "checks.jsonl")


def _chain_names(run_dir: Path) -> set[str]:
    chain_path = run_dir / CHAIN_FILE
    if not chain_path.exists():
        return set()
    names: set[str] = set()
    for line in chain_path.read_text(encoding="utf-8").splitlines():
        try:
            names.add(str(json.loads(line)["artifact"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return names


def seal_run(run_dir: Path) -> None:
    """Make every receipt file read-only (best-effort over missing files)."""
    for name in (*RUN_ARTIFACTS, CHAIN_FILE, SCREEN_FILE):
        path = run_dir / name
        if path.exists():
            path.chmod(_SEALED_MODE)


def verify_run_integrity(run_dir: Path) -> list[str]:
    """Replay the receipt digest chain; empty list = intact run dir."""
    chain_path = run_dir / CHAIN_FILE
    if not chain_path.exists():
        return [f"missing {CHAIN_FILE}"]
    problems: list[str] = []
    prev = ""
    latest: dict[str, dict] = {}
    for lineno, line in enumerate(
        chain_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            entry = json.loads(line)
            name = str(entry["artifact"])
            digest = str(entry["sha256"])
            chain_sha = str(entry["chain_sha256"])
        except (json.JSONDecodeError, KeyError):
            problems.append(f"{CHAIN_FILE}:{lineno}: unreadable chain entry")
            continue
        expected = hashlib.sha256((prev + name + digest).encode()).hexdigest()
        if chain_sha != expected:
            problems.append(f"{CHAIN_FILE}:{lineno}: chain break")
        latest[name] = entry
        prev = chain_sha
    for name in (*RUN_ARTIFACTS, CHAIN_FILE, SCREEN_FILE):
        path = run_dir / name
        if not path.exists():
            if name in latest or name == CHAIN_FILE:
                problems.append(f"{name}: chained but missing")
            continue
        if name not in latest and name != CHAIN_FILE:
            problems.append(f"{name}: no chain entry")
            continue
        if name in latest:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current != latest[name]["sha256"]:
                problems.append(f"{name}: modified after chain entry")
        if path.stat().st_mode & 0o222:
            problems.append(f"{name}: not sealed (writable)")
    return problems


def write_summary(
    run_dir: Path,
    checks: list[Check],
    *,
    lead_questions: list[str],
    blocked: list[str],
    notes: list[str] | None = None,
) -> list[str]:
    """Write summary.json, screen all artifacts, chain and seal the run.

    Returns the privacy-screen findings (empty list = clean). The screen
    runs AFTER the summary is written, over every artifact that exists;
    nothing is deleted on findings — failed evidence survives, sealed, for
    the operator to inspect.
    """
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        raise SystemExit(
            f"refusing to rewrite existing {summary_path}; use a fresh --out"
        )
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
        "blocked": [redact_text(entry) for entry in blocked],
        "notes": [redact_text(entry) for entry in (notes or [])],
        "wall_clock_note": (
            "elapsed_ms fields in observations are per step; "
            "run wall time is the manifest-to-summary span"
        ),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _chain_append(run_dir, "summary.json")

    # Post-write screen over every artifact, then record the outcome.
    findings: list[str] = []
    screened: list[str] = []
    for name in (*RUN_ARTIFACTS, SCREEN_FILE):
        path = run_dir / name
        if not path.exists():
            continue
        screened.append(name)
        findings.extend(screen_artifact(str(path)))
    screen_record = {
        "finished_utc": utc_now_iso(),
        "screened": screened,
        "clean": not findings,
        "findings": findings,
    }
    (run_dir / SCREEN_FILE).write_text(
        json.dumps(screen_record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _chain_append(run_dir, SCREEN_FILE)

    # Failure-path ordering: the ChecksWriter may still be open (runner
    # closes after summary); chain any artifact that has no entry yet so
    # the sealed state covers every receipt file.
    for name in RUN_ARTIFACTS:
        if name not in _chain_names(run_dir):
            _chain_append(run_dir, name)
    seal_run(run_dir)
    return findings


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
