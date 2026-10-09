import Foundation
#if canImport(Compression)
import Compression
#endif

// ZIP writer for the scan bundle: JPEGs, motion CSVs, manifest.json, scene.json and on LiDAR
// phones a mesh and depth maps. Entries that shrink are deflated (method 8) with Apple's
// Compression framework, whose ZLIB algorithm writes raw RFC 1951 DEFLATE, the stream ZIP stores.
// JPEGs skip the attempt, since they do not compress, and anything that would not get smaller is
// stored (method 0). Without the Compression framework every entry is stored.
// Layout per PKWARE APPNOTE 6.3.x: local header + data for each file, then the central directory,
// then the end-of-central-directory record. No ZIP64: anything that would need it throws.

/// One file of the scan bundle's archive: a path inside it and the bytes to write there.
public struct ZipEntry: Sendable, Equatable {
    /// Path inside the archive, forward slashes, UTF-8.
    public var name: String
    /// The entry's bytes, uncompressed. The CRC and the stored size always describe these,
    /// whichever method the entry is written with.
    public var data: Data

    /// An entry: `name` inside the archive, `data` as it will be read back.
    public init(name: String, data: Data) {
        self.name = name
        self.data = data
    }
}

/// Why `ZipWriter` refuses an archive: a name it cannot write, a name it has already written, or
/// a size or date the format holds no room for without ZIP64.
public enum ZipWriterError: Error, Equatable, CustomStringConvertible {
    case tooManyEntries(Int)
    case entryTooLarge(name: String, bytes: Int)
    case archiveTooLarge
    case invalidName(String, reason: String)
    case duplicateName(String)
    case dateOutOfRange(Date)

    /// The error in words, naming the entry and the limit it broke.
    public var description: String {
        switch self {
        case .tooManyEntries(let n): "\(n) entries; a ZIP without ZIP64 holds at most 65535"
        case .entryTooLarge(let name, let bytes): "\(name) is \(bytes) bytes; a ZIP without ZIP64 holds at most 4 GiB per file"
        case .archiveTooLarge: "archive exceeds 4 GiB, which needs ZIP64"
        case .invalidName(let name, let reason): "invalid entry name \"\(name)\": \(reason)"
        case .duplicateName(let name): "duplicate entry name \"\(name)\""
        case .dateOutOfRange(let date): "\(date) is outside the DOS date range 1980-2107"
        }
    }
}

/// The checksum each ZIP entry carries, so a reader can tell the bytes arrived whole
/// (`ZipCRC32.checksum`).
public enum ZipCRC32 {
    private static let table: [UInt32] = (0..<256).map { n in
        var c = UInt32(n)
        for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
        return c
    }

    /// CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320) as ZIP uses it.
    public static func checksum(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xFFFF_FFFF
        data.withUnsafeBytes { buffer in
            for byte in buffer {
                crc = table[Int((crc ^ UInt32(byte)) & 0xFF)] ^ (crc >> 8)
            }
        }
        return crc ^ 0xFFFF_FFFF
    }
}

/// The scan bundle's ZIP writer, per PKWARE APPNOTE 6.3.x: entries that shrink are deflated
/// (method 8, Apple's Compression framework), already-compressed image formats are stored as
/// they are, and everything else is stored when deflating would not make it smaller. No ZIP64:
/// an archive past the format's limits throws (`ZipWriterError`). Build in memory
/// (`ZipWriter.archive`) or stream to a file one entry at a time (`ZipWriter.write`).
public enum ZipWriter {
    /// Builds a ZIP archive in memory, deflating each entry that shrinks and storing the rest.
    /// - Parameters:
    ///   - modified: timestamp written for every entry; nil writes the DOS epoch 1980-01-01 00:00 so
    ///     the same inputs give byte-identical archives on the same OS build (the DEFLATE bytes come
    ///     from Apple's encoder, which can change between releases).
    ///   - timeZone: zone the DOS local time is expressed in.
    public static func archive(_ entries: [ZipEntry], modified: Date? = nil, timeZone: TimeZone = .gmt) throws -> Data {
        var out = Data()
        try build(entries.map { entry in (entry.name, { entry.data }) }, modified: modified, timeZone: timeZone) { out.append($0) }
        return out
    }

