import Foundation
import simd

/// One keyframe's LiDAR depth: metres along camera -z (not along the ray), 0 where nothing was hit.
public struct DepthMap: Sendable {
    public let width: Int
    public let height: Int
    public let meters: [Float]
    /// 0 low, 1 medium, 2 high. All high when the recording had no confidence file.
    public let confidence: [UInt8]
    /// fx, fy, cx, cy in depth-map pixels.
    public let intrinsics: SIMD4<Float>

    public init(meters: [Float], confidence: [UInt8], width: Int, height: Int, intrinsics: SIMD4<Float>) {
        precondition(meters.count == width * height && confidence.count == width * height, "depth arrays must match the map size")
        self.meters = meters
        self.confidence = confidence
        self.width = width
        self.height = height
        self.intrinsics = intrinsics
    }

    /// Decodes the raw files. Byte counts are checked against the declared size before anything
    /// is read, and a mismatch names the file and both counts.
    public init(
        depth: Data, depthPath: String, confidence: Data?, confidencePath: String?,
        width: Int, height: Int, intrinsics: SIMD4<Float>
    ) throws(FixtureError) {
        let count = width * height
        guard depth.count == count * 4 else {
            throw .depthByteCount(path: depthPath, found: depth.count, expected: count * 4, width: width, height: height)
        }
        if let confidence, confidence.count != count {
            throw .confidenceByteCount(path: confidencePath ?? "confidence", found: confidence.count, expected: count, width: width, height: height)
        }
        let meters: [Float] = depth.withUnsafeBytes { raw in
            (0..<count).map { Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
        }
        self.init(
            meters: meters, confidence: confidence.map { [UInt8]($0) } ?? [UInt8](repeating: 2, count: count),
            width: width, height: height, intrinsics: intrinsics)
    }

    /// Depth at the pixel containing continuous coordinate (u, v), or nil outside the map.
    public func depth(atU u: Float, v: Float) -> Float? {
        index(atU: u, v: v).map { meters[$0] }
    }

    /// Confidence at the pixel containing continuous coordinate (u, v), or nil outside the map.
    public func confidence(atU u: Float, v: Float) -> UInt8? {
        index(atU: u, v: v).map { confidence[$0] }
    }

    private func index(atU u: Float, v: Float) -> Int? {
        guard u >= 0, v >= 0 else { return nil }
        let i = Int(u), j = Int(v)
        guard i < width, j < height else { return nil }
        return j * width + i
    }
}

public enum CameraMath {
    /// Camera-space point for continuous pixel (u, v) at depth `depth` along -z. A pixel's centre
    /// is (i + 0.5, j + 0.5), which is how the fixture generator cast its rays.
    public static func unproject(u: Float, v: Float, depth: Float, intrinsics k: SIMD4<Float>) -> SIMD3<Float> {
        SIMD3((u - k.z) * depth / k.x, -(v - k.w) * depth / k.y, -depth)
    }

    /// Continuous pixel and depth of a camera-space point, or nil when it is not in front.
    public static func project(_ p: SIMD3<Float>, intrinsics k: SIMD4<Float>) -> (u: Float, v: Float, depth: Float)? {
        let depth = -p.z
        guard depth > 1e-4 else { return nil }
        return (k.x * p.x / depth + k.z, -k.y * p.y / depth + k.w, depth)
    }

    public static func transform(_ m: simd_float4x4, _ p: SIMD3<Float>) -> SIMD3<Float> {
        let r = m * SIMD4(p, 1)
        return SIMD3(r.x, r.y, r.z)
    }

    public static func rotate(_ m: simd_float4x4, _ v: SIMD3<Float>) -> SIMD3<Float> {
        let r = m * SIMD4(v, 0)
        return SIMD3(r.x, r.y, r.z)
    }
}
