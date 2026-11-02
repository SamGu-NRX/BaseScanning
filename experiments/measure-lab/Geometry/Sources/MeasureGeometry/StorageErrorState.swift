/// The one storage error the app shows and the manifest write that can clear it on retry.
/// A successful current or closed manifest retry clears only its own displayed error.
/// Unrecoverable errors, such as a lost keyframe, survive all manifest retries.
public struct StorageErrorState<Session: Hashable & Sendable>: Sendable, Equatable {
    public private(set) var message: String?
    private var closedSession: Session?
    private var currentManifest = false

    public init() {}

    /// An error that no retry clears.
    public mutating func report(_ message: String) {
        self.message = message
        closedSession = nil
        currentManifest = false
    }

    public mutating func currentManifestFailed(_ message: String) {
        // A later manifest retry cannot recover an earlier lost keyframe.
        guard self.message == nil || currentManifest || closedSession != nil else { return }
        report(message)
        currentManifest = true
    }

    public mutating func currentManifestSaved() {
        guard currentManifest else { return }
        clear()
    }

    /// A failed write of a closed session's manifest, which a later retry can clear.
    public mutating func report(_ message: String, closedSession session: Session) {
        // A retriable manifest failure must not hide an unrelated error that its retry cannot fix.
        guard self.message == nil || closedSession != nil else { return }
        self.message = message
        closedSession = session
    }

    /// The closed session's manifest is on disk now.
    public mutating func closedSessionSaved(_ session: Session) {
        guard closedSession == session else { return }
        message = nil
        closedSession = nil
    }

    public mutating func clear() {
        message = nil
        closedSession = nil
        currentManifest = false
    }
}