    /// Writes the same archive as `archive` to `url`, loading one entry at a time, so memory holds
    /// one file, its compressed copy and the central directory instead of the whole bundle. A
    /// compressed copy is never allowed to reach the file's own size. Replaces any file at `url`;
    /// on a throw the partial file is removed.
    /// - Parameter entries: each entry's name and a loader called once, in order.
    public static func write(
        _ entries: [(name: String, load: () throws -> Data)], to url: URL, modified: Date? = nil, timeZone: TimeZone = .gmt
    ) throws {
        let fm = FileManager.default
        try? fm.removeItem(at: url)
        guard fm.createFile(atPath: url.path, contents: nil) else {
            throw CocoaError(.fileWriteUnknown, userInfo: [NSFilePathErrorKey: url.path])
        }
        do {
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try build(entries, modified: modified, timeZone: timeZone) { try handle.write(contentsOf: $0) }
        } catch {
            try? fm.removeItem(at: url)
            throw error
        }
    }

    private static func build(
        _ entries: [(name: String, load: () throws -> Data)], modified: Date?, timeZone: TimeZone, emit: (Data) throws -> Void
    ) throws {
        guard entries.count <= 0xFFFF else { throw ZipWriterError.tooManyEntries(entries.count) }
        let (dosTime, dosDate) = try dosTimestamp(modified, timeZone: timeZone)

        var seen = Set<String>()
        var written = 0
        var central = Data()
        for entry in entries {
            let name = Data(entry.name.utf8)
            guard !name.isEmpty else { throw ZipWriterError.invalidName(entry.name, reason: "empty") }
            guard name.count <= 0xFFFF else { throw ZipWriterError.invalidName(entry.name, reason: "longer than 65535 bytes") }
            guard !entry.name.hasPrefix("/"), !entry.name.contains("\\") else {
                throw ZipWriterError.invalidName(entry.name, reason: "must be relative with forward slashes")
            }
            guard seen.insert(entry.name).inserted else { throw ZipWriterError.duplicateName(entry.name) }
            let data = try entry.load()
            guard data.count <= 0xFFFF_FFFF else {
                throw ZipWriterError.entryTooLarge(name: entry.name, bytes: data.count)
            }
            guard written <= 0xFFFF_FFFF else { throw ZipWriterError.archiveTooLarge }

            let offset = UInt32(written)
            // The CRC and the uncompressed size describe the raw data whichever method is used.
            let crc = ZipCRC32.checksum(data)
            let size = UInt32(data.count)
            let packed = isPrecompressed(entry.name) ? nil : deflated(data)
            let method = packed == nil ? methodStored : methodDeflated
            let body = packed ?? data
            let compressedSize = UInt32(body.count)

            var local = Data()
            local.appendLE(UInt32(0x0403_4B50))  // local file header signature
            local.appendLE(versionNeeded)
            local.appendLE(flagUTF8)
            local.appendLE(method)
            local.appendLE(dosTime)
            local.appendLE(dosDate)
            local.appendLE(crc)
            local.appendLE(compressedSize)
            local.appendLE(size)  // uncompressed size
            local.appendLE(UInt16(name.count))
            local.appendLE(UInt16(0))  // extra field length
            local.append(name)
            try emit(local)
            try emit(body)
            written += local.count + body.count

            central.appendLE(UInt32(0x0201_4B50))  // central directory header signature
            central.appendLE(versionMadeBy)
            central.appendLE(versionNeeded)
            central.appendLE(flagUTF8)
            central.appendLE(method)
            central.appendLE(dosTime)
            central.appendLE(dosDate)
            central.appendLE(crc)
            central.appendLE(compressedSize)
            central.appendLE(size)
            central.appendLE(UInt16(name.count))
            central.appendLE(UInt16(0))  // extra field length
            central.appendLE(UInt16(0))  // comment length
            central.appendLE(UInt16(0))  // disk number start
            central.appendLE(UInt16(0))  // internal attributes
            central.appendLE(regularFileMode << 16)  // external attributes: Unix mode in the high half
            central.appendLE(offset)
            central.append(name)
        }

        guard written <= 0xFFFF_FFFF, central.count <= 0xFFFF_FFFF,
              written + central.count <= 0xFFFF_FFFF else { throw ZipWriterError.archiveTooLarge }
        var tail = central
        tail.appendLE(UInt32(0x0605_4B50))  // end of central directory signature
        tail.appendLE(UInt16(0))  // this disk
        tail.appendLE(UInt16(0))  // disk with the central directory
        tail.appendLE(UInt16(entries.count))
        tail.appendLE(UInt16(entries.count))
        tail.appendLE(UInt32(central.count))
        tail.appendLE(UInt32(written))
        tail.appendLE(UInt16(0))  // comment length
        try emit(tail)
    }

