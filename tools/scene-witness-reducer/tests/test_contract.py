"""Contract tests: strict validation, the three-outcome split, and failure modes.

Every test here uses real subprocess runs against tiny inline predicates, so the
exit-code protocol, timeouts, and spawn failures are exercised for real.
"""

import json
import sys
from pathlib import Path

import pytest

from contract import ContractError, Outcome, evaluate, load_contract

BASE_CONTRACT: dict = {
    "contract_version": "1.0",
    "description": "test predicate",
    "executable": sys.executable,
    "args": ["-c", "import sys; sys.exit(0)", "@SCENE"],
    "scene_placeholder": "@SCENE",
    "cwd": ".",
    "outcome": {
        "reproduces_exit_codes": [0],
        "not_reproduces_exit_codes": [1],
        "unknown": "anything else",
    },
    "timeout_seconds": 20,
    "determinism_repeats": 2,
}


def write_contract(tmp_path, **overrides):
    raw = json.loads(json.dumps(BASE_CONTRACT))
    raw.update(overrides)
    path = tmp_path / "predicate.json"
    path.write_text(json.dumps(raw))
    return path


def scene_file(tmp_path, content="{}\n"):
    path = tmp_path / "scene.json"
    path.write_text(content)
    return path


def code_contract(tmp_path, code, timeout=20):
    return write_contract(tmp_path, args=["-c", code, "@SCENE"], timeout_seconds=timeout)


def test_loads_a_valid_contract(tmp_path):
    contract = load_contract(write_contract(tmp_path))
    assert contract.executable == BASE_CONTRACT["executable"]
    assert contract.scene_placeholder == "@SCENE"
    assert contract.reproduces_exit_codes == frozenset({0})
    assert contract.not_reproduces_exit_codes == frozenset({1})
    assert contract.determinism_repeats == 2


def test_rejects_unknown_fields(tmp_path):
    with pytest.raises(ContractError, match="unknown contract fields"):
        load_contract(write_contract(tmp_path, shell="python3 -c 'x'"))


def test_rejects_wrong_version(tmp_path):
    with pytest.raises(ContractError, match="contract_version"):
        load_contract(write_contract(tmp_path, contract_version="2.0"))


def test_rejects_shell_text_in_executable(tmp_path):
    with pytest.raises(ContractError, match="plain program name"):
        load_contract(write_contract(tmp_path, executable="python3 -c 'x'"))


def test_rejects_placeholder_with_whitespace(tmp_path):
    with pytest.raises(ContractError, match="without whitespace"):
        load_contract(write_contract(tmp_path, scene_placeholder="@ SCENE"))


def test_rejects_missing_or_duplicate_placeholder(tmp_path):
    dup = dict(BASE_CONTRACT, args=["-c", "@SCENE", "@SCENE"])
    (tmp_path / "c1.json").write_text(json.dumps(dup))
    with pytest.raises(ContractError, match="exactly one argument"):
        load_contract(tmp_path / "c1.json")
    none = dict(BASE_CONTRACT, args=["-c", "x"])
    (tmp_path / "c2.json").write_text(json.dumps(none))
    with pytest.raises(ContractError, match="exactly one argument"):
        load_contract(tmp_path / "c2.json")


def test_rejects_overlapping_exit_code_sets(tmp_path):
    with pytest.raises(ContractError, match="overlap"):
        load_contract(write_contract(
            tmp_path, outcome={"reproduces_exit_codes": [0, 1],
                               "not_reproduces_exit_codes": [1]}))


def test_rejects_out_of_range_timeout_and_repeats(tmp_path):
    with pytest.raises(ContractError, match="timeout_seconds"):
        load_contract(write_contract(tmp_path, timeout_seconds=0))
    with pytest.raises(ContractError, match="timeout_seconds"):
        load_contract(write_contract(tmp_path, timeout_seconds=601))
    with pytest.raises(ContractError, match="determinism_repeats"):
        load_contract(write_contract(tmp_path, determinism_repeats=0))
    with pytest.raises(ContractError, match="determinism_repeats"):
        load_contract(write_contract(tmp_path, determinism_repeats=11))


def test_rejects_unreadable_or_non_object_contract(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ContractError, match="unreadable"):
        load_contract(tmp_path / "bad.json")
    (tmp_path / "arr.json").write_text("[]")
    with pytest.raises(ContractError, match="JSON object"):
        load_contract(tmp_path / "arr.json")


def test_evaluate_maps_declared_exit_codes(tmp_path):
    contract = load_contract(code_contract(tmp_path, "import sys; sys.exit(0)"))
    assert evaluate(contract, scene_file(tmp_path), tmp_path).outcome is Outcome.REPRODUCES
    contract = load_contract(code_contract(tmp_path, "import sys; sys.exit(1)"))
    assert evaluate(contract, scene_file(tmp_path), tmp_path).outcome is Outcome.NOT_REPRODUCES


def test_undeclared_exit_code_is_unknown(tmp_path):
    contract = load_contract(code_contract(tmp_path, "import sys; sys.exit(2)"))
    ev = evaluate(contract, scene_file(tmp_path), tmp_path)
    assert ev.outcome is Outcome.UNKNOWN
    assert "undeclared exit code 2" in ev.unknown_reason


def test_signal_death_is_unknown_with_signal_name(tmp_path):
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    contract = load_contract(code_contract(tmp_path, code))
    ev = evaluate(contract, scene_file(tmp_path), tmp_path)
    assert ev.outcome is Outcome.UNKNOWN
    assert ev.signal_name == "SIGTERM"


def test_spawn_failure_is_unknown(tmp_path):
    contract = load_contract(
        write_contract(tmp_path, executable="definitely-not-a-real-program-xyz"))
    ev = evaluate(contract, scene_file(tmp_path), tmp_path)
    assert ev.outcome is Outcome.UNKNOWN
    assert ev.spawn_error


def test_timeout_is_unknown_and_kills_the_child(tmp_path):
    contract = load_contract(code_contract(tmp_path, "import time; time.sleep(30)", timeout=2))
    ev = evaluate(contract, scene_file(tmp_path), tmp_path)
    assert ev.outcome is Outcome.UNKNOWN
    assert ev.timed_out and "timeout" in ev.unknown_reason


def test_transcript_has_no_timing_information(tmp_path):
    contract = load_contract(code_contract(tmp_path, "print('out'); import sys; sys.exit(0)"))
    record = evaluate(contract, scene_file(tmp_path), tmp_path).transcript()
    assert record["stdout"] == "out\n"
    assert "duration" not in json.dumps(record)


def test_script_arg_resolves_relative_to_contract_file(tmp_path):
    """A path arg before the placeholder resolves against the contract file's own
    directory, so the same contract works from any working directory."""
    preds = tmp_path / "preds"
    preds.mkdir()
    (preds / "helper.py").write_text("import sys; sys.exit(0)\n")
    contract_path = write_contract(preds, args=["helper.py", "@SCENE"])
    contract = load_contract(contract_path)
    assert contract.args[0] == str((preds / "helper.py").resolve())
    evaluation = evaluate(contract, scene_file(tmp_path), cwd=tmp_path)
    assert evaluation.outcome is Outcome.REPRODUCES


def test_arg_that_names_no_contract_relative_file_is_left_alone(tmp_path):
    contract = load_contract(
        write_contract(tmp_path, args=["-c", "import sys; sys.exit(0)", "@SCENE"]))
    assert contract.args[0] == "-c"
