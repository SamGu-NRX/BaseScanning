import Foundation

/// gzip (RFC 1952) around the raw DEFLATE stream Apple's `.zlib` compression writes. The packet's
/// streams travel as `.csv.gz`, and Foundation has no gzip writer of its own.
public enum Gzip {
    public static func compress(_ data: Data) throws -> Data {
        // NSData's .zlib is raw DEFLATE (RFC 1951) with no zlib header, which is what gzip wraps.
        let deflated = try (data as NSData).compressed(using: .zlib) as Data
        var out = Data([0x1F, 0x8B, 0x08, 0x00, 0, 0, 0, 0, 0x00, 0xFF])
        out.append(deflated)
        appendLE(ZipCRC32.checksum(data), to: &out)
        appendLE(UInt32(truncatingIfNeeded: data.count), to: &out)
        return out
    }

    private static func appendLE(_ value: UInt32, to data: inout Data) {
        withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
    }
}
