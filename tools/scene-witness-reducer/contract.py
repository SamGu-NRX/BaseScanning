"""Executable predicate contracts for scene-witness reduction.

A contract declares how to run a predicate program once on one scene file: a plain
executable name, an argv list with exactly one scene placeholder, and an exit-code
protocol. `evaluate` runs it and classifies the result into exactly three outcomes:

- REPRODUCES      — an exit code listed under `reproduces_exit_codes`
- NOT_REPRODUCES  — an exit code listed under `not_reproduces_exit_codes`
- UNKNOWN         — anything else: undeclared exit codes, signals, timeouts, spawn
                    failures. Unknown never counts as a refusal and never as a
                    reproduction; the reducer treats it as "this candidate told us
                    nothing".

There is no shell anywhere: the contract is argv, passed to subprocess with
`shell=False`. Contracts are plain JSON, validated strictly so a typo fails loudly
instead of silently changing what runs.
"""

from __future__ import annotations

import enum
import json
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

_CONTRACT_VERSION = "1.0"
_MAX_TIMEOUT_S = 600
_ALLOWED_KEYS = {
    "contract_version", "description", "executable", "args", "scene_placeholder",
    "cwd", "outcome", "timeout_seconds", "determinism_repeats",
}
_SHELL_CHARS = set("|&;<>()$`\\\"'*?~#")


class ContractError(ValueError):
    """The contract file is malformed or unsafe."""


@dataclass(frozen=True)
class Contract:
    executable: str
    args: tuple[str, ...]
    scene_placeholder: str
    reproduces_exit_codes: frozenset[int]
    not_reproduces_exit_codes: frozenset[int]
    timeout_seconds: float
    determinism_repeats: int
    cwd: str
    description: str = ""
    path: str = ""

    def argv_for(self, scene_path: str | Path) -> list[str]:
        """Full argv for one run: the executable, then the args with the placeholder
        argument replaced by the scene path."""
        return [self.executable] + [
            scene_path if a == self.scene_placeholder else a for a in self.args
        ]


class Outcome(enum.Enum):
    REPRODUCES = "reproduces"
    NOT_REPRODUCES = "not_reproduces"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Evaluation:
    """One classified predicate run. `transcript()` is the record kept in evidence:
    everything the run did, and never how long it took."""
    outcome: Outcome
    exit_code: int | None
    signal_name: str | None
    timed_out: bool
    stdout: str
    stderr: str
    unknown_reason: str | None
    spawn_error: str | None

    def transcript(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "signal": self.signal_name,
            "timed_out": self.timed_out,
            "unknown_reason": self.unknown_reason,
            "spawn_error": self.spawn_error,
        }


def _check_exit_codes(raw: object, field: str) -> list[int]:
    if raw is None:
        return []
    if (not isinstance(raw, list) or not raw
            or not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c < 256
                       for c in raw)):
        raise ContractError(f"{field} must be a nonempty list of exit codes 0..255")
    return list(dict.fromkeys(raw))


