import Foundation
@testable import HouseScanKit
import Testing

/// A completed scan outlives a relaunch; the scan being made always has a folder of its own.
@Suite struct KeptScansTests {
    static func makeRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appending(path: "kept-scans-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    /// A capture packet as the app zips it, manifest.json first. Synthetic, never a capture.
    static let packet: Data = try! ZipWriter.archive([
        ZipEntry(name: "manifest.json", data: Data(#"{"version":"1.1"}"#.utf8)),
        ZipEntry(name: "photos/p00001.jpg", data: Data((0..<4096).map { UInt8($0 % 251) })),
    ])

    /// A scan folder, completed (with its bundle, written at `minutesAgo`) or not. A `partial`
    /// bundle is the packet cut short, as a quit during its write leaves it.
    static func scan(_ name: String, in root: URL, bundledMinutesAgo minutesAgo: Double? = nil, partial: Bool = false) throws {
        let folder = root.appending(path: name)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("photo".utf8).write(to: folder.appending(path: "k00001.jpg"))
        guard let minutesAgo else { return }
        let bundle = folder.appending(path: ScanFolderCleanup.bundleName)
        try (partial ? packet.prefix(packet.count / 2) : packet).write(to: bundle)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSinceNow: -minutesAgo * 60)], ofItemAtPath: bundle.path)
    }

    static func names(_ urls: [URL]) -> [String] {
        urls.map(\.lastPathComponent)
    }

    /// A relaunch makes a new store: the two most recent completed scans stay, with their
    /// bundles; an older completed scan and one never packaged go. Before, all of them went.
    @Test func aRelaunchKeepsTheLastCompletedScans() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("abandoned", in: root)
        try Self.scan("run1", in: root, bundledMinutesAgo: 90)
        try Self.scan("run2", in: root, bundledMinutesAgo: 30)
        try Self.scan("run3", in: root, bundledMinutesAgo: 5)
        try Self.scan("now", in: root)

