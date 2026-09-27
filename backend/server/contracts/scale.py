"""Scale block written to scale/scale.json and mirrored under provenance.producer.params.scale."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from server.contracts._base import JsonInt, JsonNum, Strict

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


class ScaleBlock(Strict):
    source: ScaleSource
    model: str | None = None
    scale_factor: JsonNum | None = Field(default=None, gt=0)
    rel_bound_95: JsonNum = Field(ge=0)
    status: ScaleStatus
    estimates: list[ScaleEstimate]
    conflict: bool
