import HouseScanKit
import simd
import Testing

@Suite struct GuidancePlannerTests {
    /// The homeowner standing `out` m from the wall at s = x, phone pitched down 20 degrees.
    static func homeowner(x: Float = 0, out: Float = 2.6) -> CameraFrame {
        portraitCamera(at: SIMD3(x, 1.4, out), forward: forwardFacingWall(pitchedDown: 20))
    }

    /// A map whose stretch in front of the meter, the planner's own window (cells -2 ... 1), is
    /// skipped in both bands, so the walk's first request is done and no band lags there: the walk
    /// goes on to the sides. Covered cells stay covered.
    static func meterGroundSkipped(_ map: CoverageMap = CoverageMap(wall: standardWall())) -> CoverageMap {
        var map = map
        for band in SurfaceBand.allCases {
            map.markSkipped(band, -GuidancePlanner.aimHalfWidth...GuidancePlanner.aimHalfWidth)
        }
        return map
    }

    /// Wall covered and ground unseen around the meter, the ground in front of the meter skipped.
    /// Wall cameras at 0 and 0.3 cover cells -4 ... 5 ([-0.6096, 0.9144], see CoverageMapTests);
    /// they never see the ground.
    static func wallOnlyCoverage() -> CoverageMap {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0), trackingNormal: true)
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        return meterGroundSkipped(map)
    }

    /// The walk's first request is the ground in front of the meter, with or without a camera,
    /// before either side: every check the server runs starts beside the meter.
    @Test func firstRequestIsTheGroundByTheMeter() throws {
        let map = CoverageMap(wall: standardWall())
        var planner = GuidancePlanner()
        let output = planner.update(coverage: map, camera: Self.homeowner(), time: 0)
        #expect(output.task == .aimAtGround(s: 0))
        // The middle of the ground band in front of the meter.
        #expect(nearlyEqual(try #require(output.target), SIMD3(0, 0, 0.6)))
        var noCamera = GuidancePlanner()
        #expect(noCamera.update(coverage: map, camera: nil, time: 0).task == .aimAtGround(s: 0))
        // Once it is done the walk goes left first.
        var skipped = GuidancePlanner()
        #expect(skipped.update(coverage: Self.meterGroundSkipped(), camera: Self.homeowner(), time: 0).task == .walk(.left))
    }

    /// The two-position rule settles it: `frontCamera` sees every ground row of cells -1 and 0 and,
    /// 2.6 m out with a half-angle of 24 degrees across, cells -2 and 1 as well. One view leaves
    /// them seen; a second 0.3 m away covers all four, which satisfies the task: the planner moves
    /// on at once, well inside the dwell. The same views cover the wall's walking band (1.98 m
    /// seen against 4.5 ft asked) over a wider stretch, cells -5 to 6, and the ground over cells
    /// -3 to 4. The walk goes left first, and a lagging band is looked for only ahead of the
    /// camera that way, [-1, 0.3]: there the ground lags over cells -5 and -4, fewer than
    /// `lagRun`'s three, so the walk goes on. No wall task comes up.
    @Test func groundByTheMeterIsMetFromTwoPlaces() {
        var map = CoverageMap(wall: standardWall())
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .aimAtGround(s: 0))
        map.observe(CoverageMapTests.frontCamera(), trackingNormal: true)
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0.2).task == .aimAtGround(s: 0))
        map.observe(CoverageMapTests.frontCamera(x: 0.3), trackingNormal: true)
        for index in -2...1 { #expect(map.level(.ground, index) == .covered, "cell \(index)") }
        let next = planner.update(coverage: map, camera: Self.homeowner(), time: 0.4)
        #expect(next.task == .walk(.left))
        #expect(next.switched == .satisfied)
    }

    /// "Can't get there" on it marks the ground skipped, as the engine does, and settles the
    /// planner's task (`ScanEngine.resetGuidanceAfterSkip`): the walk moves on and doesn't ask for
    /// it again.
    @Test func cantGetThereOnTheGroundByTheMeterMovesOn() {
        var map = CoverageMap(wall: standardWall())
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .aimAtGround(s: 0))
        map.markSkipped(.ground, -0.5...0.5)
        // Skipped is not covered: without settling the request holds for its dwell.
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0.5).task == .aimAtGround(s: 0))
        planner.settleCurrentTask()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0.6).task != .aimAtGround(s: 0))
        // Away from the meter nothing lags, and the walk goes left.
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: -3), time: 10).task == .walk(.left))
    }

    /// Changed deliberately for #77: stepping too close no longer replaces it with "Take a step
    /// back" after the 3 s dwell. On build 4.1 the two alternated as two cards, which read as two
    /// different requests. The request stays and asks to step back as well.
    @Test func groundByTheMeterStaysWithAStepBackHint() {
        var planner = GuidancePlanner()
        let map = CoverageMap(wall: standardWall())
        let first = planner.update(coverage: map, camera: Self.homeowner(), time: 0)
        #expect(first.task == .aimAtGround(s: 0))
        #expect(!first.stepBack)
        for time in [2.9, 3, 10] {
            let close = planner.update(coverage: map, camera: Self.homeowner(out: 1), time: time)
            #expect(close.task == .aimAtGround(s: 0), "at \(time) s")
            #expect(close.stepBack, "at \(time) s")
            #expect(close.switched == nil, "at \(time) s")
        }
    }

    @Test func tooCloseAsksToStepBack() {
        var planner = GuidancePlanner()
        let map = CoverageMap(wall: standardWall())
        // 1.0 m out < tooClose 1.2.
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1.0), time: 0).task == .stepBack)
    }

    @Test func laggingGroundAsksToAimDown() {
        var planner = GuidancePlanner()
        // The walk goes left, so the window runs 1 m ahead that way and 0.3 m back: from s = 0.5,
        // [-0.5, 0.8], cells -4 ... 5. Cells -4, -3 and 2 ... 5 have the wall covered and the
        // ground unseen; -2 ... 1 are skipped, so done, and split them into two runs (#129).
        // Only 2 ... 5 is at least ceil(0.45 / 0.1524) = 3 cells: its middle is
        // (0.3048 + 0.9144) / 2 = 0.6096. The middle of the first and last lagging cells,
        // (-0.6096 + 0.9144) / 2 = 0.1524, lies on the done ground between the runs.
        let map = Self.wallOnlyCoverage()
        let output = planner.update(coverage: map, camera: Self.homeowner(x: 0.5), time: 0)
        guard case .aimAtGround(let s) = output.task else {
            Issue.record("expected aimAtGround, got \(output.task)")
            return
        }
        #expect(nearlyEqual(s, 0.6096))
        // Every cell of the stretch asked for is still missing ground.
        for index in map.indices(overlapping: (s - GuidancePlanner.aimHalfWidth)...(s + GuidancePlanner.aimHalfWidth)) {
            #expect(map.level(.ground, index) != .covered && map.level(.ground, index) != .skipped, "cell \(index)")
        }
        // Target: the middle of the ground band there, (0.6096, 0, 0.6).
        #expect(nearlyEqual(output.target ?? .zero, SIMD3(0.6096, 0, 0.6)))
    }

    /// A run of exactly three missing ground cells between two skipped ones is never asked for
    /// (review of #154). Wall cameras from s = 1.8 to 2.7 cover the wall around cells 12 ... 18;
    /// the ground is skipped over cells 6 ... 12 and 18 ... 23, so from s = 2.4 (window [1.4, 2.7],
    /// cells 9 ... 17) the ground lags over 13 ... 17 only, and the aim is at its middle,
    /// 15.5 * 0.1524 = 2.3622. With 13 and 17 skipped too the run is 14 ... 16, whose middle is
    /// the same, but the stretch there, [2.0622, 2.6622], still spans 13 ... 17: covering all
    /// three cells reaches only 3/5 < 0.8, so the aim could never be met and is not asked for.
    @Test func threeMissingCellsBetweenSkippedOnesAreNotAskedFor() {
        let width = CoverageConfig().cellWidth
        var map = Self.wallOnlyCoverage()
        for s: Float in [1.8, 2.1, 2.4, 2.7] {
            map.observe(wallCamera(s: s), trackingNormal: true)
        }
        map.markSkipped(.ground, 1.0...(12.5 * width))
        map.markSkipped(.ground, (18.5 * width)...3.6)
        var open = GuidancePlanner()
        let aim = open.update(coverage: map, camera: Self.homeowner(x: 2.4), time: 0).task
        guard case .aimAtGround(let s) = aim, nearlyEqual(s, 15.5 * width) else {
            Issue.record("expected aimAtGround at 2.3622, got \(aim)")
            return
        }

        map.markSkipped(.ground, (13.5 * width)...(13.5 * width))
        map.markSkipped(.ground, (17.5 * width)...(17.5 * width))
        var planner = GuidancePlanner()
        let task = planner.update(coverage: map, camera: Self.homeowner(x: 2.4), time: 0).task
        if case .aimAtGround = task {
            Issue.record("expected no aim at the ground, got \(task)")
        }
    }

    /// A task other than an aim task is held for 3 s, then gives way. Changed deliberately for
    /// #84: an unmet aim task whose stretch is still in view is not released at 3 s. On build 4.1
    /// the 3 s dwell was also the longest any card stayed, and the card changed 22 times in 86 s.
    @Test func hysteresisHoldsATaskForThreeSeconds() {
        var planner = GuidancePlanner()
        let map = Self.meterGroundSkipped()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .walk(.left))
        // Preferred is stepBack from here on, but walking left isn't satisfied and 3 s haven't passed.
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 1).task == .walk(.left))
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 2.9).task == .walk(.left))
        let switched = planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 3)
        #expect(switched.task == .stepBack)
        #expect(switched.switched == .dwell)
        #expect(planner.current == .stepBack)

        // The aim task of `laggingGroundAsksToAimDown`, s = 0.6096. From s = 0 the walk left is
        // preferred (the window [-1, 0.3] holds two lagging cells), but the stretch is 0.61 m
        // away, inside the 1 m it is held within.
        var aiming = GuidancePlanner()
        let lagging = Self.wallOnlyCoverage()
        let aim = aiming.update(coverage: lagging, camera: Self.homeowner(x: 0.5), time: 0).task
        guard case .aimAtGround(let s) = aim, nearlyEqual(s, 0.6096) else {
            Issue.record("expected aimAtGround at 0.6096, got \(aim)")
            return
        }
        for time in [3.0, 6, 12] {
            #expect(aiming.update(coverage: lagging, camera: Self.homeowner(x: 0), time: time).task == aim, "at \(time) s")
        }
    }

    @Test func satisfiedTaskSwitchesAtOnce() {
        var planner = GuidancePlanner()
        var map = Self.meterGroundSkipped()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .walk(.left))
        map.setEnd(.left, at: -3)
        // The left end is marked: walk(.left) is satisfied, and the right side comes next.
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0.5).task == .walk(.right))
    }

    @Test func walkTargetIsOnTheWallToTheLeft() throws {
        let planner = GuidancePlanner()
        // Reach 0, so the target is s = -(0 + 1) = -1 at height 1: world (-1, 1, 0).
        let output = planner.cues(for: .walk(.left), coverage: CoverageMap(wall: standardWall()), camera: Self.homeowner())
        let target = try #require(output.target)
        #expect(nearlyEqual(target, SIMD3(-1, 1, 0)))
        let point = standardWall().wallPoint(target)
        #expect(point.s < 0 && nearlyEqual(point.out, 0))
    }

    /// Reach stops at the first hole, so a homeowner can be past it. The walk right must then aim
    /// ahead of them: the target to their right on screen (camera +y is screen right in portrait)
    /// and the path heading toward +s.
    @Test func walkTargetLeadsAHomeownerAlreadyPastTheReach() throws {
        var planner = GuidancePlanner()
        var map = Self.meterGroundSkipped()
        map.setEnd(.left, at: -3)
        let camera = Self.homeowner(x: 3)
        let output = planner.update(coverage: map, camera: camera, time: 0)
        #expect(output.task == .walk(.right))
        let target = try #require(output.target)
        // Reach is 0 here; the old target was s = 1, 2 m behind the camera at s = 3.
        #expect(nearlyEqual(target, SIMD3(4, 1, 0)))
        #expect(camera.cameraSpace(target).y > 0)
        let first = try #require(output.path.first)
        let last = try #require(output.path.last)
        #expect(standardWall().wallPoint(last).s > standardWall().wallPoint(first).s)

        let left = planner.cues(for: .walk(.left), coverage: CoverageMap(wall: standardWall()), camera: Self.homeowner(x: -2))
        #expect(nearlyEqual(left.target ?? .zero, SIMD3(-3, 1, 0)))
        #expect(Self.homeowner(x: -2).cameraSpace(left.target ?? .zero).y < 0)
    }

    @Test func pathRunsAlongTheStandOffLine() {
        let planner = GuidancePlanner()
        let wall = standardWall()
        // From s = 0 to the goal s = -1: ceil(1 / 0.5) = 2 steps, points at s = 0, -0.5, -1.
        let path = planner.cues(for: .walk(.left), coverage: CoverageMap(wall: wall), camera: Self.homeowner()).path
        #expect(path.count == 3)
        for (point, s) in zip(path, [Float(0), -0.5, -1]) {
            #expect(nearlyEqual(point, SIMD3(s, 0, GuidanceConfig().standOff)))
        }
    }

    @Test func pathIsAtMostThreeMetres() throws {
        var planner = GuidancePlanner()
        let wall = standardWall()
        // Standing at s = 4 with the goal at s = -1: clipped to 3 m, ending at s = 1, in 6 steps.
        let path = planner.update(coverage: Self.meterGroundSkipped(CoverageMap(wall: wall)), camera: Self.homeowner(x: 4), time: 0).path
        #expect(path.count == 7)
        for point in path {
            let p = wall.wallPoint(point)
            #expect(nearlyEqual(p.out, GuidanceConfig().standOff) && nearlyEqual(p.height, 0))
        }
        let first = try #require(path.first)
        let last = try #require(path.last)
        #expect(nearlyEqual(wall.wallPoint(first).s, 4))
        #expect(nearlyEqual(wall.wallPoint(last).s, 1))
        #expect(simd_distance(first, last) <= 3 + 1e-4)
    }

    /// Checklist I6: instructions never go A, B, A within 3 s. The camera alternates every 0.5 s
    /// between s = 0.5 (preferred: aimAtGround at 0.6096, as in `laggingGroundAsksToAimDown`) and
    /// s = 10 (no lag there, preferred: walk(.left)); neither task can be satisfied, since the map
    /// never changes. Hand trace with minDwell 3: aim from 0; at 3.0 the preference is aim again;
    /// at 3.5 it is walk, 3.5 s have passed and the aim's stretch is 9.4 m from the camera, so
    /// walk; then aim again at 7.0 (3.5 s later). Switches at exactly 3.5 and 7.0. Changed
    /// deliberately from s = 0: the lagging band is now looked for only ahead of the walk, and
    /// from s = 0 walking left it lags over two cells, too few to ask.
    @Test func neverFlipsBackWithinThreeSeconds() {
        var planner = GuidancePlanner()
        let map = Self.wallOnlyCoverage()
        var history: [(time: Double, task: GuidanceTask)] = []
        for step in 0...20 {
            let time = Double(step) * 0.5
            let camera = step.isMultiple(of: 2) ? Self.homeowner(x: 0.5) : Self.homeowner(x: 10)
            history.append((time, planner.update(coverage: map, camera: camera, time: time).task))
        }
        let switches = zip(history, history.dropFirst()).filter { $0.task != $1.task }.map(\.1.time)
        #expect(switches == [3.5, 7.0])
        for i in history.indices {
            for j in history.indices where j > i {
                for k in history.indices where k > j && history[k].time - history[i].time < 3 {
                    let flipped = history[i].task == history[k].task && history[j].task != history[i].task
                    #expect(!flipped, "A, B, A at \(history[i].time), \(history[j].time), \(history[k].time)")
                }
            }
        }
    }

    /// LiDAR saw a box in front of the wall (CoverageDepthTests' box, s in [-0.5, 0.5], 0.5 to
    /// 1 m out): from `wallCamera` at s = 0 and 0.3, row 0 of every wall cell they see from -6
    /// to 5 ([-0.9144, 0.9144]) is hidden in both views, so all twelve are hidden, more than the
    /// three `lagRun` needs, and their middle is s = 0. (The ground cells there are hidden too:
    /// their row at the wall's foot is the wall's row 0, which these views see at u = 620, just
    /// inside the image margin.) Seeing behind comes before aiming.
    /// Walking off to s = 5 prefers the walk left (the ground in front of the meter, hidden and
    /// so not done, is asked for only within 1 m of the meter), which waits out the 3 s dwell;
    /// once the cells are no longer hidden the task is satisfied and switches at once.
    @Test func hiddenCellsNearTheCameraAskToSeeBehind() {
        var map = CoverageMap(wall: standardWall())
        let scene = CoverageDepthTests.boxScene
        for s: Float in [0, 0.3] {
            map.observe(wallCamera(s: s), trackingNormal: true, depth: renderDepth(scene, from: wallCamera(s: s)))
        }
        var planner = GuidancePlanner()
        let output = planner.update(coverage: map, camera: Self.homeowner(), time: 0)
        guard case .seeBehind(let s) = output.task else {
            Issue.record("expected seeBehind, got \(output.task)")
            return
        }
        #expect(nearlyEqual(s, 0))
        #expect(nearlyEqual(output.target ?? SIMD3(repeating: 9), SIMD3(0, 0, 0)))
        #expect(output.path.isEmpty)

        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 5), time: 2.9).task == .seeBehind(s: s))
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 5), time: 3).task == .walk(.left))

        // Satisfied: nothing within 0.3 m of s is hidden once the homeowner marks it skipped.
        var planner2 = GuidancePlanner()
        #expect(planner2.update(coverage: map, camera: Self.homeowner(), time: 0).task == .seeBehind(s: s))
        map.markSkipped(.wall, -1...1)
        map.markSkipped(.ground, -1...1)
        #expect(planner2.update(coverage: map, camera: Self.homeowner(), time: 0.5).task != .seeBehind(s: s))
    }
    /// A camera at s = x, 2.6 m out, looking away from the wall: kept, so its position counts as
    /// walked, but it sees no cell, so it adds no coverage (and no lag) anywhere.
    static func walkedAway(x: Float) -> CameraFrame {
        portraitCamera(at: SIMD3(x, 1.4, 2.6), forward: SIMD3(0, 0, 1))
    }

    /// B-06: the end prompt goes by how far the walked path has gone on the side, not by unbroken
    /// coverage. Here nothing is covered past the meter (reach 0), yet a walk to 6.2 m left asks
    /// for the left end, and one to 5.9 m doesn't yet. The prompt's aim is the reticle: no ring.
    @Test func endPromptFollowsTheWalkedPath() {
        var map = Self.meterGroundSkipped()
        for x in stride(from: Float(0), through: -5.9, by: -0.5) { map.observe(Self.walkedAway(x: x), trackingNormal: true) }
        map.observe(Self.walkedAway(x: -5.9), trackingNormal: true)
        var short = GuidancePlanner()
        #expect(nearlyEqual(map.walkedFarthest(.left), 5.9))
        #expect(short.update(coverage: map, camera: Self.homeowner(x: -5.9), time: 0).task == .walk(.left))
        map.observe(Self.walkedAway(x: -6.2), trackingNormal: true)
        // Unbroken coverage reaches only over the skipped cells in front of the meter.
        #expect(nearlyEqual(GuidancePlanner().reach(.left, coverage: map), 0.3048))
        var far = GuidancePlanner()
        let output = far.update(coverage: map, camera: Self.homeowner(x: -6.2), time: 0)
        #expect(output.task == .markEnd(.left))
        #expect(output.target == nil)
        #expect(output.path.isEmpty)
    }

    /// The lag window slides with the camera. When the homeowner steps from s = 0.5 to 0 the
    /// window no longer holds enough lagging cells and the walk is preferred, but the task's
    /// stretch is still in view: the card keeps its distance past the dwell instead of changing
    /// it. Out of view (s = 10) it gives way.
    @Test func aimTaskKeepsItsStretchWhileTheWindowDrifts() {
        var planner = GuidancePlanner()
        let map = Self.wallOnlyCoverage()
        let first = planner.update(coverage: map, camera: Self.homeowner(x: 0.5), time: 0).task
        guard case .aimAtGround(let s) = first, nearlyEqual(s, 0.6096) else {
            Issue.record("expected aimAtGround at 0.6096, got \(first)")
            return
        }
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 0), time: 3.5).task == first)
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 10), time: 3.6).task == .walk(.left))
    }

    /// Issue #38: cells seen before an end was set stay in the map, but past the end nothing is
    /// observed or skipped, so a task there could never fill and "Can't get there" didn't clear
    /// it. In `wallOnlyCoverage` the ground lags over cells -4, -3 and 2 ... 5, all in the window
    /// seen from s = 0.5 (`laggingGroundAsksToAimDown`); a right end at s = 0.3 leaves only -4 and
    /// -3 between the ends, fewer than `lagRun`'s three, so the walk goes left instead of asking to
    /// tilt down at s = 0.6096. Likewise the box's twelve hidden cells
    /// (`hiddenCellsNearTheCameraAskToSeeBehind`) with the ends at s = -0.1 and 0.1: two lie
    /// between them, too few to ask to see behind.
    @Test func noCameraTaskPastAMarkedEnd() {
        var lagging = Self.wallOnlyCoverage()
        lagging.setEnd(.right, at: 0.3)
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: lagging, camera: Self.homeowner(x: 0.5), time: 0).task == .walk(.left))

        var hidden = CoverageMap(wall: standardWall())
        let scene = CoverageDepthTests.boxScene
        for s: Float in [0, 0.3] {
            hidden.observe(wallCamera(s: s), trackingNormal: true, depth: renderDepth(scene, from: wallCamera(s: s)))
        }
        hidden.setEnd(.left, at: -0.1)
        hidden.setEnd(.right, at: 0.1)
        var planner2 = GuidancePlanner()
        let task = planner2.update(coverage: hidden, camera: Self.homeowner(), time: 0).task
        if case .seeBehind = task { Issue.record("asked to see behind cells past the ends: \(task)") }
    }

    // MARK: Build 4.1 field test (#84, #77)

    /// #84 and #77: an unmet aim task stays while it gains, and stalls once it has gained nothing
    /// for `stallTimeout` (20 s). The ground by the meter is cells -2 ... 1. Views straight down
    /// from s = -0.45 and -0.15 (`groundCamera`, 0.3 m apart) cover cells -4 ... -1, half of it,
    /// at 8 s; nothing more comes, and the request stalls 20 s after that, at 28 s. It is deferred,
    /// so the walk moves on (here to the wall beside those ground views, which lags the ground),
    /// and it is asked for again after a reset.
    @Test func anUnmetAimIsHeldWhileItGainsAndDeferredWhenItStalls() {
        var map = CoverageMap(wall: standardWall())
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .aimAtGround(s: 0))
        map.observe(groundCamera(s: -0.45), trackingNormal: true)
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 4).task == .aimAtGround(s: 0))
        map.observe(groundCamera(s: -0.15), trackingNormal: true)
        #expect(nearlyEqual(Float(map.coveredFraction(.ground, in: -0.3...0.3)), 0.5))
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 8).task == .aimAtGround(s: 0))
        let held = planner.update(coverage: map, camera: Self.homeowner(), time: 27.9)
        #expect(held.task == .aimAtGround(s: 0))
        #expect(held.switched == nil && held.stalled == nil)
        let stalled = planner.update(coverage: map, camera: Self.homeowner(), time: 28)
        #expect(stalled.task != .aimAtGround(s: 0))
        #expect(stalled.switched == .stalled)
        #expect(stalled.stalled == .aimAtGround(s: 0))
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 40).task != .aimAtGround(s: 0))
        planner.reset()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 41).task == .aimAtGround(s: 0))
    }

    /// #56: answering something else doesn't bring back a request that stalled. The ground by the
    /// meter stalls and is deferred (as in the test above); the camera stays near the meter, and
    /// the left end is marked at s = -0.6 and answered blocked while the right stays open. The
    /// engine then settles the planner's task (`ScanEngine.resetGuidanceAfterSkip`): the deferred
    /// ground stays deferred, rather than coming back with a fresh 20 s clock. The same planner
    /// reset for a new scan (or a spatial reset) forgets it and asks again.
    @Test func settlingATaskKeepsWhatStalledButANewScanForgetsIt() {
        var map = CoverageMap(wall: standardWall())
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: Self.homeowner(), time: 0).task == .aimAtGround(s: 0))
        map.observe(groundCamera(s: -0.45), trackingNormal: true)
        map.observe(groundCamera(s: -0.15), trackingNormal: true)
        #expect(planner.update(coverage: map, camera: Self.homeowner(out: 1), time: 8).task == .aimAtGround(s: 0))
        let stalled = planner.update(coverage: map, camera: Self.homeowner(), time: 28)
        #expect(stalled.stalled == .aimAtGround(s: 0))
        #expect(stalled.task != .aimAtGround(s: 0))

        map.setEnd(.left, at: -0.6)
        map.setEndIsLimit(.left, true)
        var newScan = planner

        planner.settleCurrentTask()
        for time in [28.5, 29.0, 40.0, 60.0] {
            #expect(planner.update(coverage: map, camera: Self.homeowner(), time: time).task != .aimAtGround(s: 0), "settled at \(time) s")
        }

        newScan.reset()
        #expect(newScan.update(coverage: map, camera: Self.homeowner(), time: 28.5).task == .aimAtGround(s: 0))
    }

    /// Once the middle stretch stalls, the remaining three-cell runs have aim windows
    /// overlapping it. The walk continues until another accessible stretch is available.
    @Test func aStalledLaggingBandIsNotAskedForAgainAtOnce() {
        var map = Self.meterGroundSkipped()
        map.setEnd(.left, at: -0.5)
        map.markSkipped(.wall, 1...4)
        let camera = Self.homeowner(x: 2)
        var planner = GuidancePlanner()
        let first = planner.update(coverage: map, camera: camera, time: 0)
        guard case .aimAtGround(let target) = first.task else {
            Issue.record("expected ground aim")
            return
        }
        #expect(nearlyEqual(target, 2.3622))
        #expect(planner.update(coverage: map, camera: camera, time: 19.9).task == first.task)
        let stalled = planner.update(coverage: map, camera: camera, time: 20)
        #expect(stalled.stalled == first.task)
        #expect(stalled.switched == .stalled)
        #expect(stalled.task == .walk(.right))
        #expect(planner.update(coverage: map, camera: camera, time: 40).task == .walk(.right))
    }

    @Test func anActionableAimStillStallsWithoutCoverageProgress() {
        let map = CoverageMap(wall: standardWall())
        let camera = Self.homeowner()
        var planner = GuidancePlanner()
        let first = planner.update(coverage: map, camera: camera, time: 0, aimActionable: true)
        #expect(first.task == .aimAtGround(s: 0))
        for time in [5.0, 10, 15, 19.9] {
            #expect(planner.update(coverage: map, camera: camera, time: time, aimActionable: true).stalled == nil)
        }
        #expect(planner.update(coverage: map, camera: camera, time: 20, aimActionable: true).stalled == first.task)
    }

    @Test func hiddenAimTimeDoesNotConsumeTheStallBudget() {
        let map = CoverageMap(wall: standardWall())
        let camera = Self.homeowner()
        var planner = GuidancePlanner()
        let first = planner.update(coverage: map, camera: camera, time: 0)
        #expect(first.task == .aimAtGround(s: 0))
        #expect(planner.update(coverage: map, camera: camera, time: 5, aimActionable: false).task == first.task)
        #expect(planner.update(coverage: map, camera: camera, time: 35, aimActionable: false).stalled == nil)
        #expect(planner.update(coverage: map, camera: camera, time: 40).task == first.task)
        #expect(planner.update(coverage: map, camera: camera, time: 54.9).stalled == nil)
        #expect(planner.update(coverage: map, camera: camera, time: 55).stalled == first.task)
    }

    @Test func aReplacementAimWindowDoesNotOverlapADeferredStretch() {
        var map = Self.meterGroundSkipped()
        map.setEnd(.left, at: -0.5)
        map.markSkipped(.wall, 1...4)
        let camera = Self.homeowner(x: 2)
        var planner = GuidancePlanner()
        guard case .aimAtGround(let first) = planner.update(coverage: map, camera: camera, time: 0).task else {
            Issue.record("expected ground aim")
            return
        }
        let next = planner.update(coverage: map, camera: camera, time: 20)
        if case .aimAtGround(let replacement) = next.task {
            #expect(abs(replacement - first) >= 2 * GuidancePlanner.aimHalfWidth)
        } else {
            #expect(next.task == .walk(.right))
        }
    }

    /// #77: the ground in front of the meter is asked for only while the phone is within 1 m of
    /// the meter. Farther away the walk goes on, and once the phone leaves that metre the request
    /// gives way after its dwell.
    @Test func theGroundByTheMeterIsAskedForOnlyNearTheMeter() {
        let map = CoverageMap(wall: standardWall())
        for x: Float in [-0.8, 0, 0.8] {
            var planner = GuidancePlanner()
            #expect(planner.update(coverage: map, camera: Self.homeowner(x: x), time: 0).task == .aimAtGround(s: 0), "at s = \(x)")
        }
        for x: Float in [-1.2, 1.1, 1.5, 6] {
            var planner = GuidancePlanner()
            #expect(planner.update(coverage: map, camera: Self.homeowner(x: x), time: 0).task == .walk(.left), "at s = \(x)")
        }
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 0.8), time: 0).task == .aimAtGround(s: 0))
        #expect(planner.update(coverage: map, camera: Self.homeowner(x: 1.5), time: 2.9).task == .aimAtGround(s: 0))
        let left = planner.update(coverage: map, camera: Self.homeowner(x: 1.5), time: 3)
        #expect(left.task == .walk(.left))
        #expect(left.switched == .leftWindow)
    }

    /// #77: once both ends are marked, the ground by the meter comes back wherever the phone is,
    /// as the first stretch between the ends that isn't done (`firstHole`). Everything from -2 to
    /// 2 is skipped but the ground cells -2 ... 1; from s = 1.8 the walk asks for them only once
    /// both ends are marked.
    @Test func theGroundByTheMeterComesBackOnceBothEndsAreMarked() throws {
        var map = CoverageMap(wall: standardWall())
        for band in SurfaceBand.allCases {
            map.markSkipped(band, -2...(-0.35))
            map.markSkipped(band, 0.35...2)
        }
        map.markSkipped(.wall, -0.35...0.35)
        for index in -2...1 { #expect(map.level(.ground, index) == .unseen, "cell \(index)") }
        var noEnds = GuidancePlanner()
        #expect(noEnds.update(coverage: map, camera: Self.homeowner(x: 1.8), time: 0).task == .walk(.left))
        map.setEnd(.left, at: -2)
        var oneEnd = GuidancePlanner()
        #expect(oneEnd.update(coverage: map, camera: Self.homeowner(x: 1.8), time: 0).task == .walk(.right))
        map.setEnd(.right, at: 2)
        var bothEnds = GuidancePlanner()
        let output = bothEnds.update(coverage: map, camera: Self.homeowner(x: 1.8), time: 0)
        #expect(output.task == .aimAtGround(s: 0))
        #expect(nearlyEqual(try #require(output.target), SIMD3(0, 0, 0.6)))
    }

    /// #84: aim targets landed up to about 3 ft behind the phone, and the card changed every 3 s.
    /// A homeowner walks right (the left end marked, as on run 2) 0.6 m every 2 s, the phone
    /// glancing between 5 and 35 degrees down, so each view leaves some ground beside the phone
    /// short of a second position. That ground lies behind the phone by the next step: the walk
    /// no longer turns back for it (those stretches come back once both ends are marked), and no
    /// aim task it asks for lies more than 0.3 m behind the phone. Before, the window reached 1 m
    /// back, and each of five cards asked for ground about 0.5 m behind the phone.
    @Test func aWalkNeverAimsBehindThePhone() {
        var map = Self.meterGroundSkipped()
        map.setEnd(.left, at: -0.5)
        var planner = GuidancePlanner()
        var tasks: [GuidanceTask] = []
        for step in 0...10 {
            let x = Float(step) * 0.6
            let camera = portraitCamera(at: SIMD3(x, 1.4, 2.6), forward: forwardFacingWall(pitchedDown: step.isMultiple(of: 2) ? 5 : 35))
            map.observe(camera, trackingNormal: true)
            let task = planner.update(coverage: map, camera: camera, time: Double(step) * 2).task
            guard tasks.last != task else { continue }
            tasks.append(task)
            switch task {
            case .aimAtGround(let s), .aimAtWall(let s):
                #expect(s >= x - 0.3, "\(task) with the phone at s = \(x)")
            default:
                break
            }
        }
        #expect(tasks.count <= 4, "\(tasks)")
    }

    /// #77: the ground by the meter seen twice from one spot has every row seen once, and a second
    /// look from there (within `coveringBaseline`, 0.25 m) adds nothing: the planner says a step to
    /// the side is needed. From 0.3 m away it isn't, and a view from there covers the stretch.
    @Test func aStretchSeenFromOneSpotNeedsASecondPosition() {
        var map = CoverageMap(wall: standardWall())
        #expect(!map.needsSecondPosition(band: .ground, range: -0.3...0.3, from: CoverageMapTests.front))
        map.observe(CoverageMapTests.frontCamera(), trackingNormal: true)
        map.observe(CoverageMapTests.frontCamera(), trackingNormal: true)
        #expect(map.needsSecondPosition(band: .ground, range: -0.3...0.3, from: CoverageMapTests.front))
        #expect(map.needsSecondPosition(band: .ground, range: -0.3...0.3, from: CoverageMapTests.frontCamera(x: 0.2).position))
        #expect(!map.needsSecondPosition(band: .ground, range: -0.3...0.3, from: CoverageMapTests.frontCamera(x: 0.3).position))
        var planner = GuidancePlanner()
        let output = planner.update(coverage: map, camera: Self.homeowner(), time: 0)
        #expect(output.task == .aimAtGround(s: 0))
        #expect(output.needsSecondPosition)
        map.observe(CoverageMapTests.frontCamera(x: 0.3), trackingNormal: true)
        #expect(!map.needsSecondPosition(band: .ground, range: -0.3...0.3, from: CoverageMapTests.frontCamera(x: 0.3).position))
        #expect(map.coveredFraction(.ground, in: -0.3...0.3) == 1)
    }
}
