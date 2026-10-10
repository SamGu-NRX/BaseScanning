// TypeScript mirrors of the placement contract: server/schemas/scene.schema.json and
// server/schemas/result.schema.json. The schemas are the authority; tests in this folder
// load the real schema files and check this mirror against them (schema-conformance
// tests), so a schema change that matters fails a test instead of drifting silently.
//
// Lengths are feet everywhere, matching the schema. Plan coordinates are [x, z].

/** [x, z] plan point in feet (scene frame, ARKit .gravity world alignment). */
export type Point2 = [number, number];

/** [start, end] in s: feet along the wall chain from the meter, start <= end. */
export type Span = [number, number];

export type WallSource = "tap" | "mesh" | "plane";
export type ObjectSource = "vlm" | "tap" | "tape";
export type ObjectKind =
  | "window"
  | "door"
  | "garage_door"
  | "ac"
  | "gas_meter"
  | "elec_box"
  | "vent"
  | "downspout"
  | "pool"
  | "battery";
export type GroundKind = "drive" | "concrete" | "gravel" | "lawn" | "mulch" | "deck";
export type CoverageBand = "wall" | "ground" | "overhead" | "facing";
export type EndKind = "limit" | "unexplored";

export interface SceneWall {
  readonly id: string;
  readonly baseline: readonly Point2[];
  readonly height_ft?: number;
  readonly source?: WallSource;
  readonly plus_minus_ft?: number;
}

export interface SceneObject {
  readonly type: ObjectKind;
  readonly wall_id: string;
  readonly span_ft: Span;
  readonly bottom_ft?: number;
  readonly top_ft?: number;
  readonly attrs?: { readonly operable?: boolean; readonly well?: boolean };
  readonly source: ObjectSource;
  readonly conf?: number;
  readonly plus_minus_ft?: number;
  readonly footprint?: readonly Point2[];
}

export interface SceneGroundPatch {
  readonly type: GroundKind;
  readonly polygon: readonly Point2[];
  readonly plus_minus_ft?: number;
}

export interface SceneOverhead {
  readonly wall_id: string;
  readonly span_ft: Span;
  readonly clearance_ft: number;
  readonly plus_minus_ft?: number;
}

export interface SceneFacing {
  readonly wall_id: string;
  readonly span_ft: Span;
  readonly depth_ft: number;
  readonly plus_minus_ft?: number;
}

export interface CoverageObserved {
  readonly band: CoverageBand;
  readonly span_ft: Span;
  readonly out_ft?: number;
  readonly camera_pos_ft?: readonly [number, number];
}

export interface SceneCoverage {
  readonly ends?: {
    readonly left?: { readonly kind?: EndKind; readonly note?: string };
    readonly right?: { readonly kind?: EndKind; readonly note?: string };
  };
  readonly observed?: readonly CoverageObserved[];
}

export interface Scene {
  readonly schema_version?: string;
  readonly meter: {
    readonly pos: readonly [number, number, number];
    readonly wall_id: string;
    readonly plus_minus_ft?: number;
  };
  readonly walls: readonly SceneWall[];
  readonly objects?: readonly SceneObject[];
  readonly ground?: readonly SceneGroundPatch[];
  readonly overheads?: readonly SceneOverhead[];
  readonly facing?: readonly SceneFacing[];
  readonly coverage?: SceneCoverage;
  readonly keyframes?: readonly {
    readonly id: string;
    readonly img: string;
  }[];
  readonly stills?: Readonly<Record<string, string>>;
  readonly gps?: { readonly lat: number; readonly lon: number };
  readonly heading?: { readonly deg: number; readonly accuracy_deg?: number };
}

export type Outcome = "pass" | "fail" | "unsure";
export type Decision = "pass" | "manual_review" | "reject";
export type UnsureCause = "margin" | "unobserved" | "unknown_attribute" | "rule_requires_review";

