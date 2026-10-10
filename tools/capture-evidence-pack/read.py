#!/usr/bin/env python3
"""Standalone reader for capture evidence packs.

Deliberately independent of pack.py and validate.py: it imports neither, and
shares no validation code with them. Every rule here is re-derived from
SPEC.md and the zip itself, so a shared bug cannot hide on both sides of the
format.

Usage:
    python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip
    python tools/capture-evidence-pack/read.py pack.zip --capture-root /elsewhere/capture-root
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Re-derived from SPEC.md. If SPEC.md changes, change these here by hand.
SUPPORTED_PACK_FORMAT = "1.0"
REQUIRED_FIELDS = (
    "evidence_pack_format_version",
    "capture_schema_version",
    "capture",
    "result",
    "missing_fields",
    "reproduction",
    "geometry_correctness",
    "packaged_files",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Refusal(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and HEX64.match(value) is not None


def is_relative_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and value
        and not value.startswith("/")
        and "\\" not in value
        and value not in (".", "..")
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def check_record(label: str, record: object) -> None:
    if not isinstance(record, dict):
        raise Refusal("invalid-manifest", f"{label} is not an object")
    if not is_relative_path(record.get("path")):
        raise Refusal("invalid-manifest", f"{label}.path is not a relative POSIX path: {record.get('path')!r}")
    if not is_sha256(record.get("sha256")):
        raise Refusal("invalid-manifest", f"{label}.sha256 is not 64 lowercase hex digits")
    size = record.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise Refusal("invalid-manifest", f"{label}.bytes is not a positive integer: {size!r}")


def load_pack(pack_path: Path) -> tuple[dict, dict[str, bytes]]:
    try:
        with zipfile.ZipFile(pack_path) as archive:
            names = sorted(archive.namelist())
            expected = ["manifest.json", "report.md"]
            if names != expected:
                raise Refusal("invalid-manifest", f"pack entries are {names}, a conforming pack carries exactly {expected}")
            data = {name: archive.read(name) for name in names}
    except Refusal:
        raise
    except zipfile.BadZipFile:
        raise Refusal("invalid-manifest", f"{pack_path} is not a readable zip archive") from None
    try:
        manifest = json.loads(data["manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal("invalid-manifest", f"manifest.json is not readable JSON: {error}") from None
    if not isinstance(manifest, dict):
        raise Refusal("invalid-manifest", "manifest.json must contain a JSON object")
    return manifest, data


def validate_manifest(manifest: dict) -> None:
    for key in REQUIRED_FIELDS:
        if key not in manifest:
            raise Refusal("invalid-manifest", f"required field {key} is missing")
    for key in manifest:
        if key not in REQUIRED_FIELDS:
            raise Refusal("invalid-manifest", f"unknown top-level field {key}")

    version = manifest["evidence_pack_format_version"]
    if version != SUPPORTED_PACK_FORMAT:
        raise Refusal("wrong-pack-format", f"evidence_pack_format_version is {version!r}, this reader supports {SUPPORTED_PACK_FORMAT!r}")

    if not isinstance(manifest["capture_schema_version"], str) or not manifest["capture_schema_version"].strip():
        raise Refusal("invalid-manifest", "capture_schema_version must be a non-empty string")

    capture = manifest["capture"]
    if not isinstance(capture, dict):
        raise Refusal("invalid-manifest", "capture must be an object")
    if not isinstance(capture.get("capture_id"), str) or not capture["capture_id"].strip():
        raise Refusal("invalid-manifest", "capture.capture_id must be a non-empty string")
    if not is_relative_path(capture.get("capture_root")):
        raise Refusal("invalid-manifest", f"capture.capture_root is not a relative POSIX path: {capture.get('capture_root')!r}")
    refs = capture.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise Refusal("invalid-manifest", "capture.source_refs must be a non-empty list")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or not isinstance(ref.get("role"), str) or not ref["role"].strip():
            raise Refusal("invalid-manifest", f"capture.source_refs[{index}].role must be a non-empty string")
        check_record(f"capture.source_refs[{index}]", ref)

    result = manifest["result"]
    if not isinstance(result, dict):
        raise Refusal("invalid-manifest", "result must be an object")
    if not isinstance(result.get("result_id"), str) or not result["result_id"].strip():
        raise Refusal("invalid-manifest", "result.result_id must be a non-empty string")
    check_record("result", result)

    missing_fields = manifest["missing_fields"]
    if not isinstance(missing_fields, list):
        raise Refusal("invalid-manifest", "missing_fields must be a list (possibly empty)")
    for index, entry in enumerate(missing_fields):
        if not isinstance(entry, dict) or not isinstance(entry.get("field"), str) or not entry["field"].strip():
            raise Refusal("invalid-manifest", f"missing_fields[{index}] needs a non-empty field name")

    reproduction = manifest["reproduction"]
    if not isinstance(reproduction, dict) or not isinstance(reproduction.get("command"), str) or not reproduction["command"].strip():
        raise Refusal("invalid-manifest", "reproduction.command must be a non-empty command string")

    slot = manifest["geometry_correctness"]
    if not isinstance(slot, dict) or set(slot) != {"assertion", "note"}:
        raise Refusal("invalid-manifest", "geometry_correctness must have exactly assertion and note")
    if slot["assertion"] is not None:
        raise Refusal(
            "invalid-manifest",
            "geometry_correctness.assertion must be null; a non-null assertion claims geometry "
            "correctness, which this identity-only format forbids (SPEC.md, Identity is not correctness)",
        )
    if not isinstance(slot["note"], str) or not slot["note"].strip():
        raise Refusal("invalid-manifest", "geometry_correctness.note must be a non-empty string")

    packaged = manifest["packaged_files"]
    if not isinstance(packaged, list) or not packaged:
        raise Refusal("invalid-manifest", "packaged_files must be a non-empty list")
    for index, record in enumerate(packaged):
        check_record(f"packaged_files[{index}]", record)


def check_packaged_files(manifest: dict, entries: dict[str, bytes]) -> None:
    recorded = {record["path"]: record for record in manifest["packaged_files"]}
    entry_names = set(entries) - {"manifest.json"}
    if set(recorded) != entry_names:
        raise Refusal(
            "invalid-manifest",
            f"packaged_files covers {sorted(recorded)}, the pack carries {sorted(entry_names)}",
        )
    for path, record in sorted(recorded.items()):
        data = entries[path]
        if sha256_hex(data) != record["sha256"] or len(data) != record["bytes"]:
            raise Refusal(
                "invalid-manifest",
                f"packaged file {path} does not match its recorded hash and size",
            )


def satisfy(label: str, record: dict, root: Path, schema_version: str, mismatch_kind: str) -> str:
    path = root / record["path"]
    if not path.is_file():
        raise Refusal("missing-source", f"{record['path']!r} not found under {root}")
    data = path.read_bytes()
    digest, size = sha256_hex(data), len(data)
    if digest != record["sha256"] or size != record["bytes"]:
        raise Refusal(
            mismatch_kind,
            f"{record['path']!r} does not satisfy its recorded identity: "
            f"hash {digest} vs recorded {record['sha256']}, size {size} vs recorded {record['bytes']}",
        )
    try:
        parsed = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict) and "schema_version" in parsed and parsed["schema_version"] != schema_version:
        raise Refusal(
            "schema-drift",
            f"{record['path']!r} declares schema_version {parsed['schema_version']!r}, "
            f"the manifest records {schema_version!r}",
        )
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pack", type=Path, help="path to the evidence pack zip")
    parser.add_argument("--capture-root", type=Path, default=None, help="resolve source refs against this directory instead of the recorded capture_root")
    args = parser.parse_args()

    pack_path = args.pack.resolve()
    if not pack_path.is_file():
        raise Refusal("missing-source", f"pack {pack_path} does not exist")

    manifest, entries = load_pack(pack_path)
    validate_manifest(manifest)
    check_packaged_files(manifest, entries)

    schema_version = manifest["capture_schema_version"]
    capture = manifest["capture"]
    root = (args.capture_root or REPO_ROOT / capture["capture_root"]).resolve()

    checked = []
    for ref in capture["source_refs"]:
        digest = satisfy(f"source ref {ref['path']}", ref, root, schema_version, mismatch_kind="missing-source")
        checked.append(f"{ref['role']}:{ref['path']} sha256 {digest}")
    result = manifest["result"]
    result_digest = satisfy(f"result {result['path']}", result, root, schema_version, mismatch_kind="mismatched-result")

    print("capture-evidence-pack reader")
    print(f"pack: {pack_path}")
    print(f"pack sha256: {sha256_hex(pack_path.read_bytes())}")
    print(f"manifest sha256: {sha256_hex(entries['manifest.json'])}")
    print(f"evidence_pack_format_version: {manifest['evidence_pack_format_version']}")
    print(f"capture_schema_version: {schema_version}")
    print(f"capture: {capture['capture_id']} captured_at {capture['captured_at']}")
    for line in checked:
        print(f"source ref ok: {line}")
    print(f"result ok: {result['result_id']} {result['path']} sha256 {result_digest}")
    print(f"missing fields (explicit): {len(manifest['missing_fields'])}")
    for entry in manifest["missing_fields"]:
        print(f"  missing: {entry['field']} ({entry['detail']})")
    print(f"geometry_correctness: assertion null, no claim made ({manifest['geometry_correctness']['note']})")
    print(f"reproduce: {manifest['reproduction']['command']}")
    print("verdict: PASS")


if __name__ == "__main__":
    try:
        main()
    except Refusal as refusal:
        print(f"REFUSAL {refusal.kind}: {refusal.detail}")
        print("verdict: REFUSED")
        sys.exit(2)
