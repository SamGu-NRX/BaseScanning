import Foundation
import simd

// Builds scene.json (contract C1, server/schemas/scene.schema.json) from what the capture measured.
// The capture works in ARKit's gravity-aligned world frame in meters; the scene frame is that same
// frame in feet, so conversion is a uniform scale with no rotation or origin shift.

/// Meters to feet for the scene frame: scene.json is the capture's ARKit world frame written in
/// feet, so the one conversion between the two is this scale.
public enum SceneUnits {
    /// Exact by definition: the international foot is 0.3048 m.
    public static let feetPerMeter: Double = 1 / 0.3048
}

/// The wall chain described around the electric meter: the meter's wall, plus one more straight
/// piece for each corner the walk followed (`WallFrame.segments`).
///
/// `s` runs along the chain (positive to the right for someone outside facing the wall), `out`
/// runs horizontally away from the piece at `s` toward that person, and `height` is measured up
/// from `groundY`. On the meter's piece world(s, height, out) = (meter.x, groundY, meter.z) +
/// along * s + outward * out + (0, height, 0).
public struct SceneWall: Sendable, Equatable {
    /// A point on the wall face at the meter, world meters.
    public var meter: SIMD3<Float>
    /// Unit horizontal vector from the meter's wall toward the homeowner.
    public var outward: SIMD3<Float>
    /// World y of the ground in front of the wall, meters.
    public var groundY: Float
    /// Corners the walk followed, nearest the meter first (`WallFrame.leftCorners`). Each carries
    /// how the line of the piece past it was found.
    public var leftCorners: [WallCorner]
    /// Corners the walk followed, nearest the meter first (`WallFrame.rightCorners`). Each carries
    /// how the line of the piece past it was found.
    public var rightCorners: [WallCorner]
    /// How the line of the meter's piece was found.
    public var source: WallLineSource

    /// The chain from the meter's wall out through the corners, in walk order. Nothing is
    /// re-derived here; `SceneExport` refuses corners that do not run outward from the meter
    /// (`SceneExportError.cornersOutOfOrder`).
    public init(
        meter: SIMD3<Float>, outward: SIMD3<Float>, groundY: Float,
        leftCorners: [WallCorner] = [], rightCorners: [WallCorner] = [], source: WallLineSource = .tap
    ) {
        self.meter = meter
        self.outward = outward
        self.groundY = groundY
        self.leftCorners = leftCorners
        self.rightCorners = rightCorners
        self.source = source
    }


    /// Unit vector toward +s on the meter's wall: cross(-outward, up). For outward (0, 0, 1) this
    /// is (1, 0, 0), which is the scene schema's rule that outward is the baseline direction
    /// turned 90 degrees clockwise viewed from +y.
    public var along: SIMD3<Float> {
        simd_normalize(simd_cross(-outward, SIMD3<Float>(0, 1, 0)))
    }

    /// The straight pieces, left to right, and the index of the meter's.
    public var chain: (segments: [WallSegment], meter: Int) {
        WallSegment.chain(outward: outward, source: source, left: leftCorners, right: rightCorners)
    }

    /// The world point, meters, at coordinates on the chain: `s` picks the piece (past a corner,
    /// the piece there), `height` rises from `groundY` and `out` runs away from that piece's face.
    /// `wallCoordinates(of:)` maps such a point back.
    public func world(s: Float, height: Float, out: Float) -> SIMD3<Float> {
        let segments = chain.segments
        let piece = segments[WallSegment.index(in: segments, atS: s)]
        return SIMD3<Float>(meter.x, groundY, meter.z) + piece.anchor + piece.along * (s - piece.anchorS) + piece.outward * out
            + SIMD3<Float>(0, height, 0)
    }

    /// Coordinates on the piece nearest the point in plan, s clamped to that piece (as
    /// `WallFrame.wallPoint`).
    public func wallCoordinates(of point: SIMD3<Float>) -> (s: Float, height: Float, out: Float) {
        let d = point - meter
        let segments = chain.segments
        let piece = segments[WallSegment.nearest(in: segments, toOffset: d)]
        let local = piece.coordinates(ofOffset: d)
        return (min(max(local.s, piece.span.lowerBound), piece.span.upperBound), point.y - groundY, local.out)
    }

    /// Projects a scene plan point [x, z] in feet (for example a `route.polyline` vertex from the
    /// placement result) onto this wall, returning s and out in meters.
    public func wallCoordinates(ofPlanPointFeet point: SIMD2<Double>) -> (s: Float, out: Float) {
        let world = SIMD3<Float>(
            Float(point.x / SceneUnits.feetPerMeter), meter.y, Float(point.y / SceneUnits.feetPerMeter))
        let c = wallCoordinates(of: world)
        return (c.s, c.out)
    }

    /// s along the chain, meters, of a polyline given as plan offsets from the meter ([dx, dz]
    /// feet in the scene frame's axes), with the s of every corner the line passes between two of
    /// its points added in between. Each point maps to the piece nearest it (`wallCoordinates`).
    ///
    /// Drawn as straight lines between wall points, a route from one piece to the next with no
    /// vertex at the corner would cut across it; with the corner's s as a vertex every drawn
    /// stretch lies on one piece and the line bends where the wall does. A corner within 0.1 mm
    /// of a point is that point already and is not added again.
    public func chainS(ofPlanOffsetsFeet offsets: [SIMD2<Double>]) -> [Float] {
        let corners = chain.segments.dropLast().map(\.span.upperBound)
        let base = SIMD2<Double>(Double(meter.x), Double(meter.z)) * SceneUnits.feetPerMeter
        var path: [Float] = []
        for offset in offsets {
            let s = wallCoordinates(ofPlanPointFeet: base + offset).s
            if let last = path.last {
                let between = corners.filter { min(last, s) + 1e-4 < $0 && $0 < max(last, s) - 1e-4 }
                path += s >= last ? between : between.reversed()
            }
            path.append(s)
        }
        return path
    }
}

