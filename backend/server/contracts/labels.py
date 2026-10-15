"""Labels v0. `start_range` rows must name a wall and a start span (semantic check)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from server.contracts._base import JsonNum, SpanLoose, Strict

DataKind = Literal["real", "synthetic", "hybrid"]
SiteDecision = Literal["pass", "manual_review", "reject", "unknown"]
LabelTruth = Literal["pass", "fail", "borderline", "not_pass", "unknown"]


class Label(Strict):
    id: str
    criterion: str
    scope: Literal["site", "start_range"]
    truth: LabelTruth
    why: str
    wall_id: str | None = None
    start_s_m: SpanLoose | None = None
    value_ft: JsonNum | None = None
    plus_minus_ft: JsonNum | None = Field(default=None, ge=0)


class Labels(Strict):
    format: Literal["base-labels/v0"]
    site: str
    data: DataKind
    labeller: str
    method: str
    rules_profile: str | None = None
    site_decision: SiteDecision | None = None
    labels: list[Label]


def labels_consistency_errors(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for index, label in enumerate(doc.get("labels") or []):
        if label.get("scope") == "start_range" and (not label.get("wall_id") or "start_s_m" not in label):
            errors.append(f"labels[{index}]: start_range requires wall_id and start_s_m")
    return errors
