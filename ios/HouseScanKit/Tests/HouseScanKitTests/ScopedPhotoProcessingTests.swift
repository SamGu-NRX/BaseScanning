import Foundation
import HouseScanKit
import Synchronization
import Testing

/// `FixtureCaptureHTTP` whose register answers sign each storage upload with an Authorization
/// header, as a misconfigured service might.
final class AuthorizingStorageHTTP: CaptureHTTP, Sendable {
    let inner: FixtureCaptureHTTP

    init(_ inner: FixtureCaptureHTTP) {
        self.inner = inner
    }

    func send(_ request: URLRequest) async throws -> HTTPReply {
        let reply = try await inner.send(request)
        guard request.httpMethod == "POST", request.url?.lastPathComponent == "files",
              var body = try JSONSerialization.jsonObject(with: reply.body) as? [String: Any],
              let files = body["files"] as? [[String: Any]] else { return reply }
        body["files"] = files.map { file -> [String: Any] in
            guard var upload = file["upload"] as? [String: Any], var headers = upload["headers"] as? [String: String] else { return file }
            headers["Authorization"] = "Bearer storage"
            upload["headers"] = headers
            var signed = file
            signed["upload"] = upload
            return signed
        }
        return HTTPReply(status: reply.status, body: try JSONSerialization.data(withJSONObject: body), retryAfter: reply.retryAfter)
    }

    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        try await inner.upload(request, file: file)
    }
}

/// The scoped credential boundary's refusals as photo processing sees them: the real
/// `ScopedCaptureHTTP` and `CaptureUploader` end the upload with their code at status 0, and the
/// scan ends in the setup state, never as the homeowner's scan being refused.
@Suite(.serialized) @MainActor struct ScopedPhotoProcessingTests {
    struct NoCredential: Error {}

    let root = FileManager.default.temporaryDirectory.appending(path: "scoped-photo-\(UUID().uuidString)")

    func controller(_ http: any CaptureHTTP) -> PhotoProcessingController {
        var policy = CaptureUploader.Policy()
        policy.eventsWait = 0
        policy.firstDelay = 0.01
        policy.maxDelay = 0.02
        let environment = CaptureSessionCoordinator.Environment(
            endpoint: FixtureCaptureHTTP.base, sends: true, http: http, capturesFolder: root.appending(path: "Captures"),
            sessionInfo: { packetID, video in SyntheticCapture.sessionInfo(packetID: packetID, video: video, appVersion: "test") },
            device: .init(model: "synthetic", systemVersion: "synthetic", appVersion: "test"), tier: .arkit, policy: policy)
        return PhotoProcessingController(setup: .ready(environment, standIn: true))
    }

    func scoped(base: URL = FixtureCaptureHTTP.base, credential: @escaping ScopedCaptureHTTP.Credential, inner: any CaptureHTTP) throws
        -> ScopedCaptureHTTP
    {
        ScopedCaptureHTTP(scope: try CaptureAPIScope(base: base), credential: credential, inner: inner)
    }

    func until(_ seconds: Double = 30, _ condition: () -> Bool) async throws -> Bool {
        let deadline = ContinuousClock.now + .seconds(seconds)
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
        return condition()
    }

    func run(_ controller: PhotoProcessingController) async throws {
        let capture = SyntheticCapture.standard
        controller.beginScan(ScanContext(scanID: "scan", profile: controller.profile, recordingSessionID: "rec"), recording: capture.recording)
        controller.answerConsent(true)
        for photo in try capture.photos(in: root.appending(path: "photos")) { controller.kept(photo) }
        controller.captureEnded(acceptedCloseUpAt: capture.acceptedCloseUpAt)
    }

    @Test(arguments: [PhotoSetupProblem.destinationRefused, .credentialUnavailable, .credentialMalformed, .storageAuthorization])
    func eachBoundaryRefusalEndsInTheSetupState(problem: PhotoSetupProblem) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let fixture = FixtureCaptureHTTP(answer: .candidate)
        let http: ScopedCaptureHTTP
        switch problem {
        case .destinationRefused:
            let elsewhere = try #require(URL(string: "https://elsewhere.invalid/v1"))
            http = try scoped(base: elsewhere, credential: { "token" }, inner: fixture)
        case .credentialUnavailable:
            http = try scoped(credential: { throw NoCredential() }, inner: fixture)
        case .credentialMalformed:
            http = try scoped(credential: { "not a token" }, inner: fixture)
        case .storageAuthorization:
            http = try scoped(credential: { "token" }, inner: AuthorizingStorageHTTP(fixture))
        }
        let controller = controller(http)
        try await run(controller)
        try #require(try await until { controller.status?.isFinal == true }, "\(String(describing: controller.status))")
        let step = problem == .storageAuthorization ? "put" : "create"
        #expect(controller.status?.stage == .ended(.setupRefused(step: step, problem)))
        #expect(controller.status?.consent == .granted)
        // Refused on the phone: nothing reached the API, or past register for the storage case.
        if problem == .storageAuthorization {
            #expect(!fixture.routes.contains("PUT upload") && !fixture.routes.contains("POST captures/finalize"))
        } else {
            #expect(fixture.routes.isEmpty)
        }
        controller.endScan(recording: SyntheticCapture.standard.recording)
    }

    /// A credential that stops being available once the service has the whole capture, so the
    /// refusal comes at the result read, after the service may have processed the scan. The scan
    /// ends in the same setup state, at the result step.
    @Test func aRefusalAtTheResultStepIsTheSameSetupState() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let fixture = FixtureCaptureHTTP(answer: .candidate)
        // The fixture lists a route once the request got past the boundary, so the events poll
        // that finds the run finished gets its credential, and the result read after it doesn't.
        let http = try scoped(credential: {
            if fixture.routes.contains("GET captures/events") { throw NoCredential() }
            return "token"
        }, inner: fixture)
        let controller = controller(http)
        try await run(controller)
        try #require(try await until { controller.status?.isFinal == true }, "\(String(describing: controller.status))")
        #expect(controller.status?.stage == .ended(.setupRefused(step: "result", .credentialUnavailable)))
        #expect(fixture.routes.contains("POST captures/finalize"))
        #expect(!fixture.routes.contains("GET captures/result"))
        controller.endScan(recording: SyntheticCapture.standard.recording)
    }

    /// The homeowner stops sending while the credential is being fetched, and the provider then
    /// fails: the scan ends withdrawn, never in the setup state.
    @Test func aWithdrawalOutranksARefusalThatFollowsIt() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let fixture = FixtureCaptureHTTP(answer: .candidate)
        let asked = Mutex(0)
        let http = try scoped(credential: {
            asked.withLock { $0 += 1 }
            // Waits without watching for cancellation, then fails.
            await Task.detached { try? await Task.sleep(for: .milliseconds(400)) }.value
            throw NoCredential()
        }, inner: fixture)
        let controller = controller(http)
        controller.beginScan(
            ScanContext(scanID: "scan", profile: controller.profile, recordingSessionID: "rec"), recording: SyntheticCapture.standard.recording)
        controller.answerConsent(true)
        try #require(try await until(10) { asked.withLock { $0 } > 0 }, "the credential was never asked for")
        controller.stopSending()
        #expect(controller.status?.stage == .ended(.withdrawn(recorded: true)))
        try await Task.sleep(for: .seconds(1))
        #expect(controller.status?.stage == .ended(.withdrawn(recorded: true)))
        #expect(fixture.routes.isEmpty)
        controller.endScan(recording: SyntheticCapture.standard.recording)
    }
}
