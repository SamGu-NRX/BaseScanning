from __future__ import annotations

import json

import pytest

from scripts.cost_report import gpu_usd
from server.bench import estimate_usd
from server.common.telemetry import StageTimer, log_event


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(line) for line in out if line]


def test_log_event_keys_and_redaction(capsys: pytest.CaptureFixture[str]) -> None:
    log_event(
        "http_request",
        msg="hello",
        capture_id="cap_test",
        OPENAI_API_KEY="super-secret-value",
        XAI_API_KEY="another-secret-value",
        CAPTURE_API_TOKENS="token-one,token-two",
    )
    payload = _lines(capsys)[0]
    for key in (
        "ts",
        "level",
        "event",
        "msg",
        "service.name",
        "deployment.environment",
        "product",
        "git_sha",
    ):
        assert key in payload
    assert payload["event"] == "http_request"
    assert payload["capture_id"] == "cap_test"
    assert payload["ts"].endswith("Z")
    blob = json.dumps(payload)
    assert "super-secret-value" not in blob
    assert "another-secret-value" not in blob
    assert "token-one,token-two" not in blob
    assert "OPENAI_API_KEY" not in payload
    assert "XAI_API_KEY" not in payload
    assert "CAPTURE_API_TOKENS" not in payload


def test_log_event_drops_unknown_keys_and_scrubs_values(capsys: pytest.CaptureFixture[str]) -> None:
    log_event(
        "http_request",
        msg="Authorization: Bearer super-secret-token",
        DATABASE_URL="postgres://user:password@db/house",
        capture_id="https://storage.googleapis.com/b/o?X-Goog-Signature=abcdef",
    )
    payload = _lines(capsys)[0]
    blob = json.dumps(payload)
    assert "DATABASE_URL" not in payload
    assert "postgres://user:password@db/house" not in blob
    assert "super-secret-token" not in blob
    assert "X-Goog-Signature=abcdef" not in blob
    assert payload["msg"] == "[redacted]"
    assert payload["capture_id"] == "[redacted]"


def test_stage_timer_failed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(RuntimeError):
        with StageTimer("poses", "cap_test", "run_test", 2):
            raise RuntimeError("boom")
    events = {row["event"]: row for row in _lines(capsys)}
    assert "stage_started" in events
    failed = events["stage_failed"]
    assert failed["error_code"] == "RuntimeError"
    assert failed["stage"] == "poses"
    assert failed["attempt"] == 2
    assert "duration_ms" in failed


def test_cost_report_matches_estimate(tmp_path) -> None:
    est = estimate_usd("L4", 10.0)
    record = {"kind": "run", "house": "eth3d-facade", "gpu": "L4", "client_s": 10.0, "est_usd": est}
    path = tmp_path / "bench.jsonl"
    path.write_text(json.dumps(record) + "\n")
    reported = gpu_usd(path)
    assert abs(reported - est) < 0.0001
