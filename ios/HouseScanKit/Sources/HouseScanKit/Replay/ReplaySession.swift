import Foundation
import simd

// Reads a recorded capture in the "measure-lab-session" format, version 2, so the engine can be
// run on saved frames instead of a live ARKit session. The format is documented in
// experiments/measure-lab/README.md ("Session format") on the t3/measure-lab branch. This reader
// takes only the keyframes and the first declared wall and ignores every other field, because the
// format is a lab format that keeps gaining fields.

/// One saved keyframe: the image path and the camera that took it.
public struct ReplayFrame: Sendable, Equatable {
    public let id: String
    /// Path of the JPEG relative to the session folder.
    public let imagePath: String
    public let width: Int
    public let height: Int
    /// fx, fy, cx, cy in pixels of the unrotated landscape JPEG.
    public let intrinsics: SIMD4<Float>
    public let cameraToWorld: simd_float4x4
    /// Seconds of device uptime.
    public let timestamp: Double
    /// True only when ARKit reported tracking as "normal" for this frame.
    public let trackingNormal: Bool
    /// The LiDAR depth saved with the frame, or nil when the session recorded none.
    public let depth: ReplayDepthFile?
}

/// Where a keyframe's LiDAR depth lies in a session folder: Float32 little-endian meters,
/// row-major, `width` x `height`, and optionally UInt8 confidence in the same layout (0 low,
/// 1 medium, 2 high). The depth map covers the same field of view as the keyframe's JPEG.
public struct ReplayDepthFile: Sendable, Equatable {
    /// Paths relative to the session folder.
    public let file: String
    public let confidenceFile: String?
    public let width: Int
    public let height: Int
}

/// The wall the person marked during capture, reduced to what a replay needs to check results.
public struct ReplayDeclaredWall: Sendable, Equatable {
    /// World position of the electric meter tapped on this wall.
    public let meter: SIMD3<Float>
    /// Unit horizontal vector pointing out of the wall, toward where the camera stood.
    public let outward: SIMD3<Float>
    /// World height of the ground at the wall's foot.
    public let groundY: Float
}

public struct ReplaySession: Sendable {
    public let id: String
    public let deviceModel: String
    /// Sorted by timestamp, then id.
    public let frames: [ReplayFrame]
    /// The first wall in the session with a meter point on it, or nil if the session has none.
    public let declaredWall: ReplayDeclaredWall?

    public static let format = "measure-lab-session"
    public static let supportedVersion = 2

    public static func decode(sessionJSON: Data) throws -> ReplaySession {
        let decoder = JSONDecoder()
        let header = try decodeMapped(Header.self, from: sessionJSON, decoder: decoder)
        guard let format = header.format else { throw ReplayError.missingField("format") }
        guard format == Self.format else { throw ReplayError.wrongFormat(found: format) }
        guard let version = header.formatVersion else { throw ReplayError.missingField("formatVersion") }
        guard version == Self.supportedVersion else { throw ReplayError.unsupportedVersion(version) }

        let manifest = try decodeMapped(Manifest.self, from: sessionJSON, decoder: decoder)
        guard !manifest.keyframes.isEmpty else { throw ReplayError.noFrames }
        // Typed steps: the one-expression form took about 1.4 s to type-check on Swift 6.4, and
        // CI's Swift 6.2 gives up on expressions like it.
        let unsorted: [ReplayFrame] = try manifest.keyframes.map(makeFrame)
        let frames: [ReplayFrame] = unsorted.sorted { (a: ReplayFrame, b: ReplayFrame) -> Bool in
            if a.timestamp != b.timestamp { return a.timestamp < b.timestamp }
            return a.id < b.id
        }
        return ReplaySession(
            id: manifest.session.id,
            deviceModel: manifest.session.deviceModel,
            frames: frames,
            declaredWall: try makeDeclaredWall(walls: manifest.walls ?? [], points: manifest.points ?? [])
        )
    }

