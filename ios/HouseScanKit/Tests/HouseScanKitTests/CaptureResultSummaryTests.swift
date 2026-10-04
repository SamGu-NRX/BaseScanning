import Foundation
import HouseScanKit
import simd
import Testing

/// What the integration build shows of a capture-API result, from self-authored responses, and
/// the coordinator binding the result it fetched to its own scan.
@Suite struct CaptureResultSummaryTests {
    static let world = CaptureResult.Association(sessionID: "session-A", captureID: "cap_A", runID: "run_A", epoch: "e1")

    static func response(_ json: String) throws -> CaptureResult.Response {
        try JSONDecoder().decode(CaptureResult.Response.self, from: Data(json.utf8))
    }

    static func summary(_ json: String, association: CaptureResult.Association = world, analysis: CaptureResult.Analysis = .unverified) throws -> CaptureResultSummary {
        CaptureResultSummary(.init(response: try response(json), association: association, analysis: analysis), world: world, meterAnchor: matrix_identity_float4x4)
    }

    static let prompt = #"{"id":"vn1","kind":"wall_band","criteria":["C3"],"why":"unseen","promptId":"p1","prompt":{"title":"Step back and show the wall","body":"Include the ground under the meter."}}"#

    @Test func processingHasNoOutcomeAndNoMessage() throws {
        let s = try Self.summary(#"{"runId":"run_A","status":"processing","viewsNeeded":[],"memberActions":[]}"#)
        #expect(s.state == .working(.processing))
        #expect(s.message == nil && s.prompts.isEmpty && s.criteria == nil)
        #expect(s.arUnavailable == .noOutcome(.processing))
    }

    /// A capture that failed or expired has no outcome, and it is over: it is not shown as
    /// still working.
    @Test(arguments: ["failed", "expired"]) func aCaptureThatEndedWithoutAnOutcomeIsNotWorking(status: String) throws {
        let s = try Self.summary(#"{"runId":"run_A","status":"\#(status)","viewsNeeded":[],"memberActions":[]}"#)
        #expect(s.state == .ended(CaptureResult.Status(rawValue: status)))
        #expect(s.message == nil && s.prompts.isEmpty)
    }

    @Test func manualReviewKeepsTheServersWordsAndNoInventedChecks() throws {
        let s = try Self.summary(#"""
        {"runId":"run_A","status":"manual_review","viewsNeeded":[],"memberActions":[],
         "outcome":{"kind":"manual_review","profile":"C","message":"RECEIVER TEST: a person will look at this.","viewsNeeded":[],"reasons":[]}}
        """#)
        #expect(s.state == .decided(.manualReview))
        #expect(s.message == "RECEIVER TEST: a person will look at this.")
        #expect(s.criteria == nil)
        #expect(s.arUnavailable == .notEligible(.manualReview))
    }

    @Test func aMissingViewShowsItsPromptVerbatim() throws {
        let s = try Self.summary("""
        {"runId":"run_A","status":"needs_views","viewsNeeded":[\(Self.prompt)],"memberActions":[],
         "outcome":{"kind":"needs_more_photos","profile":"C","message":"One more view, please.","viewsNeeded":[\(Self.prompt)],"reasons":[]}}
        """)
        #expect(s.state == .decided(.needsMorePhotos))
        #expect(s.prompts.map(\.title) == ["Step back and show the wall"])
        #expect(s.prompts.map(\.body) == ["Include the ground under the meter."])
    }

    /// An eligible-shaped outcome from an unverified server stays without AR, and without the
    /// criteria it didn't send.
    @Test func eligibleWithoutCriteriaStaysUnverifiedAndOffAR() throws {
        let s = try Self.summary(#"""
        {"runId":"run_A","status":"complete","viewsNeeded":[],"memberActions":[],
         "outcome":{"kind":"eligible","profile":"C","message":"A spot fits.","viewsNeeded":[],"reasons":[],"arkitEpoch":"e1",
          "recommendedPlacement":{"wallId":"w1","startSM":1.2,"confidence":0.8,
           "boxArkitWorld":{"pose":[1,0,0,0,0,1,0,0,0,0,1,0,0.5,0,-2,1],"size":[0.79,1.0,0.56]}}}}
        """#)
        #expect(s.state == .decided(.eligible))
        #expect(s.criteria == nil)
        #expect(!s.analysisVerified)
        #expect(s.arUnavailable == .analysisUnverified)
    }

    @Test func anUnknownStatusStaysReadable() throws {
        let s = try Self.summary(#"{"runId":"run_A","status":"paused_for_audit","viewsNeeded":[],"memberActions":[]}"#)
        #expect(s.state == .working(.unknown("paused_for_audit")))
        #expect(s.arUnavailable == .noOutcome(.unknown("paused_for_audit")))
    }

    /// Even a verified result doesn't place a box for another session, capture or run.
    @Test(arguments: [
        CaptureResult.Association(sessionID: "session-B", captureID: "cap_A", runID: "run_A", epoch: "e1"),
        CaptureResult.Association(sessionID: "session-A", captureID: "cap_B", runID: "run_A", epoch: "e1"),
        CaptureResult.Association(sessionID: "session-A", captureID: "cap_A", runID: "run_B", epoch: "e1"),
    ])
    func aResultForAnotherScanPlacesNothing(_ association: CaptureResult.Association) throws {
        let s = try Self.summary(#"""
        {"runId":"run_A","status":"complete","viewsNeeded":[],"memberActions":[],
         "outcome":{"kind":"eligible","profile":"C","message":"A spot fits.","viewsNeeded":[],"reasons":[],"arkitEpoch":"e1",
          "recommendedPlacement":{"wallId":"w1","startSM":1.2,"confidence":0.8,
           "boxArkitWorld":{"pose":[1,0,0,0,0,1,0,0,0,0,1,0,0.5,0,-2,1],"size":[0.79,1.0,0.56]}}}}
        """#, association: association, analysis: .verified)
        guard case .mismatch? = s.arUnavailable else { Issue.record("placed a box for another scan: \(String(describing: s.arUnavailable))"); return }
    }
}

/// The coordinator fetches the result through the real uploader and binds it to its own session.
@Suite(.serialized) @MainActor struct CaptureResultBindingTests {
    @Test func theFetchedResultIsBoundToThisScanAndStaysUnverified() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "result-binding-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.resultMessage = "RECEIVER TEST: distinctive message 7f3" }
        let coordinator = CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(
            endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: root.appending(path: "Captures")))
        var results: [CaptureResult.Record] = []
        coordinator.onResult = { results.append($0) }
        try await NativeCaptureFixture(folder: root.appending(path: "store")).run(coordinator)
        for _ in 0..<100 where results.isEmpty { try await Task.sleep(for: .milliseconds(20)) }

        let session = try #require(coordinator.session)
        let record = try #require(results.first)
        #expect(results.count == 1)
        let state = await session.uploader!.snapshot
        #expect(record.association == .init(sessionID: session.localID, captureID: state.captureID!, runID: state.finalized!.runID, epoch: "e1"))
        #expect(record.response.outcome?.message == "RECEIVER TEST: distinctive message 7f3")
        #expect(record.analysis == .unverified)
        #expect(session.result == record)
    }

    /// A result arriving for a session that has ended (a new scan started) is never published.
    @Test func aResultForAnEndedSessionIsDropped() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "result-stale-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["GET captures/result"] }
        let fixture = NativeCaptureFixture(folder: root.appending(path: "store"))
        let coordinator = CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(
            endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: root.appending(path: "Captures")))
        var results: [CaptureResult.Record] = []
        coordinator.onResult = { results.append($0) }
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 3))
        coordinator.captureEnded(acceptedCloseUpAt: nil)
        for _ in 0..<500 where server.requests("GET captures/result").isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        let old = try #require(coordinator.session)
        coordinator.newWorld("start over", recording: fixture.recording, newScan: true)
        server.release("GET captures/result")
        try await Task.sleep(for: .milliseconds(500))
        #expect(results.isEmpty)
        #expect(old.result == nil)
    }
}
