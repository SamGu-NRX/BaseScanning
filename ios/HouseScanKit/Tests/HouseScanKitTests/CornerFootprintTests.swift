import Foundation
import HouseScanKit
import Testing
import simd

/// The assumed square of a tapped AC unit or gas meter near a corner. The square used to be drawn
/// by mapping each vertex through the chain, so vertices past the corner landed on the next piece.
/// At an inside corner the edges then crossed, and the server refused the scene with HTTP 422
/// `invalid_scene` ("polygon is not simple"). Each case here checks that the exported footprint is
/// a simple rectangle of the full assumed size, standing on the tapped piece and centred on the tap,
/// and that its span covers every stretch of wall the square meets. The server checks the cable
/// route only against objects whose span overlaps the route (`server/solver.py`, `route_objects`).
///
/// To check the exported scenes against the server's own `parse_scene`, which this package cannot
/// run, write them out and parse them there:
///
///     HOUSESCAN_CORNER_SCENES_OUT=/tmp/hs-corner-scenes swift test --package-path ios/HouseScanKit \
///         --filter CornerFootprintTests
///
/// The folder is created if missing. Each case writes `<name>.json`, replacing a file of that name
/// and leaving other files alone, so start from an empty folder to keep runs apart.
@Suite struct CornerFootprintTests {
    typealias Value = JSONSchemaValidator.Value

    enum Turn { case inside, outside }

    struct Case: CustomTestStringConvertible, Sendable {
        let name: String
        let kind: ScenePointObjectKind
        /// Corner s, meters: positive puts it right of the meter, negative left.
        let cornerS: Float
        let turn: Turn
        let tapS: Float
        /// Whether the whole square lies on one piece, where the export must not have changed.
        let onOnePiece: Bool
        /// The chain stretch the square meets, meters, worked out by hand.
        let span: ClosedRange<Float>
        var testDescription: String { name }
    }

    /// Corners 2 m from a meter on a wall facing +z, the geometry of the review on #131
    /// (discussion r4114590692). The first two cases are the ones the server refused. The taps on
    /// the far piece and the left corner run the square through a piece whose frame is not the
    /// meter's. A gas meter tapped just round a corner 0.2 m from the meter stands across the
    /// meter's wall: its span must reach back past the meter, or a route leaving the meter is
    /// never checked against it.
    static let cases: [Case] = [
        // Square s 1.2428...2.1572 m; it crosses the next wall over its first 0.9144 m.
        Case(name: "ac-1.7-inside-right", kind: .ac, cornerS: 2, turn: .inside, tapS: 1.7, onOnePiece: false, span: 1.2428...2.9144),
        Case(name: "gas-1.9-inside-right", kind: .gasMeter, cornerS: 2, turn: .inside, tapS: 1.9, onOnePiece: false, span: 1.75...2.3),
        // On the next wall at s 1.8428...2.7572 m; it crosses the meter's wall back to x = 1.0856 m.
        Case(name: "ac-2.3-inside-right-far-piece", kind: .ac, cornerS: 2, turn: .inside, tapS: 2.3, onOnePiece: false, span: 1.0856...2.7572),
        Case(name: "ac-1.7-inside-left", kind: .ac, cornerS: -2, turn: .inside, tapS: -1.7, onOnePiece: false, span: -2.9144 ... -1.2428),
        // Past an outside corner the square meets the next wall only at the corner.
        Case(name: "ac-1.7-outside-right", kind: .ac, cornerS: 2, turn: .outside, tapS: 1.7, onOnePiece: false, span: 1.2428...2.1572),
        Case(name: "gas-0.25-inside-right-far-piece", kind: .gasMeter, cornerS: 0.2, turn: .inside, tapS: 0.25, onOnePiece: false, span: -0.1...0.4),
        // The square's side lies exactly along the meter's wall, which Float arithmetic leaves
        // about 1e-8 m off the line.
        Case(name: "gas-0.36-inside-right-flush", kind: .gasMeter, cornerS: 0.21, turn: .inside, tapS: 0.36, onOnePiece: false, span: -0.09...0.51),
        Case(name: "ac-1.0-inside-right", kind: .ac, cornerS: 2, turn: .inside, tapS: 1.0, onOnePiece: true, span: 0.5428...1.4572),
        Case(name: "gas-1.5-inside-right", kind: .gasMeter, cornerS: 2, turn: .inside, tapS: 1.5, onOnePiece: true, span: 1.35...1.65),
    ]

