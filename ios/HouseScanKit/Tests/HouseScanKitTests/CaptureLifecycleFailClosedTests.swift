import Foundation
import HouseScanKit
import simd
import Synchronization
import Testing

/// A photo or tap the scan accepted but the phone couldn't keep makes the capture one that can't be
/// prepared: the running upload stops at once with a local code, nothing is finalized, and an
/// upload that hasn't started never starts. A withdrawal still outranks it. Frames the producer
/// leaves out on purpose (a repeated frame, photos after the scan was sent) are not losses. A
/// failure from a world that was reset never reaches the next one.
@Suite(.serialized) @MainActor struct CaptureLifecycleFailClosedTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "fail-closed-\(UUID().uuidString)")
    var fixture: NativeCaptureFixture { NativeCaptureFixture(folder: root.appending(path: "store")) }
    var captures: URL { root.appending(path: "Captures") }

    static let preparationFailed = CaptureUploadState.End.failed(step: "prepare", codes: ["capture_input_lost"], status: 0)

    func coordinator(_ server: LoopbackCaptureAPI, log: @escaping @Sendable (String) -> Void = { _ in }) -> CaptureSessionCoordinator {
        CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(
                endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures, log: log))
    }

    /// A pose the producer refuses: scaled, so not rigid.
    static func scaled(_ m: simd_float4x4) -> simd_float4x4 {
        var out = m
        out.columns.0 *= 2
        out.columns.1 *= 2
        out.columns.2 *= 2
        return out
    }

    enum Loss: String, CaseIterable, Sendable {
        /// The JPEG the scan kept is gone before it could be staged.
        case stagingCopy
        /// Staged, then refused by the producer when its turn came.
        case queuedSeal
        /// The tap's frame couldn't be encoded.
        case tapFrame
        /// The tap's seal was refused.
        case tapSeal
    }

    func lose(_ loss: Loss, in coordinator: CaptureSessionCoordinator) throws {
        let t = fixture.start + 9
        switch loss {
        case .stagingCopy:
            var photo = try fixture.photo(at: t)
            photo.jpeg = root.appending(path: "missing.jpg")
            coordinator.kept(photo)
        case .queuedSeal:
            var photo = try fixture.photo(at: t)
            photo.cameraToWorld = Self.scaled(photo.cameraToWorld)
            coordinator.kept(photo)
        case .tapFrame:
            var (tap, hit) = try fixture.tap(at: t)
            tap.jpeg = { nil }
            coordinator.meterTapped(tap, hit: hit)
        case .tapSeal:
            var (tap, hit) = try fixture.tap(at: t)
            tap.cameraToWorld = Self.scaled(tap.cameraToWorld)
            coordinator.meterTapped(tap, hit: hit)
        }
    }

    /// The scan's photos so far, sent while it runs.
    func startScan(_ coordinator: CaptureSessionCoordinator, consent: Bool?) async throws {
        coordinator.begin(recording: fixture.recording)
        if let consent { coordinator.answerConsent(consent) }
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        coordinator.kept(try fixture.photo(at: fixture.start + 4))
        await coordinator.settle()
    }

    @Test(arguments: Loss.allCases)
    func aLostInputStopsTheRunningUploadBeforeTheScanEnds(loss: Loss) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = coordinator(server)
        try await startScan(coordinator, consent: true)
        let uploader = try #require(coordinator.session?.uploader)
        #expect(await uploader.snapshot.end == nil)

        try lose(loss, in: coordinator)
        await coordinator.settle()
        // Ended by the loss itself, before the scan was sent.
        #expect(await uploader.snapshot.end == Self.preparationFailed)
        #expect(await uploader.status.retryingAt == nil)
        #expect(coordinator.session?.preparationFailure == CaptureSessionCoordinator.inputLostCode)

        coordinator.kept(try fixture.photo(at: fixture.start + 10))
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        await coordinator.settle()
        #expect(await uploader.snapshot.end == Self.preparationFailed)
        #expect(await uploader.snapshot.packet == nil)
        #expect(server.requests("POST captures/finalize").isEmpty)
    }

    /// Lost before the homeowner answered: a later yes sends nothing, so no capture is opened for
    /// a packet that can't be complete.
    @Test(arguments: Loss.allCases)
    func aLostInputBeforeConsentOpensNoCapture(loss: Loss) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = coordinator(server)
        try await startScan(coordinator, consent: nil)
        try lose(loss, in: coordinator)
        await coordinator.settle()
        coordinator.answerConsent(true)
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        await coordinator.settle()
        #expect(coordinator.session?.uploader == nil)
        #expect(coordinator.session?.preparationFailure == CaptureSessionCoordinator.inputLostCode)
        #expect(server.state.withLock { $0.log.isEmpty })
    }

    /// The homeowner's no stands: a loss afterwards doesn't relabel the upload's end.
    @Test func aWithdrawalOutranksALostInput() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = coordinator(server)
        try await startScan(coordinator, consent: true)
        let uploader = try #require(coordinator.session?.uploader)
        guard case .success = coordinator.answerConsent(false) else {
            Issue.record("the withdrawal wasn't recorded")
            return
        }
        try lose(.queuedSeal, in: coordinator)
        await coordinator.settle()
        await uploader.settled()
        #expect(await uploader.snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
    }

    /// What the producer leaves out on purpose isn't a loss: the close-up's frame kept again, a
    /// photo for a purpose that isn't a still, and photos after the scan was sent.
    @Test func framesLeftOutOnPurposeStillFinish() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = coordinator(server)
        try await startScan(coordinator, consent: true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5))
        coordinator.kept(try fixture.photo(at: fixture.start + 6, purpose: "door_view"))
        await coordinator.settle()
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        coordinator.kept(try fixture.photo(at: fixture.start + 11))
        await coordinator.settle()
        #expect(await coordinator.session?.uploader?.snapshot.end == .finished(status: "manual_review"))
        #expect(server.requests("POST captures/finalize").count == 1)
    }

    /// A tap whose frame is still being encoded when the world is reset, and then fails, doesn't
    /// touch the next world's session, upload or status.
    @Test func aFailureFromAResetWorldDoesNotReachTheNextOne() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let lines = Mutex<[String]>([])
        let coordinator = coordinator(server) { line in lines.withLock { $0.append(line) } }
        let statuses = Mutex<[CaptureUploadStatus?]>([])
        coordinator.onStatus = { status in statuses.withLock { $0.append(status) } }
        try await startScan(coordinator, consent: true)

        let entered = Mutex(false)
        let gate = DispatchSemaphore(value: 0)
        var (tap, hit) = try fixture.tap(at: fixture.start + 9)
        tap.jpeg = {
            entered.withLock { $0 = true }
            gate.wait()
            return nil
        }
        coordinator.meterTapped(tap, hit: hit)
        for _ in 0..<500 where !entered.withLock({ $0 }) { try await Task.sleep(for: .milliseconds(10)) }
        try #require(entered.withLock { $0 })

        coordinator.newWorld("world reset", recording: fixture.recording, newScan: false)
        let next = try #require(coordinator.session)
        let seen = statuses.withLock { $0.count }
        gate.signal()
        // The old world's work has run to its end once it logs the failed frame.
        let failed = { lines.withLock { $0.contains { $0.contains("meter tap") } } }
        for _ in 0..<500 where !failed() { try await Task.sleep(for: .milliseconds(10)) }
        try #require(failed())
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        await coordinator.settle()
        let nextUploader = try #require(next.uploader)
        await nextUploader.settled()

        #expect(coordinator.session === next)
        #expect(next.preparationFailure == nil)
        #expect(await nextUploader.snapshot.end == nil)
        #expect(statuses.withLock { $0.dropFirst(seen) }.allSatisfy { status in status?.phase != .failed })
    }
}

/// The code is readable where the app maps an upload's end to a stage, outside the main actor.
struct InputLostCodeIsNonisolated {
    static func matches(_ codes: [String]) -> Bool { codes.contains(CaptureSessionCoordinator.inputLostCode) }

    @Test func aNonisolatedMappingCanMatchTheCode() {
        #expect(Self.matches(["capture_input_lost"]))
        #expect(!Self.matches(["local_state_unsaved"]))
    }
}
