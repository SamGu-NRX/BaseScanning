@testable import HouseScanKit
import simd
import Testing

// A rebuild that reuses kept views (`CoverageMap.rebuildViews`) must leave the map exactly as one
// that works every view out again. Each test runs the same changes on two copies of a map, one
// with `reusesRebuildViews` off, and after every change compares their whole stored state
// (`stateDifferences`), what the public readers return, and both again after depthless views
// taken on top: the order a row's sightings were kept in decides what a later depthless view adds.
@Suite struct RebuildViewCacheTests {
    /// The far wall of the corridor scenes, 2 m out (`FarSurfaceTests.farWall`).
    static let scene = standardScene(boxes: [FarSurfaceTests.farWall(at: 2.0)])

    /// Frames with depth: the corridor cameras facing the far wall, oblique ones looking along the
    /// corridor both ways, wall cameras and a ground camera.
    static let depthFrames: [(camera: CameraFrame, depth: DepthImage)] = {
        let pitch = Float.pi / 6
        var cameras = FarSurfaceTests.corridorCameras
        for (s, yaw) in [(Float(-1), Float(0.6)), (0.5, -0.6), (1.5, 0.6), (2.5, -0.6)] {
            cameras.append(portraitCamera(at: SIMD3(s, 1.4, 0.8), forward: SIMD3(sin(yaw) * cos(pitch), -sin(pitch), cos(yaw) * cos(pitch))))
        }
        cameras += [wallCamera(s: -0.5), wallCamera(s: 0.4), wallCamera(s: 1.2), groundCamera(s: 0.2)]
        return cameras.map { ($0, renderDepth(RebuildViewCacheTests.scene, from: $0)) }
    }()

    /// Frames without depth.
    static let depthlessFrames = [wallCamera(s: 0.1), groundCamera(s: -0.3), wallCamera(s: 0.9)]

    /// Far surfaces the lookup moves between, over s -3...4 m.
    static let surfaces: [String: [ObservedSpan]] = {
        let wall = standardWall()
        func spans(_ planes: [WallPlaneEvidence]) -> [ObservedSpan] { FarSurface.spans(planes: planes, wall: wall, over: -3...4) }
        return [
            "none": [],
            "far wall": spans([FarSurfaceTests.plane(at: 2.0)]),
            // 4 mm nearer, still in the same 0.1 ft step (65.6 steps at 2 m): the same spans, so
            // no rebuild.
            "far wall, jittered within its step": spans([FarSurfaceTests.plane(at: 1.996)]),
            // Across the next step down (2.0 to 1.9812 m): every cell's value changes.
            "far wall, one step nearer": spans([FarSurfaceTests.plane(at: 1.97)]),
            // A nearer plane over part of the stretch: the overlap's minimum changes there only.
            "far wall with a nearer plane": spans([FarSurfaceTests.plane(at: 2.0), FarSurfaceTests.plane(at: 1.4, from: 0, to: 1.5)]),
            "far wall over part of the stretch": spans([FarSurfaceTests.plane(at: 2.0, from: -1, to: 0.6)]),
            // Only where no camera looks: against the far wall, the wall's cells go absent.
            "far away": [ObservedSpan(span: 50...51, out: 2)],
            // The far wall and a surface where no camera looks: from the far wall alone this
            // changes the lookup, and so rebuilds, at no cell a frame consulted.
            "far wall and far away": spans([FarSurfaceTests.plane(at: 2.0)]) + [ObservedSpan(span: 50...51, out: 2)],
        ]
    }()

    /// What the public readers return, over cells -40...40 and 30 cells past each end.
    struct Readout: Equatable {
        var revision: Int
        var levels: [[CoverageLevel]]
        var groundDepths: [Float?]
        var hiddenDepthRows: [Set<Int>]
        var wallHeights: [Float?]
        var walked: [Float?]
        var pastLimits: [Float?]
        var spans: [[ObservedSpan]]
        var seen: ClosedRange<Float>?
        var viewed: ClosedRange<Float>?

