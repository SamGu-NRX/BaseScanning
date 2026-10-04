import Foundation
import HouseScanKit
import Synchronization
import Testing

@Suite struct ScanContextTests {
    let profile = ProcessingProfile(backend: .photoProcessing, answers: .standIn, origin: FixtureCaptureHTTP.base)

    @Test func aWorldResetKeepsTheScanAndItsProfileButStartsANewSpatialSession() {
        let first = ScanContext(scanID: "scan-a", profile: profile, recordingSessionID: "rec-1")
        let second = first.inNewWorld(recordingSessionID: "rec-2")
        #expect(second.scanID == first.scanID)
        #expect(second.profile == first.profile)
        #expect(first.spatial == SpatialSession(recordingSessionID: "rec-1", worldEpoch: 0))
        #expect(second.spatial == SpatialSession(recordingSessionID: "rec-2", worldEpoch: 1))
        #expect(second.inNewWorld(recordingSessionID: "rec-3").spatial.worldEpoch == 2)
    }

    @Test func theStoredChoiceReadsBackAndAnUnknownOneReadsAsTheDefault() throws {
        let suite = "processing-backend-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        #expect(ProcessingBackendPreference.read(defaults) == .legacy)
        ProcessingBackendPreference.write(.photoProcessing, to: defaults)
        #expect(ProcessingBackendPreference.read(defaults) == .photoProcessing)
        defaults.set("onDevice", forKey: ProcessingBackendPreference.key)
        #expect(ProcessingBackendPreference.read(defaults) == .legacy)
    }
}

@Suite struct PhotoCaptureBindingTests {
    static let world = SpatialSession(recordingSessionID: "rec-1", worldEpoch: 0)
    let binding = PhotoCaptureBinding(
        spatial: world, origin: FixtureCaptureHTTP.base, captureSessionID: "session-1", packetID: "packet-1", epoch: "e1")
    let matching = PhotoCaptureObservation(
        spatial: world, origin: FixtureCaptureHTTP.base, captureSessionID: "session-1", packetID: "packet-1", captureID: "cap_1",
        runID: "run_1")

    func record(
        sessionID: String = "session-1", captureID: String = "cap_1", runID: String = "run_1", responseRun: String = "run_1", epoch: String = "e1"
    ) throws -> CaptureResult.Record {
        let body = """
            {"runId": "\(responseRun)", "status": "manual_review", "viewsNeeded": [], "memberActions": [],
             "outcome": {"kind": "manual_review", "profile": "p", "message": "m", "viewsNeeded": [], "reasons": []}}
            """
        let response = try JSONDecoder().decode(CaptureResult.Response.self, from: Data(body.utf8))
        return CaptureResult.Record(response: response, association: .init(sessionID: sessionID, captureID: captureID, runID: runID, epoch: epoch))
    }

    @Test func theRecordingsOwnAnswerIsAccepted() throws {
        #expect(binding.mismatch(try record(), observed: matching) == nil)
    }

    @Test func anAnswerAssociatedWithAnotherWorldIsRefusedWhateverElseMatches() throws {
        var observed = matching
        observed.spatial = SpatialSession(recordingSessionID: "rec-2", worldEpoch: 1)
        #expect(binding.mismatch(try record(), observed: observed) == .world)
        observed.spatial = SpatialSession(recordingSessionID: "rec-1", worldEpoch: 1)
        #expect(binding.mismatch(try record(), observed: observed) == .world)
        observed.spatial = nil
        #expect(binding.mismatch(try record(), observed: observed) == .world)
    }

    @Test func anAnswerWhoseRunOrLocalAssociationDiffersIsRefused() throws {
        var observed = matching
        observed.origin = URL(string: "https://elsewhere.invalid/v1")
        #expect(binding.mismatch(try record(), observed: observed) == .origin)
        observed = matching
        observed.captureSessionID = "session-2"
        #expect(binding.mismatch(try record(), observed: observed) == .session)
        #expect(binding.mismatch(try record(sessionID: "session-2"), observed: matching) == .session)
        observed = matching
        observed.packetID = "packet-2"
        #expect(binding.mismatch(try record(), observed: observed) == .packet)
        #expect(binding.mismatch(try record(captureID: "cap_2"), observed: matching) == .capture)
        observed = matching
        observed.captureID = nil
        #expect(binding.mismatch(try record(), observed: observed) == .capture)
        #expect(binding.mismatch(try record(runID: "run_2"), observed: matching) == .run)
        #expect(binding.mismatch(try record(responseRun: "run_2"), observed: matching) == .run)
        #expect(binding.mismatch(try record(epoch: "e2"), observed: matching) == .epoch)
    }

