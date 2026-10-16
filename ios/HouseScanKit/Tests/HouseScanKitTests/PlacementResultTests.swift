import Foundation
import HouseScanKit
import Testing
import simd

@Suite struct PlacementResultTests {
    /// ios/HouseScan/Runtime/SampleResult.json, the app's offline sample.
    static func sampleData(file: String = #filePath) throws -> Data {
        let url = URL(fileURLWithPath: file)
            .deletingLastPathComponent()  // HouseScanKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // HouseScanKit
            .deletingLastPathComponent()  // ios
            .appendingPathComponent("HouseScan/Runtime/SampleResult.json")
        return try Data(contentsOf: url)
    }

    /// The sample with one exact substring replaced; fails the test if the substring is absent.
    static func sample(replacing target: String, with replacement: String) throws -> Data {
        let text = String(decoding: try sampleData(), as: UTF8.self)
        try #require(text.contains(target), "sample no longer contains \(target)")
        return Data(text.replacingOccurrences(of: target, with: replacement).utf8)
    }

    private func expectError(_ expected: PlacementDecodingError, _ data: Data, sourceLocation: SourceLocation = #_sourceLocation) {
        #expect(throws: expected, sourceLocation: sourceLocation) { try PlacementResult.decode(data) }
    }

    @Test func sampleMatchesResultSchema() throws {
        #expect(try SceneSchemas.result().validate(Self.sampleData()) == [])
    }

    @Test func sampleDecodes() throws {
        let result = try PlacementResult.decode(Self.sampleData())
        #expect(result.decision == .manualReview)
        #expect(result.policy.id == nil && result.policy.sources == ["public"] && !result.policy.autoApprove)
        let spot = try #require(result.spot)
        #expect(spot.spanFt == SIMD2(3.0, 5.5))
        #expect(spot.footprint.count == 4)
        #expect(result.checks.map(\.outcome) == [.pass, .unsure, .unsure, .pass])
        #expect(result.checks[1].unsureCause == .margin)
        #expect(result.checks[2].unsureCause == .unobserved && result.checks[2].measuredFt == nil)
        #expect(result.checks[2].comparison == .atLeast && result.checks[2].subject == nil)
        #expect(result.nearestConsidered == nil)
        #expect(result.missingEvidence.map(\.kind) == [.band, .pastEnd])
        #expect(result.missingEvidence[1].side == .left && result.missingEvidence[1].spanFt == nil)
        #expect(result.ends.left.kind == .unexplored && result.ends.right.kind == .limit)
    }

    @Test func encodingRoundTripsAndStaysInSchema() throws {
        let result = try PlacementResult.decode(Self.sampleData())
        let encoded = try JSONEncoder().encode(result)
        #expect(try SceneSchemas.result().validate(encoded) == [])
        #expect(try PlacementResult.decode(encoded) == result)
    }

    @Test func routePolylineMapsOntoTheWall() throws {
        let result = try PlacementResult.decode(Self.sampleData())
        let route = try #require(result.route)
        // Sample wall: along +x, meter at world (0, 1.5, 0), outward +z.
        let wall = SceneWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0)
        let end = wall.wallCoordinates(ofPlanPointFeet: try #require(route.polyline.last))
        #expect(abs(end.s - 0.9144) < 1e-5 && abs(end.out) < 1e-5)  // 3 ft
    }

