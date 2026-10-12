"""Schema-level validation for capture evidence pack manifests.

The packer runs these checks before writing a pack, and the tests use them to
pin SPEC.md to code. The reader deliberately does NOT import this module: it
re-derives its own checks from SPEC.md and the zip, so a shared bug cannot hide
in both sides of the format.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_FORMAT_VERSION = "1.0"
REPORT_ENTRY = "report.md"

REQUIRED_TOP_LEVEL = (
    "evidence_pack_format_version",
    "capture_schema_version",
    "result_schema_version",
    "capture",
    "result",
    "missing_fields",
    "reproduction",
    "geometry_correctness",
    "packaged_files",
)

PACK_SPEC_REQUIRED = (
    "capture_id",
    "captured_at",
    "capture_schema_version",
    "result_schema_version",
    "result_id",
    "source_refs",
    "result_path",
    "missing_fields",
)
PACK_SPEC_OPTIONAL = ("report_path",)

# Packer-side resource bounds; SPEC.md, Archive limits, explains them. They
# match the reader's values by intent, not by import: the reader keeps its
# own copy so a wrong bound here cannot loosen it there.
MAX_PACK_SPEC_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_REFERENCED_FILE_BYTES = 64 * 1024 * 1024
# Output limits the reader enforces on the archive the packer writes.
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# C0 controls, DEL and C1 controls. NUL makes the OS path calls raise, and
# the others make names that terminals and receipts cannot show faithfully.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_STRICT_TIMESTAMP_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T[0-2]\d:[0-5]\d:[0-5]\dZ$")


class InputProblem(Exception):
    """A packer input that cannot be used, with the SPEC.md refusal kind."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.match(value) is not None


def is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_relative_path(value: object) -> bool:
    """A pack path is relative, POSIX-style, and cannot escape its root."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or value in (".", "..") or _CONTROL_RE.search(value):
        return False
    parts = value.split("/")
    return ".." not in parts and all(part not in ("", ".") for part in parts)


def display_path(path: Path) -> str:
    """Repository-relative when possible, so receipts carry no home directory."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-standard JSON constant {name}")


def load_json_strict(data: bytes) -> object:
    """UTF-8 JSON with no duplicate keys and no NaN or Infinity. Raises
    ValueError (UnicodeDecodeError is one) on anything else."""
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except RecursionError:
        raise ValueError("JSON nests deeper than the parser follows") from None


def read_bounded(root: Path, relative: str, limit: int) -> bytes:
    """Read one input under root exactly once, through one handle, at most
    limit bytes. Refuses a path that resolves outside root (a symlink), a
    non-regular file, and a file over the limit."""
    root = root.resolve()
    try:
        resolved = (root / relative).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):  # RuntimeError: symlink loop on Python < 3.13
        raise InputProblem("missing-source", f"{relative!r} not found under {display_path(root)}") from None
    if not resolved.is_relative_to(root):
        raise InputProblem("missing-source", f"{relative} resolves outside {display_path(root)}")
    try:
        with open(resolved, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InputProblem("missing-source", f"{relative} is not a regular file")
            data = handle.read(limit + 1)
    except (OSError, ValueError) as error:
        raise InputProblem("missing-source", f"{relative!r} cannot be read: {error}") from None
    if len(data) > limit:
        raise InputProblem("invalid-manifest", f"{relative} is larger than its {limit}-byte budget")
    return data


class DeclaringObject(dict):
    """A parsed JSON object that keeps the keys it saw more than once, so a
    repeated schema_version or result_id is refused instead of letting the
    last value win."""

    repeated: frozenset = frozenset()


def _keep_repeats(pairs: list[tuple[str, object]]) -> DeclaringObject:
    result = DeclaringObject()
    repeated = set()
    for key, value in pairs:
        if key in result:
            repeated.add(key)
        result[key] = value
    result.repeated = frozenset(repeated)
    return result


def parse_json_object(relative: str, data: bytes) -> DeclaringObject | None:
    """The file as a JSON object, or None when it is not one: not UTF-8, not
    JSON, or JSON whose top level is not an object. Those files make no schema
    or identity declaration. NaN and Infinity parse, as Python's json reads
    them, so they cannot turn a declaring file into an opaque one. A file
    nested too deeply to parse is refused, not treated as declaring nothing."""
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_keep_repeats)
    except RecursionError:
        raise InputProblem("schema-drift", f"{relative} nests too deeply to check its schema_version") from None
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def schema_drift(parsed: dict | None, expected: object, field: str) -> str | None:
    """A file that declares schema_version must declare `expected`. A declared
    null is a declaration that does not match, not an absent one."""
    if parsed is None or "schema_version" not in parsed:
        return None
    if "schema_version" in getattr(parsed, "repeated", ()):
        return "declares schema_version more than once"
    declared = parsed["schema_version"]
    if declared != expected:
        return f"declares schema_version {declared!r}, the manifest's {field} is {expected!r}"
    return None


