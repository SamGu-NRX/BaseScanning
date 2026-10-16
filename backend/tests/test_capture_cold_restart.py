"""Checks for ``scripts/capture_cold_restart.py``: recovery across a client cold restart.

Each test first runs the real runner (``scripts/capture_flow_e2e.py``) to
produce a saved receipt, then drives the cold-restart command and asserts the
journey recovers when the client that resumes is a different Python process
from the one that lost the response. The subprocess tests are the point: the
primary client is a process that exits before the resume client starts, so the
only state the resume inherits is the saved receipt, the journal file, and
whatever the server remembers. Everything runs against the local memory/volume
backends with orchestrator stage stubs; no network, no Modal, no credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI_TIMEOUT_S = 600
STOP_POINTS = ["create-lost", "pre-commit", "commit-lost", "pre-finalize", "finalize-lost"]


@pytest.fixture
def runner():
    import scripts.capture_flow_e2e as mod

    return mod


@pytest.fixture
def cold():
    import scripts.capture_cold_restart as mod

    return mod


@pytest.fixture
def saved_receipt(runner, tmp_path) -> Path:
    """A receipt the runner wrote to disk, the way a prior session leaves it."""
    workdir = tmp_path / "original"
    receipt = runner.run_capture_flow(workdir)
    assert receipt["ok"] is True, receipt.get("errorDetail")
    path = workdir / "receipt.json"
    runner.write_receipt(receipt, path)
    return path


def _run_cli(receipt: Path, workdir: Path, *extra: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [
            sys.executable,
            "scripts/capture_cold_restart.py",
            "--receipt",
            str(receipt),
            "--workdir",
            str(workdir),
            *extra,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=CLI_TIMEOUT_S,
    )


def test_missing_replay_fields_names_each_field(cold) -> None:
    assert cold.missing_replay_fields({}) == ["idempotencyKeys", "files", "packetSha256"]
    assert cold.missing_replay_fields({"idempotencyKeys": {}}) == [
        "idempotencyKeys.create",
        "idempotencyKeys.finalize",
        "files",
        "packetSha256",
    ]
    assert cold.missing_replay_fields(
        {"idempotencyKeys": {"create": "k", "finalize": "k"}, "files": [{"path": "p"}]}
    ) == [
        "files[0]",
        "packetSha256",
    ]


@pytest.mark.parametrize("field", ["idempotencyKeys", "files", "packetSha256"])
def test_receipt_missing_replay_field_fails_loudly_naming_the_field(
    cold, saved_receipt, tmp_path, capsys, field
) -> None:
    receipt = json.loads(saved_receipt.read_text())
    receipt.pop(field)
    stripped = tmp_path / "stripped-receipt.json"
    stripped.write_text(json.dumps(receipt))

    code = cold.main(["--receipt", str(stripped), "--workdir", str(tmp_path / "run")])

    assert code == 1
    out = capsys.readouterr().out
    assert f"receipt_missing_field:{field}" in out
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    assert report["missingReceiptFields"] == [field]
    assert report["captureId"] is None  # nothing reached the API


def test_cli_missing_replay_field_exits_nonzero(cold, saved_receipt, tmp_path) -> None:
    receipt = json.loads(saved_receipt.read_text())
    receipt.pop("packetSha256")
    stripped = tmp_path / "stripped-receipt.json"
    stripped.write_text(json.dumps(receipt))

    completed = _run_cli(stripped, tmp_path / "cli")

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "receipt_missing_field:packetSha256" in completed.stdout


@pytest.mark.parametrize("stop", STOP_POINTS)
def test_subprocess_recovery_after_client_process_exit(cold, saved_receipt, tmp_path, stop) -> None:
    completed = _run_cli(saved_receipt, tmp_path / "run", "--stop", stop)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    assert report["ok"] is True, report.get("errorDetail")
    assert report["errorCodes"] == []
    assert all(report["invariants"].values()), report["invariants"]

    # Two fresh client processes: the resume shares nothing with the primary
    # but the receipt, the journal and the server.
    assert report["processModel"]["legs"] == ["primary", "resume"]
    assert report["processModel"]["freshPythonProcessPerLeg"] is True
    assert report["primary"]["pid"] != report["resume"]["pid"]
    assert report["primary"]["freshProcess"] and report["resume"]["freshProcess"]
    assert report["primary"]["stop"] == stop

    assert report["finalCaptureStatus"] in cold.TERMINAL_STATUSES
    assert report["terminalEvents"] == ["verdict_ready"]


def test_commit_lost_resume_rebuilds_state_from_the_server(cold, saved_receipt, tmp_path) -> None:
    completed = _run_cli(saved_receipt, tmp_path / "run", "--stop", "commit-lost")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    primary, resume = report["primary"], report["resume"]

    # The primary died after the commit executed, without seeing the reply.
    assert primary["interruption"] == {"kind": "lost_response", "step": "commit"}
    assert primary["journal"]["commitExecuted"] is True
    assert primary["journal"]["commitResponseSeen"] is False
    assert primary["journal"]["captureId"]

    # The fresh client learned the commit outcome from the server, not from
    # itself: create replayed from the idempotency store, re-register reported
    # every file committed, and the identical commit body committed again.
    assert resume["recoveryPath"] == "register_commit"
    assert resume["createReplayHttpStatus"] == 200
    assert resume["recreatedSameCaptureId"] is True
    assert resume["captureId"] == primary["journal"]["captureId"]
    assert resume["serverFileStates"] and set(resume["serverFileStates"].values()) == {"committed"}
    assert resume["recommit"] is True
    assert resume["commitMismatch"] == []

    # One deterministic run id across the restart, and one terminal state.
    assert resume["runId"] == resume["expectedRunId"] == resume["finalizeReplayRunId"]
    assert resume["finalCaptureStatus"] in cold.TERMINAL_STATUSES
    assert resume["resultRunId"] == resume["runId"]
    assert resume["resultOutcomeKind"] == resume["finalCaptureStatus"]
    assert resume["terminalEvents"] == ["verdict_ready"]
    assert resume["terminalCreateCaptureId"] == resume["captureId"]
    assert report["invariants"] == {
        "oneCaptureId": True,
        "oneDeterministicRunId": True,
        "committedHashesMatchReceipt": True,
        "oneTerminalEvent": True,
        "oneTerminalResult": True,
    }


def test_finalize_lost_resume_recovers_the_run_id_the_dead_process_never_saw(cold, saved_receipt, tmp_path) -> None:
    completed = _run_cli(saved_receipt, tmp_path / "run", "--stop", "finalize-lost")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    primary, resume = report["primary"], report["resume"]
    assert primary["journal"]["finalizeExecuted"] is True
    assert primary["journal"]["finalizeResponseSeen"] is False

    # The run id the dead process never learned is the deterministic one, and
    # the resume's second finalize returns it unchanged. Recovery went through
    # the finalize replay: the resume never re-registered (the capture is
    # closed to files) and never re-committed.
    assert resume["recoveryPath"] == "finalize_replay"
    assert resume["serverFileStates"] == {}
    assert resume["recommit"] is False
    assert resume["runId"] == resume["expectedRunId"] == resume["finalizeReplayRunId"]
    assert report["invariants"]["oneDeterministicRunId"] is True
    assert report["invariants"]["oneCaptureId"] is True


def test_create_lost_resume_recovers_a_capture_id_that_never_reached_the_client(cold, saved_receipt, tmp_path) -> None:
    completed = _run_cli(saved_receipt, tmp_path / "run", "--stop", "create-lost")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    primary, resume = report["primary"], report["resume"]
    assert primary["journal"]["captureId"] is None

    # The store answered the saved key with the one capture, and the fresh
    # client walked the whole remaining journey to the terminal result.
    assert resume["createReplayHttpStatus"] == 200
    assert resume["captureId"]
    assert resume["recreatedSameCaptureId"] is True
    assert report["invariants"]["oneCaptureId"] is True
    assert all(report["invariants"].values()), report["invariants"]


def test_pre_commit_resume_uploads_and_commits_for_the_first_time(cold, saved_receipt, tmp_path) -> None:
    completed = _run_cli(saved_receipt, tmp_path / "run", "--stop", "pre-commit")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((tmp_path / "run" / "cold-restart-report.json").read_text())
    primary, resume = report["primary"], report["resume"]
    assert primary["journal"]["commitExecuted"] is False

    # The commit was never sent before the restart, so the fresh client is the
    # first to commit — and the server still answers with the receipt's hashes.
    assert resume["recommit"] is False
    assert report["invariants"]["committedHashesMatchReceipt"] is True
    assert all(report["invariants"].values()), report["invariants"]