    /// The server adds optional fields within schema 1.0; the phone must keep decoding.
    @Test func ignoresUnknownKeys() throws {
        _ = try PlacementResult.decode(
            try Self.sample(replacing: #""schema_version": "1.0","#, with: #""schema_version": "1.0", "extra": true,"#))
        _ = try PlacementResult.decode(
            try Self.sample(replacing: #""key": "sample_window_clearance_ft""#, with: #""key": "sample_window_clearance_ft", "bogus": 1"#))
    }

    /// Review of #53: a mark the server set aside past an unexplored end is decoded, so the
    /// result can say it wasn't counted. Absent, the list is empty, as in the sample.
    @Test func decodesObjectsNotUsed() throws {
        #expect(try PlacementResult.decode(Self.sampleData()).objectsNotUsed.isEmpty)
        let message = "The window marked 12 ft left of the meter is past the left end of the scan, where the wall may turn, so it was not used."
        let data = try Self.sample(
            replacing: #""schema_version": "1.0","#,
            with: #""schema_version": "1.0", "objects_not_used": [{"object": "objects[0] window", "side": "left", "message": "\#(message)"}],"#)
        #expect(try SceneSchemas.result().validate(data) == [])
        let result = try PlacementResult.decode(data)
        #expect(result.objectsNotUsed == [PlacementObjectNotUsed(object: "objects[0] window", side: "left", message: message)])
        let encoded = try JSONEncoder().encode(result)
        #expect(try SceneSchemas.result().validate(encoded) == [])
        #expect(try PlacementResult.decode(encoded) == result)
    }

    /// A real answer from the hosted server (deployed from origin/t3/server at 737bf75) to the
    /// synthetic replay's scene, carrying fields newer than the first published schema.
    @Test func decodesARealServerAnswer() throws {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Schemas/server-answer-synthetic-wall.json")
        let data = try Data(contentsOf: url)
        #expect(try SceneSchemas.result().validate(data) == [])
        let result = try PlacementResult.decode(data)
        #expect(result.decision == .manualReview)
        #expect(!result.checks.isEmpty)
    }

    @Test func rejectsUnknownEnumValues() throws {
        expectError(.unknownEnumValue(path: "checks[1].unsure_cause", value: "tight"),
                    try Self.sample(replacing: #""unsure_cause": "margin""#, with: #""unsure_cause": "tight""#))
        expectError(.unknownEnumValue(path: "decision", value: "maybe"),
                    try Self.sample(replacing: #""decision": "manual_review""#, with: #""decision": "maybe""#))
        expectError(.unknownEnumValue(path: "route.outcome", value: "maybe"),
                    try Self.sample(replacing: #""outcome": "pass",\#n    "length_ft""#, with: #""outcome": "maybe",\#n    "length_ft""#))
    }

    /// Enums the app never reads take any string, so a value the server adds doesn't stop the
    /// result screen.
    @Test func acceptsNewValuesTheAppDoesNotRead() throws {
        let reason = try PlacementResult.decode(
            try Self.sample(replacing: #""code": "unsure_checks""#, with: #""code": "new_reason""#))
        #expect(reason.reasons.contains { $0.code == "new_reason" })
        let source = try PlacementResult.decode(
            try Self.sample(replacing: #""sources": ["public"]"#, with: #""sources": ["public", "regional"]"#))
        #expect(source.policy.sources == ["public", "regional"])
        let crossing = try PlacementResult.decode(
            try Self.sample(replacing: #""crossings": []"#, with: #""crossings": [{"subject": "gate", "span_ft": [1, 2], "effect": "bridge"}]"#))
        #expect(crossing.route?.crossings.first?.effect == "bridge")
    }

    /// A missing-evidence kind or band the server adds later decodes as unknown, and no capture
    /// can settle it, so the result offers it for installer review; the rest of the answer
    /// still shows. The keys stay required and must be strings.
    @Test func unknownMissingEvidenceIsKeptForInstallerReview() throws {
        let kind = try PlacementResult.decode(
            try Self.sample(replacing: #""kind": "past_end""#, with: #""kind": "roof_line""#))
        #expect(kind.missingEvidence.map(\.kind) == [.band, .unknown("roof_line")])
        #expect(kind.decision == .manualReview && kind.checks.count == 4)
        let band = try PlacementResult.decode(
            try Self.sample(replacing: #""band": "ground""#, with: #""band": "attic""#))
        #expect(band.missingEvidence[0].band == .unknown("attic"))

        let planner = GapPlanner()
        #expect(planner.plan(for: kind.missingEvidence[1], leftEnd: -2, rightEnd: 2) == nil)
        #expect(planner.plan(for: band.missingEvidence[0], leftEnd: -2, rightEnd: 2) == nil)
        #expect(!planner.isBeyondCapture(band.missingEvidence[0]))
        // The known item beside the unknown one keeps its request.
        #expect(planner.plan(for: kind.missingEvidence[0], leftEnd: -2, rightEnd: 2) != nil)

        // Unknown values survive a round trip as the server wrote them.
        let encoded = try JSONEncoder().encode(kind)
        #expect(try PlacementResult.decode(encoded) == kind)
        #expect(String(decoding: encoded, as: UTF8.self).contains(#""roof_line""#))

        // Still strict on presence and type.
        expectError(.missingKey(path: "missing_evidence[1].kind"),
                    try Self.sample(replacing: #""kind": "past_end","#, with: ""))
        let number = try Self.sample(replacing: #""kind": "past_end""#, with: #""kind": 7"#)
        do {
            _ = try PlacementResult.decode(number)
            Issue.record("a kind that is not a string must fail")
        } catch PlacementDecodingError.malformed(let path, _) {
            #expect(path == "missing_evidence[1].kind")
        }
        let list = try Self.sample(replacing: #""band": "ground""#, with: #""band": ["ground"]"#)
        do {
            _ = try PlacementResult.decode(list)
            Issue.record("a band that is not a string must fail")
        } catch PlacementDecodingError.malformed(let path, _) {
            #expect(path == "missing_evidence[0].band")
        }
    }

    @Test func rejectsOtherSchemaVersions() throws {
        expectError(.unsupportedSchemaVersion("2.0"),
                    try Self.sample(replacing: #""schema_version": "1.0""#, with: #""schema_version": "2.0""#))
    }

    @Test func requiredNullableKeysMustBePresent() throws {
        let data = try Self.sample(replacing: #""measured_ft": null,"#, with: "")
        expectError(.missingKey(path: "checks[2].measured_ft"), data)
    }

    @Test func pairsMustHaveTwoNumbers() throws {
        expectError(.wrongArrayLength(path: "spot.center", expected: 2, actual: 1),
                    try Self.sample(replacing: #""center": [4.25, 0.5]"#, with: #""center": [4.25]"#))
    }

    // MARK: Closer unseen end (issue #83). Made-up numbers.

    /// The sample with its spot over `spotFt` (nil: no spot) and these ends, in s feet.
    private func result(
        spotFt: SIMD2<Double>?, outcome: PlacementOutcome = .unsure,
        left: (PlacementEndKind, Double, Bool?), right: (PlacementEndKind, Double, Bool?)
    ) throws -> PlacementResult {
        var result = try PlacementResult.decode(Self.sampleData())
        if let spotFt {
            result.spot?.spanFt = spotFt
            result.spot?.outcome = outcome
        } else {
            result.spot = nil
        }
        result.ends.left.kind = left.0
        result.ends.left.sFt = left.1
        result.ends.left.beyondReach = left.2
        result.ends.right.kind = right.0
        result.ends.right.sFt = right.1
        result.ends.right.beyondReach = right.2
        return result
    }

    /// A spot left of the meter, a far unexplored left end and a near unexplored right one: only
    /// past the right end could a spot be closer, though the server lists the left first.
    @Test func theCloserUnseenEndIsTheOneNearerThanTheSpot() throws {
        let answer = try result(spotFt: SIMD2(-5.9, -3.3), left: (.unexplored, -13.2, nil), right: (.unexplored, 1.75, nil))
        #expect(answer.closerUnseenEnd() == PlacementUnseenEnd(side: .right, sFt: 1.75))
        // A spot right of the meter, past a near left end: the left.
        let right = try result(spotFt: SIMD2(12.0, 14.5), left: (.unexplored, -1.0, nil), right: (.limit, 20.0, nil))
        #expect(right.closerUnseenEnd() == PlacementUnseenEnd(side: .left, sFt: -1.0))
    }

    /// A spot over the meter: nothing past an end could be closer.
    @Test func aSpotOverTheMeterLeavesNoCloserUnseenEnd() throws {
        let answer = try result(spotFt: SIMD2(-1.0, 2.5), left: (.unexplored, -2.0, nil), right: (.unexplored, 3.0, nil))
        #expect(answer.closerUnseenEnd() == nil)
    }

    /// A passing spot with the unexplored end farther out than it: nothing to say.
    @Test func anEndFartherThanThePassingSpotIsNotNamed() throws {
        let answer = try result(spotFt: SIMD2(3.0, 5.5), outcome: .pass, left: (.limit, -4.0, nil), right: (.unexplored, 20.0, nil))
        #expect(answer.closerUnseenEnd() == nil)
        // The bundled sample: the spot 3 ft right, the left end unexplored 8 ft out.
        #expect(try PlacementResult.decode(Self.sampleData()).closerUnseenEnd() == nil)
    }

    /// Without a spot, the nearest unexplored end within cable reach; a limit end or one beyond
    /// reach is never named.
    @Test func withoutASpotTheNearestUnexploredEndWithinReach() throws {
        let both = try result(spotFt: nil, left: (.unexplored, -6.0, nil), right: (.unexplored, 4.0, false))
        #expect(both.closerUnseenEnd() == PlacementUnseenEnd(side: .right, sFt: 4.0))
        let beyond = try result(spotFt: nil, left: (.unexplored, -6.0, nil), right: (.unexplored, 4.0, true))
        #expect(beyond.closerUnseenEnd() == PlacementUnseenEnd(side: .left, sFt: -6.0))
        let limits = try result(spotFt: nil, left: (.limit, -6.0, nil), right: (.limit, 4.0, nil))
        #expect(limits.closerUnseenEnd() == nil)
    }
}
