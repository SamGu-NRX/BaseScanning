"""A run on the stub path names its stand-ins in the result it publishes.

With HSS_ORCH_STAGE_STUBS on, the result stage itself is the stub in
server/orchestrator/stubs/result.py, so the stand-in record has to come out of
that stub too: the runs whose stages are stand-ins are exactly the runs that
never reach the real composer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.orchestrator import runner
from server.orchestrator.api import start_run
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.services import bind
from server.orchestrator.tests.conftest import build_services


def test_stub_run_names_every_stand_in_including_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_ORCH_REAL_STAGES", raising=False)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert run.status == "done"

    internal = json.loads(services.storage.get_bytes(run.artifacts["result/result.internal.json"]))
    # placement is stubs_exempt, so it is the one stage that ran for real.
    expected = {stage: "stand-in" for stage in STAGE_NAMES}
    expected["placement"] = "real"
    assert internal["stageImplementations"] == expected
    stand_ins = sorted(stage for stage in STAGE_NAMES if stage != "placement")
    assert internal["standIns"] == stand_ins

    page = services.storage.get_bytes(run.artifacts["result/review.html"]).decode()
    assert f"Stand-in steps: {', '.join(stand_ins)}. Their output is not from this scan." in page

    # The app-facing result stays as the stub always wrote it.
    assert "standIns" not in json.loads(services.storage.get_bytes(run.artifacts["result/result.json"]))


def test_stub_result_refuses_a_marker_that_names_another_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # objects.json claims to describe reads. The stub result must stop on it
    # before sending verdict_ready, as the real composer would.
    publish = runner._publish_impl_marker

    def mislabel(ctx, stored, fn):
        publish(ctx, stored, fn)
        if ctx.stage == "objects":
            body = {"schema": "hss-stage-impl/v1", "stage": "reads", "implementation": "real", "callable": "x.run"}
            ctx.storage.put_bytes(stored["impl/objects.json"], json.dumps(body).encode(), "application/json")

    monkeypatch.setattr(runner, "_publish_impl_marker", mislabel)
    monkeypatch.delenv("HSS_ORCH_REAL_STAGES", raising=False)
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)

    assert run.status == "failed"
    assert "result/result.internal.json" not in run.artifacts
    events = services.state.events_after("cap_test", 0)
    assert "verdict_ready" not in {event.type for event in events}
    result_failures = [
        event.data
        for event in events
        if event.type == "stage" and event.data.get("stage") == "result" and event.data.get("status") == "failed"
    ]
    assert result_failures and all(data.get("errorCode") == "impl_invalid" for data in result_failures)
