import simd

/// One ARFrame's own calibration as the recorder stores it beside that frame's pose: the row
/// `Packet04Streams.poseRows(trajectory:intrinsics:tracking:)` joins to the pose with the same `t`.
public enum FrameCalibrationRow {
    /// `[t, fx, fy, cx, cy, width, height]`: ARKit's `camera.intrinsics` (column-major, so fx is
    /// column 0's x, fy column 1's y, cx and cy column 2's x and y) in pixels of the unrotated
    /// landscape sensor image, whose size `camera.imageResolution` gives. Values are kept as ARKit
    /// reported them, with no rotation, scaling or rounding.
    public static func row(t: Double, intrinsics k: simd_float3x3, imageWidth: Double, imageHeight: Double) -> [Double] {
        [t, Double(k.columns.0.x), Double(k.columns.1.y), Double(k.columns.2.x), Double(k.columns.2.y), imageWidth, imageHeight]
    }
}
