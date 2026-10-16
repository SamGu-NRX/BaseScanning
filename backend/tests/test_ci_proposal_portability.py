"""Regressions for the proposed backend CI job and its local mirror.

Pins the two PR #288 review findings:

- The proposed workflow must not gate on `make hygiene`, which is known red
  on alpha (the tracked synthetic-meter.tar is denied by the repo's own
  hygiene rules). The step runs only in a job with continue-on-error: true.
- backend/scripts/ci_local.sh must time its steps without GNU date's %N,
  which macOS/BSD date does not support; the script has to keep working there.
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


def test_timer_falls_back_to_whole_seconds_without_python3():
    """No python3 on PATH: the whole-second date fallback still yields an integer."""
    with tempfile.TemporaryDirectory() as tmp:
        out = _now_ms_output(_install_bsd_date_shim(tmp))
    assert out.isdigit() and int(out) % 1000 == 0, (
        f"the no-python3 fallback must return whole-second milliseconds: {out!r}"
    )
