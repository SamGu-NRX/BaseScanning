"""Checks for ``scripts/replay_capture.py``: the saved-receipt replay.

Each test first runs the real runner (``scripts/capture_flow_e2e.py``) to
produce a saved receipt, then replays that receipt against a fresh instance of
the real app and inspects the replay report. The failure paths use the
deterministic ``FailureInjector`` — a scripted lost response that fires after
the request actually executed — never clocks, randomness, or unbounded
retries. Everything runs in-process over the local memory/volume backends with
orchestrator stage stubs; no network, no Modal, no credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner():
    import scripts.capture_flow_e2e as mod

    return mod


@pytest.fixture
def replay():
    import scripts.replay_capture as mod

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


def _by_step(report: dict) -> dict:
    return {step["step"]: step for step in report["steps"]}


def test_replay_of_saved_receipt_reaches_the_terminal_result(replay, saved_receipt, tmp_path) -> None:
    report = replay.run_replay(saved_receipt, tmp_path / "replay")
    report_path = tmp_path / "replay" / "replay-receipt.json"
    replay.write_report(report, report_path)

    assert report["ok"] is True, report.get("errorDetail")
    assert report["errorCodes"] == []
    assert report["finalCaptureStatus"] in replay.TERMINAL_STATUSES
    assert report["resultOutcomeKind"] == report["finalCaptureStatus"]
    assert report["fileHashesVerified"] > 0
    assert report["terminalEvents"] == ["verdict_ready"]

    steps = _by_step(report)
    assert steps["create"]["idempotencyKey"] == "mini-arkit"
    assert steps["finalize"]["idempotencyKey"] == "mini-arkit"
    assert steps["fixture"]["drift"] == []
    assert steps["result"]["runId"] == report["runId"] == steps["finalize"]["runId"]

    stored = json.loads(report_path.read_text())
    assert stored["ok"] is True and stored["captureId"] == report["captureId"]


def test_retry_after_simulated_timeout_keeps_one_capture_id(replay, saved_receipt, tmp_path) -> None:
    report = replay.run_replay(saved_receipt, tmp_path / "replay", faults={"create": 1})

    assert report["ok"] is True, report.get("errorDetail")
    attempts = report["attempts"]["create"]
    # The first request really executed (201); its response was lost, and the
    # identical retry came back from the idempotency store: 200, same capture.
    assert [attempt["outcome"] for attempt in attempts] == ["simulated_timeout", "delivered"]
    assert attempts[0]["httpStatus"] == 201
    assert attempts[1]["httpStatus"] == 200

    steps = _by_step(report)
    assert steps["create"]["httpStatus"] == 200
    assert steps["create-stable"]["httpStatus"] == 200
    assert steps["create-stable"]["stable"] is True
    assert steps["create-terminal"]["captureId"] == report["captureId"]


def test_resume_after_files_already_committed_reuses_the_server_state(replay, saved_receipt, tmp_path) -> None:
    report = replay.run_replay(saved_receipt, tmp_path / "replay", faults={"commit": 1})

    assert report["ok"] is True, report.get("errorDetail")
    steps = _by_step(report)
    assert steps["commit"]["notFound"] == [] and steps["commit"]["mismatch"] == []

    # The replay learned what it may skip from the server, not from itself:
    # re-register reports every file committed, so no bytes were PUT twice.
    resume = steps["resume-commit"]
    assert resume["registerHttpStatus"] == 200
    assert resume["serverStates"] and set(resume["serverStates"].values()) == {"committed"}
    assert resume["recommitHttpStatus"] == 200
    assert sorted(resume["recomputed"]) == sorted(resume["serverStates"])
    assert steps["upload"]["skippedCommitted"] == 0  # nothing was skipped pre-commit

    # The resumed commit committed the same hashes: the terminal journey holds.
    assert report["finalCaptureStatus"] in replay.TERMINAL_STATUSES
    assert report["terminalEvents"] == ["verdict_ready"]


def test_retry_budget_is_bounded_and_fails_loudly(replay, saved_receipt, tmp_path) -> None:
    # Three lost finalize responses against a one-retry budget: the replay
    # must give up with a named error, not retry forever.
    report = replay.run_replay(saved_receipt, tmp_path / "replay", faults={"finalize": 3})

    assert report["ok"] is False
    assert "finalize_retry_budget_exhausted" in report["errorCodes"]
    assert len(report["attempts"]["finalize"]) == replay.MAX_RETRIES + 1


def test_replay_fails_loudly_when_the_fixture_drifts_from_the_receipt(replay, saved_receipt, tmp_path) -> None:
    receipt = json.loads(saved_receipt.read_text())
    receipt["files"][0]["sha256"] = "0" * 64
    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(receipt))

    report = replay.run_replay(drifted, tmp_path / "replay")

    assert report["ok"] is False
    assert any(code.startswith("fixture_drift:") for code in report["errorCodes"])
    assert report["captureId"] is None  # nothing was sent to the API


def test_cli_replays_a_saved_receipt_and_exits_zero(replay, saved_receipt, tmp_path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/replay_capture.py",
            "--receipt",
            str(saved_receipt),
            "--workdir",
            str(tmp_path / "cli"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    stored = json.loads((tmp_path / "cli" / "replay-receipt.json").read_text())
    assert stored["ok"] is True
    assert stored["finalCaptureStatus"] in replay.TERMINAL_STATUSES


def test_replay_rejects_a_pre_replay_receipt_missing_the_replay_block(replay, saved_receipt, tmp_path) -> None:
    """A receipt written before the replay block exists must fail loudly.

    The runner's replay block (files, idempotencyKeys, packetSha256) is what
    makes the receipt replayable; a pre-PR receipt has none of it and must be
    rejected with a named error, not replayed with guessed hashes — and the
    CLI must exit non-zero instead of reporting a green run.
    """
    receipt = json.loads(saved_receipt.read_text())
    for key in ("files", "idempotencyKeys", "packetSha256"):
        receipt.pop(key, None)
    stripped = tmp_path / "stripped" / "receipt.json"
    stripped.parent.mkdir(parents=True)
    stripped.write_text(json.dumps(receipt))

    report = replay.run_replay(stripped, tmp_path / "replay")

    assert report["ok"] is False, report
    assert any(code.startswith("missing_replay_block") for code in report["errorCodes"]), report["errorCodes"]
    assert report["captureId"] is None

    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "replay_capture.py"),
            "--receipt",
            str(stripped),
            "--workdir",
            str(tmp_path / "cli"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    stored = json.loads((tmp_path / "cli" / "replay-receipt.json").read_text())
    assert stored["ok"] is False


def test_a_crash_mid_replay_reports_ok_false(replay, saved_receipt, tmp_path) -> None:
    """No exception path may leave the report green: a crash is a failure.

    The receipt's files block is malformed (an entry without path/sha256), so
    the replay dies before the first error code would be appended. The report
    must still come back ok=False with an exception error code — and the CLI
    must exit 1.
    """
    receipt = json.loads(saved_receipt.read_text())
    receipt["files"] = [{"path": "x.bin", "sha256": "0" * 64}, {"broken": True}]
    broken = tmp_path / "broken" / "receipt.json"
    broken.parent.mkdir(parents=True)
    broken.write_text(json.dumps(receipt))

    report = replay.run_replay(broken, tmp_path / "replay")

    assert report["ok"] is False, report
    assert any(code.startswith("exception:") for code in report["errorCodes"]), report["errorCodes"]
    assert report["captureId"] is None


def test_fixture_check_flags_receipt_files_outside_the_manifest(replay) -> None:
    """A receipt claiming files the packet fixture never had is drift.

    The old comparison only looked at paths present in both sets, so an empty
    or disjoint receipt files list passed vacuously. Every receipt path must
    exist in the committed manifest.
    """
    errors: list[str] = []

    fixture = replay.check_fixture(
        replay.DEFAULT_PACKET, {"files": [{"path": "not/in/this-packet.bin", "sha256": "0" * 64}]}, errors
    )

    assert fixture is not None and fixture["drift"], fixture
    assert errors and errors[0].startswith("fixture_drift:"), errors
    assert "not/in/this-packet.bin:not-in-manifest" in fixture["drift"]
