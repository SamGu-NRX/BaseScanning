"""Run receipts: one fresh directory per run, validated, screened, chained, sealed.

A run directory holds exactly five files, all mandatory:

  run-manifest.json     what ran: refs, source digest, fixture digests,
                        redacted command, runtime, settings
  checks.jsonl          one validated line per check, flushed as it decides
  summary.json          verdict, counts, failed/blocked/not-run ids,
                        output digests
  privacy-screen.json   the pattern screen over the three files above,
                        run after the summary exists
  privacy-chain.jsonl   a SHA-256 chain over every artifact's final bytes

The directory is created with an exclusive mkdir and a random run id, and
every file with exclusive creation, so a replay can never overwrite or
merge into earlier receipts. A missing mandatory file is reported by name;
nothing creates an empty stand-in for it.

What the integrity check proves, and what it does not: the chain and the
read-only seal detect an accidental or careless edit after the run (a
changed digest, a broken link, a writable file, an extra or missing file).
They do not stop the file owner: anyone who can write the directory can
rewrite a file, recompute the chain and chmod it again. The chain head is
printed when the run ends; recording it somewhere the run cannot write is
the only anchor.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .export import (
    CHAINED_ARTIFACTS,
    SCREEN_SCHEMA,
    SCREENED_ARTIFACTS,
    SUMMARY_SCHEMA,
    ExportContext,
    ExportError,
    validate_chain_entry,
    validate_check_line,
    validate_manifest,
    validate_screen,
    validate_summary,
)
from .modes import Status, Transport
from .observations import Observation
from .plan import DERIVED_SUFFIXES, PACKET_ID_RE, PATH_RE, derived_id
from .sanitize import screen_artifact
from .scenarios.oracles import oracle_for

MANIFEST = "run-manifest.json"
CHECKS = "checks.jsonl"
SUMMARY = "summary.json"
SCREEN_FILE = "privacy-screen.json"
CHAIN_FILE = "privacy-chain.jsonl"
MANDATORY = (MANIFEST, CHECKS, SUMMARY, SCREEN_FILE, CHAIN_FILE)

_SEALED_MODE = 0o444


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclasses.dataclass
class Check:
    id: str
    transport: Transport
    status: Status
    observed: dict
    # In-process only: the Observation that decided the check. Never serialized.
    observation: Observation | None = dataclasses.field(default=None, repr=False, compare=False)

    @classmethod
    def from_line(cls, line: dict) -> Check:
        return cls(
            id=line["id"],
            transport=Transport(line["transport"]),
            status=Status(line["status"]),
            observed=line["observed"],
            observation=line.get("observation"),
        )

    def as_json(self) -> dict:
        oracle, expected = oracle_for(self.id)
        return {
            "id": self.id,
            "transport": self.transport.value,
            "status": self.status.value,
            "oracle": oracle,
            "expected": expected,
            "observed": self.observed,
            "ts": utc_now(),
        }


def fresh_run_dir(root: Path, label: str) -> tuple[Path, str]:
    """A new run directory and its run id. Never reuses or merges a directory."""
    root.mkdir(parents=True, exist_ok=True)
    run_id = secrets.token_hex(8)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"{label}-{stamp}-{run_id}"
    run_dir.mkdir()  # FileExistsError rather than reuse
    return run_dir, run_id


def context_for(paths: frozenset[str], packet_id: str, nonce: str = "") -> ExportContext:
    """The export context of a plan: its paths and its derived packet ids."""
    if not PACKET_ID_RE.match(packet_id) or not all(PATH_RE.match(p) for p in paths):
        raise ExportError(MANIFEST, "/fixture", "undeclared-identity")
    ids = {packet_id}
    for suffix in DERIVED_SUFFIXES:
        ids.add(derived_id(packet_id, suffix))
        if nonce:
            ids.add(derived_id(packet_id, suffix, nonce))
    return ExportContext(paths=frozenset(paths), packet_ids=frozenset(ids))


def _write_exclusive(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as fh:
        fh.write(text)


def _chain_append(run_dir: Path, name: str) -> None:
    """Link the artifact's current bytes to the previous chain entry."""
    data = (run_dir / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    chain_path = run_dir / CHAIN_FILE
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
        "at": utc_now(),
    }
    validate_chain_entry(entry)
    with chain_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def chain_head(run_dir: Path) -> str | None:
    chain_path = run_dir / CHAIN_FILE
    if not chain_path.exists():
        return None
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    return str(json.loads(lines[-1])["chain_sha256"]) if lines else None