    /// 2.0: the lowest version that defines the UTF-8 name flag readers check against, and the
    /// version APPNOTE asks for when an entry is deflated.
    private static let versionNeeded: UInt16 = 20
    private static let methodStored: UInt16 = 0
    private static let methodDeflated: UInt16 = 8
    /// Already-compressed formats: deflating them costs time and saves nothing.
    private static let precompressedSuffixes = [".jpg", ".jpeg", ".heic", ".png"]
    /// High byte 3 = Unix, so readers apply the mode in the external attributes.
    private static let versionMadeBy: UInt16 = (3 << 8) | 20
    /// General purpose bit 11: names are UTF-8.
    private static let flagUTF8: UInt16 = 1 << 11
    /// S_IFREG | 0644.
    private static let regularFileMode: UInt32 = 0o100644

    private static func isPrecompressed(_ name: String) -> Bool {
        let lower = name.lowercased()
        return precompressedSuffixes.contains { lower.hasSuffix($0) }
    }

    /// Raw DEFLATE of `data` when that is strictly smaller than `data`, else nil (store it).
    /// Streams through a fixed 64 KiB buffer, so the only memory added next to the loaded entry is
    /// the compressed output, and gives up as soon as that output is no smaller than the input.
    private static func deflated(_ data: Data) -> Data? {
        #if canImport(Compression)
        guard data.count > 1 else { return nil }
        let chunk = 64 * 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: chunk)
        defer { buffer.deallocate() }
        let stream = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
        defer { stream.deallocate() }
        // COMPRESSION_ZLIB is raw RFC 1951 DEFLATE, no zlib header: ZIP method 8.
        guard compression_stream_init(stream, COMPRESSION_STREAM_ENCODE, COMPRESSION_ZLIB) == COMPRESSION_STATUS_OK else {
            return nil
        }
        defer { compression_stream_destroy(stream) }
        return data.withUnsafeBytes { (src: UnsafeRawBufferPointer) -> Data? in
            guard let srcBase = src.bindMemory(to: UInt8.self).baseAddress else { return nil }
            stream.pointee.src_ptr = srcBase
            stream.pointee.src_size = data.count
            var out = Data()
            while true {
                stream.pointee.dst_ptr = buffer
                stream.pointee.dst_size = chunk
                let status = compression_stream_process(stream, Int32(COMPRESSION_STREAM_FINALIZE.rawValue))
                guard status != COMPRESSION_STATUS_ERROR else { return nil }
                out.append(buffer, count: chunk - stream.pointee.dst_size)
                guard out.count < data.count else { return nil }  // no saving: store it
                if status == COMPRESSION_STATUS_END { return out }
            }
        }
        #else
        return nil
        #endif
    }

    private static func dosTimestamp(_ date: Date?, timeZone: TimeZone) throws -> (time: UInt16, date: UInt16) {
        guard let date else { return (0, (1 << 5) | 1) }  // 1980-01-01 00:00:00
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let c = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        guard let year = c.year, let month = c.month, let day = c.day,
              let hour = c.hour, let minute = c.minute, let second = c.second,
              (1980...2107).contains(year) else { throw ZipWriterError.dateOutOfRange(date) }
        let time = UInt16(hour << 11 | minute << 5 | second / 2)
        let dosDate = UInt16((year - 1980) << 9 | month << 5 | day)
        return (time, dosDate)
    }
}

extension Data {
    fileprivate mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        Swift.withUnsafeBytes(of: value.littleEndian) { append(contentsOf: $0) }
    }
}
