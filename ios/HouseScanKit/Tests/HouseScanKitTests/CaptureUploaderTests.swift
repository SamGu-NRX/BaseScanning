import Foundation
import HouseScanKit
import Synchronization
import Testing

/// The uploader against `LoopbackCaptureAPI` over real HTTP, through the same `URLSession`
/// transport the app uses. Retries wait 10 ms instead of seconds.
@Suite(.serialized) struct CaptureUploaderTests {
    struct Rig {
        let server: LoopbackCaptureAPI
        let root: URL
        let capture: SyntheticCapture04
        private var ownedUploader: CaptureUploader?
        var uploader: CaptureUploader {
            guard let ownedUploader else { preconditionFailure("The test released this uploader to simulate process exit") }
            return ownedUploader
        }

        mutating func releaseUploader() { ownedUploader = nil }
        let http: any CaptureHTTP

        init(
            packetID: String = UUID().uuidString, appVersion: String = "test (1)", server: LoopbackCaptureAPI? = nil,
            http: any CaptureHTTP = URLSessionCaptureHTTP.ephemeral(timeout: 10), policy: CaptureUploader.Policy = Rig.fast,
            sleep: @escaping @Sendable (Double) async throws -> Void = { _ in try await Task.sleep(for: .milliseconds(10)) },
            now: @escaping @Sendable () -> Date = { Date() }
        ) throws {
            self.http = http
            self.server = try server ?? LoopbackCaptureAPI()
            root = FileManager.default.temporaryDirectory.appending(path: "uploader-\(UUID().uuidString)")
            capture = try SyntheticCapture04(folder: root.appending(path: "packet"), packetID: packetID)
            ownedUploader = try CaptureUploader.start(
                folder: capture.folder, base: self.server.base, http: http,
                create: .init(packetId: packetID, tier: .arkit, device: .init(model: "iPhone15,4", systemVersion: "26.0", appVersion: appVersion)),
                consentedAt: Date(), policy: policy, now: now, sleep: sleep)
        }

        static var fast: CaptureUploader.Policy {
            var policy = CaptureUploader.Policy()
            policy.eventsWait = 0
            return policy
        }

        func finishAndSeal() async throws {
            let (streams, packet) = try await capture.finish()
            await uploader.seal(packet: packet, files: streams)
        }

        func cleanUp() { try? FileManager.default.removeItem(at: root) }
    }

    /// Photos go up during the capture; the frozen packet finalizes early, the streams follow, and
    /// the capture ends with a result for its own run.
    @Test func joinedPathCommitsPhotosBeforeTheCaptureEnds() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        let images = try await rig.capture.sealImages()
        await rig.uploader.add(images)
        await rig.uploader.settled()

        let during = await rig.uploader.snapshot
        #expect(during.committedCount == images.count)
        #expect(during.packet == nil)
        #expect(rig.server.requests("POST captures/finalize").isEmpty)

