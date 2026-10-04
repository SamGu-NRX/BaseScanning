import Foundation
import simd

/// Where a wall end goes when the homeowner ends the walk on a side without pointing at the end:
/// "Can't get there" while the walk asks them to walk that way, or "Wall ends here" during the
/// walk.
///
/// The end goes where the phone is, along the wall chain (`WallFrame.wallPoint`, so s runs on
/// round a corner the walk followed). It used to go where coverage ran unbroken from the meter
/// over both bands (`GuidancePlanner.reach`). On device run 1 (2026-09-26) the ground in front of
/// the meter had not been seen from two places when the homeowner tapped, so both ends landed
/// within 4 in of the meter and the scan left out the 19 ft it had walked and photographed.
/// Stretches between the meter and the end that nothing saw stay in the scan as unseen, and the
/// server asks for them.
///
/// Team decision (2026-09-27, #71 and #24): with the phone on that side of the meter and
/// tracking normal, the end goes where the phone is even past the farthest kept view. Build 4.1
/// kept that cap everywhere, and on run 2 the homeowner stood at the end post a few feet left of
/// the meter with no photo kept on the left, so the end went to the meter and the strip dropped
/// the cells it had shown there. The cap (`farthest`) now applies only where the phone's place
/// says nothing about the side: across the meter, or with tracking limited or lost.
public enum WalkedEnd {
    /// Where an end goes and why, for the log line (`choose`).
    public struct Choice: Equatable, Sendable {
        /// s of the end.
        public var s: Float
        /// The phone's s along the chain, nil when it has lost its place.
        public var phone: Float?
        /// s of the farthest kept view on that side (`farthest`, signed), the cap.
        public var cap: Float
        /// True when the cap decided the end: the phone across the meter, its place lost, or
        /// tracking limited.
        public var capped: Bool
        /// True when the end stopped `maxPastEvidence` past the farthest kept view or seen cell,
        /// short of the phone, rather than at the kept-photo cap.
        public var pastEvidence: Bool

        public init(s: Float, phone: Float?, cap: Float, capped: Bool, pastEvidence: Bool = false) {
            self.s = s
            self.phone = phone
            self.cap = cap
            self.capped = capped
            self.pastEvidence = pastEvidence
        }
    }

    /// How far the walk went on `side`, meters from the meter along the chain: the farthest
    /// position a kept view was taken from on that side, 0 when none was.
    public static func farthest(_ side: WalkSide, walked: [SIMD3<Float>], wall: WallFrame) -> Float {
        walked.reduce(0) { max($0, side.sign * wall.wallPoint($1).s) }
    }

    /// s of the end on `side` (`choose`).
    public static func end(
        _ side: WalkSide, phone: SIMD3<Float>?, trackingNormal: Bool = true, walked: [SIMD3<Float>], wall: WallFrame,
        seen: ClosedRange<Float>? = nil
    ) -> Float {
        choose(side, phone: phone, trackingNormal: trackingNormal, walked: walked, wall: wall, seen: seen).s
    }

    /// The end on `side`. `walked` holds the positions of the kept views; `phone` is where the
    /// phone is now, nil when it has lost its place; `trackingNormal` is false while tracking is
    /// limited, taken as it is at the tap: a brief drop before it doesn't matter (review of #147);
    /// `seen` is the strip's seen extent
    /// (`CoverageMap.seenExtent`).
    ///
    /// - Phone on that side of the meter, tracking normal: its s, past the farthest kept view
    ///   too, but no more than `maxPastEvidence` past the farthest kept view or seen cell there.
    ///   Standing at the end is the measurement.
    /// - Phone on that side, tracking limited, or more than `phoneOutLimit` out from the wall's
    ///   line: its s, but no farther out than `farthest`.
    /// - Phone across the meter, or its place unknown: `farthest`. The phone's position then says
    ///   nothing about how far this side goes, and the walked stretch is the only measurement of
    ///   it; an end at the meter would drop everything walked there, which is how run 1 lost its
    ///   wall. Nothing walked on that side: the meter.
    public static func choose(
        _ side: WalkSide, phone: SIMD3<Float>?, trackingNormal: Bool = true, walked: [SIMD3<Float>], wall: WallFrame,
        seen: ClosedRange<Float>? = nil
    ) -> Choice {
        let reach = farthest(side, walked: walked, wall: wall)
        let cap = side.sign * reach
        guard let phone else { return Choice(s: cap, phone: nil, cap: cap, capped: true) }
        let point = wall.wallPoint(phone)
        let s = point.s
        let along = side.sign * s
        if along < 0 { return Choice(s: cap, phone: s, cap: cap, capped: true) }
        if trackingNormal, abs(point.out) <= phoneOutLimit {
            let seenEdge = seen.map { side.sign * (side == .left ? $0.lowerBound : $0.upperBound) } ?? 0
            let limit = max(reach, seenEdge) + maxPastEvidence
            if along <= limit { return Choice(s: s, phone: s, cap: cap, capped: false) }
            return Choice(s: side.sign * limit, phone: s, cap: cap, capped: true, pastEvidence: true)
        }
        return Choice(s: side.sign * min(along, reach), phone: s, cap: cap, capped: true)
    }