    /// Reads `session.json` inside a session folder. Image paths in the result stay relative to it.
    public static func load(folder: URL) throws -> ReplaySession {
        let url = folder.appendingPathComponent("session.json")
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw ReplayError.unreadable(path: url.path, message: error.localizedDescription)
        }
        return try decode(sessionJSON: data)
    }

    /// Reads a keyframe's depth from the session folder (`ReplayDepthFile`); nil when it has none.
    public static func loadDepth(for frame: ReplayFrame, folder: URL) throws -> DepthImage? {
        guard let file = frame.depth else { return nil }
        func read(_ path: String) throws -> Data {
            let url = folder.appendingPathComponent(path)
            do {
                return try Data(contentsOf: url)
            } catch {
                throw ReplayError.unreadable(path: url.path, message: error.localizedDescription)
            }
        }
        return try depthImage(for: frame, depth: read(file.file), confidence: file.confidenceFile.map(read))
    }

    /// Builds a keyframe's `DepthImage` from the bytes of its depth and confidence files: meters
    /// become millimeters as `DepthImage(meters:...)` converts them, and the intrinsics are the
    /// JPEG's, scaled by the depth map's size over the JPEG's on each axis
    /// (`DepthImage.intrinsics(scaling:from:toWidth:height:)`).
    public static func depthImage(for frame: ReplayFrame, depth: Data, confidence: Data?) throws -> DepthImage {
        guard let file = frame.depth else { throw ReplayError.badDepth(frameID: frame.id, reason: "the keyframe lists no depth") }
        guard frame.width >= 1, frame.height >= 1 else {
            throw ReplayError.badDepth(frameID: frame.id, reason: "the JPEG size \(frame.width) x \(frame.height) can't scale intrinsics")
        }
        let count = file.width * file.height
        guard depth.count == count * 4 else {
            throw ReplayError.badDepth(frameID: frame.id, reason: "depth has \(depth.count) bytes, expected \(count * 4) for \(file.width) x \(file.height) Float32")
        }
        if let confidence, confidence.count != count {
            throw ReplayError.badDepth(frameID: frame.id, reason: "confidence has \(confidence.count) bytes, expected \(count)")
        }
        let meters: [Float] = depth.withUnsafeBytes { raw in
            (0..<count).map { Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
        }
        let intrinsics = DepthImage.intrinsics(
            scaling: frame.intrinsics, from: SIMD2(Float(frame.width), Float(frame.height)), toWidth: file.width, height: file.height)
        return DepthImage(meters: meters, width: file.width, height: file.height, confidence: confidence.map { [UInt8]($0) }, intrinsics: intrinsics)
    }
}

public enum ReplayError: Error, Equatable, CustomStringConvertible {
    case wrongFormat(found: String)
    case unsupportedVersion(Int)
    /// A required field is absent or null; the value is its path, for example "keyframes[3].pose".
    case missingField(String)
    /// The pose is not 16 finite numbers.
    case badPose(frameID: String)
    /// The keyframe image dimensions are not a positive landscape sensor image.
    case badImageSize(frameID: String, reason: String)
    /// The intrinsics are not 4 finite numbers with positive focal lengths.
    case badIntrinsics(frameID: String)
    case noFrames
    /// A keyframe's depth entry or files do not match: `reason` says how.
    case badDepth(frameID: String, reason: String)
    /// The file could not be read, or its contents are not the JSON shape this reader expects.
    case unreadable(path: String, message: String)

    public var description: String {
        switch self {
        case .wrongFormat(let found):
            "session.json has format \"\(found)\", expected \"\(ReplaySession.format)\""
        case .unsupportedVersion(let version):
            "session.json has formatVersion \(version); this reader supports only \(ReplaySession.supportedVersion)"
        case .missingField(let path):
            "session.json is missing required field \(path)"
        case .badPose(let frameID):
            "keyframe \(frameID) has a pose that is not 16 finite numbers"
        case .badImageSize(let frameID, let reason):
            "keyframe \(frameID) image size: \(reason)"
        case .badIntrinsics(let frameID):
            "keyframe \(frameID) has intrinsics that are not [fx, fy, cx, cy] with positive focal lengths"
        case .noFrames:
            "session.json lists no keyframes"
        case .badDepth(let frameID, let reason):
            "keyframe \(frameID) depth: \(reason)"
        case .unreadable(let path, let message):
            "cannot read \(path): \(message)"
        }
    }
}

// MARK: - Wire format (only the fields this reader uses)

private struct Header: Decodable {
    let format: String?
    let formatVersion: Int?
}

private struct Manifest: Decodable {
    struct Session: Decodable {
        let id: String
        let deviceModel: String
    }

    struct Keyframe: Decodable {
        let id: String
        let img: String
        let w: Int
        let h: Int
        let intrinsics: [Double]
        let pose: [Double]
        let timestamp: Double
        let tracking: String
        let depth: Depth?
    }

    struct Depth: Decodable {
        let file: String
        let confidenceFile: String?
        let w: Int
        let h: Int
    }

    struct Point: Decodable {
        struct OnWall: Decodable {
            let wall: String
        }

        let id: String
        let kind: String
        let position: [Double]
        let onWall: OnWall?
    }

    struct Wall: Decodable {
        let id: String
        let start: [Double]
        let end: [Double]
        let normal: [Double]
    }

