import Foundation

/// "Can't get there" on the walk's card, which ends the wall on the side being walked
/// (`WalkedEnd`), and what the scan remembers about it.
///
/// - A second one soon after the last asks "End the scan here?" instead of ending the other side
///   too (#82, option C). On the build 4.1 field test a homeowner who wanted to stop tapped it
///   seven times in a row, and each tap silently skipped a task or set an end.
/// - A side it ended before the homeowner walked any of it counts as refused. When both sides
///   were refused, the wall line comes from the meter tap alone and nothing confirmed it, so the
///   result shows no spot (#76, option (a)). A side walked first and then ended with "Can't get
///   there", at a fence say, was walked: device run 1 ended both sides that way 16 ft out.
public struct WalkRefusals: Equatable, Sendable {
    /// A second "Can't get there" on a walk card within this many seconds of the one that last
    /// ended a side asks "End the scan here?" instead of ending another.
    public static let repeatWindow: TimeInterval = 5

    /// A side counts as refused when "Can't get there" ended it with less than 0.79 m walked
    /// (`WalkedEnd.farthest`) and the end less than 0.79 m from the meter. D7 changes valid wall
    /// lengths, not this existing threshold for whether a side was walked before refusal.
    public static let walkedMinimum: Float = 0.79

    /// When "Can't get there" last ended a side, seconds on the caller's clock.
    private var lastEnded: TimeInterval?
    /// The s of each end a refusal set. It counts only while that end is still there: an end
    /// cleared and set again (a past-end request, a corner) is no longer the refused one.
    private var refusedEnds: [WalkSide: Float] = [:]

    public init() {}

    /// Whether a "Can't get there" on a walk card at `time` should ask "End the scan here?"
    /// rather than end the side: the last one ended a side at most `repeatWindow` seconds before.
    public func asksToEndScan(at time: TimeInterval) -> Bool {
        guard let lastEnded else { return false }
        let since = time - lastEnded
        return since >= 0 && since <= Self.repeatWindow
    }

    /// "Can't get there" ended `side` at `s` at `time`, with `walked` meters walked on that side
    /// (`WalkedEnd.farthest`). The side counts as walked when either the walk or the end reached
    /// `walkedMinimum` from the meter: an end set at the phone's place out along the wall means
    /// the homeowner walked there, even with few views kept on the way (#71, run 2).
    public mutating func ended(_ side: WalkSide, at s: Float, walked: Float, time: TimeInterval) {
        lastEnded = time
        let reach = max(walked, side.sign * s)
        refusedEnds[side] = reach < Self.walkedMinimum ? s : nil
    }

    /// "Keep walking": the next "Can't get there" ends its side again without asking.
    public mutating func keepWalking() {
        lastEnded = nil
    }

    /// Whether the end on `side`, at `end` (nil when unmarked), is one a refusal set.
    public func wasRefused(_ side: WalkSide, end: Float?) -> Bool {
        guard let end, let refused = refusedEnds[side] else { return false }
        return refused == end
    }

    /// True when the ends are coincident or reversed. A missing end also leaves no wall to
    /// finish with; a narrow positive wall is valid and placement is left to the server.
    public static func endsTooClose(left: Float?, right: Float?) -> Bool {
        guard let left, let right else { return true }
        return !WallFrame.hasPositiveLength(left: left, right: right)
    }

    /// True when both ends of the wall are ones a refusal set: neither side of the meter was
    /// walked, and the result must not show a spot.
    public func neitherSideWalked(leftEnd: Float?, rightEnd: Float?) -> Bool {
        wasRefused(.left, end: leftEnd) && wasRefused(.right, end: rightEnd)
    }
}