/// An opening in the wall the scene records: a door or a window. The rawValue is the object
/// `type` scene.json carries.
public enum SceneOpeningKind: String, Sendable, Equatable {
    case door
    case window
}

/// Something tapped once that stands at the wall: a gas meter or an AC unit. The rawValue is the
/// object `type` scene.json carries.
public enum ScenePointObjectKind: String, Sendable, Equatable {
    case gasMeter = "gas_meter"
    case ac
}

/// Something the scene records beside the wall: an opening in it, an object standing at it, a
/// fence or hedge in front of it, or a driveway. `SceneExport` writes openings and point objects
/// as objects, fences as facing entries and driveways as ground entries.
public enum SceneFeature: Sendable {
    /// A door or window on the wall. `span` is in s meters; `bottom` and `top` are meters above the
    /// ground. `operable` nil means the homeowner was not asked, and it is then left out.
    case opening(kind: SceneOpeningKind, span: ClosedRange<Float>, bottom: Float, top: Float, operable: Bool?)
    /// Something tapped once that stands off the wall (gas meter, AC unit). `tap` is a world point.
    case pointObject(kind: ScenePointObjectKind, tap: SIMD3<Float>, bottom: Float?, top: Float?)
    /// Two world points at the foot of a fence or hedge facing the wall.
    case fence(foot: [SIMD3<Float>])
    /// Two world points along one edge of a driveway.
    case driveway(edge: [SIMD3<Float>])
}

/// What the walk saw of the wall and the ground in front of it: stretches of s, each with its
/// reach, plus what the homeowner said the two ends are. Every span reader of `CoverageMap` has
/// a band here (`SceneCoverage.init(_:leftEndMarked:rightEndMarked:)`).
public struct SceneCoverage: Sendable {
    /// True when the homeowner marked the left end as a real limit (fence, property line).
    public var leftEndMarked: Bool
    /// True when the homeowner marked the right end as a real limit (fence, property line).
    public var rightEndMarked: Bool
    /// Stretches of wall face seen, each with how high up it was seen, meters above the ground
    /// (`CoverageMap.wallSeenSpans()`). Always sent as `out_ft`: scene.json's "no out_ft" means
    /// seen up to headroom height, which would claim more than a phone's view shows.
    public var wall: [ObservedSpan]
    /// Stretches of ground seen in front of the wall, each with how far out it was seen
    /// (`CoverageMap.groundDepthSpans()`).
    public var ground: [ObservedSpan]
    /// Stretches known clear in front of the wall, each out to where the homeowner walked less
    /// the position error (`CoverageMap.facingSpans()`).
    public var facing: [ObservedSpan]
    /// Stretches seen clear overhead, each up to the height (`out`) the tilt-up view reached
    /// (`CoverageMap.overheadSpans()`). The height is always sent: scene.json's "no out_ft"
    /// means seen clear all the way up, which a phone view of the wall's plane can't show.
    public var overhead: [ObservedSpan]

    /// The coverage as its parts, for spans already in hand;
    /// `init(_:leftEndMarked:rightEndMarked:)` reads them off a `CoverageMap`.
    public init(
        leftEndMarked: Bool, rightEndMarked: Bool, wall: [ObservedSpan], ground: [ObservedSpan],
        facing: [ObservedSpan] = [], overhead: [ObservedSpan] = []
    ) {
        self.leftEndMarked = leftEndMarked
        self.rightEndMarked = rightEndMarked
        self.wall = wall
        self.ground = ground
        self.facing = facing
        self.overhead = overhead
    }

    /// Everything `map` observed, with the ends' kinds from the homeowner's answers.
    public init(_ map: CoverageMap, leftEndMarked: Bool, rightEndMarked: Bool) {
        self.init(
            leftEndMarked: leftEndMarked, rightEndMarked: rightEndMarked,
            wall: map.wallSeenSpans(), ground: map.groundDepthSpans(), facing: map.facingSpans(),
            overhead: map.overheadSpans())
    }
}

/// One kept keyframe: a photo in the uploaded bundle and the camera pose and intrinsics it was
/// taken with, in the form scene.json's keyframes carry.
public struct SceneKeyframe: Sendable {
    /// The keyframe's identifier; `SceneExport` refuses an empty one.
    public var id: String
    /// ARKit camera transform: column-major, translation in meters.
    public var cameraToWorld: simd_float4x4
    /// [fx, fy, cx, cy] in pixels of the landscape sensor image.
    public var intrinsics: SIMD4<Float>
    /// The image's width, pixels; below 1 the keyframe is refused
    /// (`SceneExportError.invalidKeyframe`).
    public var w: Int
    /// The image's height, pixels; below 1 the keyframe is refused
    /// (`SceneExportError.invalidKeyframe`).
    public var h: Int
    /// JPEG file name inside the uploaded bundle.
    public var img: String

    /// One keyframe as captured: ARKit's camera transform, the landscape sensor image's
    /// intrinsics, and the image's size and file name.
    public init(id: String, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, w: Int, h: Int, img: String) {
        self.id = id
        self.cameraToWorld = cameraToWorld
        self.intrinsics = intrinsics
        self.w = w
        self.h = h
        self.img = img
    }
}

/// What the meter tap's raycast hit.
public enum MeterPlaneSource: Sendable, Equatable {
    /// The extent of a plane ARKit detected.
    case detectedPlane
    /// A plane ARKit estimated from feature points around the tap, with no detected plane there.
    case estimatedPlane

    /// Added to the meter's position error when the tap hit an estimated plane, meters. 0.15 m
    /// (about 6 in) is a guess: no measurement of estimated-plane depth error exists for this app.
    /// It is there so the server treats a meter placed on a guessed surface as less certain than
    /// one on a detected wall.
    public static let estimatedPlaneExtraError: Float = 0.15
}

