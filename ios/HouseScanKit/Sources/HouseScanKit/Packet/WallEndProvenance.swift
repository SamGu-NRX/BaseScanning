import Foundation

/// Whether the homeowner put a wall end where it is, or the app inferred it from the walk.
///
/// Before, every end carried a mark time, so the packet listed ends the app placed as ends the
/// homeowner marked: "Can't get there" and "The wall keeps going" put the end where the walk
/// reached (`WalkedEnd`), and a past-end request met by walking past moved it on to the far edge
/// of what the request showed (`GapPlanner.endAfterPastEnd`). Field analysts read those as marks
/// (run3-2055, A41-2055-06). Either kind of end keeps its place and kind in scene.json and the
/// packet; only the packet says which it is (packet/README.md, `attrs.inferred`).
public enum WallEndSource: Sendable, Equatable {
    /// "Wall ends here", at the circle or where the homeowner stands.
    case homeowner
    /// Placed from the walk: where it reached, or past what a past-end request showed.
    case inferred
}

/// A wall end's source and, for a homeowner's mark, when it was made.
public struct WallEndStamp: Sendable, Equatable {
    public let source: WallEndSource
    /// Uptime of the homeowner's mark. Nil for an inferred end, which nobody marked, and nil for
    /// a mark made before the packet's clock started; absence never stands for a homeowner.
    public let markedAt: Double?

    private init(source: WallEndSource, markedAt: Double?) {
        self.source = source
        self.markedAt = markedAt
    }

    /// The homeowner marked the end at uptime `time`.
    public static func marked(at time: Double?) -> WallEndStamp {
        WallEndStamp(source: .homeowner, markedAt: time)
    }

    /// The app placed the end from the walk; it has no mark time.
    public static let inferred = WallEndStamp(source: .inferred, markedAt: nil)

    public var isInferred: Bool { source == .inferred }

    /// How a "mark the end" request closes once this end is set: met only when the homeowner
    /// marked it. An end the walk inferred answers a different request ("Can't get there", which
    /// closes as `cannot_reach` before the end is placed), so it never counts as the mark.
    public var markEndOutcome: PacketGuidanceEntry.Outcome {
        source == .homeowner ? .met : .superseded
    }
}

/// What becomes of the end a past-end request cleared when the request leaves the screen.
public enum PastEndSettlement: Sendable, Equatable {
    /// The homeowner marked the end during the request: that mark stands, nearer or farther than
    /// the cleared end, since the cleared end only said how far the walk had seen.
    case keepMarked
    /// The request was met by views and nobody marked the end: it moves on past the ground the
    /// request showed, still unexplored, as an inferred end (`GapPlanner.endAfterPastEnd`).
    case moveOn
    /// The homeowner said "I can't get there": the cleared end comes back with its place, kind,
    /// source and mark time, and nothing past it counts as seen.
    case restore

    public static func when(endMarkedDuringRequest: Bool, met: Bool) -> PastEndSettlement {
        if endMarkedDuringRequest { return .keepMarked }
        return met ? .moveOn : .restore
    }
}