def result_identity_problem(parsed: dict | None, claimed: object) -> str | None:
    """A present result_id must be a non-empty string equal to the claim. Only
    an absent key, or a file that is not a JSON object, narrows the guarantee
    to the bytes; that case returns None like a match does."""
    if parsed is None or "result_id" not in parsed:
        return None
    if "result_id" in getattr(parsed, "repeated", ()):
        return "the result file declares result_id more than once"
    embedded = parsed["result_id"]
    if not is_nonblank_string(embedded):
        return f"the result file embeds a malformed result_id {embedded!r}; a present result_id must be a non-empty string"
    if embedded != claimed:
        return f"the result file embeds result_id {embedded!r}, the manifest claims {claimed!r}"
    return None


def file_record_problems(field: str, record: object) -> list[str]:
    """Check one {path, sha256, bytes} record (source refs and packaged files)."""
    problems: list[str] = []
    if not isinstance(record, dict):
        return [f"{field}: expected an object, got {type(record).__name__}"]
    if not is_relative_path(record.get("path")):
        problems.append(f"{field}.path: not a relative POSIX path under its root: {record.get('path')!r}")
    if not is_sha256(record.get("sha256")):
        problems.append(f"{field}.sha256: expected 64 lowercase hex digits, got {record.get('sha256')!r}")
    size = record.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        problems.append(f"{field}.bytes: expected a positive integer, got {size!r}")
    return problems


def _timestamp_problems(label: str, value: object) -> list[str]:
    """Strict zero-padded `YYYY-MM-DDTHH:MM:SSZ` and a real calendar date."""
    if isinstance(value, str) and _STRICT_TIMESTAMP_RE.match(value):
        try:
            datetime.datetime.strptime(value, _TIMESTAMP_FORMAT)
            return []
        except ValueError:
            return [f"{label}: not a real UTC date: {value!r}"]
    return [f"{label}: expected strict zero-padded {_TIMESTAMP_FORMAT} UTC form, got {value!r}"]


def _missing_fields_problems(value: object) -> list[str]:
    """Every gap is named with a non-empty field and detail; null is not an empty list."""
    problems: list[str] = []
    if isinstance(value, list):
        for index, entry in enumerate(value):
            if not isinstance(entry, dict) or not is_nonblank_string(entry.get("field")):
                problems.append(f"missing_fields[{index}]: expected an object with a non-empty field name")
            elif not is_nonblank_string(entry.get("detail")):
                problems.append(f"missing_fields[{index}].detail: expected a non-empty string explaining the gap")
    else:
        problems.append(f"missing_fields: expected a list (possibly empty), got {value!r}")
    return problems


