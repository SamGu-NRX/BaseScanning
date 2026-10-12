"""Archive limits (SPEC.md, Archive limits): every unsafe or oversized archive
is refused by the real reader command line before it decodes more than its
budget, and the refusal names the bytes it rejected.

Each pack here is built by hand with zipfile and, where zipfile cannot write
the defect, by patching the central directory. None goes through pack.py."""

import hashlib
import stat
import struct
import warnings
import zipfile
from pathlib import Path

import pytest
from support import EXAMPLE_PACK, example_entries, run_reader, sha256_hex, write_pack

MiB = 1024 * 1024
# Restated from SPEC.md rather than imported from read.py, so a changed
# constant in the code under test cannot move the test's expectation with it.
REPORT_BUDGET = 2 * MiB
PACK_BUDGET = 8 * MiB


def assert_refused(pack: Path, kind: str, *needles: str):
    outcome = run_reader(pack)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert f"REFUSAL {kind}:" in outcome.stdout, outcome.stdout + outcome.stderr
    for needle in needles:
        assert needle in outcome.stdout, outcome.stdout
    data = pack.read_bytes()
    if len(data) <= PACK_BUDGET:
        assert f"rejected pack sha256: {sha256_hex(data)}" in outcome.stdout
        assert f"rejected pack bytes: {len(data)}" in outcome.stdout
    assert "Traceback" not in outcome.stderr
    return outcome


def patch_central_directory(path: Path, name: str, **fields) -> None:
    """Overwrite fixed-width fields of one central directory record.
    Offsets from the ZIP APPNOTE, section 4.3.12."""
    offsets = {"flag_bits": (8, "<H"), "compress_type": (10, "<H"), "file_size": (24, "<I"), "external_attr": (38, "<I")}
    data = bytearray(path.read_bytes())
    encoded = name.encode()
    position = data.find(b"PK\x01\x02")
    while position != -1:
        name_length = struct.unpack_from("<H", data, position + 28)[0]
        if bytes(data[position + 46 : position + 46 + name_length]) == encoded:
            for field, value in fields.items():
                offset, fmt = offsets[field]
                struct.pack_into(fmt, data, position + offset, value)
            path.write_bytes(bytes(data))
            return
        position = data.find(b"PK\x01\x02", position + 4)
    raise AssertionError(f"{name} not found in the central directory")


def example_with_report(tmp_path: Path, report: bytes, name: str = "pack.zip") -> Path:
    manifest, _ = example_entries()
    manifest["packaged_files"] = [{"path": "report.md", "sha256": hashlib.sha256(report).hexdigest(), "bytes": len(report)}]
    return write_pack(tmp_path / name, manifest, report)


def incompressible_text(size: int) -> bytes:
    """Hex text compresses about 2:1, well inside the ratio budget."""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(counter.to_bytes(8, "big")).hexdigest().encode()
        counter += 1
    return bytes(out[:size])


def test_control_example_pack_passes():
    outcome = run_reader(EXAMPLE_PACK)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert "verdict: PASS" in outcome.stdout


def test_control_report_exactly_at_budget_passes(tmp_path):
    outcome = run_reader(example_with_report(tmp_path, incompressible_text(REPORT_BUDGET)))
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_report_one_byte_over_budget_is_refused(tmp_path):
    assert_refused(example_with_report(tmp_path, incompressible_text(REPORT_BUDGET + 1)), "invalid-archive", "budget")


