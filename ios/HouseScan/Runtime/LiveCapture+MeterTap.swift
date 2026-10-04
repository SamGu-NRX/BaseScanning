import ARKit
import HouseScanKit

/// The meter tap's one-frame snapshot, for photo processing's capture packet: taken only when the
/// homeowner's meter mark has been accepted, never on a timer (`LiveMode` and the walk's encoding
/// are unchanged).
extension LiveCapture {
    /// What one ARFrame gave for the tap: its own camera, the image to encode, or why there is none.
    enum MeterTapSnapshot {
        case captured(MeterTapFrame, image: LiveSessionDelegate.PixelBufferBox)
        case failed(MeterTapFailure)
    }

    /// ARKit's current frame, read once, in the same main-actor turn as the accepted raycast. Its
    /// time, raw pose, intrinsics, image size, tracking and image all come from that one frame;
    /// nothing is taken from the engine's sampled frames. The image is kept only until it is
    /// encoded (`encodeMeterTapImage`), as the walk's encode keeps its own.
    func meterTapSnapshot() -> MeterTapSnapshot {
        guard let frame = arView.session.currentFrame else { return .failed(.noFrame) }
        let k = frame.camera.intrinsics
        let size = frame.camera.imageResolution
        let tracking: PacketTracking = switch frame.camera.trackingState {
        case .normal: .normal
        case .notAvailable: .notAvailable
        case .limited(_): .limited(nil)
        }
        let camera = MeterTapFrame(
            t: frame.timestamp, cameraToWorld: frame.camera.transform,
            intrinsics: SIMD4(k.columns.0.x, k.columns.1.y, k.columns.2.x, k.columns.2.y),
            width: Int(size.width), height: Int(size.height), tracking: tracking)
        return .captured(camera, image: LiveSessionDelegate.PixelBufferBox(buffer: frame.capturedImage))
    }

    /// Encodes the snapshot's image off the main actor with the walk's encoder. Nil when it fails.
    func encodeMeterTapImage(_ image: LiveSessionDelegate.PixelBufferBox) async -> Data? {
        let delegate = delegate
        return await Task.detached(priority: .userInitiated) { delegate.encode(image.buffer) }.value
    }
}
