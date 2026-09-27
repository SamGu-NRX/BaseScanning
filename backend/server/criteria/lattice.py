"""Outcome lattice: coverage does not live here; C2 vetoes, the judge only downgrades."""

from __future__ import annotations

from typing import Literal

from server.criteria.c3.facts import DECISION
from server.criteria.config import CriteriaConfig

Outcome = Literal["pass", "unsure", "fail"]
Decision = Literal["pass", "manual_review", "reject"]

_SEV = {"pass": 0, "unsure": 1, "fail": 2}
_DEC = {"pass": 2, "manual_review": 1, "reject": 0}


def c2_veto(c1: Outcome, c2: Outcome | None) -> Outcome:
    """A C2 fail or unsure turns a C1 pass into unsure. C2 never upgrades."""
    if c2 is None:
        return c1
    if c1 == "pass" and c2 in {"fail", "unsure"}:
        return "unsure"
    return c1


def judge_join(pipeline: Decision, judge: Decision | None, may_reject: bool) -> Decision:
    """min() on reject < manual_review < pass. A judge reject clamps unless may_reject."""
    if judge is None:
        return pipeline
    if judge == "reject" and not may_reject:
        judge = "manual_review"
    return pipeline if _DEC[pipeline] <= _DEC[judge] else judge


def margin_outcome(
    value_ft: float, plus_minus_ft: float, threshold_ft: float, comparison: str
) -> tuple[Outcome, float, float | None]:
    """Signed margin toward passing. Pass needs margin/uncertainty > 1, fail needs < -1."""
    margin = (threshold_ft - value_ft) if comparison == "at_most" else (value_ft - threshold_ft)
    if plus_minus_ft <= 0:
        return "unsure", margin, None
    mou = margin / plus_minus_ft
    if mou > 1:
        return "pass", margin, mou
    if mou < -1:
        return "fail", margin, mou
    return "unsure", margin, mou


def site_decision(
    placement: Outcome,
    decision_facts: dict[str, Outcome | None],
    feature_facts: dict[str, dict],
    views_needed: list,
    profile: dict,
    cfg: CriteriaConfig,
) -> tuple[Decision, list[str]]:
    """Site lattice from the brief. `placement` is the placement criterion outcome."""
    reasons: list[str] = []
    allow_reject = bool(profile.get("policy", {}).get("allow_reject"))
    # not_same_wall never fails the site. not_evaluated rows are omitted by the caller,
    # which leaves the required ids missing. Under review, missing is absent.
    live = {fid: outcome for fid, outcome in decision_facts.items() if fid != "not_same_wall"}
    if cfg.absent_facts == "review":
        for fid in sorted(DECISION):
            live.setdefault(fid, None)
    fact_fail = [fid for fid, outcome in live.items() if outcome == "fail"]
    fact_unsure = [fid for fid, outcome in live.items() if outcome == "unsure"]
    fact_absent = [fid for fid, outcome in live.items() if outcome is None and cfg.absent_facts == "review"]
    if placement == "fail":
        reasons.append("all_spots_fail")
    for fid in fact_fail:
        reasons.append("all_spots_fail" if fid == "placement" else f"fact_unsure:{fid}")
    for fid in fact_unsure:
        reasons.append(f"fact_unsure:{fid}")
    for fid in fact_absent:
        reasons.append(f"fact_absent:{fid}")
    meter = feature_facts.get("meter_number")
    meter_missing = meter is None
    unclean = bool(cfg.meter_number_required and meter is not None and not meter.get("clean", True))
    if cfg.meter_number_required and meter_missing and cfg.absent_facts == "review":
        unclean = True
    if unclean:
        reasons.append("fact_unsure:meter_number")
    if views_needed:
        reasons.append("views_needed")

    if (placement == "fail" and allow_reject) or fact_fail:
        return "reject", list(dict.fromkeys(reasons)) or ["all_spots_fail"]
    if placement != "pass" or fact_unsure or fact_absent or unclean or views_needed:
        return "manual_review", list(dict.fromkeys(reasons)) or ["views_needed"]
    return "pass", []