    /// 4 m, twice the walk's stand-off (`GuidanceConfig.standOff`), as for `leavesOut`: farther
    /// out from the wall's line than this, the phone's s says little about where the wall ends.
    /// 2 m would cap ordinary walks, which stand about 2 m out. A guess, not measured. This does
    /// not cover a wall line skewed by a bad tap (#69): distance out is measured from that same
    /// line, so a skewed line can read a phone as close; `maxPastEvidence` bounds that case.
    public static let phoneOutLimit: Float = 2 * GuidanceConfig().standOff

    /// 2 m: how far past the farthest kept view or seen cell on its side an end at the phone may
    /// land. Whatever the wall line, the walk has evidence up to there, so a skewed line (#69)
    /// or a bad pose can't put the end far out along nothing. Run 2's end post was about 0.6 m
    /// past the cells seen. A guess, not measured.
    public static let maxPastEvidence: Float = 2

    /// The strip's seen extent that counts toward what an end leaves out (`leftOut`): nil unless
    /// the cap decided the end, since the camera of a phone standing at the end sees past it.
    /// With the phone on that side of the meter, only up to the phone: cells the camera saw
    /// ahead of it were never walked (review of #136).
    public static func countedSeen(_ side: WalkSide, choice: Choice, seen: ClosedRange<Float>?) -> ClosedRange<Float>? {
        guard choice.capped, let seen else { return nil }
        guard let phone = choice.phone, side.sign * phone > 0 else { return seen }
        switch side {
        case .left:
            let low = max(seen.lowerBound, phone)
            return low <= seen.upperBound ? low...seen.upperBound : nil
        case .right:
            let high = min(seen.upperBound, phone)
            return seen.lowerBound <= high ? seen.lowerBound...high : nil
        }
    }

    /// Meters of the strip's seen cells (`CoverageMap.seenExtent`) past an end at `s` on `side`,
    /// which the scan drops once the end is set. Nil when that is under `minimum`, by default
    /// `shownPastMinimum`.
    public static func shownPast(
        _ side: WalkSide, s: Float, seen: ClosedRange<Float>?, minimum: Float = shownPastMinimum
    ) -> Float? {
        guard let seen else { return nil }
        let edge = side == .left ? seen.lowerBound : seen.upperBound
        let past = side.sign * (edge - s)
        return past >= minimum ? past : nil
    }

    /// 1 m: past a phone at the usual stand-off the camera sees about that far along the wall
    /// ahead of it, and those cells aren't left out of anything the homeowner walked. A guess to
    /// try on a phone, not measured.
    public static let shownPastMinimum: Float = 1

    /// The side "Wall ends here" ends while the walk asks about the wall in front of the phone
    /// (tilt down, tilt up, step back) instead of asking to walk a side: the side of the meter the
    /// phone is on, by its s along the chain as `end` measures it, when that side has no end yet.
    /// The planner does the left side first, so the first side without an end can be the one
    /// behind the homeowner: after walking right with the left end unmarked, the button ended the
    /// left side at its farthest walked point, far from the phone (review of #24). With the
    /// phone's place unknown, the phone at the meter, or its side already ended: the first side
    /// without an end, as before. Nil when both ends are marked.
    public static func side(phone: SIMD3<Float>?, wall: WallFrame, leftEnd: Float?, rightEnd: Float?) -> WalkSide? {
        let open = WalkSide.allCases.filter { ($0 == .left ? leftEnd : rightEnd) == nil }
        if let phone {
            let s = wall.wallPoint(phone).s
            if let side = open.first(where: { $0.sign * s > 0 }) { return side }
        }
        return open.first
    }
}

extension CoverageMap {
    /// Where the kept views were taken from (`observedCameras`).
    public var walkedPositions: [SIMD3<Float>] { observedCameras.map(\.position) }

    /// How far the walk went on `side` (`WalkedEnd.farthest`).
    public func walkedFarthest(_ side: WalkSide) -> Float {
        WalkedEnd.farthest(side, walked: walkedPositions, wall: wall)
    }

    /// True when both ends are marked closer together along the chain than
    /// `WallFrame.minWallLength`: too short a wall to finish the walk with.
    public var endsTooClose: Bool {
        guard let leftEnd, let rightEnd else { return false }
        return rightEnd - leftEnd < WallFrame.minWallLength
    }

    /// True when an end on `side` at `s` would leave the wall shorter than
    /// `WallFrame.minWallLength` from the other end, or from the meter while the other side has
    /// none: the same policy as `endsTooClose`, asked before the end is set (B-12, a past-end
    /// request's end marked again nearer). The other end is never moved.
    public func endWouldLeaveTooLittle(_ side: WalkSide, at s: Float) -> Bool {
        let other: Float = side == .left ? (rightEnd ?? 0) : (leftEnd ?? 0)
        let length = side == .left ? other - s : s - other
        return length < WallFrame.minWallLength
    }