        init(_ map: CoverageMap) {
            let cells = -40...40
            revision = map.revision
            levels = SurfaceBand.allCases.map { band in cells.map { map.level(band, $0) } }
            groundDepths = cells.map(map.groundDepth(at:))
            hiddenDepthRows = cells.map(map.groundDepthHiddenRows(at:))
            wallHeights = cells.map(map.wallSeenHeight(at:))
            walked = cells.map(map.walkedClearance(at:))
            pastLimits = WalkSide.allCases.flatMap { side in (0..<30).map { map.groundDepthPastLimit(side, $0) } }
            spans = [map.wallSeenSpans(), map.groundDepthSpans(), map.facingSpans(), map.overheadSpans()]
            seen = map.seenExtent
            viewed = map.viewedExtent
        }
    }

    /// A map reusing kept views and one working them all out, changed in step.
    struct Twin {
        var cached: CoverageMap
        var full: CoverageMap

        init(_ map: CoverageMap = CoverageMap(wall: standardWall())) {
            cached = map
            full = map
            full.reusesRebuildViews = false
        }

        mutating func step(_ name: String, _ change: (inout CoverageMap) throws -> Void) rethrows {
            try change(&cached)
            try change(&full)
            #expect(cached.stateDifferences(from: full) == [], "state after \(name)")
            #expect(Readout(cached) == Readout(full), "readers after \(name)")
            var later = (cached, full)
            for (index, camera) in RebuildViewCacheTests.depthlessFrames.enumerated() {
                later.0.observe(camera, trackingNormal: true, time: 1000 + Double(index))
                later.1.observe(camera, trackingNormal: true, time: 1000 + Double(index))
            }
            #expect(later.0.stateDifferences(from: later.1) == [], "depthless views after \(name)")
            #expect(full.rebuildViewCounts.reused == 0)
        }

        mutating func observeAll(times: [Double]) {
            for (index, frame) in RebuildViewCacheTests.depthFrames.enumerated() {
                step("observing frame \(index)") { $0.observe(frame.camera, trackingNormal: true, time: times[index], depth: frame.depth) }
            }
        }

        mutating func setSurface(_ name: String) {
            let spans = RebuildViewCacheTests.surfaces[name]!
            step("far surface \(name)") { $0.setFarSurface(spans) }
        }
    }

    /// Store-completion order: the frames are observed in an order other than their times'.
    static func shuffledTimes() -> [Double] {
        depthFrames.indices.map { Double(($0 * 7) % depthFrames.count) }
    }

    @Test func farSurfaceTransitionsMatchAFullRebuild() {
        var twin = Twin()
        twin.observeAll(times: Self.shuffledTimes())
        for name in [
            "far wall", "far wall and far away", "far wall, jittered within its step", "far wall, one step nearer",
            "far wall with a nearer plane", "far wall over part of the stretch", "far away", "none", "far wall", "none",
        ] {
            twin.setSurface(name)
        }
        twin.step("a depthless frame") { $0.observe(Self.depthlessFrames[0], trackingNormal: true, time: 50) }
        twin.setSurface("far wall")
    }

    @Test func wallMovesShiftSkippedAndWithdrawnCellsAsAFullRebuild() {
        var twin = Twin()
        twin.observeAll(times: Self.shuffledTimes())
        twin.setSurface("far wall")
        twin.step("skipping a cell") { $0.markSkipped(.wall, $0.cellRange(5)) }
        twin.step("withdrawing a cell") { $0.withdrawClaims(over: $0.cellRange(2)) }
        twin.setSurface("far wall, one step nearer")
        let width = CoverageConfig().cellWidth
        // A whole cell, then two moves of 0.4 cells: the second carries `pendingShift` over a
        // whole cell.
        for (index, shift) in [width, 0.4 * width, 0.4 * width].enumerated() {
            twin.step("meter move \(index)") { map in
                var moved = map.wall
                moved.meter.x -= shift
                map.updateWall(moved)
            }
            twin.setSurface(index % 2 == 0 ? "far wall" : "far wall with a nearer plane")
        }
        twin.step("a measured ground") { map in
            var moved = map.wall
            moved.groundY += 0.02
            map.updateWall(moved)
        }
        twin.setSurface("far away")
    }

