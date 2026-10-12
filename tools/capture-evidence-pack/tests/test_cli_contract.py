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
import read
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


# Repeated keys in referenced JSON are declarations, never "last one wins".
# Both tools refuse them; a file that is not JSON at all stays opaque.

DUPLICATE_RESULT_ID = b'{"schema_version": "synthetic-result/1.0", "result_id": "other", "result_id": "result-synth-0001"}\n'
DUPLICATE_RESULT_SCHEMA = b'{"schema_version": "synthetic-capture/2026.10", "schema_version": "synthetic-result/1.0", "result_id": "result-synth-0001"}\n'
DUPLICATE_CAPTURE_SCHEMA = b'{"schema_version": "synthetic-result/1.0", "schema_version": "synthetic-capture/2026.10", "frame_id": "frame-0001"}\n'
NAN_OTHER_RESULT = b'{"schema_version": "synthetic-result/1.0", "result_id": "other", "value": NaN}\n'
OPAQUE_RESULT = b'{"schema_version": "synthetic-result/1.0", "result_id": "other", trailing garbage\n'


@pytest.mark.parametrize(
    "relative,data,kind,needle",
    [
        ("result/result.json", DUPLICATE_RESULT_ID, "mismatched-result", "declares result_id more than once"),
        ("result/result.json", DUPLICATE_RESULT_SCHEMA, "schema-drift", "declares schema_version more than once"),
        ("capture/frame_0001.json", DUPLICATE_CAPTURE_SCHEMA, "schema-drift", "declares schema_version more than once"),
        ("result/result.json", NAN_OTHER_RESULT, "mismatched-result", "'other'"),
    ],
    ids=["result-id-twice", "result-schema-twice", "capture-schema-twice", "nan-does-not-hide-result-id"],
)
def test_packer_refuses_ambiguous_declarations(repo_fixture, tmp_path, relative, data, kind, needle):
    (repo_fixture / relative).write_bytes(data)
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert f"REFUSAL {kind}:" in outcome.stdout and needle in outcome.stdout, outcome.stdout
    assert not out.exists()


@pytest.mark.parametrize(
    "frame,result,kind,needle",
    [
        (json_bytes({"schema_version": CAPTURE}), DUPLICATE_RESULT_SCHEMA, "schema-drift", "declares schema_version more than once"),
        (DUPLICATE_CAPTURE_SCHEMA, json_bytes({"schema_version": RESULT}), "schema-drift", "declares schema_version more than once"),
        (json_bytes({"schema_version": CAPTURE}), NAN_OTHER_RESULT, "mismatched-result", "'other'"),
    ],
    ids=["result-schema-twice", "capture-schema-twice", "nan-does-not-hide-result-id"],
)
def test_reader_refuses_ambiguous_declarations(tmp_path, frame, result, kind, needle):
    root = capture_root_with(tmp_path, {"capture/frame_0001.json": frame, "result/result.json": result})
    pack = rebuilt_example(tmp_path, lambda manifest: point_manifest_at(manifest, frame, result))
    outcome = run_reader(pack, "--capture-root", root)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert f"REFUSAL {kind}:" in outcome.stdout and needle in outcome.stdout, outcome.stdout