def test_highly_compressed_entry_is_refused(tmp_path):
    # 1.5 MiB of one byte deflates to about 1.5 KB: under the size budget,
    # far over the 100:1 compression budget.
    assert_refused(example_with_report(tmp_path, b"a" * (3 * MiB // 2)), "invalid-archive", "compression budget")


def test_three_entries_are_refused_before_any_entry_is_read(tmp_path):
    manifest, report = example_entries()
    pack = write_pack(tmp_path / "three.zip", manifest, report, extra=[("smuggled.txt", b"x")])
    assert_refused(pack, "invalid-archive", "3 entries", "smuggled.txt")


def test_thousands_of_entries_are_refused_and_the_listing_is_bounded(tmp_path):
    pack = tmp_path / "many.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        for index in range(2000):
            archive.writestr(f"e{index}", b"")
    outcome = assert_refused(pack, "invalid-archive", "2000 entries", "'...'")
    assert "e1999" not in outcome.stdout


def test_empty_archive_is_refused(tmp_path):
    pack = tmp_path / "empty.zip"
    zipfile.ZipFile(pack, "w").close()
    assert_refused(pack, "invalid-archive", "0 entries")


def test_duplicate_entry_names_are_refused(tmp_path):
    pack = tmp_path / "dup.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(pack, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("manifest.json", b"{}")
    assert_refused(pack, "invalid-archive", "repeats an entry name")


@pytest.mark.parametrize("name", ["../report.md", "/report.md", "a\\report.md", "report.md/"])
def test_unsafe_entry_names_are_refused(tmp_path, name):
    _, report = example_entries()
    pack = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr(zipfile.ZipInfo(name), report)
    assert_refused(pack, "invalid-archive", repr(name))


def test_symlink_entry_is_refused(tmp_path):
    link = zipfile.ZipInfo("report.md")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    pack = tmp_path / "link.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr(link, b"/etc/passwd")
    assert_refused(pack, "invalid-archive", "not a regular file")


def test_encrypted_entry_is_refused(tmp_path):
    pack = example_with_report(tmp_path, b"# report\n")
    patch_central_directory(pack, "report.md", flag_bits=0x1)
    assert_refused(pack, "invalid-archive", "encrypted")


def test_unsupported_compression_method_is_refused(tmp_path):
    _, report = example_entries()
    pack = tmp_path / "bzip.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("report.md", report, compress_type=zipfile.ZIP_BZIP2)
    assert_refused(pack, "invalid-archive", "compression method 12")


def test_size_field_that_lies_low_is_refused(tmp_path):
    """The central directory claims fewer bytes than the entry holds. The
    reader decodes at most the claim and the CRC no longer matches."""
    pack = example_with_report(tmp_path, b"# a report that is longer than ten bytes\n")
    patch_central_directory(pack, "report.md", file_size=10)
    assert_refused(pack, "invalid-archive")


def test_pack_over_the_pack_budget_is_refused_without_a_partial_hash(tmp_path):
    pack = tmp_path / "huge.zip"
    pack.write_bytes(b"\0" * (PACK_BUDGET + 1))
    outcome = assert_refused(pack, "invalid-archive", "pack budget")
    assert "rejected pack sha256: unavailable" in outcome.stdout


def test_not_a_zip_is_refused(tmp_path):
    pack = tmp_path / "text.zip"
    pack.write_bytes(b"not a zip archive\n")
    assert_refused(pack, "invalid-archive", "not a readable zip archive")


def test_manifest_with_duplicate_keys_is_refused(tmp_path):
    _, report = example_entries()
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        manifest_bytes = archive.read("manifest.json")
    duplicated = manifest_bytes.replace(b'"assertion": null', b'"assertion": "verified", "assertion": null', 1)
    assert duplicated != manifest_bytes
    pack = write_pack(tmp_path / "dupkey.zip", duplicated, report)
    assert_refused(pack, "invalid-manifest", "repeats the key 'assertion'")


def test_manifest_with_nan_is_refused(tmp_path):
    _, report = example_entries()
    with zipfile.ZipFile(EXAMPLE_PACK) as archive:
        manifest_bytes = archive.read("manifest.json")
    pack = write_pack(tmp_path / "nan.zip", manifest_bytes.replace(b'"bytes": ', b'"bytes": NaN, "x": ', 1), report)
    assert_refused(pack, "invalid-manifest", "NaN")


def test_report_that_is_not_utf8_is_refused(tmp_path):
    assert_refused(example_with_report(tmp_path, b"\xff\xfe report"), "invalid-archive", "UTF-8")


def test_deeply_nested_manifest_is_refused_not_crashed(tmp_path):
    _, report = example_entries()
    pack = tmp_path / "deep.zip"
    depth = 100_000
    with zipfile.ZipFile(pack, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", b"[" * depth + b"]" * depth)
        archive.writestr("report.md", report)
    assert_refused(pack, "invalid-manifest", "nests deeper")
