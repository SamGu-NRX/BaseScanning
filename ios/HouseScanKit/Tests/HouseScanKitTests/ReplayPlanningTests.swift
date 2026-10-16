@testable import HouseScanKit
import simd
import Testing

@Suite struct ReplayPlanningTests {
    /// Portrait camera 2.8 m in front of the wall z = 0 at height 1.4, pitched down 12 degrees, so
    /// one view holds every sample row of both bands. Looking at s = x, with the image spanning
    /// 31.03 degrees either side of its axis: the top wall row (2.286 m) is 17.6 degrees above
    /// level, 29.6 above the axis; the ground band's far row (1.2 m out) 41.2 below level, 29.2
    /// below the axis; the wall's foot is 63.4 degrees from the ground's normal, under 65.
    static func planned(x: Float, t: Double) -> PlannedFrame {
        let camera = portraitCamera(at: SIMD3(x, 1.4, 2.8), forward: forwardFacingWall(pitchedDown: 12))
        return PlannedFrame(camera: camera, timestamp: t, trackingNormal: true)
    }

    /// Walk left from 0 to -5, then right to 5, in 0.5 m steps 0.55 s apart (0.91 m/s, under the
    /// 1.5 m/s limit): 11 + 20 = 31 frames.
    static func outAndBack() -> [PlannedFrame] {
        // Typed closures and two statements: Swift 6.2 (CI's Xcode 26.6) times out type-checking
        // the one-expression form.
        let left: [Float] = (0...10).map { (step: Int) -> Float in -Float(step) * 0.5 }
        let right: [Float] = (1...20).map { (step: Int) -> Float in -5 + Float(step) * 0.5 }
        let xs = left + right
        return xs.enumerated().map { (index: Int, x: Float) -> PlannedFrame in
            planned(x: x, t: Double(index) * 0.55)
        }
    }

    @Test func assumedWallFromAStraightWalk() throws {
        // x from -5 to 5: centroid (0, 1.4, 2.8); the covariance has only an xx term, so the walk runs
        // along (1, 0, 0) and the perpendicular is (0, 0, 1). The cameras look along -z, against it, so
        // outward = (0, 0, 1). Ground = 1.4 - 1.4 = 0; the path's middle is x = 0 (5 m of 10), so the
        // meter is at (0, 1.5, 2.8 - offset).
        let frames = (0...20).map { Self.planned(x: -5 + Float($0) * 0.5, t: Double($0) * 0.55) }
        let result = try #require(ReplayPlanning.assumedWall(frames: frames))
        #expect(nearlyEqual(result.wall.outward, SIMD3(0, 0, 1)))
        #expect(nearlyEqual(result.wall.groundY, 0))
        #expect(nearlyEqual(result.wall.meter, SIMD3(0, 1.5, 2.8 - result.offset)))
        #expect(result.coveredCells > 0)
        // The offset search maximises covered cells; it need not recover the true 2.8. Straight
        // ahead, nearer than 2.70 m the ground band's far row leaves the bottom of the image (and
        // nearer than 2.56 m the top wall row, 2.286 m, the top); past 3.0 m the ground at the
        // wall's foot is more than 65 degrees from its normal. It is 2.75 today.
        #expect(result.offset >= 2.5 && result.offset <= 3.0)
    }

    @Test func assumedMeterGoesWhereTheWalkFacesTheWall() throws {
        // x from -10 to 10 at z = 2.6. Only x <= -4 faces the wall z = 0; the rest look along the walk
        // (+x), tilted up 10 degrees. Such a camera sees no wall cell at any offset d: it needs
        // |s - x| >= d / tan(24.8 deg) = 2.17 d to be inside the image's horizontal half-width
        // (atan(240 / 500) less the 3 % margin), but at most d tan(65 deg) = 2.14 d to be within
        // 65 degrees of the normal. Its lowest ray, 22.6 degrees down, meets the ground 3.4 m out,
        // past the 3.0 m that 65 degrees allows from 1.4 m up. So only x in [-10, -4] covers
        // anything, and the meter belongs in its middle, not at the walk's middle x = 0.
        let frames = (0...40).map { (step: Int) -> PlannedFrame in
            let x = -10 + Float(step) * 0.5
            let forward = x <= -4 ? forwardFacingWall(pitchedDown: 20) : simd_normalize(SIMD3<Float>(1, 0.176, 0))
            return PlannedFrame(camera: portraitCamera(at: SIMD3(x, 1.4, 2.6), forward: forward), timestamp: Double(step) * 0.55, trackingNormal: true)
        }
        let result = try #require(ReplayPlanning.assumedWall(frames: frames))
        #expect(nearlyEqual(result.wall.outward, SIMD3(0, 0, 1)))
        #expect(abs(result.wall.meter.x - -7) <= 0.5, "meter at x = \(result.wall.meter.x)")
        #expect(result.coveredCells > 0)
    }

    @Test func assumedWallNeedsTwoFrames() {
        #expect(ReplayPlanning.assumedWall(frames: [Self.planned(x: 0, t: 0)]) == nil)
    }