/// Everything scene.json is built from: the wall chain around the meter, the wall's ends and
/// position errors, what was marked on and in front of the wall, what the walk and the LiDAR
/// mesh saw, the keyframes and stills the bundle carries, and what the homeowner said the ground
/// is. Feed one to `SceneExport.jsonData`.
public struct SceneInput: Sendable {
    /// The wall chain the meter sits on, corners included (`SceneWall`).
    public var wall: SceneWall
    /// Id of the meter's wall. A chain's other walls are named after it, with their side and
    /// count from the meter: "<wallID>-left-1" is the first wall round the left corner.
    public var wallID: String
    /// s meters of the chain's left and right ends. Must contain 0, the meter.
    public var baselineS: ClosedRange<Float>
    /// The wall's height above the ground, meters, written on every wall of the chain as
    /// `height_ft`; nil omits it.
    public var wallHeight: Float?
    /// Meter position error, meters. Nil leaves the server's default for AR taps.
    public var meterPlusMinus: Float?
    /// How the meter tap found the wall. An estimated plane widens the exported meter error by
    /// `MeterPlaneSource.estimatedPlaneExtraError`.
    public var meterPlane: MeterPlaneSource
    /// Position error of every object (openings, gas meter, AC), meters. Nil leaves the server's
    /// default for the object's source.
    public var objectPlusMinus: Float?
    /// What was marked along the wall: openings, point objects, fences and driveways
    /// (`SceneFeature`).
    public var features: [SceneFeature]
    /// What the walk saw (`SceneCoverage`).
    public var coverage: SceneCoverage
    /// The scan's kept keyframes, each with its photo's file name (`SceneKeyframe`).
    public var keyframes: [SceneKeyframe]
    /// Close-up photo file names keyed by purpose.
    public var stills: [String: String]
    /// Facing gaps measured on the LiDAR mesh (`TriangleMesh.facingSpans`): stretches in s
    /// meters, each with the gap in meters. Written as `facing` entries without `plus_minus_ft`,
    /// so the server applies its mesh error.
    public var meshFacing: [ObservedSpan]
    /// Headroom measured on the LiDAR mesh (`TriangleMesh.overheadSpans`), as `meshFacing`;
    /// written as `overheads` entries.
    public var meshOverheads: [ObservedSpan]
    /// What the homeowner said the ground along the wall is. With a type, the ground the ground
    /// coverage saw is sent as patches of it (`SceneWall.groundPatchPolygons`); nil sends none,
    /// and the server treats the surface as unknown.
    public var groundType: SceneGroundType?

    /// Creates the scene's input. Required are the wall, its ends (`baselineS`) and the coverage;
    /// defaults carry the fields a scan may have nothing of: a measured wall height, error
    /// overrides, features, keyframes and stills, mesh measurements, a named ground type.
    public init(
        wall: SceneWall, wallID: String = "wall", baselineS: ClosedRange<Float>, wallHeight: Float? = nil,
        meterPlusMinus: Float? = nil, meterPlane: MeterPlaneSource = .detectedPlane, objectPlusMinus: Float? = nil,
        features: [SceneFeature] = [], coverage: SceneCoverage, keyframes: [SceneKeyframe] = [], stills: [String: String] = [:],
        meshFacing: [ObservedSpan] = [], meshOverheads: [ObservedSpan] = [], groundType: SceneGroundType? = nil
    ) {
        self.wall = wall
        self.wallID = wallID
        self.baselineS = baselineS
        self.wallHeight = wallHeight
        self.meterPlusMinus = meterPlusMinus
        self.meterPlane = meterPlane
        self.objectPlusMinus = objectPlusMinus
        self.features = features
        self.coverage = coverage
        self.keyframes = keyframes
        self.stills = stills
        self.meshFacing = meshFacing
        self.meshOverheads = meshOverheads
        self.groundType = groundType
    }
}

/// What `SceneExport.jsonData` refuses to export, with the failing value named in `description`.
public enum SceneExportError: Error, Equatable, CustomStringConvertible {
    case emptyWallID
    case outwardNotUnitHorizontal(SIMD3<Float>)
    case degenerateBaseline(lower: Float, upper: Float)
    case meterOutsideBaseline(lower: Float, upper: Float)
    case nonPositiveWallHeight(Float)
    case negativeValue(field: String, value: Float)
    case topBelowBottom(field: String, bottom: Float, top: Float)
    case wrongPointCount(feature: String, expected: Int, actual: Int)
    case degenerateSegment(feature: String)
    case invalidKeyframe(id: String, reason: String)
    case nonFiniteNumber(String)
    /// Left corners must run from the meter leftward (s falling below 0), right corners rightward.
    case cornersOutOfOrder([Float])

    /// The error in words, naming the field and value that failed.
    public var description: String {
        switch self {
        case .emptyWallID: "wallID is empty"
        case .outwardNotUnitHorizontal(let v): "wall outward \(v) is not a unit horizontal vector"
        case .degenerateBaseline(let l, let u): "baseline s range \(l)...\(u) has no length"
        case .meterOutsideBaseline(let l, let u): "baseline s range \(l)...\(u) does not contain the meter (s = 0)"
        case .nonPositiveWallHeight(let h): "wall height \(h) m is not positive"
        case .negativeValue(let f, let v): "\(f) is \(v), must be >= 0"
        case .topBelowBottom(let f, let b, let t): "\(f): top \(t) m is below bottom \(b) m"
        case .wrongPointCount(let f, let e, let a): "\(f) needs \(e) points, got \(a)"
        case .degenerateSegment(let f): "\(f) points coincide in plan"
        case .invalidKeyframe(let id, let r): "keyframe \(id): \(r)"
        case .nonFiniteNumber(let d): "non-finite number in scene: \(d)"
        case .cornersOutOfOrder(let s): "corner s values \(s) do not run outward from the meter"
        }
    }
}

