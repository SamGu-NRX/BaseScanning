"""hunter-selfcheck: the runner, end to end, against the in-process fake.

The fake (fakeapi.py) is FAKE_HUNTER plumbing. A green self-check shows
the harness runs, labels honestly, and writes receipts that verify. It is
never evidence about any backend.

It runs the real runner with the fault round and a no-op restart command,
then requires: exit 0; every lifecycle, control, fault and trace row PASS
and FAKE_HUNTER; the three GPU arms NOT_RUN; and hunter-verify-run finding
nothing in the run directory.

Usage: hunter-selfcheck [--out DIR]. Exit 0 when all of that holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .fakeapi import FakeApi
from .fixturegen import write_plan
from .modes import Status, Transport
from .runner import run
from .scenarios.oracles import GPU, ORACLES, RETIRED
from .verify import verify_run


def selfcheck(out_root: Path) -> list[str]:
    problems: list[str] = []
    plan_path = out_root / "selfcheck-plan.json"
    out_root.mkdir(parents=True, exist_ok=True)
    write_plan(plan_path, seed=1)
    runs = out_root / "runs"
    with FakeApi() as api:
        code = run(
            [
                "--endpoint",
                api.base_url,
                "--plan-file",
                str(plan_path),
                "--out",
                str(runs),
                "--label",
                "selfcheck",
                "--backend-mode",
                "FAKE_HUNTER",
                "--poll-interval-s",
                "0.05",
                "--events-wait-s",
                "0",
                "--retry-backoff-s",
                "0",
                "--with-faults",
                "--restart-argv-json",
                json.dumps([sys.executable, "-c", "pass"]),
            ]
        )
    if code != 0:
        problems.append(f"runner exit {code}, expected 0")
    run_dirs = sorted(runs.iterdir()) if runs.exists() else []
    if len(run_dirs) != 1:
        return [*problems, "expected exactly one run directory"]
    run_dir = run_dirs[0]
    lines = [json.loads(line) for line in (run_dir / "checks.jsonl").read_text().splitlines()]
    seen = {line["id"]: line for line in lines}
    for check_id in ORACLES:
        if check_id in RETIRED:
            continue
        if check_id == "harness.internal-error":
            if check_id in seen:
                problems.append("harness raised an internal error")
            continue
        line = seen.get(check_id)
        want = Status.NOT_RUN.value if check_id in GPU else Status.PASS.value
        transport = Transport.GPU_STUB.value if check_id in GPU else Transport.FAKE_HUNTER.value
        if line is None:
            problems.append(f"{check_id}: missing")
        elif line["status"] != want or line["transport"] != transport:
            problems.append(
                f"{check_id}: {line['transport']}/{line['status']}, want {transport}/{want}"
            )
    problems += [f"verify: {p}" for p in verify_run(run_dir)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-selfcheck", description=__doc__)
    parser.add_argument("--out", default=None, help="directory for the plan and the run")
    args = parser.parse_args()
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="hli-selfcheck-"))
    problems = selfcheck(out)
    for problem in problems:
        print(f"SELF-CHECK PROBLEM: {problem}")
    print("selfcheck: PASS" if not problems else "selfcheck: FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
