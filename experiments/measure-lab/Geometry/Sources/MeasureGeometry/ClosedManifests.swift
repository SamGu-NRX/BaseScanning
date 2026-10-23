/// Manifests of closed sessions that are not safely finished on disk.
///
/// A closed session stays here while keyframe writes it reserved are still in flight, and while
/// its latest manifest has not been written. It leaves only when both are done, so a failed
/// manifest write is retried instead of dropping the keyframes that manifest lists.
public struct ClosedManifests<Session: Hashable & Sendable, Value: Sendable>: Sendable {
    private struct Entry: Sendable {
        var value: Value
        var unsaved: Bool
        var drained: Bool
    }

    private var entries: [Session: Entry] = [:]

    public init() {}

    /// Keeps a session that just closed if it is still waiting for writes or its last save
    /// failed. A drained, saved session needs nothing more and is not kept.
    public mutating func close(_ session: Session, _ value: Value, awaitingWrites: Bool, saved: Bool) {
        guard awaitingWrites || !saved else { return }
        entries[session] = Entry(value: value, unsaved: !saved, drained: !awaitingWrites)
    }

    /// Changes a kept manifest, for example to list a late keyframe, and marks it unsaved.
    /// Returns false when the session is not kept.
    @discardableResult
    public mutating func update(_ session: Session, _ change: (inout Value) -> Void) -> Bool {
        guard entries[session] != nil else { return false }
        change(&entries[session]!.value)
        entries[session]!.unsaved = true
        return true
    }

    /// The session's last reserved write has reported back.
    public mutating func markDrained(_ session: Session) {
        entries[session]?.drained = true
    }

    /// Writes every unsaved manifest, then releases sessions that are drained and saved. A
    /// failed write leaves its session kept and unsaved for the next flush. Returns the sessions
    /// written and the failures, each in no particular order.
    public mutating func flush(
        _ write: (Value) throws -> Void
    ) -> (saved: [Session], failures: [(session: Session, error: any Error)]) {
        var saved: [Session] = []
        var failures: [(session: Session, error: any Error)] = []
        for (session, entry) in entries where entry.unsaved {
            do {
                try write(entry.value)
                entries[session]?.unsaved = false
                saved.append(session)
            } catch {
                failures.append((session, error))
            }
        }
        entries = entries.filter { !$0.value.drained || $0.value.unsaved }
        return (saved, failures)
    }

    public func value(for session: Session) -> Value? {
        entries[session]?.value
    }

    /// Whether the session's latest manifest is not on disk yet.
    public func isUnsaved(_ session: Session) -> Bool {
        entries[session]?.unsaved ?? false
    }

    /// Sessions still kept.
    public var sessions: Set<Session> {
        Set(entries.keys)
    }
}