extension ObservedSpan {
    /// At most `limit` spans (`limit` >= 1), made by joining the touching neighbours (within
    /// 0.1 mm) whose reaches differ least, each join keeping the smaller reach, so a joined span
    /// reaches no farther than any span it covers. Spans farther apart are never joined: that
    /// would claim the gap between them. If more than `limit` separate runs remain, the shortest
    /// are dropped, which only reports less.
    static func coarsened(_ spans: [ObservedSpan], toAtMost limit: Int) -> [ObservedSpan] {
        var spans = spans.sorted { $0.span.lowerBound < $1.span.lowerBound }
        let touching: Float = 1e-4
        while spans.count > limit {
            let joinable = spans.indices.dropLast().filter { abs(spans[$0].span.upperBound - spans[$0 + 1].span.lowerBound) < touching }
            let difference = { (i: Int) in abs(spans[i].out - spans[i + 1].out) }
            let length = { (i: Int) in spans[i].span.upperBound - spans[i].span.lowerBound }
            guard let best = joinable.min(by: { difference($0) < difference($1) }) else {
                if let shortest = spans.indices.min(by: { length($0) < length($1) }) { spans.remove(at: shortest) }
                continue
            }
            spans[best] = ObservedSpan(span: spans[best].span.lowerBound...spans[best + 1].span.upperBound, out: min(spans[best].out, spans[best + 1].out))
            spans.remove(at: best + 1)
        }
        return spans
    }
}

/// The scene.json builder: `jsonData` validates a `SceneInput` (`SceneExportError`), converts the
/// capture's meters to the scene's feet (`SceneUnits`), and encodes deterministic JSON whose
/// numbers are rounded to four decimals.
public enum SceneExport {
    /// Half the plan width of a tapped gas meter. The tap gives one point, not a size, so the
    /// meter is drawn as a 0.3 m square: a hypothesis for a typical residential gas meter or
    /// regulator, not a measured size. Replace it once the capture measures the object.
    static let pointObjectHalfWidth: Float = 0.15
    /// How far a tapped gas meter is assumed to stand off the wall, same 0.3 m hypothesis.
    static let pointObjectDepth: Float = 0.3
    /// Side of the square an AC unit is assumed to cover, in meters: about 3 ft along the wall and
    /// 3 ft out from it, centred on the one tap (team decision on #72, option (b)). A typical
    /// residential condenser, not a measured size: the tap gives no size, and the review says the
    /// size is assumed. Replace it once the AC is marked by its two edges.
    public static let acAssumedSide: Float = 0.9144

    /// Half the plan width and the depth of a tapped point object of `kind`.
    static func pointObjectSize(_ kind: ScenePointObjectKind) -> (halfWidth: Float, depth: Float) {
        switch kind {
        case .gasMeter: (pointObjectHalfWidth, pointObjectDepth)
        case .ac: (acAssumedSide / 2, acAssumedSide)
        }
    }
    /// Width of the strip drawn along a tapped driveway edge, feet. The tap marks only the edge
    /// line; the strip gives the polygon the area the schema requires. Illustrative, not measured.
    static let drivewayStripFeet: Double = 0.5
    /// Tolerance for "unit horizontal" on the wall's outward vector. Float round-off from ARKit
    /// transforms is ~1e-6; 1e-3 rejects a caller passing an unnormalized or tilted vector.
    static let unitTolerance: Float = 1e-3
    /// scene.schema.json's `coverage.observed` maxItems.
    static let maxObserved = 500
    /// scene.schema.json's `facing` and `overheads` maxItems.
    static let maxMeasured = 500
    /// scene.schema.json's `ground` maxItems, shared by driveway strips and ground patches.
    static let maxGround = 200

    /// How many `coverage.observed` entries each band (wall, ground, facing, overhead) may use:
    /// the schema's limit shared equally. Every band carries a reach, which changes from cell to
    /// cell, so any of them can run to many entries.
    static let bandBudget = maxObserved / 4

    /// How many entries `band` ("wall", "ground", "facing" or "overhead") is joined to before it
    /// is written on a chain with `corners` corners: `bandBudget`, less one ground entry per
    /// corner, since a ground entry is cut in two at every corner it crosses (`makeDocument`).
    /// The guidance planner reads each band through this same limit (`GapPlanner.exported`), so
    /// a request is met locally only when the joined reaches the export writes meet it (#129).
    static func observedBudget(_ band: String, corners: Int) -> Int {
        band == "ground" ? max(1, bandBudget - corners) : bandBudget
    }

    /// Encodes the scene as deterministic JSON (sorted keys, numbers rounded to 4 decimals).
    public static func jsonData(_ input: SceneInput) throws -> Data {
        let document = try makeDocument(input)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        do {
            return try encoder.encode(document)
        } catch let EncodingError.invalidValue(value, context) {
            let path = context.codingPath.map { $0.intValue.map(String.init) ?? $0.stringValue }.joined(separator: ".")
            throw SceneExportError.nonFiniteNumber("\(value) at \(path)")
        }
    }

