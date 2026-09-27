"""Seam fixes: valid stub result, real Modal bodies, purge, privacy, webhook retry."""

from __future__ import annotations

import json
import os

from server.contracts.result import ResultResponse
from server.orchestrator.api import purge_capture, start_run
from server.orchestrator.modal_functions import coordinate_run_impl, pipeline_resources, run_stage_impl
from server.orchestrator.privacy import apply_deployed_privacy, photo_egress_allowed
from server.orchestrator.services import bind
from server.orchestrator.stateio import get_json, put_json
from server.orchestrator.sweeper import sweep
from server.orchestrator.tests.conftest import build_services


def test_stub_result_validates_as_result_response(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
        raw = json.loads(services.storage.get_bytes(run.artifacts["result/result.json"]))
    parsed = ResultResponse.model_validate(raw)
    assert parsed.status == "manual_review"
    assert parsed.outcome is not None
    assert parsed.outcome.kind == "manual_review"
    assert parsed.outcome.message
    assert "verdictSha256" not in raw


def test_pipeline_knobs_ignore_legacy_names() -> None:
    resources = pipeline_resources(
        {
            "gpu": "H100",
            "min_containers": 9,
            "max_containers": 9,
            "pipeline_gpu": "A10G",
            "pipeline_min_containers": 1,
            "pipeline_max_containers": 4,
        }
    )
    assert resources == {"gpu": "A10G", "min_containers": 1, "max_containers": 4}
    assert pipeline_resources({})["gpu"] == "L4"


def test_modal_bodies_call_the_coordinator(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
        assert coordinate_run_impl(run.run_id) == run.run_id
        produced = run_stage_impl(run.run_id, "validate", 1)
    assert produced["gpu"] == "L4"
    assert "packet/validation.json" in produced["artifacts"]


def test_missing_environment_blocks_photo_egress(monkeypatch) -> None:
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    apply_deployed_privacy()
    assert os.environ["SMP_ENVIRONMENT"] == "prod"
    assert os.environ["VISION_EXTERNAL_CALLS"] == "off"
    assert photo_egress_allowed(environment=None, vision="on") is False
    assert photo_egress_allowed(environment="staging", vision="on") is False
    assert photo_egress_allowed(environment="prod", vision="on") is True
    assert photo_egress_allowed(environment="dev", vision="off") is False


def test_purge_leaves_only_the_tomb(tmp_path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    planted = services.storage.root / "captures" / "cap_test" / "runs" / "run_x" / "result" / "review.html"
    planted.parent.mkdir(parents=True)
    planted.write_text("customer photo")
    services.storage._meta.pop("captures/cap_test/packet.json", None)
    with bind(services):
        run = start_run("cap_test", digest, uri)
        services.state.put_kv(f"confirm:{run.capture_id}:meter_number", {"value": "MTR 12345678"})
        services.state.add_spend(run.capture_id, 1.25)
        work = services.work_root / run.capture_id / "frame.jpg"
        work.parent.mkdir(parents=True)
        work.write_bytes(b"pixels")
        assert (services.work_root / run.run_id).is_dir()
        purge_capture(run.capture_id)
        purge_capture(run.capture_id)
        assert services.state.runs_for_capture(run.capture_id) == []
        assert services.state.get_stage(run.run_id, "validate") is None
        assert services.state.get_kv(f"confirm:{run.capture_id}:meter_number") is None
        assert services.state.get_spend(run.capture_id) == 0.0
        assert services.state.get_kv(f"tomb:{run.capture_id}")["capture_id"] == run.capture_id
        assert not (services.work_root / run.run_id).exists()
        assert not (services.work_root / run.capture_id).exists()
        assert not planted.exists()
        _assert_only_tomb(services.state, run.capture_id)


def test_sweeper_schedules_webhook_retries(tmp_path) -> None:
    services, _uri, _digest = build_services(tmp_path, "memory")
    put_json(services.state, "webhook:run_1", {"status": "webhook_failed", "attempt": 0})
    put_json(services.state, "webhook:run_done", {"status": "delivered", "attempt": 0})
    with bind(services):
        sweep(services, retry_schedule=[30, 120, 600, 1800, 7200])
    failed = get_json(services.state, "webhook:run_1")
    assert failed["next_delay_s"] == 30
    assert failed["attempt"] == 1
    assert get_json(services.state, "webhook:run_done")["attempt"] == 0


def _assert_only_tomb(state: object, capture_id: str) -> None:
    leftovers: list[str] = []
    for attr in ("_kv", "_blobs", "_spend", "_runs", "_stages", "_events", "_files", "_captures"):
        mapping = getattr(state, attr, None)
        if not isinstance(mapping, dict):
            continue
        for key in mapping:
            text = key if isinstance(key, str) else str(key)
            if capture_id in text and not text.startswith("tomb:"):
                leftovers.append(f"{attr}:{text}")
    assert leftovers == []
