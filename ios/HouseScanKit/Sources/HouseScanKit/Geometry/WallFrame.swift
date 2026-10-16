import Foundation
import simd

/// A point in wall coordinates: meters along the wall from the meter (`s`, negative to the left
/// for someone facing the wall), above the ground (`height`) and out from the wall (`out`).
public struct WallPoint: Sendable, Equatable {
    public var s: Float
    public var height: Float
    public var out: Float

    public init(s: Float, height: Float, out: Float) {
        self.s = s
        self.height = height
        self.out = out
    }
}

/// How the line of a piece of wall was found: scene.json's `walls[].source`, which sets the
/// server's default error for the wall (tap 0.3 ft, mesh 0.5, plane 0.75, each plus its drift).
public enum WallLineSource: String, Sendable, Equatable {
    /// Through tapped points: the line's position and direction come from where taps landed.
    case tap
    /// Fitted to the LiDAR mesh.
    case mesh
    /// Taken from an ARKit detected plane (an ARPlaneAnchor): its direction and its distance
    /// from the camera are the plane's.
    case plane
}

/// A corner the walk followed. At `s` the wall turns; beyond it, away from the meter, it faces
/// `outward`.
public struct WallCorner: Sendable, Equatable {
    /// Meters along the chain from the meter: negative for a corner left of the meter.
    public var s: Float
    /// Unit, horizontal, from the wall past the corner toward the homeowner.
    public var outward: SIMD3<Float>
    /// How the line of the wall past the corner was found. `.tap` by default, which is also what
    /// scene.json means when a wall has no source.
    public var source: WallLineSource

    public init(s: Float, outward: SIMD3<Float>, source: WallLineSource = .tap) {
        self.s = s
        self.outward = outward
        self.source = source
    }
}

/// One straight piece of a wall chain.
public struct WallSegment: Sendable, Equatable {
    /// The stretch of s it covers, meters: infinite toward an open end of the chain. Neighbours
    /// share the corner's s as an edge.
    public let span: ClosedRange<Float>
    /// Unit, horizontal: the direction of +s along this piece.
    public let along: SIMD3<Float>
    /// Unit, horizontal, from the wall toward the homeowner.
    public let outward: SIMD3<Float>
    /// The point of the piece's line where s = `anchorS`, as a horizontal offset from the meter:
    /// zero for the meter's piece, the corner nearer the meter for the others.
    public let anchor: SIMD3<Float>
    public let anchorS: Float
    /// How this piece's line was found: the meter piece's `WallFrame.source`, or its corner's.
    public let source: WallLineSource

    /// s and out of a point given as its offset from the meter (or the meter's foot: only the
    /// horizontal part counts). s is not clamped to `span`.
    func coordinates(ofOffset d: SIMD3<Float>) -> (s: Float, out: Float) {
        let e = d - anchor
        return (anchorS + simd_dot(e, along), simd_dot(e, outward))
    }

    /// Plan distance from a point, given as its offset from the meter, to this piece within its span.
    func planDistance(toOffset d: SIMD3<Float>) -> Float {
        let s = coordinates(ofOffset: d).s
        let clamped = min(max(s, span.lowerBound), span.upperBound)
        let foot = anchor + along * (clamped - anchorS)
        return simd_length(SIMD2(d.x - foot.x, d.z - foot.z))
    }

