import Foundation
import simd

/// Something the homeowner marked, in the meter frame (packet/README.md, "Marks, guidance and the
/// scene"). The factories fix each kind's point count: one for the meter, a wall end, a gas meter
/// or an AC unit; two for an opening (bottom-left, top-right), a drive edge or a fence.
public struct PacketMark: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable, CaseIterable {
        case meter
        case wallEnd = "wall_end"
        case gasMeter = "gas_meter"
        case ac
        case door
        case window
        case garageDoor = "garage_door"
        case driveEdge = "drive_edge"
        case fence

        public var pointCount: Int {
            switch self {
            case .meter, .wallEnd, .gasMeter, .ac: 1
            case .door, .window, .garageDoor, .driveEdge, .fence: 2
            }
        }
    }

    public enum Side: String, Codable, Sendable {
        case left
        case right
    }

    /// A wall end is a `limit` when something blocks the wall there, `unexplored` for a corner or
    /// no answer.
    public enum EndKind: String, Codable, Sendable {
        case limit
        case unexplored
    }

    public enum OpeningKind: Sendable {
        case door
        case window
        case garageDoor

        var kind: Kind {
            switch self {
            case .door: .door
            case .window: .window
            case .garageDoor: .garageDoor
            }
        }
    }

    public var id: String
    public var kind: Kind
    /// Meter frame, meters.
    public var points: [SIMD3<Float>]
    /// Uptime when the homeowner marked it. An inferred wall end has none (`inferred`).
    public var t: Double?
    public var photoIDs: [String]?
    public var side: Side?
    public var endKind: EndKind?
    /// `attrs.operable` of an opening; nil when the homeowner was not asked.
    public var operable: Bool?
    /// `attrs.inferred` of a wall end the app placed from the walk rather than one the homeowner
    /// marked (`WallEndSource`). Written only when true.
    public var inferred: Bool

    private init(
        id: String, kind: Kind, points: [SIMD3<Float>], t: Double?, photoIDs: [String]?,
        side: Side? = nil, endKind: EndKind? = nil, operable: Bool? = nil, inferred: Bool = false
    ) {
        self.id = id
        self.kind = kind
        self.points = points
        self.t = t
        self.photoIDs = photoIDs
        self.side = side
        self.endKind = endKind
        self.operable = operable
        self.inferred = inferred
    }

    /// The meter itself: the meter frame's origin.
    public static func meter(id: String, t: Double? = nil, photoIDs: [String]? = nil) -> PacketMark {
        PacketMark(id: id, kind: .meter, points: [.zero], t: t, photoIDs: photoIDs)
    }

    /// Where the wall ends, at `s` meters along `wall`'s chain. The point is on the wall face at
    /// the meter's height, as S4's synthetic fixture places wall ends. `stamp` says whether the
    /// homeowner marked it, with the time, or the app inferred it from the walk, with no time.
    public static func wallEnd(
        id: String, side: Side, endKind: EndKind, s: Float, wall: SceneWall, frame: MeterFrame, stamp: WallEndStamp
    ) -> PacketMark {
        let point = frame.point(on: wall, s: s, height: wall.meter.y - wall.groundY, out: 0)
        return PacketMark(
            id: id, kind: .wallEnd, points: [point], t: stamp.markedAt, photoIDs: nil, side: side, endKind: endKind,
            inferred: stamp.isInferred)
    }

    /// A door, window or garage door: the bottom-left and top-right corners on the wall face,
    /// left being the lower s. `span` is in s meters and `bottom`, `top` in meters above the
    /// ground, as scene.json's objects are.
    public static func opening(
        _ opening: OpeningKind, id: String, span: ClosedRange<Float>, bottom: Float, top: Float, operable: Bool?,
        wall: SceneWall, frame: MeterFrame, t: Double? = nil, photoIDs: [String]? = nil
    ) -> PacketMark {
        let corners = [
            frame.point(on: wall, s: span.lowerBound, height: bottom, out: 0),
            frame.point(on: wall, s: span.upperBound, height: top, out: 0),
        ]
        return PacketMark(id: id, kind: opening.kind, points: corners, t: t, photoIDs: photoIDs, operable: operable)
    }

    /// A gas meter or AC unit tapped once; `point` is already in the meter frame.
    public static func pointObject(
        _ kind: ScenePointObjectKind, id: String, point: SIMD3<Float>, t: Double? = nil, photoIDs: [String]? = nil
    ) -> PacketMark {
        PacketMark(id: id, kind: kind == .gasMeter ? .gasMeter : .ac, points: [point], t: t, photoIDs: photoIDs)
    }

    /// A fence's foot or a driveway's edge, two points already in the meter frame.
    public static func fence(id: String, from a: SIMD3<Float>, to b: SIMD3<Float>, t: Double? = nil, photoIDs: [String]? = nil) -> PacketMark {
        PacketMark(id: id, kind: .fence, points: [a, b], t: t, photoIDs: photoIDs)
    }

    public static func driveEdge(id: String, from a: SIMD3<Float>, to b: SIMD3<Float>, t: Double? = nil, photoIDs: [String]? = nil) -> PacketMark {
        PacketMark(id: id, kind: .driveEdge, points: [a, b], t: t, photoIDs: photoIDs)
    }

    /// The mark for one of scene.json's features, from the same inputs `SceneExport` reads.
    /// Point objects, fences and drive edges keep their taps (world points moved into the meter
    /// frame); openings get corners from their span and heights.
    public static func from(
        _ feature: SceneFeature, id: String, wall: SceneWall, frame: MeterFrame, t: Double? = nil, photoIDs: [String]? = nil
    ) throws(PacketError) -> PacketMark {
        switch feature {
        case let .opening(kind, span, bottom, top, operable):
            return opening(
                kind == .door ? .door : .window, id: id, span: span, bottom: bottom, top: top, operable: operable,
                wall: wall, frame: frame, t: t, photoIDs: photoIDs)
        case let .pointObject(kind, tap, _, _):
            return pointObject(kind, id: id, point: frame.point(tap), t: t, photoIDs: photoIDs)
        case let .fence(foot):
            guard foot.count == 2 else { throw .invalidMark(id: id, reason: "a fence has 2 points, got \(foot.count)") }
            return fence(id: id, from: frame.point(foot[0]), to: frame.point(foot[1]), t: t, photoIDs: photoIDs)
        case let .driveway(edge):
            guard edge.count == 2 else { throw .invalidMark(id: id, reason: "a drive edge has 2 points, got \(edge.count)") }
            return driveEdge(id: id, from: frame.point(edge[0]), to: frame.point(edge[1]), t: t, photoIDs: photoIDs)
        }
    }

    /// The problem the validator would report, if any.
    func problem() -> String? {
        if points.count != kind.pointCount { return "\(points.count) points, a \(kind.rawValue) has \(kind.pointCount)" }
        if !points.allSatisfy(PacketNumber.isFinite) { return "points must be finite" }
        if kind == .wallEnd, side == nil || endKind == nil { return "a wall end needs side and end_kind" }
        if inferred, kind != .wallEnd { return "only a wall end can be inferred" }
        if inferred, t != nil { return "an inferred wall end has no mark time: nobody marked it" }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, points, t, side, attrs
        case photoIDs = "photo_ids"
        case endKind = "end_kind"
    }

    private struct Attrs: Codable {
        var operable: Bool?
        var inferred: Bool?
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(Kind.self, forKey: .kind)
        points = try c.decode([[Float]].self, forKey: .points).map { values in
            guard values.count == 3 else {
                throw DecodingError.dataCorruptedError(forKey: .points, in: c, debugDescription: "a point has 3 numbers")
            }
            return SIMD3(values[0], values[1], values[2])
        }
        t = try c.decodeIfPresent(Double.self, forKey: .t)
        photoIDs = try c.decodeIfPresent([String].self, forKey: .photoIDs)
        side = try c.decodeIfPresent(Side.self, forKey: .side)
        endKind = try c.decodeIfPresent(EndKind.self, forKey: .endKind)
        let attrs = try c.decodeIfPresent(Attrs.self, forKey: .attrs)
        operable = attrs?.operable
        inferred = attrs?.inferred ?? false
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(kind, forKey: .kind)
        try c.encode(points.map { [$0.x, $0.y, $0.z].map(PacketNumber.double) }, forKey: .points)
        try c.encodeIfPresent(t, forKey: .t)
        try c.encodeIfPresent(photoIDs, forKey: .photoIDs)
        try c.encodeIfPresent(side, forKey: .side)
        try c.encodeIfPresent(endKind, forKey: .endKind)
        if operable != nil || inferred {
            try c.encode(Attrs(operable: operable, inferred: inferred ? true : nil), forKey: .attrs)
        }
    }
}

