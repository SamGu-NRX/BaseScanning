"""A claim that never gets a run record must not stick the capture."""

from __future__ import annotations

import json

from server.orchestrator.api import start_run, sweep
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.outcomes import Facts, decide
from server.orchestrator.services import bind, run_id_for
from server.orchestrator.tests.conftest import build_services


def test_dead_claimer_is_taken_over(tmp_path, monkeypatch) -> None:
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=True)
    monkeypatch.setattr(RunCoordinator, "drive", lambda *args, **kwargs: "running")
    run_id = run_id_for(services.storage.get_bytes(uri), "cap_test")
    services.state.put_if_absent(
        f"run_claim:{run_id}",
        {
            "capture_id": "cap_test",
            "packet_sha256": digest,
            "packet_uri": uri,
            "at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert services.state.get_run(run_id) is None
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert run.run_id == run_id
    assert services.state.get_run(run_id) is not None
    assert services.state.get_stage(run_id, "validate") is not None


def test_sweeper_takes_over_a_dead_claim(tmp_path, monkeypatch) -> None:
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=True)
    monkeypatch.setattr(RunCoordinator, "drive", lambda *args, **kwargs: "running")
    run_id = run_id_for(services.storage.get_bytes(uri), "cap_test")
    services.state.put_if_absent(
        f"run_claim:{run_id}",
        {
            "capture_id": "cap_test",
            "packet_sha256": digest,
            "packet_uri": uri,
            "at": "2000-01-01T00:00:00+00:00",
        },
    )
    with bind(services):
        assert sweep() == 1
    assert services.state.get_run(run_id) is not None


def test_fresh_claim_without_a_run_is_not_stolen(tmp_path, monkeypatch) -> None:
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=True)
    monkeypatch.setattr("server.orchestrator.api.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(RunCoordinator, "drive", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("drive")))
    run_id = run_id_for(services.storage.get_bytes(uri), "cap_test")
    services.state.put_if_absent(
        f"run_claim:{run_id}",
        {
            "capture_id": "cap_test",
            "packet_sha256": digest,
            "packet_uri": uri,
            "at": services.clock.iso(),
        },
    )
    with bind(services):
        try:
            start_run("cap_test", digest, uri)
        except KeyError as exc:
            assert exc.args == (run_id,)
        else:
            raise AssertionError("fresh claim was stolen")
    assert services.state.get_run(run_id) is None


def test_early_cost_failure_still_records_the_run(tmp_path, monkeypatch) -> None:
    services, uri, digest = build_services(tmp_path, "memory", stage_stubs=True)

    def boom(_run: object) -> None:
        raise RuntimeError("scan")

    monkeypatch.setattr("server.orchestrator.api._add_early_vlm", boom)
    monkeypatch.setattr(RunCoordinator, "drive", lambda *args, **kwargs: "running")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    assert services.state.get_run(run.run_id) is not None
    assert services.state.get_stage(run.run_id, "validate") is not None


def test_exhausted_stage_writes_a_manual_review_result(tmp_path) -> None:
    services, _uri, digest = build_services(tmp_path, "memory")
    from datetime import timedelta

    from server.contracts.jobs import RunRecord

    now = services.clock.iso()
    run = RunRecord(
        run_id="run_exhausted",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=now,
        deadline_at=(services.clock.now() + timedelta(hours=1)).isoformat(),
        artifacts={},
        spend_usd={},
    )
    services.state.put_run(run)
    decision = decide(Facts(required_exhausted=True, exhausted_stage="scale", exhausted_code="timeout"))
    RunCoordinator(services)._close(run, decision, "scale exceeded its budget")
    stored = services.state.get_run(run.run_id)
    assert stored is not None
    key = stored.artifacts["result/result.json"]
    payload = json.loads(services.storage.get_bytes(key))
    assert payload["outcome"]["kind"] == "manual_review"
    assert payload["outcome"]["message"] == "stage_exhausted:scale:timeout"
    assert payload["site"]["decision"] == "manual_review"
