import Foundation
import simd

/// An `ARPlaneAnchor` in the meter frame (packet/README.md, "Planes"). The plane is the local x-z
/// plane of `pose`, with +y its normal. `pose` sits at the centre of the plane's extent and is
/// turned by the extent's rotation, so `extent` is a rectangle centred on it and `boundary` is in
/// the same frame. ARKit's anchor transform is neither: `init(id:alignment:classification:
/// anchorToMeter:center:rotationOnYAxis:extent:boundaryVertices:)` builds both from what ARKit
/// reports.
public struct PacketPlane: Codable, Sendable, Equatable {
    /// Whether the plane lies flat or stands up, as ARKit names them
    /// (`ARPlaneAnchor.alignment`).
    public enum Alignment: String, Codable, Sendable {
        case horizontal
        case vertical
    }

    /// `ARPlaneAnchor.Classification`, as the packet names it.
    public enum Classification: String, Codable, Sendable, CaseIterable {
        case none, wall, floor, ceiling, table, seat, window, door
    }

    /// Unique among the packet's planes (`PacketWriter.addPlane` refuses a duplicate).
    public var id: String
    /// Whether the plane lies flat or stands up (`Alignment`).
    public var alignment: Alignment
    /// What ARKit took the surface for (`Classification`), when it said.
    public var classification: Classification?
    /// Plane to meter frame, at the extent's centre and turned by its rotation.
    public var pose: simd_float4x4
    /// Size along the pose's x and z, meters.
    public var extent: SIMD2<Float>
    /// `ARPlaneGeometry.boundaryVertices` as (x, z) in the pose's frame; nil when not recorded.
    /// ARKit's extent often claims a full rectangle over an L of wall or a wall broken by a
    /// window, and the boundary is the outline it actually found.
    public var boundary: [SIMD2<Float>]?

    /// How far a boundary vertex may lie past the extent's edge: float rounding and 1 cm, the
    /// validator's `BOUNDARY_TOL_M`.
    public static let boundaryTolerance: Float = 0.01

    /// The direct form: `pose` already sits at the extent's centre, turned by the extent's
    /// rotation, and `boundary` is already in the pose's frame. ARKit's anchor-shaped input goes
    /// through `init(id:alignment:classification:anchorToMeter:center:rotationOnYAxis:
    /// extent:boundaryVertices:)`.
    public init(
        id: String, alignment: Alignment, classification: Classification?, pose: simd_float4x4, extent: SIMD2<Float>,
        boundary: [SIMD2<Float>]? = nil
    ) {
        self.id = id
        self.alignment = alignment
        self.classification = classification
        self.pose = pose
        self.extent = extent
        self.boundary = boundary
    }

    /// A plane from what ARKit reports about its anchor.
    ///
    /// - `anchorToMeter`: the anchor's transform in the meter frame (`MeterFrame.pose(_:)` of
    ///   `ARPlaneAnchor.transform`).
    /// - `center`: `ARPlaneAnchor.center`, in the anchor's coordinates.
    /// - `rotationOnYAxis`: `ARPlaneExtent.rotationOnYAxis`, radians about the anchor's +y.
    /// - `extent`: `ARPlaneExtent.width` and `height`, along the turned x and z.
    /// - `boundaryVertices`: `ARPlaneGeometry.boundaryVertices`, in the anchor's coordinates.
    ///
    /// The pose is `anchorToMeter · extentInAnchor(center:rotationOnYAxis:)`, and each boundary
    /// vertex is moved by the inverse of `extentInAnchor` before its y (about 0 on the plane) is
    /// dropped. A boundary left in the anchor's coordinates would be off-centre by `center` and
    /// turned by the rotation; `problem()` catches it leaving the extent.
    public init(
        id: String, alignment: Alignment, classification: Classification?, anchorToMeter: simd_float4x4,
        center: SIMD3<Float>, rotationOnYAxis: Float, extent: SIMD2<Float>, boundaryVertices: [SIMD3<Float>]
    ) {
        let extentFrame = Self.extentInAnchor(center: center, rotationOnYAxis: rotationOnYAxis)
        var pose = anchorToMeter * extentFrame
        // Both factors end in (0, 0, 0, 1), so the product does too; set it exactly anyway, as
        // `MeterFrame.pose(_:)` does, since the validator checks it to 1e-9.
        pose.columns.0.w = 0
        pose.columns.1.w = 0
        pose.columns.2.w = 0
        pose.columns.3.w = 1
        self.init(
            id: id, alignment: alignment, classification: classification, pose: pose, extent: extent,
            boundary: boundaryVertices.isEmpty ? nil : boundaryVertices.map {
                Self.extentPoint($0, center: center, rotationOnYAxis: rotationOnYAxis)
            }
        )
    }