    @Test func limitsCornerCorrectionAndGroundMatchAFullRebuild() throws {
        var twin = Twin()
        twin.observeAll(times: Self.depthFrames.indices.map(Double.init))
        twin.setSurface("far wall")
        twin.step("right end") { $0.setEnd(.right, at: 0.6) }
        twin.step("right end a limit") { $0.setEndIsLimit(.right, true) }
        twin.setSurface("far wall, one step nearer")
        twin.setSurface("far away")
        twin.step("left end a limit") { map in
            map.setEnd(.left, at: -1.2)
            map.setEndIsLimit(.left, true)
        }
        twin.setSurface("far wall")
        try twin.step("corner after the limit end") { map in
            _ = try map.turnCorner(.right, meeting: SIMD3(0.6, 0, -2), outward: SIMD3(1, 0, 0), source: .plane)
        }
        twin.setSurface("far wall over part of the stretch")
        twin.step("anchor correction") { $0.apply(YawCorrection(yaw: 0.005, translation: SIMD3(0.01, 0, 0.004))) }
        twin.setSurface("far wall")
        twin.step("guessed ground") { $0.heightError = 0.3 }
        twin.setSurface("far wall, jittered within its step")
        twin.step("measured ground") { $0.heightError = 0 }
        twin.step("wall line source") { $0.setWallLineSource(.plane) }
        twin.step("overhead view") { _ = $0.recordOverhead(portraitCamera(at: SIMD3(0, 1.4, 2.6), forward: SIMD3(0, 0.5, -1)), trackingNormal: true) }
        twin.step("walked path break") { $0.breakWalkedPath() }
        twin.step("clearing the left end") { $0.clearEnd(.left) }
        twin.setSurface("none")
    }

    /// A frame's views depend on the lookup where its depth readings met something, not only where
    /// its samples lie. From 3 m out, beyond a 1.1 m fence 2 m out, a phone turned along the wall
    /// sees the ground behind the fence blocked by it. A sample there lies short of the fence's
    /// surface, so whether it is past the space turns on the lookup at the cell where the reading
    /// met the fence, nearer the phone along the wall. Cutting the fence's surface back over those
    /// cells only must work the views out again. A cache keyed on the samples' cells alone keeps
    /// the old answer.
    @Test func viewsFollowTheLookupWhereReadingsMetTheFence() {
        let scene = standardScene(boxes: [(SIMD3(-10, 0, 2.0), SIMD3(10, 1.1, 2.1))])
        let pitch: Float = 25 * .pi / 180
        var twin = Twin()
        var time = 0.0
        for s in [Float(2), 3, 4] {
            for yaw in [Float(0.9), -0.9] {
                let camera = portraitCamera(at: SIMD3(s, 1.4, 3.0), forward: SIMD3(sin(yaw) * cos(pitch), -sin(pitch), -cos(yaw) * cos(pitch)))
                let depth = renderDepth(scene, from: camera)
                time += 1
                let t = time
                twin.step("fence frame at s \(s), yaw \(yaw)") { $0.observe(camera, trackingNormal: true, time: t, depth: depth) }
            }
        }
        func fence(from low: Float, to high: Float) -> [ObservedSpan] {
            let plane = WallPlaneEvidence(
                id: "fence", kind: .wall, center: SIMD3((low + high) / 2, 0.55, 2.0), normal: SIMD3(0, 0, -1),
                boundary: [SIMD3(low, 0, 2.0), SIMD3(high, 0, 2.0), SIMD3(high, 1.1, 2.0), SIMD3(low, 1.1, 2.0)])
            return FarSurface.spans(planes: [plane], wall: standardWall(), over: -4...8)
        }
        let whole = fence(from: -4, to: 8)
        twin.step("the whole fence") { $0.setFarSurface(whole) }
        for cut in [Float(0.5), 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5] {
            for (low, high) in [(Float(-4), cut), (cut, Float(8))] {
                let part = fence(from: low, to: high)
                twin.step("fence over s \(low)...\(high)") { $0.setFarSurface(part) }
                twin.step("the whole fence again") { $0.setFarSurface(whole) }
            }
        }
        #expect(twin.cached.rebuildViewCounts.reused > 0)
    }

