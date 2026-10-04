import Foundation
import HouseScanKit
import Synchronization
import Testing

@Suite(.serialized) struct CaptureFolderOwnershipTests {
    private func create(_ folder: URL, _ base: URL) throws -> CaptureUploader {
        try CaptureUploader.start(
            folder: folder, base: base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10),
            create: .init(packetId: "ownership-test", tier: .arkit, device: .init(model: "test", systemVersion: "test", appVersion: "test")),
            consentedAt: Date())
    }

    @Test func concurrentStartsSaveOnlyTheOwnersAttempt() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "owners-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let owners = await withTaskGroup(of: CaptureUploader?.self, returning: [CaptureUploader].self) { group in
            for _ in 0..<12 {
                group.addTask {
                    do { return try create(root, server.base) }
                    catch { #expect(error as? CaptureUploader.OwnershipError == .alreadyOwned); return nil }
                }
            }
            var owners: [CaptureUploader] = []
            for await owner in group { if let owner { owners.append(owner) } }
            return owners
        }
        try #require(owners.count == 1)
        let before = await owners[0].snapshot
        #expect(try CaptureUploadState.load(from: CaptureUploader.stateURL(in: root)) == before)
        #expect(throws: CaptureUploader.OwnershipError.alreadyOwned) {
            try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        }
        await owners[0].kick()
        try #require(await CaptureUploaderTests.settles(owners[0]))
        #expect(server.requests("POST captures").count == 1)
    }

    @Test func concurrentResumesSaveOnlyTheOwnersAttempt() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "resume-owners-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        var first: CaptureUploader? = try create(root, server.base)
        weak let released = first
        first = nil
        try #require(released == nil)
        let owners = await withTaskGroup(of: CaptureUploader?.self, returning: [CaptureUploader].self) { group in
            for _ in 0..<12 {
                group.addTask {
                    do { return try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10)) }
                    catch { #expect(error as? CaptureUploader.OwnershipError == .alreadyOwned); return nil }
                }
            }
            var owners: [CaptureUploader] = []
            for await owner in group { if let owner { owners.append(owner) } }
            return owners
        }
        try #require(owners.count == 1)
        #expect(try CaptureUploadState.load(from: CaptureUploader.stateURL(in: root)) == (await owners[0].snapshot))
        await owners[0].kick()
        try #require(await CaptureUploaderTests.settles(owners[0]))
        #expect(server.requests("POST captures").count == 1)
    }

    @Test func aliasesCannotAcquireTheSameFolder() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "alias-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let folder = root.appending(path: "Capture")
        let owner = try create(folder, server.base)
        let link = root.appending(path: "link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: folder)
        var aliases = [link, root.appending(path: "Capture/../Capture")]
        let lowercased = root.appending(path: "capture")
        if FileManager.default.fileExists(atPath: lowercased.path) { aliases.append(lowercased) }
        for alias in aliases {
            #expect(throws: CaptureUploader.OwnershipError.alreadyOwned, "alias: \(alias.path)") { try create(alias, server.base) }
            #expect(throws: CaptureUploader.OwnershipError.alreadyOwned, "alias: \(alias.path)") {
                try CaptureUploader.resume(folder: alias, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10))
            }
        }
        #expect(owner.folder.path == (try folder.resourceValues(forKeys: [.canonicalPathKey]).canonicalPath))
        #expect(server.state.withLock { $0.log.isEmpty })
    }

    @Test func idleOwnershipReleasesOnlyAfterTheUploaderDies() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "lifetime-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        var owner: CaptureUploader? = try create(root, server.base)
        await owner?.kick()
        try #require(await CaptureUploaderTests.settles(try #require(owner)))
        #expect(throws: CaptureUploader.OwnershipError.alreadyOwned) {
            try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        }
        weak let released = owner
        owner = nil
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        #expect(throws: CaptureUploader.OwnershipError.savedUploadExists) { try create(root, server.base) }
        let resumed = try #require(try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10)))
        await resumed.kick()
        try #require(await CaptureUploaderTests.settles(resumed))
        #expect(server.requests("POST captures").count == 1)
    }

    @Test func anInFlightRequestKeepsOwnershipAfterTheCallerReleasesIt() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "inflight-owner-\(UUID().uuidString)")
        let server = try LoopbackCaptureAPI()
        defer {
            server.state.withLock { $0.held = [] }
            server.release("POST captures")
            try? FileManager.default.removeItem(at: root)
        }
        server.state.withLock { $0.held = ["POST captures"] }
        var owner: CaptureUploader? = try create(root, server.base)
        await owner?.kick()
        for _ in 0..<500 where server.state.withLock({ $0.parked["POST captures"]?.isEmpty ?? true }) {
            try await Task.sleep(for: .milliseconds(10))
        }
        try #require(server.state.withLock { !($0.parked["POST captures"]?.isEmpty ?? true) })
        weak let runningOwner = owner
        owner = nil
        #expect(runningOwner != nil)
        #expect(throws: CaptureUploader.OwnershipError.alreadyOwned) {
            try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        }
        server.state.withLock { $0.held = [] }
        server.release("POST captures")
        for _ in 0..<500 where runningOwner != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(runningOwner == nil)
        let resumed = try #require(try CaptureUploader.resume(folder: root, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10)))
        await resumed.kick()
        try #require(await CaptureUploaderTests.settles(resumed))
        #expect(server.requests("POST captures").count == 1)
    }

    /// Failure injection uses permissions only on this test's temporary folder. Restoring the
    /// stale saved yes after stopping models the disk contents a failed withdrawal can leave.
    @Test(arguments: [0, 1, 2]) func withdrawalBlocksReuseEvenWhenDiskStillHasConsent(failedWrites: Int) async throws {
        var rig = try CaptureUploaderTests.Rig(http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)))
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: rig.capture.folder.path)
            rig.cleanUp()
        }
        rig.server.state.withLock { $0.held = ["POST captures/files"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 1))
        for _ in 0..<500 where rig.server.state.withLock({ $0.parked["POST captures/files"]?.isEmpty ?? true }) {
            try await Task.sleep(for: .milliseconds(10))
        }
        try #require(rig.server.state.withLock { !($0.parked["POST captures/files"]?.isEmpty ?? true) })
        let stateURL = CaptureUploader.stateURL(in: rig.capture.folder)
        let staleConsent = try Data(contentsOf: stateURL)
        let marker = CaptureUploadState.withdrawnURL(in: rig.capture.folder)
        if failedWrites == 1 {
            try FileManager.default.createDirectory(at: marker.appending(path: "blocked"), withIntermediateDirectories: true)
        } else if failedWrites == 2 {
            try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: rig.capture.folder.path)
        }
        let result = rig.uploader.withdrawConsent()
        switch failedWrites {
        case 0: #expect(result == .marked)
        case 1: #expect(result == .savedStateRemoved)
        default:
            guard case .notRecorded = result else { Issue.record("Both filesystem writes must fail"); return }
            #expect(try Data(contentsOf: stateURL) == staleConsent)
        }
        rig.server.state.withLock { $0.held = [] }
        rig.server.release("POST captures/files")
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(rig.server.requests("PUT upload").isEmpty)
        #expect(rig.server.requests("POST captures/files:commit").isEmpty)
        #expect(await rig.uploader.snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: rig.capture.folder.path)
        if FileManager.default.fileExists(atPath: marker.path) { try FileManager.default.removeItem(at: marker) }
        try staleConsent.write(to: stateURL, options: .atomic)
        weak let released = rig.uploader
        rig.releaseUploader()
        for _ in 0..<500 where released != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(released == nil)
        #expect(try CaptureUploader.resume(folder: rig.capture.folder, base: rig.server.base, http: rig.http) == nil)
        #expect(throws: CaptureUploader.OwnershipError.consentWithdrawn) { try create(rig.capture.folder, rig.server.base) }
        #expect(try Data(contentsOf: stateURL) == staleConsent)
    }

    @Test(arguments: [false, true]) func withdrawalDuringPutsStopsTheNextBatchAndCommit(uncancellable: Bool) async throws {
        let transport = URLSessionCaptureHTTP.ephemeral(timeout: 10)
        let http: any CaptureHTTP = uncancellable ? UncancellableHTTP(transport) : transport
        var policy = CaptureUploaderTests.Rig.fast
        policy.maxConcurrentPuts = 2
        let rig = try CaptureUploaderTests.Rig(http: http, policy: policy)
        defer {
            rig.server.state.withLock { $0.held = [] }
            rig.server.release("PUT upload")
            rig.cleanUp()
        }
        rig.server.state.withLock { $0.held = ["PUT upload"] }
        await rig.uploader.add(try await rig.capture.sealImages(count: 3))
        for _ in 0..<500 where rig.server.state.withLock({ ($0.parked["PUT upload"]?.count ?? 0) < 2 }) {
            try await Task.sleep(for: .milliseconds(10))
        }
        try #require(rig.server.state.withLock { ($0.parked["PUT upload"]?.count ?? 0) == 2 })
        #expect(rig.uploader.withdrawConsent() == .marked)
        await rig.uploader.kick()
        rig.server.state.withLock { $0.held = [] }
        rig.server.release("PUT upload")
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(rig.server.requests("PUT upload").count == 2)
        #expect(rig.server.requests("POST captures/files:commit").isEmpty)
        #expect(await rig.uploader.snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
    }

    @Test func withdrawalBeforeConcurrentKicksAdmitsNoRequests() async throws {
        let rig = try CaptureUploaderTests.Rig()
        defer { rig.cleanUp() }
        #expect(rig.uploader.withdrawConsent() == .marked)
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<20 { group.addTask { await rig.uploader.kick() } }
        }
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        #expect(rig.server.state.withLock { $0.log.isEmpty })
    }
}
