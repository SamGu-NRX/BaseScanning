import Foundation
@testable import HouseScanKit
import Testing
import simd

@Suite struct ACUnitGeometryTests {
    @Test(arguments: [Float(0), 0.2, 0.5, 2.5, 8])
    func exportAndBothRenderersUseTheMeasuredCenter(out: Float) throws {
        let wall = SceneExportTests.wall
        let tap = wall.world(s: 1, height: 0, out: out)
        let ac = ACUnitGeometry(tap: tap, along: wall.along, outward: wall.outward, groundY: wall.groundY)
        var input = SceneExportTests.input()
        input.features = [.pointObject(kind: .ac, tap: tap, bottom: nil, top: nil)]
        let json = try SceneExportTests.Value.parse(SceneExport.jsonData(input))
        let polygon = try #require(json["objects"]?[0]?["footprint"]?.array).compactMap(\.numbers)
        try #require(polygon.count == 4)
        let centroid = ac.footprint.reduce(SIMD3<Float>.zero, +) / 4
        #expect(simd_distance(centroid, tap) < 1e-5)
        #expect(abs(ac.size.x - 0.9144) < 1e-6)
        #expect(abs(ac.size.z - 0.9144) < 1e-6)

        // RealityKit's box uses this center and these dimensions. Its four bottom vertices
        // must be the AR wire box's base and the polygon sent to the server's distance check.
        let rotation = simd_float3x3(columns: (ac.along, SIMD3(0, 1, 0), ac.outward))
        let signs: [SIMD3<Float>] = [SIMD3(-1, -1, -1), SIMD3(1, -1, -1),
                                    SIMD3(1, -1, 1), SIMD3(-1, -1, 1)]
        for i in 0..<4 {
            let rendered = ac.boxCenter + rotation * (signs[i] * ac.size / 2)
            #expect(simd_distance(rendered, ac.footprint[i]) < 1e-5)
            #expect(abs(ac.top[i].y - wall.groundY - ac.size.y) < 1e-5)
            try #require(polygon[i].count == 2)
            #expect(abs(polygon[i][0] - Double(rendered.x) * SceneUnits.feetPerMeter) < 0.0002)
            #expect(abs(polygon[i][1] - Double(rendered.z) * SceneUnits.feetPerMeter) < 0.0002)
        }
        // A nearby tap keeps its center even when half the assumed square lies behind the wall.
        let backOut = simd_dot(ac.footprint[0] - wall.world(s: 1, height: 0, out: 0), wall.outward)
        #expect(abs(backOut - (out - 0.4572)) < 1e-5)
    }

    @Test func clearanceInputMovesWithTheOffsetInsteadOfStayingAtTheWall() throws {
        let wall = SceneWall(meter: SIMD3(0, 1.2, 0), outward: SIMD3(0, 0, 1), groundY: 0)
        var input = SceneExportTests.input()
        input.wall = wall
        for out in [Float(0.5), 2.5] {
            let tap = wall.world(s: 1, height: 0, out: out)
            input.features = [.pointObject(kind: .ac, tap: tap, bottom: nil, top: nil)]
            let json = try SceneExportTests.Value.parse(SceneExport.jsonData(input))
            let vertices = try #require(json["objects"]?[0]?["footprint"]?.array).compactMap(\.numbers)
            try #require(vertices.count == 4)
            let exported = vertices.map { SIMD2(Float($0[0] / SceneUnits.feetPerMeter), Float($0[1] / SceneUnits.feetPerMeter)) }
            let ac = ACUnitGeometry(tap: tap, along: wall.along, outward: wall.outward, groundY: 0)
            let drawn = ac.footprint.map { SIMD2($0.x, $0.z) }
            // Synthetic battery front, 0.6 m from the wall, overlapping the AC's along-wall span.
            let front = (SIMD2<Float>(0.5, 0.6), SIMD2<Float>(1.5, 0.6))
            let fromExport = GroundPlaneChoice.distance(from: exported, to: front)
            let fromDrawing = GroundPlaneChoice.distance(from: drawn, to: front)
            let expected: Float = out == 0.5 ? 0 : 1.4428
            #expect(abs(fromExport - expected) < 0.0001)
            #expect(abs(fromDrawing - expected) < 0.0001)
        }
    }

    @Test(arguments: [Float(1.7), 2, 3])
    func cornerFootprintsStaySquareAndCentered(s: Float) throws {
        let wall = SceneWall(meter: SIMD3(4, 1.2, -3), outward: SIMD3(0, 0, 1), groundY: -0.4,
                             rightCorners: [WallCorner(s: 2, outward: SIMD3(1, 0, 0))])
        let tap = wall.world(s: s, height: 0, out: 0.3)
        let coordinate = wall.wallCoordinates(of: tap)
        let segment = try #require(wall.chain.segments.first { $0.span.contains(coordinate.s) })
        let ac = ACUnitGeometry(tap: tap, along: segment.along, outward: segment.outward, groundY: wall.groundY)
        var input = SceneExportTests.input()
        input.wall = wall
        input.features = [.pointObject(kind: .ac, tap: tap, bottom: nil, top: nil)]
        let json = try SceneExportTests.Value.parse(SceneExport.jsonData(input))
        let polygon = try #require(json["objects"]?[0]?["footprint"]?.array).compactMap(\.numbers)
        try #require(polygon.count == 4)
        for i in 0..<4 {
            #expect(abs(simd_distance(ac.footprint[i], ac.footprint[(i + 1) % 4]) - 0.9144) < 1e-5)
            try #require(polygon[i].count == 2)
            #expect(abs(polygon[i][0] - Double(ac.footprint[i].x) * SceneUnits.feetPerMeter) < 0.0002)
            #expect(abs(polygon[i][1] - Double(ac.footprint[i].z) * SceneUnits.feetPerMeter) < 0.0002)
        }
        #expect(simd_distance(ac.footprint.reduce(SIMD3<Float>.zero, +) / 4, tap) < 1e-5)
    }
}
