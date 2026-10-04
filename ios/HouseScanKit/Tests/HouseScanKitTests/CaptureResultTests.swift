import Foundation
import HouseScanKit
import Testing
import simd

// Every body here is self-authored to the shape of the server's device result; none is a server
// example or a real capture.
@Suite struct CaptureResultTests {
    typealias Association = CaptureResult.Association

    static let scan = Association(sessionID: "session-a", captureID: "cap-a", runID: "run-a", epoch: "e1")

    static let eligibleJSON = """
        {"runId": "run-a", "status": "complete", "viewsNeeded": [], "memberActions": [],
         "outcome": {"kind": "eligible", "profile": "C", "message": "A battery fits on this wall.",
           "viewsNeeded": [], "reasons": [], "arkitEpoch": "e1",
           "frameToArkit": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
           "recommendedPlacement": {"wallId": "w1", "startSM": 1.1, "confidence": 0.7, "profile": "C",
             "footprintM": {"w": 0.8, "h": 1.0, "d": 0.5},
             "boxSceneM": {"centerM": [1.5, 0.5, 0.3], "sizeM": [0.8, 1.0, 0.5], "yawDeg": 0},
             "boxArkitWorld": {"pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 1.5,0.5,0.3,1], "size": [0.8, 1.0, 0.5]}},
           "cableRoutePolylineArkit": null},
         "verdictUrl": null, "previewUrl": null, "reviewUrl": "/v1/captures/cap-a/review"}
        """

    static func decode(_ json: String) throws -> CaptureResult.Response {
        try JSONDecoder().decode(CaptureResult.Response.self, from: Data(json.utf8))
    }

    static func eligible() throws -> CaptureResult.Response { try decode(eligibleJSON) }

    static func verified(_ response: CaptureResult.Response) -> CaptureResult.Record {
        CaptureResult.Record(response: response, association: scan, analysis: .verified)
    }

    // MARK: Decoding