    private static func makeDocument(_ input: SceneInput) throws -> SceneDocument {
        let wall = input.wall
        guard !input.wallID.isEmpty else { throw SceneExportError.emptyWallID }
        for outward in [wall.outward] + (wall.leftCorners + wall.rightCorners).map(\.outward) {
            guard abs(outward.y) < unitTolerance, abs(simd_length(outward) - 1) < unitTolerance else {
                throw SceneExportError.outwardNotUnitHorizontal(outward)
            }
        }
        let rightCornerS = [0] + wall.rightCorners.map(\.s)
        let leftCornerS = [0] + wall.leftCorners.map(\.s)
        guard zip(rightCornerS, rightCornerS.dropFirst()).allSatisfy({ $0 < $1 }), zip(leftCornerS, leftCornerS.dropFirst()).allSatisfy({ $0 > $1 }) else {
            throw SceneExportError.cornersOutOfOrder(wall.leftCorners.map(\.s).reversed() + wall.rightCorners.map(\.s))
        }
        let chain = wall.chain
        guard input.baselineS.lowerBound < input.baselineS.upperBound else {
            throw SceneExportError.degenerateBaseline(lower: input.baselineS.lowerBound, upper: input.baselineS.upperBound)
        }
        guard input.baselineS.contains(0) else {
            throw SceneExportError.meterOutsideBaseline(lower: input.baselineS.lowerBound, upper: input.baselineS.upperBound)
        }
        if let h = input.wallHeight, !(h > 0) { throw SceneExportError.nonPositiveWallHeight(h) }
        if let pm = input.meterPlusMinus { try requireNonNegative(pm, "meterPlusMinus") }
        if let pm = input.objectPlusMinus { try requireNonNegative(pm, "objectPlusMinus") }
        let objectError = input.objectPlusMinus.map(feet)
        let meterError: Float? = switch input.meterPlane {
        case .detectedPlane: input.meterPlusMinus
        case .estimatedPlane: (input.meterPlusMinus ?? ServerErrorDefaults.meter) + MeterPlaneSource.estimatedPlaneExtraError
        }

        let plan = { (s: Float, out: Float) in planFeet(wall.world(s: s, height: 0, out: out)) }
        // One scene wall per piece of the chain: the meter's keeps `wallID`.
        let wallIDs = chain.segments.indices.map { index in
            index == chain.meter ? input.wallID
                : "\(input.wallID)-\(index < chain.meter ? "left" : "right")-\(abs(index - chain.meter))"
        }
        // Only pieces inside the baseline are written (`walls`), so an object past an end that
        // comes before a corner belongs to the last written wall, which the server continues past
        // its end; the piece round the corner has no wall in the scene to refer to.
        let writtenPieces = chain.segments.indices.filter {
            max(chain.segments[$0].span.lowerBound, input.baselineS.lowerBound) < min(chain.segments[$0].span.upperBound, input.baselineS.upperBound)
        }
        let wallIDAt = { (s: Float) -> String in
            let index = WallSegment.index(in: chain.segments, atS: s)
            if writtenPieces.contains(index) || writtenPieces.isEmpty { return wallIDs[index] }
            // Past the written chain: the written piece nearest along the chain, which is the one
            // at that end.
            let nearest = writtenPieces.min { abs($0 - index) < abs($1 - index) } ?? chain.meter
            return wallIDs[nearest]
        }

        var objects: [SceneDocument.Object] = []
        var ground: [SceneDocument.Ground] = []
        var facing: [SceneDocument.Facing] = []
        for (index, feature) in input.features.enumerated() {
            let name = "features[\(index)]"
            switch feature {
            case let .opening(kind, span, bottom, top, operable):
                try requireNonNegative(bottom, "\(name).bottom")
                guard top >= bottom else { throw SceneExportError.topBelowBottom(field: name, bottom: bottom, top: top) }
                objects.append(.init(
                    type: kind.rawValue, wall_id: wallIDAt((span.lowerBound + span.upperBound) / 2), span_ft: spanFeet(span),
                    bottom_ft: feet(bottom), top_ft: feet(top),
                    attrs: operable.map { SceneDocument.Attrs(operable: $0) }, source: "tap", footprint: nil,
                    plus_minus_ft: objectError))
            case let .pointObject(kind, tap, bottom, top):
                if let bottom { try requireNonNegative(bottom, "\(name).bottom") }
                if let top { try requireNonNegative(top, "\(name).top") }
                if let bottom, let top, top < bottom {
                    throw SceneExportError.topBelowBottom(field: name, bottom: bottom, top: top)
                }
                let s = wall.wallCoordinates(of: tap).s
                let (halfWidth, depth) = pointObjectSize(kind)
                let left = s - halfWidth
                let right = s + halfWidth
                objects.append(.init(
                    type: kind.rawValue, wall_id: wallIDAt(s), span_ft: spanFeet(left...right),
                    bottom_ft: bottom.map(feet), top_ft: top.map(feet), attrs: nil, source: "tap",
                    footprint: [plan(left, 0), plan(right, 0), plan(right, depth), plan(left, depth)],
                    plus_minus_ft: objectError))
            case let .fence(foot):
                guard foot.count == 2 else {
                    throw SceneExportError.wrongPointCount(feature: "\(name) fence", expected: 2, actual: foot.count)
                }
                let a = wall.wallCoordinates(of: foot[0])
                let b = wall.wallCoordinates(of: foot[1])
                // The nearer tap: a fence that angles toward the wall must not read as farther out
                // at its narrow end than it is. The mean overstated that end by half the difference.
                let depth = min(a.out, b.out)
                try requireNonNegative(depth, "\(name) fence depth")
                facing.append(.init(
                    wall_id: wallIDAt((a.s + b.s) / 2), span_ft: spanFeet(min(a.s, b.s)...max(a.s, b.s)), depth_ft: feet(depth)))
            case let .driveway(edge):
                guard edge.count == 2 else {
                    throw SceneExportError.wrongPointCount(feature: "\(name) driveway", expected: 2, actual: edge.count)
                }
                ground.append(.init(type: "drive", polygon: try drivewayStrip(edge[0], edge[1], wall: wall, name: name)))
            }
        }

        let coverage = input.coverage
        var reaches: [(band: String, spans: [ObservedSpan])] = [
            ("wall", coverage.wall), ("ground", coverage.ground), ("facing", coverage.facing), ("overhead", coverage.overhead),
        ]
        for (band, spans) in reaches {
            for (index, item) in spans.enumerated() { try requireNonNegative(item.out, "coverage.\(band)[\(index)].out") }
        }
        let corners = chain.segments.dropLast().map(\.span.upperBound)
        let writtenWalls = walls(chain.segments, ids: wallIDs, sources: chain.segments.map(\.source), baselineS: input.baselineS, height: input.wallHeight, plan: plan)
        let meterFeet = point3Feet(wall.meter)
        // Ground entries stop short of every corner, after all joining. The server draws a ground
        // entry as the strip in front of the chain over its span and, where the span crosses a
        // corner, fills the sector between the two pieces' strips (server/scene.py
        // `band_polygon`, t3/server 930e8e5); no coverage sample lies in that sector. It finds
        // the corners from the walls as written, whose rounding moves them off the phone's by
        // up to about 0.0001 ft, and an entry crossing by more than 1e-9 ft brings the sector
        // back, so the cut is at the corners as the server computes them (`serverCornerS`).
        // Entries that stop at a corner are drawn separately and unioned, which adds no sector.
        // The wall, facing and overhead bands are read only as stretches of s (`missing`,
        // `seen_to`), so crossing a corner claims nothing extra there. Joining first leaves room
        // for one more entry per corner.
        let serverCorners = Self.serverCornerS(writtenWalls, meterPlan: SIMD2(meterFeet[0], meterFeet[2]), meterWallID: input.wallID)
        reaches = reaches.map { band, spans in
            (band, ObservedSpan.coarsened(spans, toAtMost: observedBudget(band, corners: corners.count)))
        }
        var observed: [SceneDocument.Observed] = []
        for (band, spans) in reaches {
            for item in spans {
                guard let span = spanInward(item.span) else { continue }
                let pieces = band == "ground" ? Self.cut(span, around: serverCorners, margin: cornerMarginFeet) : [span]
                observed += pieces.map { .init(band: band, span_ft: $0, out_ft: feetDown(item.out)) }
            }
        }
        if let type = input.groundType {
            // Patches from the ground entries as written, not the meters before rounding.
            let written = observed.filter { $0.band == "ground" }.compactMap { entry in
                entry.out_ft.map { out in
                    ObservedSpan(
                        span: Float(entry.span_ft[0] / SceneUnits.feetPerMeter)...Float(entry.span_ft[1] / SceneUnits.feetPerMeter),
                        out: Float(out / SceneUnits.feetPerMeter))
                }
            }
            ground += groundPatches(type, over: written, wall: wall, extent: input.baselineS, room: maxGround - ground.count)
        }

        // Mesh measurements, split where the chain turns a corner so each entry names the wall it
        // is in front of. Coarsening first leaves room for one more entry per corner.
        func measured(_ spans: [ObservedSpan], _ field: String, room: Int) throws -> [(id: String, span: [Double], value: Double)] {
            for (index, item) in spans.enumerated() { try requireNonNegative(item.out, "\(field)[\(index)].out") }
            let limit = room - corners.count
            guard limit >= 1 else { return [] }
            return Self.split(ObservedSpan.coarsened(spans, toAtMost: limit), at: corners).compactMap { item in
                spanInward(item.span).map { (wallIDAt((item.span.lowerBound + item.span.upperBound) / 2), $0, feetDown(item.out)) }
            }
        }
        facing += try measured(input.meshFacing, "meshFacing", room: maxMeasured - facing.count).map {
            SceneDocument.Facing(wall_id: $0.id, span_ft: $0.span, depth_ft: $0.value)
        }
        let overheads = try measured(input.meshOverheads, "meshOverheads", room: maxMeasured).map {
            SceneDocument.Overhead(wall_id: $0.id, span_ft: $0.span, clearance_ft: $0.value)
        }

        let keyframes = try input.keyframes.map(keyframe)

        return SceneDocument(
            schema_version: "1.0",
            meter: .init(pos: point3Feet(wall.meter), wall_id: input.wallID, plus_minus_ft: meterError.map(feet)),
            walls: writtenWalls,
            objects: objects, ground: ground, overheads: overheads.isEmpty ? nil : overheads, facing: facing,
            coverage: .init(
                ends: .init(
                    left: .init(kind: coverage.leftEndMarked ? "limit" : "unexplored"),
                    right: .init(kind: coverage.rightEndMarked ? "limit" : "unexplored")),
                observed: observed),
            keyframes: keyframes,
            stills: input.stills.isEmpty ? nil : input.stills)
    }

