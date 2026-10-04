import Foundation

/// The old scan folders a new scan's store deletes, and the ones it keeps.
///
/// Kept: the new scan's own folder, the `keepCompleted` most recent completed scans (those whose
/// bundle, `bundleName`, is a whole capture packet, newest bundle first) until newer completed
/// scans push them out, and every folder whose bundle is there but not whole. Every other entry
/// goes: scans never packaged, and older completed ones. A scan the homeowner finished survives
/// the app being quit and relaunched, which makes a new store; before, every relaunch deleted it,
/// and a field run's export was lost that way.
///
/// A bundle counts as completed only when `PacketArchiveCheck` finds it written to the end. The
/// zip is written in place, so a quit or crash during the write leaves a partial `scan.zip`. When
/// the file's presence alone counted, that partial bundle took one of the kept places and an
/// intact older scan was deleted to make room.
///
/// A partial or unreadable bundle takes no kept place, and its folder is not deleted, however old
/// it is. Nothing here can tell an abandoned partial from one whose writer is still going: Start
/// over makes a new store while the previous scan's bundle may still be being written, and a
/// suspended phone can pause a write for any length of time, so the file's age proves nothing.
/// Deleting it could take the folder from under that write. The cost is that an abandoned partial
/// bundle stays on the phone. Reclaiming it needs a way to prove no writer holds it, which this
/// type does not have.
///
/// The folders to delete are listed when the new scan's folder is made, before any newer scan
/// can exist, and only those are deleted, whenever the deletion gets to run. Listing at deletion
/// time instead let a cleanup that ran late delete a scan started after it: store A is made,
/// Start over makes B, and A's cleanup then found B in the listing and deleted the scan in use.
///
/// A folder whose bundle is still being built has no `scan.zip` yet, so it is listed as never
/// packaged. The packet is assembled in the folder first and zipped last, and Start over doesn't
/// wait for that write, so deleting on the listing alone took the folder from under it and the
/// scan never reached Saved scans. The app therefore deletes with `run(after:)`, waiting for the
/// bundle writes that were started before the listing; the recheck in `run` then keeps a folder
/// whose bundle landed meanwhile.
public struct ScanFolderCleanup: Sendable {
    /// The bundle Share scan offers. A scan is completed when this file is a whole capture packet
    /// (`PacketArchiveCheck`); the file being there is not enough.
    public static let bundleName = "scan.zip"
    /// The most recent completed scan, and one more: a homeowner who starts another scan still
    /// has the one before it. Each is a few tens of megabytes of photos.
    public static let defaultKeepCompleted = 2
    /// The folders beside the kept one when the cleanup was made, to delete.
    public let obsolete: [URL]
    /// The completed scans kept, newest first.
    public let keptCompleted: [URL]
    /// Folders whose bundle is there but not whole: left alone, holding no kept place.
    public let incompleteBundles: [URL]
    /// What each obsolete entry was when listed. `run` deletes an entry only if it still is.
    private let listedAs: [URL: Entry]

    /// Lists `root` now. An unreadable root lists nothing.
    public init(root: URL, keeping kept: String, keepCompleted: Int = defaultKeepCompleted) {
        let files = FileManager.default
        let names = ((try? files.contentsOfDirectory(atPath: root.path)) ?? []).filter { $0 != kept }.sorted()
        var completed: [(name: String, date: Date)] = []
        var incomplete: [String] = []
        var entries: [String: Entry] = [:]
        for name in names {
            let entry = Self.entry(at: root.appending(path: name))
            entries[name] = entry
            switch entry {
            case .file, .folder(.absent):
                continue
            case .folder(.complete(let date, _)):
                completed.append((name, date))
            case .unjudged, .folder(.incomplete):
                incomplete.append(name)
            }
        }
        completed.sort { ($0.date, $0.name) > ($1.date, $1.name) }
        let keptNames = completed.prefix(max(0, keepCompleted)).map(\.name)
        let keep = Set(keptNames + incomplete)
        keptCompleted = keptNames.map { root.appending(path: $0) }
        incompleteBundles = incomplete.map { root.appending(path: $0) }
        let obsoleteNames = names.filter { !keep.contains($0) }
        obsolete = obsoleteNames.map { root.appending(path: $0) }
        listedAs = Dictionary(uniqueKeysWithValues: obsoleteNames.compactMap { name in entries[name].map { (root.appending(path: name), $0) } })
    }

