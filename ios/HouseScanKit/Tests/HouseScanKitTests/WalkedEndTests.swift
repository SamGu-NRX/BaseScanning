import Foundation
import HouseScanKit
import simd
import Testing

/// Where "Can't get there" (and "Wall ends here" during the walk) puts a wall end: the phone's s
/// along the chain on its own side with tracking normal, else within the stretch walked on that
/// side (`WalkedEnd`, team decision on #71, 2026-09-27).
@Suite struct WalkedEndTests {
    /// A kept view's position, 2 m in front of the standard wall at s = x.
    static func stood(_ x: Float) -> SIMD3<Float> { SIMD3(x, 1.4, 2) }

    @Test func straightWallEndsWhereThePhoneIs() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3].map(Self.stood)
        #expect(WalkedEnd.farthest(.left, walked: walked, wall: wall) == 3)
        #expect(WalkedEnd.end(.left, phone: Self.stood(-2.5), walked: walked, wall: wall) == -2.5)
        // Walked back toward the meter: the end follows the phone, not the farthest view.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-1.2), walked: walked, wall: wall) == -1.2)
        // Ahead of every kept view (#71, changed deliberately: this was capped at -3, the farthest
        // kept view): with tracking normal, standing there is the evidence.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-4), walked: walked, wall: wall) == -4)
        let ahead = WalkedEnd.choose(.left, phone: Self.stood(-4), walked: walked, wall: wall)
        #expect(ahead == WalkedEnd.Choice(s: -4, phone: -4, cap: -3, capped: false))
        // With tracking limited the phone's place is less sure: no farther than the kept views.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-4), trackingNormal: false, walked: walked, wall: wall) == -3)
        #expect(WalkedEnd.end(.left, phone: Self.stood(-2.5), trackingNormal: false, walked: walked, wall: wall) == -2.5)
        // Within the walked stretch, how far out the phone stands doesn't change the end.
        #expect(WalkedEnd.end(.left, phone: SIMD3(-2.5, 1.4, 6), walked: walked, wall: wall) == -2.5)
    }

    /// Across the meter the phone's place says nothing about this side: the end goes as far as the
    /// walk went there, not to the meter (which dropped run 1's walk).
    @Test func phoneAcrossTheMeterUsesTheWalkedStretch() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3, 1, 2].map(Self.stood)
        #expect(WalkedEnd.end(.left, phone: Self.stood(1.5), walked: walked, wall: wall) == -3)
        #expect(WalkedEnd.end(.right, phone: Self.stood(-2), walked: walked, wall: wall) == 2)
        // The phone's place unknown (lost tracking): the same.
        #expect(WalkedEnd.end(.left, phone: nil, walked: walked, wall: wall) == -3)
        #expect(WalkedEnd.choose(.left, phone: nil, walked: walked, wall: wall).capped)
        // Tracking limited across the meter: the same.
        #expect(WalkedEnd.end(.left, phone: Self.stood(1.5), trackingNormal: false, walked: walked, wall: wall) == -3)
    }

    /// A side with no kept view ends at the meter unless the phone stands on it with tracking
    /// normal. Changed deliberately (#71): the phone at -1 m on the left ended at 0 because no
    /// photo was kept there; it now ends under the phone.
    @Test func sideNeverWalkedEndsAtTheMeterUnlessThePhoneIsThere() {
        let wall = standardWall()
        let walked = [0.4, 1, 2].map(Self.stood)
        #expect(WalkedEnd.farthest(.left, walked: walked, wall: wall) == 0)
        #expect(WalkedEnd.end(.left, phone: Self.stood(2), walked: walked, wall: wall) == 0)
        #expect(WalkedEnd.end(.left, phone: Self.stood(-1), walked: walked, wall: wall) == -1)
        #expect(WalkedEnd.end(.left, phone: Self.stood(-1), trackingNormal: false, walked: walked, wall: wall) == 0)
        #expect(WalkedEnd.end(.left, phone: nil, walked: [], wall: wall) == 0)
    }

    /// Build 4.1 run 2 (#71): the two photos kept were right of the meter, at s = 0.32 and 0.11,
    /// and the homeowner walked to the end post about 1.2 m left of it, where the strip showed
    /// cells left of the meter. "Wall ends here" there put the end at the meter and the strip
    /// dropped them. It now lands under the phone and they stay.
    @Test func run2WallEndsHereAtThePostLandsAtThePhone() {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0.32), trackingNormal: true)
        map.observe(wallCamera(s: 0.11), trackingNormal: true)
        let seen = map.seenExtent
        #expect((seen?.lowerBound ?? 0) < -0.3)
        let phone = Self.stood(-1.2)
        #expect(WalkedEnd.side(phone: phone, wall: map.wall, leftEnd: nil, rightEnd: nil) == .left)
        let left = WalkedEnd.end(.left, phone: phone, walked: map.walkedPositions, wall: map.wall)
        #expect(left != 0)
        #expect(nearlyEqual(left, -1.2))
        // Every cell the strip showed on the left is between the ends, so none is dropped.
        #expect((seen?.lowerBound ?? 0) >= left)
        #expect(WalkedEnd.shownPast(.left, s: left, seen: seen) == nil)
    }

    /// Review of #136: past the kept views, the phone's s counts only within `phoneOutLimit`
    /// (4 m) of the wall's line, in front or behind; farther out the cap applies. (This doesn't
    /// catch a skewed wall line, #69, which measures out from itself: see
    /// `endAtThePhoneStaysNearTheEvidence`.)
    @Test func phoneFarFromTheWallLineFallsBackToTheCap() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3].map(Self.stood)
        #expect(WalkedEnd.phoneOutLimit == 4)
        #expect(WalkedEnd.end(.left, phone: SIMD3(-4, 1.4, 3.9), walked: walked, wall: wall) == -4)
        let far = WalkedEnd.choose(.left, phone: SIMD3(-4, 1.4, 4.5), walked: walked, wall: wall)
        #expect(far == WalkedEnd.Choice(s: -3, phone: -4, cap: -3, capped: true))
        #expect(WalkedEnd.end(.left, phone: SIMD3(-4, 1.4, -4.5), walked: walked, wall: wall) == -3)
        // Short of the farthest kept view, the phone's s is inside the cap anyway.
        #expect(WalkedEnd.end(.left, phone: SIMD3(-2, 1.4, 6), walked: walked, wall: wall) == -2)
    }

    /// Re-review of #136: an end at the phone lands no more than `maxPastEvidence` (2 m) past the
    /// farthest kept view or seen cell on its side. A wall line skewed by a bad tap (#69) reads
    /// the phone's distance out from itself, so the 4 m guard can pass a phone whose s runs far
    /// along nothing; this bound doesn't depend on the line's direction.
    @Test func endAtThePhoneStaysNearTheEvidence() {
        let wall = standardWall()
        let walked = [0, -1, -2].map(Self.stood)
        #expect(WalkedEnd.maxPastEvidence == 2)
        // 2 m past the farthest kept view (-2): at the phone.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-4), walked: walked, wall: wall) == -4)
        // 4 m past it, on the line (out 2 m): capped 2 m past it.
        let far = WalkedEnd.choose(.left, phone: Self.stood(-6), walked: walked, wall: wall)
        #expect(far == WalkedEnd.Choice(s: -4, phone: -6, cap: -2, capped: true, pastEvidence: true))
        // The kept-photo cap is flagged apart from this bound, for the log.
        #expect(!WalkedEnd.choose(.left, phone: Self.stood(-6), trackingNormal: false, walked: walked, wall: wall).pastEvidence)
        // Cells seen farther than the kept views move the bound out with them.
        let seen: ClosedRange<Float> = -3.5 ... 1
        #expect(WalkedEnd.end(.left, phone: Self.stood(-5.5), walked: walked, wall: wall, seen: seen) == -5.5)
        #expect(WalkedEnd.end(.left, phone: Self.stood(-6), walked: walked, wall: wall, seen: seen) == -5.5)
        // Seen cells on the other side don't.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-6), walked: walked, wall: wall, seen: -0.5 ... 6) == -4)
        // Nothing walked or seen on that side: up to 2 m from the meter.
        #expect(WalkedEnd.end(.left, phone: Self.stood(-3), walked: [], wall: wall) == -2)
    }

    /// Review of #147: a brief tracking drop just before "Wall ends here" doesn't send run 2's end
    /// back to the meter. The rule reads tracking at the tap only (the engine passes the current
    /// frame's), so once it is normal again the end is at the phone, within `maxPastEvidence`.
    /// A 1 s debounce here put the end at the meter.
    @Test func tapRightAfterABriefTrackingDropEndsAtThePhone() {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0.32), trackingNormal: true)
        map.observe(wallCamera(s: 0.11), trackingNormal: true)
        // The drop: frames at the post with tracking limited keep no view.
        map.observe(wallCamera(s: -1.2), trackingNormal: false)
        #expect(map.walkedFarthest(.left) == 0)
        let phone = Self.stood(-1.2)
        // During the drop: the kept-photo cap, the meter.
        #expect(WalkedEnd.end(.left, phone: phone, trackingNormal: false, walked: map.walkedPositions, wall: map.wall, seen: map.seenExtent) == 0)
        // The next frame, tracking normal again: at the phone.
        let after = WalkedEnd.choose(.left, phone: phone, trackingNormal: true, walked: map.walkedPositions, wall: map.wall, seen: map.seenExtent)
        #expect(!after.capped)
        #expect(nearlyEqual(after.s, -1.2))
    }

    /// Review of #136: with tracking limited the end is capped, and the strip's cells count toward
    /// what it leaves out only up to the phone, not what its camera saw ahead of it.
    @Test func limitedTrackingCountsSeenCellsOnlyUpToThePhone() {
        let wall = standardWall()
        let walked = [0, -1, -2].map(Self.stood)
        let seen: ClosedRange<Float> = -5 ... 1
        let choice = WalkedEnd.choose(.left, phone: Self.stood(-3.5), trackingNormal: false, walked: walked, wall: wall)
        #expect(choice.capped && choice.s == -2)
        let counted = WalkedEnd.countedSeen(.left, choice: choice, seen: seen)
        #expect(counted == Float(-3.5) ... Float(1))
        #expect(WalkedEnd.leftOut(.left, s: choice.s, walked: walked, wall: wall, seen: counted) == 1.5)
        // The phone short of the cap: nothing past the end counts (under the 1 m minimum).
        let near = WalkedEnd.choose(.left, phone: Self.stood(-1.5), trackingNormal: false, walked: walked, wall: wall)
        #expect(WalkedEnd.leftOut(.left, s: near.s, walked: walked, wall: wall, seen: WalkedEnd.countedSeen(.left, choice: near, seen: seen)) == 0.5)
        // Right side, mirrored.
        let right = WalkedEnd.Choice(s: 1, phone: 2.5, cap: 1, capped: true)
        #expect(WalkedEnd.countedSeen(.right, choice: right, seen: -1 ... 4) == Float(-1) ... Float(2.5))
        // Across the meter or place lost: the whole extent. At the phone (not capped): none.
        let across = WalkedEnd.choose(.left, phone: Self.stood(1), walked: walked, wall: wall)
        #expect(WalkedEnd.countedSeen(.left, choice: across, seen: seen) == seen)
        let atPhone = WalkedEnd.choose(.left, phone: Self.stood(-3.5), walked: walked, wall: wall)
        #expect(WalkedEnd.countedSeen(.left, choice: atPhone, seen: seen) == nil)
    }

    /// Where the cap decides the end, cells the strip shows past it are said, not dropped
    /// silently: `leftOut` counts them from `shownPastMinimum` (1 m), with the walk past the end.
    @Test func cappedEndSaysWhatTheStripShowedPastIt() {
        let wall = standardWall()
        let walked = [0, -1, -2, 1].map(Self.stood)
        let seen: ClosedRange<Float> = -3.5 ... 2
        // Across the meter: the end is the farthest kept view on the left, -2.
        let choice = WalkedEnd.choose(.left, phone: Self.stood(1), walked: walked, wall: wall)
        #expect(choice.capped && choice.s == -2)
        #expect(WalkedEnd.shownPast(.left, s: choice.s, seen: seen) == 1.5)
        #expect(WalkedEnd.leftOut(.left, s: choice.s, walked: walked, wall: wall, seen: seen) == 1.5)
        #expect(WalkedEnd.leavesOut(side: .left, s: choice.s, walked: walked, wall: wall, phoneOut: nil, onWalkTask: true, seen: seen) == 1.5)
        // Under a meter past it: the camera's look-ahead, not said.
        #expect(WalkedEnd.shownPast(.left, s: -2.6, seen: seen) == nil)
        #expect(WalkedEnd.shownPast(.right, s: 1, seen: seen) == 1)
        #expect(WalkedEnd.shownPast(.right, s: 1, seen: nil) == nil)
        // Without `seen` only the walk past the end counts, as before.
        #expect(WalkedEnd.leftOut(.left, s: -1, walked: walked, wall: wall, seen: nil) == 1)
        #expect(WalkedEnd.leftOut(.left, s: -1, walked: walked, wall: wall, seen: seen) == 2.5)
        // Re-review of #136: when the seen cells decide it, the words say "you saw".
        #expect(WalkedEnd.leftOutIsSeen(.left, s: choice.s, walked: walked, wall: wall, seen: seen))
        #expect(WalkedEnd.leftOutIsSeen(.left, s: -1, walked: walked, wall: wall, seen: seen))
        #expect(!WalkedEnd.leftOutIsSeen(.left, s: -1, walked: walked, wall: wall, seen: nil))
        // Walked 1.5 m past -0.5, seen 1.3 m past it: the walk decides, "you walked".
        #expect(!WalkedEnd.leftOutIsSeen(.left, s: -0.5, walked: walked, wall: wall, seen: -1.8 ... 1))
    }

    /// Round a followed corner s runs on along the chain (`rightCornerWall`: corner at s = 3, the
    /// next piece running toward -z facing +x). `CoverageAcrossCornersTests.roundCornerCamera(s:)`
    /// stands 2 m in front of that piece at chain s = c.
    @Test func cornerChainMeasuresAlongTheChain() throws {
        let wall = try rightCornerWall()
        let corner = CoverageAcrossCornersTests()
        let walked = [Self.stood(1), Self.stood(2.5)] + [4.0, 5.0].map { corner.roundCornerCamera(s: $0).position }
        #expect(nearlyEqual(WalkedEnd.farthest(.right, walked: walked, wall: wall), 5))
        #expect(nearlyEqual(WalkedEnd.end(.right, phone: corner.roundCornerCamera(s: 4.6).position, walked: walked, wall: wall), 4.6))
        // Past the farthest kept view, round the corner (#71, changed deliberately: this was capped
        // at 5): the phone's place along the chain.
        #expect(nearlyEqual(WalkedEnd.end(.right, phone: corner.roundCornerCamera(s: 5.5).position, walked: walked, wall: wall), 5.5))
        #expect(nearlyEqual(WalkedEnd.end(.right, phone: corner.roundCornerCamera(s: 5.5).position, trackingNormal: false, walked: walked, wall: wall), 5))
        // In front of the meter's piece, short of the corner.
        #expect(nearlyEqual(WalkedEnd.end(.right, phone: Self.stood(2), walked: walked, wall: wall), 2))
    }

    /// The same rule read off a coverage map: every kept view counts as walked, whether or not it
    /// saw anything.
    @Test func mapReadsItsKeptViews() {
        var map = CoverageMap(wall: standardWall())
        for x: Float in [0, -1.5, 2.5] { map.observe(wallCamera(s: x), trackingNormal: true) }
        // Not kept: limited tracking adds no view.
        map.observe(wallCamera(s: -4), trackingNormal: false)
        #expect(map.walkedFarthest(.left) == 1.5)
        #expect(map.walkedFarthest(.right) == 2.5)
    }

    /// "Wall ends here" during a tilt or step-back request ends the side the phone is on. The
    /// planner does the left side first, so it used to end the left whenever that was open: with
    /// the phone 2.5 m right of the meter, the left end at the farthest walked point, 3 m left.
    @Test func wallEndsHereEndsTheSideThePhoneIsOn() throws {
        let wall = standardWall()
        let walked = [0, -1, -2, -3, 1, 2, 2.5].map(Self.stood)
        let side = try #require(WalkedEnd.side(phone: Self.stood(2.5), wall: wall, leftEnd: nil, rightEnd: nil))
        #expect(side == .right)
        #expect(WalkedEnd.end(side, phone: Self.stood(2.5), walked: walked, wall: wall) == 2.5)
        // What ending the left there did.
        #expect(WalkedEnd.end(.left, phone: Self.stood(2.5), walked: walked, wall: wall) == -3)
        #expect(WalkedEnd.side(phone: Self.stood(-2), wall: wall, leftEnd: nil, rightEnd: nil) == .left)
        #expect(WalkedEnd.side(phone: Self.stood(-2), wall: wall, leftEnd: nil, rightEnd: 2.5) == .left)
        #expect(WalkedEnd.side(phone: Self.stood(2), wall: wall, leftEnd: -3, rightEnd: nil) == .right)
        // Place lost, at the meter, or on a side already ended: the first side without an end.
        #expect(WalkedEnd.side(phone: nil, wall: wall, leftEnd: nil, rightEnd: nil) == .left)
        #expect(WalkedEnd.side(phone: nil, wall: wall, leftEnd: -3, rightEnd: nil) == .right)
        #expect(WalkedEnd.side(phone: Self.stood(0), wall: wall, leftEnd: nil, rightEnd: nil) == .left)
        #expect(WalkedEnd.side(phone: Self.stood(2), wall: wall, leftEnd: nil, rightEnd: 2.5) == .left)
        #expect(WalkedEnd.side(phone: Self.stood(2), wall: wall, leftEnd: -3, rightEnd: 2.5) == nil)
    }

    /// Ending both sides at the meter without walking ("Wall ends here" or "Can't get there" on
    /// each side before a step) puts both ends there: a wall of no length, which the walk must
    /// not finish with. A meter by a corner, with one end close to it, is still a wall. The right
    /// end is under the phone, 0.1 m right (#71: it was capped at 0, the only kept view).
    @Test func endsAtTheMeterAreTooClose() {
        let wall = standardWall()
        var map = CoverageMap(wall: wall)
        map.observe(wallCamera(s: 0), trackingNormal: true)
        let phone = Self.stood(0.1)
        let left = WalkedEnd.end(.left, phone: phone, walked: map.walkedPositions, wall: wall)
        let right = WalkedEnd.end(.right, phone: phone, walked: map.walkedPositions, wall: wall)
        #expect(left == 0 && right == 0.1)
        #expect(!map.endsTooClose)
        map.setEnd(.left, at: left)
        #expect(!map.endsTooClose)
        map.setEnd(.right, at: right)
        #expect(!map.endsTooClose, "a positive 0.1 m return is still a wall")
        map.setEnd(.right, at: 0.5)
        #expect(!map.endsTooClose)
        map.setEnd(.right, at: left)
        #expect(map.endsTooClose, "coincident ends describe no wall")
        map.setEnd(.left, at: -0.1)
        map.setEnd(.right, at: 3)
        #expect(!map.endsTooClose)
    }

    /// A phone past an unexplored end whose view shows nothing between the ends takes photos the
    /// scan can't use; one that still sees the wall between them doesn't, nor one past a limit end,
    /// whose ground still counts.
    @Test func pastAnUnexploredEndWithNothingBetweenTheEndsInView() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.right, at: 2)
        map.setEnd(.left, at: -2)
        // `wallCamera` sees the wall within 0.9 m of its s.
        #expect(map.unexploredEndPassed(by: wallCamera(s: 4)) == .right)
        #expect(map.unexploredEndPassed(by: wallCamera(s: -4)) == .left)
        #expect(map.unexploredEndPassed(by: wallCamera(s: 2.5)) == nil)
        #expect(map.unexploredEndPassed(by: wallCamera(s: 1)) == nil)
        // #70: while the next wall round that end is looked for, past it is where the homeowner
        // was asked to go. The other side still counts.
        #expect(map.unexploredEndPassed(by: wallCamera(s: 4), ignoring: .right) == nil)
        #expect(map.unexploredEndPassed(by: wallCamera(s: -4), ignoring: .right) == .left)
        map.setEndIsLimit(.right, true)
        #expect(map.unexploredEndPassed(by: wallCamera(s: 4)) == nil)
    }

    /// Device run 1 in miniature on the synthetic replay (ios/HouseScanUITests/Fixtures): the walk
    /// goes to 5 m left and 5 m right, pitched 20 degrees down. Its views cover both bands, the
    /// wall to the walk's 4.5 ft (`CoverageConfig.wallWalkHeight`), along the whole walk and
    /// 0.33 m past each turn, so the unbroken covered reach is 5.334 m either side. (With the wall
    /// band at 7.5 ft it was 0.762 m, and ends there would have left out the rest of the walk.)
    /// Ends where the phone stood keep its wall and ground whatever the reach.
    @Test func syntheticWalkKeepsItsWallAndGround() throws {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../../HouseScanUITests/Fixtures/synthetic-wall")
            .standardizedFileURL
        let session = try ReplaySession.load(folder: folder)
        let declared = try #require(session.declaredWall)
        let wall = try #require(WallFrame(meter: declared.meter, outward: declared.outward, groundY: declared.groundY))
        var map = CoverageMap(wall: wall)
        let cameras = session.frames.map { frame in
            CameraFrame(cameraToWorld: frame.cameraToWorld, intrinsics: frame.intrinsics, imageSize: SIMD2(Float(frame.width), Float(frame.height)))
        }
        // Frames 5 to 37: stepping back from the meter, then the walk (0 to -5 m and back to 5 m).
        for index in 5..<38 {
            map.observe(cameras[index], trackingNormal: true, time: session.frames[index].timestamp)
        }
        let planner = GuidancePlanner()
        let oldLeft = planner.reach(.left, coverage: map)
        let oldRight = planner.reach(.right, coverage: map)
        #expect(nearlyEqual(oldLeft, 5.334) && nearlyEqual(oldRight, 5.334))
        // Where the walk turned (frame 17, x = -5) and where it stopped (frame 37, x = 5).
        let left = WalkedEnd.end(.left, phone: cameras[17].position, walked: map.walkedPositions, wall: map.wall)
        let right = WalkedEnd.end(.right, phone: cameras[37].position, walked: map.walkedPositions, wall: map.wall)
        #expect(nearlyEqual(left, -5, 1e-3))
        #expect(nearlyEqual(right, 5, 1e-3))

        var old = map
        old.setEnd(.left, at: -oldLeft)
        old.setEnd(.right, at: oldRight)
        #expect(old.groundDepthSpans().allSatisfy { $0.span.lowerBound >= -oldLeft && $0.span.upperBound <= oldRight })

        map.setEnd(.left, at: left)
        map.setEnd(.right, at: right)
        let wallSeen = map.wallSeenSpans()
        let groundSeen = map.groundDepthSpans()
        #expect(!wallSeen.isEmpty && !groundSeen.isEmpty)
        #expect(wallSeen.allSatisfy { $0.span.lowerBound >= left && $0.span.upperBound <= right })
        // The ground in front of the meter and along both sides of it.
        #expect(groundSeen.contains { $0.span.contains(0) })
        #expect(groundSeen.contains { $0.span.upperBound < -3 || $0.span.lowerBound < -3 })
        #expect(groundSeen.contains { $0.span.upperBound > 3 })
    }

    /// What an end at s leaves out of the walk: how far the farthest kept view on that side is
    /// past it, once that is at least a keyframe's spacing (0.5 m).
    @Test func walkedPastCountsFromAKeyframesSpacing() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3, 1, 2].map(Self.stood)
        #expect(WalkedEnd.walkedPast(.left, s: -1, walked: walked, wall: wall) == 2)
        #expect(WalkedEnd.walkedPast(.left, s: -2.5, walked: walked, wall: wall) == 0.5)
        #expect(WalkedEnd.walkedPast(.left, s: -2.75, walked: walked, wall: wall) == nil)
        #expect(WalkedEnd.walkedPast(.left, s: -3, walked: walked, wall: wall) == nil)
        #expect(WalkedEnd.walkedPast(.right, s: 0.5, walked: walked, wall: wall) == 1.5)
        // A side never walked leaves nothing out.
        #expect(WalkedEnd.walkedPast(.left, s: 0, walked: [], wall: wall) == nil)
    }

    /// Issue #66: the strip's "leaves out N ft you walked" shows only while the walk asks to walk
    /// that side or mark its end. During a tilt or step-back request it stays off, however far
    /// back the phone's place is from the farthest view.
    @Test func leavesOutOnlyOnAWalkTask() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3].map(Self.stood)
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: 2, onWalkTask: true) == 2)
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: 2, onWalkTask: false) == nil)
        // At the front of the walk there is nothing to leave out, on any task.
        #expect(WalkedEnd.leavesOut(side: .left, s: -2.8, walked: walked, wall: wall, phoneOut: 2, onWalkTask: true) == nil)
        // The reticle's end (no phone distance): only the task counts.
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: nil, onWalkTask: true) == 2)
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: nil, onWalkTask: false) == nil)
    }

    /// Issue #66, run 3: walking out into the yard moved the phone's place along the wall and the
    /// line counted up to 11 ft. More than twice the walk's stand-off (2 m) out from the wall, the
    /// line stays off; at it, it shows.
    @Test func leavesOutNotWithThePhoneFarOut() {
        let wall = standardWall()
        let walked = [0, -1, -2, -3].map(Self.stood)
        let limit = 2 * GuidanceConfig().standOff
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: limit, onWalkTask: true) == 2)
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: limit + 0.5, onWalkTask: true) == nil)
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: 6, onWalkTask: true) == nil)
        // Behind the wall's line counts by distance too.
        #expect(WalkedEnd.leavesOut(side: .left, s: -1, walked: walked, wall: wall, phoneOut: -6, onWalkTask: true) == nil)
        // Where the end lands doesn't change with the phone's distance out (`end`).
        #expect(WalkedEnd.end(.left, phone: SIMD3(-1, 1.4, 6), walked: walked, wall: wall) == -1)
    }

    /// Made-up ends and marks (issue #42): a mark wholly past an end lies past it; one that reaches
    /// an end, straddles one or sits between them doesn't.
    @Test func marksWhollyPastAnEndLiePastIt() {
        let left: Float = -2.0
        let right: Float = 0.3
        #expect(WalkedEnd.liesPastAnEnd(4.0...4.3, leftEnd: left, rightEnd: right))
        #expect(WalkedEnd.liesPastAnEnd(1.5...2.0, leftEnd: left, rightEnd: right))
        #expect(WalkedEnd.liesPastAnEnd(-3.2 ... -2.4, leftEnd: left, rightEnd: right))
        // Reaching an end, straddling one, or between them: on the scanned wall.
        #expect(!WalkedEnd.liesPastAnEnd(0.3...0.6, leftEnd: left, rightEnd: right))
        #expect(!WalkedEnd.liesPastAnEnd(-2.3 ... -1.7, leftEnd: left, rightEnd: right))
        #expect(!WalkedEnd.liesPastAnEnd(-0.5...0.1, leftEnd: left, rightEnd: right))
        // A side without an end has nothing to lie past.
        #expect(!WalkedEnd.liesPastAnEnd(4.0...4.3, leftEnd: left, rightEnd: nil))
        #expect(!WalkedEnd.liesPastAnEnd(-3.2 ... -2.4, leftEnd: nil, rightEnd: nil))
    }
}