    /// The pieces of the chain within `baselineS`, left to right. Each wall starts at the point the
    /// previous one ends (the same numbers, computed once), so the server reads the chain as
    /// continuous and each corner as a corner.
    private static func walls(
        _ segments: [WallSegment], ids: [String], sources: [WallLineSource], baselineS: ClosedRange<Float>, height: Float?,
        plan: (Float, Float) -> [Double]
    ) -> [SceneDocument.Wall] {
        let pieces = segments.indices.compactMap { index -> (id: String, source: WallLineSource, span: ClosedRange<Float>)? in
            let low = max(segments[index].span.lowerBound, baselineS.lowerBound)
            let high = min(segments[index].span.upperBound, baselineS.upperBound)
            return low < high ? (ids[index], sources[index], low...high) : nil
        }
        let points = ([pieces.first?.span.lowerBound] + pieces.map(\.span.upperBound)).compactMap { $0 }.map { plan($0, 0) }
        // The source is always written, also `tap`, so the scene says how every line was found.
        return pieces.enumerated().map { index, piece in
            SceneDocument.Wall(
                id: piece.id, baseline: [points[index], points[index + 1]], height_ft: height.map(feet), source: piece.source.rawValue)
        }
    }

    /// How far short of a corner each ground entry beside it ends, feet. The corners come from
    /// the server's own arithmetic on the written chain, so no margin is needed for rounding;
    /// this one covers floating-point differences from the server's Python and small changes in
    /// how it unrolls the chain, either of which would otherwise bring the whole sector back.
    /// The 0.004 ft left between the two sides is under the server's COVERAGE_TOLERANCE_FT
    /// (0.01 ft), so the wall, facing and overhead bands read it as rounding, and inside the
    /// 0.005 ft by which it grows seen ground, which closes it with a rounded corner of that
    /// radius rather than the sector.
    static let cornerMarginFeet: Double = 0.002

    /// A written span in feet with `margin` either side of each corner removed: the side to the
    /// left ends at the corner less the margin rounded down to 4 decimals, the side to the right
    /// starts at the corner plus the margin rounded up.
    static func cut(_ span: [Double], around corners: [Double], margin: Double) -> [[Double]] {
        var pieces = [span]
        for corner in corners.sorted() {
            let left = ((corner - margin) * 10_000).rounded(.down) / 10_000
            let right = ((corner + margin) * 10_000).rounded(.up) / 10_000
            pieces = pieces.flatMap { piece -> [[Double]] in
                guard piece[0] < right, piece[1] > left else { return [piece] }
                return [[piece[0], min(piece[1], left)], [max(piece[0], right), piece[1]]].filter { $0[0] < $0[1] }
            }
        }
        return pieces
    }

