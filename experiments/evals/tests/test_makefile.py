"""The field recipe resolves capture identity once and preserves output overrides."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def run_make(tmp_path, target, override=None, lookup="success", check=True):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.jsonl"
    uv = binary / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "log = Path(os.environ['COMMAND_LOG'])\n"
        "with log.open('a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "if args[:5] == ['run', 'python', '-m', 'evals.field', 'result-dir']:\n"
        "    mode = os.environ['LOOKUP_MODE']\n"
        "    if mode in ['success', 'failed-with-path']:\n"
        "        print(os.environ['DEFAULT_OUT'])\n"
        "    elif mode == 'whitespace':\n"
        "        print('   ')\n"
        "    if mode in ['failed', 'failed-with-path']:\n"
        "        sys.exit(1)\n"
        "elif args[:5] == ['run', 'python', '-m', 'evals.field', 'prepare']:\n"
        "    print(os.environ['WORK_DIR'])\n"
    )
    uv.chmod(0o755)
    scoring = tmp_path / "scoring"
    scoring.mkdir()
    args = [
        "make",
        "-f",
        str(MAKEFILE),
        target,
        "SESSION=session.zip",
        "TRUTH=survey.json",
        "MAP=map.json",
        f"SCORING={scoring}",
    ]
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DEFAULT_OUT": str(tmp_path / "default"),
        "WORK_DIR": str(tmp_path / "work"),
        "LOOKUP_MODE": lookup,
    }
    env.pop("FIELD_OUT", None)
    if override == "command-line":
        args.append(f"FIELD_OUT={tmp_path / 'override'}")
    elif override == "environment":
        env["FIELD_OUT"] = str(tmp_path / "override")
    elif override == "empty":
        args.append("FIELD_OUT=")
    result = subprocess.run(
        args, cwd=tmp_path, env=env, check=check, capture_output=True, text=True
    )
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


@pytest.mark.parametrize("override", [None, "command-line", "environment"])
def test_field_output_lookup_runs_once_unless_overridden(tmp_path, override):
    _result, commands = run_make(tmp_path, "field", override)
    lookups = [
        args
        for args in commands
        if args[:5] == ["run", "python", "-m", "evals.field", "result-dir"]
    ]
    assert len(lookups) == (1 if override is None else 0)
    output = str(tmp_path / ("default" if override is None else "override"))
    for step in ("stamp", "score"):
        [args] = [
            args for args in commands if args[:5] == ["run", "python", "-m", "evals.field", step]
        ]
        assert args[args.index("--out-dir") + 1] == output
    [import_args] = [
        args for args in commands if args[:3] == ["run", "score", "import-measure-lab"]
    ]
    assert import_args[import_args.index("--map") + 1] == output + "/inputs/map.json"
    assert import_args[import_args.index("--out") + 1] == output + "/measure-lab.json"
    [score_args] = [args for args in commands if args[:3] == ["run", "score", "--rules"]]
    assert score_args[score_args.index("--out") + 1] == output + "/scored"


def test_unrelated_target_does_not_resolve_a_capture(tmp_path):
    _result, commands = run_make(tmp_path, "test")
    assert not any(args[:4] == ["run", "python", "-m", "evals.field"] for args in commands)


@pytest.mark.parametrize("lookup", ["failed", "failed-with-path", "empty", "whitespace"])
def test_failed_or_empty_lookup_stops_before_field_work(tmp_path, lookup):
    report = tmp_path / "field_report.md"
    report.write_bytes(b"previous report")
    result, commands = run_make(tmp_path, "field", lookup=lookup, check=False)
    assert result.returncode != 0
    assert "FIELD_OUT" in result.stderr
    assert commands == [["run", "python", "-m", "evals.field", "result-dir", "session.zip"]]
    assert report.read_bytes() == b"previous report"


def test_explicit_empty_output_stops_without_a_lookup(tmp_path):
    result, commands = run_make(tmp_path, "field", override="empty", check=False)
    assert result.returncode != 0
    assert "FIELD_OUT" in result.stderr
    assert commands == []