    /// The unexplored end the camera stands beyond while its view shows nothing between the marked
    /// ends, or nil. A photo taken there adds nothing: cells past an end are never observed
    /// (`isWithinEnds`). Past a limit end it is nil, since the ground seen there still counts
    /// (`groundDepthPastLimit`).
    ///
    /// `ignoring` is the side whose next wall is being looked for after "It turns a corner"
    /// (#70): walking round the corner puts the phone past that end, and the card telling the
    /// homeowner to walk back contradicted the instruction to walk round it. Nil on that side, so
    /// photos there are kept; once the corner is followed they are replayed against the new wall
    /// (`turnCorner`), and if it isn't they add no coverage.
    public func unexploredEndPassed(by camera: CameraFrame, ignoring: WalkSide? = nil) -> WalkSide? {
        let s = wall.wallPoint(camera.position).s
        let side: WalkSide
        if let leftEnd, s < leftEnd {
            side = .left
        } else if let rightEnd, s > rightEnd {
            side = .right
        } else {
            return nil
        }
        guard !limitEnds.contains(side), side != ignoring else { return nil }
        // Only cells between the ends are candidates, so any sighting is one the scan keeps.
        return visibleCells(from: camera).isEmpty ? side : nil
    }
}

extension WalkedEnd {
    /// Meters of the walk on `side` past an end at `s`: how far the farthest kept view there
    /// (`farthest`) is beyond it, which the scan leaves out if the wall ends at `s`. Nil when that
    /// is less than `minimum`, by default one keyframe's spacing: that close to the farthest view
    /// is still the front of the walk.
    public static func walkedPast(
        _ side: WalkSide, s: Float, walked: [SIMD3<Float>], wall: WallFrame,
        minimum: Float = AutoCaptureConfig().spacingMeters
    ) -> Float? {
        let past = farthest(side, walked: walked, wall: wall) - side.sign * s
        return past >= minimum ? past : nil
    }

    /// What the wall strip says an end at `s` would leave out of the walk (`walkedPast`), before
    /// anything is pressed. Nil unless the walk asks to walk `side` or to mark its end
    /// (`onWalkTask`): during a tilt or step-back request the homeowner isn't ending the wall, and
    /// the line read as a warning that something went wrong (issue #66). Nil too when the phone
    /// stands more than twice `GuidanceConfig.standOff` out from the wall, where its place along
    /// the wall, and so `s`, says little: walking out into the yard counted up to 11 ft on device
    /// run 3. `phoneOut` is the phone's distance out from the wall (`WallPoint.out`) when `s` is
    /// the phone's place, nil when it isn't (the reticle's end) or the phone has lost its place.
    ///
    /// `seen` is the strip's seen extent, passed when the cap decided the end (`Choice.capped`):
    /// cells the strip shows past it count too (`leftOut`), so an end short of what the strip
    /// showed says so instead of dropping it silently (#71).
    public static func leavesOut(
        side: WalkSide, s: Float, walked: [SIMD3<Float>], wall: WallFrame,
        phoneOut: Float?, onWalkTask: Bool, seen: ClosedRange<Float>? = nil, config: GuidanceConfig = GuidanceConfig()
    ) -> Float? {
        guard onWalkTask else { return nil }
        if let phoneOut, abs(phoneOut) > 2 * config.standOff { return nil }
        return leftOut(side, s: s, walked: walked, wall: wall, seen: seen)
    }

    /// What an end at `s` leaves out: the walk past it (`walkedPast`) or, with `seen`, the cells
    /// the strip shows past it (`shownPast`), whichever is more. Nil when neither counts.
    public static func leftOut(
        _ side: WalkSide, s: Float, walked: [SIMD3<Float>], wall: WallFrame, seen: ClosedRange<Float>?
    ) -> Float? {
        let parts = [walkedPast(side, s: s, walked: walked, wall: wall), shownPast(side, s: s, seen: seen)]
        return parts.compactMap(\.self).max()
    }

    /// Whether `leftOut`'s meters are the cells the strip showed rather than the walk: then the
    /// words say "you saw", not "you walked" (re-review of #136: across the meter the homeowner
    /// never walked that stretch).
    public static func leftOutIsSeen(
        _ side: WalkSide, s: Float, walked: [SIMD3<Float>], wall: WallFrame, seen: ClosedRange<Float>?
    ) -> Bool {
        guard let shown = shownPast(side, s: s, seen: seen) else { return false }
        return shown > (walkedPast(side, s: s, walked: walked, wall: wall) ?? 0)
    }

    /// Whether a mark's span (meters of s) lies wholly past a marked end. The scan doesn't cover
    /// it there: the wall may turn or stop at that end, so the review says so (issue #42). A mark
    /// reaching an end, or with any of it between the ends, is on the scanned wall.
    public static func liesPastAnEnd(_ span: ClosedRange<Float>, leftEnd: Float?, rightEnd: Float?) -> Bool {
        if let leftEnd, span.upperBound < leftEnd { return true }
        if let rightEnd, span.lowerBound > rightEnd { return true }
        return false
    }
}
