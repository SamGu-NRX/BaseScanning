"""The packer never writes a pack its reader must refuse.

Each case packs through pack.py's command line and then hands the result to
read.py's command line, which shares no code with the packer. Sizes are
measured from the written zip with zipfile, never from packer output."""

import hashlib
import json
import shutil
import sys
import zipfile

import pytest
from support import copy_fixture_into_repo, run_packer, run_reader

MiB = 1024 * 1024
# Restated from SPEC.md, Archive limits.
MANIFEST_BUDGET = 1 * MiB
REPORT_BUDGET = 2 * MiB


@pytest.fixture
def repo_fixture(request):
    path = copy_fixture_into_repo("budget-" + "".join(c if c.isalnum() else "_" for c in request.node.name))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def entry(pack, name) -> zipfile.ZipInfo:
    with zipfile.ZipFile(pack) as archive:
        return archive.getinfo(name)


def pack_and_read(fixture, out):
    packed = run_packer(fixture, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    read = run_reader(out)
    assert read.returncode == 0, read.stdout + read.stderr
    return packed


@pytest.mark.parametrize("size", [3 * MiB // 2, REPORT_BUDGET], ids=["1.5MiB", "exactly-2MiB"])
def test_highly_compressible_report_packs_and_reads(repo_fixture, tmp_path, size):
    """Deflate would shrink this report past the reader's 100:1 budget, so the
    packer must store it rather than write a pack the reader refuses."""
    (repo_fixture / "report.md").write_bytes(b"a" * size)
    out = tmp_path / "pack.zip"
    pack_and_read(repo_fixture, out)
    info = entry(out, "report.md")
    assert info.file_size == size
    assert info.file_size <= 100 * max(info.compress_size, 1)


def test_compressible_report_repacks_to_identical_bytes(repo_fixture, tmp_path):
    (repo_fixture / "report.md").write_bytes(b"a" * (3 * MiB // 2))
    out = tmp_path / "pack.zip"
    pack_and_read(repo_fixture, out)
    first = hashlib.sha256(out.read_bytes()).hexdigest()
    pack_and_read(repo_fixture, out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == first


def test_report_one_byte_over_budget_is_refused_by_the_packer(repo_fixture, tmp_path):
    (repo_fixture / "report.md").write_bytes(b"a" * (REPORT_BUDGET + 1))
    out = tmp_path / "pack.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL invalid-manifest: report.md is larger than its" in outcome.stdout
    assert not out.exists()


def grow_manifest(fixture, extra: int, entries: int = 4000) -> None:
    """Add missing_fields entries to pack-spec.json. The manifest indents them,
    so it grows faster than the compact pack-spec does."""
    spec_path = fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    for index in range(entries):
        spec["missing_fields"].append({"field": f"capture.synthetic_gap_{index:05d}", "detail": f"synthetic gap number {index} for a size test"})
    spec["missing_fields"][-1]["detail"] += "x" * extra
    spec_path.write_text(json.dumps(spec, separators=(",", ":")))


@pytest.fixture
def manifest_at_budget(repo_fixture, tmp_path):
    """Return a function that sets pack-spec so the packed manifest is
    MANIFEST_BUDGET + delta bytes, measured from a probe pack."""
    original = (repo_fixture / "pack-spec.json").read_bytes()

    def build(delta: int):
        (repo_fixture / "pack-spec.json").write_bytes(original)
        grow_manifest(repo_fixture, 0)
        # The out path is part of the manifest (reproduction.command), so the
        # probe must use the same path as the pack under test.
        probe = tmp_path / "pack.zip"
        assert run_packer(repo_fixture, probe).returncode == 0
        base = entry(probe, "manifest.json").file_size
        probe.unlink()
        assert base < MANIFEST_BUDGET, base
        (repo_fixture / "pack-spec.json").write_bytes(original)
        grow_manifest(repo_fixture, MANIFEST_BUDGET - base + delta)
        assert (repo_fixture / "pack-spec.json").stat().st_size <= MANIFEST_BUDGET
        return repo_fixture

    return build


def test_manifest_exactly_at_budget_packs_and_reads(manifest_at_budget, tmp_path):
    fixture = manifest_at_budget(0)
    out = tmp_path / "pack.zip"
    pack_and_read(fixture, out)
    assert entry(out, "manifest.json").file_size == MANIFEST_BUDGET


def test_manifest_one_byte_over_budget_is_refused_by_the_packer(manifest_at_budget, tmp_path):
    fixture = manifest_at_budget(1)
    out = tmp_path / "pack.zip"
    outcome = run_packer(fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert f"REFUSAL invalid-manifest: manifest.json would be {MANIFEST_BUDGET + 1} bytes" in outcome.stdout
    assert not out.exists()


def test_packer_checks_the_whole_archive_against_the_pack_budget(monkeypatch, tmp_path, capsys):
    """Two entries within their budgets cannot reach 8 MiB today; the check
    exists so a later budget change cannot let the packer write a pack the
    reader's size budget refuses. A lowered limit exercises it."""
    from support import EXAMPLE_FIXTURE, TOOL_DIR

    monkeypatch.syspath_prepend(str(TOOL_DIR))
    sys.modules.pop("pack", None)
    import pack
    import validate

    monkeypatch.setattr(validate, "MAX_PACK_BYTES", 1000)
    out = tmp_path / "pack.zip"
    code = pack.main(["--fixture", str(EXAMPLE_FIXTURE), "--out", str(out)])
    assert code == 2
    assert "REFUSAL invalid-archive: the pack would be" in capsys.readouterr().out
    assert not out.exists()