    @Test func aRefusalReadsAsTheStageItEndsIn() {
        typealias Stage = PhotoProcessingStatus.Stage
        #expect(Stage.refusal(step: "create", codes: ["schema"], status: 422) == .ended(.refused(step: "create")))
        #expect(Stage.refusal(step: "put", codes: ["storage_refused"], status: 403) == .ended(.refused(step: "put")))
        #expect(Stage.refusal(step: "result", codes: ["result_not_ready"], status: 404) == .ended(.answerNotReady))
        #expect(Stage.refusal(step: "result", codes: ["result_unreadable"], status: 200) == .ended(.answerUnreadable))
        #expect(Stage.refusal(step: "result", codes: ["result_for_another_run"], status: 200) == .ended(.answerMismatch(.run)))
        #expect(Stage.refusal(step: "result", codes: [], status: 500) == .ended(.refused(step: "result")))
        // The boundary's codes at status 0, where no server answered; the same text from a server isn't the phone's setup.
        for problem in PhotoSetupProblem.allCases {
            #expect(Stage.refusal(step: "create", codes: [problem.code], status: 0) == .ended(.setupRefused(step: "create", problem)))
            #expect(Stage.refusal(step: "create", codes: [problem.code], status: 401) == .ended(.refused(step: "create")))
        }
    }
}

/// `FixtureCaptureHTTP` with every request to one route held until `release`, to deliver an
/// answer after the scan it was for has gone.
final class HeldResultHTTP: CaptureHTTP, Sendable {
    private struct Held {
        var open = false
        var waiting: [CheckedContinuation<Void, Never>] = []
    }

    let inner: FixtureCaptureHTTP
    private let held = Mutex(Held())

    init(_ inner: FixtureCaptureHTTP) {
        self.inner = inner
    }

    var waiting: Int { held.withLock { $0.waiting.count } }

    func send(_ request: URLRequest) async throws -> HTTPReply {
        if request.httpMethod == "GET", request.url?.lastPathComponent == "result" {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                let open = held.withLock { state -> Bool in
                    if state.open { return true }
                    state.waiting.append(continuation)
                    return false
                }
                if open { continuation.resume() }
            }
        }
        return try await inner.send(request)
    }

    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        try await inner.upload(request, file: file)
    }

    func release() {
        let waiting = held.withLock { state -> [CheckedContinuation<Void, Never>] in
            state.open = true
            defer { state.waiting = [] }
            return state.waiting
        }
        waiting.forEach { $0.resume() }
    }
}