    @Test func processingHasNoOutcome() throws {
        let response = try Self.decode("""
            {"runId": null, "status": "processing", "viewsNeeded": [], "memberActions": [], "outcome": null}
            """)
        #expect(response.status == .processing)
        #expect(response.runId == nil && response.outcome == nil && response.criteria == nil)
        #expect(response.previewUrl == nil && response.reviewUrl == nil)
        #expect(Self.verified(response).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4)
            == .unavailable(.noOutcome(.processing)))
    }

    @Test func eligibleWithoutCriteriaKeepsThemAbsent() throws {
        let response = try Self.eligible()
        #expect(response.outcome?.kind == .eligible)
        #expect(response.criteria == nil)
        #expect(response.reviewUrl == "/v1/captures/cap-a/review")
        let box = try #require(response.outcome?.recommendedPlacement?.boxArkitWorld)
        #expect(box.size == [0.8, 1.0, 0.5])
        guard case .box(let placed) = Self.verified(response).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4) else {
            Issue.record("expected a box")
            return
        }
        #expect(placed.size == SIMD3(0.8, 1.0, 0.5))
        #expect(nearlyEqual(placed.anchorFromBox, translation(1.5, 0.5, 0.3)))
    }

    @Test func eligibleWithCriteriaKeepsEachAsSent() throws {
        let json = Self.eligibleJSON.replacingOccurrences(
            of: "\"verdictUrl\": null",
            with: """
                "criteria": [{"id": "c1", "outcome": "pass", "measuredFt": 4.2, "coverage": "observed"},
                             {"id": "c2", "outcome": "unsure", "unsureCause": "unobserved"},
                             {"id": "c3", "outcome": "waived"}],
                "verdictUrl": null
                """)
        let criteria = try #require(try Self.decode(json).criteria)
        #expect(criteria.map(\.outcome) == [.pass, .unsure, .unknown("waived")])
        #expect(criteria[0].measuredFt == 4.2 && criteria[0].coverage == "observed" && criteria[0].thresholdFt == nil)
        #expect(criteria[1].unsureCause == "unobserved" && criteria[1].measuredFt == nil)
    }

    @Test func manualReviewKeepsViewsAndPromptsVerbatim() throws {
        let body = "  Hold the phone at chest height,\nand include 3 ft left of the “meter”.  "
        let view = """
            {"id": "vn1", "kind": "past_end", "criteria": ["c2"], "why": "The left end was not seen.",
             "promptId": "p-left-end", "prompt": {"title": "Show the left end", "body": \(try jsonString(body))},
             "side": "left", "wallId": "w1", "futureKey": {"nested": true},
             "referenceCrop": {"image": "k00002", "boxPx": [10, 20, 300, 400]}}
            """
        let response = try Self.decode("""
            {"runId": "run-a", "status": "manual_review", "viewsNeeded": [\(view)],
             "memberActions": ["retake_meter_closeups"],
             "outcome": {"kind": "manual_review", "profile": "C", "message": "An installer will look at this scan.",
               "viewsNeeded": [\(view)], "reasons": [], "arkitEpoch": "e1"},
             "criteria": [{"id": "c2", "outcome": "unsure"}],
             "verdictUrl": null, "previewUrl": null, "reviewUrl": "/v1/captures/cap-a/review"}
            """)
        let outcome = try #require(response.outcome)
        #expect(outcome.kind == .manualReview && outcome.reasons.isEmpty)
        #expect(outcome.message == "An installer will look at this scan.")
        #expect(response.viewsNeeded == outcome.viewsNeeded)
        let needed = try #require(outcome.viewsNeeded.first)
        #expect(needed.prompt.title == "Show the left end")
        #expect(needed.prompt.body == body)
        #expect(needed.kind == "past_end" && needed.wallId == "w1" && needed.band == nil)
        #expect(needed.referenceCrop?.boxPx == [10, 20, 300, 400])
        #expect(response.memberActions == ["retake_meter_closeups"])
        #expect(Self.verified(response).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4)
            == .unavailable(.notEligible(.manualReview)))
    }

    @Test func unknownStatusAndKindDecodeAndWithholdAR() throws {
        let unknownKind = try Self.decode(Self.eligibleJSON
            .replacingOccurrences(of: "\"status\": \"complete\"", with: "\"status\": \"archived\"")
            .replacingOccurrences(of: "\"kind\": \"eligible\"", with: "\"kind\": \"deferred\""))
        #expect(unknownKind.status == .unknown("archived"))
        #expect(unknownKind.outcome?.kind == .unknown("deferred"))
        #expect(unknownKind.outcome?.message == "A battery fits on this wall.")
        #expect(Self.verified(unknownKind).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4)
            == .unavailable(.notEligible(.unknown("deferred"))))

        var unknownStatus = try Self.eligible()
        unknownStatus.status = .unknown("archived")
        #expect(Self.verified(unknownStatus).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4)
            == .unavailable(.statusNotComplete(.unknown("archived"))))
    }

    @Test func missingStatusFailsToDecode() {
        #expect(throws: DecodingError.self) {
            try Self.decode(#"{"runId": "run-a", "viewsNeeded": [], "memberActions": [], "outcome": null}"#)
        }
    }

    // MARK: Gating

    @Test func eligibleResultIsUnverifiedByDefault() throws {
        let record = CaptureResult.Record(response: try Self.eligible(), association: Self.scan)
        #expect(record.analysis == .unverified)
        #expect(record.placement(in: Self.scan, meterAnchor: matrix_identity_float4x4) == .unavailable(.analysisUnverified))
    }

    enum Mismatch: CaseIterable, Sendable {
        case worldSession, worldCapture, worldRun, worldEpoch, responseRun, responseRunMissing, outcomeEpoch

        var field: CaptureResult.IdentityField {
            switch self {
            case .worldSession: .session
            case .worldCapture: .capture
            case .worldRun, .responseRun, .responseRunMissing: .run
            case .worldEpoch, .outcomeEpoch: .epoch
            }
        }
    }

    @Test(arguments: Mismatch.allCases)
    func everyIdentityMismatchWithholdsAR(_ mismatch: Mismatch) throws {
        var response = try Self.eligible()
        var world = Self.scan
        switch mismatch {
        case .worldSession: world.sessionID = "session-b"
        case .worldCapture: world.captureID = "cap-b"
        case .worldRun: world.runID = "run-b"
        case .worldEpoch: world.epoch = "e2"
        case .responseRun: response.runId = "run-b"
        case .responseRunMissing: response.runId = nil
        case .outcomeEpoch: response.outcome?.arkitEpoch = "e2"
        }
        let record = Self.verified(response)
        #expect(record.placement(in: world, meterAnchor: matrix_identity_float4x4) == .unavailable(.mismatch(mismatch.field)))
        #expect(record.response.outcome?.message == "A battery fits on this wall.")
    }

    @Test func epochReusedByANewSessionIsNotAMatch() throws {
        let newSession = Association(sessionID: "session-b", captureID: "cap-a", runID: "run-a", epoch: "e1")
        #expect(newSession.epoch == Self.scan.epoch)
        #expect(Self.verified(try Self.eligible()).placement(in: newSession, meterAnchor: matrix_identity_float4x4)
            == .unavailable(.mismatch(.session)))
    }

    enum BadBox: CaseIterable, Sendable {
        case shortPose, twoSizes, nanPose, infiniteSize, zeroSize, scaledPose, shearedPose, mirroredPose, badBottomRow
        case noBox, noEpoch, noPlacement
        case floatOverflowSize, floatUnderflowSize, floatOverflowTranslation

        var expected: CaptureResult.Unavailable {
            switch self {
            case .shortPose: .malformedBox(.poseCount(15))
            case .twoSizes: .malformedBox(.sizeCount(2))
            case .nanPose, .infiniteSize, .floatOverflowSize, .floatOverflowTranslation: .malformedBox(.nonFinite)
            case .zeroSize, .floatUnderflowSize: .malformedBox(.nonPositiveSize)
            case .scaledPose, .shearedPose, .mirroredPose, .badBottomRow: .malformedBox(.notRigid)
            case .noBox, .noEpoch: .notInARKitWorld
            case .noPlacement: .noPlacement
            }
        }

        func apply(to outcome: inout CaptureResult.Outcome) {
            switch self {
            case .shortPose: outcome.recommendedPlacement?.boxArkitWorld?.pose.removeLast()
            case .twoSizes: outcome.recommendedPlacement?.boxArkitWorld?.size = [0.8, 1.0]
            case .nanPose: outcome.recommendedPlacement?.boxArkitWorld?.pose[13] = .nan
            case .infiniteSize: outcome.recommendedPlacement?.boxArkitWorld?.size[1] = .infinity
            case .zeroSize: outcome.recommendedPlacement?.boxArkitWorld?.size[2] = 0
            case .floatOverflowSize: outcome.recommendedPlacement?.boxArkitWorld?.size[0] = 1e40
            case .floatUnderflowSize: outcome.recommendedPlacement?.boxArkitWorld?.size[0] = 1e-50
            case .floatOverflowTranslation: outcome.recommendedPlacement?.boxArkitWorld?.pose[12] = 1e40
            case .scaledPose:
                for i in 0..<11 { outcome.recommendedPlacement?.boxArkitWorld?.pose[i] *= 1.25 }
            case .shearedPose: outcome.recommendedPlacement?.boxArkitWorld?.pose[4] = 0.3
            case .mirroredPose: outcome.recommendedPlacement?.boxArkitWorld?.pose[0] = -1
            case .badBottomRow: outcome.recommendedPlacement?.boxArkitWorld?.pose[3] = 0.5
            case .noBox: outcome.recommendedPlacement?.boxArkitWorld = nil
            case .noEpoch: outcome.arkitEpoch = nil
            case .noPlacement: outcome.recommendedPlacement = nil
            }
        }
    }

    @Test(arguments: BadBox.allCases)
    func malformedGeometryWithholdsARAndKeepsTheResult(_ bad: BadBox) throws {
        var response = try Self.eligible()
        var outcome = try #require(response.outcome)
        bad.apply(to: &outcome)
        response.outcome = outcome
        let record = Self.verified(response)
        #expect(record.placement(in: Self.scan, meterAnchor: matrix_identity_float4x4) == .unavailable(bad.expected))
        #expect(record.response.outcome?.kind == .eligible)
        #expect(record.response.outcome?.message == "A battery fits on this wall.")
    }

    @Test func nonFiniteMeterAnchorWithholdsAR() throws {
        var anchor = matrix_identity_float4x4
        anchor.columns.3.x = .nan
        #expect(Self.verified(try Self.eligible()).placement(in: Self.scan, meterAnchor: anchor) == .unavailable(.invalidMeterAnchor))
    }

    // MARK: Transform

    @Test func boxIsExpressedInATranslatedRotatedMeterAnchor() throws {
        let worldFromAnchor = pose(rotationY(degrees: 90), SIMD3(2, 0, -1))
        let anchorFromBox = pose(rotationY(degrees: 30), SIMD3(0.5, 0.4, 0.3))
        var response = try Self.eligible()
        response.outcome?.recommendedPlacement?.boxArkitWorld?.pose = columnMajor(worldFromAnchor * anchorFromBox)

        let placement = Self.verified(response).placement(in: Self.scan, meterAnchor: float(worldFromAnchor))
        guard case .box(let placed) = placement else {
            Issue.record("expected a box, got \(placement)")
            return
        }
        #expect(nearlyEqual(placed.anchorFromBox, float(anchorFromBox)))
        #expect(nearlyEqual(SIMD3(placed.anchorFromBox.columns.3.x, placed.anchorFromBox.columns.3.y, placed.anchorFromBox.columns.3.z),
                            SIMD3(0.5, 0.4, 0.3), 1e-5))
        #expect(placed.size == SIMD3(0.8, 1.0, 0.5))
    }

    /// The server builds the box's world pose from the scene box and the scene-to-ARKit similarity:
    /// the similarity's scale is divided out of the rotation and multiplied into the size. The app
    /// must take that size as it is, not scale it again by `frameToArkit`.
    @Test func sizeCarriesTheServersScaleOnce() throws {
        let scale = 1.25
        let frameToArkit = pose(rotationY(degrees: 40) * scale, SIMD3(1, 0, 2))
        let centerM = SIMD3<Double>(1.5, 0.5, 0.3), sizeM = SIMD3<Double>(0.8, 1.0, 0.56), yawDeg = 10.0

        let rotation = rotation3(frameToArkit) * (1 / scale) * rotationY(degrees: yawDeg)
        let center = frameToArkit * SIMD4(centerM, 1)
        let serverPose = pose(rotation, SIMD3(center.x, center.y, center.z))
        let serverSize = sizeM * scale

        var response = try Self.eligible()
        response.outcome?.recommendedPlacement?.boxArkitWorld?.pose = columnMajor(serverPose)
        response.outcome?.recommendedPlacement?.boxArkitWorld?.size = [serverSize.x, serverSize.y, serverSize.z]

        let placement = Self.verified(response).placement(in: Self.scan, meterAnchor: matrix_identity_float4x4)
        guard case .box(let placed) = placement else {
            Issue.record("expected a box, got \(placement)")
            return
        }
        #expect(nearlyEqual(placed.size, SIMD3(1.0, 1.25, 0.7), 1e-6))
        #expect(nearlyEqual(placed.anchorFromBox, float(serverPose)))
        for column in [placed.anchorFromBox.columns.0, placed.anchorFromBox.columns.1, placed.anchorFromBox.columns.2] {
            #expect(abs(simd_length(SIMD3(column.x, column.y, column.z)) - 1) < 1e-5)
        }
    }
}

