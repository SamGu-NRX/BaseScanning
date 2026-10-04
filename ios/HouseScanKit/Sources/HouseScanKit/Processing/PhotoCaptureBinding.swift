import Foundation

/// What a photo-processing answer has to match before the app shows it, taken when the scan's
/// capture ended.
///
/// This is a local association, not a binding the service declares. The answer
/// (`CaptureResult.Response`) names only its run; the session, packet and capture it is held
/// against are this phone's own record of what it sent, and the service returns no digest of the
/// input it processed and no attestation. So a match means "the answer the uploader fetched for
/// the capture this scan sent, for the run that capture started", not "the service says it read
/// these bytes". Until the capture API states its input, photo processing can't be called
/// accepted for live use.
public struct PhotoCaptureBinding: Sendable, Equatable {
    public var spatial: SpatialSession
    public var origin: URL
    /// The id of the coordinator session whose packet was frozen (`CaptureSessionCoordinator.Session.localID`).
    public var captureSessionID: String
    public var packetID: String
    /// The ARKit epoch name the packet used (`CaptureSessionCoordinator.epoch`).
    public var epoch: String

    public init(spatial: SpatialSession, origin: URL, captureSessionID: String, packetID: String, epoch: String) {
        self.spatial = spatial
        self.origin = origin
        self.captureSessionID = captureSessionID
        self.packetID = packetID
        self.epoch = epoch
    }
}

/// The scan on screen when an answer arrives. Nil fields mean there is nothing of that kind now.
public struct PhotoCaptureObservation: Sendable, Equatable {
    public var spatial: SpatialSession?
    public var origin: URL?
    public var captureSessionID: String?
    public var packetID: String?
    /// The server's capture and run for the upload now, from its latest status.
    public var captureID: String?
    public var runID: String?

    public init(
        spatial: SpatialSession?, origin: URL?, captureSessionID: String?, packetID: String?, captureID: String?, runID: String?
    ) {
        self.spatial = spatial
        self.origin = origin
        self.captureSessionID = captureSessionID
        self.packetID = packetID
        self.captureID = captureID
        self.runID = runID
    }
}

/// The first identity an answer and the scan on screen disagree on. All but `run` are compared
/// with the phone's own records (`PhotoCaptureBinding`).
public enum PhotoBindingMismatch: Sendable, Equatable, CaseIterable {
    /// Another ARKit world, or none: an answer for a world that is gone.
    case world
    /// Another destination than the one the scan's profile sends to.
    case origin
    /// Another coordinator session than the one whose packet was frozen.
    case session
    case packet
    case capture
    case run
    case epoch
}

extension PhotoCaptureBinding {
    /// The first thing `record` and the scan on screen disagree on, or nil when the answer is this
    /// capture's.
    public func mismatch(_ record: CaptureResult.Record, observed: PhotoCaptureObservation) -> PhotoBindingMismatch? {
        guard observed.spatial == spatial else { return .world }
        guard observed.origin == origin else { return .origin }
        guard observed.captureSessionID == captureSessionID, record.association.sessionID == captureSessionID else { return .session }
        guard observed.packetID == packetID else { return .packet }
        guard let captureID = observed.captureID, record.association.captureID == captureID else { return .capture }
        guard let runID = observed.runID, record.association.runID == runID, record.response.runId == runID else { return .run }
        guard record.association.epoch == epoch else { return .epoch }
        return nil
    }
}