@Suite(.serialized) @MainActor struct PhotoProcessingControllerTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "photo-processing-\(UUID().uuidString)")
    var fixture: NativeCaptureFixture { NativeCaptureFixture(folder: root.appending(path: "store")) }

    func controller(_ http: any CaptureHTTP) -> PhotoProcessingController {
        var environment = NativeCaptureFixture.environment(endpoint: FixtureCaptureHTTP.base, http: http, captures: root.appending(path: "Captures"))
        // Retries and unreadable answers come round in milliseconds, not the app's seconds.
        environment.policy.firstDelay = 0.01
        environment.policy.maxDelay = 0.02
        return PhotoProcessingController(setup: .ready(environment, standIn: true))
    }

    func context(_ controller: PhotoProcessingController, scan: String = "scan-1", recording: String = "rec-1") -> ScanContext {
        ScanContext(scanID: scan, profile: controller.profile, recordingSessionID: recording)
    }

    /// The scan from the meter tap to the send: the tap, the close-up and five walk photos.
    func capture(_ controller: PhotoProcessingController, consent: Bool?) async throws {
        if let consent { controller.answerConsent(consent) }
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        controller.meterTapped(tap, hit: hit)
        controller.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        for i in 0..<5 { controller.kept(try fixture.photo(at: fixture.start + 3 + Double(i) * 1.5)) }
        await controller.settle()
        controller.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
    }

    func until(_ seconds: Double = 30, _ condition: () -> Bool) async throws -> Bool {
        let deadline = ContinuousClock.now + .seconds(seconds)
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
        return condition()
    }

    @Test func theFixtureWritesTheCoordinatorsEpoch() {
        #expect(FixtureCaptureHTTP.arkitEpoch == CaptureSessionCoordinator.epoch)
    }

    @Test(arguments: [
        FixtureCaptureHTTP.Answer.candidate, .needsViews, .manualReview, .notEligible, .failed, .expired, .notReady, .unreadable, .wrongRun,
    ])
    func eachAnswerReachesTheScanAsItsTypedStage(answer: FixtureCaptureHTTP.Answer) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: answer)
        let controller = controller(http)
        controller.beginScan(context(controller), recording: fixture.recording)
        try await capture(controller, consent: true)
        try #require(try await until { controller.status?.isFinal == true }, "no final stage: \(String(describing: controller.status))")
        let stage = try #require(controller.status?.stage)
        let message = answer.message ?? ""
        switch answer {
        case .candidate:
            // Proposed, never confirmed: AR stays withheld because nobody verified the analysis.
            #expect(stage == .answered(.candidate(.init(message: message, previewMade: false, ar: .withheld(.analysisUnverified)))))
        case .needsViews:
            guard case .answered(.needsMorePhotos(let more)) = stage else {
                Issue.record("expected more photos, got \(stage)")
                return
            }
            #expect(more.prompts.map(\.title) == ["Fixture: show the ground below the meter"])
            #expect(more.message == message)
            let parent = try #require(more.parent)
            #expect(parent.viewIDs == ["view_fixture_1"])
            #expect(parent.profile == controller.profile)
            #expect(parent.captureID.hasPrefix("cap_fixture_"))
            #expect(parent.runID.hasPrefix("run_fixture_"))
            #expect(parent.folder == controller.captureFolder)
        case .manualReview:
            #expect(stage == .answered(.installerReview(message: message)))
        case .notEligible:
            #expect(stage == .answered(.noCandidate(message: message)))
        case .failed:
            #expect(stage == .ended(.processingFailed))
        case .expired:
            #expect(stage == .ended(.expired))
        case .notReady:
            #expect(stage == .ended(.answerNotReady))
        case .unreadable:
            #expect(stage == .ended(.answerUnreadable))
        case .wrongRun:
            #expect(stage == .ended(.answerMismatch(.run)))
        case .refuseCreate, .hold:
            Issue.record("not an argument of this test")
        }
        #expect(controller.status?.consent == .granted)
        #expect(controller.status?.standIn == true)
        #expect(http.routes.contains("POST captures/finalize"))
        controller.endScan(recording: fixture.recording)
    }

    @Test func aRefusedCaptureEndsWithTheStepTheServiceRefused() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .refuseCreate)
        let controller = controller(http)
        controller.beginScan(context(controller), recording: fixture.recording)
        try await capture(controller, consent: true)
        try #require(try await until { controller.status?.isFinal == true })
        #expect(controller.status?.stage == .ended(.refused(step: "create")))
        #expect(!http.routes.contains("POST captures/files"))
        controller.endScan(recording: fixture.recording)
    }

    @Test(arguments: [false, nil] as [Bool?])
    func withoutAYesNothingIsSent(consent: Bool?) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        controller.beginScan(context(controller), recording: fixture.recording)
        #expect(controller.status?.consent == .asking)
        try await capture(controller, consent: consent)
        #expect(controller.status?.stage == .ended(.consentNotGiven))
        let answered: PhotoProcessingStatus.Consent = consent == false ? .declined : .asking
        #expect(controller.status?.consent == answered)
        try await Task.sleep(for: .milliseconds(300))
        #expect(http.routes.isEmpty)
        controller.endScan(recording: fixture.recording)
    }

    @Test(arguments: [true, false])
    func stoppingPartWayStopsSendingAndSaysWhetherThePhoneSavedIt(saved: Bool) async throws {
        let http = FixtureCaptureHTTP(answer: .hold)
        let controller = controller(http)
        controller.beginScan(context(controller), recording: fixture.recording)
        try await capture(controller, consent: true)
        try #require(try await until { controller.status?.stage == .processing })
        let folder = try #require(controller.captureFolder)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path)
            try? FileManager.default.removeItem(at: root)
        }
        // Neither the withdrawal marker nor the saved state's removal can be written.
        if !saved { try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path) }
        controller.stopSending()
        #expect(controller.status?.stage == .ended(.withdrawn(recorded: saved)))
        #expect(controller.status?.consent == .withdrawn(recorded: saved))
        let sent = http.routes.count
        // Longer than the uploader waits between polls of a service that answers at once.
        try await Task.sleep(for: .seconds(2.5))
        #expect(http.routes.count == sent)
        // A second yes doesn't revive the capture.
        controller.answerConsent(true)
        #expect(controller.status?.stage == .ended(.withdrawn(recorded: saved)))
        controller.endScan(recording: fixture.recording)
    }

    @Test func aLateAnswerAfterStartOverNeverReachesTheNextScan() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = HeldResultHTTP(FixtureCaptureHTTP(answer: .candidate))
        let controller = controller(http)
        controller.beginScan(context(controller, scan: "scan-1"), recording: fixture.recording)
        try await capture(controller, consent: true)
        try #require(try await until { http.waiting > 0 }, "the result was never asked for")
        controller.endScan(recording: fixture.recording)
        #expect(controller.status == nil)
        let next = context(controller, scan: "scan-2", recording: "rec-2")
        controller.beginScan(next, recording: fixture.recording)
        http.release()
        try await Task.sleep(for: .milliseconds(500))
        #expect(controller.context == next)
        #expect(controller.status == PhotoProcessingStatus(stage: .capturing(sent: 0), consent: .asking, standIn: true))
        #expect(http.inner.routes.filter { $0 == "POST captures" }.count == 1)
        controller.endScan(recording: fixture.recording)
    }

    @Test func aWorldResetSendsTheNextPacketUnderTheSameYesAndBindsItsAnswerToTheNewWorld() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .manualReview)
        let controller = controller(http)
        let first = context(controller)
        controller.beginScan(first, recording: fixture.recording)
        controller.answerConsent(true)
        controller.kept(try fixture.photo(at: fixture.start + 3))
        await controller.settle()
        let firstFolder = controller.captureFolder
        let next = first.inNewWorld(recordingSessionID: "rec-2")
        controller.worldReset(next, recording: fixture.recording)
        #expect(controller.status?.consent == .granted)
        #expect(controller.status?.stage == .capturing(sent: 0))
        #expect(controller.captureFolder != firstFolder)
        try await capture(controller, consent: nil)
        try #require(try await until { controller.status?.isFinal == true })
        #expect(controller.status?.stage == .answered(.installerReview(message: FixtureCaptureHTTP.Answer.manualReview.message ?? "")))
        #expect(controller.context?.spatial == next.spatial)
        #expect(controller.context?.scanID == first.scanID)
        #expect(http.routes.filter { $0 == "POST captures" }.count == 2)
        controller.endScan(recording: fixture.recording)
    }

    @Test func theScansUploaderOwnsItsFolderAgainstAnyOtherStartOrResume() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .hold)
        let controller = controller(http)
        controller.beginScan(context(controller), recording: fixture.recording)
        controller.answerConsent(true)
        controller.kept(try fixture.photo(at: fixture.start + 3))
        await controller.settle()
        let folder = try #require(controller.captureFolder)
        let create = CaptureAPI.CreateRequest(packetId: "other", tier: .arkit, device: .init(model: "m", systemVersion: "s", appVersion: "a"))
        #expect(throws: CaptureUploader.OwnershipError.alreadyOwned) {
            _ = try CaptureUploader.start(folder: folder, base: FixtureCaptureHTTP.base, http: http, create: create, consentedAt: Date())
        }
        #expect(throws: CaptureUploader.OwnershipError.alreadyOwned) {
            _ = try CaptureUploader.resume(folder: folder, base: FixtureCaptureHTTP.base, http: http)
        }
        controller.endScan(recording: fixture.recording)
    }

    @Test func aBuildWithoutSetupSaysSoAndSendsNothing() throws {
        let controller = PhotoProcessingController(setup: .notSetUp("live sending is off in this build"))
        #expect(controller.profile.answers == .notSetUp("live sending is off in this build"))
        #expect(controller.profile.origin == nil)
        controller.beginScan(context(controller), recording: fixture.recording)
        let ended = PhotoProcessingStatus(stage: .ended(.notSetUp("live sending is off in this build")), consent: .notNeeded, standIn: false)
        #expect(controller.status == ended)
        controller.answerConsent(true)
        controller.captureEnded(acceptedCloseUpAt: nil)
        #expect(controller.status == ended)
        #expect(controller.captureFolder == nil)
    }

    @Test func anEndpointThatMayNotReceiveTheScanIsNotSetUp() {
        var environment = NativeCaptureFixture.environment(
            endpoint: FixtureCaptureHTTP.base, http: FixtureCaptureHTTP(answer: .candidate), captures: root.appending(path: "Captures"))
        environment.sends = false
        let controller = PhotoProcessingController(setup: .ready(environment, standIn: true))
        guard case .notSetUp = controller.profile.answers else {
            Issue.record("a record-only endpoint was offered as set up")
            return
        }
    }

    @Test func aScanWhoseProfileIsNotThisBuildsSendsNothing() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        let elsewhere = ProcessingProfile(backend: .photoProcessing, answers: .service, origin: URL(string: "https://elsewhere.invalid/v1"))
        controller.beginScan(ScanContext(scanID: "scan-1", profile: elsewhere, recordingSessionID: "rec-1"), recording: fixture.recording)
        #expect(controller.status?.stage == .ended(.notSetUp("the scan's processing setup is not this build's")))
        try await capture(controller, consent: true)
        try await Task.sleep(for: .milliseconds(300))
        #expect(http.routes.isEmpty)
        controller.endScan(recording: fixture.recording)
    }
}
