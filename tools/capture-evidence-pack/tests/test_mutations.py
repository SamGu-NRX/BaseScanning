"""Isolated mutation matrix: one defect per case, caught by the real CLIs.

Each case rebuilds the committed example pack with exactly one defect and
replays it through the packer or the reader command line. Nothing here shares
code with the tools under test: if a refusal regresses into a crash or a
silent pass, a test in this file fails with the CLI's actual output.
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = "tools/capture-evidence-pack"
EXAMPLE_PACK = REPO_ROOT / TOOL / "evidence" / "example.zip"


def run_packer(fixture: Path, out: Path):
    return subprocess.run(
        [sys.executable, f"{TOOL}/pack.py", "--fixture", str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def rebuilt_example(tmp_path, mutate):
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        report = archive.read("report.md")
    mutate(manifest)
    out = tmp_path / "rebuilt.zip"
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        archive.writestr("report.md", report)
    return out


def run_reader(pack: Path, *extra):
    return subprocess.run(
        [sys.executable, f"{TOOL}/read.py", str(pack), *extra],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


# Committed invalid fixtures: the packer refuses each by name, before writing.
PACKER_MUTATIONS = [
    ("missing-captured-at", "invalid-manifest", ["captured_at"]),
    ("invalid-captured-at", "invalid-manifest", ["captured_at"]),
    ("null-missing-fields", "invalid-manifest", ["missing_fields"]),
    ("missing-detail", "invalid-manifest", ["detail"]),
    ("mismatched-result-id", "mismatched-result", ["result-synth-0002"]),
]


@pytest.mark.parametrize("fixture_name,refusal_kind,needles", PACKER_MUTATIONS, ids=[case[0] for case in PACKER_MUTATIONS])
def test_packer_catches_each_defect(tmp_path, fixture_name, refusal_kind, needles):
    out = tmp_path / "refused.zip"
    result = run_packer(REPO_ROOT / TOOL / "fixtures" / fixture_name, out)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"REFUSAL {refusal_kind}" in result.stdout, result.stdout + result.stderr
    for needle in needles:
        assert needle in result.stdout, result.stdout
    assert not out.exists(), "a refused pack writes nothing"


def drop_captured_at(manifest):
    del manifest["capture"]["captured_at"]


def impossible_captured_at(manifest):
    manifest["capture"]["captured_at"] = "2026-02-30T00:00:00Z"


def non_padded_captured_at(manifest):
    manifest["capture"]["captured_at"] = "2026-1-5T00:00:00Z"


def null_missing_fields(manifest):
    manifest["missing_fields"] = None


def drop_field_detail(manifest):
    del manifest["missing_fields"][0]["detail"]


def blank_field_detail(manifest):
    manifest["missing_fields"][0]["detail"] = "   "


def claim_other_result_id(manifest):
    manifest["result"]["result_id"] = "result-synth-0002"


READER_MUTATIONS = [
    (drop_captured_at, "invalid-manifest", ["captured_at"]),
    (impossible_captured_at, "invalid-manifest", ["captured_at"]),
    (non_padded_captured_at, "invalid-manifest", ["captured_at"]),
    (null_missing_fields, "invalid-manifest", ["missing_fields"]),
    (drop_field_detail, "invalid-manifest", ["detail"]),
    (blank_field_detail, "invalid-manifest", ["detail"]),
    (claim_other_result_id, "mismatched-result", ["result-synth-0002", "result-synth-0001"]),
]


@pytest.mark.parametrize("mutate,refusal_kind,needles", READER_MUTATIONS, ids=[m.__name__ for m, _, _ in READER_MUTATIONS])
def test_reader_catches_each_defect(tmp_path, mutate, refusal_kind, needles):
    pack = rebuilt_example(tmp_path, mutate)
    result = run_reader(pack)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"REFUSAL {refusal_kind}" in result.stdout, result.stdout + result.stderr
    for needle in needles:
        assert needle in result.stdout, result.stdout
    assert f"rejected pack sha256: {hashlib.sha256(pack.read_bytes()).hexdigest()}" in result.stdout
