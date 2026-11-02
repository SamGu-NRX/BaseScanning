import CoreTransferable
import Foundation
import UniformTypeIdentifiers

/// Session folders live in Documents/Sessions/<id>/, which the Files app can also see
/// (UIFileSharingEnabled), so a session can be recovered even if sharing fails.
enum SessionStore {
    private static var root: URL {
        URL.documentsDirectory.appending(path: "Sessions", directoryHint: .isDirectory)
    }

    /// Creates `<root>/<id>/keyframes/` and returns the session folder.
    static func makeFolder(id: String) throws -> URL {
        let folder = root.appending(path: id, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(
            at: folder.appending(path: "keyframes", directoryHint: .isDirectory),
            withIntermediateDirectories: true
        )
        return folder
    }

    static func write(_ manifest: SessionManifest, to folder: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(manifest)
        try data.write(to: folder.appending(path: "session.json"), options: .atomic)
    }

    /// Zips a session folder with the system's own archiver: NSFileCoordinator's `.forUploading`
    /// read hands back a temporary zip of any directory.
    static func zip(folder: URL) throws -> URL {
        let destination = URL.temporaryDirectory.appending(path: "MeasureLab-\(folder.lastPathComponent).zip")
        try? FileManager.default.removeItem(at: destination)
        var coordinationError: NSError?
        var copyError: (any Error)?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: .forUploading, error: &coordinationError) { zipURL in
            do {
                try FileManager.default.copyItem(at: zipURL, to: destination)
            } catch {
                copyError = error
            }
        }
        if let error = coordinationError ?? copyError {
            throw error
        }
        return destination
    }
}

/// A session folder shared as one zip. The zip is made when the share target asks for it, after
/// session.json is flushed. A keyframe still being written at that moment can appear in the zip
/// without a `keyframes` entry; replay code should use only listed keyframes.
struct SessionArchive: Transferable {
    let session: LabSession

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(exportedContentType: .zip) { archive in
            // No zip when the current session.json couldn't be written: the older file on disk
            // may disagree with what the app shows.
            guard let folder = await archive.session.prepareExport() else {
                let reason = await archive.session.storageError ?? "No session folder is open."
                throw CocoaError(.fileWriteUnknown, userInfo: [NSLocalizedDescriptionKey: reason])
            }
            return SentTransferredFile(try SessionStore.zip(folder: folder))
        }
    }
}
