"""Verdict v0. `view_angle_deg` stays off until the copied schema carries it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from server.contracts._base import JsonInt, JsonNum, Point2, SpanLoose, Strict, keep_null

Outcome3 = Literal["pass", "fail", "unsure"]
CoverageStatus = Literal["observed", "grazing", "partial", "unobserved", "generated"]
DataKind = Literal["real", "synthetic", "hybrid"]


class VerdictSceneRef(Strict):
    scene_id: str
    site: str
    sha256: str | None = None
    producer_path: str | None = None
    data: DataKind | None = None


class Evaluator(Strict):
    path: str
    version: str | None = None
    rules_profile: str | None = None
    rules_sha256: str | None = None
    ground_rule: str | None = None
    auto_approve: bool | None = None
    runtime_s: JsonNum | None = Field(default=None, ge=0)
    cost_usd: JsonNum | None = Field(default=None, ge=0)
    ground_incidence_deg: JsonNum | None = None


class SiteAnswer(Strict):
    decision: Literal["pass", "manual_review", "reject"]
    reasons: list[str]
    chosen_candidate: str | None = keep_null()


class EvidenceRegion(Strict):
    wall_id: str | None = None
    s_m: SpanLoose | None = None
    height_m: SpanLoose | None = None
    out_m: SpanLoose | None = None
    element_ids: list[str] | None = None
    images: list[str] | None = None
    description: str | None = None


class CoverageRule(Strict):
    min_views: JsonInt = Field(ge=1)
    max_incidence_deg: JsonNum | None = Field(json_schema_extra={"keep_null": True})
    surface: Literal["wall", "ground", "free"] | None = None
    observed_share: JsonNum | None = None
    max_footprint_m: JsonNum | None = Field(default=None, gt=0)


class SensitivityGrade(Strict):
    max_incidence_deg: JsonNum
    status: CoverageStatus
    observed_share: JsonNum | None = None
    views_median: JsonNum | None = None
    rule_id: str | None = None
    max_footprint_m: JsonNum | None = Field(default=None, gt=0)


class CoverageEvidence(Strict):
    region: EvidenceRegion
    status: CoverageStatus
    observed_share: JsonNum = Field(ge=0, le=1)
    views_median: JsonNum = Field(ge=0)
    best_incidence_deg: JsonNum | None = Field(
        json_schema_extra={"keep_null": True},
        ge=0,
        le=90,
    )
    rule: CoverageRule
    probes: JsonInt | None = Field(default=None, ge=0)
    seen_share: JsonNum | None = Field(default=None, ge=0, le=1)
    views_min: JsonInt | None = Field(default=None, ge=0)
    worst_best_incidence_deg: JsonNum | None = keep_null(ge=0, le=90)
    method: str | None = None
    sensitivity: list[SensitivityGrade] | None = None


class Measured(Strict):
    value_ft: JsonNum
    plus_minus_ft: JsonNum = Field(ge=0)
    coverage: list[CoverageEvidence] = Field(min_length=1)
    subject: str | None = None
    element_ids: list[str] | None = None


class Fact(Strict):
    value: str | JsonNum | bool | None
    clean: bool
    coverage: list[CoverageEvidence] = Field(min_length=1)
    raw: str | None = None


class Threshold(Strict):
    value_ft: JsonNum
    comparison: Literal["at_least", "at_most"]
    review_value_ft: JsonNum | None = None
    rule_key: str | None = None
    source: str | None = None
    placeholder: bool | None = None


class CriterionEvidence(Strict):
    element_ids: list[str] | None = None
    views: list[str] | None = None
    coverage: Literal["observed", "partial", "unobserved", "not_applicable"] | None = None


class Criterion(Strict):
    id: str
    scope: Literal["site", "candidate"]
    outcome: Outcome3
    reason: str
    measured: Measured | None = Field(json_schema_extra={"keep_null": True})
    threshold: Threshold | None = Field(json_schema_extra={"keep_null": True})
    candidate_id: str | None = None
    unsure_cause: (
        Literal[
            "margin",
            "unobserved",
            "grazing",
            "unknown_attribute",
            "rule_requires_review",
            "generated_only",
            "not_evaluated",
        ]
        | None
    ) = None
    fact: Fact | None = None
    margin_ft: JsonNum | None = keep_null()
    margin_over_uncertainty: JsonNum | None = keep_null()
    evidence: CriterionEvidence | None = None
    views_needed: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def fact_not_null(cls, data: Any) -> Any:
        if isinstance(data, dict) and "fact" in data and data["fact"] is None:
            raise ValueError("fact cannot be null")
        return data


class Candidate(Strict):
    id: str
    wall_id: str
    start_s_m: SpanLoose
    outcome: Outcome3
    failing: list[str] | None = None
    unsure: list[str] | None = None
    footprint_m: list[Point2] | None = None


class ViewNeeded(Strict):
    id: str
    kind: Literal["band", "past_end", "close_up", "confirm_attribute", "region"]
    criteria: list[str]
    why: str
    band: Literal["wall", "ground", "overhead", "facing"] | None = None
    wall_id: str | None = None
    s_m: SpanLoose | None = None
    out_m: JsonNum | None = Field(default=None, ge=0)
    side: Literal["left", "right"] | None = None
    target: str | None = None


class Verdict(Strict):
    format: Literal["base-verdict/v0"]
    verdict_id: str = Field(min_length=1)
    scene: VerdictSceneRef
    evaluator: Evaluator
    site: SiteAnswer
    criteria: list[Criterion]
    views_needed: list[ViewNeeded]
    candidates: list[Candidate] | None = None


def verdict_contract_errors(verdict: dict[str, Any]) -> list[str]:
    """Mirror evalharness.sceneio.verdict_contract_errors. Pass/fail need observed coverage."""

    errors: list[str] = []
    for criterion in verdict.get("criteria") or []:
        if criterion.get("outcome") == "unsure":
            continue
        evidence: list[dict[str, Any]] = []
        for key in ("measured", "fact"):
            block = criterion.get(key)
            if block:
                evidence.extend(block.get("coverage") or [])
        where = f"criteria[{criterion.get('id')}"
        if criterion.get("candidate_id"):
            where += "@" + criterion["candidate_id"]
        where += "]"
        if not evidence:
            if criterion.get("outcome") == "pass":
                errors.append(f"{where}: pass with no measurement or fact, so no coverage evidence")
            continue
        for item in evidence:
            rule = item.get("rule") or {}
            bad: list[str] = []
            if item.get("status") != "observed":
                bad.append(f"status {item.get('status')}")
            views = item.get("views_median", item.get("views_min", 0))
            minimum = rule.get("min_views", 1)
            if views < minimum:
                bad.append(f"views {views} < {minimum}")
            angle = item.get("best_incidence_deg")
            limit = rule.get("max_incidence_deg")
            if angle is not None and limit is not None and angle > limit:
                bad.append(f"viewing angle {angle} deg > {limit}")
            if bad:
                errors.append(
                    f"{where}: {criterion.get('outcome')} on {item.get('region')}: {', '.join(bad)} (must be unsure)"
                )
    return errors