def write_run_manifest(
    run_dir: Path, manifest: dict, ctx: ExportContext, known_flags: Mapping[str, str]
) -> None:
    validate_manifest(manifest, ctx, known_flags)
    _write_exclusive(run_dir / MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _chain_append(run_dir, MANIFEST)


class ChecksWriter:
    """checks.jsonl, created exclusively; each line validated, then flushed."""

    def __init__(self, run_dir: Path, ctx: ExportContext) -> None:
        self._run_dir = run_dir
        self._ctx = ctx
        self._handle = (run_dir / CHECKS).open("x", encoding="utf-8")
        self.closed = False

    def write(self, check: Check) -> None:
        line = check.as_json()
        validate_check_line(line, self._ctx)
        self._handle.write(json.dumps(line, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self.closed:
            return
        self._handle.close()
        self.closed = True
        _chain_append(self._run_dir, CHECKS)


class ChecksSink:
    """The receipt-backed sink: converts, validates and writes each line."""

    def __init__(self, writer: ChecksWriter) -> None:
        self._writer = writer
        self.checks: list[Check] = []

    def append(self, line: dict) -> Check:
        check = Check.from_line(line)
        self._writer.write(check)
        self.checks.append(check)
        return check


@dataclasses.dataclass(frozen=True)
class RunReport:
    verdict: str
    screen_findings: tuple[str, ...]
    missing: tuple[str, ...]
    integrity: tuple[str, ...]
    chain_head: str | None

    @property
    def receipts_ok(self) -> bool:
        return not (self.screen_findings or self.missing or self.integrity)


def verdict_of(checks: list[Check]) -> str:
    statuses = {c.status for c in checks}
    if Status.FAIL in statuses:
        return "FAIL"
    if Status.BLOCKED in statuses or not checks:
        return "BLOCKED"
    return "PASS"


def write_summary(
    run_dir: Path,
    run_id: str,
    checks: list[Check],
    *,
    declared_backend: str,
    lead_questions: tuple[str, ...],
    ctx: ExportContext,
) -> RunReport:
    """Write the summary, screen every artifact, chain, seal and verify.

    The manifest and checks must already exist; a missing one is reported
    as ``missing-artifact`` and makes the report not ok. Nothing is
    deleted or rewritten when the screen finds something: the evidence
    stays, sealed, and the report says what is wrong with it.
    """
    present = {name: (run_dir / name).exists() for name in (MANIFEST, CHECKS)}
    counts: dict[str, dict[str, int]] = {}
    for check in checks:
        bucket = counts.setdefault(check.transport.value, {})
        bucket[check.status.value] = bucket.get(check.status.value, 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "run_id": run_id,
        "finished_utc": utc_now(),
        "verdict": verdict_of(checks),
        "total": len(checks),
        "counts_by_transport": counts,
        "failed": [c.id for c in checks if c.status is Status.FAIL],
        "blocked": [c.id for c in checks if c.status is Status.BLOCKED],
        "not_run": [c.id for c in checks if c.status is Status.NOT_RUN],
        "mode": {
            "declared_backend": declared_backend,
            "backend_verified": False,
            "real_mirror_cpu": "NOT_ESTABLISHED",
            "gpu_arms": "NOT_RUN",
        },
        "lead_questions": list(lead_questions),
        "artifacts": {
            name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest() if ok else None
            for name, ok in present.items()
        },
    }
    validate_summary(summary, ctx)
    _write_exclusive(run_dir / SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _chain_append(run_dir, SUMMARY)

    findings: list[str] = []
    screened: list[str] = []
    missing: list[str] = []
    for name in SCREENED_ARTIFACTS:
        path = run_dir / name
        if not path.exists():
            missing.append(name)
            findings.append(f"{name}: missing-artifact")
            continue
        screened.append(name)
        findings.extend(screen_artifact(str(path)))
    record = {
        "schema": SCREEN_SCHEMA,
        "finished_utc": utc_now(),
        "screened": screened,
        "missing": missing,
        "clean": not findings,
        "findings": findings,
    }
    validate_screen(record, ctx)
    _write_exclusive(run_dir / SCREEN_FILE, json.dumps(record, indent=2, sort_keys=True) + "\n")
    _chain_append(run_dir, SCREEN_FILE)
    seal_run(run_dir)
    return RunReport(
        verdict=summary["verdict"],
        screen_findings=tuple(findings),
        missing=tuple(missing),
        integrity=tuple(verify_run_integrity(run_dir)),
        chain_head=chain_head(run_dir),
    )


def seal_run(run_dir: Path) -> None:
    """Make every present receipt file read-only. A tamper signal, not a lock."""
    for name in MANDATORY:
        path = run_dir / name
        if path.exists():
            path.chmod(_SEALED_MODE)


def read_json(path: Path, *, lines: bool = False) -> tuple[object, str | None]:
    """Parse an artifact, or name why it cannot be: never raises, never echoes.

    Returns ``(value, None)`` or ``(None, problem)`` where the problem is
    one of missing-artifact, unreadable, not-utf8 or not-json. With
    ``lines`` the value is a list of parsed lines.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "missing-artifact"
    except OSError:
        return None, "unreadable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not-utf8"
    try:
        if lines:
            return [json.loads(line) for line in text.splitlines()], None
        return json.loads(text), None
    except (ValueError, RecursionError):
        return None, "not-json"


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def verify_run_integrity(run_dir: Path) -> list[str]:
    """Problems with a sealed run directory; empty means none were found.

    Every mandatory file must exist, independently of whether the chain
    mentions it. The chain must replay, every chained artifact's latest
    entry must match its bytes, the summary's recorded digests must match
    the manifest and checks, every file must be read-only, and no other
    file may be present. A file that cannot be read or parsed is a problem
    named by file and category; its content is never repeated.
    """
    problems: list[str] = []
    for name in MANDATORY:
        if not (run_dir / name).exists():
            problems.append(f"{name}: missing-artifact")
    try:
        extra = sorted(p.name for p in run_dir.iterdir() if p.name not in MANDATORY)
    except OSError:
        return [*problems, "run-dir: unreadable"]
    if extra:
        problems.append(f"run-dir: {len(extra)} unexpected file(s)")

    latest: dict[str, str] = {}
    chain_path = run_dir / CHAIN_FILE
    if chain_path.exists():
        try:
            chain_text = chain_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            chain_text = None
            problems.append(f"{CHAIN_FILE}: not-utf8")
        except OSError:
            chain_text = None
            problems.append(f"{CHAIN_FILE}: unreadable")
        prev = ""
        for lineno, line in enumerate((chain_text or "").splitlines(), 1):
            try:
                entry = json.loads(line)
                validate_chain_entry(entry)
            except (ValueError, RecursionError):  # JSON errors and ExportError
                problems.append(f"{CHAIN_FILE}:{lineno}: unreadable chain entry")
                continue
            expected = hashlib.sha256(
                (prev + entry["artifact"] + entry["sha256"]).encode()
            ).hexdigest()
            if entry["chain_sha256"] != expected:
                problems.append(f"{CHAIN_FILE}:{lineno}: chain break")
            latest[entry["artifact"]] = entry["sha256"]
            prev = entry["chain_sha256"]

    for name in CHAINED_ARTIFACTS:
        path = run_dir / name
        if not path.exists():
            continue
        if name not in latest:
            problems.append(f"{name}: no chain entry")
        elif _digest(path) != latest[name]:
            problems.append(f"{name}: modified after chain entry")

    if (run_dir / SUMMARY).exists():
        summary, problem = read_json(run_dir / SUMMARY)
        recorded = summary.get("artifacts") if isinstance(summary, dict) else None
        if problem:
            problems.append(f"{SUMMARY}: {problem}")
        elif not isinstance(recorded, dict):
            problems.append(f"{SUMMARY}: artifacts is not an object")
        else:
            for name in (MANIFEST, CHECKS):
                path = run_dir / name
                actual = _digest(path) if path.exists() else None
                if recorded.get(name) != actual:
                    problems.append(f"{name}: differs from the digest in the summary")

    for name in MANDATORY:
        path = run_dir / name
        try:
            writable = path.exists() and path.stat().st_mode & 0o222
        except OSError:
            writable = False
        if writable:
            problems.append(f"{name}: not sealed (writable)")
    return problems
