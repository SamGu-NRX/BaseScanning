"""Capture packet v0.4. Field names are the wire names. Schema: capture-packet.v0.4."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from server.contracts._base import (
    Box4,
    Intrinsics,
    IsoTime,
    JsonInt,
    JsonNum,
    Loose,
    Mat4,
    Point2,
    RelPath,
    SegmentId,
    Sha256Hex,
    StorageId,
    Strict,
    Vec3,
    keep_null,
)

Tier = Literal["arkit_lidar", "arkit", "photo_only"]
TrackingState = Literal[
    "normal",
    "notAvailable",
    "limited.initializing",
    "limited.excessiveMotion",
    "limited.insufficientFeatures",
    "limited.relocalizing",
]
FileRole = Literal[
    "keyframe",
    "depth",
    "confidence",
    "features",
    "still",
    "stream",
    "truedepth",
    "mesh",
    "compat",
    "other",
]
_ARKIT = ("arkit", "arkit_lidar")


class Units(Strict):
    length: Literal["meters"]
    angle: Literal["degrees"]
    time: str
    image: str
    rotationRate: Literal["radians per second"] | None = None
    acceleration: Literal["g (9.80665 m/s^2)"] | None = None
    magneticField: Literal["microtesla"] | None = None


class Conventions(Loose):
    world: str
    camera: str
    pose: str
    intrinsics: str
    pixel: str

    @model_validator(mode="before")
    @classmethod
    def values_are_strings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(value, str):
                    raise ValueError(f"conventions.{key} must be a string")
        return data


class Source(Strict):
    kind: Literal["device", "replay", "synthetic"]
    appBuild: str | None = None
    generator: str | None = None
    variant: str | None = None
    noiseProfile: str | None = None
    sourceDataset: str | None = None
    synthesizedFields: list[str] | None = None
    notes: str | None = None


class VideoFormat(Strict):
    imageResolution: list[JsonInt] = Field(min_length=2, max_length=2)
    framesPerSecond: JsonNum | None = Field(default=None, gt=0)
    captureDeviceType: str | None = None

    @model_validator(mode="after")
    def resolution_positive(self) -> VideoFormat:
        if any(int(item) < 1 for item in self.imageResolution):
            raise ValueError("imageResolution entries must be >= 1")
        return self


class Session(Strict):
    id: str = Field(min_length=1)
    startedAt: IsoTime
    startedAtUptime: JsonNum
    endedAtUptime: JsonNum | None = None
    timeZone: str | None = None
    appVersion: str
    deviceModel: str
    systemVersion: str
    tier: Tier
    lidarAvailable: bool
    meshReconstructionSupported: bool | None = None
    sceneDepthEnabled: bool
    sceneReconstruction: Literal["none", "mesh", "meshWithClassification"] | None = None
    worldAlignment: Literal["gravity", "gravityAndHeading", "camera"] | None = None
    planeDetection: list[Literal["horizontal", "vertical"]] | None = None
    videoFormat: VideoFormat | None = None
    locationAuthorization: Literal["fullAccuracy", "reducedAccuracy", "denied", "notDetermined"] | None = None
    motionReferenceFrame: (
        Literal[
            "xArbitraryZVertical",
            "xArbitraryCorrectedZVertical",
            "xMagneticNorthZVertical",
            "xTrueNorthZVertical",
        ]
        | None
    ) = None
    thermalStateMax: Literal["nominal", "fair", "serious", "critical"] | None = None
    gates: dict[str, JsonNum] | None = None

    @field_validator("planeDetection")
    @classmethod
    def unique_planes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("planeDetection items must be unique")
        return value

    @field_validator("gates")
    @classmethod
    def gates_are_numbers(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        for key, item in value.items():
            if isinstance(item, bool) or not isinstance(item, int | float):
                raise ValueError(f"gates.{key} must be a number")
        return value


class Epoch(Strict):
    id: str = Field(min_length=1)
    startTime: JsonNum
    reason: Literal["sessionStart", "trackingReset", "relocalizationFailed", "userRestart"]


class Exposure(Strict):
    duration: JsonNum | None = Field(default=None, gt=0)
    offset: JsonNum | None = None
    iso: JsonNum | None = Field(default=None, gt=0)
    fNumber: JsonNum | None = Field(default=None, gt=0)


class Exif(Loose):
    Make: str | None = None
    Model: str | None = None
    LensModel: str | None = None
    DateTimeOriginal: str | None = None
    SubsecTimeOriginal: str | None = None
    OffsetTimeOriginal: str | None = None
    ExposureTime: JsonNum | None = Field(default=None, gt=0)
    FNumber: JsonNum | None = Field(default=None, gt=0)
    FocalLength: JsonNum | None = Field(default=None, gt=0)
    FocalLenIn35mmFilm: JsonNum | None = Field(default=None, gt=0)
    ISOSpeedRatings: list[JsonNum] | None = None
    BrightnessValue: JsonNum | None = None
    ExposureBiasValue: JsonNum | None = None
    Orientation: JsonInt | None = Field(default=None, ge=1, le=8)
    PixelXDimension: JsonInt | None = None
    PixelYDimension: JsonInt | None = None


class LightEstimate(Strict):
    ambientIntensity: JsonNum | None = None
    ambientColorTemperature: JsonNum | None = None


class DepthRef(Strict):
    file: RelPath
    confidenceFile: RelPath | None = keep_null()
    w: JsonInt = Field(ge=1)
    h: JsonInt = Field(ge=1)
    smoothed: bool | None = None


class FeaturesRef(Strict):
    file: RelPath
    count: JsonInt = Field(ge=0)


class AdmissionSupport(Strict):
    requirement: str
    revision: JsonInt = Field(ge=1)


class Admission(Strict):
    requestId: str | None = None
    usable: bool | None = None
    supports: list[AdmissionSupport] | None = None


class Keyframe(Strict):
    id: SegmentId
    img: RelPath
    w: JsonInt = Field(ge=1)
    h: JsonInt = Field(ge=1)
    intrinsics: Intrinsics
    pose: Mat4
    timestamp: JsonNum
    tracking: TrackingState
    epoch: str
    reason: Literal["motion", "tap", "freeze", "still", "request", "auto"]
    highResolution: bool | None = None
    exposure: Exposure | None = None
    exif: Exif | None = None
    lightEstimate: LightEstimate | None = None
    cameraGrainIntensity: JsonNum | None = Field(default=None, ge=0, le=1)
    worldMappingStatus: Literal["notAvailable", "limited", "extending", "mapped"] | None = None
    depth: DepthRef | None = keep_null()
    features: FeaturesRef | None = keep_null()
    purpose: str | None = None
    admission: Admission | None = None
    ext: dict[str, Any] | None = None


class StillLocation(Strict):
    latitude: JsonNum = Field(ge=-90, le=90)
    longitude: JsonNum = Field(ge=-180, le=180)
    horizontalAccuracy: JsonNum | None = None


class Still(Strict):
    id: SegmentId
    purpose: str
    img: RelPath
    w: JsonInt = Field(ge=1)
    h: JsonInt = Field(ge=1)
    timestamp: JsonNum
    orientation: JsonInt = Field(ge=1, le=8)
    keyframe: str | None = None
    intrinsics: Intrinsics | None = None
    intrinsicsSource: Literal["arkit", "calibration", "exif_focal35"] | None = None
    gravity: Vec3 | None = None
    location: StillLocation | None = None
    exif: Exif | None = None
    ext: dict[str, Any] | None = None


class TapHit(Strict):
    position: Vec3
    target: Literal["existingPlaneGeometry", "existingPlaneInfinite", "estimatedPlane", "sceneDepth", "mesh", "none"]
    alignment: Literal["horizontal", "vertical", "any"] | None = None
    planeId: str | None = None
    distance: JsonNum | None = Field(default=None, ge=0)


class Tap(Strict):
    id: str = Field(min_length=1)
    time: JsonNum
    label: str
    keyframe: str
    pixel: Point2
    rayOrigin: Vec3
    rayDirection: Vec3
    hit: TapHit | None = None
    tool: str | None = None
    step: str | None = None
    frozen: bool | None = None
    displayMappingCheck: JsonNum | None = keep_null()
    confirmedByUser: bool | None = None
    refusal: str | None = keep_null()
    epoch: str | None = None
    ext: dict[str, Any] | None = None


class TrackingEvent(Strict):
    time: JsonNum
    state: TrackingState
    epoch: str | None = None


class PlaneExtent(Strict):
    width: JsonNum = Field(ge=0)
    height: JsonNum = Field(ge=0)
    rotationOnYAxis: JsonNum | None = None


class Plane(Strict):
    id: str = Field(min_length=1)
    epoch: str
    alignment: Literal["horizontal", "vertical"]
    classification: (
        Literal[
            "none",
            "wall",
            "floor",
            "ceiling",
            "table",
            "seat",
            "window",
            "door",
            "notAvailable",
            "undetermined",
            "unknown",
        ]
        | None
    ) = None
    transform: Mat4
    center: Vec3 | None = None
    planeExtent: PlaneExtent
    boundary: list[Point2] | None = None
    firstSeen: JsonNum | None = None
    lastUpdated: JsonNum | None = None
    sourceInliers: JsonInt | None = None


class Mesh(Strict):
    file: RelPath
    epoch: str
    faceCount: JsonInt | None = Field(default=None, ge=0)
    classified: bool | None = None


class CsvStream(Strict):
    file: RelPath
    columns: list[str]
    rateHz: JsonNum | None = None


class MotionStream(Strict):
    file: RelPath
    columns: list[str]
    rateHz: JsonNum = Field(gt=0)
    referenceFrame: Literal[
        "xArbitraryZVertical",
        "xArbitraryCorrectedZVertical",
        "xMagneticNorthZVertical",
        "xTrueNorthZVertical",
    ]


class HeadingStream(Strict):
    file: RelPath
    columns: list[str]
    headingOrientation: (
        Literal[
            "portrait",
            "portraitUpsideDown",
            "landscapeLeft",
            "landscapeRight",
            "faceUp",
            "faceDown",
            "unknown",
        ]
        | None
    ) = None
    headingFilter: JsonNum | None = None


class LocationStream(Strict):
    file: RelPath
    columns: list[str]
    desiredAccuracy: (
        Literal[
            "bestForNavigation",
            "best",
            "nearestTenMeters",
            "hundredMeters",
            "kilometer",
            "threeKilometers",
            "reduced",
        ]
        | None
    ) = None


class ImuRawStream(Strict):
    file: RelPath
    columns: list[str]
    rateHz: JsonNum = Field(ge=50)


class ArkitPoseStream(Strict):
    file: RelPath
    columns: list[str]
    rateHz: JsonNum = Field(ge=10)


class Streams(Strict):
    motion: MotionStream | None = None
    heading: HeadingStream | None = None
    location: LocationStream | None = None
    altimeter: CsvStream | None = None
    imuRaw: ImuRawStream | None = None
    arkitPoses: ArkitPoseStream | None = None


class CloseUp(Strict):
    captured: bool
    still: str | None = keep_null()
    arkitDistanceM: JsonNum | None = keep_null(gt=0)
    viewAngleDeg: JsonNum | None = keep_null(ge=0, le=90)
    nameplateVisible: bool | None = keep_null()

    @model_validator(mode="after")
    def still_required_when_captured(self) -> CloseUp:
        if self.captured and not isinstance(self.still, str):
            raise ValueError("still is required when captured is true")
        return self


class ObliqueCloseUp(CloseUp):
    side: Literal["left", "right"] | None = keep_null()


class MeterIdentity(Strict):
    make: str | None = keep_null()
    model: str | None = keep_null()
    rawText: str | None = keep_null()
    source: Literal["on_device_text", "not_read"]


class DoorView(Strict):
    frame: str
    kind: Literal["entry_door", "garage_door", "other_door"]
    box: Box4 | None = None
    viewAngleDeg: JsonNum | None = keep_null(ge=0, le=90)


class ServiceAddress(Strict):
    line1: str
    city: str
    state: str
    postalCode: str
    line2: str | None = keep_null()
    country: str = "US"
    source: Literal["account", "order"]


class TrueDepthFrame(Strict):
    depth: RelPath
    image: RelPath | None = None
    w: JsonInt = Field(ge=1)
    h: JsonInt = Field(ge=1)
    intrinsics: Intrinsics
    timestamp: JsonNum


class TrueDepthScan(Strict):
    captured: bool
    prompt: Literal["shade", "scale_unverified", "always"] | None = None
    failure: Literal["too_bright", "no_valid_depth", "declined", "unsupported_device"] | None = keep_null()
    depthDataAccuracy: Literal["absolute", "relative"] | None = None
    depthDataQuality: Literal["high", "low"] | None = None
    frames: list[TrueDepthFrame] | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def captured_needs_frames(self) -> TrueDepthScan:
        if self.captured and (not self.frames or self.depthDataAccuracy is None):
            raise ValueError("captured TrueDepth scans require frames and depthDataAccuracy")
        return self


class ScaleExt(Loose):
    method: str | None = None
    obliqueCloseUp: ObliqueCloseUp | None = None
    doorViews: list[DoorView] | None = Field(default=None, max_length=10)
    doorOnMeterSide: bool | None = keep_null()
    serviceAddress: ServiceAddress | None = None
    trueDepthScan: TrueDepthScan | None = None


class ScaleReference(Strict):
    meterCloseUp: CloseUp | None = None
    meterIdentity: MeterIdentity | None = None
    ext: ScaleExt | None = None


class FileEntry(Strict):
    path: RelPath
    bytes: JsonInt = Field(ge=0)
    sha256: Sha256Hex
    role: FileRole


class Packet(Strict):
    format: Literal["house-capture-packet"]
    formatVersion: str = Field(pattern=r"^0\.[0-9]+$")
    packetId: StorageId
    parentPacketId: StorageId | None = None
    createdAt: IsoTime
    units: Units
    conventions: Conventions
    source: Source
    session: Session
    epochs: list[Epoch] | None = None
    keyframes: list[Keyframe] = Field(max_length=5000)
    stills: list[Still] | None = Field(default=None, max_length=500)
    taps: list[Tap] | None = Field(default=None, max_length=2000)
    tracking: list[TrackingEvent] | None = None
    planes: list[Plane] | None = Field(default=None, max_length=500)
    mesh: Mesh | None = None
    streams: Streams | None = None
    scaleReference: ScaleReference | None = None
    files: list[FileEntry] = Field(min_length=1)
    ext: dict[str, Any] | None = None

    @model_validator(mode="after")
    def tier_rules(self) -> Packet:
        tier = self.session.tier
        if tier == "photo_only":
            if self.keyframes:
                raise ValueError("photo_only packets must have no keyframes")
            if not self.stills:
                raise ValueError("photo_only packets must have at least one still")
            return self
        if tier in _ARKIT:
            if not self.epochs:
                raise ValueError("arkit packets must include epochs")
            if self.tracking is None:
                raise ValueError("arkit packets must include tracking")
            if not self.keyframes:
                raise ValueError("arkit packets must include at least one keyframe")
            if self.session.worldAlignment is None:
                raise ValueError("arkit packets must include session.worldAlignment")
            if self.session.videoFormat is None:
                raise ValueError("arkit packets must include session.videoFormat")
            if self.scaleReference is None or self.scaleReference.meterCloseUp is None:
                raise ValueError("arkit packets must include scaleReference.meterCloseUp")
            ext = self.scaleReference.ext
            if ext is None or ext.method is None or ext.obliqueCloseUp is None or ext.doorViews is None:
                raise ValueError("arkit packets must include scaleReference.ext.doorViews")
            if self.streams is None or self.streams.imuRaw is None or self.streams.arkitPoses is None:
                raise ValueError("arkit packets must include streams.imuRaw and streams.arkitPoses")
        if tier == "arkit_lidar" and self.session.lidarAvailable is not True:
            raise ValueError("arkit_lidar packets must have lidarAvailable true")
        return self
