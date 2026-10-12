"""The shipped artifact: wheel -> scratch venv -> console scripts over subprocess.

The other suites import the source tree. These build the wheel, install it
non-editable with no index into a scratch venv, and run the installed
console scripts from a directory outside the source tree with PYTHONPATH
removed, so nothing can resolve back to the checkout.

One wheel build and one venv per session. The wheel build may fetch
setuptools (build isolation); the install itself is offline.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import canary

from hunter_local_ingest.fakeapi import FakeApi

TOOL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("hunter-local-ingest", "hunter-selfcheck", "hunter-verify-run", "hunter-gen-plan")
FAST = [
    "--poll-interval-s",
    "0.01",
    "--events-wait-s",
    "0",
    "--retry-backoff-s",
    "0",
    "--result-budget-s",
    "10",
]


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env["PYTHONNOUSERSITE"] = "1"
    return env


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory) -> Path:
    dist = tmp_path_factory.mktemp("wheel-dist")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(dist), str(TOOL_ROOT)],
        check=True,
        cwd=dist,
        env=_env(),
        timeout=300,
    )
    wheels = list(dist.glob("hunter_local_ingest-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


@pytest.fixture(scope="session")
def venv(tmp_path_factory, built_wheel) -> Path:
    root = tmp_path_factory.mktemp("pkg-venv") / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(root)], check=True, timeout=120)
    subprocess.run(
        [str(root / "bin" / "pip"), "install", "-q", "--no-deps", "--no-index", str(built_wheel)],
        check=True,
        cwd=root.parent,
        env=_env(),
        timeout=300,
    )
    return root


@pytest.fixture()
def outside(tmp_path) -> Path:
    """A working directory with no source tree in it."""
    work = tmp_path / "work"
    work.mkdir()
    return work


def _cli(venv: Path, script: str, argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(venv / "bin" / script), *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=_env(),
        timeout=180,
    )


def _plan(venv: Path, cwd: Path, seed: str = "11") -> Path:
    out = cwd / "plan.json"
    proc = _cli(venv, "hunter-gen-plan", ["--seed", seed, "--mesh-kb", "4", "--out", str(out)], cwd)
    assert proc.returncode == 0, proc.stderr
    return out


def _run_dir(out: Path) -> Path:
    dirs = list(out.iterdir())
    assert len(dirs) == 1, dirs
    return dirs[0]


def _rows(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "checks.jsonl").read_text().splitlines()]


# -- the wheel ----------------------------------------------------------------


def test_wheel_ships_modules_traces_and_scripts(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
        entry_points = zf.read(next(n for n in names if n.endswith("entry_points.txt"))).decode()
    for required in (
        "hunter_local_ingest/runner.py",
        "hunter_local_ingest/fixturegen.py",
        "hunter_local_ingest/traces/expected-lifecycle-v1.jsonl",
        "hunter_local_ingest/traces/expected-lifecycle-v2.jsonl",
    ):
        assert required in names, required
    for script in SCRIPTS:
        assert f"{script} = " in entry_points, script


def test_installed_package_resolves_inside_the_venv(venv, outside):
    proc = subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "import hunter_local_ingest, hunter_local_ingest.traces as t; "
            "print(hunter_local_ingest.__file__); print(len(t.load_trace()))",
        ],
        capture_output=True,
        text=True,
        cwd=outside,
        env=_env(),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    location, count = proc.stdout.split()
    assert Path(location).resolve().is_relative_to(venv.resolve())
    assert not Path(location).resolve().is_relative_to(TOOL_ROOT)
    assert count == "12"


# -- console scripts ------------------------------------------------------------


def test_installed_selfcheck_exits_zero(venv, outside):
    proc = _cli(venv, "hunter-selfcheck", ["--out", str(outside / "sc")], outside)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "selfcheck: PASS" in proc.stdout


def test_installed_runner_with_faults_and_verify(venv, outside):
    plan = _plan(venv, outside)
    out = outside / "deep" / "absent" / "runs"
    with FakeApi() as api:
        proc = _cli(
            venv,
            "hunter-local-ingest",
            [
                "--endpoint",
                api.base_url,
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--label",
                "pkg",
                "--backend-mode",
                "FAKE_HUNTER",
                "--with-faults",
                *FAST,
            ],
            outside,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run_dir = _run_dir(out)
    rows = _rows(run_dir)
    by_id = {row["id"]: row for row in rows}
    assert all(
        row["status"] == "PASS"
        for row in rows
        if not row["id"].startswith("gpu-stub.") and row["id"] != "control.restart-recovery"
    ), proc.stdout
    assert {row["transport"] for row in rows} == {"FAKE_HUNTER", "GPU_STUB"}
    assert by_id["fault.truncated-response"]["status"] == "PASS"
    verified = _cli(venv, "hunter-verify-run", [str(run_dir)], outside)
    assert verified.returncode == 0, verified.stdout
    checks = run_dir / "checks.jsonl"
    checks.chmod(0o644)
    checks.write_text(checks.read_text().replace('"PASS"', '"FAIL"', 1))
    tampered = _cli(venv, "hunter-verify-run", [str(run_dir)], outside)
    assert tampered.returncode == 5
    assert "checks.jsonl: modified after chain entry" in tampered.stdout


def test_installed_usage_error_exits_two_without_echoing(venv, outside):
    secret = canary("tok")
    plan = _plan(venv, outside)
    proc = _cli(
        venv,
        "hunter-local-ingest",
        [
            "--endpoint",
            "http://127.0.0.1:9/v1",
            "--plan-file",
            str(plan),
            "--backend-mode",
            "FAKE_HUNTER",
            "--out",
            str(outside / "o"),
            "--token",
            secret,
        ],
        outside,
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stderr and "usage:unrecognized-arguments" in proc.stderr
    assert secret not in proc.stdout + proc.stderr
    assert not (outside / "o").exists()


def test_installed_malformed_flag_value_exits_two(venv, outside):
    plan = _plan(venv, outside)
    proc = _cli(
        venv,
        "hunter-local-ingest",
        [
            "--endpoint",
            "http://127.0.0.1:9/v1",
            "--plan-file",
            str(plan),
            "--backend-mode",
            "FAKE_HUNTER",
            "--poll-interval-s",
            "nan",
        ],
        outside,
    )
    assert proc.returncode == 2
    assert "usage:invalid-value:--poll-interval-s" in proc.stderr


def test_installed_insecure_endpoint_exits_two(venv, outside):
    plan = _plan(venv, outside)
    proc = _cli(
        venv,
        "hunter-local-ingest",
        [
            "--endpoint",
            "http://127.0.0.1.evil.com:8000/v1",
            "--plan-file",
            str(plan),
            "--backend-mode",
            "REAL_API",
        ],
        outside,
    )
    assert proc.returncode == 2
    assert "endpoint:base_insecure" in proc.stderr and "evil" not in proc.stderr


def test_installed_malformed_plan_exits_three(venv, outside):
    bad = outside / "bad.json"
    bad.write_text("{not json")
    proc = _cli(
        venv,
        "hunter-local-ingest",
        [
            "--endpoint",
            "http://127.0.0.1:9/v1",
            "--plan-file",
            str(bad),
            "--backend-mode",
            "FAKE_HUNTER",
        ],
        outside,
    )
    assert proc.returncode == 3
    assert "plan refused: plan-file: not JSON" in proc.stderr


def test_installed_unreachable_endpoint_exits_four(venv, outside):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    plan = _plan(venv, outside)
    out = outside / "runs"
    proc = _cli(
        venv,
        "hunter-local-ingest",
        [
            "--endpoint",
            f"http://127.0.0.1:{port}/v1",
            "--plan-file",
            str(plan),
            "--out",
            str(out),
            "--backend-mode",
            "REAL_API",
            *FAST,
        ],
        outside,
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    run_dir = _run_dir(out)
    assert {row["status"] for row in _rows(run_dir)} == {"BLOCKED", "NOT_RUN"}
    assert json.loads((run_dir / "summary.json").read_text())["verdict"] == "BLOCKED"


class _Always500(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _reply(self) -> None:
        body = b'{"errors":[{"code":"harness_forced_500"}]}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = _reply


def test_installed_forced_fail_exits_one(venv, outside):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Always500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    plan = _plan(venv, outside)
    out = outside / "runs"
    try:
        proc = _cli(
            venv,
            "hunter-local-ingest",
            [
                "--endpoint",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--backend-mode",
                "REAL_API",
                "--max-retries",
                "0",
                *FAST,
            ],
            outside,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = json.loads((_run_dir(out) / "summary.json").read_text())
    assert summary["verdict"] == "FAIL" and summary["counts_by_transport"]["REAL_API"]["FAIL"] >= 1
