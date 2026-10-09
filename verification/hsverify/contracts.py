"""Typed contracts for the JSON shapes hsverify reads and writes.

The scene (contract C1) and result (contract C2) shapes are defined by
`server/schemas/scene.schema.json` and `server/schemas/result.schema.json` in the
repository. The TypedDicts here are those schemas as this harness reads them, so
`mypy --strict` catches a misread field before a run misreports it.

The pattern at a trust boundary (a file on disk, an HTTP body, subprocess output) is:

1. validate the parsed data against the schema with `resultcheck.schema_errors` (or the
   module's own checker), so a failure names the file, the field and the offending value;
2. only then annotate the data as the TypedDict below. That annotation is a *checked*
   cast: each schema rejects unknown fields and requires the keys, so once validation
   passed, the TypedDict's shape holds.

TypedDicts are erased at runtime: nothing here changes what a run does. Keep this module
import-free of the rest of the package (no cycles); every name in it is data only.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# --- Shared primitives -----------------------------------------------------------------------
#
# JSON has no tuples: every array below is a list at runtime.

type Span = list[float]  # [start, end] in s (feet from the meter along the wall chain)
type PlanPoint = list[float]  # [x, z] in the gravity-aligned scene frame
type Point3 = list[float]  # [x, y, z]; the meter's position in the scene frame
type Outcome = Literal["pass", "fail", "unsure"]
type Band = Literal["wall", "ground", "overhead", "facing"]
type Decision = Literal["pass", "manual_review", "reject"]
type Side = Literal["left", "right"]

# --- Scene (contract C1, scene.schema.json) --------------------------------------------------


class MeterSpec(TypedDict):
    """scene["meter"]: the electric meter's position and the wall it sits on."""

    pos: Point3
    wall_id: str
    plus_minus_ft: NotRequired[float]


class Wall(TypedDict):
    """One wall of the chain, left to right as seen from outside."""

    id: str
    baseline: list[PlanPoint]
    height_ft: NotRequired[float]
    source: NotRequired[Literal["tap", "mesh", "plane"]]
    plus_minus_ft: NotRequired[float]


class SceneObjectAttrs(TypedDict, total=False):
    """Recognised attributes; a missing attribute is unknown, never false."""

    operable: bool
    well: bool


class SceneObject(TypedDict):
    """A marked object on a wall (window, gas meter, ...)."""

    type: Literal[
        "window",
        "door",
        "garage_door",
        "ac",
        "gas_meter",
        "elec_box",
        "vent",
        "downspout",
        "pool",
        "battery",
    ]
    wall_id: str
    span_ft: Span
    source: NotRequired[Literal["vlm", "tap", "tape"]]
    bottom_ft: NotRequired[float]
    top_ft: NotRequired[float]
    attrs: NotRequired[SceneObjectAttrs]
    conf: NotRequired[float]
    plus_minus_ft: NotRequired[float]
    footprint: NotRequired[list[PlanPoint]]


class GroundArea(TypedDict):
    """A stretch of ground (drive, lawn, ...) in plan."""

    type: Literal["drive", "concrete", "gravel", "lawn", "mulch", "deck"]
    polygon: list[PlanPoint]
    plus_minus_ft: NotRequired[float]


class Overhead(TypedDict):
    """Anything overhead along the wall, with the clear height beneath it."""

    wall_id: str
    span_ft: Span
    clearance_ft: float
    plus_minus_ft: NotRequired[float]


class FacingEntry(TypedDict):
    """Gap from the wall straight out to whatever faces it."""

    wall_id: str
    span_ft: Span
    depth_ft: float
    plus_minus_ft: NotRequired[float]


class CoverageEntry(TypedDict):
    """One observed stretch of a band; ground requires out_ft, the others may omit it."""

    band: Band
    span_ft: Span
    out_ft: NotRequired[float]


class SceneEnd(TypedDict):
    """How a walk ended on one side; absent means unexplored."""

    kind: Literal["limit", "unexplored"]
    note: NotRequired[str]


class SceneEnds(TypedDict, total=False):
    left: SceneEnd
    right: SceneEnd