def validate_manifest(manifest: object) -> list[str]:
    """Return every SPEC.md violation in the manifest, or an empty list if it conforms.

    A required key that is present with a null value is checked like any
    other value. Only an absent key skips its own checks, because the absence
    is already reported."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest: expected a JSON object"]

    known = set(REQUIRED_TOP_LEVEL)
    present = set(manifest)
    for key in sorted(known - present):
        problems.append(f"{key}: required field is missing")
    for key in sorted(present - known):
        problems.append(f"{key}: unknown top-level field")

    if "evidence_pack_format_version" in manifest and manifest["evidence_pack_format_version"] != PACK_FORMAT_VERSION:
        problems.append(f"evidence_pack_format_version: expected {PACK_FORMAT_VERSION!r}, got {manifest['evidence_pack_format_version']!r}")

    for key in ("capture_schema_version", "result_schema_version"):
        if key in manifest and not is_nonblank_string(manifest[key]):
            problems.append(f"{key}: expected a non-empty string, got {manifest[key]!r}")

    if "capture" in manifest:
        problems.extend(_capture_problems(manifest["capture"]))
    if "result" in manifest:
        problems.extend(_result_problems(manifest["result"]))
    if "missing_fields" in manifest:
        problems.extend(_missing_fields_problems(manifest["missing_fields"]))

    if "reproduction" in manifest:
        reproduction = manifest["reproduction"]
        if not isinstance(reproduction, dict):
            problems.append(f"reproduction: expected an object with a command field, got {reproduction!r}")
        elif not is_nonblank_string(reproduction.get("command")):
            problems.append("reproduction.command: expected a non-empty command string")

    if "geometry_correctness" in manifest:
        problems.extend(_correctness_problems(manifest["geometry_correctness"]))

    if "packaged_files" in manifest:
        packaged = manifest["packaged_files"]
        if not isinstance(packaged, list):
            problems.append(f"packaged_files: expected a list of file records, got {packaged!r}")
        elif not packaged:
            problems.append("packaged_files: expected at least one record")
        else:
            for index, record in enumerate(packaged):
                problems.extend(file_record_problems(f"packaged_files[{index}]", record))

    return problems


def _capture_problems(capture: object) -> list[str]:
    if not isinstance(capture, dict):
        return [f"capture: expected an object, got {capture!r}"]
    problems: list[str] = []
    if not is_nonblank_string(capture.get("capture_id")):
        problems.append("capture.capture_id: expected a non-empty string")
    problems.extend(_timestamp_problems("capture.captured_at", capture.get("captured_at")))
    if not is_relative_path(capture.get("capture_root")):
        problems.append(f"capture.capture_root: not a relative POSIX path: {capture.get('capture_root')!r}")
    refs = capture.get("source_refs")
    if isinstance(refs, list):
        if not refs:
            problems.append("capture.source_refs: expected at least one source ref")
        for index, ref in enumerate(refs):
            if isinstance(ref, dict) and not is_nonblank_string(ref.get("role")):
                problems.append(f"capture.source_refs[{index}].role: expected a non-empty string")
            problems.extend(file_record_problems(f"capture.source_refs[{index}]", ref))
    else:
        problems.append("capture.source_refs: expected a non-empty list of source refs")
    return problems


def _result_problems(result: object) -> list[str]:
    if not isinstance(result, dict):
        return [f"result: expected an object, got {result!r}"]
    problems: list[str] = []
    if not is_nonblank_string(result.get("result_id")):
        problems.append(f"result.result_id: expected a non-empty string, got {result.get('result_id')!r}")
    problems.extend(file_record_problems("result", result))
    return problems


def _correctness_problems(slot: object) -> list[str]:
    if not isinstance(slot, dict):
        return ["geometry_correctness: expected an object with assertion and note"]
    problems: list[str] = []
    if set(slot) != {"assertion", "note"}:
        problems.append("geometry_correctness: expected exactly assertion and note")
    if "assertion" in slot and slot["assertion"] is not None:
        problems.append(
            "geometry_correctness.assertion: must be null; a non-null assertion claims geometry "
            "correctness, which this identity-only format forbids (SPEC.md, Identity is not correctness)"
        )
    if not is_nonblank_string(slot.get("note")):
        problems.append("geometry_correctness.note: expected a non-empty string")
    return problems


def check_sources(manifest: dict, root: Path) -> list[str]:
    """Satisfiability: every source ref and the result must exist under root
    with the recorded hash and size. Capture inputs that declare a
    schema_version must match capture_schema_version; a result that declares
    one must match result_schema_version, and a result that embeds result_id
    must name the claimed result. Empty list means the identity is satisfied."""
    problems: list[str] = []
    capture = manifest.get("capture", {})
    for index, ref in enumerate(capture.get("source_refs", [])):
        problems.extend(_satisfy_problems(f"source_refs[{index}]", ref, root, manifest.get("capture_schema_version"), "capture_schema_version"))
    result = manifest.get("result", {})
    if isinstance(result, dict) and result:
        problems.extend(_satisfy_problems("result", result, root, manifest.get("result_schema_version"), "result_schema_version"))
    return problems


def _satisfy_problems(label: str, record: dict, root: Path, schema_version: object, field: str) -> list[str]:
    try:
        data = read_bounded(root, record.get("path", ""), MAX_REFERENCED_FILE_BYTES)
    except InputProblem as problem:
        return [f"{label}: {problem.kind}: {problem.detail}"]
    return snapshot_problems(label, record, data, schema_version, field)


def snapshot_problems(label: str, record: dict, data: bytes, schema_version: object, field: str) -> list[str]:
    """Check bytes already read once against their record: hash, size, the
    declared schema_version, and for the result its embedded result_id."""
    digest = hashlib.sha256(data).hexdigest()
    mismatches = []
    if digest != record.get("sha256"):
        mismatches.append(f"hash {digest} != recorded {record.get('sha256')}")
    if len(data) != record.get("bytes"):
        mismatches.append(f"size {len(data)} != recorded {record.get('bytes')}")
    if mismatches:
        return [f"{label}: {record.get('path')!r} does not satisfy its recorded identity: " + "; ".join(mismatches)]
    try:
        parsed = parse_json_object(record.get("path", ""), data)
    except InputProblem as problem:
        return [f"{label}: {problem.kind}: {problem.detail}"]
    drift = schema_drift(parsed, schema_version, field)
    if drift:
        return [f"{label}: schema-drift: {record.get('path')!r} {drift}"]
    if label == "result":
        problem = result_identity_problem(parsed, record.get("result_id"))
        if problem:
            return [f"result: mismatched-result: {problem}"]
    return []


def validate_pack_spec(spec: object) -> list[str]:
    """Validate the flat pack-spec.json a fixture carries before the packer
    builds anything from it. Same rules as validate_manifest, applied to the
    packer's input shape, so a malformed fixture is refused by name instead of
    crashing the packer with a KeyError."""
    if not isinstance(spec, dict):
        return ["pack-spec: expected a JSON object"]
    problems: list[str] = []
    for key in PACK_SPEC_REQUIRED:
        if key not in spec:
            problems.append(f"{key}: required pack-spec field is missing")
    for key in sorted(set(spec) - set(PACK_SPEC_REQUIRED) - set(PACK_SPEC_OPTIONAL)):
        problems.append(f"{key}: unknown pack-spec field")
    if not is_nonblank_string(spec.get("capture_id")):
        problems.append("capture_id: expected a non-empty string")
    problems.extend(_timestamp_problems("captured_at", spec.get("captured_at")))
    for key in ("capture_schema_version", "result_schema_version", "result_id"):
        if not is_nonblank_string(spec.get(key)):
            problems.append(f"{key}: expected a non-empty string, got {spec.get(key)!r}")
    if not is_relative_path(spec.get("result_path")):
        problems.append(f"result_path: not a relative POSIX path: {spec.get('result_path')!r}")
    # The archive entry is always report.md (SPEC.md, Pack layout), and the
    # packer reads it from the fixture root under that name. Any other value
    # would produce a pack the reader refuses, so the packer refuses it first.
    if "report_path" in spec and spec["report_path"] != REPORT_ENTRY:
        problems.append(f"report_path: the report is always {REPORT_ENTRY} at the fixture root, got {spec['report_path']!r}")
    refs = spec.get("source_refs")
    if isinstance(refs, list) and refs:
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                problems.append(f"source_refs[{index}]: expected an object with role and path")
                continue
            if not is_nonblank_string(ref.get("role")):
                problems.append(f"source_refs[{index}].role: expected a non-empty string")
            if not is_relative_path(ref.get("path")):
                problems.append(f"source_refs[{index}].path: not a relative POSIX path: {ref.get('path')!r}")
            for key in sorted(set(ref) - {"role", "path"}):
                problems.append(f"source_refs[{index}].{key}: unknown pack-spec field")
    else:
        problems.append("source_refs: expected a non-empty list of source refs")
    problems.extend(_missing_fields_problems(spec.get("missing_fields")))
    return problems
