"""Run one function of a test module in a fresh process pinned to two CPUs.

Usage: python realcpu_launcher.py MODULE_PATH FUNCTION [ARGS...]

`test_real_cpu_upstream.py` measures the real stages' wall time and peak RSS
against the 2-CPU, 8 GB worker budget. Both numbers have to belong to the
stages, not to the pytest process that started them, which is why this
launcher exists and why it forks.

A child started with `subprocess` reports its parent's lifetime peak as its own
ru_maxrss on Linux: CPython starts it with vfork, and execve folds the parent's
high-water mark into the child's counter. Measured on WSL2 (kernel 6.18): after
the parent touched 600 MB, a child whose VmHWM was 11 MB reported a ru_maxrss of
626 MB. A plain fork() starts a fresh counter at the forking process's current
RSS, so the launcher forks while it is still a bare interpreter and runs the
work in that fork.

Pinning happens here for a related reason. sched_setaffinity on pid 0 applies
to the calling thread and the threads it creates afterwards, and OpenBLAS starts
its thread pool when numpy is imported. Pinning and forking before the module
under test is imported keeps every thread on the two CPUs and keeps the fork
single-threaded. macOS has no sched_setaffinity; the test reads the affinity
the work actually ran with and only claims the wall-time budget when it is two
CPUs or fewer.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import sys
import time
import traceback

CPUS = 2
CHILD_TIMEOUT_S = 900.0
_TERMINATE_GRACE_S = 1.0
_POLL_INTERVAL_S = 0.02


def _pin() -> None:
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:CPUS])


def _run(module_path: str, function: str, args: list[str]) -> int:
    try:
        spec = importlib.util.spec_from_file_location("realcpu_child", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        getattr(module, function)(*args)
    except BaseException:
        traceback.print_exc()
        return 1
    finally:
        # os._exit skips interpreter shutdown, which is what would flush these.
        sys.stdout.flush()
        sys.stderr.flush()
    return 0


def _wait_for_child(pid: int, stage: str, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while True:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return os.waitstatus_to_exitcode(status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_POLL_INTERVAL_S, remaining))

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        os.waitpid(pid, 0)
        raise TimeoutError(f"{stage} exceeded launcher timeout of {timeout_s:g}s") from None
    terminate_deadline = time.monotonic() + _TERMINATE_GRACE_S
    while True:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            raise TimeoutError(f"{stage} exceeded launcher timeout of {timeout_s:g}s")
        remaining = terminate_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_POLL_INTERVAL_S, remaining))

    os.kill(pid, signal.SIGKILL)
    _, status = os.waitpid(pid, 0)
    raise TimeoutError(f"{stage} exceeded launcher timeout of {timeout_s:g}s")


def main(argv: list[str]) -> int:
    module_path, function, *args = argv
    _pin()
    pid = os.fork()
    if pid == 0:
        os._exit(_run(module_path, function, args))
    return _wait_for_child(pid, function, CHILD_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
