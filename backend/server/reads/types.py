"""Pydantic models for meter and panel reads.

`ConfirmBlock` uses the camelCase names in WS-00 `HintData.confirm`.
Everything else is snake_case, matching internal artifacts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReadsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ConfirmationConflict(ReadsError):
    def __init__(self) -> None:
        super().__init__("confirmation_conflict")


class StillRef(BaseModel):
    capture_id: str
    still_id: str
    path: str
    sha256: str
    purpose: str
    w: int = 0
    h: int = 0
    keyframe: str | None = None
    view_angle_deg: float | None = None


class ReaderAnswer(BaseModel):
    model: str
    prompt_id: str
    answer: dict | None = None
    raw_text: str = ""
    secs: float = 0.0
    usage: dict = Field(default_factory=dict)
    cost_usd: float = 0.0
    cached: bool = False
    error: str | None = None


class Candidate(BaseModel):
    value: str
    core: str
    sources: list[str]
    rank: int
    flags: list[str] = Field(default_factory=list)


MeterStatus = Literal["auto_fill", "prefill", "choose", "unreadable", "single_reader", "disabled"]
Cl320 = Literal["cl320_meter", "not_cl320_meter", "cl320_unclear"]
Hazard = Literal["confirmed", "disputed", "none", "unknown"]


class MeterRead(BaseModel):
    status: MeterStatus
    value: str | None = None
    core: str | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    confidence: float = 0.0
    agree: bool = False
    barcode_match: bool = False
    class_label: str | None = None
    class_number: str | None = None
    cl320: Cl320 = "cl320_unclear"
    form: str | None = None
    manufacturer: str | None = None
    legibility: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)


class PanelRead(BaseModel):
    equipment: str | None = None
    brand: str | None = None
    brand_evidence: str | None = None
    product_line: str | None = None
    main_breaker_amps: int | None = None
    panel_rating_amps: int | None = None
    hazard: Hazard = "unknown"
    hazard_line: str | None = None
    review_hints: list[str] = Field(default_factory=list)
    rust: str | None = None
    damage_or_scorching: bool | None = None
    enclosures_visible: int | None = None
    mains_visible: int | None = None
    text_seen: list[str] = Field(default_factory=list)
    legibility: str | None = None
    hazard_evidence: str | None = None
    # Measured pair (SD-50): mini then GPT-5.5. The panel pass rule reads these; Grok is not a panel reader.
    hazard_line_mini: str | None = None
    hazard_line_gpt: str | None = None
    brand_mini: str | None = None
    brand_gpt: str | None = None
    evidence_mini: str | None = None
    evidence_gpt: str | None = None
    gpt_read: bool = False


class StillResult(BaseModel):
    still: StillRef
    answers: list[ReaderAnswer] = Field(default_factory=list)
    barcodes: list[dict] = Field(default_factory=list)
    meter: MeterRead | None = None
    panel: PanelRead | None = None
    cost_usd: float = 0.0
    secs: float = 0.0


class ReadsStatus(BaseModel):
    meter: Literal["ok", "pending", "unreadable", "absent"]
    panel: Literal["ok", "pending", "unreadable", "absent"]


class MeterIdentity(BaseModel):
    manufacturer: str | None = None
    model_family: str | None = None
    model_evidence: Literal["printed", "visual"] | None = None
    model_confidence: float = 0.0
    cover_material: str | None = None
    ansi_socket_meter: bool | None = None
    base_flange_visible: bool | None = None
    socket_type: str | None = None
    agree: bool = False
    images: list[str] = Field(default_factory=list)


class ConfirmBlock(BaseModel):
    """Same names as `HintData.confirm`. Reply state (`answered_by`, `answer`, `locks`) stays on the artifact."""

    imageId: str = ""
    fieldId: Literal["meter_number", "panel_brand", "main_breaker_amps"] = "meter_number"
    mode: Literal["pick", "type", "none"] = "pick"
    candidates: list[str] = Field(default_factory=list, max_length=3)
    preselected: str | None = None
    allowNone: bool = True
    answered_by: str | None = None
    answer: str | None = None
    # Per-field locks so a second device answer 409s. Not a customer-facing field.
    locks: dict[str, str] = Field(default_factory=dict)


class PanelBundle(BaseModel):
    merged: PanelRead | None = None
    per_image: dict[str, PanelRead] = Field(default_factory=dict)


class PanelCount(BaseModel):
    enclosures_max: int | None = None
    mains_max: int | None = None
    agree: bool = False


class Reads(BaseModel):
    format: Literal["base-reads/v0"] = "base-reads/v0"
    capture_id: str
    status: Literal["complete", "partial", "disabled"] = "partial"
    facts: list[dict] = Field(default_factory=list)
    views_needed: list[dict] = Field(default_factory=list)
    meter: MeterRead | None = None
    meter_identity: MeterIdentity | None = None
    panel: PanelBundle | None = None
    panel_count: PanelCount | None = None
    confirm: ConfirmBlock | None = None
    cost_usd: float = 0.0
    timings_s: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
