import Foundation

/// A reading that shows as soon as it is first found and then holds still: later readings don't
/// change it, and it clears only once nothing has been found for `release` seconds.
///
/// For a card line that must neither move nor come and go with every frame, but must not stick
/// once the thing it reports has gone. The walk-out request's "the space ends" line (#164) is the
/// one user: ARKit refines its planes about ten times a second, and a line that followed them
/// re-armed the card reply's lock each time. Latched for the whole request instead, a plane ARKit
/// later moved or removed left the card saying the walk was impossible (review of #168).
public struct SlowRelease<Value: Equatable & Sendable>: Sendable, Equatable {
    /// Seconds with nothing found before the shown reading clears.
    public let release: Double
    /// The reading shown, if any.
    public private(set) var value: Value?
    /// When readings stopped being found while `value` was shown.
    private var absentSince: Double?

    public init(release: Double) {
        self.release = release
    }

    /// Takes this frame's reading (nil when nothing was found) at `time`, seconds on any steady
    /// clock, and returns the reading to show.
    @discardableResult
    public mutating func update(_ found: Value?, time: Double) -> Value? {
        guard value != nil else {
            value = found
            return value
        }
        if found != nil {
            absentSince = nil
        } else if let absentSince {
            if time - absentSince >= release {
                value = nil
                self.absentSince = nil
            }
        } else {
            absentSince = time
        }
        return value
    }
}
