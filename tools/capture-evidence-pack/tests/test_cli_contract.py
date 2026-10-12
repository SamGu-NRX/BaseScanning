"""Command-line contract of pack.py and read.py: result identity, the two
independent schema versions, report naming, reproduction quoting, refusal
binding, output paths and working directories. Every case runs the real
command line; each refusal has a passing control next to it."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import pytest
from support import (
    EXAMPLE_FIXTURE,
    EXAMPLE_PACK,
    REPO_ROOT,
    TOOL_DIR,
    capture_root_with,
    copy_fixture_into_repo,
    point_manifest_at,
    rebuilt_example,
    run_packer,
    run_reader,
    sha256_hex,
)

CAPTURE = "synthetic-capture/2026.10"
RESULT = "synthetic-result/1.0"


@pytest.fixture
def repo_fixture(request):
    """A writable copy of the example fixture inside the repository (the
    packer requires a repository-relative capture_root), removed afterwards."""
    path = copy_fixture_into_repo(re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def json_bytes(value) -> bytes:
    return (json.dumps(value) + "\n").encode()


def read_with(tmp_path, frame: dict, result_bytes: bytes, result_id: str = "result-synth-0001"):
    frame_bytes = json_bytes(frame)
    root = capture_root_with(tmp_path, {"capture/frame_0001.json": frame_bytes, "result/result.json": result_bytes})
    pack = rebuilt_example(tmp_path, lambda manifest: point_manifest_at(manifest, frame_bytes, result_bytes, result_id))
    return run_reader(pack, "--capture-root", root), pack


# Result identity: a present result_id is checked, whatever its value.

MALFORMED_IDS = [None, 5, "", "   ", ["result-synth-0001"]]


@pytest.mark.parametrize("embedded", MALFORMED_IDS, ids=repr)
def test_reader_refuses_a_present_malformed_result_id(tmp_path, embedded):
    outcome, _ = read_with(tmp_path, {"schema_version": CAPTURE}, json_bytes({"schema_version": RESULT, "result_id": embedded}))
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL mismatched-result: the result file embeds a malformed result_id" in outcome.stdout


def test_reader_refuses_a_result_id_declared_twice(tmp_path):
    result = b'{"schema_version": "synthetic-result/1.0", "result_id": "other", "result_id": "result-synth-0001"}'
    outcome, _ = read_with(tmp_path, {"schema_version": CAPTURE}, result)
    assert outcome.returncode == 2, outcome.stdout
    assert "declares result_id more than once" in outcome.stdout


def test_control_reader_accepts_matching_and_absent_result_ids(tmp_path):
    matching, _ = read_with(tmp_path / "a", {"schema_version": CAPTURE}, json_bytes({"schema_version": RESULT, "result_id": "result-synth-0001"}))
    assert matching.returncode == 0, matching.stdout
    assert "embedded id 'result-synth-0001' matches" in matching.stdout
    absent, _ = read_with(tmp_path / "b", {"schema_version": CAPTURE}, json_bytes({"schema_version": RESULT}))
    assert absent.returncode == 0, absent.stdout
    assert "bytes-only guarantee" in absent.stdout


@pytest.mark.parametrize("embedded", MALFORMED_IDS, ids=repr)
def test_packer_refuses_a_present_malformed_result_id(repo_fixture, tmp_path, embedded):
    result = repo_fixture / "result/result.json"
    data = json.loads(result.read_text())
    data["result_id"] = embedded
    result.write_text(json.dumps(data))
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL mismatched-result: the result file embeds a malformed result_id" in outcome.stdout
    assert not out.exists()


def test_control_packer_packs_a_result_without_result_id_as_bytes_only(repo_fixture, tmp_path):
    result = repo_fixture / "result/result.json"
    data = json.loads(result.read_text())
    del data["result_id"]
    result.write_text(json.dumps(data))
    out = tmp_path / "out.zip"
    packed = run_packer(repo_fixture, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    assert "bytes-only guarantee" in packed.stdout
    read = run_reader(out)
    assert read.returncode == 0, read.stdout
    assert "bytes-only guarantee" in read.stdout


# Two schema versions, checked independently.


def test_reader_checks_the_result_against_result_schema_version(tmp_path):
    outcome, _ = read_with(tmp_path, {"schema_version": CAPTURE}, json_bytes({"schema_version": CAPTURE, "result_id": "result-synth-0001"}))
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL schema-drift" in outcome.stdout
    assert "result_schema_version is 'synthetic-result/1.0'" in outcome.stdout


def test_reader_checks_capture_inputs_against_capture_schema_version(tmp_path):
    outcome, _ = read_with(tmp_path, {"schema_version": RESULT}, json_bytes({"schema_version": RESULT, "result_id": "result-synth-0001"}))
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL schema-drift" in outcome.stdout
    assert "capture_schema_version is 'synthetic-capture/2026.10'" in outcome.stdout


def test_reader_refuses_a_declared_null_schema_version(tmp_path):
    outcome, _ = read_with(tmp_path, {"schema_version": None}, json_bytes({"schema_version": RESULT}))
    assert outcome.returncode == 2, outcome.stdout
    assert "declares schema_version None" in outcome.stdout


def test_control_reader_accepts_each_file_in_its_own_schema(tmp_path):
    outcome, _ = read_with(tmp_path, {"schema_version": CAPTURE}, json_bytes({"schema_version": RESULT, "result_id": "result-synth-0001"}))
    assert outcome.returncode == 0, outcome.stdout
    assert f"result_schema_version: {RESULT}" in outcome.stdout


def test_packer_checks_the_result_against_result_schema_version(repo_fixture, tmp_path):
    result = repo_fixture / "result/result.json"
    data = json.loads(result.read_text())
    data["schema_version"] = CAPTURE
    result.write_text(json.dumps(data))
    outcome = run_packer(repo_fixture, tmp_path / "out.zip")
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL schema-drift: result/result.json declares schema_version 'synthetic-capture/2026.10'" in outcome.stdout


def test_packer_does_not_schema_check_the_report(repo_fixture, tmp_path):
    """report.md is the bug report text, not a capture input. Text that happens
    to be a JSON object with a schema_version is packed as is."""
    (repo_fixture / "report.md").write_text('{"schema_version": "anything-else/9"}\n')
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert run_reader(out).returncode == 0


# The report is always report.md.


@pytest.mark.parametrize("report_path", ["notes/report.md", None])
def test_packer_refuses_any_report_path_but_report_md(repo_fixture, tmp_path, report_path):
    spec_path = repo_fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    spec["report_path"] = report_path
    spec_path.write_text(json.dumps(spec))
    (repo_fixture / "notes").mkdir()
    shutil.copy(repo_fixture / "report.md", repo_fixture / "notes/report.md")
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL invalid-manifest: report_path: the report is always report.md" in outcome.stdout
    assert not out.exists()


# Reproduction commands survive unusual paths.


def test_reproduction_command_quotes_the_pack_path(tmp_path):
    out = tmp_path / "dir with space $HOME" / "pack 1;x.zip"
    packed = run_packer(EXAMPLE_FIXTURE, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    reread = run_reader(out)
    command = next(line.split(": ", 1)[1] for line in reread.stdout.splitlines() if line.startswith("reproduce: "))
    argv = shlex.split(command)
    assert argv[:2] == ["python", "tools/capture-evidence-pack/read.py"]
    assert argv[2:] == [str(out.resolve())]
    replay = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, cwd=REPO_ROOT)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "verdict: PASS" in replay.stdout


# Refusals record the bytes that were checked and the arguments as given.


def test_refusal_binds_the_pack_when_capture_root_comes_first():
    root = TOOL_DIR / "fixtures/mismatched-result"
    outcome = run_reader("--capture-root", root, EXAMPLE_PACK)
    assert outcome.returncode == 2, outcome.stdout
    data = EXAMPLE_PACK.read_bytes()
    assert f"rejected pack sha256: {sha256_hex(data)}" in outcome.stdout
    assert f"rejected pack bytes: {len(data)}" in outcome.stdout
    assert f"reader arguments: --capture-root {shlex.quote(str(root))} {shlex.quote(str(EXAMPLE_PACK))}" in outcome.stdout


def test_refusal_of_a_missing_pack_says_no_bytes_were_held(tmp_path):
    outcome = run_reader(tmp_path / "absent.zip")
    assert outcome.returncode == 2
    assert "rejected pack sha256: unavailable" in outcome.stdout
    assert "REFUSAL missing-source: pack" in outcome.stdout


# Output paths: fresh directories, atomic replacement, no overwrite on refusal.


def test_packer_creates_a_fresh_nested_output_directory(tmp_path):
    out = tmp_path / "a" / "b" / "c" / "pack.zip"
    outcome = run_packer(EXAMPLE_FIXTURE, out)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert sorted(os.listdir(out.parent)) == ["pack.zip"], "no temporary file is left behind"


def test_refused_pack_leaves_an_existing_output_untouched(tmp_path):
    out = tmp_path / "pack.zip"
    out.write_bytes(b"an earlier pack")
    outcome = run_packer(TOOL_DIR / "fixtures/missing-source", out)
    assert outcome.returncode == 2
    assert out.read_bytes() == b"an earlier pack"
    assert sorted(os.listdir(tmp_path)) == ["pack.zip"]


def test_repacking_to_the_same_path_replaces_it_with_identical_bytes(tmp_path):
    out = tmp_path / "pack.zip"
    assert run_packer(EXAMPLE_FIXTURE, out).returncode == 0
    first = out.read_bytes()
    second = run_packer(EXAMPLE_FIXTURE, out)
    assert second.returncode == 0
    assert out.read_bytes() == first
    assert f"pack sha256: {sha256_hex(first)}" in second.stdout


# Working directory and symlinks.


def test_both_tools_work_from_another_working_directory(tmp_path):
    out = tmp_path / "pack.zip"
    packed = run_packer(EXAMPLE_FIXTURE, out, cwd=tmp_path)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    read = run_reader(out, cwd=tmp_path)
    assert read.returncode == 0, read.stdout + read.stderr


def test_reader_refuses_a_source_that_symlinks_out_of_the_capture_root(tmp_path):
    frame = json_bytes({"schema_version": CAPTURE})
    result = json_bytes({"schema_version": RESULT, "result_id": "result-synth-0001"})
    root = capture_root_with(tmp_path, {"result/result.json": result})
    outside = tmp_path / "outside.json"
    outside.write_bytes(frame)
    (root / "capture").mkdir()
    (root / "capture/frame_0001.json").symlink_to(outside)
    pack = rebuilt_example(tmp_path, lambda manifest: point_manifest_at(manifest, frame, result))
    outcome = run_reader(pack, "--capture-root", root)
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL missing-source: 'capture/frame_0001.json' resolves outside the capture root" in outcome.stdout


def test_packer_refuses_a_source_that_symlinks_out_of_the_fixture(repo_fixture, tmp_path):
    frame = repo_fixture / "capture/frame_0001.json"
    outside = tmp_path / "frame.json"
    outside.write_bytes(frame.read_bytes())
    frame.unlink()
    frame.symlink_to(outside)
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout
    assert "REFUSAL missing-source: capture/frame_0001.json resolves outside" in outcome.stdout
    assert not out.exists()


DEEP = b'{"schema_version": "synthetic-capture/2026.10", "x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"


def test_reader_refuses_a_capture_input_too_deep_to_check(tmp_path):
    root = capture_root_with(tmp_path, {"capture/frame_0001.json": DEEP, "result/result.json": json_bytes({"schema_version": RESULT})})
    pack = rebuilt_example(tmp_path, lambda manifest: point_manifest_at(manifest, DEEP, json_bytes({"schema_version": RESULT})))
    outcome = run_reader(pack, "--capture-root", root)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL schema-drift: 'capture/frame_0001.json' nests too deeply" in outcome.stdout
    assert "Traceback" not in outcome.stderr


def test_packer_refuses_a_capture_input_too_deep_to_check(repo_fixture, tmp_path):
    (repo_fixture / "capture/frame_0001.json").write_bytes(DEEP)
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL schema-drift: capture/frame_0001.json nests too deeply" in outcome.stdout
    assert not out.exists()
