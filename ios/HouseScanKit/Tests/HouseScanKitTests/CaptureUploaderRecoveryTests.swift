import Foundation
import HouseScanKit
import Synchronization
import Testing

/// Two ways an upload could keep going on limits or state that no longer hold. A server that
/// lost the capture gets a new run, so the old run's retry limits must not end it early. A state
/// save that fails ends the upload in this process, so it never sends what the saved state
/// doesn't record.
///
/// The loopback decides an answer when a request arrives and only delays sending it while its
/// route is held, so these tests change the server between one request and the next.
@Suite struct CaptureUploaderRecoveryTests {
    typealias Rig = CaptureUploaderTests.Rig

    /// Waits until at least `count` requests on `route` are parked. Like `settles`, the budget is
    /// this waiter's own turns, not wall time, and the answer is read after the last wait.
    static func parked(_ server: LoopbackCaptureAPI, _ route: String, count: Int = 1, within seconds: Int = 20) async -> Bool {
        let ready = { server.state.withLock { ($0.parked[route] ?? []).count >= count } }
        for _ in 0..<(seconds * 100) {
            if ready() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return ready()
    }

    /// Sends the answers parked on `route` and keeps holding it.
    static func deliver(_ server: LoopbackCaptureAPI, _ route: String) {
        let waiting = server.state.withLock { $0.parked.removeValue(forKey: route) ?? [] }
        waiting.forEach { $0() }
    }

    static func saved(_ rig: Rig) throws -> CaptureUploadState {
        try CaptureUploadState.load(from: CaptureUploader.stateURL(in: rig.capture.folder))
    }

    static func routes(_ server: LoopbackCaptureAPI) -> [String] { server.state.withLock { $0.log.map(LoopbackCaptureAPI.route) } }

    // MARK: A recreated capture is a new run

    enum PutFault: String, CaseIterable, Sendable {
        /// A second 400 BadDigest for a file whose one resend was spent.
        case badDigest
        /// A refused PUT past `maxRefusedPuts`.
        case refused
    }

    /// Every file of one sealed photo spends its PUT limits on the first server capture: a 400
    /// BadDigest (its one resend), a 403 refusal and a 200, three PUTs against `maxRefusedPuts` 3.
    /// The server then loses the capture. The same fault once more for every file on the new
    /// capture is retried, not the end of the upload, and the state a relaunch would load already
    /// holds the new run's counts.
    @Test(arguments: PutFault.allCases)
    func aRecreatedCaptureGetsFreshPutLimits(fault: PutFault) async throws {
        var policy = Rig.fast
        policy.maxRefusedPuts = 3
        let rig = try Rig(policy: policy)
        defer { rig.cleanUp() }
        let files = try await rig.capture.sealImages(count: 1)
        // One register answer and one round of PUTs covers every file, so each fault below lands
        // once on each file.
        let n = files.count
        try #require(n <= policy.maxConcurrentPuts)
        rig.server.state.withLock {
            $0.badDigestPuts = n
            $0.expireNextPuts = n
            $0.held = ["PUT upload"]
        }
        await rig.uploader.add(files)
        // Rounds one and two go through. A round starts only once the one before is answered, so
        // a parked PUT past the 2n-th belongs to round three.
        while true {
            try #require(await Self.parked(rig.server, "PUT upload"))
            if rig.server.requests("PUT upload").count > 2 * n { break }
            Self.deliver(rig.server, "PUT upload")
        }
        try #require(await Self.parked(rig.server, "PUT upload", count: n))
        #expect(rig.server.requests("PUT upload").count == 3 * n)

        // Round three's 200s are decided. The server forgets the capture before the commit.
        rig.server.state.withLock { s in
            s.captures = [:]
            s.byPacket = [:]
            switch fault {
            case .badDigest: s.badDigestPuts = n
            case .refused: s.expireNextPuts = n
            }
            s.held = ["POST captures"]
        }
        Self.deliver(rig.server, "PUT upload")
        try #require(await Self.parked(rig.server, "POST captures"))
        let gone = try Self.saved(rig)
        #expect(gone.captureID == nil)
        #expect(gone.files.values.map(\.attempts) == Array(repeating: 0, count: n))
        #expect(gone.files.values.allSatisfy { $0.phase == .queued })
        rig.server.release("POST captures")
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(await rig.uploader.snapshot.end == nil)

        try await rig.finishAndSeal()
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        #expect(rig.server.requests("POST captures").count == 2)
    }

    /// The first server capture loses its finalize and follows `retry_finalize` once, the whole
    /// limit. The server then loses the capture. A lost finalize on the new capture is sent again,
    /// and the saved state reads 0 follows before the new capture exists.
    @Test func aRecreatedCaptureGetsAFreshFinalizeLimit() async throws {
        var policy = Rig.fast
        policy.maxFinalizeRetries = 1
        let rig = try Rig(policy: policy)
        defer { rig.cleanUp() }
        rig.server.state.withLock {
            $0.loseNextFinalize = true
            $0.held = ["POST captures/finalize"]
        }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        try await rig.finishAndSeal()
        try #require(await Self.parked(rig.server, "POST captures/finalize"))
        Self.deliver(rig.server, "POST captures/finalize")
        try #require(await Self.parked(rig.server, "POST captures/finalize"))
        #expect(try Self.saved(rig).finalizeRetries == 1)

        // The second finalize is accepted. The server forgets the capture before the next poll.
        rig.server.state.withLock { s in
            s.captures = [:]
            s.byPacket = [:]
            s.loseNextFinalize = true
            s.held = ["POST captures"]
        }
        Self.deliver(rig.server, "POST captures/finalize")
        try #require(await Self.parked(rig.server, "POST captures"))
        let gone = try Self.saved(rig)
        #expect(gone.captureID == nil)
        #expect(gone.finalizeRetries == 0)
        rig.server.release("POST captures")
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        #expect(rig.server.requests("POST captures").count == 2)
        #expect(rig.server.requests("POST captures/finalize").count == 4)
    }

    // MARK: A state save that fails

    /// A failed save ends the upload in this process with the local code and status 0. The
    /// observer hears it, nothing more is sent or retried, the saved file keeps its last good
    /// bytes, and this process doesn't resume the folder from those older bytes.
    @Test func aFailedSaveEndsTheUploadAndSendsNothingMore() async throws {
        let sleeps = ScopedCaptureUploadTests.Sleeps()
        var rig = try Rig(sleep: sleeps.record)
        defer { rig.cleanUp() }
        let folder = rig.capture.folder
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        let statuses = Mutex<[CaptureUploadStatus]>([])
        await rig.uploader.observe { status in statuses.withLock { $0.append(status) } }
        rig.server.state.withLock { $0.held = ["POST captures"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        try #require(await Self.parked(rig.server, "POST captures"))

        let url = CaptureUploader.stateURL(in: folder)
        let before = try Data(contentsOf: url)
        // A folder that takes no new file: the atomic save can't write its replacement.
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        rig.server.release("POST captures")
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        let unsaved = CaptureUploadState.End.failed(step: "save", codes: ["local_state_unsaved"], status: 0)
        #expect(await rig.uploader.snapshot.end == unsaved)
        let last = try #require(statuses.withLock { $0.last })
        #expect(last.phase == .failed)
        #expect(last.detail == "save 0 local_state_unsaved")
        #expect(last.retryingAt == nil)
        #expect(try Data(contentsOf: url) == before)

        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(Self.routes(rig.server) == ["POST captures"])
        #expect(sleeps.isEmpty)

        // Writable again, the folder still holds the older saved yes. Once the uploader is gone,
        // this process doesn't pick that up and send the capture again.
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path)
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        #expect(try CaptureUploader.resume(folder: folder, base: rig.server.base, http: rig.http, policy: Rig.fast, sleep: { _ in }) == nil)
        #expect(try Data(contentsOf: url) == before)
        #expect(Self.routes(rig.server) == ["POST captures"])
    }

    enum Order: String, CaseIterable, Sendable {
        /// The withdrawal lands while the create is in flight; the reply's save then fails.
        case withdrawalFirst
        /// The save fails and ends the upload; the withdrawal comes after.
        case saveFailureFirst
    }

    /// A withdrawal outranks the save failure in either order. The folder is unwritable, so the
    /// withdrawal itself can't be recorded (`notRecorded`) and no save succeeds. The coordinator
    /// then abandons the upload as withdrawn, as it does on every withdrawal: the upload ends as
    /// withdrawn, not as a local save failure, and sends nothing more.
    @Test(arguments: Order.allCases)
    func aWithdrawalOutranksAFailedSave(order: Order) async throws {
        let sleeps = ScopedCaptureUploadTests.Sleeps()
        let rig = try Rig(sleep: sleeps.record)
        defer { rig.cleanUp() }
        let folder = rig.capture.folder
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        rig.server.state.withLock { $0.held = ["POST captures"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        try #require(await Self.parked(rig.server, "POST captures"))

        let url = CaptureUploader.stateURL(in: folder)
        let before = try Data(contentsOf: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        if order == .saveFailureFirst {
            rig.server.release("POST captures")
            try #require(await CaptureUploaderTests.settles(rig.uploader))
            #expect(await rig.uploader.snapshot.end == .failed(step: "save", codes: ["local_state_unsaved"], status: 0))
        }
        let record = rig.uploader.withdrawConsent()
        guard case .notRecorded = record else {
            Issue.record("expected the withdrawal to go unrecorded in an unwritable folder, got \(record)")
            return
        }
        if order == .withdrawalFirst { rig.server.release("POST captures") }
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        await rig.uploader.abandon(CaptureUploader.withdrawnReason)

        #expect(await rig.uploader.snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
        #expect(await rig.uploader.status.phase == .abandoned)
        #expect(try Data(contentsOf: url) == before)
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(Self.routes(rig.server) == ["POST captures"])
        #expect(sleeps.isEmpty)
    }

    /// Counts what reaches the network under `ScopedCaptureHTTP`.
    final class CountingHTTP: CaptureHTTP {
        let inner: any CaptureHTTP
        let sends = Mutex(0)
        init(_ inner: any CaptureHTTP) { self.inner = inner }
        func send(_ request: URLRequest) async throws -> HTTPReply {
            sends.withLock { $0 += 1 }
            return try await inner.send(request)
        }
        func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
            sends.withLock { $0 += 1 }
            return try await inner.upload(request, file: file)
        }
    }

    /// A save that fails while a request waits for its credential stops that request. The create
    /// waits on a provider that ignores cancellation. Meanwhile `add` saves into an unwritable
    /// folder and fails. When the provider then returns a good token, nothing reaches the
    /// network, nothing is retried, and the upload ends as the local save failure, not as a
    /// withdrawal.
    @Test func aFailedSaveStopsARequestWaitingForItsCredential() async throws {
        let sleeps = ScopedCaptureUploadTests.Sleeps()
        let gate = ScopedCaptureHTTPTests.Gate()
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.expectedBearer = ScopedCaptureUploadTests.token }
        let network = CountingHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10))
        let http = ScopedCaptureHTTP(
            scope: try CaptureAPIScope(base: server.base, transport: .loopbackHTTP),
            credential: {
                await gate.wait()
                return ScopedCaptureUploadTests.token
            },
            inner: network)
        let rig = try Rig(server: server, http: http, sleep: sleeps.record)
        defer { rig.cleanUp() }
        let folder = rig.capture.folder
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        let images = try await rig.capture.sealImages(count: 1)
        await rig.uploader.kick()
        await gate.entered()

        let url = CaptureUploader.stateURL(in: folder)
        let before = try Data(contentsOf: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        await rig.uploader.add(images)
        gate.release()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(network.sends.withLock { $0 } == 0)
        #expect(server.state.withLock { $0.log.isEmpty })
        #expect(await rig.uploader.snapshot.end == .failed(step: "save", codes: ["local_state_unsaved"], status: 0))
        #expect(await rig.uploader.status.phase == .failed)
        #expect(await rig.uploader.status.retryingAt == nil)
        #expect(sleeps.isEmpty)
        #expect(try Data(contentsOf: url) == before)
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(network.sends.withLock { $0 } == 0)
    }

    enum UnsavedEnd: String, CaseIterable, Sendable {
        /// The result arrives and ends the upload, and that save fails.
        case finished
        /// The server's answer ends the upload as refused, and that save fails.
        case refused
    }

    /// A withdrawal outranks an end this process reached but couldn't save. The end stays when
    /// its save fails; the folder is unwritable, so the withdrawal goes unrecorded too. The
    /// coordinator's `abandon(withdrawnReason)` must still end the upload as withdrawn.
    ///
    /// This holds at the library and coordinator boundary. The app doesn't offer Stop sending
    /// once its screen shows a final state, so no button reaches this path today.
    @Test(arguments: UnsavedEnd.allCases)
    func aWithdrawalOutranksAnEndWhoseSaveFailed(end: UnsavedEnd) async throws {
        let sleeps = ScopedCaptureUploadTests.Sleeps()
        let rig = try Rig(sleep: sleeps.record)
        defer { rig.cleanUp() }
        let folder = rig.capture.folder
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        let route: String
        switch end {
        case .finished:
            route = "GET captures/result"
            rig.server.state.withLock { $0.held = [route] }
            await rig.uploader.add(try await rig.capture.sealImages(count: 1))
            try #require(await CaptureUploaderTests.settles(rig.uploader))
            try await rig.finishAndSeal()
        case .refused:
            route = "POST captures/files"
            // A register answer with a plain-http storage URL to another host, which ends the upload.
            rig.server.state.withLock {
                $0.held = [route]
                $0.uploadURLOverride = "http://example.com/upload/x"
            }
            await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        }
        try #require(await Self.parked(rig.server, route))

        let url = CaptureUploader.stateURL(in: folder)
        let before = try Data(contentsOf: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        rig.server.release(route)
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        let reached = await rig.uploader.snapshot.end
        switch end {
        case .finished: #expect(reached == .finished(status: "manual_review"))
        case .refused: #expect(reached == .failed(step: "register", codes: ["upload_url_insecure"], status: 200))
        }
        #expect(try Data(contentsOf: url) == before)
        let sent = Self.routes(rig.server)

        let record = rig.uploader.withdrawConsent()
        guard case .notRecorded = record else {
            Issue.record("expected the withdrawal to go unrecorded in an unwritable folder, got \(record)")
            return
        }
        await rig.uploader.abandon(CaptureUploader.withdrawnReason)
        #expect(await rig.uploader.snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
        #expect(await rig.uploader.status.phase == .abandoned)
        #expect(try Data(contentsOf: url) == before)
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(Self.routes(rig.server) == sent)
    }
}
