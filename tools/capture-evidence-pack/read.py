#!/usr/bin/env python3
"""Standalone reader for capture evidence packs.

Deliberately independent of pack.py and validate.py: it imports neither, and
shares no validation code with them. Every rule here is re-derived from
SPEC.md and the zip itself, so a shared bug cannot hide on both sides of the
format. The inspection server imports this module and calls verify_pack, so
the page shows only what this reader checked.

Usage:
    python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip
    python tools/capture-evidence-pack/read.py pack.zip --capture-root /elsewhere/capture-root
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import math
import os
import re
import shlex
import stat
import sys
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Re-derived from SPEC.md. If SPEC.md changes, change these here by hand.
SUPPORTED_PACK_FORMAT = "1.0"
REQUIRED_FIELDS = (
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
PACK_ENTRIES = ("manifest.json", "report.md")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
# Not allowed in pack paths: C0 controls, DEL, C1 controls, and surrogate
# code points. NUL and a lone surrogate make the OS path calls raise; the
# controls cannot be shown faithfully in a receipt.
UNSAFE_PATH_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
# A str holds a surrogate code point only when it is unpaired: json combines
# a valid escaped pair into one character. Unpaired ones are not text.
SURROGATE = re.compile(r"[\ud800-\udfff]")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STRICT_TIMESTAMP = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T[0-2]\d:[0-5]\d:[0-5]\dZ$")

# Resource bounds (SPEC.md, Archive limits). They are not calibrated against
# real packs; none exist yet. A pack holds one manifest and one text report
# (the committed example is 1353 bytes), so these leave three orders of
# magnitude of headroom while keeping a hostile archive or reference from
# forcing an unbounded read, an unbounded decompression, or a scan of
# thousands of entries. Every read below goes through a cap of limit + 1
# bytes, so a size field that lies cannot widen it.
MAX_PACK_BYTES = 8 * 1024 * 1024
MAX_ENTRY_BYTES = {"manifest.json": 1024 * 1024, "report.md": 2 * 1024 * 1024}
# Deflate cannot exceed about 1032:1. Text manifests and reports compress
# below 10:1; a ratio past 100:1 means the entry is built to expand.
MAX_COMPRESSION_RATIO = 100
ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# Capture inputs and results are referenced, not packed, and are read whole
# to hash them. 64 MiB covers the synthetic fixtures by a wide margin; a pack
# about larger inputs needs a reader with a streaming hash.
MAX_REFERENCED_FILE_BYTES = 64 * 1024 * 1024


class Refusal(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class _StrictObject(dict):
    """A JSON object that remembers keys it saw more than once."""

    duplicates: tuple[str, ...] = ()


def _strict_pairs(pairs: list[tuple[str, object]]) -> _StrictObject:
    result = _StrictObject()
    seen: list[str] = []
    for key, value in pairs:
        if key in result:
            seen.append(key)
        result[key] = value
    result.duplicates = tuple(seen)
    return result


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-standard JSON constant {name}")


class NotFinite(ValueError):
    """A number in manifest.json that Python's json would read as infinity."""


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise NotFinite(f"manifest.json contains a number that is not finite: {text[:40]}")
    return value


class TooDeep(ValueError):
    """JSON nested deeper than Python's parser follows. Kept distinct from
    other parse failures so a referenced file that is too deep to inspect is
    refused rather than treated as making no declaration."""


def parse_json(data: bytes, allow_constants: bool = False) -> object:
    """UTF-8 JSON whose objects record duplicate keys. The manifest refuses
    NaN, Infinity and numbers too large to be finite (1e400); referenced
    files allow them (allow_constants), because a file Python's json reads as
    an object must not count as opaque."""
    constant_hook = None if allow_constants else _reject_constant
    float_hook = None if allow_constants else _finite_float
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_strict_pairs, parse_constant=constant_hook, parse_float=float_hook
        )
    except RecursionError:
        raise TooDeep("JSON nests deeper than the parser follows") from None


