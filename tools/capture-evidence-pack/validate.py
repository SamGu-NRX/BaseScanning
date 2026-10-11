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
import re
from pathlib import Path

PACK_FORMAT_VERSION = "1.0"

REQUIRED_TOP_LEVEL = (
    "evidence_pack_format_version",
    "capture_schema_version",
    "capture",
    "result",
    "missing_fields",
    "reproduction",
    "geometry_correctness",
    "packaged_files",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_STRICT_TIMESTAMP_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T[0-2]\d:[0-5]\d:[0-5]\dZ$")


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.match(value) is not None


def is_relative_path(value: object) -> bool:
    """A pack path is relative, POSIX-style, and cannot escape its root."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or value in (".", ".."):
        return False
    parts = value.split("/")
    return ".." not in parts and all(part not in ("", ".") for part in parts)


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
            if not isinstance(entry, dict) or not isinstance(entry.get("field"), str) or not entry["field"].strip():
                problems.append(f"missing_fields[{index}]: expected an object with a non-empty field name")
            elif not isinstance(entry.get("detail"), str) or not entry["detail"].strip():
                problems.append(f"missing_fields[{index}].detail: expected a non-empty string explaining the gap")
    else:
        problems.append(f"missing_fields: expected a list (possibly empty), got {value!r}")
    return problems


def validate_manifest(manifest: object) -> list[str]:
    """Return every SPEC.md violation in the manifest, or an empty list if it conforms."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest: expected a JSON object"]

    known = set(REQUIRED_TOP_LEVEL)
    present = set(manifest)
    for key in sorted(known - present):
        problems.append(f"{key}: required field is missing")
    for key in sorted(present - known):
        problems.append(f"{key}: unknown top-level field")

    version = manifest.get("evidence_pack_format_version")
    if version is not None and version != PACK_FORMAT_VERSION:
        problems.append(f"evidence_pack_format_version: expected {PACK_FORMAT_VERSION!r}, got {version!r}")

    schema_version = manifest.get("capture_schema_version")
    if schema_version is not None and (not isinstance(schema_version, str) or not schema_version.strip()):
        problems.append("capture_schema_version: expected a non-empty string")

    problems.extend(_capture_problems(manifest.get("capture")))
    problems.extend(_result_problems(manifest.get("result")))

    problems.extend(_missing_fields_problems(manifest.get("missing_fields")))

    reproduction = manifest.get("reproduction")
    if isinstance(reproduction, dict):
        command = reproduction.get("command")
        if not isinstance(command, str) or not command.strip():
            problems.append("reproduction.command: expected a non-empty command string")
    elif reproduction is not None:
        problems.append("reproduction: expected an object with a command field")

    problems.extend(_correctness_problems(manifest.get("geometry_correctness")))

    packaged = manifest.get("packaged_files")
    if isinstance(packaged, list):
        if not packaged:
            problems.append("packaged_files: expected at least one record")
        for index, record in enumerate(packaged):
            problems.extend(file_record_problems(f"packaged_files[{index}]", record))
    elif packaged is not None:
        problems.append("packaged_files: expected a list of file records")

    return problems


def _capture_problems(capture: object) -> list[str]:
    if not isinstance(capture, dict):
        return ["capture: expected an object"]
    problems: list[str] = []
    capture_id = capture.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id.strip():
        problems.append("capture.capture_id: expected a non-empty string")
    problems.extend(_timestamp_problems("capture.captured_at", capture.get("captured_at")))
    if not is_relative_path(capture.get("capture_root")):
        problems.append(f"capture.capture_root: not a relative POSIX path: {capture.get('capture_root')!r}")
    refs = capture.get("source_refs")
    if isinstance(refs, list):
        if not refs:
            problems.append("capture.source_refs: expected at least one source ref")
        for index, ref in enumerate(refs):
            if isinstance(ref, dict):
                role = ref.get("role")
                if not isinstance(role, str) or not role.strip():
                    problems.append(f"capture.source_refs[{index}].role: expected a non-empty string")
            problems.extend(file_record_problems(f"capture.source_refs[{index}]", ref))
    else:
        problems.append("capture.source_refs: expected a non-empty list of source refs")
    return problems


