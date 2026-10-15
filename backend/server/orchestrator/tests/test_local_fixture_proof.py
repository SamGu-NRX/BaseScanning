"""Proof: the committed packet runs end to end through the local entry point.

One committed capture packet (``tests/data/packets/synthetic-meter.tar``) drives
``python -m server.orchestrator.cli run-local`` — the orchestrator's supported
local entry point — with stage stubs and no Modal, GPU, network or credentials.

Every stubbed stage is named here and asserted from the actual execution trace:
validate, poses, objects, reads, scale, dense, coverage, scene, objects_3d,
criteria, result (the eleven of ``graph.STAGE_NAMES``, all served by
``server/orchestrator/stubs/<stage>.py``).

Two runs of the same packet must agree on stage outcomes and on the
deterministic artifact bytes, and an injected stage failure must land as
recorded failure data, not as an uncaught coordinator crash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from server.contracts.result import ResultResponse
from server.orchestrator import runner
from server.orchestrator.cli import main
from server.orchestrator.graph import STAGE_NAMES

FIXTURE = Path(__file__).parent / "data" / "packets" / "synthetic-meter.tar"
CAPTURE_ID = "cap_fixture_orch"

# The documented local contract (orchestrator README "Run locally"). ``run-local``
# pins these in its own Settings too; the pin here keeps the test honest if that
# ever drifts.
LOCAL_ENV = {
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "local",
    "HSS_ORCH_STAGE_STUBS": "1",
    "VISION_EXTERNAL_CALLS": "off",
    "SMP_ENVIRONMENT": "dev",
}


@pytest.fixture
def no_modal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the run if anything reaches for Modal."""
    touched: list[str] = []

    def _forbidden(*args: Any, **kwargs: Any) -> bool:
        touched.append("modal_available")
        return False

    monkeypatch.setattr("server.orchestrator.cli.modal_available", _forbidden)
    return touched


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in LOCAL_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def stub_trace(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Record (stage, stubs?) for every stage the coordinator dispatches."""
    calls: list[tuple[str, bool]] = []
    real = runner.stage_callable

    def spy(stage: str, *, stubs: bool) -> Callable[..., Any]:
        calls.append((str(stage), stubs))
        return real(stage, stubs=stubs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(runner, "stage_callable", spy)
    return calls


def _events(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]


def _latest_stage_status(events: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for event in events:
        if event["type"] != "stage":
            continue
        data = event.get("data") or {}
        if data.get("stage"):
            latest[str(data["stage"])] = data
    return latest


def _terminal(events: list[dict]) -> list[str]:
    return [event["type"] for event in events if event["type"] in {"verdict_ready", "retake_request", "failed"}]


def _stored(out: Path, key: str) -> bytes:
    return (out / "store" / key).read_bytes()


def _run(out: Path) -> dict:
    return json.loads((out / "run.json").read_text())


def _run_local(out: Path, monkeypatch: pytest.MonkeyPatch, no_modal: list[str]) -> int:
    assert not no_modal, "Modal was reached during run-local"
    return main(["run-local", "--tar", str(FIXTURE), "--variant", "arkit-nonlidar", "--out", str(out)])


def test_committed_packet_runs_to_a_terminal_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_modal: list[str], env: None, stub_trace: list[tuple[str, bool]]
) -> None:
    out = tmp_path / "out"
    code = _run_local(out, monkeypatch, no_modal)
    assert code == 0
    assert not no_modal, "run-local reached for Modal"

    run = _run(out)
    assert run["status"] == "done"
    assert run["outcome_kind"] == "manual_review"  # stub verdicts are manual_review, views_needed []
    assert run["failure_code"] is None
    assert run["packet_sha256"] == hashlib.sha256(_stored(out, f"captures/{CAPTURE_ID}/packet.json")).hexdigest()

    # Every stubbed stage, named from the execution trace: all eleven, stubs on.
    traced = {stage for stage, stubs in stub_trace if stubs}
    assert traced == set(STAGE_NAMES), f"stubbed stages traced: {sorted(traced)}"

    # Stage records: all eleven reach done. (The graph still *plans* GPU names on
    # stage events; the stubs bypass GPU execution — proven by the stub trace above.)
    latest = _latest_stage_status(_events(out))
    assert set(latest) == set(STAGE_NAMES)
    for stage in STAGE_NAMES:
        assert latest[stage]["status"] == "done", stage

    # Exactly one terminal outcome, and it is the verdict.
    terminals = _terminal(_events(out))
    assert terminals == ["verdict_ready"]

    # The artifacts the API consumes (server/api/routes/result.py reads
    # run.artifacts["result/result.json"]): present, stored, and schema-valid.
    assert run["artifacts"]["result/result.json"].startswith(f"captures/{CAPTURE_ID}/runs/{run['run_id']}/")
    result = ResultResponse.model_validate(json.loads(_stored(out, run["artifacts"]["result/result.json"])))
    assert result.status == "manual_review"
    assert result.outcome.kind == "manual_review"
    for key in ("result/review.html", "result/preview.ply", "verdicts/verdict.C.json"):
        assert run["artifacts"][key]
        assert len(_stored(out, run["artifacts"][key])) > 0


def test_same_packet_twice_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_modal: list[str], env: None
) -> None:
    """Stub outputs are pure: same packet bytes, same artifact bytes, same stage outcomes."""
    outs = [tmp_path / "first", tmp_path / "second"]
    digests: list[dict[str, str]] = []
    signatures: list[list[tuple[str, str]]] = []
    for out in outs:
        assert _run_local(out, monkeypatch, no_modal) == 0
        run = _run(out)
        digests.append(
            {
                key: hashlib.sha256(_stored(out, stored)).hexdigest()
                for key, stored in run["artifacts"].items()
                if key in {"verdicts/verdict.C.json", "result/result.internal.json", "scene/scene.json"}
            }
        )
        latest = _latest_stage_status(_events(out))
        signatures.append([(stage, latest[stage]["status"]) for stage in STAGE_NAMES])
    assert digests[0] == digests[1]
    assert signatures[0] == signatures[1]


def test_stage_failure_is_recorded_data_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_modal: list[str], env: None
) -> None:
    """``scale`` exhausted its attempts: the run ends as recorded failure data."""
    monkeypatch.setenv("HSS_ORCH_TEST_FAIL_STAGE", "scale:compute_error:2")
    out = tmp_path / "out"
    assert _run_local(out, monkeypatch, no_modal) == 0  # no uncaught coordinator crash

    run = _run(out)
    assert run["status"] == "failed"
    assert run["failure_code"] == "stage_exhausted:scale:compute_error"
    assert run["outcome_kind"] == "manual_review"  # row 3: exhausted required stage -> manual review

    latest = _latest_stage_status(_events(out))
    assert latest["scale"]["status"] == "failed"
    assert latest["scale"]["attempt"] >= 2  # retried before being recorded failed

    terminals = _terminal(_events(out))
    assert terminals == ["failed"]

    # Even the failed run publishes the API's result document (manual result).
    result = ResultResponse.model_validate(json.loads(_stored(out, run["artifacts"]["result/result.json"])))
    assert result.status == "manual_review"