    /// The counts show views reused while their inputs hold and worked out again when any changes.
    @Test func keptViewsAreReusedOnlyWhileTheirInputsHold() {
        var twin = Twin()
        twin.observeAll(times: Self.depthFrames.indices.map(Double.init))
        let frames = Self.depthFrames.count
        twin.setSurface("far wall")
        // The first rebuild works out the bands and the depth rows of every frame.
        #expect(twin.cached.rebuildViewCounts.reused == 0)
        #expect(twin.cached.rebuildViewCounts.computed == 2 * frames)
        twin.setSurface("far wall and far away")
        #expect(twin.cached.rebuildViewCounts.reused == 2 * frames)
        #expect(twin.cached.rebuildViewCounts.computed == 2 * frames)
        // A value change at cells the corridor frames consulted: those work theirs out again.
        let before = twin.cached.rebuildViewCounts
        twin.setSurface("far wall, one step nearer")
        let after = twin.cached.rebuildViewCounts
        #expect(after.computed > before.computed)
        #expect(after.reused > before.reused)
        // An end changes the basis: every view is worked out again.
        twin.step("right end") { $0.setEnd(.right, at: 3) }
        #expect(twin.cached.rebuildViewCounts.computed == after.computed + 2 * frames)
        #expect(twin.cached.rebuildViewCounts.reused == after.reused)
        twin.setSurface("far wall")
    }

    /// Seeded runs of every change the map takes, in random order.
    @Test(arguments: [UInt64(1), 2, 3])
    func seededSequencesMatchAFullRebuild(seed: UInt64) {
        var random = SplitMix(state: seed)
        var twin = Twin()
        let surfaces = Self.surfaces.keys.sorted()
        var time = 0.0
        for index in 0..<60 {
            time += 1
            let at = time
            switch random.below(12) {
            case 0, 1:
                let frame = Self.depthFrames[random.below(Self.depthFrames.count)]
                // Times out of order now and then, as photos finish storing.
                let t = random.below(4) == 0 ? at - 3.5 : at
                twin.step("\(index): observe with depth") { $0.observe(frame.camera, trackingNormal: true, time: t, depth: frame.depth) }
            case 2:
                let camera = Self.depthlessFrames[random.below(Self.depthlessFrames.count)]
                twin.step("\(index): observe without depth") { $0.observe(camera, trackingNormal: true, time: at) }
            case 3, 4, 5:
                twin.setSurface(surfaces[random.below(surfaces.count)])
            case 6:
                let side: WalkSide = random.below(2) == 0 ? .left : .right
                let s = side == .left ? -0.5 - Float(random.below(10)) * 0.2 : 0.4 + Float(random.below(10)) * 0.2
                let limit = random.below(2) == 0
                twin.step("\(index): end \(side) at \(s), limit \(limit)") { map in
                    map.setEnd(side, at: s)
                    map.setEndIsLimit(side, limit)
                }
            case 7:
                let side: WalkSide = random.below(2) == 0 ? .left : .right
                twin.step("\(index): clear \(side) end") { $0.clearEnd(side) }
            case 8:
                let shift = Float(random.below(9)) * 0.05 - 0.2
                twin.step("\(index): meter move \(shift)") { map in
                    var moved = map.wall
                    moved.meter.x += shift
                    map.updateWall(moved)
                }
            case 9:
                let yaw = Float(random.below(5)) * 0.002 - 0.004
                twin.step("\(index): anchor correction \(yaw)") { $0.apply(YawCorrection(yaw: yaw, translation: SIMD3(yaw, 0, -yaw))) }
            case 10:
                let cell = random.below(20) - 10
                if random.below(2) == 0 {
                    twin.step("\(index): skip cell \(cell)") { $0.markSkipped(.ground, $0.cellRange(cell)) }
                } else {
                    twin.step("\(index): withdraw cell \(cell)") { $0.withdrawClaims(over: $0.cellRange(cell)) }
                }
            default:
                let error: Float = random.below(2) == 0 ? 0.3 : 0
                twin.step("\(index): height error \(error)") { $0.heightError = error }
            }
        }
    }

    struct SplitMix {
        var state: UInt64
        mutating func below(_ bound: Int) -> Int {
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return Int((z ^ (z >> 31)) % UInt64(bound))
        }
    }
}
