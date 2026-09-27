"""Ingest API v1 bodies. Paths are under `/v1` except where a route says otherwise."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from server.contracts._base import IsoTime, JsonInt, JsonNum, RelPath, Sha256Hex, StorageId, Strict
from server.contracts.capture_check import CaptureCheckCode, HintCode
from server.contracts.jobs import CaptureStatus, EventType, Flow, StageName, StageStatus
from server.contracts.packet import FileRole, Keyframe, Still, Tier, TrueDepthFrame

Profile = Literal["A", "B", "C", "S"]
ConfirmField = Literal["meter_number", "panel_brand", "main_breaker_amps"]
ConfirmKind = Literal["confirmed", "corrected", "none"]
_VALUE = r"^[A-Za-z0-9 ./-]{1,32}$"


class ApiErrorItem(Strict):
    code: str
    pointer: str
    message: str


class ApiError(Strict):
    errors: list[ApiErrorItem]


class DeviceInfo(Strict):
    model: str
    systemVersion: str
    appVersion: str


class ExpectedCounts(Strict):
    keyframes: JsonInt | None = Field(default=None, ge=0)
    stills: JsonInt | None = Field(default=None, ge=0)


class FixtureMeter(Strict):
    """Replay pixels. Stored and applied only when ``SMP_ENVIRONMENT`` is ``dev``."""

    meterFrames: str = Field(
        min_length=1,
        max_length=8000,
        description="id:u,v pairs, the same text as models['meter_frames']. Ignored unless SMP_ENVIRONMENT is dev.",
    )
    anchorLabel: str | None = Field(
        default=None,
        max_length=64,
        description="Tap label when the packet has no meter tap, such as reference_point. Ignored in prod.",
    )


class CreateCaptureRequest(Strict):
    packetId: StorageId
    formatVersion: str
    tier: Tier
    flow: Flow
    device: DeviceInfo
    orderRef: str | None = None
    expected: ExpectedCounts | None = None
    fixtureMeter: FixtureMeter | None = None


class UploadLimits(Strict):
    maxSinglePutBytes: int = 16_777_216
    partBytes: int = 8_388_608
    urlTtlS: int = 3600
    maxFiles: int = 2000
    maxBatch: int = 50


class CreateCaptureResponse(Strict):
    captureId: str
    status: CaptureStatus
    upload: UploadLimits
    eventsUrl: str
    finalizeBy: IsoTime


class RegisterFileMeta(Strict):
    """Exactly one of `keyframe`, `still`, or `trueDepthFrame`."""

    keyframe: Keyframe | None = None
    still: Still | None = None
    trueDepthFrame: TrueDepthFrame | None = None

    @model_validator(mode="after")
    def exactly_one_record(self) -> RegisterFileMeta:
        present = (self.keyframe is not None) + (self.still is not None) + (self.trueDepthFrame is not None)
        if present != 1:
            raise ValueError("meta must set exactly one of keyframe, still, trueDepthFrame")
        return self


class RegisterFile(Strict):
    path: RelPath
    bytes: JsonInt = Field(ge=0)
    sha256: Sha256Hex
    md5: str
    role: FileRole
    contentType: str
    meta: RegisterFileMeta | None = None


class RegisterFilesRequest(Strict):
    files: list[RegisterFile] = Field(min_length=1, max_length=50)


class UploadPart(Strict):
    n: JsonInt = Field(ge=1)
    url: str


class UploadTarget(Strict):
    method: Literal["PUT", "MULTIPART"]
    url: str | None = None
    headers: dict[str, str] | None = None
    expiresAt: IsoTime | None = None
    uploadId: str | None = None
    parts: list[UploadPart] | None = None
    completeUrl: str | None = None


class RegisteredFile(Strict):
    path: str
    state: Literal["pending", "committed"]
    upload: UploadTarget | None = None


class RegisterFilesResponse(Strict):
    files: list[RegisteredFile]


class CommitFile(Strict):
    path: RelPath
    sha256: Sha256Hex


class CommitRequest(Strict):
    files: list[CommitFile] = Field(min_length=1, max_length=50)


class CommitResponse(Strict):
    committed: list[str]
    notFound: list[str]
    mismatch: list[str]


class MultipartPart(Strict):
    n: JsonInt
    etag: str


class CompleteMultipartRequest(Strict):
    path: RelPath
    uploadId: str
    parts: list[MultipartPart]


class CompleteMultipartResponse(Strict):
    path: str
    state: str


class FinalizeResponse(Strict):
    status: Literal["awaiting_files", "processing"]
    missing: list[str]
    runId: str
    etaS: JsonInt = Field(ge=0)


class FilesCommittedData(Strict):
    paths: list[str]
    committed: JsonInt
    listed: JsonInt | None = None


class ConfirmPrompt(Strict):
    imageId: str
    fieldId: ConfirmField
    mode: Literal["pick", "type", "none"]
    candidates: list[str] = Field(max_length=3)
    preselected: str | None = None
    allowNone: bool


class HintData(Strict):
    code: HintCode
    message: str
    retake: str | None = None
    promptId: str | None = None
    frames: list[str] | None = None
    confirm: ConfirmPrompt | None = None

    @model_validator(mode="after")
    def confirm_only_for_meter_number(self) -> HintData:
        if self.confirm is not None and self.code != "meter_number_confirm":
            raise ValueError("confirm is only valid when code is meter_number_confirm")
        return self


class CaptureCheckData(Strict):
    complete: bool
    missing: list[CaptureCheckCode]
    meterRead: Literal["ok", "unreadable", "pending", "absent"] | None = None
    prompts: list[str] | None = None


class StageData(Strict):
    stage: StageName
    status: StageStatus
    attempt: JsonInt
    runId: str
    gpu: str | None = None
    durationS: JsonNum | None = None
    errorCode: str | None = None


class VerdictReadyData(Strict):
    runId: str
    kind: Literal["eligible", "needs_more_photos", "not_eligible", "manual_review"]


class RetakeRequestData(Strict):
    runId: str
    viewsNeeded: list[str]
    memberActions: list[str]


class FailedData(Strict):
    code: str
    message: str
    next: Literal["manual_review", "none", "retry_finalize"]


class ConfirmationData(Strict):
    confirmationId: str
    fieldId: ConfirmField
    imageId: str
    kind: ConfirmKind
    by: Literal["device", "reviewer"]


class Event(Strict):
    seq: JsonInt = Field(ge=1)
    type: EventType
    at: IsoTime
    data: dict[str, Any]


class EventsResponse(Strict):
    status: CaptureStatus
    next: JsonInt
    events: list[Event]


class ConfirmationRequest(Strict):
    fieldId: ConfirmField
    kind: ConfirmKind
    value: str | None = Field(default=None, pattern=_VALUE)
    imageId: str
    clientAt: IsoTime

    @model_validator(mode="after")
    def value_unless_none(self) -> ConfirmationRequest:
        if self.kind != "none" and not self.value:
            raise ValueError("value_required")
        return self


class ConfirmationResponse(Strict):
    confirmationId: str
    fieldId: ConfirmField
    imageId: str
    kind: ConfirmKind
    by: Literal["device", "reviewer"]
    receivedAt: IsoTime
    seq: JsonInt


class HealthResponse(Strict):
    status: Literal["ok", "degraded"]
    storage: str
    state: str
    wifTokenExpiresAt: IsoTime | None = None
    version: str


class CaptureStatusResponse(Strict):
    captureId: str
    packetId: StorageId
    status: CaptureStatus
    tier: Tier
    flow: Flow
    createdAt: IsoTime
    finalizeBy: IsoTime
    filesRegistered: JsonInt
    filesCommitted: JsonInt
    runId: str | None = None


class AmendmentRequest(Strict):
    amendmentId: StorageId
    answers: list[str]


class AmendmentResponse(Strict):
    amendmentId: StorageId
    pathPrefix: str


class DeviceRegisterRequest(Strict):
    token: str
    platform: Literal["ios"]
    bundleId: str | None = None


ApiModels = Annotated[
    CreateCaptureRequest
    | CreateCaptureResponse
    | RegisterFilesRequest
    | RegisterFilesResponse
    | CommitRequest
    | CommitResponse
    | CompleteMultipartRequest
    | FinalizeResponse
    | ApiError
    | EventsResponse
    | ConfirmationRequest
    | ConfirmationResponse
    | HealthResponse
    | CaptureStatusResponse
    | AmendmentRequest
    | AmendmentResponse
    | DeviceRegisterRequest,
    Field(title="ApiV1"),
]
