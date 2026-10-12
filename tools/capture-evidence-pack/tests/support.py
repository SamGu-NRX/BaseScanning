"""Shared helpers: run the real command lines and build test packs by hand.

Packs built here never go through pack.py, so a reader test cannot pass
because the packer and reader share a mistake."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = "tools/capture-evidence-pack"
TOOL_DIR = REPO_ROOT / TOOL
EXAMPLE_PACK = TOOL_DIR / "evidence" / "example.zip"
EXAMPLE_FIXTURE = TOOL_DIR / "fixtures" / "example"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_reader(*args, cwd: Path = REPO_ROOT):
    return subprocess.run(
        [sys.executable, str(TOOL_DIR / "read.py"), *map(str, args)], capture_output=True, text=True, cwd=cwd
    )


def run_packer(fixture, out, cwd: Path = REPO_ROOT):
    return subprocess.run(
        [sys.executable, str(TOOL_DIR / "pack.py"), "--fixture", str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def example_entries() -> tuple[dict, bytes]:
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        return json.loads(archive.read("manifest.json")), archive.read("report.md")


def write_pack(path: Path, manifest: dict | bytes, report: bytes, extra: list[tuple[zipfile.ZipInfo | str, bytes]] = ()) -> Path:
    manifest_bytes = manifest if isinstance(manifest, bytes) else (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("report.md", report)
        for info, data in extra:
            archive.writestr(info, data)
    return path


def rebuilt_example(tmp_path: Path, mutate=None, name: str = "rebuilt.zip") -> Path:
    manifest, report = example_entries()
    if mutate is not None:
        mutate(manifest)
    return write_pack(tmp_path / name, manifest, report)


def copy_fixture_into_repo(tmp_name: str) -> Path:
    """A writable copy of the example fixture inside the repository, because
    the packer requires capture_root to be repository-relative. The caller
    removes it (see the fixture_copy pytest fixture)."""
    target = TOOL_DIR / "fixtures" / f".test-{tmp_name}"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(EXAMPLE_FIXTURE, target)
    return target


def capture_root_with(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "capture-root"
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def point_manifest_at(manifest: dict, frame: bytes, result: bytes, result_id: str = "result-synth-0001") -> None:
    """Rewrite the example manifest's refs to describe one frame and one result."""
    manifest["capture"]["source_refs"] = [
        {"role": "frame", "path": "capture/frame_0001.json", "sha256": sha256_hex(frame), "bytes": len(frame)}
    ]
    manifest["result"] = {"result_id": result_id, "path": "result/result.json", "sha256": sha256_hex(result), "bytes": len(result)}
