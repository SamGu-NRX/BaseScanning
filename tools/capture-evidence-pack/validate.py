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

    missing_fields = manifest.get("missing_fields")
    if isinstance(missing_fields, list):
        for index, entry in enumerate(missing_fields):
            if not isinstance(entry, dict) or not isinstance(entry.get("field"), str) or not entry["field"].strip():
                problems.append(f"missing_fields[{index}]: expected an object with a non-empty field name")
    elif missing_fields is not None:
        problems.append("missing_fields: expected a list (possibly empty)")

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
    captured_at = capture.get("captured_at")
    if isinstance(captured_at, str):
        try:
            datetime.datetime.strptime(captured_at, _TIMESTAMP_FORMAT)
        except ValueError:
            problems.append(f"capture.captured_at: expected {_TIMESTAMP_FORMAT} UTC form, got {captured_at!r}")
    else:
        problems.append("capture.captured_at: expected a timestamp string")
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
    with the recorded hash and size. Empty list means the identity is satisfied."""
    problems: list[str] = []
    capture = manifest.get("capture", {})
    for index, ref in enumerate(capture.get("source_refs", [])):
        problems.extend(_satisfy_problems(f"source_refs[{index}]", ref, root))
    result = manifest.get("result", {})
    if isinstance(result, dict) and result:
        problems.extend(_satisfy_problems("result", result, root))
    return problems


def _satisfy_problems(label: str, record: dict, root: Path) -> list[str]:
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
    return []


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain a JSON object")
    return manifest
