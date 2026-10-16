"""Scale block written to scale/scale.json and mirrored under provenance.producer.params.scale.

The base fields are WS-00's. The optional fields below are the WS-05 contracts extension:
origin, reason codes, the ARKit gate, evidence and the close-up request. A document that
only carries the base fields still validates.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from server.contracts._base import JsonInt, JsonNum, Strict, Vec3
from server.contracts.verdict import ViewNeeded

ScaleSource = Literal["meter_model", "lidar", "arkit", "none"]
ScaleStatus = Literal["verified", "single_reference", "unverified"]
ScaleKind = Literal[
    "meter_model",
    "meter_generic",
    "arkit_poses",
    "lidar",
    "door_sku",
    "garage_door_sku",
    "siding",
    "brick",
    "footprint",
    "truedepth",
]
ScaleConfidence = Literal["tight", "standard", "near_only", "weak", "none"]
ScaleReason = Literal["scale_unresolved", "scale_conflict"]
ScaleMethod = Literal[
    "two_view",
    "one_view_rim_offset",
    "walk_frame",
    "lidar_ratio",
    "umeyama",
    "truedepth",
]
ReasonDetail = Literal[
    "lidar_no_depth_files",
    "lidar_too_few_pairs",
    "meter_closeup_missing",
    "meter_closeup_unregistered",
    "meter_model_unread",
    "meter_identity_low_confidence",
    "meter_model_not_in_table",
    "meter_dimension_assumed",
    "meter_contour_ambiguous",
    "scale_reference_edge_not_found",
    "scale_reference_views_disagree",
    "scale_plane_fit_failed",
    "scale_conflict_objects",
    "arkit_gate_fail",
    "no_scale_source",
]
MemberAction = Literal["retake_meter_closeups", "truedepth_meter_scan"]


class ScaleEstimate(Strict):
    kind: str
    contour: Literal["cover", "ring", "base"] | None = None
    rel_sd: JsonNum = Field(ge=0)
    used: bool
    views: JsonInt | None = Field(default=None, ge=0)
    residual_pct: JsonNum | None = None
    role: Literal["primary", "check"] | None = None
    sku: str | None = None
    source: str | None = None
    method: ScaleMethod | None = None
    view_ids: list[str] | None = None
    pairs: JsonInt | None = Field(default=None, ge=0)
    reason: str | None = None


class ArkitGateRecord(Strict):
    available: bool
    passed: bool
    profile: str | None = None
    band_pct: JsonNum | None = None
    residual_pct: JsonNum | None = None
    rel_sd: JsonNum | None = None
    s_arkit_per_source: JsonNum | None = None


class ScaleEvidence(Strict):
    images: list[str] = Field(default_factory=list)
    boxes_px: dict[str, list[JsonNum]] = Field(default_factory=dict)


class ScaleBlock(Strict):
    source: ScaleSource
    model: str | None = None
    scale_factor: JsonNum | None = Field(default=None, gt=0)
    rel_bound_95: JsonNum = Field(ge=0)
    status: ScaleStatus
    estimates: list[ScaleEstimate]
    conflict: bool
    origin_m: Vec3 | None = None
    source_frame: str | None = None
    rel_sd: JsonNum | None = None
    confidence: ScaleConfidence | None = None
    reason: ScaleReason | None = None
    reason_detail: ReasonDetail | None = None
    table_entry: str | None = None
    table_version: str | None = None
    arkit_gate: ArkitGateRecord | None = None
    evidence: ScaleEvidence | None = None
    views_needed: list[ViewNeeded] = Field(default_factory=list)
    member_actions: list[MemberAction] = Field(default_factory=list)
    timings_s: dict[str, JsonNum] | None = None