/// One request the homeowner was shown and whether it was met (packet/README.md, "Marks, guidance
/// and the scene"). The log tells the server what the homeowner could not reach.
public struct PacketGuidanceEntry: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable, CaseIterable {
        case walk
        case tiltToGround = "tilt_to_ground"
        case stepBack = "step_back"
        case markEnd = "mark_end"
        case closeup
        case gapBand = "gap_band"
        case gapPastEnd = "gap_past_end"
    }

    public enum Origin: String, Codable, Sendable {
        case phone
        case server
    }

    public enum Band: String, Codable, Sendable {
        case wall
        case ground
        case overhead
        case facing
    }

    public enum Outcome: String, Codable, Sendable, CaseIterable {
        case met
        case skipped
        case cannotReach = "cannot_reach"
        case superseded
        case unresolved
    }

    public var id: String
    public var kind: Kind
    public var origin: Origin
    /// What the homeowner read.
    public var message: String?
    public var band: Band?
    /// The stretch along the wall, meter frame x (scene s), meters.
    public var span: ClosedRange<Float>?
    public var tShown: Double
    /// Nil while unresolved; every other outcome needs it.
    public var tResolved: Double?
    public var outcome: Outcome

    public init(
        id: String, kind: Kind, origin: Origin, message: String?, band: Band? = nil, span: ClosedRange<Float>? = nil,
        tShown: Double, tResolved: Double?, outcome: Outcome
    ) {
        self.id = id
        self.kind = kind
        self.origin = origin
        self.message = message
        self.band = band
        self.span = span
        self.tShown = tShown
        self.tResolved = tResolved
        self.outcome = outcome
    }

    /// The problem the validator would report, if any.
    func problem() -> String? {
        if let tResolved {
            guard tResolved.isFinite else { return "t_resolved must be finite" }
            if tResolved < tShown { return "resolved at \(tResolved), before it was shown at \(tShown)" }
        } else if outcome != .unresolved {
            return "outcome \(outcome.rawValue) needs t_resolved"
        }
        if let span, !(span.lowerBound.isFinite && span.upperBound.isFinite) { return "span must be finite" }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, origin, message, band, outcome
        case span = "span_m"
        case tShown = "t_shown"
        case tResolved = "t_resolved"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(Kind.self, forKey: .kind)
        origin = try c.decode(Origin.self, forKey: .origin)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        band = try c.decodeIfPresent(Band.self, forKey: .band)
        if let values = try c.decodeIfPresent([Float].self, forKey: .span) {
            guard values.count == 2, values[0] <= values[1] else {
                throw DecodingError.dataCorruptedError(forKey: .span, in: c, debugDescription: "span_m is [low, high]")
            }
            span = values[0]...values[1]
        } else {
            span = nil
        }
        tShown = try c.decode(Double.self, forKey: .tShown)
        tResolved = try c.decodeIfPresent(Double.self, forKey: .tResolved)
        outcome = try c.decode(Outcome.self, forKey: .outcome)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(kind, forKey: .kind)
        try c.encode(origin, forKey: .origin)
        try c.encodeIfPresent(message, forKey: .message)
        try c.encodeIfPresent(band, forKey: .band)
        try c.encodeIfPresent(span.map { [$0.lowerBound, $0.upperBound].map(PacketNumber.double) }, forKey: .span)
        try c.encode(tShown, forKey: .tShown)
        try c.encodeIfPresent(tResolved, forKey: .tResolved)
        try c.encode(outcome, forKey: .outcome)
    }
}