def test_control_both_tools_treat_a_file_that_is_not_json_as_opaque(repo_fixture, tmp_path):
    """The documented narrower guarantee: text that is not a JSON object makes
    no declaration, whatever it looks like, in both tools alike."""
    (repo_fixture / "result/result.json").write_bytes(OPAQUE_RESULT)
    out = tmp_path / "out.zip"
    packed = run_packer(repo_fixture, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    assert "bytes-only guarantee" in packed.stdout
    read = run_reader(out)
    assert read.returncode == 0, read.stdout
    assert "bytes-only guarantee" in read.stdout


# Paths with NUL or other control characters are refused by name, never a traceback.

CONTROL_PATHS = ["capture/frame\x00.json", "capture/frame\x1f.json", "capture/frame\x7f.json", "capture/\x85frame.json"]


@pytest.mark.parametrize("bad", CONTROL_PATHS, ids=["nul", "unit-separator", "delete", "next-line"])
def test_packer_refuses_control_characters_in_paths(repo_fixture, tmp_path, bad):
    spec_path = repo_fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    spec["source_refs"][0]["path"] = bad
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest: source_refs[0].path: not a relative POSIX path" in outcome.stdout
    assert "Traceback" not in outcome.stderr
    assert not out.exists()


def with_path(field: str, bad: str):
    def mutate(manifest):
        if field == "source":
            manifest["capture"]["source_refs"][0]["path"] = bad
        elif field == "result":
            manifest["result"]["path"] = bad
        else:
            manifest["capture"]["capture_root"] = bad

    return mutate


@pytest.mark.parametrize("field", ["source", "result", "capture_root"])
@pytest.mark.parametrize("bad", CONTROL_PATHS, ids=["nul", "unit-separator", "delete", "next-line"])
def test_reader_refuses_control_characters_in_paths(tmp_path, field, bad):
    pack = rebuilt_example(tmp_path, with_path(field, bad))
    outcome = run_reader(pack)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest:" in outcome.stdout and "not a relative POSIX path" in outcome.stdout, outcome.stdout
    assert "Traceback" not in outcome.stderr


# Read failures name the declared path and the OS's category, never the
# absolute path the OS reported.


@pytest.fixture
def unreadable_frame(repo_fixture):
    frame = repo_fixture / "capture/frame_0001.json"
    yield frame
    frame.chmod(0o644)


def assert_no_absolute_paths(outcome_text: str):
    assert str(REPO_ROOT) not in outcome_text, outcome_text
    assert "/Users/" not in outcome_text and "/private/" not in outcome_text, outcome_text


def test_packer_read_failure_names_the_declared_path_only(unreadable_frame, repo_fixture, tmp_path):
    unreadable_frame.chmod(0)
    outcome = run_packer(repo_fixture, tmp_path / "out.zip")
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL missing-source: 'capture/frame_0001.json' cannot be read: Permission denied" in outcome.stdout
    assert_no_absolute_paths(outcome.stdout + outcome.stderr)


def test_reader_read_failure_names_the_declared_path_only(unreadable_frame, repo_fixture, tmp_path):
    pack = tmp_path / "pack.zip"
    assert run_packer(repo_fixture, pack).returncode == 0
    unreadable_frame.chmod(0)
    outcome = run_reader(pack)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL missing-source: 'capture/frame_0001.json' cannot be read: Permission denied" in outcome.stdout
    lines = [line for line in outcome.stdout.splitlines() if not line.startswith("reader arguments:")]
    assert_no_absolute_paths("\n".join(lines) + outcome.stderr)


def test_reader_value_error_on_open_is_a_fixed_message():
    with pytest.raises(read.Refusal) as caught:
        read.read_regular_file(REPO_ROOT / "not\x00a-path", 10, "'not-a-path'")
    assert caught.value.kind == "missing-source"
    assert caught.value.detail == "'not-a-path' cannot be read: the path cannot be passed to the operating system"


# Unpaired surrogates are not text: refused in every declared path and string,
# while legitimate non-ASCII names keep working.

SURROGATES = ["\ud800", "\udfff"]


@pytest.mark.parametrize("surrogate", SURROGATES, ids=["high", "low"])
@pytest.mark.parametrize("field", ["source-path", "result-path", "capture-id"])
def test_packer_refuses_unpaired_surrogates(repo_fixture, tmp_path, surrogate, field):
    spec_path = repo_fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    if field == "source-path":
        spec["source_refs"][0]["path"] = f"capture/frame{surrogate}.json"
    elif field == "result-path":
        spec["result_path"] = f"result/result{surrogate}.json"
    else:
        spec["capture_id"] = f"capture{surrogate}"
    spec_path.write_text(json.dumps(spec))  # ensure_ascii writes the surrogate as an escape
    out = tmp_path / "out.zip"
    outcome = run_packer(repo_fixture, out)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest:" in outcome.stdout, outcome.stdout
    assert "Traceback" not in outcome.stderr
    assert not out.exists()


def with_text(field: str, surrogate: str):
    def mutate(manifest):
        if field == "capture_root":
            manifest["capture"]["capture_root"] = f"tools/root{surrogate}"
        elif field == "source":
            manifest["capture"]["source_refs"][0]["path"] = f"capture/frame{surrogate}.json"
        elif field == "result":
            manifest["result"]["path"] = f"result/result{surrogate}.json"
        elif field == "capture_id":
            manifest["capture"]["capture_id"] = f"capture{surrogate}"
        else:
            manifest["missing_fields"][0]["detail"] = f"gap{surrogate}"

    return mutate


@pytest.mark.parametrize("surrogate", SURROGATES, ids=["high", "low"])
@pytest.mark.parametrize("field", ["capture_root", "source", "result", "capture_id", "missing-detail"])
def test_reader_refuses_unpaired_surrogates(tmp_path, surrogate, field):
    pack = rebuilt_example(tmp_path, with_text(field, surrogate))
    outcome = run_reader(pack)
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert "REFUSAL invalid-manifest:" in outcome.stdout, outcome.stdout
    assert "Traceback" not in outcome.stderr


NON_ASCII = "capture/främé_кадр_幀.json"


def test_control_reader_accepts_legitimate_non_ascii_paths(tmp_path):
    frame = json_bytes({"schema_version": CAPTURE})
    result = json_bytes({"schema_version": RESULT, "result_id": "result-synth-0001"})
    root = capture_root_with(tmp_path, {NON_ASCII: frame, "result/result.json": result})

    def mutate(manifest):
        point_manifest_at(manifest, frame, result)
        manifest["capture"]["source_refs"][0]["path"] = NON_ASCII

    outcome = run_reader(rebuilt_example(tmp_path, mutate), "--capture-root", root)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert f"source ref ok: frame:{NON_ASCII}" in outcome.stdout


def test_control_packer_packs_legitimate_non_ascii_paths(repo_fixture, tmp_path):
    (repo_fixture / "capture/frame_0001.json").rename(repo_fixture / NON_ASCII)
    spec_path = repo_fixture / "pack-spec.json"
    spec = json.loads(spec_path.read_text())
    spec["source_refs"][0]["path"] = NON_ASCII
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.zip"
    packed = run_packer(repo_fixture, out)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    read = run_reader(out)
    assert read.returncode == 0, read.stdout + read.stderr


# Expected filesystem failures on the paths a caller passes (the pack, the
# fixture directory) are named refusals that print the path as given, never a
# traceback carrying the machine path. The locked directory belongs to the
# test and its permissions are restored in a finally block.


@pytest.fixture
def locked(tmp_path):
    """tmp_path/locked holds a copy of the example pack and fixture, then loses
    its read and search permission for the duration of the test."""
    directory = tmp_path / "locked"
    directory.mkdir()
    shutil.copy(EXAMPLE_PACK, directory / "pack.zip")
    shutil.copytree(EXAMPLE_FIXTURE, directory / "fixture")
    directory.chmod(0)
    try:
        yield directory
    finally:
        directory.chmod(0o755)


def assert_clean_refusal(outcome, kind: str, needle: str, tmp_path):
    assert outcome.returncode == 2, outcome.stdout + outcome.stderr
    assert f"REFUSAL {kind}: {needle}" in outcome.stdout, outcome.stdout
    assert "Traceback" not in outcome.stderr, outcome.stderr
    assert str(tmp_path) not in outcome.stdout + outcome.stderr
    assert str(tmp_path.resolve()) not in outcome.stdout + outcome.stderr


def test_reader_refuses_a_pack_under_an_inaccessible_directory(locked, tmp_path):
    outcome = run_reader("locked/pack.zip", cwd=tmp_path)
    assert_clean_refusal(outcome, "missing-source", "pack locked/pack.zip cannot be read: Permission denied", tmp_path)
    assert "rejected pack sha256: unavailable" in outcome.stdout


def test_packer_refuses_a_fixture_under_an_inaccessible_directory(locked, tmp_path):
    outcome = run_packer("locked/fixture", tmp_path / "out.zip", cwd=tmp_path)
    assert_clean_refusal(outcome, "missing-source", "fixture directory locked/fixture cannot be read: Permission denied", tmp_path)
    assert not (tmp_path / "out.zip").exists()


@pytest.mark.parametrize(
    "setup,needle",
    [
        ("missing", "pack absent.zip cannot be read: No such file or directory"),
        ("dangling-symlink", "pack absent.zip cannot be read: No such file or directory"),
        ("directory", "pack absent.zip cannot be read: Is a directory"),
    ],
)
def test_reader_names_each_unopenable_pack(tmp_path, setup, needle):
    if setup == "dangling-symlink":
        (tmp_path / "absent.zip").symlink_to(tmp_path / "nowhere.zip")
    elif setup == "directory":
        (tmp_path / "absent.zip").mkdir()
    outcome = run_reader("absent.zip", cwd=tmp_path)
    assert_clean_refusal(outcome, "missing-source", needle, tmp_path)


@pytest.mark.parametrize(
    "setup,needle",
    [
        ("missing", "fixture directory absent cannot be read: No such file or directory"),
        ("file", "fixture absent is not a directory"),
    ],
)
def test_packer_names_each_unusable_fixture(tmp_path, setup, needle):
    if setup == "file":
        (tmp_path / "absent").write_text("not a directory")
    outcome = run_packer("absent", tmp_path / "out.zip", cwd=tmp_path)
    assert_clean_refusal(outcome, "missing-source", needle, tmp_path)


def test_control_reader_accepts_a_relative_and_a_symlinked_pack(tmp_path):
    shutil.copy(EXAMPLE_PACK, tmp_path / "pack.zip")
    (tmp_path / "link.zip").symlink_to(tmp_path / "pack.zip")
    for name in ("pack.zip", "link.zip"):
        outcome = run_reader(name, cwd=tmp_path)
        assert outcome.returncode == 0, outcome.stdout + outcome.stderr
        assert f"pack sha256: {sha256_hex(EXAMPLE_PACK.read_bytes())}" in outcome.stdout


def test_control_packer_accepts_a_symlinked_fixture_directory(tmp_path):
    (tmp_path / "fixture").symlink_to(EXAMPLE_FIXTURE, target_is_directory=True)
    out = tmp_path / "out.zip"
    outcome = run_packer("fixture", out, cwd=tmp_path)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert run_reader(out).returncode == 0
