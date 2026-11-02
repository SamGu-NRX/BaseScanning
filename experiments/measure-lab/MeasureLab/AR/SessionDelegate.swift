import ARKit

/// Receives ARSession callbacks and forwards them to `LabSession`.
///
/// ARKit calls these on the session's `delegateQueue`, a private serial queue set by
/// `CaptureController`, so JPEG encoding for motion keyframes stays off the main thread. Each
/// callback copies what it needs into Sendable values, then enqueues on the main queue in callback
/// order. Independent Tasks can reorder a limited/normal cycle and incorrectly permit taps.
/// ARKit's own objects never leave the delegate queue.
final class SessionDelegate: NSObject, ARSessionDelegate {
    private let session: LabSession
    private let recorder: KeyframeRecorder

    init(session: LabSession, recorder: KeyframeRecorder) {
        self.session = session
        self.recorder = recorder
    }

    func session(_ arSession: ARSession, didUpdate frame: ARFrame) {
        guard let delivery = recorder.offerMotionFrame(frame) else { return }
        DispatchQueue.main.async { [session] in session.keyframeDelivered(delivery) }
    }

    func session(_ arSession: ARSession, cameraDidChangeTrackingState camera: ARCamera) {
        let state = TrackingState(camera.trackingState)
        let time = ProcessInfo.processInfo.systemUptime
        recorder.trackingChanged(isNormal: state == .normal, at: time)
        DispatchQueue.main.async { [session] in session.trackingChanged(to: state, at: time) }
    }

    func session(_ arSession: ARSession, didAdd anchors: [ARAnchor]) {
        let planes = anchors.compactMap(DetectedPlane.init)
        guard !planes.isEmpty else { return }
        DispatchQueue.main.async { [session] in session.planesAdded(planes) }
    }

    func session(_ arSession: ARSession, didRemove anchors: [ARAnchor]) {
        let planes = anchors.compactMap(DetectedPlane.init)
        guard !planes.isEmpty else { return }
        DispatchQueue.main.async { [session] in session.planesRemoved(planes) }
    }

    func sessionWasInterrupted(_ arSession: ARSession) {
        let time = ProcessInfo.processInfo.systemUptime
        DispatchQueue.main.async { [session] in session.interruptionChanged(isInterrupted: true, at: time) }
    }

    func sessionInterruptionEnded(_ arSession: ARSession) {
        let time = ProcessInfo.processInfo.systemUptime
        DispatchQueue.main.async { [session] in session.interruptionChanged(isInterrupted: false, at: time) }
    }

    func session(_ arSession: ARSession, didFailWithError error: any Error) {
        let failure: SessionFailure
        if let arError = error as? ARError, arError.code == .cameraUnauthorized {
            failure = .cameraAccessDenied
        } else {
            failure = .other(error.localizedDescription)
        }
        DispatchQueue.main.async { [session] in session.sessionFailed(failure) }
    }
}

/// A plane anchor reduced to the fields the model counts, so it can cross to the main actor.
struct DetectedPlane: Sendable {
    enum Orientation: Sendable {
        case horizontal
        case vertical
    }

    let id: UUID
    let orientation: Orientation

    /// Returns nil for anchors that are not planes and for alignments added after iOS 26.
    init?(_ anchor: ARAnchor) {
        guard let plane = anchor as? ARPlaneAnchor else { return nil }
        switch plane.alignment {
        case .horizontal: orientation = .horizontal
        case .vertical: orientation = .vertical
        @unknown default: return nil
        }
        id = plane.identifier
    }
}
