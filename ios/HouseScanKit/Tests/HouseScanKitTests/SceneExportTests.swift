import Foundation
import HouseScanKit
import Testing
import simd

@Suite struct SceneExportTests {
    typealias Value = JSONSchemaValidator.Value

    /// Wall facing (0.6, 0, 0.8): not axis aligned, so any swapped x/z or sign error shows.
    static let wall = SceneWall(meter: SIMD3(1.0, 1.2, -2.0), outward: SIMD3(0.6, 0, 0.8), groundY: -0.3)

    /// Camera turned 30 degrees about +y, standing at (1, 1.5, 2) m.
    static let pose: simd_float4x4 = {
        var m = simd_float4x4(simd_quatf(angle: .pi / 6, axis: SIMD3(0, 1, 0)))
        m.columns.3 = SIMD4(1, 1.5, 2, 1)
        return m
    }()

    static func input() -> SceneInput {
        let w = wall
        return SceneInput(
            wall: w, wallID: "side", baselineS: -3...5, wallHeight: 2.7, meterPlusMinus: 0.1,
            features: [
                .opening(kind: .door, span: 0.5...1.4, bottom: 0, top: 2.0, operable: nil),
                .opening(kind: .window, span: -2.0 ... -1.0, bottom: 0.9, top: 2.1, operable: true),
                .pointObject(kind: .gasMeter, tap: w.world(s: -2.5, height: 0.8, out: 0.25), bottom: 0.3, top: 1.0),
                .pointObject(kind: .ac, tap: w.world(s: 3.0, height: 0, out: 0.5), bottom: nil, top: nil),
                .fence(foot: [w.world(s: -1, height: 0, out: 2.0), w.world(s: 2, height: 0, out: 2.4)]),
                .driveway(edge: [w.world(s: 4, height: 0, out: 1), w.world(s: 5, height: 0, out: 1)]),
            ],
            coverage: SceneCoverage(leftEndMarked: true, rightEndMarked: false, wall: [ObservedSpan(span: -3...5, out: 2.286)], ground: [ObservedSpan(span: -3...4, out: 3)]),
            keyframes: [
                SceneKeyframe(id: "k1", cameraToWorld: pose, intrinsics: SIMD4(1450, 1450, 960, 720), w: 1920, h: 1440, img: "k1.jpg"),
                SceneKeyframe(id: "k2", cameraToWorld: matrix_identity_float4x4, intrinsics: SIMD4(1450, 1450, 960, 720), w: 1920, h: 1440, img: "k2.jpg"),
            ],
            stills: ["meter_close": "meter_close.jpg"])
    }

    static func exported() throws -> (data: Data, value: Value) {
        let data = try SceneExport.jsonData(input())
        return (data, try Value.parse(data))
    }

    private func expectClose(_ actual: [Double]?, _ expected: [Double], tolerance: Double = 2e-4,
                             sourceLocation: SourceLocation = #_sourceLocation) {
        guard let actual, actual.count == expected.count else {
            Issue.record("expected \(expected), got \(String(describing: actual))", sourceLocation: sourceLocation)
            return
        }
        for (a, e) in zip(actual, expected) where abs(a - e) > tolerance {
            Issue.record("expected \(expected), got \(actual)", sourceLocation: sourceLocation)
            return
        }
    }

    @Test func outwardIsBaselineTurnedClockwiseFromAbove() {
        // Scene schema: outward = baseline direction turned 90 degrees clockwise viewed from +y,
        // which in plan [x, z] is (-along.z, along.x).
        let axis = SceneWall(meter: .zero, outward: SIMD3(0, 0, 1), groundY: 0)
        #expect(axis.along == SIMD3(1, 0, 0))
        for w in [axis, Self.wall] {
            let along = w.along
            #expect(simd_distance(SIMD2(-along.z, along.x), SIMD2(w.outward.x, w.outward.z)) < 1e-6)
        }
    }

    @Test func wallCoordinatesRoundTrip() {
        let w = Self.wall
        let p = w.world(s: 1.25, height: 0.7, out: -0.4)
        let c = w.wallCoordinates(of: p)
        #expect(abs(c.s - 1.25) < 1e-5 && abs(c.height - 0.7) < 1e-5 && abs(c.out + 0.4) < 1e-5)
        // Plan feet back to wall meters: the baseline's right end is s = 5 m on the wall line.
        let plan = w.wallCoordinates(ofPlanPointFeet: SIMD2(16.4042, -16.4042))
        #expect(abs(plan.s - 5) < 1e-4 && abs(plan.out) < 1e-4)
    }