    /// The meter's piece runs along +x and faces +z. Past an inside corner the wall comes toward
    /// the homeowner; past an outside corner it turns away.
    static func wall(_ c: Case) -> SceneWall {
        let right = c.cornerS > 0
        let outward: SIMD3<Float> = switch (right, c.turn) {
        case (true, .inside), (false, .outside): SIMD3(-1, 0, 0)
        case (true, .outside), (false, .inside): SIMD3(1, 0, 0)
        }
        let corner = WallCorner(s: c.cornerS, outward: outward)
        return SceneWall(
            meter: SIMD3(0, 1.2, 0), outward: SIMD3(0, 0, 1), groundY: 0,
            leftCorners: right ? [] : [corner], rightCorners: right ? [corner] : [])
    }

    static func input(_ c: Case) -> SceneInput {
        let wall = wall(c)
        // Tapped on the wall face. The export keeps only the tap's s, so its height and distance
        // out do not move the square.
        let tap = wall.world(s: c.tapS, height: c.kind == .ac ? 0 : 0.8, out: 0)
        return SceneInput(
            wall: wall, wallID: "side", baselineS: -4...4, wallHeight: 2.7,
            features: [.pointObject(kind: c.kind, tap: tap, bottom: nil, top: nil)],
            coverage: SceneCoverage(
                leftEndMarked: false, rightEndMarked: false,
                wall: [ObservedSpan(span: -4...4, out: 2.286)], ground: [ObservedSpan(span: -4...4, out: 2)]))
    }

    /// The assumed width along the wall and depth out from it, meters.
    static func size(_ kind: ScenePointObjectKind) -> (width: Double, depth: Double) {
        switch kind {
        case .ac: (Double(SceneExport.acAssumedSide), Double(SceneExport.acAssumedSide))
        case .gasMeter: (0.3, 0.3)
        }
    }

    static let feet = SceneUnits.feetPerMeter

    /// The exported JSON is rounded to 4 decimals of a foot.
    static let tolerance = 2e-4

