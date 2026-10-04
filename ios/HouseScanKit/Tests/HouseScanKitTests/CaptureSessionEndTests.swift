import Foundation
import HouseScanKit
import Testing

/// A world reset ends its capture for good before `newWorld` returns. The upload's own abandonment
/// still runs on the uploader's turn, so the ended record written synchronously here is what keeps
/// a relaunch from finding the old yes and resuming the capture the homeowner left. It is a
/// reset's record, not a withdrawal's.
@Suite(.serialized) @MainActor struct CaptureSessionEndTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "session-end-\(UUID().uuidString)")
    var fixture: NativeCaptureFixture { NativeCaptureFixture(folder: root.appending(path: "store")) }
    var captures: URL { root.appending(path: "Captures") }
    static let endedFile = "capture-ended"

    func coordinator(_ server: LoopbackCaptureAPI) -> CaptureSessionCoordinator {
        CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures))
    }

    func scan(_ coordinator: CaptureSessionCoordinator) async throws -> URL {
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        coordinator.kept(try fixture.photo(at: fixture.start + 4))
        await coordinator.settle()
        let folder = try #require(coordinator.session?.folder)
        #expect(try CaptureUploadState.load(from: CaptureUploader.stateURL(in: folder)).end == nil)
        return folder
    }

    @Test func aResetRecordsTheEndBeforeNewWorldReturns() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = coordinator(server)
        let folder = try await scan(coordinator)

        coordinator.newWorld("world reset", recording: fixture.recording, newScan: false)
        // No suspension point since newWorld: only what it did synchronously is on disk.
        #expect(FileManager.default.fileExists(atPath: folder.appending(path: Self.endedFile).path))
        #expect(!FileManager.default.fileExists(atPath: CaptureUploadState.withdrawnURL(in: folder).path))
        await coordinator.settle()
    }

    /// The record is what a relaunch reads: a folder carrying it isn't resumed, even with its
    /// saved state still unended and nothing in this process remembering it.
    @Test func aFolderWithTheEndedRecordIsNotResumed() async throws {
        var rig = try CaptureUploaderTests.Rig()
        defer { rig.cleanUp() }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        try Data("world reset".utf8).write(to: rig.capture.folder.appending(path: Self.endedFile))
        #expect(try CaptureUploadState.load(from: CaptureUploader.stateURL(in: rig.capture.folder)).end == nil)

        #expect(try CaptureUploader.resume(folder: rig.capture.folder, base: rig.server.base, http: rig.http, policy: CaptureUploaderTests.Rig.fast) == nil)
    }
}
