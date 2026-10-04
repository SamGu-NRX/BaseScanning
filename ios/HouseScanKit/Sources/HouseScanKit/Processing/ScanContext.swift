import Foundation

/// One ARKit world within a scan. Results and AR bind to this, not to the scan's id: a world reset
/// keeps the scan but makes every pose and answer from the old world meaningless.
public struct SpatialSession: Sendable, Equatable, Hashable {
    /// The packet recorder's session id, which it makes new for every world.
    public var recordingSessionID: String
    /// Worlds this scan threw away before this one: 0 for its first.
    public var worldEpoch: Int

    public init(recordingSessionID: String, worldEpoch: Int) {
        self.recordingSessionID = recordingSessionID
        self.worldEpoch = worldEpoch
    }
}

/// What a scan is, fixed when it starts: its identity, its backend profile and its current world.
///
/// The engine's work counter isn't an identity: it moves on every reset, inside a scan as well as
/// between scans, so it can't say which scan a capture or an answer belongs to. This can.
public struct ScanContext: Sendable, Equatable {
    /// The scan's id, which the app takes from its scan folder's name. Start over makes a new scan
    /// and so a new id; a world reset keeps it.
    public let scanID: String
    public let profile: ProcessingProfile
    public private(set) var spatial: SpatialSession

    public init(scanID: String, profile: ProcessingProfile, recordingSessionID: String) {
        self.scanID = scanID
        self.profile = profile
        spatial = SpatialSession(recordingSessionID: recordingSessionID, worldEpoch: 0)
    }

    /// The same scan in a new ARKit world: the id and the backend stay, the spatial session is
    /// new.
    public func inNewWorld(recordingSessionID: String) -> ScanContext {
        var next = self
        next.spatial = SpatialSession(recordingSessionID: recordingSessionID, worldEpoch: spatial.worldEpoch + 1)
        return next
    }
}