    /// The pieces of a chain, left to right, and the index of the meter's piece. The meter's
    /// piece faces `outward`; each corner starts (right side) or ends (left side) a new piece
    /// facing the corner's outward, whose line passes through the previous piece's point at the
    /// corner's s, so the chain is continuous and s is distance along it. `source` is the meter
    /// piece's line source; the others carry their corner's.
    static func chain(
        outward: SIMD3<Float>, source: WallLineSource, left: [WallCorner], right: [WallCorner]
    ) -> (segments: [WallSegment], meter: Int) {
        func along(_ outward: SIMD3<Float>) -> SIMD3<Float> { simd_normalize(simd_cross(-outward, WallFrame.up)) }
        let meterPiece = WallSegment(
            span: (left.first?.s ?? -.infinity)...(right.first?.s ?? .infinity),
            along: along(outward), outward: outward, anchor: .zero, anchorS: 0, source: source)
        func pieces(_ corners: [WallCorner], rightward: Bool) -> [WallSegment] {
            var previous = meterPiece
            var result: [WallSegment] = []
            for (index, corner) in corners.enumerated() {
                let far = index + 1 < corners.count ? corners[index + 1].s : (rightward ? .infinity : -.infinity)
                let piece = WallSegment(
                    span: rightward ? corner.s...far : far...corner.s,
                    along: along(corner.outward), outward: corner.outward,
                    anchor: previous.anchor + previous.along * (corner.s - previous.anchorS), anchorS: corner.s,
                    source: corner.source)
                result.append(piece)
                previous = piece
            }
            return result
        }
        let leftPieces = pieces(left, rightward: false)
        return (leftPieces.reversed() + [meterPiece] + pieces(right, rightward: true), leftPieces.count)
    }

    /// The piece whose span holds `s`; a corner's own s belongs to the piece on its left. Past the
    /// chain (only for a NaN s) the end piece on that side.
    static func index(in segments: [WallSegment], atS s: Float) -> Int {
        if segments.count == 1 { return 0 }
        return segments.firstIndex { $0.span.contains(s) } ?? (s < 0 ? 0 : segments.count - 1)
    }

    /// The piece nearest a point in plan, given as its offset from the meter; ties go to the
    /// leftmost.
    static func nearest(in segments: [WallSegment], toOffset d: SIMD3<Float>) -> Int {
        if segments.count == 1 { return 0 }
        var best = 0
        var bestDistance = Float.infinity
        for (index, segment) in segments.enumerated() {
            let distance = segment.planDistance(toOffset: d)
            if distance < bestDistance {
                best = index
                bestDistance = distance
            }
        }
        return best
    }
}

/// Why a marked wall can't be the next wall round a corner.
public enum CornerRefusal: Error, Sendable, Equatable {
    /// The marked surface's normal has no horizontal part: a floor or ceiling, not a wall.
    case notAWall
    /// The marked wall runs within `WallFrame.minCornerAngle` of the current one.
    case nearlyParallel
    /// The two walls' lines meet at `s`, where the walk can't have turned: behind the meter or
    /// the previous corner, with the marked wall on the wrong side of the corner, or farther than
    /// `CoverageMap.maxCornerFromEnd` from where the wall was marked as ending.
    case implausible(s: Float)
}

/// The wall the scan measures, anchored on the electric meter: one straight piece, plus one
/// more for each corner the walk followed. s runs on continuously round a corner.
///
/// `outward` is horizontal and points from the wall toward the homeowner. `along` is
/// cross(-outward, up): facing the wall you look along -outward, so right is cross(forward, up).
/// Both describe the meter's piece; `segments` has every piece.
public struct WallFrame: Sendable, Equatable {
    public static let up = SIMD3<Float>(0, 1, 0)
    /// A marked wall within 30 degrees of the current one is refused as the next wall. A guess,
    /// not measured: house corners are nearly always about 90 degrees, and a wall that shallow is
    /// more likely the same wall, or a fence seen at a slant, than the wall round the corner.
    public static let minCornerAngle: Float = 30 * .pi / 180
    /// A corner must lie at least this far along from the meter or the previous corner. A guess:
    /// 0.15 m (6 in, one coverage cell) is within tap error of there being no piece at all.
    public static let minSegmentLength: Float = 0.15
    /// D7 option A: any positive wall length may be sent; placement rules belong to the
    /// server. Coincident or reversed ends still describe no wall.
    public static func hasPositiveLength(left: Float, right: Float) -> Bool { right > left }

