"""Checks for ``scripts/capture_flow_e2e.py``: the receipt and the failure exit.

The happy-path run replays the fixture-test journey through the runner and
inspects the receipt it writes; the failure path injects one never-uploaded
file and asserts the receipt records the error codes and ``main`` exits
nonzero. The CLI itself is exercised the way a reviewer would run it, as a
subprocess from ``backend/``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRST_FILE = "keyframes/k00001.jpg"


def _read_receipt(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def runner():
    import scripts.capture_flow_e2e as mod

    return mod


def test_happy_path_receipt_records_ids_statuses_counts(runner, tmp_path) -> None:
    receipt = runner.run_capture_flow(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    runner.write_receipt(receipt, receipt_path)
    stored = _read_receipt(receipt_path)

    assert receipt["ok"] is True, receipt.get("errorDetail")
    assert receipt["errorCodes"] == []
    assert receipt["captureId"] and receipt["runId"]
    assert stored["captureId"] == receipt["captureId"] and stored["runId"] == receipt["runId"]

    by_stage = {stage["stage"]: stage for stage in receipt["stages"]}
    assert by_stage["create"]["httpStatus"] == 201
    assert by_stage["create"]["idempotencyKey"]
    assert by_stage["register"]["httpStatus"] == 200
    assert by_stage["commit"]["httpStatus"] == 200
    assert by_stage["commit"]["notFound"] == [] and by_stage["commit"]["mismatch"] == []
    assert by_stage["finalize"]["httpStatus"] == 202
    assert by_stage["finalize"]["missing"] == []
    assert by_stage["events"]["count"] > 0 and "files_committed" in by_stage["events"]["types"]
    assert by_stage["events"]["validateStageForRun"] is True

    counts = receipt["fileCounts"]
    assert counts["expected"] == counts["registered"] == counts["uploaded"] == counts["committed"]
    assert receipt["finalCaptureStatus"] == "processing"
    assert by_stage["status"]["filesCommitted"] == counts["expected"]


def test_missing_upload_fails_with_error_codes_and_nonzero_exit(runner, tmp_path) -> None:
    receipt = runner.run_capture_flow(tmp_path, skip_upload=FIRST_FILE)

    assert receipt["ok"] is False
    assert receipt["finalCaptureStatus"] == "awaiting_files"
    assert "finalize_missing_files" in receipt["errorCodes"]
    assert "unexpected_final_status" in receipt["errorCodes"]
    assert receipt["fileCounts"]["uploaded"] == receipt["fileCounts"]["expected"] - 1
    finalize = next(stage for stage in receipt["stages"] if stage["stage"] == "finalize")
    assert finalize["missing"] == [FIRST_FILE]

    # The same fault through the CLI exits nonzero and still writes the receipt.
    code = runner.main(["--workdir", str(tmp_path / "cli"), "--skip-upload", FIRST_FILE])
    assert code == 1
    stored = _read_receipt(tmp_path / "cli" / "receipt.json")
    assert stored["ok"] is False and stored["errorCodes"]


def test_cli_runs_from_a_clean_directory_and_exits_zero(runner, tmp_path) -> None:
    workdir = tmp_path / "clean"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "scripts/capture_flow_e2e.py", "--workdir", str(workdir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = _read_receipt(workdir / "receipt.json")
    assert receipt["ok"] is True and receipt["finalCaptureStatus"] == "processing"


def test_rerun_into_a_used_workdir_fails_closed(runner, tmp_path) -> None:
    """The replay action the receipt invites — rerun with its recorded --workdir —
    hits the already-present packet copy, so the crash must exit 1, not report
    success with the errorDetail tucked beside ok:true."""
    used = tmp_path / "replay"
    assert runner.run_capture_flow(used)["ok"] is True

    receipt = runner.run_capture_flow(used)
    assert receipt["ok"] is False
    assert "runner_exception" in receipt["errorCodes"]
    assert receipt["errorDetail"]

    code = runner.main(["--workdir", str(used)])
    assert code == 1
    stored = _read_receipt(used / "receipt.json")
    assert stored["ok"] is False and "runner_exception" in stored["errorCodes"]


def test_missing_packet_dir_fails_closed(runner, tmp_path) -> None:
    code = runner.main(["--packet", str(tmp_path / "no-such-packet"), "--workdir", str(tmp_path / "w")])
    assert code == 1
    stored = _read_receipt(tmp_path / "w" / "receipt.json")
    assert stored["ok"] is False
    assert "runner_exception" in stored["errorCodes"]
    assert stored["errorDetail"]