    /// The s of each corner of the written chain as the server computes it (server/scene.py
    /// `parse_scene`, t3/server 930e8e5): walls laid end to end from the chain's left end, each
    /// as long as its written baseline, then shifted so the meter's plan point, projected onto
    /// the meter's wall and clamped to it, is at 0. Feet.
    fileprivate static func serverCornerS(_ walls: [SceneDocument.Wall], meterPlan: SIMD2<Double>, meterWallID: String) -> [Double] {
        var s = 0.0
        var pieces: [(id: String, a: SIMD2<Double>, along: SIMD2<Double>, s0: Double, s1: Double)] = []
        for wall in walls {
            let a = SIMD2(wall.baseline[0][0], wall.baseline[0][1])
            let b = SIMD2(wall.baseline[1][0], wall.baseline[1][1])
            let length = simd_length(b - a)
            guard length > 0 else { continue }
            pieces.append((wall.id, a, (b - a) / length, s, s + length))
            s += length
        }
        let meterPieces = pieces.filter { $0.id == meterWallID }.map { piece -> (distance: Double, s: Double) in
            let local = piece.s0 + simd_dot(meterPlan - piece.a, piece.along)
            let clamped = min(max(local, piece.s0), piece.s1)
            return (simd_length(meterPlan - (piece.a + piece.along * (clamped - piece.s0))), clamped)
        }
        let shift = meterPieces.min { $0.distance < $1.distance }?.s ?? 0
        return pieces.dropLast().map { $0.s1 - shift }
    }

    /// Spans cut at every one of `cuts` that lies strictly inside them.
    static func split(_ spans: [ObservedSpan], at cuts: [Float]) -> [ObservedSpan] {
        spans.flatMap { item in
            let edges = [item.span.lowerBound] + cuts.filter { item.span.lowerBound < $0 && $0 < item.span.upperBound }.sorted()
                + [item.span.upperBound]
            return zip(edges, edges.dropFirst()).map { ObservedSpan(span: $0...$1, out: item.out) }
        }
    }

    /// Ground entries closer than this are one stretch to the server, which reads gaps under its
    /// COVERAGE_TOLERANCE_FT (0.01 ft, server/scene.py at 930e8e5) as rounding and grows what was
    /// seen by half of it; a patch may run across such a gap at the lower reach.
    static let patchJoinGapFeet: Double = 0.01
    /// How far every seen edge of a patch is pulled in before its vertices are rounded to the
    /// nearest 0.0001 ft, which moves a point at most 0.71e-4 ft.
    static let patchInsetFeet: Double = 1e-4
    /// How far behind the wall line a patch starts. The server's wall line runs through the
    /// baseline as written, rounded like the patch; a patch starting exactly on the phone's line
    /// could round to a sliver short of the server's, which leaves that sliver an unrecorded
    /// surface along the whole wall and the footprint's back edge off the patch. 0.001 ft is ten
    /// rounding steps; behind the line is the house, where the server models no ground.
    static let patchBehindWallFeet: Double = 0.001

    /// Patches of `type` over the ground entries as written (`SceneWall.groundPatchPolygons`), at
    /// most `room` of them, without `plus_minus_ft` (the server's tap default). The entries are
    /// already split at corners and joined to their budget, so there is at most one patch per
    /// entry.
    private static func groundPatches(
        _ type: SceneGroundType, over spans: [ObservedSpan], wall: SceneWall, extent: ClosedRange<Float>, room: Int
    ) -> [SceneDocument.Ground] {
        guard room >= 1 else { return [] }
        let meters = { (feet: Double) in Float(feet / SceneUnits.feetPerMeter) }
        let polygons = wall.groundPatchPolygons(
            over: spans, within: extent, joinGap: meters(patchJoinGapFeet), inset: meters(patchInsetFeet), behind: meters(patchBehindWallFeet))
        let patches = polygons.compactMap { polygon -> SceneDocument.Ground? in
            var points: [[Double]] = []
            for p in polygon.map({ [feet($0.x), feet($0.y)] }) where p != points.last { points.append(p) }
            if points.count > 1, points.first == points.last { points.removeLast() }
            return points.count >= 3 ? SceneDocument.Ground(type: type.rawValue, polygon: points) : nil
        }
        // At most one polygon per written ground entry (125 at most, `bandBudget`), each of at
        // most 2 + 2 x 125 points plus two from a corner's cut, under the schema's 500. With so
        // many driveway strips that the entries outnumber the room, the last patches go: less
        // ground, never more.
        return Array(patches.prefix(room))
    }

    /// A strip `drivewayStripFeet` wide on the far side of the tapped edge from the wall. The offset
    /// is perpendicular to the edge, on whichever side has +out; an edge running straight out from
    /// the wall has no such side and keeps the perpendicular that is the edge direction turned
    /// 90 degrees clockwise viewed from +y.
    private static func drivewayStrip(
        _ p: SIMD3<Float>, _ q: SIMD3<Float>, wall: SceneWall, name: String
    ) throws -> [[Double]] {
        let a = SIMD2<Double>(planFeet(p)[0], planFeet(p)[1])
        let b = SIMD2<Double>(planFeet(q)[0], planFeet(q)[1])
        let d = b - a
        // 1e-3 ft is well under any tap error; below it the two taps are the same point.
        guard simd_length(d) > 1e-3 else { throw SceneExportError.degenerateSegment(feature: "\(name) driveway") }
        let unit = simd_normalize(d)
        var perp = SIMD2<Double>(-unit.y, unit.x)
        // The outward of the wall piece nearest the edge's middle.
        let middle = wall.wallCoordinates(of: (p + q) / 2).s
        let outward = wall.chain.segments[WallSegment.index(in: wall.chain.segments, atS: middle)].outward
        let outwardPlan = SIMD2<Double>(Double(outward.x), Double(outward.z))
        if simd_dot(perp, outwardPlan) < 0 { perp = -perp }
        let offset = perp * drivewayStripFeet
        return [a, b, b + offset, a + offset].map { [round4($0.x), round4($0.y)] }
    }

