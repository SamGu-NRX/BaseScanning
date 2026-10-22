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
import sys
import traceback

CPUS = 2


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


def main(argv: list[str]) -> int:
    module_path, function, *args = argv
    _pin()
    pid = os.fork()
    if pid == 0:
        os._exit(_run(module_path, function, args))
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