class Coverage(TypedDict, total=False):
    ends: SceneEnds
    observed: list[CoverageEntry]


class SceneKeyframe(TypedDict):
    """A captured camera pose; scene schema requires all six fields."""

    id: str
    pose: list[float]
    intrinsics: list[float]
    w: int
    h: int
    img: str


class Gps(TypedDict):
    lat: float
    lon: float


class Heading(TypedDict):
    deg: float
    accuracy_deg: NotRequired[float]


class Scene(TypedDict):
    """The scene contract C1: what the phone measured around the meter."""

    meter: MeterSpec
    walls: list[Wall]
    schema_version: NotRequired[str]
    objects: NotRequired[list[SceneObject]]
    ground: NotRequired[list[GroundArea]]
    overheads: NotRequired[list[Overhead]]
    facing: NotRequired[list[FacingEntry]]
    coverage: NotRequired[Coverage]
    keyframes: NotRequired[list[SceneKeyframe]]
    stills: NotRequired[dict[str, str]]
    gps: NotRequired[Gps]
    heading: NotRequired[Heading]


# --- Result (contract C2, result.schema.json) ------------------------------------------------


class Reason(TypedDict):
    """Why the decision came out as it did."""

    code: Literal[
        "all_checks_pass",
        "policy_not_approved",
        "unsure_checks",
        "unobserved_area",
        "unexplored_end",
        "all_spots_fail",
        "no_wall_segment_fits",
    ]
    message: str
    checks: NotRequired[list[str]]


class Policy(TypedDict):
    """Which rules decided, and whether they allow automatic decisions."""

    id: str | None
    version: str | None
    auto_approve: bool
    sources: list[Literal["public", "private"]]
    rules_sha256: str
    notice: NotRequired[str | None]


class CheckRule(TypedDict):
    """Where a check's threshold comes from."""

    key: str
    source: str
    placeholder: bool


class Check(TypedDict):
    """One rule check at a spot; unsure_cause only on unsure outcomes."""

    id: str
    label: str
    outcome: Outcome
    reason: str
    measured_ft: float | None
    plus_minus_ft: float | None
    threshold_ft: float | None
    comparison: Literal["at_least", "at_most"] | None
    rule: CheckRule
    unsure_cause: NotRequired[
        Literal["margin", "unobserved", "unknown_attribute", "rule_requires_review"]
    ]
    review_threshold_ft: NotRequired[float]
    subject: NotRequired[str | None]


class SweepRun(TypedDict):
    """Merged runs of equal outcome along a wall's battery start positions."""

    wall_id: str
    start_ft: Span
    outcome: Outcome
    failing: list[str]
    unsure: list[str]
    segment: NotRequired[int]


class Spot(TypedDict):
    """The chosen battery position, as the app places it in AR."""

    outcome: Outcome
    wall_id: str
    segment: int
    span_ft: Span
    width_ft: float
    depth_ft: float
    height_ft: float
    footprint: list[PlanPoint]
    center: PlanPoint
    along: PlanPoint
    outward: PlanPoint
    meter_offset_ft: list[float]
    route_length_ft: float | None


class Detour(TypedDict):
    subject: str
    extra_ft: float


class Crossing(TypedDict):
    subject: str
    span_ft: Span
    effect: Literal["fail", "review", "detour", "allow"]


class Route(TypedDict):
    """The cable route from the meter to the chosen spot."""

    outcome: Outcome
    length_ft: float
    plus_minus_ft: float
    height_ft: float
    polyline: list[PlanPoint]
    detours: list[Detour]
    crossings: list[Crossing]


# "pass" is a Python keyword, so this one uses the functional form.
Stats = TypedDict(
    "Stats",
    {
        "candidates": int,
        "pass": int,
        "unsure": int,
        "fail": int,
        "elapsed_ms": float,
        "input_sha256": str,
    },
)


class ResultEnd(TypedDict):
    """Where the wall chain ends, as the result reports it."""

    kind: Literal["limit", "unexplored"]
    s_ft: float
    point: PlanPoint
    beyond_reach: NotRequired[bool]