    /// The extent's frame in the anchor's: translate to `center`, then turn by `angle` about +y
    /// (right-handed, as `simd_quatf(angle:axis:)` turns). Its columns are the extent's x axis
    /// (cos, 0, -sin), the anchor's y, the extent's z axis (sin, 0, cos), and the centre.
    public static func extentInAnchor(center: SIMD3<Float>, rotationOnYAxis angle: Float) -> simd_float4x4 {
        let c = cos(angle)
        let s = sin(angle)
        return simd_float4x4(SIMD4(c, 0, -s, 0), SIMD4(0, 1, 0, 0), SIMD4(s, 0, c, 0), SIMD4(center, 1))
    }

    /// A point in the anchor's coordinates as (x, z) in the extent's frame: the offset from the
    /// centre, projected on the extent's x and z axes.
    static func extentPoint(_ p: SIMD3<Float>, center: SIMD3<Float>, rotationOnYAxis angle: Float) -> SIMD2<Float> {
        let d = p - center
        let c = cos(angle)
        let s = sin(angle)
        return SIMD2(c * d.x - s * d.z, s * d.x + c * d.z)
    }

    /// The problem the validator would report, if any.
    func problem() -> String? {
        if !PacketPose.isRigid(pose) { return "pose is not a rotation plus a translation" }
        if !(extent.x >= 0 && extent.y >= 0 && extent.x.isFinite && extent.y.isFinite) { return "extent \(extent) must be finite and >= 0" }
        guard let boundary else { return nil }
        if boundary.count < 3 { return "boundary has \(boundary.count) vertices; an outline needs at least 3" }
        if !boundary.allSatisfy({ $0.x.isFinite && $0.y.isFinite }) { return "boundary must be finite" }
        let half = extent / 2 + Self.boundaryTolerance
        if let outside = boundary.first(where: { abs($0.x) > half.x || abs($0.y) > half.y }) {
            return "boundary vertex (\(outside.x), \(outside.y)) leaves the \(extent.x) x \(extent.y) m extent centred on the pose; "
                + "is it still in the anchor's coordinates?"
        }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id, alignment, classification, pose
        case extent = "extent_m"
        case boundary = "boundary_m"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        alignment = try c.decode(Alignment.self, forKey: .alignment)
        classification = try c.decodeIfPresent(Classification.self, forKey: .classification)
        let numbers = try c.decode([Float].self, forKey: .pose)
        guard numbers.count == 16 else {
            throw DecodingError.dataCorruptedError(forKey: .pose, in: c, debugDescription: "a pose has 16 numbers")
        }
        pose = simd_float4x4(
            SIMD4(numbers[0], numbers[1], numbers[2], numbers[3]), SIMD4(numbers[4], numbers[5], numbers[6], numbers[7]),
            SIMD4(numbers[8], numbers[9], numbers[10], numbers[11]), SIMD4(numbers[12], numbers[13], numbers[14], numbers[15]))
        let size = try c.decode([Float].self, forKey: .extent)
        guard size.count == 2 else {
            throw DecodingError.dataCorruptedError(forKey: .extent, in: c, debugDescription: "extent_m has 2 numbers")
        }
        extent = SIMD2(size[0], size[1])
        boundary = try c.decodeIfPresent([[Float]].self, forKey: .boundary).map { points in
            try points.map { xz in
                guard xz.count == 2 else {
                    throw DecodingError.dataCorruptedError(forKey: .boundary, in: c, debugDescription: "a boundary vertex is [x, z]")
                }
                return SIMD2(xz[0], xz[1])
            }
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(alignment, forKey: .alignment)
        try c.encodeIfPresent(classification, forKey: .classification)
        try c.encode(PacketPose.columnMajor(pose), forKey: .pose)
        try c.encode([extent.x, extent.y].map(PacketNumber.double), forKey: .extent)
        try c.encodeIfPresent(boundary.map { $0.map { [$0.x, $0.y].map(PacketNumber.double) } }, forKey: .boundary)
    }
}
