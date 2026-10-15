"""Criteria knobs. Shared names come from WS-00 Settings; the rest are HSS_CRITERIA_*."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _on(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "on", "true", "yes"}


def _profiles() -> tuple[str, ...]:
    raw = os.environ.get("HSS_CRITERIA_PROFILES", "A,B,C,S")
    return tuple(p.strip() for p in raw.split(",") if p.strip())


@dataclass(frozen=True)
class CriteriaConfig:
    profiles: tuple[str, ...] = ("A", "B", "C", "S")
    default_profile: str = "C"
    ground_rule: str = "angle80"
    step_ft: float = 1 / 6
    hybrid: bool = True
    c2: bool = True
    c2_budget_s: float = 40.0
    c2_profiles: str = "default"
    c3: bool = True
    c3_facts: bool = True
    c3_judge: bool = True
    c3_autopass: bool = False
    judge_may_reject: bool = False
    judge_required: bool = False
    c3_budget_usd: float = 0.60
    external_vision: bool = False
    absent_facts: str = "review"
    meter_number_required: bool = True
    max_views: int = 8
    # Debug only. Set from --allow-missing-occluders. Never from the environment.
    allow_missing_occluders: bool = False
    # Test hook. A judge answer or a decision string. Live chat stays off unless external_vision.
    judge_stub: Any = None

    @staticmethod
    def from_env() -> CriteriaConfig:
        env = os.environ.get("SMP_ENVIRONMENT", "dev")
        absent = os.environ.get("HSS_CRITERIA_ABSENT_FACTS")
        if absent is None:
            absent = "review" if env == "prod" else "ignore"
        return CriteriaConfig(
            profiles=_profiles(),
            default_profile=os.environ.get("RULES_PROFILE_DEFAULT", "C"),
            ground_rule=os.environ.get("GROUND_RULE_DEFAULT", "angle80"),
            step_ft=float(os.environ.get("HSS_CRITERIA_STEP_FT", 1 / 6)),
            hybrid=_on("HSS_CRITERIA_HYBRID", True),
            c2=_on("HSS_CRITERIA_C2", True),
            c2_budget_s=float(os.environ.get("HSS_CRITERIA_C2_BUDGET_S", "40")),
            c2_profiles=os.environ.get("HSS_CRITERIA_C2_PROFILES", "default"),
            c3=_on("HSS_CRITERIA_C3", True),
            c3_facts=_on("HSS_CRITERIA_C3_FACTS", True),
            c3_judge=_on("HSS_CRITERIA_C3_JUDGE", True),
            c3_autopass=_on("HSS_CRITERIA_C3_AUTOPASS", False),
            judge_may_reject=_on("HSS_CRITERIA_JUDGE_MAY_REJECT", False),
            judge_required=_on("HSS_CRITERIA_JUDGE_REQUIRED", False),
            c3_budget_usd=float(os.environ.get("HSS_CRITERIA_C3_BUDGET_USD_PER_HOUSE", "0.60")),
            external_vision=_on("VISION_EXTERNAL_CALLS", False),
            absent_facts=absent,
            meter_number_required=_on("HSS_CRITERIA_METER_NUMBER_REQUIRED", True),
            max_views=int(os.environ.get("HSS_CRITERIA_MAX_VIEWS", "8")),
        )
