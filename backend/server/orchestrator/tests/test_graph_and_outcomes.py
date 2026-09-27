"""Graph ready-set, GPU attempts and the §4.8 outcome rows."""

from __future__ import annotations

import pytest

from server.contracts.jobs import CaptureStatus, StageName, StageStatus
from server.orchestrator.config import OrchConfig
from server.orchestrator.graph import GPU_BY_ATTEMPT, STAGE_NAMES, STAGES, gpu_for_attempt, ready_stages
from server.orchestrator.outcomes import Facts, decide
from server.orchestrator.spend import vlm_budget_usd


def _status(**overrides: StageStatus) -> dict[StageName, StageStatus]:
    base = {name: "pending" for name in STAGE_NAMES}
    base.update(overrides)
    return base


def test_parallel_ready_after_validate() -> None:
    status = _status(**{"validate": "done"})
    ready = set(ready_stages(status))
    assert ready == {"poses", "objects", "reads"}


def test_scene_waits_for_coverage_and_terminal_objects() -> None:
    status = _status(
        **{
            "validate": "done",
            "poses": "done",
            "reads": "done",
            "scale": "done",
            "dense": "done",
            "coverage": "done",
            "objects": "running",
        }
    )
    assert "scene" not in ready_stages(status)
    status["objects"] = "failed"
    assert "scene" in ready_stages(status)


def test_objects_3d_waits_for_scene_and_criteria_waits_for_objects_3d() -> None:
    status = _status(
        **{
            "validate": "done",
            "poses": "done",
            "reads": "done",
            "objects": "done",
            "scale": "done",
            "dense": "done",
            "coverage": "done",
            "scene": "running",
        }
    )
    assert "objects_3d" not in ready_stages(status)
    status["scene"] = "done"
    assert "objects_3d" in ready_stages(status)
    assert "criteria" not in ready_stages(status)
    status["objects_3d"] = "done"
    assert "criteria" in ready_stages(status)


def test_gpu_by_attempt() -> None:
    assert GPU_BY_ATTEMPT == ("L4", "L4", "A100-40GB")
    assert STAGES["poses"].gpu_by_attempt == GPU_BY_ATTEMPT
    assert [gpu_for_attempt(STAGES["poses"], n) for n in (1, 2, 3)] == ["L4", "L4", "A100-40GB"]
    assert gpu_for_attempt(STAGES["validate"], 1) is None


def test_vlm_budget_hits_zero_at_hard_cap() -> None:
    config = OrchConfig()
    assert config.vlm_run_soft_usd == 0.30
    assert vlm_budget_usd(config.vlm_run_hard_usd, config) == 0.0
    assert vlm_budget_usd(config.vlm_run_hard_usd - 0.01, config) == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("facts", "status", "code", "event"),
    [
        (Facts(validate_failed=True, images_usable=False), "failed", "packet_invalid", "failed"),
        (Facts(validate_failed=True, images_usable=True), "manual_review", "packet_invalid", "failed"),
        (
            Facts(required_exhausted=True, exhausted_stage="poses", exhausted_code="gpu_oom"),
            "manual_review",
            "stage_exhausted:poses:gpu_oom",
            "failed",
        ),
        (Facts(stale_stage="poses"), "manual_review", "stage_stale:poses", "failed"),
        (Facts(coordinator_dead=True), "manual_review", "coordinator_dead", "failed"),
        (Facts(hard_stop=True), "manual_review", "hard_stop", "failed"),
        (Facts(no_meter_model=True), "manual_review", "no_meter_anchor", None),
        (Facts(fragmented=True), "processing", None, None),
        (Facts(member_retake=True), "needs_views", None, "retake_request"),
        (Facts(degraded=("reads",)), "manual_review", "degraded:reads", None),
        (Facts(vlm_budget_exhausted=True), "manual_review", "vlm_budget_exhausted", "failed"),
        (Facts(spend_cap=True), "manual_review", "spend_cap", "failed"),
        (Facts(artifact_invalid="scene"), "manual_review", "artifact_invalid:scene", "failed"),
        (Facts(site_decision="pass"), "complete", None, "verdict_ready"),
        (
            Facts(site_decision="reject", show_reject_to_homeowner=False),
            "manual_review",
            None,
            "verdict_ready",
        ),
        (Facts(site_decision="reject", show_reject_to_homeowner=True), "complete", None, "verdict_ready"),
        (Facts(site_decision="manual_review", views_needed=True), "needs_views", None, "retake_request"),
        (Facts(site_decision="manual_review", views_needed=False), "manual_review", None, "verdict_ready"),
    ],
)
def test_outcome_rows(facts: Facts, status: CaptureStatus, code: str | None, event: str | None) -> None:
    decision = decide(facts)
    assert decision.capture_status == status
    assert decision.failure_code == code
    assert decision.event == event
