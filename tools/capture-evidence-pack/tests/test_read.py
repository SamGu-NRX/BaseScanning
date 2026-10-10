"""The independent reader: acceptance, refusals, and independence from the packer."""

import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = "tools/capture-evidence-pack"
EXAMPLE_PACK = REPO_ROOT / TOOL / "evidence" / "example.zip"


def run_reader(pack, *extra):
    return subprocess.run(
        [sys.executable, f"{TOOL}/read.py", str(pack), *extra],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def rebuilt_pack(tmp_path, manifest_mutation=None, report_bytes=None) -> Path:
    """A pack rebuilt from the example with one field or file changed."""
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        report = archive.read("report.md") if report_bytes is None else report_bytes
    if manifest_mutation is not None:
        manifest_mutation(manifest)
    out = tmp_path / "rebuilt.zip"
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        archive.writestr("report.md", report)
    return out


def test_reader_accepts_example_pack():
    assert EXAMPLE_PACK.is_file(), "the committed example pack is missing"
    result = run_reader(EXAMPLE_PACK)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verdict: PASS" in result.stdout
    assert "evidence_pack_format_version: 1.0" in result.stdout
    assert "capture_schema_version: synthetic-capture/2026.10" in result.stdout
    assert "missing fields (explicit): 2" in result.stdout
    assert "capture.depth_map" in result.stdout
    assert "capture.lidar_point_cloud" in result.stdout
    assert "reproduce: python tools/capture-evidence-pack/read.py" in result.stdout
    assert "assertion null" in result.stdout


def test_reader_refuses_missing_source_variant():
    result = run_reader(
        EXAMPLE_PACK,
        "--capture-root",
        str(REPO_ROOT / TOOL / "fixtures/missing-source"),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSAL missing-source" in result.stdout
    assert "frame_0002.json" in result.stdout


def test_reader_refuses_mismatched_result_variant():
    result = run_reader(
        EXAMPLE_PACK,
        "--capture-root",
        str(REPO_ROOT / TOOL / "fixtures/mismatched-result"),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSAL mismatched-result" in result.stdout
    assert "result/result.json" in result.stdout
    assert "does not satisfy its recorded identity" in result.stdout


def test_reader_refuses_non_null_correctness_assertion(tmp_path):
    def claim_correctness(manifest):
        manifest["geometry_correctness"]["assertion"] = "geometry verified correct"

    pack = rebuilt_pack(tmp_path, manifest_mutation=claim_correctness)
    result = run_reader(pack)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "must be null" in result.stdout
    assert "Identity is not correctness" in result.stdout


def test_reader_refuses_wrong_pack_format(tmp_path):
    pack = rebuilt_pack(
        tmp_path,
        manifest_mutation=lambda manifest: manifest.update(evidence_pack_format_version="2.0"),
    )
    result = run_reader(pack)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSAL wrong-pack-format" in result.stdout


def test_reader_refuses_tampered_report(tmp_path):
    pack = rebuilt_pack(tmp_path, report_bytes=b"# tampered report\n")
    result = run_reader(pack)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not match its recorded hash and size" in result.stdout


def test_reader_refuses_extra_unlisted_entry(tmp_path):
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        report = archive.read("report.md")
    out = tmp_path / "extra.zip"
    with zipfile.ZipFile(out, "w") as dst:
        dst.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        dst.writestr("report.md", report)
        dst.writestr("smuggled.txt", "not listed in packaged_files")
    result = run_reader(out)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "smuggled.txt" in result.stdout
    assert "a conforming pack carries exactly" in result.stdout


def test_reader_is_independent_of_the_packer():
    """read.py must not import pack.py or validate.py, and must not import them
    lazily inside functions: a shared bug must not hide on both sides."""
    source = (REPO_ROOT / TOOL / "read.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pack" not in imported
    assert "validate" not in imported