    let session: Session
    let keyframes: [Keyframe]
    let points: [Point]?
    let walls: [Wall]?
}

private func decodeMapped<T: Decodable>(_ type: T.Type, from data: Data, decoder: JSONDecoder) throws -> T {
    do {
        return try decoder.decode(type, from: data)
    } catch let error as DecodingError {
        switch error {
        case .keyNotFound(let key, let context):
            throw ReplayError.missingField(fieldPath(context.codingPath + [key]))
        case .valueNotFound(_, let context):
            throw ReplayError.missingField(fieldPath(context.codingPath))
        case .typeMismatch(_, let context), .dataCorrupted(let context):
            let at = context.codingPath.isEmpty ? "" : " at \(fieldPath(context.codingPath))"
            throw ReplayError.unreadable(path: "session.json", message: context.debugDescription + at)
        @unknown default:
            throw ReplayError.unreadable(path: "session.json", message: String(describing: error))
        }
    }
}

/// "keyframes[3].pose" style path for error messages.
private func fieldPath(_ keys: [any CodingKey]) -> String {
    keys.reduce(into: "") { path, key in
        if let index = key.intValue {
            path += "[\(index)]"
        } else {
            path += path.isEmpty ? key.stringValue : ".\(key.stringValue)"
        }
    }
}

private func makeFrame(_ k: Manifest.Keyframe) throws -> ReplayFrame {
    guard k.w >= 1, k.h >= 1 else {
        throw ReplayError.badImageSize(frameID: k.id, reason: "width and height must be integers of at least 1")
    }
    guard k.w > k.h else {
        let reason = k.w == k.h
            ? "square keyframe; replay requires a landscape sensor image"
            : "portrait keyframe; replay requires a landscape sensor image"
        throw ReplayError.badImageSize(frameID: k.id, reason: reason)
    }
    guard k.pose.count == 16, k.pose.allSatisfy(\.isFinite) else { throw ReplayError.badPose(frameID: k.id) }
    guard k.intrinsics.count == 4, k.intrinsics.allSatisfy(\.isFinite), k.intrinsics[0] > 0, k.intrinsics[1] > 0
    else { throw ReplayError.badIntrinsics(frameID: k.id) }
    if let depth = k.depth, depth.w < 1 || depth.h < 1 {
        throw ReplayError.badDepth(frameID: k.id, reason: "size \(depth.w) x \(depth.h)")
    }
    let p = k.pose.map(Float.init)
    let column = { (i: Int) in SIMD4<Float>(p[i * 4], p[i * 4 + 1], p[i * 4 + 2], p[i * 4 + 3]) }
    return ReplayFrame(
        id: k.id,
        imagePath: k.img,
        width: k.w,
        height: k.h,
        intrinsics: SIMD4(k.intrinsics.map(Float.init)),
        cameraToWorld: simd_float4x4(columns: (column(0), column(1), column(2), column(3))),
        timestamp: k.timestamp,
        trackingNormal: k.tracking == "normal",
        depth: k.depth.map { ReplayDepthFile(file: $0.file, confidenceFile: $0.confidenceFile, width: $0.w, height: $0.h) }
    )
}

private func vector3(_ values: [Double], _ field: String) throws -> SIMD3<Float> {
    guard values.count == 3, values.allSatisfy(\.isFinite) else {
        throw ReplayError.unreadable(path: "session.json", message: "\(field) is not 3 finite numbers")
    }
    return SIMD3(values.map(Float.init))
}

/// First wall only. The format stores each wall's normal already flipped to face the camera that
/// marked it, so the horizontal part of that normal is the outward direction.
private func makeDeclaredWall(walls: [Manifest.Wall], points: [Manifest.Point]) throws -> ReplayDeclaredWall? {
    guard let wall = walls.first else { return nil }
    guard let meterIndex = points.firstIndex(where: { $0.kind == "wall" && $0.onWall?.wall == wall.id }) else {
        return nil
    }
    let meter = try vector3(points[meterIndex].position, "points[\(meterIndex)].position")
    let normal = try vector3(wall.normal, "walls[0].normal")
    let horizontal = SIMD3<Float>(normal.x, 0, normal.z)
    guard simd_length(horizontal) > 1e-3 else {
        throw ReplayError.unreadable(path: "session.json", message: "walls[0].normal has no horizontal component")
    }
    let start = try vector3(wall.start, "walls[0].start")
    let end = try vector3(wall.end, "walls[0].end")
    return ReplayDeclaredWall(meter: meter, outward: simd_normalize(horizontal), groundY: (start.y + end.y) / 2)
}