    @Test func exportValidatesAgainstSceneSchema() throws {
        let (data, _) = try Self.exported()
        #expect(try SceneSchemas.scene().validate(data) == [])
    }

    @Test func exportIsDeterministicAndHasNoNulls() throws {
        let first = try SceneExport.jsonData(Self.input())
        #expect(first == (try SceneExport.jsonData(Self.input())))
        let text = String(decoding: first, as: UTF8.self)
        #expect(!text.contains("null"))
        // The only error the input gave is the meter's.
        #expect(text.components(separatedBy: "plus_minus_ft").count == 2)
    }

    /// An estimated ground (the engine's chest-height guess) exports as an error on every object,
    /// and the result still matches the schema.
    @Test func objectErrorGoesOnEveryObject() throws {
        var input = Self.input()
        input.objectPlusMinus = 0.3
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let objects = try #require(try Value.parse(data)["objects"]?.array)
        #expect(objects.count == 4)
        for object in objects {
            // 0.3 m = 0.9843 ft.
            expectClose(object["plus_minus_ft"].map { [$0.number ?? .nan] }, [0.9843])
        }
    }

    @Test func meterAndBaselineInFeet() throws {
        let (_, v) = try Self.exported()
        #expect(v["schema_version"] == .string("1.0"))
        expectClose(v["meter"]?["pos"]?.numbers, [3.2808, 3.9370, -6.5617])
        #expect(v["meter"]?["wall_id"] == .string("side"))
        expectClose(v["meter"]?["plus_minus_ft"].map { [$0.number ?? .nan] }, [0.3281])
        let wall = try #require(v["walls"]?[0])
        // s = -3 m: (1, -2) + (0.8, -0.6) * -3 = (-1.4, -0.2) m. s = 5 m: (5, -5) m.
        expectClose(wall["baseline"]?[0]?.numbers, [-4.5932, -0.6562])
        expectClose(wall["baseline"]?[1]?.numbers, [16.4042, -16.4042])
        expectClose(wall["height_ft"].map { [$0.number ?? .nan] }, [8.8583])
    }

    @Test func objectsInFeet() throws {
        let (_, v) = try Self.exported()
        let objects = try #require(v["objects"]?.array)
        #expect(objects.map { $0["type"]?.string } == ["door", "window", "gas_meter", "ac"])
        let door = objects[0]
        expectClose(door["span_ft"]?.numbers, [1.6404, 4.5932])
        expectClose([door["bottom_ft"]?.number ?? .nan, door["top_ft"]?.number ?? .nan], [0, 6.5617])
        #expect(door["attrs"] == nil)
        #expect(door["source"] == .string("tap"))
        #expect(objects[1]["attrs"] == .object(["operable": .bool(true)]))

        let gas = objects[2]
        // Tapped at s = -2.5 m; nominal 0.3 m width gives s -2.65...-2.35 m.
        expectClose(gas["span_ft"]?.numbers, [-8.6942, -7.7100])
        let footprint = (gas["footprint"]?.array ?? []).compactMap(\.numbers)
        try #require(footprint.count == 4)
        // Back-left corner on the wall line at s = -2.65: (1, -2) + (0.8, -0.6) * -2.65 m.
        expectClose(footprint[0], [(1 - 2.12) / 0.3048, (-2 + 1.59) / 0.3048])
        // Front-left is 0.3 m further out along (0.6, 0.8).
        expectClose(footprint[3], [(1 - 2.12 + 0.18) / 0.3048, (-2 + 1.59 + 0.24) / 0.3048])
        #expect(objects[3]["bottom_ft"] == nil && objects[3]["top_ft"] == nil)
    }

    /// D1 option B preserves the measured center in both plan axes.
    @Test func acUnitGetsItsAssumedThreeFootSquare() throws {
        #expect(abs(SceneExport.acAssumedSide / 0.3048 - 3) < 1e-4)
        let (_, v) = try Self.exported()
        let ac = try #require(v["objects"]?[3])
        #expect(ac["type"] == .string("ac"))
        // Tapped at s = 3.0 m = 9.8425 ft; 1.5 ft either side.
        expectClose(ac["span_ft"]?.numbers, [8.3425, 11.3425])
        let footprint = (ac["footprint"]?.array ?? []).compactMap(\.numbers)
        try #require(footprint.count == 4)
        // Back-left to back-right: 3 ft along (0.8, -0.6). Back-left to front-left: 3 ft out along
        // (0.6, 0.8), centered on the tap 0.5 m out.
        expectClose([footprint[1][0] - footprint[0][0], footprint[1][1] - footprint[0][1]], [2.4, -1.8])
        expectClose([footprint[3][0] - footprint[0][0], footprint[3][1] - footprint[0][1]], [1.8, 2.4])
        // Back edge is 0.5 - 0.4572 m out; it is no longer reset to the wall line.
        expectClose(footprint[0], [(1 + 0.8 * 2.5428 + 0.6 * 0.0428) / 0.3048,
                                   (-2 - 0.6 * 2.5428 + 0.8 * 0.0428) / 0.3048])
    }

