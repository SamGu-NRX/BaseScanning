import Foundation
import simd

/// A replay frame reduced to what coverage needs.
public struct PlannedFrame: Sendable, Equatable {
    public var camera: CameraFrame
    public var timestamp: Double
    public var trackingNormal: Bool

    public init(camera: CameraFrame, timestamp: Double, trackingNormal: Bool) {
        self.camera = camera
        self.timestamp = timestamp
        self.trackingNormal = trackingNormal
    }
}

/// Plans for replays that carry no wall of their own, and for the autopilot's gap loop.
public enum ReplayPlanning {
    /// A wall assumed from the trajectory alone, for replays recorded without wall taps.
    ///
    /// The wall runs parallel to the principal horizontal walking direction, on the side the
    /// camera mostly looks toward. The ground is assumed 1.4 m below the mean camera height (a
    /// phone held at chest height), and the meter 1.5 m above the ground. The offset (searched from
    /// 1.5 m to 8 m in 0.25 m steps) and the meter's place along the wall are the pair that lets
    /// the most cells be covered within `reach` of the meter, the stretch the gap planner and the
    /// walk care about; among equal meter places, the one nearest the middle of the walk wins.
    ///
    /// The meter is not simply put at the middle of the walk because a walk need not look at the
    /// wall there: on the ADVIO replay (advio-20-0040-0075) the camera faces the wall only for
    /// frames 14 to 29, about 13 to 5.5 m before the middle, and faces away or straight ahead
    /// elsewhere. With the meter at the middle (and the 1.5 m offset that then covered most), the
    /// nearest covered cell was 2.9 m away and the ground from 2.9 m left to 6.1 m right of the
    /// meter was a gap no frame sees, so no held-back window could make a closable gap.
    /// This is an assumption for exercising the flow, not a measurement.
    public static func assumedWall(
        frames: [PlannedFrame], config: CoverageConfig = CoverageConfig(), reach: Float = GapPlannerConfig().reach
    ) -> (wall: WallFrame, coveredCells: Int, offset: Float)? {
        guard frames.count >= 2 else { return nil }
        let positions = frames.map(\.camera.position)
        let centroid = positions.reduce(SIMD3<Float>.zero, +) / Float(positions.count)
        var cxx: Float = 0, czz: Float = 0, cxz: Float = 0
        for p in positions {
            let d = p - centroid
            cxx += d.x * d.x
            czz += d.z * d.z
            cxz += d.x * d.z
        }
        let angle = 0.5 * atan2(2 * cxz, cxx - czz)
        let direction = SIMD3<Float>(cos(angle), 0, sin(angle))
        let perpendicular = SIMD3<Float>(-direction.z, 0, direction.x)
        let look = frames.map { SIMD3($0.camera.forward.x, 0, $0.camera.forward.z) }.reduce(.zero, +)
        let side: Float = simd_dot(look, perpendicular) >= 0 ? 1 : -1
        let outward = -perpendicular * side
        let groundY = centroid.y - 1.4
        let middle = middleOfPath(positions)

        var best: (wall: WallFrame, coveredCells: Int, offset: Float)?
        for step in 0...26 {
            let offset = 1.5 + Float(step) * 0.25
            let planePoint = centroid + perpendicular * side * offset
            let onLine = middle - outward * simd_dot(middle - planePoint, outward)
            guard let wall = WallFrame(meter: SIMD3(onLine.x, groundY + 1.5, onLine.z), outward: outward, groundY: groundY) else { continue }
            var map = CoverageMap(wall: wall, config: config)
            for frame in frames { map.observe(frame.camera, trackingNormal: frame.trackingNormal, time: frame.timestamp) }
            guard let (shift, count) = bestMeterShift(map, reach: reach), count > (best?.coveredCells ?? -1) else { continue }
            // A whole number of cells, so the shifted wall's cells are the ones counted here.
            let meter = wall.meter + wall.along * (Float(shift) * config.cellWidth)
            guard let shifted = WallFrame(meter: meter, outward: outward, groundY: groundY) else { continue }
            best = (shifted, count, offset)
        }
        return best
    }

