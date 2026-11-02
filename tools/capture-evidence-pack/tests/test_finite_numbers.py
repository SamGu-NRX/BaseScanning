"""pack-spec.json and manifest.json carry only finite numbers.

Python's json turns 1e400 into infinity without complaint, and its encoder
writes infinity as `Infinity`, which is not JSON. A non-finite number in a
place the format leaves open (an extra key in a missing_fields entry) must be
refused by the packer before it writes and by the reader when it reads.
Referenced capture files keep their own rule (SPEC.md, Source refs): they
are read as Python's json reads them, and that rule is tested here too."""

import json
import shutil
import sys

import pytest
from support import (
    TOOL_DIR,
    capture_root_with,
    copy_fixture_into_repo,
    example_entries,
    point_manifest_at,
    rebuilt_example,
    run_packer,
    run_reader,
    sha256_hex,
    write_pack,
)


@pytest.fixture
def repo_fixture(request):
    path = copy_fixture_into_repo("finite-" + "".join(c if c.isalnum() else "_" for c in request.node.name))
    yield path
    shutil.rmtree(path, ignore_errors=True)


MARKER = '"__value__"'
NON_FINITE = {
    "positive-overflow": '{"extra": 1e400}',
    "negative-overflow": '{"extra": -1e400}',
    "nested": '{"extra": {"deeper": [1, 2.5, 1E999]}}',
}


def spec_with_extension(fixture, extension: str) -> None:
    """Give missing_fields[0] an extra key, written as raw JSON text because
    json.dumps cannot write 1e400 itself."""
    spec_path = fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    spec["missing_fields"][0]["extension"] = "__value__"
    spec_path.write_text(json.dumps(spec).replace(MARKER, extension))


@pytest.mark.parametrize("extension", NON_FINITE.values(), ids=NON_FINITE.keys())
def test_packer_refuses_non_finite_numbers_in_pack_spec(repo_fixture, tmp_path, extension):
    spec_with_extension(repo_fixture, extension)
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest: pack-spec.json" in outcome.stdout and "not finite" in outcome.stdout, outcome.stdout
    assert not out.exists()


def test_control_finite_extension_packs_and_reads(repo_fixture, tmp_path):
    spec_with_extension(repo_fixture, '{"extra": [1.5e300, -2.25, 0]}')
    out = tmp_path / "out.zip"
    packed = run_packer(repo_fixture, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    read = run_reader(out)
    assert read.returncode == 0, read.stdout + read.stderr


def manifest_with_extension(tmp_path, extension: str):
    manifest, report = example_entries()
    manifest["missing_fields"][0]["extension"] = "__value__"
    text = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").replace(MARKER, extension)
    return write_pack(tmp_path / "pack.zip", text.encode(), report)


@pytest.mark.parametrize("extension", NON_FINITE.values(), ids=NON_FINITE.keys())
def test_reader_refuses_non_finite_numbers_in_the_manifest(tmp_path, extension):
    pack = manifest_with_extension(tmp_path, extension)
    outcome = run_reader(pack)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest: manifest.json contains a number that is not finite" in outcome.stdout, outcome.stdout
    assert f"rejected pack sha256: {sha256_hex(pack.read_bytes())}" in outcome.stdout


def test_control_reader_accepts_finite_extensions(tmp_path):
    outcome = run_reader(manifest_with_extension(tmp_path, '{"extra": [1.5e300, -2.25, 0]}'))
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_packer_encoder_refuses_a_non_finite_value_that_reaches_it(monkeypatch, capsys):
    """Defense in depth behind the parse check: the manifest encoder refuses
    NaN and infinity instead of writing `Infinity`."""
    monkeypatch.syspath_prepend(str(TOOL_DIR))
    sys.modules.pop("pack", None)
    import pack

    manifest, report = example_entries()
    manifest["missing_fields"][0]["extension"] = float("inf")
    with pytest.raises(pack.Refused) as caught:
        pack.archive_bytes(manifest, report)
    assert caught.value.kind == "invalid-manifest"
    assert "not finite" in caught.value.details[0]


def test_referenced_files_keep_their_own_number_rule(tmp_path):
    """A result file with 1e400 is still a JSON object that names its result,
    so the reader checks its result_id rather than treating it as opaque."""
    frame = b'{"schema_version": "synthetic-capture/2026.10"}'
    result = b'{"schema_version": "synthetic-result/1.0", "result_id": "result-synth-0001", "value": 1e400}'
    root = capture_root_with(tmp_path, {"capture/frame_0001.json": frame, "result/result.json": result})
    pack = rebuilt_example(tmp_path, lambda manifest: point_manifest_at(manifest, frame, result))
    outcome = run_reader(pack, "--capture-root", root)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert "embedded id 'result-synth-0001' matches" in outcome.stdout
