import CryptoKit
import Foundation
import simd

/// Why the packet writer refused an input. Each names the field, so the caller can fix or drop
/// that one piece instead of guessing.
public enum PacketError: Error, Equatable, CustomStringConvertible {
    case folderNotEmpty(String)
    case invalidID(String)
    case duplicateID(String)
    case invalidTime(where: String, t: Double)
    case timeBeforeStart(where: String, t: Double, start: Double)
    case timeNotIncreasing(stream: String, previous: Double, t: Double)
    case nonFiniteValue(where: String)
    case notRigid(String)
    case invalidPhoto(id: String, reason: String)
    case invalidDepth(id: String, reason: String)
    case invalidDepthFrame(id: String, reason: String)
    case invalidMesh(String)
    case invalidMark(id: String, reason: String)
    case invalidGuidance(id: String, reason: String)
    case invalidPlane(id: String, reason: String)
    case invalidScene(String)
    case invalidSession(String)
    case noPhotos

    /// The refusal as a sentence: which input, and why.
    public var description: String {
        switch self {
        case .folderNotEmpty(let path): "packet folder \(path) already holds files; remove it first"
        case .invalidID(let id): "id \"\(id)\" must be letters, digits, '_' or '-'"
        case .duplicateID(let id): "id \"\(id)\" is used twice"
        case .invalidTime(let place, let t): "\(place): time \(t) is not a finite uptime >= 0"
        case .timeBeforeStart(let place, let t, let start): "\(place): time \(t) is before the capture started at \(start)"
        case .timeNotIncreasing(let stream, let previous, let t): "streams.\(stream): time goes from \(previous) to \(t); it must strictly increase"
        case .nonFiniteValue(let place): "\(place): values must be finite"
        case .notRigid(let place): "\(place): pose is not a rotation plus a translation"
        case .invalidPhoto(let id, let reason): "photo \(id): \(reason)"
        case .invalidDepth(let id, let reason): "photo \(id) depth: \(reason)"
        case .invalidDepthFrame(let id, let reason): "depth frame \(id): \(reason)"
        case .invalidMesh(let reason): "lidar.mesh: \(reason)"
        case .invalidMark(let id, let reason): "mark \(id): \(reason)"
        case .invalidGuidance(let id, let reason): "guidance \(id): \(reason)"
        case .invalidPlane(let id, let reason): "plane \(id): \(reason)"
        case .invalidScene(let reason): "scene.json: \(reason)"
        case .invalidSession(let reason): "session: \(reason)"
        case .noPhotos: "a packet needs at least one photo"
        }
    }
}

/// The packet's binary files and hashes.
public enum PacketFiles {
    /// Lowercase hex SHA-256, the manifest's `sha256`.
    public static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// A manifest file entry for `data` written at `path` in `folder`, creating subfolders.
    static func write(_ data: Data, to path: String, in folder: URL) throws -> PacketManifest.File {
        let url = folder.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        return PacketManifest.File(path: path, bytes: data.count, sha256: sha256(data))
    }

    /// Copies `source` to `path` in `folder` unchanged (a photo keeps its bytes) and describes the
    /// copy.
    static func copy(_ source: URL, to path: String, in folder: URL) throws -> PacketManifest.File {
        let url = folder.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: source, to: url)
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        return PacketManifest.File(path: path, bytes: data.count, sha256: sha256(data))
    }

    /// Float32 little-endian, row by row: the packet's depth map.
    public static func depthData(meters: [Float]) -> Data {
        var data = Data(capacity: meters.count * 4)
        for value in meters {
            withUnsafeBytes(of: value.bitPattern.littleEndian) { data.append(contentsOf: $0) }
        }
        return data
    }

    /// The inverse of `depthData(meters:)`: float32 little-endian values, any trailing partial
    /// value ignored.
    public static func floats(littleEndian data: Data) -> [Float] {
        data.withUnsafeBytes { raw in
            (0..<(raw.count / 4)).map { Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
        }
    }

    /// The mesh as binary little-endian PLY with exactly the header packet/README.md fixes (a
    /// comment line may follow `ply`): float x y z per vertex, then per face a uchar count (3),
    /// three int32 indices and a uchar classification, ARMeshClassification's raw value 0 to 7.
    /// `mesh` must already be in the meter frame, meters (`MeterFrame.mesh(_:)`).
    public static func meshPLY(_ mesh: TriangleMesh, classification: [UInt8]) throws(PacketError) -> Data {
        guard classification.count == mesh.triangleCount else {
            throw .invalidMesh("\(classification.count) classifications for \(mesh.triangleCount) triangles")
        }
        if let bad = classification.first(where: { $0 > maxMeshClassification }) {
            throw .invalidMesh("classification \(bad) is not an ARMeshClassification raw value (0 to 7)")
        }
        guard mesh.vertices.count <= Int(Int32.max) else { throw .invalidMesh("\(mesh.vertices.count) vertices do not fit int32 indices") }
        guard mesh.vertices.allSatisfy(PacketNumber.isFinite) else { throw .invalidMesh("vertices must be finite") }
        let header = """
        ply
        format binary_little_endian 1.0
        comment House Scan LiDAR mesh: meter frame, meters
        element vertex \(mesh.vertices.count)
        property float x
        property float y
        property float z
        element face \(mesh.triangleCount)
        property list uchar int vertex_indices
        property uchar classification
        end_header

        """
        var data = Data(header.utf8)
        data.reserveCapacity(data.count + mesh.vertices.count * 12 + mesh.triangleCount * 14)
        func append(_ bits: UInt32) {
            withUnsafeBytes(of: bits.littleEndian) { data.append(contentsOf: $0) }
        }
        for vertex in mesh.vertices {
            append(vertex.x.bitPattern)
            append(vertex.y.bitPattern)
            append(vertex.z.bitPattern)
        }
        for face in 0..<mesh.triangleCount {
            data.append(3)
            // Indices are below Int32.max (checked above), so their int32 bits are the same.
            append(mesh.indices[3 * face])
            append(mesh.indices[3 * face + 1])
            append(mesh.indices[3 * face + 2])
            data.append(classification[face])
        }
        return data
    }

    /// ARMeshClassification's largest raw value (door).
    static let maxMeshClassification: UInt8 = 7
}
