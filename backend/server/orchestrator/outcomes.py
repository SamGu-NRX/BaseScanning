"""Failure-to-outcome table. Row order matches the brief §4.8; the first match wins."""

from __future__ import annotations

from dataclasses import dataclass

from server.contracts.jobs import CaptureStatus


@dataclass(frozen=True)
class Facts:
    """Inputs for one decision. Unset flags are false and do not match a row."""

    validate_failed: bool = False
    images_usable: bool = True
    required_exhausted: bool = False
    exhausted_stage: str = ""
    exhausted_code: str = ""
    stale_stage: str = ""
    coordinator_dead: bool = False
    hard_stop: bool = False
    no_meter_model: bool = False
    fragmented: bool = False
    member_retake: bool = False
    degraded: tuple[str, ...] = ()
    vlm_budget_exhausted: bool = False
    spend_cap: bool = False
    artifact_invalid: str = ""
    site_decision: str = ""
    views_needed: bool = False
    show_reject_to_homeowner: bool = False


@dataclass(frozen=True)
class Decision:
    row: int
    capture_status: CaptureStatus
    failure_code: str | None
    event: str | None
    next: str | None
    outcome_kind: str | None
    continue_pipeline: bool = False


def decide(facts: Facts) -> Decision:
    """Fourteen rows, in brief order. Rows 11–14 are applied by the result stage."""
    if facts.validate_failed and not facts.images_usable:
        return Decision(1, "failed", "packet_invalid", "failed", "none", None)
    if facts.validate_failed and facts.images_usable:
        return Decision(2, "manual_review", "packet_invalid", "failed", "manual_review", "manual_review")
    if facts.required_exhausted:
        code = f"stage_exhausted:{facts.exhausted_stage}:{facts.exhausted_code}"
        return Decision(3, "manual_review", code, "failed", "manual_review", "manual_review")
    if facts.stale_stage:
        return Decision(
            4,
            "manual_review",
            f"stage_stale:{facts.stale_stage}",
            "failed",
            "manual_review",
            "manual_review",
        )
    if facts.coordinator_dead:
        return Decision(4, "manual_review", "coordinator_dead", "failed", "manual_review", "manual_review")
    if facts.hard_stop:
        return Decision(4, "manual_review", "hard_stop", "failed", "manual_review", "manual_review")
    if facts.no_meter_model:
        return Decision(5, "manual_review", "no_meter_anchor", None, None, "manual_review", continue_pipeline=True)
    if facts.fragmented:
        return Decision(6, "processing", None, None, None, None, continue_pipeline=True)
    if facts.member_retake:
        return Decision(7, "needs_views", None, "retake_request", None, "needs_more_photos")
    if facts.degraded:
        joined = ",".join(facts.degraded)
        return Decision(8, "manual_review", f"degraded:{joined}", None, None, "manual_review")
    if facts.vlm_budget_exhausted:
        return Decision(9, "manual_review", "vlm_budget_exhausted", "failed", "manual_review", "manual_review")
    if facts.spend_cap:
        return Decision(9, "manual_review", "spend_cap", "failed", "manual_review", "manual_review")
    if facts.artifact_invalid:
        return Decision(
            10,
            "manual_review",
            f"artifact_invalid:{facts.artifact_invalid}",
            "failed",
            "manual_review",
            "manual_review",
        )
    if facts.site_decision == "pass":
        return Decision(11, "complete", None, "verdict_ready", None, "eligible")
    if facts.site_decision == "reject":
        if facts.show_reject_to_homeowner:
            return Decision(12, "complete", None, "verdict_ready", None, "not_eligible")
        return Decision(12, "manual_review", None, "verdict_ready", None, "manual_review")
    if facts.site_decision == "manual_review" and facts.views_needed:
        return Decision(13, "needs_views", None, "retake_request", None, "needs_more_photos")
    if facts.site_decision == "manual_review":
        return Decision(14, "manual_review", None, "verdict_ready", None, "manual_review")
    raise RuntimeError("outcome table did not match")