    /// The meter, on the wall face.
    public var meter: SIMD3<Float>
    public private(set) var outward: SIMD3<Float>
    /// World y of the ground at the foot of the wall.
    public var groundY: Float
    /// Corners the walk followed, nearest the meter first. Empty on a straight wall.
    public private(set) var leftCorners: [WallCorner] = []
    public private(set) var rightCorners: [WallCorner] = []
    /// The straight pieces, left to right as seen from outside. Their anchors are offsets from the
    /// meter, so moving the meter or the ground moves the whole chain with it.
    public private(set) var segments: [WallSegment]
    /// Index of the meter's piece in `segments`.
    public private(set) var meterSegmentIndex: Int
    /// How the meter piece's line was found. `.tap`, scene.json's reading of no source, until the
    /// capture says otherwise; the pieces past corners carry their own (`WallCorner.source`).
    public var source: WallLineSource = .tap {
        didSet { rebuildChain() }
    }

    /// Returns nil when `outward` has no horizontal component to speak of (a floor or ceiling hit).
    public init?(meter: SIMD3<Float>, outward: SIMD3<Float>, groundY: Float) {
        let flat = SIMD3(outward.x, 0, outward.z)
        guard simd_length(flat) > 1e-3 else { return nil }
        self.meter = meter
        self.outward = simd_normalize(flat)
        self.groundY = groundY
        (segments, meterSegmentIndex) = WallSegment.chain(outward: self.outward, source: source, left: [], right: [])
    }

    private mutating func rebuildChain() {
        (segments, meterSegmentIndex) = WallSegment.chain(outward: outward, source: source, left: leftCorners, right: rightCorners)
    }

    public var along: SIMD3<Float> { simd_normalize(simd_cross(-outward, Self.up)) }

    /// The meter's foot on the ground: the origin of wall coordinates.
    public var origin: SIMD3<Float> { SIMD3(meter.x, groundY, meter.z) }

    public var meterHeight: Float { meter.y - groundY }

    /// The piece holding `s` (a corner's own s belongs to the piece on its left).
    public func segment(atS s: Float) -> WallSegment {
        segments[WallSegment.index(in: segments, atS: s)]
    }

    public func world(_ p: WallPoint) -> SIMD3<Float> {
        let piece = segment(atS: p.s)
        return origin + piece.anchor + piece.along * (p.s - piece.anchorS) + piece.outward * p.out + Self.up * p.height
    }

    public func world(s: Float, height: Float, out: Float = 0) -> SIMD3<Float> {
        world(WallPoint(s: s, height: height, out: out))
    }

    /// Wall coordinates on the piece nearest the point in plan. s is clamped to that piece, so a
    /// point outside a corner (in front of neither piece) maps to the corner's s; out is its
    /// distance in front of that piece's line.
    public func wallPoint(_ world: SIMD3<Float>) -> WallPoint {
        let d = world - origin
        let piece = segments[WallSegment.nearest(in: segments, toOffset: d)]
        let local = piece.coordinates(ofOffset: d)
        return WallPoint(s: min(max(local.s, piece.span.lowerBound), piece.span.upperBound), height: d.y, out: local.out)
    }

    /// How far a point is in front of the line of the piece holding `s`, meters.
    func out(of world: SIMD3<Float>, pieceAtS s: Float) -> Float {
        segment(atS: s).coordinates(ofOffset: world - origin).out
    }

    /// Where a ray first meets a piece's face within that piece's span, or nil when it misses
    /// every piece (parallel, behind, or off the ends of the pieces it would meet).
    public func intersectWall(_ ray: Ray) -> WallPoint? {
        // Rays through a corner land within Float rounding of both pieces' shared edge.
        let tolerance: Float = 1e-4
        var best: (t: Float, point: WallPoint)?
        for piece in segments {
            guard let t = ray.intersect(planePoint: meter + piece.anchor, normal: piece.outward), t < best?.t ?? .infinity else { continue }
            let d = ray.at(t) - origin
            let local = piece.coordinates(ofOffset: d)
            guard local.s >= piece.span.lowerBound - tolerance, local.s <= piece.span.upperBound + tolerance else { continue }
            best = (t, WallPoint(s: min(max(local.s, piece.span.lowerBound), piece.span.upperBound), height: d.y, out: local.out))
        }
        return best?.point
    }