    private static func keyframe(_ k: SceneKeyframe) throws -> SceneDocument.Keyframe {
        guard !k.id.isEmpty else { throw SceneExportError.invalidKeyframe(id: k.id, reason: "empty id") }
        guard !k.img.isEmpty else { throw SceneExportError.invalidKeyframe(id: k.id, reason: "empty img") }
        guard k.w >= 1, k.h >= 1 else {
            throw SceneExportError.invalidKeyframe(id: k.id, reason: "image size \(k.w)x\(k.h)")
        }
        let m = k.cameraToWorld
        var pose: [Double] = []
        for column in 0..<4 {
            for row in 0..<4 {
                let value = Double(m[column][row])
                // Rotation stays unitless; only the translation (column 3, rows 0-2) becomes feet.
                pose.append(round4(column == 3 && row < 3 ? value * SceneUnits.feetPerMeter : value))
            }
        }
        let i = k.intrinsics
        return .init(
            id: k.id, pose: pose, intrinsics: [i.x, i.y, i.z, i.w].map { round4(Double($0)) },
            w: k.w, h: k.h, img: k.img)
    }

    private static func requireNonNegative(_ value: Float, _ field: String) throws {
        guard value >= 0 else { throw SceneExportError.negativeValue(field: field, value: value) }
    }

    private static func feet(_ meters: Float) -> Double { round4(Double(meters) * SceneUnits.feetPerMeter) }

    /// A reach in feet, rounded down to 4 decimals, since the server takes every `out_ft` as
    /// exact. One exception: a value less than 1e-7 ft below a 4-decimal value rounds up to it.
    /// That allowance absorbs Float32 error on exact half-foot values (three 6 in rows arrive as
    /// 1.4999999 ft and are written as 1.5, not 1.4999); it also means any value can be written
    /// up to 1e-7 ft above the Float it came from.
    static func feetDown(_ meters: Float) -> Double {
        let value = Double(meters) * SceneUnits.feetPerMeter
        let down = ((value + 1e-7) * 10_000).rounded(.down) / 10_000
        return down == 0 ? 0 : down
    }

    private static func spanFeet(_ span: ClosedRange<Float>) -> [Double] { [feet(span.lowerBound), feet(span.upperBound)] }

    /// A span of what was seen in feet, each end rounded to 4 decimals toward the other. One
    /// exception: an end less than 1e-5 ft outside a 4-decimal value is taken as that value.
    /// That allowance absorbs Float32 error on exact half-foot values: cell edges are whole 6 in
    /// steps, which arrive a few 1e-6 ft off at 30 ft from the meter, and rounding one a full
    /// step inward would open a 0.0001 ft gap between neighbouring entries that the server's
    /// `seen_to` reads as unseen. It also means any end can be written up to 1e-5 ft (3
    /// micrometres) outside the Float it came from. Nil when nothing is left.
    static func spanInward(_ span: ClosedRange<Float>) -> [Double]? {
        let noise = 1e-5
        let low = ((Double(span.lowerBound) * SceneUnits.feetPerMeter - noise) * 10_000).rounded(.up) / 10_000
        let high = ((Double(span.upperBound) * SceneUnits.feetPerMeter + noise) * 10_000).rounded(.down) / 10_000
        guard low < high else { return nil }
        return [low == 0 ? 0 : low, high == 0 ? 0 : high]
    }

    private static func planFeet(_ p: SIMD3<Float>) -> [Double] { [feet(p.x), feet(p.z)] }

    private static func point3Feet(_ p: SIMD3<Float>) -> [Double] { [feet(p.x), feet(p.y), feet(p.z)] }

    /// Four decimals of a foot is 0.03 mm, far below AR tap error; it only keeps the JSON readable.
    /// Non-finite values pass through so the encoder reports them.
    static func round4(_ x: Double) -> Double {
        guard x.isFinite else { return x }
        let r = (x * 10_000).rounded() / 10_000
        return r == 0 ? 0 : r  // drop negative zero
    }
}

/// Mirror of scene.schema.json. Property names are the schema's; nil optionals are omitted.
private struct SceneDocument: Encodable {
    struct Meter: Encodable {
        var pos: [Double]
        var wall_id: String
        var plus_minus_ft: Double?
    }
    struct Wall: Encodable {
        var id: String
        var baseline: [[Double]]
        var height_ft: Double?
        var source: String
    }
    struct Attrs: Encodable {
        var operable: Bool
    }
    struct Object: Encodable {
        var type: String
        var wall_id: String
        var span_ft: [Double]
        var bottom_ft: Double?
        var top_ft: Double?
        var attrs: Attrs?
        var source: String
        var footprint: [[Double]]?
        var plus_minus_ft: Double?
    }
    struct Ground: Encodable {
        var type: String
        var polygon: [[Double]]
    }
    struct Facing: Encodable {
        var wall_id: String
        var span_ft: [Double]
        var depth_ft: Double
    }
    struct Overhead: Encodable {
        var wall_id: String
        var span_ft: [Double]
        var clearance_ft: Double
    }
    struct End: Encodable {
        var kind: String
    }
    struct Ends: Encodable {
        var left: End
        var right: End
    }
    struct Observed: Encodable {
        var band: String
        var span_ft: [Double]
        var out_ft: Double?
    }
    struct Coverage: Encodable {
        var ends: Ends
        var observed: [Observed]
    }
    struct Keyframe: Encodable {
        var id: String
        var pose: [Double]
        var intrinsics: [Double]
        var w: Int
        var h: Int
        var img: String
    }

    var schema_version: String
    var meter: Meter
    var walls: [Wall]
    var objects: [Object]
    var ground: [Ground]
    var overheads: [Overhead]?
    var facing: [Facing]
    var coverage: Coverage
    var keyframes: [Keyframe]
    var stills: [String: String]?
}
