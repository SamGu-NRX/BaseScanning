/// Decides when taps may count: tracking must have been normal, with the camera running, for
/// `requiredSeconds` without a break.
///
/// An interruption breaks the run even when the tracking state reads normal on both sides of
/// it, because ARKit need not report a tracking change across an interruption. Times are device
/// uptime, the clock of `ARFrame.timestamp`, stamped when ARKit called back. A caller's timer only
/// prompts a fresh `isStable(at:)`; it never decides stability itself.
///
/// Callbacks reach the main actor through the main queue, so a report from the AR run before a
/// reset can arrive after it. `reset(at:)` sets a cutoff for tracking reports: those stamped at
/// or before it are refused, and after it a normal report counts only once a limited or
/// unavailable one has arrived, the fresh tracking cycle a reset run starts with (as in
/// `FrameResetGate`). Interruption reports have no cutoff (`interruptionChanged`).
public struct TrackingStability: Sendable, Equatable {
    public let requiredSeconds: Double
    private var isNormal = false
    private var isInterrupted = false
    /// Reports stamped at or before this belong to the run before the last reset.
    private var resetUptime: Double?
    private var awaitingFreshCycle = false
    // Each callback stream keeps its own clock: a newer interruption must not hide a tracking
    // change, or vice versa, when their main-actor tasks arrive out of order.
    private var trackingTime: Double?
    private var interruptionTime: Double?
    /// Start of the current run of normal, uninterrupted tracking.
    public private(set) var normalSince: Double?

    public init(requiredSeconds: Double) {
        self.requiredSeconds = requiredSeconds
    }

    /// Refuses stale reports. Each accepted normal callback starts a fresh interval because a
    /// delayed limited callback may describe a break between two delivered normal callbacks.
    @discardableResult
    public mutating func trackingChanged(isNormal: Bool, at time: Double) -> Bool {
        guard isAfterReset(time), trackingTime.map({ time > $0 }) ?? true else { return false }
        if awaitingFreshCycle {
            if isNormal { return false }
            awaitingFreshCycle = false
        }
        trackingTime = time
        self.isNormal = isNormal
        if !isNormal {
            normalSince = nil
        } else if !isInterrupted {
            normalSince = max(time, interruptionTime ?? time)
        }
        return true
    }

    /// Returns false, changing nothing, for a report older than the last interruption report.
    /// Ending an interruption starts a fresh run if tracking reads normal.
    ///
    /// A camera interruption belongs to the camera, not to an AR run, so `reset(at:)` doesn't cut
    /// these reports off: a start or end stamped before a reset still describes the camera now.
    /// Refusing a pre-reset end held the interruption forever, with no later callback to clear it;
    /// refusing only pre-reset starts would let an older end clear an interruption a newer,
    /// refused start had begun. Timestamp order alone decides. Pre-reset tracking still can't
    /// count, because the reset's cutoff and fresh cycle apply to tracking reports.
    @discardableResult
    public mutating func interruptionChanged(isInterrupted: Bool, at time: Double) -> Bool {
        guard interruptionTime.map({ time > $0 }) ?? true else { return false }
        interruptionTime = time
        self.isInterrupted = isInterrupted
        if isInterrupted {
            normalSince = nil
        } else if isNormal {
            // The end callback may arrive before its start, or after a later normal report.
            normalSince = max(time, normalSince ?? trackingTime ?? time)
        }
        return true
    }

    /// A new AR run started at `uptime`: tracking starts over from not available.
    public mutating func reset(at uptime: Double) {
        resetUptime = uptime
        awaitingFreshCycle = true
        trackingTime = nil
        isNormal = false
        normalSince = nil
    }

    /// When the current run becomes long enough, or nil when no run is under way.
    public var stableAt: Double? {
        normalSince.map { $0 + requiredSeconds }
    }

    public func isStable(at time: Double) -> Bool {
        stableAt.map { time >= $0 } ?? false
    }

    private func isAfterReset(_ time: Double) -> Bool {
        resetUptime.map { time > $0 } ?? true
    }
}