        try await rig.finishAndSeal()
        await rig.uploader.settled()
        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "manual_review"))
        #expect(done.finalized?.status == "awaiting_files")
        #expect(done.finalized?.missing.sorted() == [
            "streams/accelerometer_raw.csv.gz", "streams/arkit_poses.csv.gz", "streams/gyroscope_raw.csv.gz", "streams/imu_raw.csv.gz",
        ])
        #expect(done.committedCount == images.count + 4)
        #expect(done.result != nil)
        let marks = done.marks
        #expect(try #require(marks["firstCommit"]) < #require(marks["sealed"]))
        #expect(try #require(marks["sealed"]) <= #require(marks["lastCommit"]))

        let puts = rig.server.requests("PUT upload")
        #expect(puts.count == images.count + 4)
        #expect(puts.allSatisfy { $0.headers["authorization"] == nil && $0.headers["content-md5"] != nil })
        #expect(rig.server.requests("POST captures").count == 1)
    }

    /// A create and a finalize whose answers are lost are sent again as the same bytes, and the
    /// server keeps one capture.
    @Test func lostAnswersAreReplayedWithTheFrozenBodies() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.dropNext = ["POST captures", "POST captures/finalize"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        await rig.uploader.settled()
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let creates = rig.server.requests("POST captures")
        let finals = rig.server.requests("POST captures/finalize")
        #expect(creates.count == 2)
        #expect(Set(creates.map(\.body)).count == 1)
        #expect(finals.count == 2)
        #expect(Set(finals.map(\.body)).count == 1)
        #expect(rig.server.state.withLock { $0.captures.count } == 1)
        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
    }

    /// A relaunch resumes the saved capture: same packet and capture ids, no second create, and the
    /// old process's attempt id is replaced.
    @Test func relaunchResumesTheSameCapture() async throws {
        var rig = try Rig()
        defer { rig.cleanUp() }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        await rig.uploader.settled()
        let before = await rig.uploader.snapshot

        // The old process releases every uploader before the new one resumes its saved state.
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        let resumed = try #require(try CaptureUploader.resume(
            folder: rig.capture.folder, base: rig.server.base, http: rig.http, policy: Rig.fast, sleep: { _ in }))
        let after = await resumed.snapshot
        #expect(after.packetID == before.packetID)
        #expect(after.captureID == before.captureID)
        #expect(after.createBody == before.createBody)
        #expect(after.attemptID != before.attemptID)

        let (streams, packet) = try await rig.capture.finish()
        await resumed.seal(packet: packet, files: streams)
        await resumed.settled()
        #expect(await resumed.snapshot.end == .finished(status: "manual_review"))
        #expect(rig.server.requests("POST captures").count == 1)
    }

    /// The same packet id with a different create body is the server's idempotency conflict; the
    /// uploader stops instead of retrying it.
    @Test func aChangedCreateBodyIsRefusedNotRetried() async throws {
        let packetID = UUID().uuidString
        let first = try Rig(packetID: packetID)
        defer { first.cleanUp() }
        await first.uploader.kick()
        await first.uploader.settled()
        #expect(await first.uploader.snapshot.captureID != nil)

        let second = try Rig(packetID: packetID, appVersion: "test (2)", server: first.server)
        defer { second.cleanUp() }
        await second.uploader.kick()
        await second.uploader.settled()
        #expect(await second.uploader.snapshot.end == .failed(step: "create", codes: ["idempotency_conflict"], status: 409))
        #expect(first.server.requests("POST captures").count == 2)
    }

    /// A storage URL answered 403 (expired) is registered again and the file sent to the new URL.
    @Test func anExpiredURLIsFetchedAgain() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.expireNextPuts = 1 }
        let images = try await rig.capture.sealImages(count: 1)
        await rig.uploader.add(images)
        await rig.uploader.settled()
        let snapshot = await rig.uploader.snapshot
        #expect(snapshot.committedCount == images.count)
        #expect(rig.server.requests("PUT upload").count == images.count + 1)
        let registered = rig.server.requests("POST captures/files").flatMap { request -> [String] in
            let body = try? JSONDecoder().decode(CaptureAPI.RegisterRequest.self, from: request.body)
            return body?.files.map(\.path) ?? []
        }
        #expect(registered.count == images.count + 1)
    }

    /// Only paths the commit names as committed advance; notFound and mismatch go up again.
    @Test func mixedCommitAnswersAdvanceOnlyCommittedFiles() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        let images = try await rig.capture.sealImages(count: 2)
        // Priority order: the still, then k00001, then k00002.
        let paths = ["stills/meter_close.jpg", "keyframes/k00001.jpg", "keyframes/k00002.jpg"]
        #expect(Set(images.map(\.path)) == Set(paths))
        rig.server.state.withLock { $0.commitOverride = (committed: [paths[0]], notFound: [paths[1]], mismatch: [paths[2]]) }
        let seen = Mutex<[Int]>([])
        await rig.uploader.observe { status in seen.withLock { $0.append(status.committed) } }
        await rig.uploader.add(images)
        await rig.uploader.settled()

        #expect(await rig.uploader.snapshot.committedCount == 3)
        // After the first commit exactly one file counted as received.
        #expect(seen.withLock { $0.first { $0 > 0 } } == 1)
        let puts = rig.server.requests("PUT upload")
        #expect(puts.count == 5)
    }

    /// A world reset while a register answer is in flight: the late answer changes nothing, and the
    /// next capture is a new packet that never reuses the old one.
    @Test func resetDuringAPendingAnswerDropsTheLateReply() async throws {
        // A background session's transfer outlives the task that asked for it, so the reply
        // still arrives after the reset; only the attempt check keeps it out.
        let rig = try Rig(http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)))
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.held = ["POST captures/files"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        for _ in 0..<500 where rig.server.requests("POST captures/files").isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        #expect(rig.server.requests("POST captures/files").count == 1)

        await rig.uploader.abandon("world reset")
        rig.server.release("POST captures/files")
        try await Task.sleep(for: .milliseconds(300))
        await rig.uploader.settled()

        let after = await rig.uploader.snapshot
        #expect(after.end == .abandoned("world reset"))
        #expect(after.files.values.allSatisfy { $0.phase == .queued })
        #expect(rig.server.requests("PUT upload").isEmpty)
        // Nothing more is sent for the abandoned capture.
        let late = try await rig.capture.producer.sealKeyframe(
            jpeg: try SyntheticCapture04.jpeg(in: rig.root), observation: rig.capture.observation(SyntheticCapture04.start + 1.9), reason: "motion")
        await rig.uploader.add(late)
        await rig.uploader.settled()
        #expect(rig.server.requests("POST captures/files").count == 1)

        let next = try Rig(server: rig.server)
        defer { next.cleanUp() }
        await next.uploader.add(try await next.capture.sealImages(count: 1))
        await next.uploader.settled()
        #expect(await next.uploader.snapshot.captureID != after.captureID)
        #expect(await next.uploader.snapshot.committedCount == 2)
    }

    /// A finalize the server accepted and then lost (`failed`, next `retry_finalize`) is sent
    /// again as the same bytes, and the run then completes.
    @Test func aLostFinalizeIsSentAgainOnRetryFinalize() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.loseNextFinalize = true }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()
        let finals = rig.server.requests("POST captures/finalize")
        #expect(finals.count == 2)
        #expect(Set(finals.map(\.body)).count == 1)
        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        #expect(await rig.uploader.snapshot.finalizeRetries == 1)
    }

    /// The run is done but its answer is not readable yet (404 `result_not_ready`): the uploader
    /// reads again after a wait instead of failing the capture.
    @Test func aResultNotReadableYetIsReadAgain() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.notReadyResults = 2 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "manual_review"))
        #expect(try Self.outcomeKind(done.result) == .manualReview)
        #expect(rig.server.requests("GET captures/result").count == 3)
    }

    /// The API answers a result read with the capture's status and a null outcome until the run
    /// has written its answer. A terminal status with that body is not the answer.
    @Test func aResultWithoutItsOutcomeIsReadAgain() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.outcomelessResults = 2 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "manual_review"))
        #expect(try Self.outcomeKind(done.result) == .manualReview)
        #expect(rig.server.requests("GET captures/result").count == 3)
    }

    /// An answer that never becomes readable ends the upload visibly after the policy's reads,
    /// rather than waiting forever.
    @Test func aResultThatNeverArrivesEndsTheUpload() async throws {
        var policy = Rig.fast
        policy.maxNotReadyResults = 3
        let rig = try Rig(policy: policy)
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.outcomelessResults = 100 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .failed(step: "result", codes: ["result_not_ready"], status: 200))
        #expect(done.result == nil)
        #expect(rig.server.requests("GET captures/result").count == 4)
    }

    /// A 200 whose body is not a result is refused by name instead of being kept as the answer.
    @Test func anUnreadableResultStopsTheUpload() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.unreadableResults = 1 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .failed(step: "result", codes: ["result_unreadable"], status: 200))
        #expect(done.result == nil)
        #expect(rig.server.requests("GET captures/result").count == 1)
    }

    /// An outcome missing fields the app needs is refused by name instead of finishing the upload
    /// with an answer nothing can show.
    @Test func aMalformedOutcomeStopsTheUpload() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.malformedOutcomeResults = 1 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .failed(step: "result", codes: ["result_unreadable"], status: 200))
        #expect(done.result == nil)
        #expect(rig.server.requests("GET captures/result").count == 1)
    }

    /// Whether `uploader` goes idle within `seconds`. A loop that never goes idle fails the test
    /// here instead of hanging the run; the process ending stops it.
    static func settles(_ uploader: CaptureUploader, within seconds: Int = 20) async -> Bool {
        let done = Mutex(false)
        Task.detached { await uploader.settled(); done.withLock { $0 = true } }
        let deadline = ContinuousClock.now + .seconds(seconds)
        while ContinuousClock.now < deadline {
            if done.withLock({ $0 }) { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return false
    }

    /// A storage URL that is not https (or http to this machine) is refused when it is
    /// registered, before any bytes go to it; one that doesn't parse is refused the same way
    /// instead of leaving the file registered and the loop spinning.
    @Test(arguments: [("http://storage.example.com/upload/x", "upload_url_insecure"), ("", "upload_url_invalid")])
    func aStorageURLThatIsNotSafeIsRefusedBeforeAnyPut(url: String, code: String) async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.uploadURLOverride = url }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "register", codes: [code], status: 200))
        #expect(rig.server.requests("POST captures/files").count == 1)
        #expect(rig.server.requests("PUT upload").isEmpty)
    }

    /// Storage that answers a PUT with a redirect doesn't get the photo sent on: the real
    /// `URLSession` transport refuses every redirect, so the photo reaches only the URL that passed
    /// the check, and the 307 comes back as a storage refusal. The redirect goes to a second local
    /// server, which must see nothing.
    @Test func aStorageRedirectIsNotFollowed() async throws {
        let elsewhere = try LoopbackCaptureAPI()
        var policy = Rig.fast
        policy.maxRefusedPuts = 2
        let rig = try Rig(policy: policy)
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.redirects["PUT upload"] = "http://127.0.0.1:\(elsewhere.port)/upload/elsewhere" }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "put", codes: ["storage_refused"], status: 307))
        #expect(!rig.server.requests("PUT upload").isEmpty)
        #expect(elsewhere.state.withLock { $0.log.isEmpty })
    }

    /// The API's own requests aren't redirected either: a 307 on create comes back as the reply,
    /// which the uploader refuses by its status, and the second server sees nothing.
    @Test func anAPIRedirectIsNotFollowed() async throws {
        let elsewhere = try LoopbackCaptureAPI()
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.redirects["POST captures"] = "\(elsewhere.base.absoluteString)/captures" }
        await rig.uploader.kick()
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "create", codes: [], status: 307))
        #expect(rig.server.requests("POST captures").count == 1)
        #expect(elsewhere.state.withLock { $0.log.isEmpty })
    }

    /// Commits that never acknowledge a file back off between tries and stop after the policy's
    /// limit, instead of sending the same photo again and again.
    @Test func aCommitThatNeverAcknowledgesBacksOffAndStops() async throws {
        var policy = Rig.fast
        policy.maxUnacknowledged = 3
        let sleeps = Mutex<[Double]>([])
        let rig = try Rig(policy: policy, sleep: { seconds in sleeps.withLock { $0.append(seconds) } })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.commitNeverAcknowledges = true }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "commit", codes: ["not_acknowledged"], status: 200))
        // The images are the close-up still and its keyframe.
        #expect(rig.server.requests("POST captures/files:commit").count == 4)
        #expect(sleeps.withLock { $0.count } == 3)
    }

    /// Register answers that leave a file out back off and stop the same way.
    @Test func aRegisterThatOmitsFilesBacksOffAndStops() async throws {
        var policy = Rig.fast
        policy.maxUnacknowledged = 3
        let sleeps = Mutex<[Double]>([])
        let rig = try Rig(policy: policy, sleep: { seconds in sleeps.withLock { $0.append(seconds) } })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.registerOmitsFiles = true }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "register", codes: ["not_acknowledged"], status: 200))
        #expect(rig.server.requests("POST captures/files").count == 4)
        #expect(rig.server.requests("PUT upload").isEmpty)
        #expect(sleeps.withLock { $0.count } == 3)
    }

    /// The limit on reads that find no answer is saved with the upload, so relaunching doesn't
    /// start it again: across two processes the reads still stop at `maxNotReadyResults + 1`.
    @Test func theNotReadyLimitSurvivesARelaunch() async throws {
        var policy = Rig.fast
        policy.maxNotReadyResults = 3
        let waits = Mutex(0)
        // The first process quits during its second wait.
        var rig = try Rig(policy: policy, sleep: { _ in
            let n = waits.withLock { $0 += 1; return $0 }
            if n >= 2 { throw CancellationError() }
        })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.outcomelessResults = 100 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        try #require(await Self.settles(rig.uploader))
        #expect(rig.server.requests("GET captures/result").count == 2)
        #expect(await rig.uploader.snapshot.end == nil)

        // The old process releases every uploader before the new one resumes its saved state.
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        let resumed = try #require(try CaptureUploader.resume(
            folder: rig.capture.folder, base: rig.server.base, http: rig.http, policy: policy, sleep: { _ in }))
        await resumed.kick()
        try #require(await Self.settles(resumed))
        #expect(await resumed.snapshot.end == .failed(step: "result", codes: ["result_not_ready"], status: 200))
        #expect(rig.server.requests("GET captures/result").count == 4)
    }

    /// A server that answers an events poll at once with nothing new is not polled again at once:
    /// the uploader waits out `minimumPollInterval` first. The clock moves only when the uploader
    /// sleeps, so every empty answer arrives at once and each wait is the whole interval. The
    /// terminal answer is fetched without a wait.
    @Test func emptyEventsAnsweredAtOnceArePaced() async throws {
        let clock = Mutex(Date(timeIntervalSince1970: 1_000_000))
        let sleeps = Mutex<[Double]>([])
        let rig = try Rig(
            sleep: { seconds in sleeps.withLock { $0.append(seconds) }; clock.withLock { $0 += seconds } },
            now: { clock.withLock { $0 } })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.processingPolls = 3 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        let interval = CaptureUploader.Policy().minimumPollInterval
        #expect(sleeps.withLock { $0 } == [interval, interval, interval])
        #expect(rig.server.requests("GET captures/events").count == 4)
    }

    /// An empty answer the server held for the whole interval needs no further wait.
    @Test func emptyEventsHeldPastTheIntervalAreNotPaced() async throws {
        // Every reading of the clock is 3 s after the last, longer than the 2 s interval.
        let clock = Mutex(Date(timeIntervalSince1970: 1_000_000))
        let sleeps = Mutex<[Double]>([])
        let rig = try Rig(
            sleep: { seconds in sleeps.withLock { $0.append(seconds) } },
            now: { clock.withLock { $0 += 3; return $0 } })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.processingPolls = 3 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        try #require(await Self.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        #expect(sleeps.withLock { $0 }.isEmpty)
        #expect(rig.server.requests("GET captures/events").count == 4)
    }

    /// A capture the server lost and the uploader opened again is a new run, so it gets a fresh
    /// not-ready limit: the old run's reads don't end it.
    @Test func aRecreatedCaptureStartsANewNotReadyLimit() async throws {
        var policy = Rig.fast
        policy.maxNotReadyResults = 3
        let waits = Mutex(0)
        // The first process quits during its third wait, after three not-ready reads.
        var rig = try Rig(policy: policy, sleep: { _ in
            let n = waits.withLock { $0 += 1; return $0 }
            if n >= 3 { throw CancellationError() }
        })
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.outcomelessResults = 100 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        try #require(await Self.settles(rig.uploader))
        #expect(try CaptureUploadState.load(from: CaptureUploader.stateURL(in: rig.capture.folder)).notReadyReads == 3)

        // The server forgets every capture (a redeploy with in-memory state). The next run has
        // one not-ready read before its answer.
        rig.server.state.withLock { $0.captures = [:]; $0.byPacket = [:]; $0.outcomelessResults = 1 }
        // The old process releases every uploader before the new one resumes its saved state.
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        let resumed = try #require(try CaptureUploader.resume(
            folder: rig.capture.folder, base: rig.server.base, http: rig.http, policy: policy, sleep: { _ in }))
        await resumed.kick()
        try #require(await Self.settles(resumed))
        #expect(await resumed.snapshot.end == .finished(status: "manual_review"))
        #expect(rig.server.requests("POST captures").count == 2)
    }

    /// `failed` ends a capture without an answer, so its outcomeless result is final at once.
    @Test func aFailedCaptureEndsWithoutWaitingForAnOutcome() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.endStatus = "failed" }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try await rig.finishAndSeal()
        await rig.uploader.settled()

        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "failed"))
        #expect(try Self.outcomeKind(done.result) == nil)
        #expect(rig.server.requests("GET captures/result").count == 1)
    }

    static func outcomeKind(_ body: Data?) throws -> CaptureResult.OutcomeKind? {
        try JSONDecoder().decode(CaptureResult.Response.self, from: #require(body)).outcome?.kind
    }

    /// Storage that keeps refusing the sealed bytes' digest stops the upload after one retry
    /// instead of cycling through register and PUT.
    @Test func aDigestRefusedTwiceStopsTheUpload() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.badDigestPuts = 100 }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        await rig.uploader.settled()
        #expect(await rig.uploader.snapshot.end == .failed(step: "put", codes: ["bad_digest"], status: 400))
        #expect(rig.server.requests("PUT upload").count <= 4)
    }

    /// A file listed in the frozen packet that reached `seal` before its own `add` is still sent.
    @Test func sealQueuesListedFilesItHasNotSeen() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        let images = try await rig.capture.sealImages(count: 2)
        await rig.uploader.add(Array(images.prefix(1)))
        await rig.uploader.settled()
        let (streams, packet) = try await rig.capture.finish()
        await rig.uploader.seal(packet: packet, files: images + streams)
        await rig.uploader.add(Array(images.suffix(1)))
        await rig.uploader.settled()
        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "manual_review"))
        #expect(done.committedCount == images.count + streams.count)
    }

    @Test func noEndpointOrNoConsentMeansNoUpload() {
        #expect(CaptureUploadGate.decide(endpoint: nil, consented: true) == .off("no capture endpoint in this build"))
        #expect(CaptureUploadGate.decide(endpoint: "", consented: true) == .off("no capture endpoint in this build"))
        #expect(CaptureUploadGate.decide(endpoint: "$(HOUSESCAN_CAPTURE_API_URL)", consented: true) == .off("no capture endpoint in this build"))
        #expect(CaptureUploadGate.decide(endpoint: "http://example.com/v1", consented: true) == .off("capture endpoint must be https"))
        #expect(CaptureUploadGate.decide(endpoint: "https://user:secret@example.com/v1", consented: true) == .off("capture endpoint is not a URL"))
        #expect(CaptureUploadGate.decide(endpoint: "https://example.com/v1", consented: false) == .off("the homeowner has not agreed to send the capture"))
        #expect(CaptureUploadGate.decide(endpoint: "https://example.com/v1", consented: true) == .on(URL(string: "https://example.com/v1")!))
    }
}

/// Runs each request in a detached task, so cancelling the caller doesn't cancel the request:
/// the way a background URLSession behaves.
struct UncancellableHTTP: CaptureHTTP {
    let inner: any CaptureHTTP
    init(_ inner: any CaptureHTTP) { self.inner = inner }

    func send(_ request: URLRequest) async throws -> HTTPReply {
        try await Task.detached { try await inner.send(request) }.value
    }

    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        try await Task.detached { try await inner.upload(request, file: file) }.value
    }
}