    @Test func heldBackWindowLeavesAGapThatItsReplayFills() throws {
        let frames = Self.outAndBack()
        let wall = standardWall()
        let planner = GapPlanner()
        let held = try #require(ReplayPlanning.heldBackWindow(frames: frames, wall: wall))
        #expect(held.frames.count >= 3 && held.frames.count <= 8)
        #expect(held.ends.lowerBound < 0 && held.ends.upperBound > 0)

        // The walk without the window, with the ends marked, leaves exactly that gap unsatisfied.
        var rest = frames
        rest.removeSubrange(held.frames)
        // Its gap lies where the rest of the walk went, so ends set where the phone stood keep it.
        let stood = rest.map { wall.wallPoint($0.camera.position).s }
        #expect((stood.min() ?? 0) <= held.gap.span.lowerBound && held.gap.span.upperBound <= (stood.max() ?? 0))
        var walked = ReplayPlanning.simulateWalk(rest, wall: wall)
        walked.setEnd(.left, at: held.ends.lowerBound)
        walked.setEnd(.right, at: held.ends.upperBound)
        #expect(planner.plan(walked) == held.gap)
        #expect(!planner.isSatisfied(held.gap, walked))

        // Replaying the window with its lead-in through a fresh auto-capture fills it.
        let replay = ReplayPlanning.gapReplayRange(held.frames)
        #expect(replay.upperBound == held.frames.upperBound)
        #expect(replay.lowerBound == max(0, held.frames.lowerBound - 2))
        var capture = AutoCapture()
        for frame in frames[replay] {
            let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: .normal, quality: nil)
            if capture.evaluate(sample, newlySeenCells: walked.newlySeenCount(from: frame.camera)).isKeep {
                capture.didKeep(sample)
                walked.observe(frame.camera, trackingNormal: true)
            }
        }
        #expect(planner.isSatisfied(held.gap, walked))
    }

    @Test func simulateWalkKeepsEveryStrideOnceTrackingSettles() {
        // Frame 0 waits for tracking; from 0.55 s on, each 0.5 m stride meets the 0.5 m spacing, so
        // the cells around every stride are covered and nothing in [-4, 4] is left unseen on the wall.
        let frames = (0...20).map { Self.planned(x: -5 + Float($0) * 0.5, t: Double($0) * 0.55) }
        let map = ReplayPlanning.simulateWalk(frames, wall: standardWall())
        #expect(map.coveredFraction(.wall, in: -4...4) == 1)
        #expect(map.coveredFraction(.ground, in: -4...4) == 1)
    }
}

/// Review of #169: the "Can't get there" flow's ends, planned, are where the engine's own
/// chooser (`WalkedEnd.choose`) puts them from the farthest frame each way, not that frame's
/// place.
@Suite struct WalkedEndsPlanningTests {
    static func frame(s: Float, out: Float = 2, trackingNormal: Bool = true) -> PlannedFrame {
        PlannedFrame(camera: makeCamera(at: SIMD3(s, 1.2, out), forward: SIMD3(0, 0, -1), right: SIMD3(1, 0, 0)), timestamp: 0, trackingNormal: trackingNormal)
    }

    @Test func normalTrackingWithinTheKeptViewsEndsAtThePhone() throws {
        let wall = standardWall()
        let stood = [Self.frame(s: -3), Self.frame(s: 0), Self.frame(s: 3)]
        let ends = try #require(ReplayPlanning.walkedEnds(stood: stood, walked: stood.map(\.camera.position), seen: nil, wall: wall))
        #expect(nearlyEqual(ends.lowerBound, -3))
        #expect(nearlyEqual(ends.upperBound, 3))
    }

    @Test func limitedTrackingAtTheFarthestFrameKeepsTheEndToTheKeptViews() throws {
        let wall = standardWall()
        let stood = [Self.frame(s: -3), Self.frame(s: 0), Self.frame(s: 2), Self.frame(s: 4, trackingNormal: false)]
        let kept = stood.prefix(3).map(\.camera.position)
        let ends = try #require(ReplayPlanning.walkedEnds(stood: stood, walked: kept, seen: nil, wall: wall))
        #expect(nearlyEqual(ends.upperBound, 2), "limited tracking caps the end at the farthest kept view")
        #expect(nearlyEqual(ends.lowerBound, -3))
    }

    @Test func aPhoneFarOutFromTheWallKeepsTheEndToTheKeptViews() throws {
        let wall = standardWall()
        let stood = [Self.frame(s: -3), Self.frame(s: 1), Self.frame(s: 4, out: WalkedEnd.phoneOutLimit + 1)]
        let kept = stood.prefix(2).map(\.camera.position)
        let ends = try #require(ReplayPlanning.walkedEnds(stood: stood, walked: kept, seen: nil, wall: wall))
        #expect(nearlyEqual(ends.upperBound, 1))
    }

    @Test func aPhoneWellPastTheEvidenceStopsMaxPastEvidenceOn() throws {
        let wall = standardWall()
        let stood = [Self.frame(s: -3), Self.frame(s: 1), Self.frame(s: 6)]
        let kept = stood.prefix(2).map(\.camera.position)
        let ends = try #require(ReplayPlanning.walkedEnds(stood: stood, walked: kept, seen: nil, wall: wall))
        #expect(nearlyEqual(ends.upperBound, 1 + WalkedEnd.maxPastEvidence))
    }
}