    /// One entry beside the scan in use.
    enum Entry: Equatable, Sendable {
        /// A plain file: no bundle, so it goes, as before.
        case file
        /// A link or an entry whose type can't be read. A link (attributesOfItem doesn't follow
        /// one) would reach another folder's bundle, so it and its target could take both kept
        /// places; it holds no place and is left alone.
        case unjudged
        /// A real folder, judged by its bundle.
        case folder(BundleState)
    }

    static func entry(at url: URL) -> Entry {
        switch (try? FileManager.default.attributesOfItem(atPath: url.path))?[.type] as? FileAttributeType {
        case .typeDirectory?: .folder(bundleState(url.appending(path: bundleName)))
        case .typeRegular?: .file
        default: .unjudged
        }
    }

    enum BundleState: Equatable, Sendable {
        case absent
        /// `file` is the bundle's file number: a rewrite makes a new file, whatever its date.
        case complete(savedAt: Date, file: UInt64)
        case incomplete
    }

    /// Only a bundle known to be missing is absent. A metadata read that fails for another reason
    /// is incomplete: the folder may hold a bundle, so it is kept without a place.
    ///
    /// A whole bundle must still be the same file after the check. A retry rewrites `scan.zip` in
    /// place (`ZipWriter.write` removes it and starts a new file), and a check that opened the old
    /// file can finish on it after the new partial one has replaced it; the file number tells them
    /// apart. A rewrite that starts after the second read is not caught: the folder then holds its
    /// place while its bundle is partial, until the next cleanup. Closing that needs the writer to
    /// replace the file atomically, which the packet code doesn't do.
    /// `verify` is `PacketArchiveCheck.verify`; tests pass one that rewrites the file mid-check.
    static func bundleState(_ bundle: URL, verify: (URL) throws -> Int64 = PacketArchiveCheck.verify) -> BundleState {
        let files = FileManager.default
        let before: [FileAttributeKey: Any]
        do {
            before = try files.attributesOfItem(atPath: bundle.path)
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileNoSuchFile {
            return .absent
        } catch {
            return .incomplete
        }
        guard (try? verify(bundle)) != nil,
              let after = try? files.attributesOfItem(atPath: bundle.path),
              let number = before[.systemFileNumber] as? UInt64,
              after[.systemFileNumber] as? UInt64 == number,
              after[.size] as? UInt64 == before[.size] as? UInt64 else { return .incomplete }
        return .complete(savedAt: before[.modificationDate] as? Date ?? .distantPast, file: number)
    }

    /// Deletes the listed folders; returns each one that could not be deleted with the reason. A
    /// folder already gone is not an error.
    ///
    /// The deletion runs later than the listing, off the main actor, and a folder can change in
    /// between: a bundle write can start in a folder listed as never packaged, or finish in one.
    /// Each entry is read again and deleted only if it is still what it was when listed; one that
    /// changed is left for the next cleanup. A change after this second read is not caught, so a
    /// write still going when this runs can lose its folder: `run(after:)` waits for known writers.
    public func run() -> [(url: URL, error: any Error)] {
        let files = FileManager.default
        return obsolete.compactMap { url in
            guard files.fileExists(atPath: url.path) else { return nil }
            guard let listed = listedAs[url], Self.entry(at: url) == listed else { return nil }
            do {
                try files.removeItem(at: url)
                return nil
            } catch {
                return (url, error)
            }
        }
    }

    /// `run()`, once `wait` returns. The app's `wait` is the scan bundle writes started before
    /// this cleanup was listed (one chain: each write waits for the one before it), so a folder
    /// listed while its packet was still being assembled is judged again only after that write
    /// ends: a bundle that landed keeps the folder, and a write that failed before zipping leaves
    /// it to be deleted. A `wait` that returns at once, as at launch with no write, is `run()`.
    public func run(after wait: () async -> Void) async -> [(url: URL, error: any Error)] {
        await wait()
        return run()
    }
}
