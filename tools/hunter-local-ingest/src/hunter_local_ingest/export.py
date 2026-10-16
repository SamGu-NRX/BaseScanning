"""Typed validation of every receipt artifact before it is written.

A pattern screen cannot tell an honest id from a word-shaped secret, so
receipts are safe by construction instead: each artifact has a schema, and
every string in it must belong to a known class. The classes are published
enum words and this package's registered constants (contract.py,
observations.py, scenarios/oracles.py, this module), digests
(``id:``/``unrecognized:``/``unexpected-path:``/``unexpected-keys:``/``large-int:`` plus 12
hex), hex digests, ISO timestamps, and the paths and packet ids the run's
own plan declared. Numbers must be finite and bounded.

A violation raises ``ExportError`` naming the artifact, a JSON pointer built
only from keys that already passed validation, and a category. It never
quotes the offending value. The caller decides what happens next; the
runner records the category and exits non-zero, so nothing unvalidated
reaches disk.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Mapping

from . import contract
from .adapter import CLIENT_REFUSALS
from .modes import Status, Transport
from .observations import TRANSPORT_ERRORS
from .scenarios.oracles import ORACLES

# Notes a scenario may attach to a check's observed record.
NOTES = frozenset(
    {
        "endpoint-unreachable",
        "skipped-after-blocked-step",
        "skipped-after-failed-step",
        "budget-exhausted",
        "not-ready-limit",
        "result-runid-mismatch",
        "result-status-mismatch",
        "retry-finalize-limit",
        "restart-not-configured",
        "restart-failed",
        "restart-timeout",
        "endpoint-not-back",
        "baseline-incomplete",
        "gpu-not-authorized",
        "plan-too-small",
        "harness-exception",
        "auth_fault_proxy_not_authorized",
    }
)

DISAGREEMENTS = frozenset(
    {"missing-from-run", "status-fail", "http-class", "fields-missing", "no-observation"}
)

STEPS = frozenset(
    {"health", "create", "register", "upload", "commit", "finalize", "events", "result"}
)
SHAPES = frozenset({"empty", "not-json", "object", "json-non-object"})
EXCEPTIONS = frozenset(
    {
        "exception:AssertionError",
        "exception:KeyError",
        "exception:TypeError",
        "exception:ValueError",
        "exception:OSError",
        "exception:RuntimeError",
        "exception:other",
    }
)

VOCABULARY = frozenset().union(
    contract.CAPTURE_STATUSES,
    contract.OUTCOME_KINDS,
    contract.ROLES,
    contract.TIERS,
    contract.REGISTER_STATES,
    contract.EVENT_TYPES,
    contract.EVENT_DIRECTIVES,
    contract.ERROR_CODES,
    contract.HARNESS_CODES,
    contract.HEALTH_STATUSES,
    contract.VIOLATIONS,
    contract.PUBLISHED_FIELDS,
    {"PUT", "ok"},
    TRANSPORT_ERRORS,
    CLIENT_REFUSALS,
    SHAPES,
    NOTES,
    DISAGREEMENTS,
    STEPS,
    EXCEPTIONS,
    {t.value for t in Transport},
    {s.value for s in Status},
    ORACLES.keys(),
)

# Keys an ``observed`` record may use, at any depth.
OBSERVED_KEYS = frozenset(
    {
        # contract public views
        "captureId",
        "status",
        "maxBatch",
        "upload_unexpected_keys",
        "unexpected_keys",
        "files",
        "files_total",
        "path",
        "state",
        "upload",
        "method",
        "url_present",
        "url_policy",
        "signed_header_count",
        "signs_auth_header",
        "signed_content_type",
        "committed",
        "notFound",
        "mismatch",
        "missing",
        "runId",
        "etaS",
        "next",
        "events",
        "events_total",
        "seq",
        "type",
        "data",
        "code",
        "kind",
        "stage",
        "runId_matches",
        "outcome",
        "reasons",
        "viewsNeeded",
        "memberActions",
        "criteria",
        "has_preview_url",
        "codes",
        "count",
        "digest",
        # observation public view
        "http_status",
        "body_sha256",
        "body_bytes",
        "elapsed_ms",
        "attempts",
        "shape",
        "fields",
        "error",
        # scenario records
        "http",
        "contract",
        "errors",
        "violations",
        "note",
        "batches",
        "uploads",
        "answers",
        "last",
        "finalize_retries",
        "not_ready_reads",
        "first",
        "second",
        "same_capture",
        "tampered",
        "intact",
        "tampered_committed",
        "tampered_reported",
        "tampered_put_status",
        "intact_committed",
        "exit_code",
        "after_restart",
        "status_matches",
        "run_matches",
        "proxy_hits",
        "compared",
        "disagreements",
        "not_comparable",
        "id",
        "exception",
        "terminal_status",
        "packet",
        "capture",
        "step",
        "declared",
        "committed_total",
        "committed_at_register",
        "intact_reported_negative",
    }
)

_DIGEST_RE = re.compile(
    r"^(id|unrecognized|unexpected-path|unexpected-keys|large-int):[0-9a-f]{12}$"
)
_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|\+00:00)$"
)
_REF_RE = re.compile(r"^[0-9a-f]{7,40}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_ENDPOINT_LABEL_RE = re.compile(r"^(127\.0\.0\.1|\[::1\]|localhost):[0-9]{1,5}$")
_RUNTIME_TEXT_RE = re.compile(r"^[A-Za-z0-9._+-]{1,40}$")
_FLAG_RE = re.compile(r"^--?[A-Za-z][A-Za-z0-9_-]{0,47}$")
_INT_TEXT_RE = re.compile(r"^-?[0-9]{1,9}$")
_FLOAT_TEXT_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?(e[+-]?[0-9]+)?$")

_MAX_DEPTH = 8
_MAX_LIST = 256
_MAX_INT = 2**53


class ExportError(ValueError):
    """An artifact value outside its schema. Carries no value text."""

    def __init__(self, artifact: str, pointer: str, category: str) -> None:
        super().__init__(f"{artifact}{pointer}: {category}")
        self.artifact = artifact
        self.pointer = pointer
        self.category = category


@dataclasses.dataclass(frozen=True)
class ExportContext:
    """What this run itself declared, and so may appear verbatim."""

    paths: frozenset[str] = frozenset()
    packet_ids: frozenset[str] = frozenset()


class _V:
    def __init__(self, artifact: str, ctx: ExportContext) -> None:
        self.artifact = artifact
        self.ctx = ctx

    def fail(self, pointer: str, category: str) -> ExportError:
        return ExportError(self.artifact, pointer, category)

    # -- leaves ------------------------------------------------------------

    def number(self, value: object, ptr: str, *, low: float = -_MAX_INT, high: float = _MAX_INT):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.fail(ptr, "not-a-number")
        if isinstance(value, float) and not math.isfinite(value):
            raise self.fail(ptr, "non-finite-number")
        if not (low <= value <= high):
            raise self.fail(ptr, "number-out-of-range")

    def integer(self, value: object, ptr: str, *, low: int = -_MAX_INT, high: int = _MAX_INT):
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.fail(ptr, "not-an-integer")
        if not (low <= value <= high):
            raise self.fail(ptr, "number-out-of-range")

    def boolean(self, value: object, ptr: str):
        if not isinstance(value, bool):
            raise self.fail(ptr, "not-a-boolean")

    def match(self, value: object, ptr: str, pattern: re.Pattern[str], category: str):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise self.fail(ptr, category)

    def member(self, value: object, ptr: str, allowed: frozenset[str] | set[str], category: str):
        if not isinstance(value, str) or value not in allowed:
            raise self.fail(ptr, category)

    def safe_string(self, value: str, ptr: str):
        if value in VOCABULARY or value in self.ctx.paths or value in self.ctx.packet_ids:
            return
        if _DIGEST_RE.fullmatch(value) or _HEX12_RE.fullmatch(value):
            return
        raise self.fail(ptr, "unsafe-string")

    def keys(self, obj: object, ptr: str, required: set[str], optional: set[str] = frozenset()):
        if not isinstance(obj, dict):
            raise self.fail(ptr, "not-an-object")
        for key in obj:
            if key not in required and key not in optional:
                raise self.fail(ptr, "unexpected-key")
        for key in required:
            if key not in obj:
                raise self.fail(f"{ptr}/{key}", "missing-key")

    # -- observed records -----------------------------------------------------

    def observed(self, value: object, ptr: str, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            raise self.fail(ptr, "too-deep")
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, int | float):
            self.number(value, ptr)
            return
        if isinstance(value, str):
            self.safe_string(value, ptr)
            return
        if isinstance(value, list):
            if len(value) > _MAX_LIST:
                raise self.fail(ptr, "list-too-long")
            for i, item in enumerate(value):
                self.observed(item, f"{ptr}/{i}", depth + 1)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or key not in OBSERVED_KEYS:
                    raise self.fail(ptr, "unexpected-key")
                self.observed(item, f"{ptr}/{key}", depth + 1)
            return
        raise self.fail(ptr, "unsupported-type")


def validate_check_line(line: object, ctx: ExportContext) -> None:
    v = _V("checks.jsonl", ctx)
    v.keys(line, "", {"id", "transport", "status", "oracle", "expected", "observed", "ts"})
    assert isinstance(line, dict)
    v.member(line["id"], "/id", set(ORACLES), "unregistered-check-id")
    oracle, expected = ORACLES[line["id"]]
    if line["oracle"] != oracle:
        raise v.fail("/oracle", "oracle-not-registered-text")
    if line["expected"] != expected:
        raise v.fail("/expected", "expected-not-registered-text")
    v.member(line["transport"], "/transport", {t.value for t in Transport}, "enum")
    v.member(line["status"], "/status", {s.value for s in Status}, "enum")
    v.match(line["ts"], "/ts", _ISO_RE, "not-a-timestamp")
    if not isinstance(line["observed"], dict):
        raise v.fail("/observed", "not-an-object")
    v.observed(line["observed"], "/observed")


def validate_command(tokens: object, known: Mapping[str, str], v: _V, ptr: str) -> None:
    """Re-check a redacted argv against the same flag table that rendered it."""
    if not isinstance(tokens, list) or len(tokens) > 128:
        raise v.fail(ptr, "not-a-command")
    pending: str | None = None
    for i, token in enumerate(tokens):
        here = f"{ptr}/{i}"
        if not isinstance(token, str):
            raise v.fail(here, "not-a-string")
        # An unknown flag may take no value; redact_argv then emits the next
        # flag as a flag, so a flag-shaped token ends the foreign pending.
        if pending == "foreign" and _FLAG_RE.fullmatch(token.partition("=")[0]):
            pending = None
        if pending is not None:
            _rendered(token, pending, v, here)
            pending = None
            continue
        if token in {"--", "<value>", "***"}:
            continue
        flag, eq, rest = token.partition("=")
        if not _FLAG_RE.fullmatch(flag):
            raise v.fail(here, "unsafe-command-token")
        kind = known.get(flag)
        if eq:
            if kind is None or kind == "flag":
                if rest not in {"<value>", "***"}:
                    raise v.fail(here, "unsafe-command-token")
            else:
                _rendered(rest, kind, v, here)
        elif kind not in (None, "flag"):
            pending = kind
        elif kind is None:
            pending = "foreign"


def _rendered(token: str, kind: str, v: _V, ptr: str) -> None:
    placeholders = {"<value>", "***", "<invalid>"}
    if token in placeholders:
        return
    ok = False
    if kind == "foreign":
        ok = False
    elif kind == "endpoint":
        ok = bool(_ENDPOINT_LABEL_RE.fullmatch(token)) or token == "<redacted:endpoint>"
    elif kind.startswith("enum:"):
        ok = token in kind[5:].split("|")
    elif kind == "int":
        ok = bool(_INT_TEXT_RE.fullmatch(token))
    elif kind == "float":
        ok = bool(_FLOAT_TEXT_RE.fullmatch(token))
    elif kind == "ref":
        ok = bool(_REF_RE.fullmatch(token))
    elif kind == "label":
        ok = bool(_LABEL_RE.fullmatch(token))
    elif kind == "path":
        ok = token == "<path>"
    elif kind == "argv-json":
        ok = token == "<argv>"
    if not ok:
        raise v.fail(ptr, "unsafe-command-token")


def validate_manifest(manifest: object, ctx: ExportContext, known: Mapping[str, str]) -> None:
    v = _V("run-manifest.json", ctx)
    v.keys(
        manifest,
        "",
        {
            "schema",
            "run_id",
            "label",
            "created_utc",
            "endpoint",
            "backend",
            "client",
            "fixture",
            "command",
            "restart",
            "runtime",
            "settings",
            "origin_note",
            "sanitization",
        },
    )
    assert isinstance(manifest, dict)
    v.member(manifest["schema"], "/schema", {MANIFEST_SCHEMA}, "schema")
    v.match(manifest["run_id"], "/run_id", _RUN_ID_RE, "not-a-run-id")
    v.match(manifest["label"], "/label", _LABEL_RE, "not-a-label")
    v.match(manifest["created_utc"], "/created_utc", _ISO_RE, "not-a-timestamp")
    endpoint = manifest["endpoint"]
    if endpoint != "<redacted:endpoint>":
        v.match(endpoint, "/endpoint", _ENDPOINT_LABEL_RE, "not-an-endpoint-label")

    backend = manifest["backend"]
    v.keys(backend, "/backend", {"declared_mode", "verified", "declared_ref"})
    v.member(
        backend["declared_mode"], "/backend/declared_mode", {"REAL_API", "FAKE_HUNTER"}, "enum"
    )
    if backend["verified"] is not False:
        raise v.fail("/backend/verified", "backend-mode-is-never-verified")
    _optional_ref(backend["declared_ref"], "/backend/declared_ref", v)

    client = manifest["client"]
    v.keys(
        client, "/client", {"package_version", "interface", "source_sha256", "git", "declared_ref"}
    )
    v.match(client["package_version"], "/client/package_version", _RUNTIME_TEXT_RE, "version")
    v.match(client["interface"], "/client/interface", _RUNTIME_TEXT_RE, "version")
    v.match(client["source_sha256"], "/client/source_sha256", _HEX64_RE, "not-a-digest")
    _optional_ref(client["declared_ref"], "/client/declared_ref", v)
    git = client["git"]
    if git is not None:
        v.keys(git, "/client/git", {"head", "dirty", "diff_sha256", "untracked"})
        v.match(git["head"], "/client/git/head", re.compile(r"^[0-9a-f]{40}$"), "not-a-commit")
        v.boolean(git["dirty"], "/client/git/dirty")
        v.match(git["diff_sha256"], "/client/git/diff_sha256", _HEX64_RE, "not-a-digest")
        v.integer(git["untracked"], "/client/git/untracked", low=0, high=10**6)

    fixture = manifest["fixture"]
    v.keys(
        fixture,
        "/fixture",
        {
            "generator",
            "format_version",
            "seed",
            "plan_file_sha256",
            "plan_sha256",
            "packet_id",
            "packet_sha256",
            "create_sha256",
            "files",
        },
    )
    v.member(
        fixture["generator"],
        "/fixture/generator",
        {"gen_synthetic_packet", "gen_miniarkit_packet"},
        "generator",
    )
    v.integer(fixture["format_version"], "/fixture/format_version", low=1, high=100)
    # The mini-arkit family is fixture-determined, not seeded: null seed.
    if fixture["seed"] is not None:
        v.integer(fixture["seed"], "/fixture/seed", low=0, high=2**31)
    for key in ("plan_file_sha256", "plan_sha256", "packet_sha256", "create_sha256"):
        v.match(fixture[key], f"/fixture/{key}", _HEX64_RE, "not-a-digest")
    v.member(
        fixture["packet_id"], "/fixture/packet_id", set(ctx.packet_ids), "undeclared-packet-id"
    )
    files = fixture["files"]
    if not isinstance(files, list) or not files or len(files) > 64:
        raise v.fail("/fixture/files", "not-a-file-list")
    for i, entry in enumerate(files):
        ptr = f"/fixture/files/{i}"
        v.keys(entry, ptr, {"path", "bytes", "sha256", "role", "contentType"})
        v.member(entry["path"], f"{ptr}/path", set(ctx.paths), "undeclared-path")
        v.integer(entry["bytes"], f"{ptr}/bytes", low=1, high=2**31)
        v.match(entry["sha256"], f"{ptr}/sha256", _HEX64_RE, "not-a-digest")
        v.member(entry["role"], f"{ptr}/role", set(contract.ROLES), "enum")
        v.member(entry["contentType"], f"{ptr}/contentType", set(CONTENT_TYPES), "enum")

    validate_command(manifest["command"], known, v, "/command")
    restart = manifest["restart"]
    v.keys(restart, "/restart", {"configured", "argv", "timeout_s"})
    v.boolean(restart["configured"], "/restart/configured")
    validate_command(restart["argv"], {}, v, "/restart/argv")
    v.number(restart["timeout_s"], "/restart/timeout_s", low=0, high=3600)

    runtime = manifest["runtime"]
    v.keys(
        runtime, "/runtime", {"python", "implementation", "system", "release", "machine", "cpus"}
    )
    for key in ("python", "implementation", "system", "release", "machine"):
        v.match(runtime[key], f"/runtime/{key}", _RUNTIME_TEXT_RE, "runtime-text")
    if runtime["cpus"] is not None:
        v.integer(runtime["cpus"], "/runtime/cpus", low=1, high=4096)

    settings = manifest["settings"]
    v.keys(
        settings,
        "/settings",
        {
            "result_budget_s",
            "poll_interval_s",
            "events_wait_s",
            "max_retries",
            "retry_backoff_s",
            "with_faults",
        },
    )
    v.number(settings["result_budget_s"], "/settings/result_budget_s", low=0, high=86400)
    v.number(settings["poll_interval_s"], "/settings/poll_interval_s", low=0, high=3600)
    v.integer(settings["events_wait_s"], "/settings/events_wait_s", low=0, high=3600)
    v.integer(settings["max_retries"], "/settings/max_retries", low=0, high=10)
    v.number(settings["retry_backoff_s"], "/settings/retry_backoff_s", low=0, high=600)
    v.boolean(settings["with_faults"], "/settings/with_faults")
    v.member(manifest["origin_note"], "/origin_note", {ORIGIN_NOTE}, "not-registered-text")
    v.member(manifest["sanitization"], "/sanitization", {SANITIZATION_NOTE}, "not-registered-text")


def _optional_ref(value: object, ptr: str, v: _V) -> None:
    if value is not None:
        v.match(value, ptr, _REF_RE, "not-a-ref")


def validate_summary(summary: object, ctx: ExportContext) -> None:
    v = _V("summary.json", ctx)
    v.keys(
        summary,
        "",
        {
            "schema",
            "run_id",
            "finished_utc",
            "verdict",
            "total",
            "counts_by_transport",
            "failed",
            "blocked",
            "not_run",
            "mode",
            "lead_questions",
            "artifacts",
        },
    )
    assert isinstance(summary, dict)
    v.member(summary["schema"], "/schema", {SUMMARY_SCHEMA}, "schema")
    v.match(summary["run_id"], "/run_id", _RUN_ID_RE, "not-a-run-id")
    v.match(summary["finished_utc"], "/finished_utc", _ISO_RE, "not-a-timestamp")
    v.member(summary["verdict"], "/verdict", set(VERDICTS), "enum")
    v.integer(summary["total"], "/total", low=0, high=10**6)
    counts = summary["counts_by_transport"]
    if not isinstance(counts, dict):
        raise v.fail("/counts_by_transport", "not-an-object")
    for transport, by_status in counts.items():
        v.member(transport, "/counts_by_transport", {t.value for t in Transport}, "enum")
        if not isinstance(by_status, dict):
            raise v.fail(f"/counts_by_transport/{transport}", "not-an-object")
        for status, n in by_status.items():
            v.member(status, f"/counts_by_transport/{transport}", {s.value for s in Status}, "enum")
            v.integer(n, f"/counts_by_transport/{transport}/{status}", low=0, high=10**6)
    for key in ("failed", "blocked", "not_run"):
        ids = summary[key]
        if not isinstance(ids, list):
            raise v.fail(f"/{key}", "not-a-list")
        for i, check_id in enumerate(ids):
            v.member(check_id, f"/{key}/{i}", set(ORACLES), "unregistered-check-id")
    mode = summary["mode"]
    v.keys(mode, "/mode", {"declared_backend", "backend_verified", "real_mirror_cpu", "gpu_arms"})
    v.member(
        mode["declared_backend"], "/mode/declared_backend", {"REAL_API", "FAKE_HUNTER"}, "enum"
    )
    if mode["backend_verified"] is not False:
        raise v.fail("/mode/backend_verified", "backend-mode-is-never-verified")
    v.member(mode["real_mirror_cpu"], "/mode/real_mirror_cpu", {"NOT_ESTABLISHED"}, "enum")
    v.member(mode["gpu_arms"], "/mode/gpu_arms", {"NOT_RUN"}, "enum")
    questions = summary["lead_questions"]
    if not isinstance(questions, list):
        raise v.fail("/lead_questions", "not-a-list")
    for i, question in enumerate(questions):
        v.member(
            question,
            f"/lead_questions/{i}",
            set(LEAD_QUESTIONS) | set(LEGACY_LEAD_QUESTIONS),
            "not-registered-text",
        )
    artifacts = summary["artifacts"]
    v.keys(artifacts, "/artifacts", set(), {"run-manifest.json", "checks.jsonl"})
    for name, digest in artifacts.items():
        if digest is not None:
            v.match(digest, f"/artifacts/{name}", _HEX64_RE, "not-a-digest")


_FINDING_RE = re.compile(
    r"^(run-manifest\.json|checks\.jsonl|summary\.json)"
    r"(:[0-9]{1,7}: [a-z-]{3,40}|: undecodable-or-binary \(not utf-8\)"
    r"|: findings truncated at [0-9]{1,5}|: missing-artifact)$"
)


def validate_screen(record: object, ctx: ExportContext) -> None:
    v = _V("privacy-screen.json", ctx)
    v.keys(record, "", {"schema", "finished_utc", "screened", "missing", "clean", "findings"})
    assert isinstance(record, dict)
    v.member(record["schema"], "/schema", {SCREEN_SCHEMA}, "schema")
    v.match(record["finished_utc"], "/finished_utc", _ISO_RE, "not-a-timestamp")
    for key in ("screened", "missing"):
        if not isinstance(record[key], list):
            raise v.fail(f"/{key}", "not-a-list")
        for i, name in enumerate(record[key]):
            v.member(name, f"/{key}/{i}", set(SCREENED_ARTIFACTS), "artifact-name")
    v.boolean(record["clean"], "/clean")
    if not isinstance(record["findings"], list):
        raise v.fail("/findings", "not-a-list")
    for i, finding in enumerate(record["findings"]):
        v.match(finding, f"/findings/{i}", _FINDING_RE, "finding-format")


def validate_chain_entry(entry: object) -> None:
    v = _V("privacy-chain.jsonl", ExportContext())
    v.keys(entry, "", {"artifact", "sha256", "bytes", "chain_sha256", "at"})
    assert isinstance(entry, dict)
    v.member(entry["artifact"], "/artifact", set(CHAINED_ARTIFACTS), "artifact-name")
    v.match(entry["sha256"], "/sha256", _HEX64_RE, "not-a-digest")
    v.integer(entry["bytes"], "/bytes", low=0, high=2**40)
    v.match(entry["chain_sha256"], "/chain_sha256", _HEX64_RE, "not-a-digest")
    v.match(entry["at"], "/at", _ISO_RE, "not-a-timestamp")


# --- registered constants ------------------------------------------------------

MANIFEST_SCHEMA = "hunter-local-ingest/run-manifest/2"
SUMMARY_SCHEMA = "hunter-local-ingest/summary/2"
SCREEN_SCHEMA = "hunter-local-ingest/privacy-screen/2"

SCREENED_ARTIFACTS = ("run-manifest.json", "checks.jsonl", "summary.json")
CHAINED_ARTIFACTS = ("run-manifest.json", "checks.jsonl", "summary.json", "privacy-screen.json")
CONTENT_TYPES = ("application/octet-stream", "application/gzip", "image/jpeg")

# PASS: every executed check passed and nothing was blocked. NOT_RUN rows
# (GPU arms, an unconfigured restart) are listed in the summary, not hidden.
VERDICTS = ("PASS", "FAIL", "BLOCKED")

ORIGIN_NOTE = (
    "Derived from published client behavior in HouseScanKit upload and result sources at "
    "alpha 263827ef. No private source, schema, spec, log or credential material appears "
    "in this package or its receipts."
)
SANITIZATION_NOTE = (
    "Every value here passed the typed export schema before it was written: published enum "
    "words, package constants, digests, bounded numbers, and the plan's own synthetic paths "
    "and packet ids. A pattern screen then ran over the written files."
)

# Exact texts that earlier versions wrote into summary.json and that are no
# longer true. They are accepted ONLY so frozen receipt sets (evidence/23d5fc9,
# evidence/1511668) still verify; no run writes them, and no other text is
# accepted.
LEGACY_LEAD_QUESTIONS = (
    "Local-run auth mode: which credential form does the local backend expect from the "
    "published client? The client can send a bearer token on API routes only; no CLI flag "
    "supplies one yet.",
)

LEAD_QUESTIONS = (
    "Amendment semantics: does the published contract allow amending a registered capture, "
    "and with which route and headers?",
    # The lead settled the harness's side of local-run auth; this states it.
    "--api-token-env opts into HUNTER_API_TOKEN for scoped API routes; "
    "token-protected proxy fault coverage remains BLOCKED.",
    "Clock and expiry authority: whose clock governs capture expiry on the local backend?",
    "Homeowner text: confirm that receipts may carry outcome kinds and counts but never "
    "messages, prompts or reasons.",
)