    /// The meter shift along the wall, in whole cells, with the most covered cells (both bands)
    /// within `reach` of the shifted meter, and that count. Ties go to the shift whose thinner side
    /// has the most covered cells, so the walk covers both sides of the meter, then to the
    /// smallest shift.
    private static func bestMeterShift(_ map: CoverageMap, reach: Float) -> (shift: Int, count: Int)? {
        guard let extent = map.seenExtent else { return nil }
        let width = map.config.cellWidth
        func covered(_ range: ClosedRange<Float>) -> Int {
            map.indices(overlapping: range).reduce(0) { total, index in
                total + SurfaceBand.allCases.filter { map.level($0, index) == .covered }.count
            }
        }
        var best: (shift: Int, count: Int, balance: Int)?
        for shift in map.indices(overlapping: extent) {
            let center = Float(shift) * width
            let left = covered((center - reach)...center)
            let right = covered(center...(center + reach))
            let candidate = (shift: shift, count: left + right, balance: min(left, right))
            if let current = best {
                let better = candidate.count != current.count ? candidate.count > current.count
                    : candidate.balance != current.balance ? candidate.balance > current.balance
                    : abs(shift) < abs(current.shift)
                guard better else { continue }
            }
            best = candidate
        }
        return best.map { ($0.shift, $0.count) }
    }

    private static func middleOfPath(_ positions: [SIMD3<Float>]) -> SIMD3<Float> {
        var lengths: [Float] = [0]
        for i in 1..<positions.count { lengths.append(lengths[i - 1] + simd_distance(positions[i], positions[i - 1])) }
        let half = (lengths.last ?? 0) / 2
        let index = lengths.firstIndex { $0 >= half } ?? 0
        return positions[index]
    }

