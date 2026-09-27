"""Status, confidence and the close-up request. Size references never verify a placement."""

from __future__ import annotations

from dataclasses import dataclass

from server.contracts.verdict import ViewNeeded
from server.scale.params import ScaleParams

CLOSEUP_TARGET = (
    "meter close-up: whole cover and nameplate, head-on from about 3 ft, "
    "then once more from about 30 degrees to the side"
)
REASON_DETAILS = (
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
)
_RETAKE = {
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
}


@dataclass
class GateView:
    available: bool
    passed: bool


@dataclass
class Decision:
    status: str
    confidence: str
    reason: str | None
    reason_detail: str | None
    member_actions: list[str]
    views_needed: list[dict]


def confidence_of(rel_bound_95: float) -> str:
    if rel_bound_95 <= 0.015:
        return "tight"
    if rel_bound_95 <= 0.02:
        return "standard"
    if rel_bound_95 <= 0.06:
        return "near_only"
    if rel_bound_95 <= 0.12:
        return "weak"
    return "none"


def _request(detail: str) -> list[dict]:
    item = ViewNeeded(
        id="vn-scale-1",
        kind="close_up",
        target=CLOSEUP_TARGET,
        criteria=["scale"],
        why=detail,
    )
    return [item.model_dump(exclude_none=True)]


def decide(
    *,
    source: str,
    rel_bound_95: float,
    reason_detail: str | None,
    gate: GateView | None,
    conflict_flag: bool,
    params: ScaleParams,
    meter_method: str | None,
    meter_prov: str | None,
    lone: bool,
    agree: bool,
    identity_source: str | None,
    pairs: int,
    frames: int,
    metric: bool,
) -> Decision:
    """Map a finished measurement to status.

    ``verified`` requires the ARKit gate to be available and passed. The behaviour
    note that treats an unavailable gate as enough for LiDAR is not used: the
    acceptance check forbids ``verified`` whenever ``arkit_gate.available`` is false.
    """

    gate_available = gate is not None and gate.available
    gate_passed = gate_available and bool(gate.passed)
    gate_failed = bool(metric and gate_available and not gate.passed)
    detail = "scale_conflict_objects" if conflict_flag else reason_detail
    if gate_failed and detail is None:
        detail = "arkit_gate_fail"

    clean = detail is None and not conflict_flag
    lidar_verified = (
        clean
        and source == "lidar"
        and pairs >= params.lidar_verified_pairs
        and frames >= 5
        and rel_bound_95 <= 0.02
        and gate_passed
    )
    meter_verified = (
        clean
        and source == "meter_model"
        and params.meter_verified_enabled
        and meter_prov in {"datasheet", "caliper"}
        and meter_method in {"two_view", "one_view_rim_offset"}
        and agree
        and identity_source == "reads"
        and not lone
        and gate_passed
    )
    if lidar_verified or meter_verified:
        status = "verified"
    elif metric and detail is None and meter_method != "walk_frame" and source not in {"none", "arkit"}:
        status = "single_reference"
    else:
        status = "unverified"

    if conflict_flag:
        reason: str | None = "scale_conflict"
    elif detail is None or detail == "no_scale_source":
        reason = None
    else:
        reason = "scale_unresolved"

    actions: list[str] = []
    views: list[dict] = []
    if detail in _RETAKE:
        views = _request(detail)
        actions = ["retake_meter_closeups"]
    elif (
        metric
        and source == "meter_model"
        and gate_passed
        and rel_bound_95 > 0.02
        and not conflict_flag
        and detail is None
    ):
        actions = ["truedepth_meter_scan"]

    return Decision(status, confidence_of(rel_bound_95), reason, detail, actions, views)
