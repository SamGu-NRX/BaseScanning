"""Installed-CLI and packaging tests: wheel -> temp venv -> subprocess.

The unit suites import the package from the source tree (editable
install). These tests prove the *shipped* artifact instead: build the
wheel, install it into a scratch venv with no editable path and no
index, then drive the installed console scripts over real subprocesses.

Covered here (recon §5 gaps):
  - installed ``hunter-selfcheck`` exits 0 on a non-editable install
    (end state; xfail until the trace loader resolves package resources —
    coordinated change in selfcheck.py, outside this child's scope)
  - installed ``hunter-local-ingest`` arms a ``--with-faults`` round
    against a reachable local endpoint
  - exit-code propagation over subprocess: 2 usage error, 3 plan-file
    schema error, 2 unreachable endpoint (BLOCKED), 1 forced FAIL
  - absent ``--out`` parents are created, not a crash
  - honest labeling survives the installed path: a fake endpoint under a
    FAKE_HUNTER declaration never produces REAL_MIRROR_CPU rows

Heavy by nature: ONE wheel build and ONE venv per pytest session
(module-scoped fixtures), tests run serially by design (shared sandbox).
The first wheel build may fetch setuptools from PyPI (build isolation);
the wheel install itself is offline (``--no-index``).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TOOL_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# module-scoped fixtures: one wheel, one venv, two tiny in-process servers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    dist = tmp_path_factory.mktemp("wheel-dist")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(dist), str(TOOL_ROOT)],
        check=True,
        cwd=TOOL_ROOT,
    )
    wheels = list(dist.glob("hunter_local_ingest-*.whl"))
    assert wheels, "wheel build must produce the package wheel"
    return wheels[0]


@pytest.fixture(scope="module")
def installed_venv(tmp_path_factory, built_wheel):
    root = tmp_path_factory.mktemp("pkg-venv")
    venv_dir = root / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, cwd=TOOL_ROOT)
    subprocess.run(
        [
            str(venv_dir / "bin" / "pip"),
            "install",
            "-q",
            "--no-deps",
            "--no-index",
            str(built_wheel),
        ],
        check=True,
        cwd=TOOL_ROOT,
    )
    return venv_dir


@pytest.fixture(scope="module")
def fake_endpoint():
    """The self-check's plumbing fake, served over real HTTP on loopback."""
    from hunter_local_ingest.selfcheck import _FakeState, build_fake_handler

    holder = {"base": ""}
    handler = build_fake_handler(holder)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.state = _FakeState()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    holder["base"] = f"http://{host}:{port}"
    yield holder["base"]
    server.shutdown()
    thread.join(timeout=5)


class _Always500(BaseHTTPRequestHandler):
    """A reachable but misbehaving endpoint: every answer is a 500 reply."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return

    def _reply(self) -> None:
        body = b'{"errors":[{"code":"harness_forced_500"}]}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = _reply


@pytest.fixture(scope="module")
def broken_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Always500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    thread.join(timeout=5)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _generate_plan(tmp_path: Path, seed: str = "3") -> Path:
    out = tmp_path / "plan.json"
    subprocess.run(
        [
            sys.executable,
            str(TOOL_ROOT / "fixtures" / "gen_synthetic_packet.py"),
            "--seed",
            seed,
            "--out",
            str(out),
        ],
        check=True,
        cwd=TOOL_ROOT,
    )
    return out


def _run_cli(venv_dir: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Run the *installed* console script; timeout guards any hang."""
    return subprocess.run(
        [str(venv_dir / "bin" / "hunter-local-ingest"), *argv],
        capture_output=True,
        text=True,
        cwd=TOOL_ROOT,
        timeout=120,
    )