    @Test(arguments: cases)
    func footprintIsTheFullSquareOnTheTappedPiece(_ c: Case) throws {
        let data = try SceneExport.jsonData(Self.input(c))
        try Self.keep(data, as: c.name)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let object = try #require(try Value.parse(data)["objects"]?[0])
        #expect(object["type"] == .string(c.kind.rawValue))
        let points = (object["footprint"]?.array ?? []).compactMap(\.numbers)
        try #require(points.count == 4 && points.allSatisfy { $0.count == 2 })
        let p = points.map { SIMD2($0[0], $0[1]) }

        #expect(Self.isSimple(p), "footprint \(p) crosses itself")

        // Full size: back and front edges one width long, sides one depth long, square corners,
        // and the area of the whole assumed square. Nothing is shrunk, clipped or clamped.
        let (width, depth) = Self.size(c.kind)
        for (a, b) in [(0, 1), (3, 2)] { #expect(abs(simd_distance(p[a], p[b]) - width * Self.feet) < Self.tolerance, "edge \(a)-\(b)") }
        for (a, b) in [(0, 3), (1, 2)] { #expect(abs(simd_distance(p[a], p[b]) - depth * Self.feet) < Self.tolerance, "edge \(a)-\(b)") }
        #expect(abs(simd_dot(simd_normalize(p[1] - p[0]), simd_normalize(p[3] - p[0]))) < 1e-4)
        #expect(abs(Self.area(p) - width * depth * Self.feet * Self.feet) < 1e-3)

        // Standing on the tapped piece: the back edge centred on the tap, on that piece's line,
        // and the front edge one depth out toward the homeowner.
        let wall = Self.wall(c)
        let piece = Self.pieceFrame(wall, atS: c.tapS)
        let tap = Self.plan(wall.world(s: c.tapS, height: 0, out: 0))
        #expect(simd_distance((p[0] + p[1]) / 2, tap) < Self.tolerance)
        for (index, out) in [(0, 0.0), (1, 0.0), (2, depth), (3, depth)] {
            let d = p[index] - tap
            #expect(abs(simd_dot(d, piece.outward) - out * Self.feet) < Self.tolerance, "vertex \(index)")
        }
        #expect(simd_dot(p[1] - p[0], piece.along) > 0, "back edge runs toward +s")

        // Away from a corner the square is what mapping each vertex through the chain gave.
        if c.onOnePiece {
            let half = Float(width / 2)
            let corners: [(s: Float, out: Float)] = [(c.tapS - half, 0), (c.tapS + half, 0), (c.tapS + half, Float(depth)), (c.tapS - half, Float(depth))]
            let chained = corners.map { Self.plan(wall.world(s: $0.s, height: 0, out: $0.out)) }
            for (a, b) in zip(p, chained) { #expect(simd_distance(a, b) < Self.tolerance) }
        }

        // The span: the hand-worked stretch, and, checked by walking the wall chain every 5 mm,
        // every wall point inside the square lies within it.
        let span = try #require(object["span_ft"]?.numbers)
        try #require(span.count == 2)
        #expect(abs(span[0] - Double(c.span.lowerBound) * Self.feet) < Self.tolerance, "span \(span)")
        #expect(abs(span[1] - Double(c.span.upperBound) * Self.feet) < Self.tolerance, "span \(span)")
        for step in -800...800 {
            let s = Float(step) * 0.005
            guard Self.distance(Self.plan(wall.world(s: s, height: 0, out: 0)), toConvex: p) < 1e-4 else { continue }
            #expect(span[0] - Self.tolerance <= Double(s) * Self.feet && Double(s) * Self.feet <= span[1] + Self.tolerance,
                    "wall at s = \(s) m is inside the footprint but outside span \(span)")
        }
    }

    /// Plan distance from a point to a convex polygon, zero inside it.
    static func distance(_ q: SIMD2<Double>, toConvex p: [SIMD2<Double>]) -> Double {
        let edges = p.indices.map { (p[$0], p[($0 + 1) % p.count]) }
        let sides = edges.map { e in (e.1.x - e.0.x) * (q.y - e.0.y) - (e.1.y - e.0.y) * (q.x - e.0.x) }
        if sides.allSatisfy({ $0 >= 0 }) || sides.allSatisfy({ $0 <= 0 }) { return 0 }
        return edges.map { e in
            let t = min(max(simd_dot(q - e.0, e.1 - e.0) / simd_length_squared(e.1 - e.0), 0), 1)
            return simd_distance(q, e.0 + (e.1 - e.0) * t)
        }.min() ?? .infinity
    }

    /// Plan [x, z] in feet of a world point, as the export writes it.
    static func plan(_ world: SIMD3<Float>) -> SIMD2<Double> {
        SIMD2(Double(world.x), Double(world.z)) * feet
    }

    /// The +s and outward directions in plan of the piece holding `s`, found from the chain's own
    /// points so the test does not reuse the export's piece lookup.
    static func pieceFrame(_ wall: SceneWall, atS s: Float) -> (along: SIMD2<Double>, outward: SIMD2<Double>) {
        let a = plan(wall.world(s: s - 0.01, height: 0, out: 0))
        let b = plan(wall.world(s: s + 0.01, height: 0, out: 0))
        let o = plan(wall.world(s: s, height: 0, out: 1))
        return (simd_normalize(b - a), simd_normalize(o - plan(wall.world(s: s, height: 0, out: 0))))
    }

    /// Shoelace area, square feet.
    static func area(_ p: [SIMD2<Double>]) -> Double {
        abs(p.indices.reduce(0) { sum, i in
            let q = p[(i + 1) % p.count]
            return sum + p[i].x * q.y - q.x * p[i].y
        }) / 2
    }

    /// True when no two edges of the closed ring meet except adjacent edges at their shared
    /// vertex, and no edge has zero length: shapely's test for a valid polygon, which the server
    /// applies (`server/scene.py` `_geometry`).
    static func isSimple(_ p: [SIMD2<Double>]) -> Bool {
        let n = p.count
        let edges = (0..<n).map { (p[$0], p[($0 + 1) % n]) }
        guard edges.allSatisfy({ simd_distance($0.0, $0.1) > 1e-9 }) else { return false }
        func cross(_ o: SIMD2<Double>, _ a: SIMD2<Double>, _ b: SIMD2<Double>) -> Double {
            (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
        }
        func onSegment(_ q: SIMD2<Double>, _ a: SIMD2<Double>, _ b: SIMD2<Double>) -> Bool {
            abs(cross(a, b, q)) < 1e-12 && min(a.x, b.x) <= q.x && q.x <= max(a.x, b.x) && min(a.y, b.y) <= q.y && q.y <= max(a.y, b.y)
        }
        func meet(_ e: (SIMD2<Double>, SIMD2<Double>), _ f: (SIMD2<Double>, SIMD2<Double>)) -> Bool {
            let d1 = cross(f.0, f.1, e.0), d2 = cross(f.0, f.1, e.1)
            let d3 = cross(e.0, e.1, f.0), d4 = cross(e.0, e.1, f.1)
            if (d1 > 0) != (d2 > 0), (d3 > 0) != (d4 > 0), d1 != 0, d2 != 0, d3 != 0, d4 != 0 { return true }
            return onSegment(e.0, f.0, f.1) || onSegment(e.1, f.0, f.1) || onSegment(f.0, e.0, e.1) || onSegment(f.1, e.0, e.1)
        }
        for i in 0..<n {
            for j in (i + 1)..<n {
                let adjacent = j == i + 1 || (i == 0 && j == n - 1)
                if adjacent {
                    // Adjacent edges share one vertex; they must not fold back over each other.
                    let (e, f) = j == i + 1 ? (edges[i], edges[j]) : (edges[j], edges[i])
                    if onSegment(f.1, e.0, e.1) || onSegment(e.0, f.0, f.1) { return false }
                } else if meet(edges[i], edges[j]) {
                    return false
                }
            }
        }
        return true
    }

    /// Writes the scene to HOUSESCAN_CORNER_SCENES_OUT when it is set.
    static func keep(_ data: Data, as name: String) throws {
        guard let path = ProcessInfo.processInfo.environment["HOUSESCAN_CORNER_SCENES_OUT"], !path.isEmpty else { return }
        let folder = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try data.write(to: folder.appendingPathComponent("\(name).json"))
    }

    /// The simplicity check itself, on the polygon the old export wrote for the AC at 1.7 m inside
    /// a corner at 2 m (the reproduction's vertices, meters), and on a plain square.
    @Test func simplicityCheckRefusesTheOldCrossedSquare() {
        let old: [SIMD2<Double>] = [SIMD2(1.2428, 0), SIMD2(2, 0.1572), SIMD2(1.0856, 0.1572), SIMD2(1.2428, 0.9144)]
        #expect(!Self.isSimple(old))
        #expect(Self.isSimple([SIMD2(0, 0), SIMD2(1, 0), SIMD2(1, 1), SIMD2(0, 1)]))
        #expect(!Self.isSimple([SIMD2(0, 0), SIMD2(1, 1), SIMD2(1, 0), SIMD2(0, 1)]))
    }
}