    /// Coverage from walking `frames` through auto-capture (without image quality, which a
    /// planner has no pixels for) and the coverage map.
    public static func simulateWalk(_ frames: [PlannedFrame], wall: WallFrame, config: CoverageConfig = CoverageConfig()) -> CoverageMap {
        var map = CoverageMap(wall: wall, config: config)
        var capture = AutoCapture()
        for frame in frames {
            let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: frame.trackingNormal ? .normal : .limited, quality: nil)
            let decision = capture.evaluate(sample, newlySeenCells: map.newlySeenCount(from: frame.camera))
            if decision.isKeep {
                capture.didKeep(sample)
                map.observe(frame.camera, trackingNormal: frame.trackingNormal, time: frame.timestamp)
            }
        }
        return map
    }

    public struct HeldBackWindow: Sendable, Equatable {
        /// Frame indices held back from the walk and replayed for the gap.
        public var frames: Range<Int>
        /// Wall ends to mark: the covered extremes of the walk without the window.
        public var ends: ClosedRange<Float>
        /// The gap the planner finds after that walk.
        public var gap: GapPlan
    }

    /// Where the walk that leaves a window out will set its ends.
    public enum EndPolicy: Sendable, Equatable {
        /// Marked at the covered extremes, as the autopilot's `markEnds` does.
        case coveredExtremes
        /// Set as "Can't get there" sets them with the phone at the frame that stood farthest
        /// each way (`walkedEnds`), after "Can't get there" on the ground by the meter. The gap
        /// must then lie `margin` meters inside those ends: "Can't get there" at an end also
        /// skips the cells there.
        case walked(margin: Float)
    }

    /// Chooses frames to hold back from the autopilot's walk so the gap loop has something real
    /// to do: without them the planner finds a gap, and replaying them alone closes it.
    ///
    /// Candidates are contiguous windows of 3 to 10 frames, tried from the middle of the walk
    /// outward. The gap must lie within where the rest of the walk went (`isWithinWalk`): a gap
    /// past the farthest frame the phone stood at falls outside ends set where the phone is
    /// ("Can't get there"), and then nothing asks for it. With `ends` `.walked`, the window is
    /// planned against the ends that flow sets and the cells it skips, so the gap it leaves is
    /// one that flow asks for. Each is screened on coverage rebuilt from what every frame sees
    /// (computed once), then confirmed with the full auto-capture simulation the app runs. Nil
    /// when no window works, for example when the planner's nearest gap is one no frame of the
    /// replay can close.
    public static func heldBackWindow(
        frames: [PlannedFrame], wall: WallFrame, ends policy: EndPolicy = .coveredExtremes,
        planner: GapPlanner = GapPlanner(), config: CoverageConfig = CoverageConfig()
    ) -> HeldBackWindow? {
        guard frames.count >= 6 else { return nil }
        let empty = CoverageMap(wall: wall, config: config)
        let sightings = frames.map { $0.trackingNormal ? empty.visibleCells(from: $0.camera) : [] }
        let kept = Set(keptIndices(frames, wall: wall, config: config))
        let middle = frames.count / 2
        let starts = (2..<(frames.count - 1)).sorted { abs($0 - middle) < abs($1 - middle) }
        var confirmations = 0
        for size in [4, 6, 3, 8, 10] {
            for start in starts where start + size <= frames.count {
                let window = start..<(start + size)
                var rest = CoverageMap(wall: wall, config: config)
                for index in kept.sorted() where !window.contains(index) {
                    rest.record(sightings[index], from: frames[index].camera.position)
                }
                let stood = frames.indices.filter { !window.contains($0) }.map { frames[$0] }
                let keptPositions = kept.sorted().filter { !window.contains($0) }.map { frames[$0].camera.position }
                guard let ends = self.ends(policy, rest, stood: stood, walked: keptPositions, wall: wall) else { continue }
                prepare(&rest, ends: ends, policy: policy)
                guard let gap = planner.plan(rest),
                      isWithinWalk(gap, kept.sorted().filter { !window.contains($0) }.map { frames[$0] }, wall: wall),
                      isInside(gap, ends: ends, policy: policy) else { continue }
                var restored = rest
                for index in window { restored.record(sightings[index], from: frames[index].camera.position) }
                guard planner.isSatisfied(gap, restored) else { continue }
                // Screened: confirm with the exact auto-capture path, a few times at most.
                confirmations += 1
                if let confirmed = confirm(frames, window: window, wall: wall, policy: policy, planner: planner, config: config) {
                    return confirmed
                }
                if confirmations >= 12 { return nil }
            }
        }
        return nil
    }

    private static func keptIndices(_ frames: [PlannedFrame], wall: WallFrame, config: CoverageConfig) -> [Int] {
        var map = CoverageMap(wall: wall, config: config)
        var capture = AutoCapture()
        var kept: [Int] = []
        for (index, frame) in frames.enumerated() {
            let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: frame.trackingNormal ? .normal : .limited, quality: nil)
            if capture.evaluate(sample, newlySeenCells: map.newlySeenCount(from: frame.camera)).isKeep {
                capture.didKeep(sample)
                map.observe(frame.camera, trackingNormal: frame.trackingNormal, time: frame.timestamp)
                kept.append(index)
            }
        }
        return kept
    }

    /// The walk's extremes: wall seen any height up (a walk pitched down covers the wall band
    /// only to where its view stops) and covered ground.
    private static func coveredExtremes(_ map: CoverageMap) -> ClosedRange<Float>? {
        let intervals = map.wallSeenSpans().map(\.span) + map.coveredIntervals(.ground)
        guard let low = intervals.map(\.lowerBound).min(), let high = intervals.map(\.upperBound).max(), low < 0, high > 0 else { return nil }
        return low...high
    }

    private static func confirm(
        _ frames: [PlannedFrame], window: Range<Int>, wall: WallFrame, policy: EndPolicy, planner: GapPlanner, config: CoverageConfig
    ) -> HeldBackWindow? {
        var rest = frames
        rest.removeSubrange(window)
        var walked = simulateWalk(rest, wall: wall, config: config)
        guard let ends = self.ends(policy, walked, stood: rest, walked: walked.walkedPositions, wall: wall) else { return nil }
        prepare(&walked, ends: ends, policy: policy)
        guard let gap = planner.plan(walked), isWithinWalk(gap, rest, wall: wall), isInside(gap, ends: ends, policy: policy),
              satisfiedWithLeadIn(frames, window: window, walked: walked, gap: gap, planner: planner) else { return nil }
        return HeldBackWindow(frames: window, ends: ends, gap: gap)
    }

    /// `walked` holds the positions of the kept views, as `CoverageMap.walkedPositions` does.
    private static func ends(
        _ policy: EndPolicy, _ map: CoverageMap, stood: [PlannedFrame], walked: [SIMD3<Float>], wall: WallFrame
    ) -> ClosedRange<Float>? {
        switch policy {
        case .coveredExtremes:
            return coveredExtremes(map)
        case .walked:
            return walkedEnds(stood: stood, walked: walked, seen: map.seenExtent, wall: wall)
        }
    }

    /// The ends the autopilot's "Can't get there" sets (`Autopilot.endWalkByCantGetThere`): on
    /// each side the phone stands again at the frame that went farthest that way, and the engine
    /// puts the end where `WalkedEnd.choose` does from there. That isn't always the frame's own
    /// place: limited tracking, a phone far out from the wall or one well past the kept views
    /// keeps the end short of it. Planning the held-back window against the frame's place alone
    /// could leave a gap the walk never asks for (review of #169). Nil unless the ends fall
    /// either side of the meter.
    static func walkedEnds(
        stood: [PlannedFrame], walked: [SIMD3<Float>], seen: ClosedRange<Float>?, wall: WallFrame
    ) -> ClosedRange<Float>? {
        func end(_ side: WalkSide) -> Float? {
            let along = { (frame: PlannedFrame) in side.sign * wall.wallPoint(frame.camera.position).s }
            guard let farthest = stood.max(by: { along($0) < along($1) }) else { return nil }
            return WalkedEnd.choose(
                side, phone: farthest.camera.position, trackingNormal: farthest.trackingNormal, walked: walked, wall: wall,
                seen: seen
            ).s
        }
        guard let low = end(.left), let high = end(.right), low < 0, high > 0 else { return nil }
        return low...high
    }

    /// Sets the ends, and for a walked flow skips the ground by the meter, as "Can't get there"
    /// on the walk's first request does (`GuidancePlanner.groundByMeterDone`, ±0.5 m).
    private static func prepare(_ map: inout CoverageMap, ends: ClosedRange<Float>, policy: EndPolicy) {
        map.setEnd(.left, at: ends.lowerBound)
        map.setEnd(.right, at: ends.upperBound)
        if case .walked = policy { map.markSkipped(.ground, -0.5...0.5) }
    }

    private static func isInside(_ gap: GapPlan, ends: ClosedRange<Float>, policy: EndPolicy) -> Bool {
        guard case .walked(let margin) = policy else { return true }
        return ends.lowerBound + margin <= gap.span.lowerBound && gap.span.upperBound <= ends.upperBound - margin
    }

    /// Whether `gap` lies between the farthest positions along the wall `frames` stood at.
    private static func isWithinWalk(_ gap: GapPlan, _ frames: [PlannedFrame], wall: WallFrame) -> Bool {
        let s = frames.map { wall.wallPoint($0.camera.position).s }
        guard let low = s.min(), let high = s.max() else { return false }
        return low <= gap.span.lowerBound && gap.span.upperBound <= high
    }

    /// The frames the app replays for a held-back window: two lead-in frames before it, so the
    /// auto-capture's tracking-stable wait (0.5 s) has passed when the window starts.
    public static func gapReplayRange(_ window: Range<Int>) -> Range<Int> {
        max(0, window.lowerBound - 2)..<window.upperBound
    }

    private static func satisfiedWithLeadIn(_ frames: [PlannedFrame], window: Range<Int>, walked: CoverageMap, gap: GapPlan, planner: GapPlanner) -> Bool {
        var restored = walked
        var capture = AutoCapture()
        for frame in frames[gapReplayRange(window)] {
            let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: frame.trackingNormal ? .normal : .limited, quality: nil)
            if capture.evaluate(sample, newlySeenCells: restored.newlySeenCount(from: frame.camera)).isKeep {
                capture.didKeep(sample)
                restored.observe(frame.camera, trackingNormal: frame.trackingNormal, time: frame.timestamp)
            }
        }
        return planner.isSatisfied(gap, restored)
    }
}