def _result_problems(result: object) -> list[str]:
    if not isinstance(result, dict):
        return ["result: expected an object"]
    problems: list[str] = []
    result_id = result.get("result_id")
    if not isinstance(result_id, str) or not result_id.strip():
        problems.append("result.result_id: expected a non-empty string")
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
    note = slot.get("note")
    if not isinstance(note, str) or not note.strip():
        problems.append("geometry_correctness.note: expected a non-empty string")
    return problems


def check_sources(manifest: dict, root: Path) -> list[str]:
    """Satisfiability: every source ref and the result must exist under root
    with the recorded hash and size, and any embedded schema_version must match
    the manifest's capture_schema_version. Empty list means the identity is satisfied."""
    problems: list[str] = []
    capture = manifest.get("capture", {})
    schema_version = manifest.get("capture_schema_version")
    for index, ref in enumerate(capture.get("source_refs", [])):
        problems.extend(_satisfy_problems(f"source_refs[{index}]", ref, root, schema_version))
    result = manifest.get("result", {})
    if isinstance(result, dict) and result:
        problems.extend(_satisfy_problems("result", result, root, schema_version))
    return problems


def _satisfy_problems(label: str, record: dict, root: Path, schema_version: object) -> list[str]:
    path = root / record.get("path", "")
    if not path.is_file():
        return [f"{label}: missing-source: {record.get('path')!r} not found under {root}"]
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    size = len(data)
    mismatches = []
    if digest != record.get("sha256"):
        mismatches.append(f"hash {digest} != recorded {record.get('sha256')}")
    if size != record.get("bytes"):
        mismatches.append(f"size {size} != recorded {record.get('bytes')}")
    if mismatches:
        return [f"{label}: {record.get('path')!r} does not satisfy its recorded identity: " + "; ".join(mismatches)]
    drift = _schema_drift(path, data, schema_version)
    if drift:
        return [f"{label}: schema-drift: {record.get('path')!r} {drift}"]
    return []


def _schema_drift(path: Path, data: bytes, expected: object) -> str | None:
    try:
        parsed = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or "schema_version" not in parsed:
        return None
    declared = parsed["schema_version"]
    if declared != expected:
        return f"declares schema_version {declared!r}, the manifest records {expected!r}"
    return None


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain a JSON object")
    return manifest


def validate_pack_spec(spec: object) -> list[str]:
    """Validate the flat pack-spec.json a fixture carries before the packer
    builds anything from it. Same rules as validate_manifest, applied to the
    packer's input shape, so a malformed fixture is refused by name instead of
    crashing the packer with a KeyError."""
    if not isinstance(spec, dict):
        return ["pack-spec: expected a JSON object"]
    problems: list[str] = []
    for key in ("capture_id", "captured_at", "capture_schema_version", "result_id", "source_refs", "result_path", "missing_fields"):
        if key not in spec:
            problems.append(f"{key}: required pack-spec field is missing")
    capture_id = spec.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id.strip():
        problems.append("capture_id: expected a non-empty string")
    problems.extend(_timestamp_problems("captured_at", spec.get("captured_at")))
    schema_version = spec.get("capture_schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        problems.append("capture_schema_version: expected a non-empty string")
    result_id = spec.get("result_id")
    if not isinstance(result_id, str) or not result_id.strip():
        problems.append("result_id: expected a non-empty string")
    result_path = spec.get("result_path")
    if not is_relative_path(result_path):
        problems.append(f"result_path: not a relative POSIX path: {result_path!r}")
    report_path = spec.get("report_path", "report.md")
    if not is_relative_path(report_path):
        problems.append(f"report_path: not a relative POSIX path: {report_path!r}")
    refs = spec.get("source_refs")
    if isinstance(refs, list) and refs:
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                problems.append(f"source_refs[{index}]: expected an object with role and path")
                continue
            role = ref.get("role")
            if not isinstance(role, str) or not role.strip():
                problems.append(f"source_refs[{index}].role: expected a non-empty string")
            if not is_relative_path(ref.get("path")):
                problems.append(f"source_refs[{index}].path: not a relative POSIX path: {ref.get('path')!r}")
    else:
        problems.append("source_refs: expected a non-empty list of source refs")
    problems.extend(_missing_fields_problems(spec.get("missing_fields")))
    return problems
