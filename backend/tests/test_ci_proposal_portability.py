"""Regressions for the proposed backend CI job and its local mirror.

Pins the two PR #288 review findings:

- The proposed workflow must not gate on `make hygiene`, which is known red
  on alpha (the tracked synthetic-meter.tar is denied by the repo's own
  hygiene rules). The step runs only in a job with continue-on-error: true.
- backend/scripts/ci_local.sh must time its steps without GNU date's %N,
  which macOS/BSD date does not support; the script has to keep working there.
- backend/scripts/ci_local.sh's exit semantics, exercised end to end with
  stubbed uv and make first on PATH (Greptile P2): a failed gating step stops
  the default run and skips the later steps, --keep-going runs the later
  steps and still exits nonzero, and a hygiene-only failure exits zero and
  says so. Without these, a change that reports success after a required
  check fails would still pass the suite.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
PROPOSAL = BACKEND / "docs" / "ci" / "backend.yml.proposed"
CI_LOCAL = BACKEND / "scripts" / "ci_local.sh"

BSD_DATE_SHIM = """\
#!/bin/sh
# A BSD-like date stub: %s works, %N is not a conversion and prints
# literally. Absolute binary paths, so it works with an empty PATH.
fmt="${1:-}"
case "$fmt" in
  +*) ;;
  *) exec /usr/bin/date "$@" ;;
esac
epoch=$(/usr/bin/date +%s)
body="${fmt#+}"
printf '%s\\n' "$(printf '%s' "$body" | /usr/bin/sed "s/%s/$epoch/")"
"""


def _workflow_jobs():
    workflow = yaml.safe_load(PROPOSAL.read_text())
    assert workflow and "jobs" in workflow, "backend.yml.proposed must define jobs"
    return workflow["jobs"]


def _jobs_running_hygiene(jobs):
    return [
        job_id
        for job_id, job in jobs.items()
        if any("make hygiene" in (step.get("run") or "") for step in job.get("steps") or [])
    ]


def test_hygiene_step_cannot_fail_a_required_job():
    """The known-red hygiene step runs only under continue-on-error: true."""
    jobs = _workflow_jobs()
    hygiene_jobs = _jobs_running_hygiene(jobs)
    assert hygiene_jobs, "the workflow must keep running make hygiene somewhere"
    for job_id in hygiene_jobs:
        assert jobs[job_id].get("continue-on-error") is True, (
            f"job {job_id} runs make hygiene without continue-on-error: true"
        )
    assert "backend-checks" in jobs, "the required backend-checks job must exist"
    assert "backend-checks" not in hygiene_jobs, (
        "backend-checks must not gate on make hygiene: the step is known red on alpha"
    )


def test_path_filters_cover_shared_server_inputs():
    """Backend checks must run when the shared server solver or schemas change."""

    workflow = yaml.safe_load(PROPOSAL.read_text())
    triggers = workflow.get("on") or workflow.get(True)
    assert isinstance(triggers, dict)
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        assert "server/**" in paths, f"{event} must cover shared server inputs"


def test_script_never_calls_date_with_percent_n():
    """GNU %N renders as a literal N on macOS/BSD and aborts the arithmetic."""
    code_lines = [line for line in CI_LOCAL.read_text().splitlines() if not line.lstrip().startswith("#")]
    offenders = [line for line in code_lines if re.search(r"date\s+\+[^|;&)\n]*%[0-9]*N", line)]
    assert not offenders, f"ci_local.sh must not use GNU date %N: {offenders}"


def _now_ms_output(path_env: str) -> str:
    text = CI_LOCAL.read_text()
    match = re.search(r"^now_ms\(\) \{.*?^\}", text, re.M | re.S)
    assert match, "now_ms() not found in scripts/ci_local.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", match.group(0) + "\nnow_ms"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PATH": path_env},
    )
    return result.stdout.strip()


def _install_bsd_date_shim(tmp: str) -> str:
    shim = Path(tmp) / "date"
    shim.write_text(BSD_DATE_SHIM)
    shim.chmod(0o755)
    return tmp


def test_timer_works_when_date_lacks_percent_n():
    """python3-backed timer under a BSD-like date stub returns an integer."""
    with tempfile.TemporaryDirectory() as tmp:
        out = _now_ms_output(f"{_install_bsd_date_shim(tmp)}:{os.environ['PATH']}")
    assert out.isdigit(), f"now_ms printed a non-integer: {out!r}"


# --- Greptile P2 on PR #288: the script's exit semantics, exercised ---------

# One stub source serves both tools: the recorded name comes from $0 at run
# time. It appends one "tool args..." line per call to $CI_STUB_LOG so tests
# can assert which steps ran and in what order, and exits with the code named
# by an env var keyed by the call's arguments: `make test` looks up
# CI_STUB_MAKE_TEST, `uv sync --frozen --extra api` looks up
# CI_STUB_UV_SYNC___FROZEN___EXTRA_API — uppercased, every non-alphanumeric
# character folded to _. A key that is not set exits 0 unless CI_STUB_DEFAULT
# says otherwise.
STUB_SOURCE = r"""#!/bin/sh
log="${CI_STUB_LOG:?CI_STUB_LOG must be set}"
printf '%s %s\n' "$(basename "$0")" "$*" >>"$log"
key=$(printf '%s %s' "$(basename "$0")" "$*" \
  | LC_ALL=C tr '[:lower:]' '[:upper:]' | LC_ALL=C tr -c '[:alnum:]' '_')