class ResultEnds(TypedDict):
    left: ResultEnd
    right: ResultEnd


class EvidenceRequest(TypedDict):
    """A view that would settle an unsure check (`missing_evidence` entries)."""

    kind: Literal["band", "past_end"]
    message: str
    band: NotRequired[Band]
    span_ft: NotRequired[Span]
    out_ft: NotRequired[float]
    side: NotRequired[Side]
    checks: NotRequired[list[str]]


class ObjectNotUsed(TypedDict):
    object: str
    side: Side
    message: str


class SceneResult(TypedDict):
    """The result contract C2: the server's answer for one scene."""

    schema_version: str
    decision: Decision
    summary: str
    reasons: list[Reason]
    policy: Policy
    spot: Spot | None
    route: Route | None
    checks: list[Check]
    missing_evidence: list[EvidenceRequest]
    ends: ResultEnds
    sweep: list[SweepRun]
    stats: Stats
    nearest_considered: NotRequired[Spot | None]
    objects_not_used: NotRequired[list[ObjectNotUsed]]


# --- Case files (verification/e2e/cases/*.json) and their expectations -----------------------


class ExpectSpot(TypedDict):
    """Where the case expects a spot (or not)."""

    wall_id: str
    span_within: Span


class ExpectSweepRun(TypedDict, total=False):
    """What a case rules in or out for the sweep runs overlapping a stretch."""

    wall_id: str
    start_ft: Span
    outcome: Outcome
    outcome_not: Outcome
    reason: str
    failing_match: str
    unsure_match: str


class ExpectCheck(TypedDict, total=False):
    """What a case expects of the checks matching `match`."""

    match: str
    outcome: Outcome
    unsure_cause: str | None
    measured_ft: float
    plus_minus_ft: float


class ExpectStartOutcome(TypedDict, total=False):
    """What a case expects at one battery start."""

    wall_id: str
    start_ft: float
    outcome: Outcome
    why: str


class Expect(TypedDict, total=False):
    """The `expect` block of a case: the outcomes the scene's geometry forces."""

    decision_in: list[Decision]
    decision_not: list[Decision]
    spot: ExpectSpot | None
    sweep_runs: list[ExpectSweepRun]
    checks: list[ExpectCheck]
    start_outcomes: list[ExpectStartOutcome]
    missing_evidence_empty: bool


class CaseFile(TypedDict, total=False):
    """A case file: a scene (inline or by path) plus its expectations."""

    id: str
    scene: Scene
    scene_path: str
    expect: Expect
    rules_assumed: dict[str, float]
    source: str
    skip_reason: str
    real: bool


# --- Simulator run report (simrun.RunReport, written to report.json) -------------------------


class DeviceRecord(TypedDict):
    """The Simulator device a run used; report.py reads the appearance keys from it."""

    name: str
    udid: str
    runtime: str
    type: NotRequired[str]
    appearance: NotRequired[str]
    content_size: NotRequired[str | None]


class SimBuild(TypedDict, total=False):
    """One xcodebuild: ok with its timings and diagnostics, or failed with an error."""

    ok: bool
    error: str
    seconds: float
    waited_for_other_builds_s: float
    command: str
    warnings: list[str]
    errors: list[str]
    log: str


class ShotRow(TypedDict):
    """One captured state, as asdict() serialises simrun.ShotRecord."""

    index: int
    state: str
    seconds_after_launch: float
    screenshot: str
    transient: bool
    log_message: str


class SimReport(TypedDict, total=False):
    """The report.json of a Simulator run (simrun.RunReport's serialised shape)."""

    ref: str
    sha: str
    started_at: str
    command: list[str]
    device: DeviceRecord
    build: SimBuild
    launch_arguments: list[str]
    static_c4: dict[str, bool]
    states: list[ShotRow]
    end_reason: str
    problems: list[str]
    crash_reports: list[str]
    final_screenshot: str | None
    server: dict[str, object] | None
    peak_memory: dict[str, float] | None
    app_export: str | None
