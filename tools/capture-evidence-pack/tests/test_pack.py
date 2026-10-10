"""The packer: deterministic output, conformance of what it writes, refusals."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = "tools/capture-evidence-pack"


def run_pack(fixture: str, out, *extra):
    return subprocess.run(
        [sys.executable, f"{TOOL}/pack.py", "--fixture", fixture, "--out", str(out), *extra],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def read_manifest(pack: Path) -> dict:
    with zipfile.ZipFile(pack) as archive:
        return json.loads(archive.read("manifest.json"))


def test_pack_example_succeeds_and_receipt_names_identity(tmp_path):
    result = run_pack(f"{TOOL}/fixtures/example", tmp_path / "example.zip")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verdict: PACKED" in result.stdout
    assert "capture-synth-0001" in result.stdout
    assert "result-synth-0001" in result.stdout
    assert "geometry_correctness: assertion null" in result.stdout


def test_packing_is_byte_stable(tmp_path):
    out = tmp_path / "example.zip"
    first = run_pack(f"{TOOL}/fixtures/example", out)
    assert first.returncode == 0, first.stdout + first.stderr
    first_bytes = out.read_bytes()
    second = run_pack(f"{TOOL}/fixtures/example", out)
    assert second.returncode == 0, second.stdout + second.stderr
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(out.read_bytes()).hexdigest()


def test_zip_entries_have_fixed_timestamps_and_order(tmp_path):
    out = tmp_path / "example.zip"
    assert run_pack(f"{TOOL}/fixtures/example", out).returncode == 0
    with zipfile.ZipFile(out) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == ["manifest.json", "report.md"]
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 0
            assert info.comment == b""
            assert info.extract_version <= 20


def test_pack_manifest_conforms_to_spec(tmp_path):
    import validate

    out = tmp_path / "example.zip"
    assert run_pack(f"{TOOL}/fixtures/example", out).returncode == 0
    assert validate.validate_manifest(read_manifest(out)) == []


def test_packaged_files_hashes_match_zip_entries(tmp_path):
    out = tmp_path / "example.zip"
    assert run_pack(f"{TOOL}/fixtures/example", out).returncode == 0
    manifest = read_manifest(out)
    with zipfile.ZipFile(out) as archive:
        covered = {record["path"] for record in manifest["packaged_files"]}
        assert covered == {"report.md"}
        for record in manifest["packaged_files"]:
            entry = archive.read(record["path"])
            assert hashlib.sha256(entry).hexdigest() == record["sha256"]
            assert len(entry) == record["bytes"]


def test_manifest_references_sources_instead_of_embedding_them(tmp_path):
    out = tmp_path / "example.zip"
    assert run_pack(f"{TOOL}/fixtures/example", out).returncode == 0
    manifest = read_manifest(out)
    with zipfile.ZipFile(out) as archive:
        entry_names = set(archive.namelist())
    for ref in manifest["capture"]["source_refs"]:
        assert ref["path"] not in entry_names, "capture inputs are referenced, never embedded"


def test_packer_refuses_missing_source_fixture(tmp_path):
    out = tmp_path / "nope.zip"
    result = run_pack(f"{TOOL}/fixtures/missing-source", out)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSAL missing-source" in result.stdout
    assert "frame_0002.json" in result.stdout
    assert not out.exists(), "a refused pack writes nothing"


def test_packer_refuses_result_schema_drift(tmp_path):
    import shutil

    variant = tmp_path / "drifted"
    shutil.copytree(REPO_ROOT / TOOL / "fixtures/example", variant)
    frame = variant / "capture/frame_0002.json"
    data = json.loads(frame.read_text())
    data["schema_version"] = "synthetic-capture/2025.01"
    frame.write_text(json.dumps(data, indent=2) + "\n")
    out = tmp_path / "nope.zip"
    result = run_pack(str(variant), out)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSAL schema-drift" in result.stdout
    assert not out.exists(), "a refused pack writes nothing"
