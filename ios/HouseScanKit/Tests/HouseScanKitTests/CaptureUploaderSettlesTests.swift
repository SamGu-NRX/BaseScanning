import Foundation
import HouseScanKit
import Testing

/// `CaptureUploaderTests.settles` is a hang safeguard, not a timing requirement, so a test
/// process that can't run its tasks for a while must not read as an uploader that never settles.
/// Hosted run 37219668935 stalled every test task for about 42 s after start (trivial tests
/// reported 28–43 s); four ScopedCaptureUploadTests then failed `settles` at its 20 s budget
/// although their uploads end at the first request.
///
/// These tests block every cooperative thread (sleeping, not spinning) for longer than a 1 s
/// budget while a real uploader settles. That stalls the whole test process, so they run only
/// when asked: `HSK_STARVE_COOPERATIVE_POOL=1 swift test --filter CaptureUploaderSettlesTests`.
@Suite(.enabled(if: ProcessInfo.processInfo.environment["HSK_STARVE_COOPERATIVE_POOL"] == "1"), .serialized)
struct CaptureUploaderSettlesTests {
    typealias Rig = CaptureUploaderTests.Rig

    /// Occupies every cooperative thread for `seconds`. Twice the processor count, so the threads
    /// a waiter frees by suspending are taken too.
    static func starve(for seconds: Double) {
        for _ in 0..<(2 * ProcessInfo.processInfo.activeProcessorCount) {
            Task.detached { usleep(useconds_t(seconds * 1_000_000)) }
        }
    }

    @Test func anIdleUploaderSettlesThroughAStall() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        Self.starve(for: 1.5)
        #expect(await CaptureUploaderTests.settles(rig.uploader, within: 1))
    }

    /// The failing hosted case: a malformed credential ends the upload at once, with a stall
    /// longer than the budget in between.
    @Test func aRefusedCredentialSettlesThroughAStall() async throws {
        let sleeps = ScopedCaptureUploadTests.Sleeps()
        let rig = try ScopedCaptureUploadTests.rig(provider: ScopedCaptureHTTPTests.Provider([""]), sleeps: sleeps)
        defer { rig.cleanUp() }
        await rig.uploader.kick()
        Self.starve(for: 1.5)
        #expect(await CaptureUploaderTests.settles(rig.uploader, within: 1))
        #expect(await rig.uploader.snapshot.end == .failed(step: "create", codes: ["auth_credential_malformed"], status: 0))
        #expect(sleeps.isEmpty)
    }
}

/// The safeguard still ends: an uploader held at its first request never settles, and `settles`
/// says so after at least its budget, every turn being a wait of 20 ms or more.
@Suite struct CaptureUploaderSettlesBoundTests {
    @Test func aHeldUploaderDoesNotSettle() async throws {
        let rig = try CaptureUploaderTests.Rig()
        defer { rig.cleanUp() }
        rig.server.state.withLock { $0.held = ["POST captures"] }
        await rig.uploader.kick()
        let started = ContinuousClock.now
        #expect(await !CaptureUploaderTests.settles(rig.uploader, within: 1))
        #expect(ContinuousClock.now - started >= .seconds(1))
        rig.server.release("POST captures")
        #expect(await CaptureUploaderTests.settles(rig.uploader))
    }
}
