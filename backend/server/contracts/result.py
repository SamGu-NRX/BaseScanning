"""Device result. The whole payload is camelCase, including `outcome`. Verdict stays snake_case."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from server.contracts._base import Box4, JsonNum, Mat4, Point3, Span, Strict, Vec3
from server.contracts.jobs import CaptureStatus, Profile
from server.contracts.scale import ScaleSource, ScaleStatus
from server.contracts.verdict import Verdict

MemberAction = Literal["retake_meter_closeups", "truedepth_meter_scan"]
OutcomeKind = Literal["eligible", "needs_more_photos", "not_eligible", "manual_review"]
ViewKind = Literal["band", "past_end", "close_up", "confirm_attribute", "region"]
BandName = Literal["wall", "ground", "overhead", "facing"]


class PromptCopy(Strict):
    title: str
    body: str


class TargetRegionArkit(Strict):
    surface: Literal["wall", "ground", "free"]
    polygon: list[Vec3] = Field(min_length=3)


class ReferenceCrop(Strict):
    image: str
    boxPx: Box4


class ViewNeededDevice(Strict):
    id: str
    kind: ViewKind
    criteria: list[str]
    why: str
    promptId: str
    prompt: PromptCopy
    band: BandName | None = None
    wallId: str | None = None
    sM: Span | None = None
    outM: JsonNum | None = Field(default=None, ge=0)
    side: Literal["left", "right"] | None = None
    target: str | None = None
    viewingAngleDeg: Span | None = None
    targetRegionArkit: TargetRegionArkit | None = None
    suggestedCameraPoseArkit: Mat4 | None = None
    referenceCrop: ReferenceCrop | None = None


class FootprintM(Strict):
    w: JsonNum
    h: JsonNum
    d: JsonNum


class BoxSceneM(Strict):
    centerM: Point3
    sizeM: list[JsonNum] = Field(min_length=3, max_length=3)
    yawDeg: JsonNum


class BoxArkitWorld(Strict):
    pose: Mat4
    size: list[JsonNum] = Field(min_length=3, max_length=3)


class RecommendedPlacement(Strict):
    wallId: str
    startSM: JsonNum
    footprintM: FootprintM
    boxSceneM: BoxSceneM
    confidence: JsonNum = Field(ge=0, le=1)
    profile: Profile
    boxArkitWorld: BoxArkitWorld | None = None


class RejectEvidence(Strict):
    images: list[str]
    boxesPx: list[Box4]


class RejectReason(Strict):
    criterion: str
    text: str
    evidence: RejectEvidence
    measuredFt: JsonNum | None = None
    plusMinusFt: JsonNum | None = None
    thresholdFt: JsonNum | None = None
    comparison: Literal["at_least", "at_most"] | None = None


class Outcome(Strict):
    kind: OutcomeKind
    profile: Profile
    message: str
    viewsNeeded: list[ViewNeededDevice]
    reasons: list[RejectReason]
    arkitEpoch: str | None = None
    frameToArkit: Mat4 | None = None
    recommendedPlacement: RecommendedPlacement | None = None
    cableRoutePolylineArkit: list[Vec3] | None = None

    @model_validator(mode="after")
    def eligible_has_placement(self) -> Outcome:
        if self.kind == "eligible" and self.recommendedPlacement is None:
            raise ValueError("eligible outcome requires recommendedPlacement")
        return self


class CriterionSummary(Strict):
    id: str
    outcome: Literal["pass", "fail", "unsure"]
    unsureCause: str | None = None
    measuredFt: JsonNum | None = None
    plusMinusFt: JsonNum | None = None
    thresholdFt: JsonNum | None = None
    coverage: Literal["observed", "grazing", "partial", "unobserved", "generated"] | None = None


class SiteSummary(Strict):
    decision: Literal["pass", "manual_review", "reject"]
    reasons: list[str]
    profile: Profile


class ScaleSummary(Strict):
    source: ScaleSource
    status: ScaleStatus
    relBound95: JsonNum


class MeterEvidence(Strict):
    image: str
    boxPx: Box4 | None = None


class MeterBlock(Strict):
    status: Literal["auto_filled", "prefill_confirm", "unreadable", "not_captured"]
    value: str | None
    candidates: list[str] = Field(max_length=3)
    barcodeConfirmed: bool
    readersAgree: bool
    clean: bool
    evidence: MeterEvidence | None = None


class ResultResponse(Strict):
    runId: str | None
    status: CaptureStatus
    viewsNeeded: list[ViewNeededDevice]
    memberActions: list[MemberAction]
    outcome: Outcome | None
    site: SiteSummary | None = None
    scale: ScaleSummary | None = None
    criteria: list[CriterionSummary] | None = None
    meter: MeterBlock | None = None
    verdict: Verdict | None = None
    verdictUrl: str | None = None
    previewUrl: str | None = None
    reviewUrl: str | None = None