private func jsonString(_ text: String) throws -> String {
    String(decoding: try JSONEncoder().encode(text), as: UTF8.self)
}

private func rotationY(degrees: Double) -> simd_double3x3 {
    let a = degrees * .pi / 180
    return simd_double3x3(SIMD3(cos(a), 0, -sin(a)), SIMD3(0, 1, 0), SIMD3(sin(a), 0, cos(a)))
}

private func rotation3(_ m: simd_double4x4) -> simd_double3x3 {
    simd_double3x3(
        SIMD3(m.columns.0.x, m.columns.0.y, m.columns.0.z),
        SIMD3(m.columns.1.x, m.columns.1.y, m.columns.1.z),
        SIMD3(m.columns.2.x, m.columns.2.y, m.columns.2.z))
}

private func pose(_ r: simd_double3x3, _ t: SIMD3<Double>) -> simd_double4x4 {
    simd_double4x4(SIMD4(r.columns.0, 0), SIMD4(r.columns.1, 0), SIMD4(r.columns.2, 0), SIMD4(t, 1))
}

private func translation(_ x: Float, _ y: Float, _ z: Float) -> simd_float4x4 {
    var m = matrix_identity_float4x4
    m.columns.3 = SIMD4(x, y, z, 1)
    return m
}

private func columnMajor(_ m: simd_double4x4) -> [Double] {
    [m.columns.0, m.columns.1, m.columns.2, m.columns.3].flatMap { [$0.x, $0.y, $0.z, $0.w] }
}

private func float(_ m: simd_double4x4) -> simd_float4x4 {
    simd_float4x4(SIMD4<Float>(m.columns.0), SIMD4<Float>(m.columns.1), SIMD4<Float>(m.columns.2), SIMD4<Float>(m.columns.3))
}

private func nearlyEqual(_ a: simd_float4x4, _ b: simd_float4x4, _ tolerance: Float = 1e-5) -> Bool {
    [(a.columns.0, b.columns.0), (a.columns.1, b.columns.1), (a.columns.2, b.columns.2), (a.columns.3, b.columns.3)]
        .allSatisfy { simd_reduce_max(simd_abs($0 - $1)) <= tolerance }
}