def _read_checks(run_dir: Path) -> list[dict]:
    lines = (run_dir / "checks.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _single_run_dir(out_root: Path) -> Path:
    run_dirs = list(out_root.iterdir())
    assert len(run_dirs) == 1, f"expected exactly one run dir under {out_root}"
    return run_dirs[0]


# --------------------------------------------------------------------------
# wheel contents
# --------------------------------------------------------------------------


class TestWheelContents:
    def test_wheel_ships_package_modules_and_console_scripts(self, built_wheel):
        with zipfile.ZipFile(built_wheel) as zf:
            names = zf.namelist()
            entry_points_name = next(n for n in names if n.endswith("dist-info/entry_points.txt"))
            entry_points = zf.read(entry_points_name).decode()
        for required in (
            "hunter_local_ingest/runner.py",
            "hunter_local_ingest/selfcheck.py",
            "hunter_local_ingest/traces.py",
            "hunter_local_ingest/scenarios/lifecycle.py",
        ):
            assert required in names, required
        # both console scripts must be declared, or the installed arms below
        # would test nothing
        assert "hunter-local-ingest" in entry_points
        assert "hunter-selfcheck" in entry_points


# --------------------------------------------------------------------------
# installed selfcheck
# --------------------------------------------------------------------------


class TestInstalledSelfcheck:
    def test_installed_selfcheck_writes_receipts(self, installed_venv, tmp_path):
        """Selfcheck runs from a non-editable install and emits its artifacts.

        Exit 1 with problems is contractual (same as the runner); the
        end-state "exits 0" gate is the dedicated test below.
        """
        out = tmp_path / "sc"
        proc = subprocess.run(
            [str(installed_venv / "bin" / "hunter-selfcheck"), "--out", str(out)],
            capture_output=True,
            text=True,
            cwd=TOOL_ROOT,
            timeout=120,
        )
        assert proc.returncode in (0, 1), proc.stdout + proc.stderr
        run_dir = _single_run_dir(out)
        artifacts = {p.name for p in run_dir.iterdir()}
        assert {"run-manifest.json", "checks.jsonl", "summary.json"} <= artifacts

    @pytest.mark.xfail(
        reason=(
            "selfcheck.py resolves the frozen trace at parents[2], which in a "
            "non-editable install is outside the installed package, so the "
            "trace never ships and the installed selfcheck exits 1 with "
            "'frozen trace missing at .../lib/python3.x/traces/'. The "
            "pyproject package-data glob (traces/*.jsonl) is in place; the "
            "trace must move under src/hunter_local_ingest/traces/ and the "
            "loader must resolve package resources first — coordinated "
            "selfcheck.py change owned by the writer (cross-file ask). "
            "XPASS(strict) once it lands: remove this marker."
        ),
        strict=True,
    )
    def test_installed_selfcheck_exits_zero(self, installed_venv, tmp_path):
        out = tmp_path / "sc"
        proc = subprocess.run(
            [str(installed_venv / "bin" / "hunter-selfcheck"), "--out", str(out)],
            capture_output=True,
            text=True,
            cwd=TOOL_ROOT,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "selfcheck: PASS" in proc.stdout


# --------------------------------------------------------------------------
# installed runner arms
# --------------------------------------------------------------------------


class TestInstalledRunner:
    def test_installed_runner_arms_with_faults_round(
        self, installed_venv, fake_endpoint, tmp_path
    ):
        plan = _generate_plan(tmp_path, seed="11")
        out = tmp_path / "runs"
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                fake_endpoint,
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--label",
                "pkg-faults",
                "--backend-mode",
                "FAKE_HUNTER",
                "--poll-interval-s",
                "0.2",
                "--with-faults",
            ],
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        run_dir = _single_run_dir(out)
        artifacts = {p.name for p in run_dir.iterdir()}
        assert {"run-manifest.json", "checks.jsonl", "summary.json"} <= artifacts
        lines = _read_checks(run_dir)
        by_id = {line["id"]: line for line in lines}
        for cid in (
            "lifecycle.health",
            "lifecycle.create",
            "lifecycle.register",
            "lifecycle.upload",
            "lifecycle.commit",
            "lifecycle.finalize",
            "lifecycle.events",
            "lifecycle.result",
        ):
            assert by_id[cid]["status"] == "PASS", (cid, by_id[cid]["observed"])
        # the fault round ran through the prefixed sink and is labeled fake
        fault_ids = [i for i in by_id if i.startswith("fault.")]
        assert fault_ids, "a --with-faults round must append fault.* receipt rows"
        assert all(by_id[i]["transport"] == "FAKE_HUNTER" for i in fault_ids)
        # honest labeling over the installed path: the endpoint is the
        # plumbing fake, so no row may claim the real-mirror class
        assert all(line["transport"] != "REAL_MIRROR_CPU" for line in lines)
        # GPU arms: named, never executed
        for arm in ("objects", "vlm", "dense"):
            assert by_id[f"gpu-stub.{arm}"]["status"] == "NOT_RUN"

    def test_installed_runner_creates_absent_output_parents(
        self, installed_venv, fake_endpoint, tmp_path
    ):
        plan = _generate_plan(tmp_path)
        out = tmp_path / "deep" / "nested" / "runs"
        assert not out.exists()
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                fake_endpoint,
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--label",
                "pkg-nested",
                "--backend-mode",
                "FAKE_HUNTER",
                "--poll-interval-s",
                "0.2",
            ],
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _single_run_dir(out).is_dir()

    def test_installed_runner_usage_error_exits_two(self, installed_venv, fake_endpoint, tmp_path):
        plan = _generate_plan(tmp_path)
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                fake_endpoint,
                "--plan-file",
                str(plan),
                "--out",
                str(tmp_path / "runs-usage"),
                "--backend-mode",
                "FAKE_HUNTER",
                "--no-such-flag",
            ],
        )
        assert proc.returncode == 2
        assert "usage:" in proc.stderr

    def test_installed_runner_bad_plan_file_exits_three(
        self, installed_venv, fake_endpoint, tmp_path
    ):
        bad = tmp_path / "bad-plan.json"
        bad.write_text(json.dumps({"plan": {"packet_id": "synthetic-000003"}}))
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                fake_endpoint,
                "--plan-file",
                str(bad),
                "--out",
                str(tmp_path / "runs-bad-plan"),
                "--backend-mode",
                "FAKE_HUNTER",
            ],
        )
        assert proc.returncode == 3
        # The "3: ... does not match the generator schema" diagnostic string is
        # swallowed by main()'s SystemExit -> int conversion (observed: empty
        # stderr, exit 3); only the code is contractual at this head.

    def test_installed_runner_unreachable_endpoint_exits_two(self, installed_venv, tmp_path):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        plan = _generate_plan(tmp_path)
        out = tmp_path / "runs-dead"
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                f"http://127.0.0.1:{dead_port}/v1",
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--label",
                "pkg-dead",
                "--backend-mode",
                "REAL_MIRROR_CPU",
            ],
        )
        assert proc.returncode == 2
        run_dir = _single_run_dir(out)
        summary = json.loads((run_dir / "summary.json").read_text())
        assert summary["blocked"], "an unreachable endpoint must name its block"
        assert any(
            line["status"] == "BLOCKED" for line in _read_checks(run_dir)
        ), "unreachable runs must record BLOCKED vocabulary rows"

    def test_installed_runner_forced_fail_exits_one(
        self, installed_venv, broken_endpoint, tmp_path
    ):
        plan = _generate_plan(tmp_path)
        out = tmp_path / "runs-fail"
        proc = _run_cli(
            installed_venv,
            [
                "--endpoint",
                broken_endpoint,
                "--plan-file",
                str(plan),
                "--out",
                str(out),
                "--label",
                "pkg-fail",
                "--backend-mode",
                "FAKE_HUNTER",
                "--poll-interval-s",
                "0.2",
            ],
        )
        assert proc.returncode == 1
        run_dir = _single_run_dir(out)
        lines = _read_checks(run_dir)
        assert any(line["status"] == "FAIL" for line in lines), (
            "a misbehaving endpoint must produce at least one FAIL row"
        )
        summary = json.loads((run_dir / "summary.json").read_text())
        assert summary["counts_by_transport"]["FAKE_HUNTER"]["FAIL"] >= 1