code=$(eval "printf '%s' \"\${CI_STUB_${key}:-}\"")
[ -n "$code" ] || code="${CI_STUB_DEFAULT:-0}"
exit "$code"
"""

# The steps backend/scripts/ci_local.sh runs, in the proposed workflow's
# order; the stubs make each one a recorded, sub-second call.
STEPS_IN_ORDER = [
    "uv sync --frozen --extra api",
    "uv run --frozen ruff check .",
    "uv run --frozen ruff format --check .",
    "uv run --frozen lint-imports",
    "make test",
    "make schemas",
    "make validate",
    "make hygiene",
]


def _stub_key(call: str) -> str:
    """The env-var key a stub call looks up, matching the shell stub's folding."""
    return re.sub(r"[^A-Z0-9]", "_", call.upper())


def _run_ci_local(codes: dict[str, int], keep_going: bool = False):
    """Run the real scripts/ci_local.sh with stubbed uv and make first on PATH.

    `codes` maps a call's argument string ("make hygiene") to the exit code
    its stub must return. Returns (proc, calls): the run's result and the
    recorded stub calls, one line per call, in call order.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stub_bin = Path(tmp) / "bin"
        stub_bin.mkdir()
        log = Path(tmp) / "calls.log"
        for tool in ("uv", "make"):
            stub = stub_bin / tool
            stub.write_text(STUB_SOURCE)
            stub.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "CI_STUB_LOG": str(log),
        }
        for call, code in codes.items():
            env[f"CI_STUB_{_stub_key(call)}"] = str(code)
        args = ["/bin/bash", str(CI_LOCAL)]
        if keep_going:
            args.append("--keep-going")
        proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=300)
        calls = log.read_text().splitlines()
    return proc, calls


def test_all_green_run_exits_zero_and_runs_every_step_in_order():
    """Nothing fails: every step runs once, in the workflow's order, exit 0."""
    proc, calls = _run_ci_local({})
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"
    assert calls == STEPS_IN_ORDER
    assert "all steps green" in proc.stdout


def test_default_run_stops_at_the_first_failing_gating_step():
    """A failed gating step fails the default run and skips the later steps."""
    proc, calls = _run_ci_local({"uv run --frozen ruff check .": 3})
    assert proc.returncode != 0, "a failed gating step must fail the default run"
    assert "uv run --frozen ruff check ." in calls
    for skipped in STEPS_IN_ORDER[2:]:
        assert skipped not in calls, f"the failing step must stop the run: {skipped} ran"
    assert "ruff check" in proc.stderr and "FAILED" in proc.stderr


def test_keep_going_runs_later_steps_and_still_exits_nonzero():
    """--keep-going runs the later steps and still exits nonzero."""
    proc, calls = _run_ci_local({"uv run --frozen ruff check .": 3}, keep_going=True)
    assert proc.returncode != 0, "--keep-going still exits nonzero on a gating failure"
    assert "uv run --frozen ruff check ." in calls
    for step in STEPS_IN_ORDER[2:]:
        assert step in calls, f"--keep-going must run the later steps: {step}"
    assert calls.index("uv run --frozen ruff check .") < calls.index("make schemas")
    assert "[ci_local] FAILED" in proc.stderr


def test_hygiene_only_failure_exits_zero_and_says_so():
    """The known-red hygiene step fails non-blocking: exit 0, failure visible."""
    proc, calls = _run_ci_local({"make hygiene": 2})
    assert proc.returncode == 0, f"hygiene-only failure must exit 0: {proc.stderr}"
    assert "make hygiene" in calls
    assert "FAILED" in proc.stderr, "the hygiene failure must be printed, not hidden"
    assert "gating steps green" in proc.stdout


def test_timer_falls_back_to_whole_seconds_without_python3():
    """No python3 on PATH: the whole-second date fallback still yields an integer."""
    with tempfile.TemporaryDirectory() as tmp:
        out = _now_ms_output(_install_bsd_date_shim(tmp))
    assert out.isdigit() and int(out) % 1000 == 0, (
        f"the no-python3 fallback must return whole-second milliseconds: {out!r}"
    )