export interface RuleCitation {
  /** Parameter name in rules.yaml. */
  readonly key: string;
  /** Citation for the value (public code or "Private rules"). */
  readonly source: string;
  /** True for a demo value with no public source. */
  readonly placeholder: boolean;
}

export interface ResultCheck {
  readonly id: string;
  readonly label: string;
  readonly outcome: Outcome;
  readonly unsure_cause?: UnsureCause;
  readonly reason: string;
  readonly measured_ft: number | null;
  readonly plus_minus_ft: number | null;
  readonly threshold_ft: number | null;
  readonly review_threshold_ft?: number;
  readonly comparison: "at_least" | "at_most" | null;
  readonly subject: string | null;
  readonly rule: RuleCitation;
}

export interface ResultSpot {
  readonly outcome: Outcome;
  readonly wall_id: string;
  readonly segment: number;
  readonly span_ft: Span;
  readonly width_ft: number;
  readonly depth_ft: number;
  readonly height_ft: number;
  readonly footprint: readonly [Point2, Point2, Point2, Point2];
  readonly center: Point2;
  readonly along: Point2;
  readonly outward: Point2;
  readonly meter_offset_ft: readonly [number, number];
  readonly route_length_ft: number | null;
}

export interface ResultRoute {
  readonly outcome: Outcome;
  readonly length_ft: number;
  readonly plus_minus_ft: number;
  readonly height_ft: number;
  readonly polyline: readonly Point2[];
  readonly detours?: readonly { readonly subject: string; readonly extra_ft: number }[];
  readonly length_is_lower_bound?: boolean;
  readonly crossings?: readonly {
    readonly subject: string;
    readonly span_ft: Span;
    readonly effect: "fail" | "review" | "detour" | "allow";
  }[];
}

export interface MissingEvidence {
  readonly kind: "band" | "past_end";
  readonly band?: CoverageBand;
  readonly span_ft?: Span;
  readonly out_ft?: number;
  readonly side?: "left" | "right";
  readonly checks?: readonly string[];
  readonly message: string;
}

export type ReasonCode =
  | "all_checks_pass"
  | "policy_not_approved"
  | "unsure_checks"
  | "unobserved_area"
  | "unexplored_end"
  | "all_spots_fail"
  | "no_wall_segment_fits";

export interface ResultReason {
  readonly code: ReasonCode;
  readonly message: string;
  readonly checks?: readonly string[];
}

export interface ResultEnd {
  readonly kind: EndKind;
  readonly s_ft: number;
  readonly point: Point2;
  readonly beyond_reach?: boolean;
}

export interface SweepRun {
  readonly wall_id: string;
  readonly segment?: number;
  readonly start_ft: Span;
  readonly outcome: Outcome;
  readonly failing: readonly string[];
  readonly unsure: readonly string[];
}

export interface PlacementResult {
  readonly schema_version: "1.0";
  readonly decision: Decision;
  readonly summary: string;
  readonly reasons: readonly ResultReason[];
  readonly policy: {
    readonly id: string | null;
    readonly version: string | null;
    readonly auto_approve: boolean;
    readonly allow_reject?: boolean;
    readonly sources: readonly ("public" | "private")[];
    readonly rules_sha256: string;
    readonly notice: string | null;
  };
  readonly spot: ResultSpot | null;
  readonly route: ResultRoute | null;
  readonly checks: readonly ResultCheck[];
  readonly nearest_considered?: ResultSpot | null;
  readonly missing_evidence: readonly MissingEvidence[];
  readonly objects_not_used?: readonly {
    readonly object: string;
    readonly side: "left" | "right";
    readonly message: string;
  }[];
  readonly ends: { readonly left: ResultEnd; readonly right: ResultEnd };
  readonly sweep: readonly SweepRun[];
  readonly stats: {
    readonly candidates: number;
    readonly pass: number;
    readonly unsure: number;
    readonly fail: number;
    readonly elapsed_ms: number;
    /** SHA-256 of the scene JSON bytes exactly as uploaded — the response-to-request binding. */
    readonly input_sha256: string;
  };
}
