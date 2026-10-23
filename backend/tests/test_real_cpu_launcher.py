from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).with_name("realcpu_launcher.py")


def _load_launcher():
    spec = importlib.util.spec_from_file_location("realcpu_launcher_test", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sleep_stage(pid_path: str) -> None:
    Path(pid_path).write_text(str(os.getpid()))
    time.sleep(30)


def test_launcher_terminates_and_reaps_a_timed_out_stage(tmp_path, monkeypatch) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "CHILD_TIMEOUT_S", 0.05)
    pid_path = tmp_path / "child.pid"

    started = time.monotonic()
    with pytest.raises(TimeoutError, match=r"sleep_stage exceeded launcher timeout of 0\.05s"):
        launcher.main([str(Path(__file__)), "sleep_stage", str(pid_path)])

    assert time.monotonic() - started < 2
    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_wait_for_child_preserves_a_normal_exit_status() -> None:
    launcher = _load_launcher()
    pid = os.fork()
    if pid == 0:
        os._exit(7)

    assert launcher._wait_for_child(pid, "exit_stage", 1) == 7