    /// Where a ray meets the ground plane, or nil when it misses.
    public func intersectGround(_ ray: Ray) -> WallPoint? {
        guard let t = ray.intersect(planePoint: origin, normal: Self.up) else { return nil }
        return wallPoint(ray.at(t))
    }

    // MARK: Corners

    /// The corner where the wall through `point`, facing `outward` (toward the homeowner), meets
    /// the line of the last piece on `side`, on the ground. Nothing changes until `turn(_:at:)`.
    public func corner(on side: WalkSide, meeting point: SIMD3<Float>, outward: SIMD3<Float>) throws(CornerRefusal) -> WallCorner {
        let flat = SIMD3(outward.x, 0, outward.z)
        guard simd_length(flat) > 1e-3 else { throw .notAWall }
        let newOutward = simd_normalize(flat)
        let newAlong = simd_normalize(simd_cross(-newOutward, Self.up))
        let last = segments[side == .left ? 0 : segments.count - 1]
        // The plan cross product of the two directions: the sine of the angle between the walls.
        let sine = last.along.x * newAlong.z - last.along.z * newAlong.x
        guard abs(sine) >= sin(Self.minCornerAngle) else { throw .nearlyParallel }
        // start + last.along * t = foot + newAlong * u, solved for t in plan.
        let start = origin + last.anchor
        let foot = SIMD3(point.x, groundY, point.z)
        let d = foot - start
        let t = (d.x * newAlong.z - d.z * newAlong.x) / sine
        let s = last.anchorS + t
        let cornerPoint = start + last.along * t
        // Past the corner the new wall runs on away from the meter, so the marked point lies that way.
        let pastCorner = side.sign * simd_dot(foot - cornerPoint, newAlong)
        let previous = (side == .left ? leftCorners.last : rightCorners.last)?.s ?? 0
        guard pastCorner > 0, side.sign * (s - previous) >= Self.minSegmentLength else { throw .implausible(s: s) }
        return WallCorner(s: s, outward: newOutward)
    }

    /// Follows the wall round `corner` on `side`: the last piece there now ends at the corner, and
    /// a new piece facing `corner.outward` runs on from it to the open end.
    public mutating func turn(_ side: WalkSide, at corner: WallCorner) {
        let previous = (side == .left ? leftCorners.last : rightCorners.last)?.s ?? 0
        precondition(side.sign * (corner.s - previous) > 0, "a \(side.rawValue) corner at s = \(corner.s) is not beyond s = \(previous)")
        precondition(abs(simd_length(corner.outward) - 1) < 1e-3 && abs(corner.outward.y) < 1e-3, "corner outward \(corner.outward) is not unit horizontal")
        switch side {
        case .left: leftCorners.append(corner)
        case .right: rightCorners.append(corner)
        }
        rebuildChain()
    }

    /// Moves every corner by `delta` meters of s, as the marked ends move when the meter does
    /// (`CoverageMap.updateWall`): a corner keeps its place in the world.
    mutating func shiftCorners(by delta: Float) {
        guard !leftCorners.isEmpty || !rightCorners.isEmpty else { return }
        for index in leftCorners.indices { leftCorners[index].s += delta }
        for index in rightCorners.indices { rightCorners[index].s += delta }
        rebuildChain()
    }

    /// Turns the meter's piece and every corner's piece about +y, corners keeping their s
    /// (`apply(_:)`).
    mutating func turn(by correction: YawCorrection) {
        func turned(_ v: SIMD3<Float>) -> SIMD3<Float> {
            let d = correction.direction(v)
            return simd_normalize(SIMD3(d.x, 0, d.z))
        }
        outward = turned(outward)
        for index in leftCorners.indices { leftCorners[index].outward = turned(leftCorners[index].outward) }
        for index in rightCorners.indices { rightCorners[index].outward = turned(rightCorners[index].outward) }
        rebuildChain()
    }

    /// Whether `other` runs the same way: the same outward, within Float noise.
    func hasSameAxes(as other: WallFrame) -> Bool {
        simd_distance(outward, other.outward) < 1e-4
    }
}
