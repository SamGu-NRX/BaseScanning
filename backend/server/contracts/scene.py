"""Scene v0. Cross-checks the schema cannot express live in scene_consistency_errors."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from server.contracts._base import (
    Intrinsics,
    JsonInt,
    JsonNum,
    Mat4,
    Point2,
    Point3,
    SpanLoose,
    Strict,
)

DataKind = Literal["real", "synthetic", "hybrid"]
Evidence = Literal["observed", "inferred", "generated"]
ObjectClass = Literal[
    "meter",
    "meter_can",
    "disconnect",
    "panel",
    "wall_box",
    "vent",
    "gas_meter",
    "gas_regulator",
    "gas_pipe",
    "ac_unit",
    "generator",
    "battery",
    "window",
    "door",
    "garage_door",
    "basement_well",
    "downspout",
    "hose_bib",
    "eave",
    "porch_roof",
    "stairs",
    "deck",
    "overhang",
    "pool",
    "hot_tub",
    "gate",
    "bin",
    "plant",
    "shrub",
    "tree",
    "other",
]
Material = Literal[
    "concrete",
    "gravel",
    "lawn",
    "dirt",
    "mulch",
    "pavers",
    "deck",
    "drive",
    "walkway",
    "pool",
    "water",
    "unknown",
]


class SourceView(Strict):
    image: str
    box_px: list[JsonNum] | None = Field(default=None, min_length=4, max_length=4)
    points_px: list[Point2] | None = None
    score: JsonNum | None = None


class ObjectSource(Strict):
    kind: Literal[
        "detector",
        "vlm",
        "tap",
        "annotation",
        "tape",
        "laser_scan",
        "lidar",
        "reconstruction",
        "generated",
    ]
    model: str | None = None
    views: list[SourceView] | None = None


class Support(Strict):
    points: JsonInt | None = Field(default=None, ge=0)
    views: list[str] | None = None
    fit_rms_m: JsonNum | None = Field(default=None, ge=0)


class BoxGeom(Strict):
    kind: Literal["box"]
    center_m: Point3
    size_m: list[JsonNum] = Field(min_length=3, max_length=3)
    yaw_deg: JsonNum | None = None


class PointGeom(Strict):
    kind: Literal["point"]
    pos_m: Point3


class WallRectGeom(Strict):
    kind: Literal["wall_rect"]
    wall_id: str
    s_m: SpanLoose
    bottom_m: JsonNum
    top_m: JsonNum
    standoff_m: JsonNum | None = Field(default=None, ge=0)


class FootprintGeom(Strict):
    kind: Literal["footprint"]
    polygon_m: list[Point2] = Field(min_length=3)
    bottom_m: JsonNum
    top_m: JsonNum


Geometry = Annotated[BoxGeom | PointGeom | WallRectGeom | FootprintGeom, Field(discriminator="kind")]


class ObjectAttrs(Strict):
    operable: bool | None = None
    ground_level: bool | None = None
    well: bool | None = None
    movable: bool | None = None
    text: str | None = None


class SceneObject(Strict):
    id: str
    class_: ObjectClass = Field(alias="class")
    geometry: Geometry
    confidence: JsonNum = Field(ge=0, le=1)
    plus_minus_m: JsonNum = Field(ge=0)
    source: ObjectSource
    evidence: Evidence
    label: str | None = None
    wall_id: str | None = None
    attrs: ObjectAttrs | None = None


class Producer(Strict):
    path: str
    version: str | None = None
    code_ref: str | None = None
    params: dict[str, Any] | None = None


class ProvenanceInputs(Strict):
    packet_id: str | None = None
    packet_sha256: str | None = None
    images_given: JsonInt | None = Field(default=None, ge=0)
    images_used: JsonInt | None = Field(default=None, ge=0)
    sensors_used: (
        list[
            Literal[
                "images",
                "intrinsics",
                "arkit_poses",
                "gravity",
                "lidar_depth",
                "arkit_mesh",
                "arkit_planes",
                "taps",
                "gps",
                "compass",
                "annotations",
                "laser_scan",
            ]
        ]
        | None
    ) = None


class Provenance(Strict):
    data: DataKind
    site: str
    producer: Producer
    scale_source: Literal[
        "ground_truth",
        "arkit_poses",
        "lidar",
        "tap_distance",
        "scale_reference",
        "native_metric",
        "none",
    ]
    gravity_source: Literal["ground_truth", "arkit", "imu", "ground_plane_fit", "none"]
    inputs: ProvenanceInputs | None = None
    scale_reference_ids: list[str] | None = None
    runtime_s: JsonNum | None = Field(default=None, ge=0)
    cost_usd: JsonNum | None = Field(default=None, ge=0)
    hardware: str | None = None
    license: str | None = None
    notes: list[str] | None = None


class FrameAnchor(Strict):
    element_id: str
    plus_minus_m: JsonNum | None = Field(default=None, ge=0)
    heading_plus_minus_deg: JsonNum | None = Field(default=None, ge=0)


class Frame(Strict):
    units: Literal["m"]
    convention: Literal["meter_anchored/v0"]
    anchor: FrameAnchor
    source_frame: str | None = None
    from_source: Mat4 | None = None


class Asset(Strict):
    id: str
    kind: Literal["point_cloud", "mesh", "gaussians", "depth_maps", "coverage_grid", "images", "other"]
    path: str
    format: str | None = None
    sha256: str | None = None
    note: str | None = None
    in_scene_frame: bool | None = None
    count: JsonInt | None = Field(default=None, ge=0)
    fields: list[str] | None = None
    generated_field: str | None = None


class Wall(Strict):
    id: str
    role: Literal["house", "garage", "shed", "fence", "hedge", "retaining", "other"]
    baseline_m: list[Point2] = Field(min_length=2, max_length=2)
    height_m: JsonNum = Field(gt=0)
    plus_minus_m: JsonNum = Field(ge=0)
    evidence: Evidence
    base_y_m: JsonNum | None = None
    polygon_m: list[Point3] | None = Field(default=None, min_length=3)
    s_range_m: SpanLoose | None = None
    observed_fraction: JsonNum | None = Field(default=None, ge=0, le=1)
    support: Support | None = None


class Corner(Strict):
    id: str
    left_wall: str
    right_wall: str
    point_m: Point2
    s_m: JsonNum | None = None
    turn_deg: JsonNum | None = None
    plus_minus_m: JsonNum | None = Field(default=None, ge=0)
    evidence: Evidence | None = None


class End(Strict):
    kind: Literal["limit", "unexplored"]
    point_m: Point2 | None = None
    s_m: JsonNum | None = None
    note: str | None = None


class ChainEnds(Strict):
    left: End
    right: End


class Chain(Strict):
    wall_ids: list[str] = Field(min_length=1)
    ends: ChainEnds
    corners: list[Corner] | None = None


class PlaneSurface(Strict):
    kind: Literal["plane"]
    normal: Point3
    offset_m: JsonNum


class HeightfieldSurface(Strict):
    kind: Literal["heightfield"]
    asset: str
    cell_m: JsonNum | None = Field(default=None, gt=0)


Surface = Annotated[PlaneSurface | HeightfieldSurface, Field(discriminator="kind")]


class Region(Strict):
    id: str
    material: Material
    polygon_m: list[Point2] = Field(min_length=3)
    evidence: Evidence
    slope_deg: JsonNum | None = Field(default=None, ge=0)
    confidence: JsonNum | None = Field(default=None, ge=0, le=1)
    plus_minus_m: JsonNum | None = Field(default=None, ge=0)
    source: ObjectSource | None = None


class Ground(Strict):
    surface: Surface
    plus_minus_m: JsonNum = Field(ge=0)
    regions: list[Region] | None = None


class CoverageGrid(Strict):
    asset: str
    voxel_m: JsonNum = Field(gt=0)
    origin_m: Point3
    shape: list[JsonInt] = Field(min_length=3, max_length=3)
    min_views: JsonInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def shape_positive(self) -> CoverageGrid:
        if any(int(item) < 1 for item in self.shape):
            raise ValueError("grid shape entries must be >= 1")
        return self


class Band(Strict):
    band: Literal["wall", "ground", "overhead", "facing"]
    s_m: SpanLoose
    wall_id: str | None = None
    out_m: JsonNum | None = Field(default=None, ge=0)
    views: JsonInt | None = Field(default=None, ge=0)
    best_incidence_deg: JsonNum | None = Field(default=None, ge=0, le=90)


class IncidenceLimits(Strict):
    wall: JsonNum = Field(ge=0, le=90)
    ground: JsonNum = Field(ge=0, le=90)


class CoverageRule(Strict):
    min_views: JsonInt = Field(ge=1)
    max_incidence_deg: IncidenceLimits
    observed_share: JsonNum | None = Field(default=None, ge=0, le=1)


class Sensitivity(Strict):
    surface: Literal["wall", "ground"]
    max_incidence_deg: JsonNum = Field(ge=0, le=90)
    bands: list[Band]
    rule_id: str | None = None
    max_footprint_m: JsonNum | None = Field(default=None, gt=0)


class Coverage(Strict):
    method: str
    grid: CoverageGrid | None = None
    bands: list[Band] | None = None
    rule: CoverageRule | None = None
    sensitivity: list[Sensitivity] | None = None


class Overhead(Strict):
    wall_id: str
    s_m: SpanLoose
    clearance_m: JsonNum = Field(ge=0)
    plus_minus_m: JsonNum = Field(ge=0)
    evidence: Evidence
    object_id: str | None = None


class Facing(Strict):
    wall_id: str
    s_m: SpanLoose
    depth_m: JsonNum = Field(ge=0)
    plus_minus_m: JsonNum = Field(ge=0)
    evidence: Evidence
    lower_bound: bool | None = None
    object_id: str | None = None


class GeneratedGeometry(Strict):
    kind: Literal["box", "footprint", "asset_mask"]
    center_m: Point3 | None = None
    size_m: Point3 | None = None
    yaw_deg: JsonNum | None = None
    polygon_m: list[Point2] | None = None
    bottom_m: JsonNum | None = None
    top_m: JsonNum | None = None
    asset: str | None = None


class GeneratedRegion(Strict):
    id: str
    geometry: GeneratedGeometry
    method: str
    note: str | None = None


class SceneKeyframe(Strict):
    id: str
    pose: Mat4
    intrinsics: Intrinsics
    w: JsonInt = Field(ge=1)
    h: JsonInt = Field(ge=1)
    image: str | None = None
    pose_source: Literal["ground_truth", "arkit", "sfm", "model", "sfm_scaled_by_arkit"] | None = None


class Scene(Strict):
    format: Literal["base-scene/v0"]
    scene_id: str
    provenance: Provenance
    frame: Frame
    walls: list[Wall] = Field(min_length=1)
    chain: Chain
    ground: Ground
    objects: list[SceneObject]
    coverage: Coverage
    assets: list[Asset] | None = None
    overheads: list[Overhead] | None = None
    facing: list[Facing] | None = None
    generated_regions: list[GeneratedRegion] | None = None
    keyframes: list[SceneKeyframe] | None = None


def scene_consistency_errors(scene: dict[str, Any]) -> list[str]:
    """Mirror evalharness.sceneio.scene_consistency_errors."""

    errors: list[str] = []
    wall_ids = {wall["id"] for wall in scene.get("walls") or []}
    object_ids = [obj["id"] for obj in scene.get("objects") or []]
    if len(set(object_ids)) != len(object_ids):
        errors.append("duplicate object ids")
    anchor = scene.get("frame", {}).get("anchor", {}).get("element_id")
    meters = [obj for obj in scene.get("objects") or [] if obj.get("id") == anchor]
    if not meters or meters[0].get("class") != "meter":
        errors.append(f"frame.anchor.element_id {anchor!r} is not a meter object")
    for wall_id in scene.get("chain", {}).get("wall_ids") or []:
        if wall_id not in wall_ids:
            errors.append(f"chain names unknown wall {wall_id!r}")
    for obj in scene.get("objects") or []:
        geometry = obj.get("geometry") or {}
        if geometry.get("kind") == "wall_rect" and geometry.get("wall_id") not in wall_ids:
            errors.append(f"object {obj.get('id')} on unknown wall {geometry.get('wall_id')!r}")
    asset_ids = {asset["id"] for asset in scene.get("assets") or []}
    grid = (scene.get("coverage") or {}).get("grid")
    if grid and grid.get("asset") not in asset_ids:
        errors.append(f"coverage grid asset {grid.get('asset')!r} not in assets")
    return errors
