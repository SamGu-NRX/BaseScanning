#!/usr/bin/env python3
"""Deterministic packer for capture evidence packs.

Packs one fixture directory into a byte-stable zip whose manifest.json carries
the identity evidence SPEC.md defines. Runs the packer-side validator and
refuses to write a pack that would not conform. The reader (read.py) is
deliberately independent of this file.

Every input is read once. The manifest's hashes, the schema and identity
checks, and the packed report all come from that one read, so a file that
changes while the packer runs cannot end up hashed as one version and packed
as another.

Usage:
    python tools/capture-evidence-pack/pack.py --fixture tools/capture-evidence-pack/fixtures/example --out tools/capture-evidence-pack/evidence/example.zip
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path

import validate
from validate import InputProblem

REPO_ROOT = validate.REPO_ROOT

# Fixed zip metadata so the same fixture always packs to the same bytes.
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o644 << 16


class Refused(Exception):
    def __init__(self, kind: str, details: list[str]):
        super().__init__("; ".join(details))
        self.kind = kind
        self.details = details


def command_pack_path(out: Path) -> str:
    """The pack path as the reproduction command records it: repo-relative when
    the pack lives in the repo, absolute otherwise. Regenerating to the same
    out path is what makes byte-stable output; the recorded path is part of the
    manifest bytes."""
    return validate.display_path(out)


def reproduction_command(out: Path) -> str:
    # Quoted so the command survives a path with spaces or shell characters.
    return f"python tools/capture-evidence-pack/read.py {shlex.quote(command_pack_path(out))}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_record(relative: str, data: bytes) -> dict:
    return {"path": relative, "sha256": sha256_hex(data), "bytes": len(data)}


def load_pack_spec(fixture: Path) -> dict:
    try:
        data = validate.read_bounded(fixture, "pack-spec.json", validate.MAX_PACK_SPEC_BYTES)
    except InputProblem as problem:
        raise Refused(problem.kind, [f"{problem.detail}; a fixture needs pack-spec.json, capture/, result/ and report.md"]) from None
    try:
        spec = validate.load_json_strict(data)
    except ValueError as error:
        raise Refused("invalid-manifest", [f"pack-spec.json is not readable JSON: {error}"]) from None
    problems = validate.validate_pack_spec(spec)
    if problems:
        raise Refused("invalid-manifest", problems)
    return spec


def read_input(fixture: Path, relative: str, limit: int) -> bytes:
    try:
        return validate.read_bounded(fixture, relative, limit)
    except InputProblem as problem:
        raise Refused(problem.kind, [problem.detail]) from None


def parse_object(relative: str, data: bytes) -> dict | None:
    try:
        return validate.parse_json_object(relative, data)
    except InputProblem as problem:
        raise Refused(problem.kind, [problem.detail]) from None


def build(fixture: Path, out: Path) -> tuple[dict, bytes, str]:
    """Return (manifest, pack bytes, result identity) built from one read of
    every input, or raise Refused before anything is written."""
    if not fixture.is_dir():
        raise Refused("missing-source", [f"fixture directory {validate.display_path(fixture)} does not exist"])
    spec = load_pack_spec(fixture)

    capture_schema = spec["capture_schema_version"]
    source_refs = []
    for ref in spec["source_refs"]:
        data = read_input(fixture, ref["path"], validate.MAX_REFERENCED_FILE_BYTES)
        drift = validate.schema_drift(parse_object(ref["path"], data), capture_schema, "capture_schema_version")
        if drift:
            raise Refused("schema-drift", [f"{ref['path']} {drift}"])
        source_refs.append({**file_record(ref["path"], data), "role": ref["role"]})

    result_relative = spec["result_path"]
    result_data = read_input(fixture, result_relative, validate.MAX_REFERENCED_FILE_BYTES)
    parsed_result = parse_object(result_relative, result_data)
    drift = validate.schema_drift(parsed_result, spec["result_schema_version"], "result_schema_version")
    if drift:
        raise Refused("schema-drift", [f"{result_relative} {drift}"])
    identity_problem = validate.result_identity_problem(parsed_result, spec["result_id"])
    if identity_problem:
        raise Refused("mismatched-result", [identity_problem])
    identity = "embedded-match" if parsed_result is not None and "result_id" in parsed_result else "bytes-only"

    report_data = read_input(fixture, validate.REPORT_ENTRY, validate.MAX_REPORT_BYTES)
    try:
        report_data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("invalid-manifest", [f"{validate.REPORT_ENTRY} is not UTF-8 text"]) from None

    try:
        capture_root = fixture.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        raise Refused(
            "invalid-manifest",
            [f"fixture {fixture} lives outside the repository; capture_root must be a repository-relative path (SPEC.md, capture)"],
        ) from None

    manifest = {
        "evidence_pack_format_version": validate.PACK_FORMAT_VERSION,
        "capture_schema_version": capture_schema,
        "result_schema_version": spec["result_schema_version"],
        "capture": {
            "capture_id": spec["capture_id"],
            "captured_at": spec["captured_at"],
            "capture_root": capture_root,
            "source_refs": source_refs,
        },
        "result": {"result_id": spec["result_id"], **file_record(result_relative, result_data)},
        "missing_fields": spec["missing_fields"],
        "reproduction": {"command": reproduction_command(out)},
        "geometry_correctness": {
            "assertion": None,
            "note": "identity-only pack: no geometry-correctness check ran, none is claimed",
        },
        "packaged_files": [file_record(validate.REPORT_ENTRY, report_data)],
    }
    problems = validate.validate_manifest(manifest)
    if problems:
        for problem in problems:
            print(f"invalid-manifest: {problem}", file=sys.stderr)
        raise Refused("invalid-manifest", ["the packer produced a manifest that violates SPEC.md; this is a packer bug"])
    return manifest, archive_bytes(manifest, report_data), identity


def archive_bytes(manifest: dict, report_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.comment = b""
        _add_entry(archive, "manifest.json", json_deterministic(manifest))
        _add_entry(archive, validate.REPORT_ENTRY, report_bytes)
    return buffer.getvalue()


def _add_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = _FILE_MODE
    info.create_system = 0
    archive.writestr(info, data, compresslevel=6)


def json_deterministic(value: dict) -> bytes:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def write_atomically(out: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory and rename it into
    place, so a reader never sees a half-written pack and a failed write leaves
    any earlier file at the out path untouched."""
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, out)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", required=True, type=Path, help="fixture directory: pack-spec.json, capture/, result/, report.md")
    parser.add_argument("--out", required=True, type=Path, help="zip path to write, relative or absolute")
    args = parser.parse_args(argv)

    fixture = args.fixture.resolve()
    out = args.out.resolve()
    try:
        manifest, pack_bytes, identity = build(fixture, out)
    except Refused as refused:
        for detail in refused.details:
            print(f"REFUSAL {refused.kind}: {detail}")
        print("verdict: REFUSED (nothing written)")
        return 2
    write_atomically(out, pack_bytes)

    packaged = manifest["packaged_files"]
    print(f"packer: wrote {command_pack_path(out)}")
    print(f"pack sha256: {sha256_hex(pack_bytes)}")
    print(f"manifest sha256: {sha256_hex(json_deterministic(manifest))}")
    print(f"entries: {len(packaged) + 1} (manifest.json plus {len(packaged)} packaged)")
    print(f"capture: {manifest['capture']['capture_id']} ({len(manifest['capture']['source_refs'])} source refs, referenced not embedded)")
    print(f"result: {manifest['result']['result_id']}")
    if identity == "embedded-match":
        print(f"result identity: embedded id {manifest['result']['result_id']!r} matches result_id")
    else:
        print("result identity: bytes-only guarantee (the result file does not self-identify)")
    print(f"missing fields (explicit): {len(manifest['missing_fields'])}")
    print("geometry_correctness: assertion null (no claim made)")
    print(f"reproduce: {manifest['reproduction']['command']}")
    print("verdict: PACKED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