def load_contract(path: Path) -> Contract:
    """Parse and validate a contract file. Unknown fields are rejected, so a typo in
    the contract fails loudly instead of silently changing what runs."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: unreadable contract ({exc})") from exc
    _require(isinstance(raw, dict), f"{path}: contract must be a JSON object")
    assert isinstance(raw, dict)
    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    _require(not unknown, f"{path}: unknown contract fields {unknown}")
    _require(raw.get("contract_version") == _CONTRACT_VERSION,
             f"{path}: contract_version must be {_CONTRACT_VERSION!r}, "
             f"saw {raw.get('contract_version')!r}")

    executable = raw.get("executable")
    _require(isinstance(executable, str) and executable, f"{path}: executable must be a name")
    assert isinstance(executable, str)
    _require(not (set(executable) & _SHELL_CHARS),
             f"{path}: executable {executable!r} must be a plain program name, not shell text")

    placeholder = raw.get("scene_placeholder")
    _require(isinstance(placeholder, str) and placeholder,
             f"{path}: scene_placeholder must be a nonempty string")
    assert isinstance(placeholder, str)
    _require(not (set(placeholder) & set(" \t\r\n")),
             f"{path}: scene_placeholder must be one argument, without whitespace")

    args = raw.get("args")
    _require(isinstance(args, list) and args and all(isinstance(a, str) for a in args),
             f"{path}: args must be a nonempty list of strings (argv, never shell text)")
    assert isinstance(args, list) and all(isinstance(a, str) for a in args)
    hits = [i for i, a in enumerate(args) if a == placeholder]
    _require(len(hits) == 1,
             f"{path}: exactly one argument must equal the scene placeholder {placeholder!r}")
    _require(all(placeholder not in a for i, a in enumerate(args) if i not in hits),
             f"{path}: no other argument may contain the placeholder {placeholder!r}")

    # Path-like arguments before the placeholder (typically the predicate script) resolve
    # against the contract file's own directory, so a contract works from any working
    # directory. Args that name no file there are left untouched (e.g. "-c").
    contract_dir = path.resolve().parent
    first = hits[0]
    args = [
        str((contract_dir / a).resolve())
        if i < first and not Path(a).is_absolute() and (contract_dir / a).is_file()
        else a
        for i, a in enumerate(args)
    ]

    outcome = raw.get("outcome")
    _require(isinstance(outcome, dict), f"{path}: outcome must be an object")
    assert isinstance(outcome, dict)
    reproduces = frozenset(_check_exit_codes(outcome.get("reproduces_exit_codes", None),
                                              "reproduces_exit_codes"))
    not_reproduces = frozenset(_check_exit_codes(outcome.get("not_reproduces_exit_codes", None),
                                                  "not_reproduces_exit_codes"))
    _require(reproduces or not_reproduces,
             f"{path}: outcome must declare at least one of reproduces_exit_codes, "
             "not_reproduces_exit_codes")
    _require(not (reproduces & not_reproduces),
             f"{path}: outcome exit-code sets overlap: {sorted(reproduces & not_reproduces)}")

    timeout = raw.get("timeout_seconds")
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
             and 0 < timeout <= _MAX_TIMEOUT_S,
             f"{path}: timeout_seconds must be a number in (0, {_MAX_TIMEOUT_S}]")

    repeats = raw.get("determinism_repeats")
    _require(isinstance(repeats, int) and not isinstance(repeats, bool) and 1 <= repeats <= 10,
             f"{path}: determinism_repeats must be an integer in 1..10")

    cwd = raw.get("cwd", ".")
    _require(isinstance(cwd, str) and cwd and not cwd.startswith("/")
             and ".." not in Path(cwd).parts,
             f"{path}: cwd must be a relative path without '..' (resolved against the "
             "reducer's working directory)")

    return Contract(
        executable=executable,
        args=tuple(args),
        scene_placeholder=placeholder,
        reproduces_exit_codes=reproduces,
        not_reproduces_exit_codes=not_reproduces,
        timeout_seconds=float(timeout),
        determinism_repeats=repeats,
        cwd=cwd,
        description=raw.get("description", ""),
        path=str(path),
    )


def evaluate(contract: Contract, scene_path: str | Path, cwd: Path) -> Evaluation:
    """Run the predicate on one scene file and classify the result. Never raises for
    anything the predicate or the environment does: every failure mode becomes an
    explicit outcome."""
    argv = contract.argv_for(scene_path)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=contract.timeout_seconds,
            cwd=str(cwd),
            shell=False,  # explicit: the contract is argv, never shell text
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed the child and waited for it.
        return Evaluation(
            outcome=Outcome.UNKNOWN,
            exit_code=None,
            signal_name=None,
            timed_out=True,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            unknown_reason=(f"timeout: the predicate exceeded "
                            f"{contract.timeout_seconds}s and was killed"),
            spawn_error=None,
        )
    except OSError as exc:
        return Evaluation(
            outcome=Outcome.UNKNOWN,
            exit_code=None,
            signal_name=None,
            timed_out=False,
            stdout="",
            stderr="",
            unknown_reason=f"spawn failed: {exc}",
            spawn_error=str(exc),
        )

    if proc.returncode < 0:  # negative return code: the child died to a signal
        name = signal.Signals(-proc.returncode).name
        return Evaluation(
            outcome=Outcome.UNKNOWN,
            exit_code=None,
            signal_name=name,
            timed_out=False,
            stdout=_decode(proc.stdout),
            stderr=_decode(proc.stderr),
            unknown_reason=f"terminated by signal {name}",
            spawn_error=None,
        )

    code = proc.returncode
    if code in contract.reproduces_exit_codes:
        outcome = Outcome.REPRODUCES
    elif code in contract.not_reproduces_exit_codes:
        outcome = Outcome.NOT_REPRODUCES
    else:
        outcome = Outcome.UNKNOWN
    unknown_reason = None
    if outcome is Outcome.UNKNOWN:
        declared = sorted(contract.reproduces_exit_codes | contract.not_reproduces_exit_codes)
        unknown_reason = f"undeclared exit code {code}; declared codes are {declared}"
    return Evaluation(
        outcome=outcome,
        exit_code=code,
        signal_name=None,
        timed_out=False,
        stdout=_decode(proc.stdout),
        stderr=_decode(proc.stderr),
        unknown_reason=unknown_reason,
        spawn_error=None,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)