def _first_unpaired_surrogate(value: object) -> str | None:
    """The first unpaired surrogate in any key or string of a parsed manifest,
    as U+XXXX, or None. Iterative, like _first_duplicate."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            found = SURROGATE.search(current)
            if found:
                return f"U+{ord(found.group()):04X}"
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def _first_duplicate(value: object) -> str | None:
    """The first duplicated key anywhere in a parsed manifest, or None.
    Iterative, so a deep but parseable manifest cannot exhaust the stack."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, _StrictObject):
            if current.duplicates:
                return current.duplicates[0]
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_path(path: Path) -> str:
    """Repository-relative when the path lies inside the repository, so receipts
    do not carry a reader's home directory."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and HEX64.match(value) is not None


def is_relative_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith("/")
        and "\\" not in value
        and not UNSAFE_PATH_CHARACTERS.search(value)
        and value not in (".", "..")
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# Opening without blocking lets the reader check the file type before it can
# wait: a plain open() of a FIFO with no writer never returns. O_NONBLOCK has
# no effect on reads from a regular file, the only kind read here.
_NONBLOCK = getattr(os, "O_NONBLOCK", None)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOCTTY", 0)


def read_regular_file(path: Path, limit: int, label: str) -> bytes:
    """Read at most limit + 1 bytes from a regular file through one
    descriptor. The caller compares the length against the limit.

    The descriptor is opened without blocking and fstat on it decides the
    type before any read, so a FIFO, device or directory is refused at once
    and there is no gap between the check and the read. Every refusal closes
    the descriptor. Messages name the file by label, the path as the caller
    declared it, and give only the OS's error category: an OSError's text
    carries the absolute path it opened."""
    if _NONBLOCK is None:
        raise Refusal("missing-source", f"{label} cannot be read: this platform cannot open a file without blocking")
    try:
        descriptor = os.open(path, _READ_FLAGS | _NONBLOCK)
    except OSError as error:
        raise Refusal("missing-source", f"{label} cannot be read: {error.strerror or type(error).__name__}") from None
    except ValueError:
        raise Refusal("missing-source", f"{label} cannot be read: the path cannot be passed to the operating system") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise Refusal("missing-source", f"{label} is not a regular file")
        handle = os.fdopen(descriptor, "rb")
        descriptor = None  # the handle owns it now
        with handle:
            return handle.read(limit + 1)
    except OSError as error:
        raise Refusal("missing-source", f"{label} cannot be read: {error.strerror or type(error).__name__}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_pack_file(pack_path: Path, label: str) -> bytes:
    """The one read of the pack. Everything the reader reports about the pack,
    including the SHA-256 printed on refusal, comes from these bytes.

    There is no separate exists or is-file check first: such a check is a
    second filesystem call that can raise on its own (EACCES on a locked
    parent directory) outside the read's error handling, and it races the
    open. The open reports a missing or unreachable pack, and fstat on the
    open handle proves it is a regular file. label names the pack in
    messages as the caller gave it."""
    data = read_regular_file(pack_path, MAX_PACK_BYTES, label)
    if len(data) > MAX_PACK_BYTES:
        raise Refusal("invalid-archive", f"pack exceeds the {MAX_PACK_BYTES}-byte pack budget")
    return data


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


def _entry_problem(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if not is_relative_path(name):
        return f"entry name {name!r} is absolute, empty, or escapes the archive"
    if info.is_dir():
        return f"entry {name!r} is a directory"
    # Unix-made entries carry a file type in the high bits; zipfile's own
    # writestr records permission bits only, which means a plain file.
    file_type = stat.S_IFMT(info.external_attr >> 16)
    if info.create_system == 3 and file_type and file_type != stat.S_IFREG:
        return f"entry {name!r} is not a regular file (unix mode {oct(info.external_attr >> 16)})"
    if info.flag_bits & 0x1:
        return f"entry {name!r} is encrypted"
    if info.compress_type not in ALLOWED_COMPRESSION:
        return f"entry {name!r} uses compression method {info.compress_type}; only stored and deflated are read"
    limit = MAX_ENTRY_BYTES.get(name)
    if limit is not None and info.file_size > limit:
        return f"entry {name!r} declares {info.file_size} bytes, over its {limit}-byte budget"
    if info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        return (
            f"entry {name!r} expands from {info.compress_size} to {info.file_size} bytes, "
            f"over the {MAX_COMPRESSION_RATIO}:1 compression budget"
        )
    return None


def load_pack(pack_bytes: bytes) -> tuple[dict, dict[str, bytes]]:
    """Open the archive from the in-memory snapshot and decode its two entries
    within their budgets. Refuses before reading any entry when the central
    directory already breaks a rule."""
    if len(pack_bytes) > MAX_PACK_BYTES:
        raise Refusal("invalid-archive", f"pack exceeds the {MAX_PACK_BYTES}-byte pack budget")
    expected = list(PACK_ENTRIES)
    try:
        with zipfile.ZipFile(io.BytesIO(pack_bytes)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) != len(expected):
                # Names can be 64 KiB each; show a bounded sample.
                shown = [name[:80] for name in names[:10]] + (["..."] if len(names) > 10 else [])
                raise Refusal(
                    "invalid-archive",
                    f"pack has {len(infos)} entries {shown}, a conforming pack carries exactly {expected}",
                )
            if len(set(names)) != len(names):
                raise Refusal("invalid-archive", f"pack repeats an entry name: {names}")
            for info in infos:
                problem = _entry_problem(info)
                if problem:
                    raise Refusal("invalid-archive", problem)
            if sorted(names) != expected:
                raise Refusal("invalid-archive", f"pack entries are {sorted(names)}, a conforming pack carries exactly {expected}")
            entries = {}
            for info in infos:
                limit = MAX_ENTRY_BYTES[info.filename]
                with archive.open(info) as handle:
                    data = handle.read(limit + 1)
                if len(data) > limit or len(data) != info.file_size:
                    raise Refusal(
                        "invalid-archive",
                        f"entry {info.filename!r} decoded to {len(data)} bytes against a declared "
                        f"{info.file_size} and a {limit}-byte budget",
                    )
                entries[info.filename] = data
    except Refusal:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError) as error:
        raise Refusal("invalid-archive", f"the pack is not a readable zip archive: {error}") from None

    try:
        manifest = parse_json(entries["manifest.json"])
    except NotFinite as error:
        raise Refusal("invalid-manifest", str(error)) from None
    except (UnicodeDecodeError, ValueError) as error:
        raise Refusal("invalid-manifest", f"manifest.json is not readable JSON: {error}") from None
    if not isinstance(manifest, dict):
        raise Refusal("invalid-manifest", "manifest.json must contain a JSON object")
    surrogate = _first_unpaired_surrogate(manifest)
    if surrogate is not None:
        raise Refusal("invalid-manifest", f"manifest.json contains an unpaired surrogate ({surrogate}), which is not text")
    duplicate = _first_duplicate(manifest)
    if duplicate is not None:
        raise Refusal("invalid-manifest", f"manifest.json repeats the key {duplicate!r}; a reader cannot tell which value counts")
    try:
        entries["report.md"].decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("invalid-archive", "report.md is not UTF-8 text") from None
    return manifest, entries


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

    for key in ("capture_schema_version", "result_schema_version"):
        if not is_nonblank_string(manifest[key]):
            raise Refusal("invalid-manifest", f"{key} must be a non-empty string, got {manifest[key]!r}")

    capture = manifest["capture"]
    if not isinstance(capture, dict):
        raise Refusal("invalid-manifest", "capture must be an object")
    if not is_nonblank_string(capture.get("capture_id")):
        raise Refusal("invalid-manifest", "capture.capture_id must be a non-empty string")
    captured_at = capture.get("captured_at")
    if not isinstance(captured_at, str) or not STRICT_TIMESTAMP.match(captured_at):
        raise Refusal("invalid-manifest", f"capture.captured_at is not a strict zero-padded {TIMESTAMP_FORMAT} UTC form: {captured_at!r}")
    try:
        datetime.datetime.strptime(captured_at, TIMESTAMP_FORMAT)
    except ValueError:
        raise Refusal("invalid-manifest", f"capture.captured_at is not a real UTC date: {captured_at!r}") from None
    if not is_relative_path(capture.get("capture_root")):
        raise Refusal("invalid-manifest", f"capture.capture_root is not a relative POSIX path: {capture.get('capture_root')!r}")
    refs = capture.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise Refusal("invalid-manifest", "capture.source_refs must be a non-empty list")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or not is_nonblank_string(ref.get("role")):
            raise Refusal("invalid-manifest", f"capture.source_refs[{index}].role must be a non-empty string")
        check_record(f"capture.source_refs[{index}]", ref)

    result = manifest["result"]
    if not isinstance(result, dict):
        raise Refusal("invalid-manifest", "result must be an object")
    if not is_nonblank_string(result.get("result_id")):
        raise Refusal("invalid-manifest", f"result.result_id must be a non-empty string, got {result.get('result_id')!r}")
    check_record("result", result)

    missing_fields = manifest["missing_fields"]
    if not isinstance(missing_fields, list):
        raise Refusal("invalid-manifest", "missing_fields must be a list (possibly empty)")
    for index, entry in enumerate(missing_fields):
        if not isinstance(entry, dict) or not is_nonblank_string(entry.get("field")):
            raise Refusal("invalid-manifest", f"missing_fields[{index}] needs a non-empty field name")
        if not is_nonblank_string(entry.get("detail")):
            raise Refusal("invalid-manifest", f"missing_fields[{index}].detail must be a non-empty string explaining the gap")

    reproduction = manifest["reproduction"]
    if not isinstance(reproduction, dict) or not is_nonblank_string(reproduction.get("command")):
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
    if not is_nonblank_string(slot["note"]):
        raise Refusal("invalid-manifest", "geometry_correctness.note must be a non-empty string")

    packaged = manifest["packaged_files"]
    if not isinstance(packaged, list) or not packaged:
        raise Refusal("invalid-manifest", "packaged_files must be a non-empty list")
    for index, record in enumerate(packaged):
        check_record(f"packaged_files[{index}]", record)


def check_packaged_files(manifest: dict, entries: dict[str, bytes]) -> None:
    records = manifest["packaged_files"]
    recorded = {record["path"]: record for record in records}
    if len(recorded) != len(records):
        raise Refusal("invalid-manifest", "packaged_files lists the same path more than once")
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
                f"packaged file {path} does not match its recorded hash and size: "
                f"hash {sha256_hex(data)} vs recorded {record['sha256']}, size {len(data)} vs recorded {record['bytes']}",
            )


def snapshot_reference(record: dict, root: Path, mismatch_kind: str) -> bytes:
    """Read one referenced file once and check it against its record. The
    returned bytes are the only ones the schema and identity checks see."""
    if record["bytes"] > MAX_REFERENCED_FILE_BYTES:
        raise Refusal(
            "invalid-manifest",
            f"{record['path']!r} records {record['bytes']} bytes, over the {MAX_REFERENCED_FILE_BYTES}-byte reference budget",
        )
    candidate = root / record["path"]
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):  # RuntimeError: symlink loop on Python < 3.13
        raise Refusal("missing-source", f"{record['path']!r} not found under {display_path(root)}") from None
    if not resolved.is_relative_to(root):
        raise Refusal("missing-source", f"{record['path']!r} resolves outside the capture root {display_path(root)}")
    data = read_regular_file(resolved, MAX_REFERENCED_FILE_BYTES, repr(record["path"]))
    if len(data) > MAX_REFERENCED_FILE_BYTES:
        raise Refusal(
            mismatch_kind,
            f"{record['path']!r} is larger than the {MAX_REFERENCED_FILE_BYTES}-byte reference budget; recorded {record['bytes']}",
        )
    digest, size = sha256_hex(data), len(data)
    if digest != record["sha256"] or size != record["bytes"]:
        raise Refusal(
            mismatch_kind,
            f"{record['path']!r} does not satisfy its recorded identity: "
            f"hash {digest} vs recorded {record['sha256']}, size {size} vs recorded {record['bytes']}",
        )
    return data


def _json_object_or_none(path: str, data: bytes) -> _StrictObject | None:
    """A referenced file parsed as a JSON object, or None when it is not one.
    Files that are not JSON objects make no schema or identity declaration."""
    try:
        parsed = parse_json(data, allow_constants=True)
    except TooDeep:
        raise Refusal("schema-drift", f"{path!r} nests too deeply to check its schema_version") from None
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, _StrictObject) else None


def check_declared_schema(path: str, parsed: _StrictObject | None, expected: str, field: str) -> bool:
    """A file that declares schema_version must declare `expected`. Returns
    whether the file declared one. A declared null or a repeated key is a
    declaration that does not match, not an absent one."""
    if parsed is None or "schema_version" not in parsed:
        return False
    if "schema_version" in parsed.duplicates:
        raise Refusal("schema-drift", f"{path!r} declares schema_version more than once")
    declared = parsed["schema_version"]
    if declared != expected:
        raise Refusal("schema-drift", f"{path!r} declares schema_version {declared!r}, the manifest's {field} is {expected!r}")
    return True


def check_result_identity(path: str, parsed: _StrictObject | None, claimed: str) -> str:
    """Compare a self-identifying result with the manifest's claim. A present
    result_id must be a non-empty string equal to the claim; only an absent
    key (or a file that is not a JSON object) narrows the guarantee."""
    if parsed is None or "result_id" not in parsed:
        return "bytes-only"
    if "result_id" in parsed.duplicates:
        raise Refusal("mismatched-result", f"{path!r} declares result_id more than once")
    embedded = parsed["result_id"]
    if not is_nonblank_string(embedded):
        raise Refusal(
            "mismatched-result",
            f"the result file embeds a malformed result_id {embedded!r}; a present result_id must be a non-empty string",
        )
    if embedded != claimed:
        raise Refusal("mismatched-result", f"the result file embeds result_id {embedded!r}, the manifest claims {claimed!r}")
    return "embedded-match"


def verify_pack(pack_bytes: bytes, capture_root: Path | None = None) -> dict:
    """Check one pack snapshot and its referenced inputs. Returns only values
    that were checked; raises Refusal on the first rule the evidence breaks."""
    manifest, entries = load_pack(pack_bytes)
    validate_manifest(manifest)
    check_packaged_files(manifest, entries)

    capture = manifest["capture"]
    try:
        root = (capture_root if capture_root is not None else REPO_ROOT / capture["capture_root"]).resolve()
    except (OSError, RuntimeError, ValueError):
        raise Refusal("missing-source", f"the capture root {capture['capture_root']!r} cannot be resolved") from None

    sources = []
    for ref in capture["source_refs"]:
        data = snapshot_reference(ref, root, mismatch_kind="missing-source")
        declared = check_declared_schema(ref["path"], _json_object_or_none(ref["path"], data), manifest["capture_schema_version"], "capture_schema_version")
        sources.append(
            {"role": ref["role"], "path": ref["path"], "sha256": sha256_hex(data), "bytes": len(data), "declares_schema_version": declared}
        )

    result = manifest["result"]
    result_data = snapshot_reference(result, root, mismatch_kind="mismatched-result")
    parsed_result = _json_object_or_none(result["path"], result_data)
    result_declared = check_declared_schema(result["path"], parsed_result, manifest["result_schema_version"], "result_schema_version")
    identity = check_result_identity(result["path"], parsed_result, result["result_id"])

    return {
        "pack_sha256": sha256_hex(pack_bytes),
        "pack_bytes": len(pack_bytes),
        "manifest_sha256": sha256_hex(entries["manifest.json"]),
        "evidence_pack_format_version": manifest["evidence_pack_format_version"],
        "capture_schema_version": manifest["capture_schema_version"],
        "result_schema_version": manifest["result_schema_version"],
        "capture": {
            "capture_id": capture["capture_id"],
            "captured_at": capture["captured_at"],
            "capture_root": capture["capture_root"],
            "resolved_root": display_path(root),
        },
        "source_refs": sources,
        "result": {
            "result_id": result["result_id"],
            "path": result["path"],
            "sha256": sha256_hex(result_data),
            "bytes": len(result_data),
            "declares_schema_version": result_declared,
            "identity": identity,
        },
        "packaged_files": [
            {"path": path, "sha256": sha256_hex(entries[path]), "bytes": len(entries[path])} for path in sorted(set(entries) - {"manifest.json"})
        ],
        "report_markdown": entries["report.md"].decode("utf-8"),
        "missing_fields": [{"field": entry["field"], "detail": entry["detail"]} for entry in manifest["missing_fields"]],
        "reproduction_command": manifest["reproduction"]["command"],
        "geometry_correctness": {"assertion": None, "note": manifest["geometry_correctness"]["note"]},
    }


def print_receipt(pack_path: Path, verified: dict) -> None:
    print("capture-evidence-pack reader")
    print(f"pack: {display_path(pack_path)}")
    print(f"pack sha256: {verified['pack_sha256']}")
    print(f"manifest sha256: {verified['manifest_sha256']}")
    print(f"evidence_pack_format_version: {verified['evidence_pack_format_version']}")
    print(f"capture_schema_version: {verified['capture_schema_version']}")
    print(f"result_schema_version: {verified['result_schema_version']}")
    capture = verified["capture"]
    print(f"capture: {capture['capture_id']} captured_at {capture['captured_at']}")
    print(f"capture root checked: {capture['resolved_root']}")
    for ref in verified["source_refs"]:
        print(f"source ref ok: {ref['role']}:{ref['path']} sha256 {ref['sha256']}")
    result = verified["result"]
    print(f"result ok: {result['result_id']} {result['path']} sha256 {result['sha256']}")
    if result["identity"] == "embedded-match":
        print(f"result identity: embedded id {result['result_id']!r} matches result_id")
    else:
        print("result identity: bytes-only guarantee (the result file does not self-identify)")
    print(f"missing fields (explicit): {len(verified['missing_fields'])}")
    for entry in verified["missing_fields"]:
        print(f"  missing: {entry['field']} ({entry['detail']})")
    print(f"geometry_correctness: assertion null, no claim made ({verified['geometry_correctness']['note']})")
    print(f"reproduce: {verified['reproduction_command']}")
    print("verdict: PASS")


def print_refusal(refusal: Refusal, pack_bytes: bytes | None, arguments: list[str]) -> None:
    if pack_bytes is None:
        print("rejected pack sha256: unavailable: the reader refused before it held the pack bytes")
    else:
        print(f"rejected pack sha256: {sha256_hex(pack_bytes)}")
        print(f"rejected pack bytes: {len(pack_bytes)}")
    print(f"reader arguments: {shlex.join(arguments)}")
    print(f"REFUSAL {refusal.kind}: {refusal.detail}")
    print("verdict: REFUSED")


def main(argv: list[str] | None = None) -> int:
    # Arguments are echoed in receipts; an undecodable byte in a filename
    # must print as an escape, not stop the reader mid-receipt.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pack", type=Path, help="path to the evidence pack zip")
    parser.add_argument("--capture-root", type=Path, default=None, help="resolve source refs against this directory instead of the recorded capture_root")
    args = parser.parse_args(arguments)

    # absolute() does not touch the filesystem; the open in read_pack_file is
    # the first call that can fail, and it fails as a refusal.
    pack_path = args.pack.absolute()
    pack_bytes = None
    try:
        pack_bytes = read_pack_file(pack_path, f"pack {args.pack}")
        verified = verify_pack(pack_bytes, args.capture_root)
    except Refusal as refusal:
        print_refusal(refusal, pack_bytes, arguments)
        return 2
    print_receipt(pack_path, verified)
    return 0


if __name__ == "__main__":
    sys.exit(main())