        let cleanup = ScanFolderCleanup(root: root, keeping: "now")
        #expect(cleanup.keptCompleted.map(\.lastPathComponent) == ["run3", "run2"])
        #expect(Set(cleanup.obsolete.map(\.lastPathComponent)) == ["abandoned", "run1"])
        let failed = cleanup.run()
        #expect(failed.isEmpty)
        #expect(try Set(FileManager.default.contentsOfDirectory(atPath: root.path)) == ["now", "run2", "run3"])
        #expect(FileManager.default.fileExists(atPath: root.appending(path: "run3/\(ScanFolderCleanup.bundleName)").path))
    }

    /// The scan being made is never listed, and a new scan is a new, empty folder beside the kept
    /// ones: nothing of a kept scan ends up in it.
    @Test func aNewScanStillGetsACleanFolder() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("run2", in: root, bundledMinutesAgo: 30)
        let fresh = root.appending(path: "fresh")
        try FileManager.default.createDirectory(at: fresh, withIntermediateDirectories: true)
        let cleanup = ScanFolderCleanup(root: root, keeping: "fresh")
        #expect(!cleanup.obsolete.contains(fresh) && !cleanup.keptCompleted.contains(fresh))
        _ = cleanup.run()
        #expect(try FileManager.default.contentsOfDirectory(atPath: fresh.path).isEmpty)
        // Once a newer completed scan exists, the kept one is still one of the two most recent.
        try Self.scan("run3", in: root, bundledMinutesAgo: 1)
        #expect(ScanFolderCleanup(root: root, keeping: "later").keptCompleted.map(\.lastPathComponent) == ["run3", "run2"])
    }

    /// A partial bundle takes no kept place, however new: both intact scans stay. Its folder
    /// is left alone too, however old, since nothing shows its writer has stopped. Before,
    /// presence alone counted, so the newer partial kept a place and an intact scan was deleted.
    @Test(arguments: [0.5, 20.0, 60.0 * 24 * 365])
    func aPartialBundleNeitherEvictsIntactScansNorIsDeleted(minutesAgo: Double) throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("intact1", in: root, bundledMinutesAgo: 40)
        try Self.scan("intact2", in: root, bundledMinutesAgo: 90)
        try Self.scan("intact3", in: root, bundledMinutesAgo: 120)
        // Newer than every intact scan, unless it is the year-old one.
        try Self.scan("partial", in: root, bundledMinutesAgo: minutesAgo, partial: true)
        try Self.scan("unpackaged", in: root)

        let cleanup = ScanFolderCleanup(root: root, keeping: "now")
        #expect(Self.names(cleanup.keptCompleted) == ["intact1", "intact2"])
        #expect(Self.names(cleanup.incompleteBundles) == ["partial"])
        #expect(Self.names(cleanup.obsolete) == ["intact3", "unpackaged"])
        #expect(cleanup.run().isEmpty)
        #expect(try Set(FileManager.default.contentsOfDirectory(atPath: root.path)) == ["intact1", "intact2", "partial"])
        #expect(try Data(contentsOf: root.appending(path: "partial/\(ScanFolderCleanup.bundleName)")) == Self.packet.prefix(Self.packet.count / 2))
    }

    /// A bundle that fails the check for any reason (empty, not a packet) is treated like a
    /// partial one; once its write finishes it competes like any completed scan.
    @Test func anUnreadableBundleIsLeftAloneUntilItIsWhole() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("intact1", in: root, bundledMinutesAgo: 40)
        try Self.scan("intact2", in: root, bundledMinutesAgo: 90)
        let empty = root.appending(path: "empty")
        try FileManager.default.createDirectory(at: empty, withIntermediateDirectories: true)
        try Data().write(to: empty.appending(path: ScanFolderCleanup.bundleName))

        let cleanup = ScanFolderCleanup(root: root, keeping: "now")
        #expect(Self.names(cleanup.incompleteBundles) == ["empty"])
        #expect(cleanup.obsolete.isEmpty)

        try Self.packet.write(to: empty.appending(path: ScanFolderCleanup.bundleName))
        let later = ScanFolderCleanup(root: root, keeping: "now")
        #expect(Self.names(later.keptCompleted) == ["empty", "intact1"])
        #expect(Self.names(later.obsolete) == ["intact2"])
    }

    /// A folder whose bundle can't be read, or whose bundle is a link or a folder, and a link in
    /// place of a scan folder hold no place and are not deleted.
    @Test func bundlesThatCannotBeJudgedAreLeftAlone() throws {
        let root = try Self.makeRoot()
        let files = FileManager.default
        try Self.scan("intact1", in: root, bundledMinutesAgo: 40)
        try Self.scan("intact2", in: root, bundledMinutesAgo: 90)
        try Self.scan("locked", in: root, bundledMinutesAgo: 1)
        let locked = root.appending(path: "locked")
        try files.setAttributes([.posixPermissions: 0o000], ofItemAtPath: locked.path)
        defer {
            try? files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: locked.path)
            try? files.removeItem(at: root)
        }
        try Self.scan("linked", in: root)
        try files.createSymbolicLink(at: root.appending(path: "linked/\(ScanFolderCleanup.bundleName)"),
                                     withDestinationURL: root.appending(path: "intact1/\(ScanFolderCleanup.bundleName)"))
        try Self.scan("folderBundle", in: root)
        try files.createDirectory(at: root.appending(path: "folderBundle/\(ScanFolderCleanup.bundleName)"), withIntermediateDirectories: true)

        try Data("stray".utf8).write(to: root.appending(path: "stray.txt"))
        // A link to the newest intact scan, named to sort ahead of it: it must not take a place.
        try files.createSymbolicLink(at: root.appending(path: "zalias"), withDestinationURL: root.appending(path: "intact1"))

        let cleanup = ScanFolderCleanup(root: root, keeping: "now")
        #expect(Self.names(cleanup.keptCompleted) == ["intact1", "intact2"])
        #expect(Set(Self.names(cleanup.incompleteBundles)) == ["locked", "linked", "folderBundle", "zalias"])
        // A stray file is not a scan folder and goes, as before.
        #expect(Self.names(cleanup.obsolete) == ["stray.txt"])
        #expect(cleanup.run().isEmpty)
        #expect(try Set(files.contentsOfDirectory(atPath: root.path)) == ["intact1", "intact2", "locked", "linked", "folderBundle", "zalias"])
    }

    /// A retry rewrites scan.zip in place while the check may still be reading the old file. A
    /// check that passes on the old file must not count the new, partial one as complete.
    @Test func aBundleReplacedDuringTheCheckIsNotComplete() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("rewritten", in: root, bundledMinutesAgo: 5)
        let bundle = root.appending(path: "rewritten/\(ScanFolderCleanup.bundleName)")

        let state = ScanFolderCleanup.bundleState(bundle) { url in
            let size = try PacketArchiveCheck.verify(url)
            // As ZipWriter.write does: remove the file, then start a new one.
            try FileManager.default.removeItem(at: url)
            try Self.packet.prefix(Self.packet.count / 2).write(to: url)
            return size
        }
        #expect(state == .incomplete)

        // Left alone, the same bundle is complete.
        try Self.packet.write(to: bundle)
        let settled = ScanFolderCleanup.bundleState(bundle)
        #expect(settled != .incomplete && settled != .absent)
        #expect(ScanFolderCleanup.bundleState(root.appending(path: "none/\(ScanFolderCleanup.bundleName)")) == .absent)
    }

    /// The scan in use is never judged, whatever its bundle; a folder made after the listing is
    /// never deleted by it.
    @Test func theScanInUseAndLaterFoldersAreSafe() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("current", in: root, bundledMinutesAgo: 60, partial: true)
        try Self.scan("old", in: root)
        let cleanup = ScanFolderCleanup(root: root, keeping: "current")
        #expect(!(cleanup.obsolete + cleanup.keptCompleted + cleanup.incompleteBundles).map(\.lastPathComponent).contains("current"))
        #expect(Self.names(cleanup.obsolete) == ["old"])

        try Self.scan("later", in: root, bundledMinutesAgo: 1)
        try Self.scan("laterPartial", in: root, bundledMinutesAgo: 60, partial: true)
        #expect(cleanup.run().isEmpty)
        #expect(try Set(FileManager.default.contentsOfDirectory(atPath: root.path)) == ["current", "later", "laterPartial"])
    }

    /// The deletion runs after the listing. A folder whose bundle appeared since, partial or
    /// whole, is left for the next cleanup, and so is a bundle a retry rewrote; one still as
    /// listed is deleted.
    @Test func runDeletesOnlyEntriesStillAsListed() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let files = FileManager.default
        let bundle = { (name: String) in root.appending(path: "\(name)/\(ScanFolderCleanup.bundleName)") }
        try Self.scan("intact1", in: root, bundledMinutesAgo: 40)
        try Self.scan("intact2", in: root, bundledMinutesAgo: 90)
        try Self.scan("intact3", in: root, bundledMinutesAgo: 120)
        try Self.scan("rewritten", in: root, bundledMinutesAgo: 150)
        // The same date before and after the rewrite: only the new file tells them apart.
        let saved = Date(timeIntervalSinceReferenceDate: 800_000_000)
        try files.setAttributes([.modificationDate: saved], ofItemAtPath: bundle("rewritten").path)
        try Self.scan("startsWriting", in: root)
        try Self.scan("finishesWriting", in: root)
        try Self.scan("stillBare", in: root)
        let cleanup = ScanFolderCleanup(root: root, keeping: "now")
        #expect(Self.names(cleanup.obsolete) == ["finishesWriting", "intact3", "rewritten", "startsWriting", "stillBare"])

        try Self.packet.prefix(Self.packet.count / 2).write(to: bundle("startsWriting"))
        try Self.packet.write(to: bundle("finishesWriting"))
        // A retry rewrites in place: the old file goes and a new one takes its name.
        try files.removeItem(at: bundle("rewritten"))
        try Self.packet.write(to: bundle("rewritten"))
        try files.setAttributes([.modificationDate: saved], ofItemAtPath: bundle("rewritten").path)

        #expect(cleanup.run().isEmpty)
        #expect(try Set(files.contentsOfDirectory(atPath: root.path)) == ["intact1", "intact2", "rewritten", "startsWriting", "finishesWriting"])
    }

    // MARK: A bundle still being written when Start over lists its folder (B44)
    //
    // The async tests below carry a five-minute time limit, only so that a gate never released
    // fails instead of hanging; the gates and assertions decide pass or fail. In the concurrent
    // package suite these tests reported up to 82.5 s (hosted run 37161498639, which had no
    // limit), so a one-minute limit was too tight to rely on.

    /// A scan folder whose bundle is being written: the photos, stamp and raw streams the packet
    /// is built from and the half-assembled `packet/`, but no `scan.zip` yet.
    static func packing(_ name: String, in root: URL) throws -> URL {
        try scan(name, in: root)
        let folder = root.appending(path: name)
        let files = FileManager.default
        try Data("{}".utf8).write(to: folder.appending(path: "scan-stamp.json"))
        try files.createDirectory(at: folder.appending(path: "streams-raw"), withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: folder.appending(path: "streams-raw/trajectory.bin"))
        try files.createDirectory(at: folder.appending(path: "packet/photos"), withIntermediateDirectories: true)
        try Data("photo".utf8).write(to: folder.appending(path: "packet/photos/p00001.jpg"))
        return folder
    }

    /// What a bundle write leaves when it ends.
    enum WriteEnd: Sendable, CaseIterable {
        /// The zip written to the end and `packet/` removed, as `zipPacket` does.
        case bundled
        /// It failed before zipping: `packet/` is left and there is no zip.
        case failedBeforeZipping
        /// A zip cut short is left.
        case partialZip

        func apply(to folder: URL) throws {
            let bundle = folder.appending(path: ScanFolderCleanup.bundleName)
            switch self {
            case .bundled:
                try KeptScansTests.packet.write(to: bundle)
                try FileManager.default.removeItem(at: folder.appending(path: "packet"))
            case .failedBeforeZipping:
                break
            case .partialZip:
                try KeptScansTests.packet.prefix(KeptScansTests.packet.count / 2).write(to: bundle)
            }
        }
    }

    /// A bundle write the test holds open, then ends as `end` says; like the app's, it starts
    /// only after the write before it (`after`).
    struct HeldWrite {
        let task: Task<Void, Never>
        private let release: AsyncStream<Void>.Continuation

        init(_ folder: URL, ending end: WriteEnd, after earlier: Task<Void, Never>? = nil) {
            let (gate, release) = AsyncStream<Void>.makeStream()
            self.release = release
            task = Task {
                await earlier?.value
                for await _ in gate { break }
                try? end.apply(to: folder)
            }
        }

        func finish() { release.finish() }
    }

    /// Deletes with `run(after:)`, waiting for `writes` as the app does, and says through
    /// `arrived` when it has reached that wait. Returns the folders it failed to delete.
    static func deleting(_ cleanup: ScanFolderCleanup, after writes: Task<Void, Never>?, arrived: AsyncStream<Void>.Continuation) -> Task<[URL], Never> {
        Task {
            await cleanup.run(after: {
                arrived.yield()
                await writes?.value
            }).map(\.url)
        }
    }

    /// Start over lists the previous scan's folder while its bundle is still being assembled:
    /// with no zip yet it is listed as never packaged. The deletion waits for the write; a zip
    /// written to the end keeps the folder, one cut short keeps it without a place (as any
    /// partial bundle), and a write that failed before zipping leaves it to be deleted.
    @Test(.timeLimit(.minutes(5)), arguments: WriteEnd.allCases)
    func aBundleStillBeingWrittenIsJudgedOnlyAfterItsWrite(end: WriteEnd) async throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let folder = try Self.packing("previous", in: root)
        try Self.scan("new", in: root)
        let write = HeldWrite(folder, ending: end)

        let cleanup = ScanFolderCleanup(root: root, keeping: "new")
        // What B44 deleted: the folder being written is listed with the never-packaged ones.
        #expect(Self.names(cleanup.obsolete) == ["previous"])
        let (arrived, arrive) = AsyncStream<Void>.makeStream()
        let deleting = Self.deleting(cleanup, after: write.task, arrived: arrive)
        var arrivals = arrived.makeAsyncIterator()
        _ = await arrivals.next()
        // The cleanup is waiting for the write, which is still assembling the packet.
        #expect(FileManager.default.fileExists(atPath: folder.appending(path: "packet/photos/p00001.jpg").path))

        write.finish()
        #expect(await deleting.value.isEmpty)
        let next = ScanFolderCleanup(root: root, keeping: "next")
        switch end {
        case .bundled:
            #expect(FileManager.default.fileExists(atPath: folder.appending(path: ScanFolderCleanup.bundleName).path))
            #expect(Self.names(next.keptCompleted) == ["previous"])
        case .failedBeforeZipping:
            #expect(!FileManager.default.fileExists(atPath: folder.path))
        case .partialZip:
            #expect(FileManager.default.fileExists(atPath: folder.path))
            #expect(Self.names(next.incompleteBundles) == ["previous"])
            #expect(!Self.names(next.obsolete).contains("previous"))
        }
    }

    /// At launch there is no write to wait for: the deletion is `run()`'s, at once.
    @Test func withNoWriteTheDeletionRunsAtOnce() async throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        try Self.scan("abandoned", in: root)
        try Self.scan("run1", in: root, bundledMinutesAgo: 90)
        try Self.scan("run2", in: root, bundledMinutesAgo: 30)
        try Self.scan("run3", in: root, bundledMinutesAgo: 5)
        try Self.scan("now", in: root)

        let none: Task<Void, Never>? = nil
        let failed = await ScanFolderCleanup(root: root, keeping: "now").run(after: { await none?.value })
        #expect(failed.isEmpty)
        #expect(try Set(FileManager.default.contentsOfDirectory(atPath: root.path)) == ["now", "run2", "run3"])
    }

    /// The ends of named bundle writes, and the cleanups waiting for them. Ending a write
    /// releases the cleanups waiting for it and records which it released, under the lock, so a
    /// test reads which cleanup a write let go instead of inferring it from scheduling.
    final class WriteEnds: @unchecked Sendable {
        private let lock = NSLock()
        private var ended: Set<String> = []
        private var waiting: [(write: String, cleanup: String, resume: CheckedContinuation<Void, Never>)] = []
        private var released: [String: [String]] = [:]

        /// Waits until `write` ends. `registered` is told once `cleanup` is on the list, so a
        /// write ended after that releases it.
        func wait(for write: String, as cleanup: String, registered: AsyncStream<Void>.Continuation) async {
            await withCheckedContinuation { (resume: CheckedContinuation<Void, Never>) in
                lock.lock()
                let already = ended.contains(write)
                if already { released[write, default: []].append(cleanup) } else { waiting.append((write, cleanup, resume)) }
                lock.unlock()
                registered.yield()
                if already { resume.resume() }
            }
        }

        func end(_ write: String) {
            lock.lock()
            ended.insert(write)
            let ready = waiting.filter { $0.write == write }
            waiting.removeAll { $0.write == write }
            released[write, default: []] += ready.map(\.cleanup)
            lock.unlock()
            for waiter in ready { waiter.resume.resume() }
        }

        func released(by write: String) -> [String] {
            lock.lock()
            defer { lock.unlock() }
            return released[write] ?? []
        }
    }

    /// Start over twice while the first scan's bundle is written, as the app chains them: the
    /// second scan's write starts only after the first, so each cleanup waits for the newest
    /// write when it listed (the chain head). The first write's end releases only the first
    /// cleanup; the second cleanup waits for the second write, which is held before zipping with
    /// its inputs in place, and finds them intact. The first scan's bundle is kept, the second's
    /// only if its write ran (one queued for a scan already started over is skipped), and the
    /// newest folders are never listed.
    @Test(.timeLimit(.minutes(5)), arguments: [true, false])
    func rapidStartOversWaitForTheWholeChain(secondScanWrites: Bool) async throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let files = FileManager.default
        let ends = WriteEnds()
        let first = try Self.packing("first", in: root)
        let (firstGate, releaseFirst) = AsyncStream<Void>.makeStream()
        let firstWrite = Task {
            for await _ in firstGate { break }
            try? WriteEnd.bundled.apply(to: first)
            ends.end("first")
        }

        // Start over: the second scan's folder is made and the first cleanup lists.
        try Self.scan("second", in: root)
        let firstCleanup = ScanFolderCleanup(root: root, keeping: "second")
        #expect(Self.names(firstCleanup.obsolete) == ["first"])
        // The second scan captures its packet inputs, and its write queues behind the first.
        let second = try Self.packing("second", in: root)
        let secondInputs = second.appending(path: "packet/photos/p00001.jpg")
        let (secondGate, releaseSecond) = AsyncStream<Void>.makeStream()
        let secondWrite = Task { () -> Bool? in
            await firstWrite.value
            guard secondScanWrites else {
                ends.end("second")
                return nil
            }
            for await _ in secondGate { break }
            // Its inputs must still be there; this write never recreates them.
            let intact = FileManager.default.fileExists(atPath: secondInputs.path)
            if intact { try? WriteEnd.bundled.apply(to: second) }
            ends.end("second")
            return intact
        }

        // Start over again: the second cleanup lists both earlier folders.
        try Self.scan("third", in: root)
        let secondCleanup = ScanFolderCleanup(root: root, keeping: "third")
        #expect(Self.names(secondCleanup.obsolete) == ["first", "second"])
        try Self.scan("fourth", in: root)

        let (registered, register) = AsyncStream<Void>.makeStream()
        let deletingFirst = Task {
            await firstCleanup.run(after: { await ends.wait(for: "first", as: "firstCleanup", registered: register) }).map(\.url)
        }
        let deletingSecond = Task {
            await secondCleanup.run(after: { await ends.wait(for: "second", as: "secondCleanup", registered: register) }).map(\.url)
        }
        var registrations = registered.makeAsyncIterator()
        _ = await registrations.next()
        _ = await registrations.next()
        #expect(files.fileExists(atPath: first.appending(path: "packet").path))

        releaseFirst.finish()
        await firstWrite.value
        #expect(ends.released(by: "first") == ["firstCleanup"])
        #expect(await deletingFirst.value.isEmpty)
        #expect(files.fileExists(atPath: first.appending(path: ScanFolderCleanup.bundleName).path))
        if secondScanWrites {
            // The first write has ended and its cleanup has run; the second cleanup still waits.
            #expect(files.fileExists(atPath: secondInputs.path))
        }

        releaseSecond.finish()
        let wrote = await secondWrite.value
        #expect(wrote == (secondScanWrites ? true : nil))
        #expect(ends.released(by: "second") == ["secondCleanup"])
        #expect(await deletingSecond.value.isEmpty)
        var expected: Set<String> = ["first", "third", "fourth"]
        if secondScanWrites { expected.insert("second") }
        #expect(try Set(files.contentsOfDirectory(atPath: root.path)) == expected)
        #expect(files.fileExists(atPath: second.appending(path: ScanFolderCleanup.bundleName).path) == secondScanWrites)
    }

    /// Bundles saved at the same moment keep the order by name, newest name first, as before.
    @Test func equalSaveTimesFallBackToNameOrder() throws {
        let root = try Self.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let bundled = Date(timeIntervalSinceNow: -30 * 60)
        for name in ["a", "b", "c"] {
            try Self.scan(name, in: root, bundledMinutesAgo: 30)
            try FileManager.default.setAttributes([.modificationDate: bundled], ofItemAtPath: root.appending(path: "\(name)/\(ScanFolderCleanup.bundleName)").path)
        }
        let cleanup = ScanFolderCleanup(root: root, keeping: "new")
        #expect(Self.names(cleanup.keptCompleted) == ["c", "b"])
        #expect(Self.names(cleanup.obsolete) == ["a"])
    }

    /// The stamp carries the build, the server and the answer's rules, with nulls where there
    /// are none, so the file always has the same shape.
    @Test func theStampSaysWhatBuiltAndJudgedTheScan() throws {
        let result = try PlacementResult.decode(PlacementResultTests.sampleData())
        let stamp = ScanStamp(
            app: .init(version: "0.1.0", build: "1", commit: "abc123def456-dirty"),
            server: .init(url: "https://house-scanning-server.vercel.app"),
            answer: .init(result, sample: true))
        let value = try JSONSchemaValidator.Value.parse(stamp.jsonData())
        #expect(value["app"]?["commit"]?.string == "abc123def456-dirty")
        #expect(value["server"]?["url"]?.string == "https://house-scanning-server.vercel.app")
        #expect(value["answer"]?["rules_sha256"]?.string == result.policy.rulesSHA256)
        #expect(value["answer"]?["input_sha256"]?.string == result.stats.inputSHA256)
        #expect(value["answer"]?["sample"] == .bool(true))
        let bare = try JSONSchemaValidator.Value.parse(ScanStamp(app: stamp.app, server: .init(url: nil)).jsonData())
        #expect(bare["answer"] == .null && bare["server"]?["url"] == .null)
        #expect(try JSONDecoder().decode(ScanStamp.self, from: stamp.jsonData()) == stamp)
    }
}
