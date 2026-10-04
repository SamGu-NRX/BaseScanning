import Foundation
import simd

/// Why the homeowner's accepted meter mark gave the capture packet no tap. Each ends the packet as
/// not prepared rather than letting it go out without its tap (`PhotoProcessingController.meterTapFailed`).
public enum MeterTapFailure: String, Error, Sendable, Equatable, CaseIterable {
    /// ARKit had no current frame to take.
    case noFrame
    /// The frame's tracking wasn't normal, so its pose can't place the tap.
    case trackingLimited
    /// The raycast's hit is behind that frame's camera or outside its image.
    case hitOutsideImage
    /// The frame's image couldn't be encoded as a JPEG.
    case encodeFailed
}

/// One ARFrame's own camera, read once from that frame: its time, raw pose, intrinsics and image
/// size in pixels of the unrotated landscape sensor image, and its tracking.
public struct MeterTapFrame: Sendable, Equatable {
    public var t: Double
    public var cameraToWorld: simd_float4x4
    /// [fx, fy, cx, cy]: ARKit's pinhole K, focal lengths and principal point in pixels from the
    /// image's top-left corner.
    public var intrinsics: SIMD4<Float>
    public var width: Int
    public var height: Int
    public var tracking: PacketTracking

    public init(t: Double, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, width: Int, height: Int, tracking: PacketTracking) {
        self.t = t
        self.cameraToWorld = cameraToWorld
        self.intrinsics = intrinsics
        self.width = width
        self.height = height
        self.tracking = tracking
    }

    /// The tap on this frame: the raycast's world hit projected through this frame's own camera
    /// (`TapObservation.pointing(at:)`), so pixel, pose and intrinsics all belong to one frame.
    /// `jpeg` returns this frame's image, already encoded.
    public func observation(hit: SIMD3<Float>, jpeg: @escaping @Sendable () -> Data?) -> Result<TapObservation, MeterTapFailure> {
        guard tracking == .normal else { return .failure(.trackingLimited) }
        let unaimed = TapObservation(
            t: t, cameraToWorld: cameraToWorld, intrinsics: intrinsics, width: width, height: height, tracking: tracking,
            pixel: SIMD2(Double(width) / 2, Double(height) / 2), jpeg: jpeg)
        guard let aimed = unaimed.pointing(at: hit) else { return .failure(.hitOutsideImage) }
        return .success(aimed)
    }

    /// What the packet records about the raycast's hit, its distance measured from this frame's
    /// camera.
    public func tapHit(_ hit: SIMD3<Float>, estimatedPlane: Bool) -> Packet04.TapHit {
        let camera = SIMD3(cameraToWorld.columns.3.x, cameraToWorld.columns.3.y, cameraToWorld.columns.3.z)
        return Packet04.TapHit(
            position: [hit.x, hit.y, hit.z].map(Double.init), target: estimatedPlane ? "estimatedPlane" : "existingPlaneGeometry",
            alignment: "vertical", distance: Double(simd_distance(hit, camera)))
    }
}
