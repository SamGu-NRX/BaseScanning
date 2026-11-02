"""One read per input. If a tool opens a file a second time, that second open
is the moment a concurrent writer could hand it different bytes. These tests
change the file on disk at exactly that moment and check that nothing the
tool reports or writes came from the changed bytes.

They run the tools' main functions in this process so that the open calls
can be observed."""

import builtins
import io
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
from support import (
    EXAMPLE_PACK,
    TOOL_DIR,
    copy_fixture_into_repo,
    run_reader,
    sha256_hex,
)


@pytest.fixture
def repo_fixture():
    path = copy_fixture_into_repo("snapshot")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def watch_opens(monkeypatch, watched: dict[Path, bytes]):
    """Count read opens of each watched path, through open() and os.open().
    On the second open of one, replace its contents with the given bytes
    before the open proceeds."""
    real_open = io.open
    real_os_open = os.open
    counts: Counter = Counter()
    targets = {path.resolve(): replacement for path, replacement in watched.items()}

    def note(file) -> None:
        path = Path(os.fspath(file)).resolve()
        if path in targets:
            counts[path] += 1
            if counts[path] == 2:
                with real_open(path, "wb") as handle:
                    handle.write(targets[path])

    def tracking_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and "r" in mode:
            note(file)
        return real_open(file, mode, *args, **kwargs)

    def tracking_os_open(file, flags, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and flags & os.O_ACCMODE == os.O_RDONLY:
            note(file)
        return real_os_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)
    monkeypatch.setattr(io, "open", tracking_open)
    monkeypatch.setattr(os, "open", tracking_os_open)
    return counts


def run_main(module_name: str, argv: list[str], monkeypatch) -> int:
    monkeypatch.syspath_prepend(str(TOOL_DIR))
    sys.modules.pop(module_name, None)
    module = __import__(module_name)
    monkeypatch.setattr(sys, "argv", [f"{module_name}.py", *argv])
    try:
        code = module.main()
    except SystemExit as exit_:
        code = exit_.code
    return 0 if code is None else code


def test_packer_reads_each_input_once_and_packs_what_it_hashed(repo_fixture, tmp_path, monkeypatch):
    inputs = [repo_fixture / "report.md", repo_fixture / "result/result.json", *sorted((repo_fixture / "capture").glob("*.json"))]
    originals = {path: path.read_bytes() for path in inputs}
    counts = watch_opens(monkeypatch, {path: data + b"\n changed between reads\n" for path, data in originals.items()})
    out = tmp_path / "pack.zip"

    code = run_main("pack", ["--fixture", str(repo_fixture), "--out", str(out)], monkeypatch)
    monkeypatch.undo()

    assert code == 0
    assert {path.name: counts[path.resolve()] for path in inputs} == {path.name: 1 for path in inputs}
    for path, data in originals.items():
        assert path.read_bytes() == data, f"{path.name} was opened twice and changed under the packer"
    outcome = run_reader(out)
    assert outcome.returncode == 0, outcome.stdout


def test_reader_reports_the_bytes_it_checked_on_pass(tmp_path, monkeypatch, capsys):
    pack = tmp_path / "pack.zip"
    pack.write_bytes(EXAMPLE_PACK.read_bytes())
    original = pack.read_bytes()
    counts = watch_opens(monkeypatch, {pack: b"not the pack that was checked"})

    code = run_main("read", [str(pack)], monkeypatch)
    out = capsys.readouterr().out
    monkeypatch.undo()

    assert code == 0, out
    assert counts[pack.resolve()] == 1
    assert f"pack sha256: {sha256_hex(original)}" in out


def test_reader_refusal_names_the_bytes_it_checked(tmp_path, monkeypatch, capsys):
    pack = tmp_path / "pack.zip"
    pack.write_bytes(EXAMPLE_PACK.read_bytes())
    original = pack.read_bytes()
    counts = watch_opens(monkeypatch, {pack: b"not the pack that was checked"})

    code = run_main("read", [str(pack), "--capture-root", str(TOOL_DIR / "fixtures/mismatched-result")], monkeypatch)
    out = capsys.readouterr().out
    monkeypatch.undo()

    assert code == 2, out
    assert counts[pack.resolve()] == 1
    assert f"rejected pack sha256: {sha256_hex(original)}" in out


def test_reader_checks_identity_on_the_bytes_it_hashed(tmp_path, monkeypatch, capsys):
    """The result is hashed and parsed from one read. A second read could
    return a file whose embedded result_id names another result."""
    root = tmp_path / "root"
    shutil.copytree(TOOL_DIR / "fixtures/example", root)
    result = root / "result/result.json"
    swapped = json.loads(result.read_text())
    swapped["result_id"] = "result-synth-0002"
    counts = watch_opens(monkeypatch, {result: json.dumps(swapped).encode()})

    code = run_main("read", [str(EXAMPLE_PACK), "--capture-root", str(root)], monkeypatch)
    out = capsys.readouterr().out
    monkeypatch.undo()

    assert code == 0, out
    assert counts[result.resolve()] == 1
    assert "embedded id 'result-synth-0001' matches" in out