    @Test(arguments: [ScenePointObjectKind.ac, .gasMeter])
    func assumedFootprintStaysInTheTappedSegmentAtAnInwardCorner(kind: ScenePointObjectKind) throws {
        let wall = SceneWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0,
                             rightCorners: [WallCorner(s: 2, outward: SIMD3(-1, 0, 0))])
        let s: Float = kind == .ac ? 1.7 : 1.95
        let half: Float = kind == .ac ? SceneExport.acAssumedSide / 2 : 0.15
        let depth = half * 2
        var input = Self.input()
        input.wall = wall
        input.baselineS = -3...5
        input.features = [.pointObject(kind: kind, tap: wall.world(s: s, height: 0, out: 0), bottom: nil, top: nil)]
        let value = try Value.parse(SceneExport.jsonData(input))
        let polygon = try #require(value["objects"]?[0]?["footprint"]?.array).compactMap(\.numbers)
        try #require(polygon.count == 4)
        let scale = SceneUnits.feetPerMeter
        let back: Float = kind == .ac ? -half : 0
        let front = back + depth
        let expected = [SIMD2(s - half, back), SIMD2(s + half, back), SIMD2(s + half, front), SIMD2(s - half, front)]
        for (actual, point) in zip(polygon, expected) {
            expectClose(actual, [Double(point.x) * scale, Double(point.y) * scale])
        }
    }

    @Test func facingGroundCoverageKeyframes() throws {
        let (_, v) = try Self.exported()
        let facing = try #require(v["facing"]?[0])
        expectClose(facing["span_ft"]?.numbers, [-3.2808, 6.5617])
        // Taps 2.0 and 2.4 m out: the facing depth is the nearer, 2.0 m = 6.5617 ft, not the mean.
        expectClose(facing["depth_ft"].map { [$0.number ?? .nan] }, [6.5617])

        let drive = try #require(v["ground"]?[0])
        #expect(drive["type"] == .string("drive"))
        let polygon = (drive["polygon"]?.array ?? []).compactMap(\.numbers)
        try #require(polygon.count == 4)
        // The strip's far side is 0.5 ft further out along the wall's outward (0.6, 0.8).
        expectClose([polygon[3][0] - polygon[0][0], polygon[3][1] - polygon[0][1]], [0.3, 0.4])

        let coverage = try #require(v["coverage"])
        #expect(coverage["ends"]?["left"]?["kind"] == .string("limit"))
        #expect(coverage["ends"]?["right"]?["kind"] == .string("unexplored"))
        let observed = try #require(coverage["observed"]?.array)
        #expect(observed.map { $0["band"]?.string } == ["wall", "ground"])
        // Every wall entry says how high it was seen: 2.286 m is 7.5 ft.
        #expect(observed[0]["out_ft"]?.number == 7.5)
        expectClose(observed[1]["out_ft"].map { [$0.number ?? .nan] }, [9.8425])

        let k1 = try #require(v["keyframes"]?[0])
        let pose = try #require(k1["pose"]?.numbers)
        #expect(pose.count == 16)
        let m = Self.pose
        let rotation = (0..<3).flatMap { c in (0..<4).map { r in Double(m[c][r]) } }
        expectClose(Array(pose[0..<12]), rotation)
        expectClose(Array(pose[12..<16]), [3.2808, 4.9213, 6.5617, 1])
        #expect(k1["w"] == .number(1920) && k1["h"] == .number(1440) && k1["img"] == .string("k1.jpg"))
        #expect(v["stills"] == .object(["meter_close": .string("meter_close.jpg")]))
    }

    @Test func validatorCatchesOutOfSchemaScene() throws {
        let (_, v) = try Self.exported()
        guard case .object(var document) = v else { Issue.record("scene is not an object"); return }
        document["walls"] = .array([])
        let errors = try SceneSchemas.scene().validate(.object(document))
        #expect(errors == ["$.walls: 0 items, fewer than minItems 1"])
    }

    /// A meter tapped on an estimated plane exports a wider error: 0.15 m on top of the given error,
    /// or on top of the server's 0.3 ft default when none is given.
    @Test func estimatedPlaneWidensTheMeterError() throws {
        func meterError(_ plusMinus: Float?, _ plane: MeterPlaneSource) throws -> Double? {
            var input = Self.input()
            input.meterPlusMinus = plusMinus
            input.meterPlane = plane
            let data = try SceneExport.jsonData(input)
            #expect(try SceneSchemas.scene().validate(data) == [])
            return try Value.parse(data)["meter"]?["plus_minus_ft"]?.number
        }
        #expect(try meterError(nil, .detectedPlane) == nil)
        expectClose([try meterError(0.1, .detectedPlane) ?? .nan], [0.3281])
        expectClose([try meterError(0.1, .estimatedPlane) ?? .nan], [0.8202])  // 0.25 m
        expectClose([try meterError(nil, .estimatedPlane) ?? .nan], [0.7921])  // 0.3 ft + 0.15 m
    }

    /// The schema closes `attrs` to operable and well: an exported window passes, another key fails.
    @Test func attrsAcceptOnlyKnownKeys() throws {
        let (_, v) = try Self.exported()
        guard case .object(var document) = v, case .array(var objects)? = document["objects"],
              case .object(var window) = objects[1] else { Issue.record("no window object"); return }
        #expect(try SceneSchemas.scene().validate(v) == [])
        window["attrs"] = .object(["operable": .bool(true), "locked": .bool(false)])
        objects[1] = .object(window)
        document["objects"] = .array(objects)
        #expect(try !SceneSchemas.scene().validate(.object(document)).isEmpty)
    }

    @Test func invalidInputThrows() {
        var input = Self.input()
        input.features = [.fence(foot: [.zero])]
        #expect(throws: SceneExportError.wrongPointCount(feature: "features[0] fence", expected: 2, actual: 1)) {
            try SceneExport.jsonData(input)
        }

        input = Self.input()
        input.baselineS = 1...5
        #expect(throws: SceneExportError.meterOutsideBaseline(lower: 1, upper: 5)) { try SceneExport.jsonData(input) }

        input = Self.input()
        input.wall.outward = SIMD3(0, 0, 2)
        #expect(throws: SceneExportError.outwardNotUnitHorizontal(SIMD3(0, 0, 2))) { try SceneExport.jsonData(input) }

        input = Self.input()
        input.keyframes[0].intrinsics.x = .nan
        #expect(throws: SceneExportError.self) { try SceneExport.jsonData(input) }
    }

    /// scene.schema.json allows 500 observed entries. Too many spans are joined with their
    /// neighbours, keeping the smaller reach, and spans with a gap between them are never joined.
    @Test func tooManySpansAreJoinedConservativelyToFitTheSchema() throws {
        // 600 touching 1 cm spans alternating 1.0 and 1.2 m, then two separated by a gap.
        var facing = (0..<600).map { i in
            ObservedSpan(span: (Float(i) * 0.01)...(Float(i + 1) * 0.01), out: i.isMultiple(of: 2) ? 1.0 : 1.2)
        }
        facing += [ObservedSpan(span: 7...7.5, out: 2), ObservedSpan(span: 8...8.5, out: 2)]
        var input = Self.input()
        input.coverage = SceneCoverage(leftEndMarked: false, rightEndMarked: false, wall: [ObservedSpan(span: -3...5, out: 2.286)], ground: [], facing: facing)
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let entries = (try Value.parse(data)["coverage"]?["observed"]?.array ?? []).filter { $0["band"]?.string == "facing" }
        #expect(entries.count <= 499)
        // No entry reaches farther than the least of the spans it covers.
        for entry in entries {
            let span = try #require(entry["span_ft"]?.numbers)
            let covered = facing.filter {
                Double($0.span.upperBound) * SceneUnits.feetPerMeter > span[0] + 1e-3
                    && Double($0.span.lowerBound) * SceneUnits.feetPerMeter < span[1] - 1e-3
            }
            let least = try #require(covered.map(\.out).min())
            #expect(try #require(entry["out_ft"]?.number) <= Double(least) * SceneUnits.feetPerMeter + 1e-6)
        }
        // The two separated spans stay two.
        #expect(entries.filter { ($0["span_ft"]?.numbers?.first ?? 0) > 22 }.count == 2)
    }

    /// Every wall entry carries the height seen, rounded down, never absent (absent reads as seen
    /// to headroom height). Many heights are joined like the other bands, each join keeping the
    /// lower, so the band stays within its share of the schema's 500 entries.
    @Test func wallEntriesAlwaysSayHowHighAndFitTheirShare() throws {
        let wall = (0..<600).map { i in
            ObservedSpan(span: (Float(i) * 0.01)...(Float(i + 1) * 0.01), out: i.isMultiple(of: 3) ? 2.0 : 2.1336)
        }
        var input = Self.input()
        input.coverage = SceneCoverage(leftEndMarked: false, rightEndMarked: false, wall: wall, ground: [])
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let entries = (try Value.parse(data)["coverage"]?["observed"]?.array ?? []).filter { $0["band"]?.string == "wall" }
        #expect(entries.count <= 125 && !entries.isEmpty)
        for entry in entries {
            let out = try #require(entry["out_ft"]?.number)
            let span = try #require(entry["span_ft"]?.numbers)
            let covered = wall.filter {
                Double($0.span.upperBound) * SceneUnits.feetPerMeter > span[0] + 1e-3
                    && Double($0.span.lowerBound) * SceneUnits.feetPerMeter < span[1] - 1e-3
            }
            #expect(out <= Double(try #require(covered.map(\.out).min())) * SceneUnits.feetPerMeter + 1e-6)
        }
        // 2.1336 m is 7 ft exactly; Float noise must not round it below.
        input.coverage.wall = [ObservedSpan(span: 0...1, out: 2.1336)]
        let one = try #require(try Value.parse(try SceneExport.jsonData(input))["coverage"]?["observed"]?.array?.first)
        #expect(one["out_ft"]?.number == 7)
    }

    /// Mesh measurements become `facing` and `overheads` entries without `plus_minus_ft` (the
    /// server's mesh default), rounded down to 4 decimals of a foot. The fence feature's entry
    /// stays first. 1.9812 m is 6.5 ft and 2.4994 m 8.2 ft (8.20013 down to 8.2001).
    @Test func meshMeasurementsBecomeFacingAndOverheads() throws {
        var input = Self.input()
        input.meshFacing = [ObservedSpan(span: -0.5...0.5, out: 1.9812)]
        input.meshOverheads = [ObservedSpan(span: 0...1, out: 2.4994)]
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let v = try Value.parse(data)
        let facing = try #require(v["facing"]?.array)
        #expect(facing.count == 2)
        #expect(facing[1]["wall_id"] == .string("side"))
        expectClose(facing[1]["span_ft"]?.numbers, [-1.6404, 1.6404])
        #expect(facing[1]["depth_ft"]?.number == 6.5)
        #expect(facing[1]["plus_minus_ft"] == nil)
        let overheads = try #require(v["overheads"]?.array)
        #expect(overheads.count == 1)
        expectClose(overheads[0]["span_ft"]?.numbers, [0, 3.2808])
        #expect(overheads[0]["clearance_ft"]?.number == 8.2001)
        #expect(overheads[0]["plus_minus_ft"] == nil)
    }

    /// Without mesh measurements there is no `overheads` key, as before.
    @Test func noMeshMeansNoOverheads() throws {
        #expect(try Self.exported().value["overheads"] == nil)
    }

    /// A measured span over a corner is split there, each part naming its own wall.
    @Test func meshSpansSplitAtCorners() throws {
        var input = Self.input()
        input.wall.rightCorners = [WallCorner(s: 0.2, outward: SIMD3(0.8, 0, -0.6))]
        input.meshOverheads = [ObservedSpan(span: -0.5...0.5, out: 2)]
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let overheads = try #require(try Value.parse(data)["overheads"]?.array)
        #expect(overheads.map { $0["wall_id"]?.string } == ["side", "side-right-1"])
        expectClose(overheads[0]["span_ft"]?.numbers, [-1.6404, 0.6562])
        expectClose(overheads[1]["span_ft"]?.numbers, [0.6562, 1.6404])
    }

    @Test func negativeMeshMeasurementIsRefused() {
        var input = Self.input()
        input.meshFacing = [ObservedSpan(span: 0...1, out: -0.1)]
        #expect(throws: SceneExportError.negativeValue(field: "meshFacing[0].out", value: -0.1)) { try SceneExport.jsonData(input) }
    }
}
