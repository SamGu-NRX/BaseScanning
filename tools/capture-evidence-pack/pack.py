#!/usr/bin/env python3
"""Deterministic packer for capture evidence packs.

Packs one fixture directory into a byte-stable zip whose manifest.json carries
the identity evidence SPEC.md defines. Runs the packer-side validator and
refuses to write a pack that would not conform. The reader (read.py) is
deliberately independent of this file.

Usage:
    python tools/capture-evidence-pack/pack.py --fixture tools/capture-evidence-pack/fixtures/example --out tools/capture-evidence-pack/evidence/example.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = Path(__file__).resolve().parents[1]

import validate

# Fixed zip metadata so the same fixture always packs to the same bytes.
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o644 << 16


def command_pack_path(out: Path) -> str:
    """The pack path as the reproduction command records it: repo-relative when
    the pack lives in the repo, absolute otherwise. Regenerating to the same
    out path is what makes byte-stable output; the recorded path is part of the
    manifest bytes."""
    try:
        return out.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return out.as_posix()


def refuse(kind: str, detail: str) -> "NoReturn":
    print(f"REFUSAL {kind}: {detail}")
    print("verdict: REFUSED (nothing written)")
    sys.exit(2)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_record(path: Path, relative: str) -> dict:
    data = path.read_bytes()
    return {"path": relative, "sha256": sha256_hex(data), "bytes": len(data)}


def load_pack_spec(fixture: Path) -> dict:
    spec_path = fixture / "pack-spec.json"
    if not spec_path.is_file():
        refuse("missing-source", f"{spec_path} not found; a fixture needs pack-spec.json, capture/, result/ and report.md")
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        refuse("invalid-manifest", f"pack-spec.json is not readable JSON: {error}")
    if not isinstance(spec, dict):
        refuse("invalid-manifest", "pack-spec.json must contain a JSON object")
    return spec


def build_manifest(fixture: Path, spec: dict, out_relative: str) -> dict:
    source_refs = []
    for ref in spec.get("source_refs", []):
        path = fixture / ref["path"]
        if not path.is_file():
            refuse("missing-source", f"{ref['path']} not found under {fixture}")
        record = file_record(path, ref["path"])
        record["role"] = ref["role"]
        source_refs.append(record)

    result_relative = spec["result_path"]
    result_path = fixture / result_relative
    if not result_path.is_file():
        refuse("missing-source", f"{result_relative} not found under {fixture}")

    report_relative = spec.get("report_path", "report.md")
    report_path = fixture / report_relative
    if not report_path.is_file():
        refuse("missing-source", f"{report_relative} not found under {fixture}")

    schema_version = spec["capture_schema_version"]
    for checked in source_refs + [{"path": result_relative}, {"path": report_relative}]:
        data = (fixture / checked["path"]).read_bytes()
        drift = validate._schema_drift(checked["path"], data, schema_version)
        if drift:
            refuse("schema-drift", drift)

    try:
        capture_root = fixture.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        refuse(
            "invalid-manifest",
            f"fixture {fixture} lives outside the repository; capture_root must be a repository-relative path (SPEC.md, capture)",
        )

    return {
        "evidence_pack_format_version": validate.PACK_FORMAT_VERSION,
        "capture_schema_version": schema_version,
        "capture": {
            "capture_id": spec["capture_id"],
            "captured_at": spec["captured_at"],
            "capture_root": capture_root,
            "source_refs": source_refs,
        },
        "result": {"result_id": spec["result_id"], **file_record(result_path, result_relative)},
        "missing_fields": spec["missing_fields"],
        "reproduction": {
            "command": f"python tools/capture-evidence-pack/read.py {out_relative}",
        },
        "geometry_correctness": {
            "assertion": None,
            "note": "identity-only pack: no geometry-correctness check ran, none is claimed",
        },
        "packaged_files": [file_record(report_path, report_relative)],
    }


def write_pack(out: Path, manifest: dict, report_bytes: bytes, report_relative: str) -> None:
    manifest_bytes = json_deterministic(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.comment = b""
        _add_entry(archive, "manifest.json", manifest_bytes)
        _add_entry(archive, report_relative, report_bytes)


def _add_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = _FILE_MODE
    info.create_system = 0
    archive.writestr(info, data, compresslevel=6)


def json_deterministic(value: dict) -> bytes:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", required=True, type=Path, help="fixture directory: pack-spec.json, capture/, result/, report.md")
    parser.add_argument("--out", required=True, type=Path, help="zip path to write, relative or absolute")
    args = parser.parse_args()

    fixture = args.fixture.resolve()
    out = args.out.resolve()
    if not fixture.is_dir():
        refuse("missing-source", f"fixture directory {fixture} does not exist")

    spec = load_pack_spec(fixture)
    manifest = build_manifest(fixture, spec, command_pack_path(out))

    problems = validate.validate_manifest(manifest)
    if problems:
        for problem in problems:
            print(f"invalid-manifest: {problem}", file=sys.stderr)
        refuse("invalid-manifest", "the packer produced a manifest that violates SPEC.md; this is a packer bug")

    report_relative = spec.get("report_path", "report.md")
    write_pack(out, manifest, (fixture / report_relative).read_bytes(), report_relative)

    pack_bytes = out.read_bytes()
    print(f"packer: wrote {command_pack_path(out)}")
    print(f"pack sha256: {sha256_hex(pack_bytes)}")
    print(f"manifest sha256: {sha256_hex(json_deterministic(manifest))}")
    print(f"entries: {len(manifest['packaged_files']) + 1} (manifest.json plus {len(manifest['packaged_files'])} packaged)")
    print(f"capture: {manifest['capture']['capture_id']} ({len(manifest['capture']['source_refs'])} source refs, referenced not embedded)")
    print(f"result: {manifest['result']['result_id']}")
    print(f"missing fields (explicit): {len(manifest['missing_fields'])}")
    print(f"geometry_correctness: assertion null (no claim made)")
    print(f"reproduce: {manifest['reproduction']['command']}")
    print("verdict: PACKED")


if __name__ == "__main__":
    main()
