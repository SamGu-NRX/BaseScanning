@testable import HouseScanKit
import simd
import Testing

// Cells are 0.1524 m wide: cell -1 is [-0.1524, 0], cell 0 is [0, 0.1524]. Wall rows sit every
// 0.1524 m from 0 to 2.286 (rows 0 to 15); ground rows 0, 0.6 and 1.2 out. A row counts when both
// its samples (a quarter and three quarters along the cell) are in view; a wall cell is covered
// when every row up to the walking height (1.3716 m, row 9) is seen from two positions 0.25 m
// apart, a ground cell when every row is.
//
// The front camera stands 2.6 m out at height 1.4, pitched down 16 degrees. With the 3 % margin the
// image spans atan(300.8 / 500) = 31.03 degrees above and below the view axis. Looking at s = 0:
//   wall h 0:      atan(-1.4 / 2.6) = -28.3, so 12.3 below the axis   -> in
//   wall h 1.98:   atan(0.58 / 2.6) = 12.6, so 28.6 above              -> in (row 13)
//   wall h 2.13:   atan(0.73 / 2.6) = 15.7, so 31.7 above              -> out (row 14)
//   ground out 0 / 0.6 / 1.2: -28.3 / -35.0 / -45.0, so 12.3 / 19.0 / 29.0 below -> in
// The shallowest ground view (out 0) is acos(1.4 / 2.95) = 61.7 degrees from the normal, under 65.
// So cells -1 and 0 see every ground row and the wall up to row 13 (1.9812 m), never its top two
// rows: from here the wall is covered to the walking height and seen 1.9812 m up. Pitched down
// 35 degrees, the view's top meets the wall at 1.22 m, under the walking height (see
// walkingBandNeedsItsOwnView).
@Suite struct CoverageMapTests {
    static let front = SIMD3<Float>(0, 1.4, 2.6)
    static func frontCamera(x: Float = 0, pitch: Float = 16) -> CameraFrame {
        portraitCamera(at: front + SIMD3(x, 0, 0), forward: forwardFacingWall(pitchedDown: pitch))
    }

    @Test func frontCameraSeesBothBandsAroundTheMeter() {
        var map = CoverageMap(wall: standardWall())
        let delta = map.observe(Self.frontCamera(), trackingNormal: true)
        #expect(delta.newlySeen > 0)
        #expect(delta.newlyCovered == 0)
        #expect(map.revision == 1)
        for band in SurfaceBand.allCases {
            for index in [-1, 0] {
                #expect(map.level(band, index) == .seen, "\(band) \(index)")
            }
        }
        #expect(map.coveredCount == 0)
        // 6 m away from the meter is far outside a 2.6 m view.
        #expect(map.level(.wall, 40) == .unseen)
    }

    /// Rows seen by different frames add up, but only a row seen from two positions counts: views
    /// that never show the top of the walking band never cover the wall, however many there are.
    @Test func walkingBandNeedsItsOwnView() {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(pitch: 35), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.3, pitch: 35), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.6, pitch: 35), trackingNormal: true)
        #expect(map.level(.wall, 0) == .seen)
        #expect(map.level(.ground, 0) == .covered)
        // Two level views from other places see row 9: the walking band now has two positions.
        map.observe(wallCamera(s: 0), trackingNormal: true)
        #expect(map.level(.wall, 0) == .seen)
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        #expect(map.level(.wall, 0) == .covered)
    }

    @Test func repeatedAndNearbyFramesNeverCover() {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(), trackingNormal: true)
        // Same position: every visible cell already holds it.
        let again = map.observe(Self.frontCamera(), trackingNormal: true)
        #expect(!again.changed)
        #expect(map.revision == 1)
        // 0.1 m away is under the 0.25 m covering baseline, so it never counts as a second view.
        let nearby = map.observe(Self.frontCamera(x: 0.1), trackingNormal: true)
        #expect(nearby.newlyCovered == 0)
        #expect(map.coveredCount == 0)
        #expect(map.level(.wall, 0) == .seen)
        #expect(map.level(.ground, -1) == .seen)
    }

    @Test func secondPositionCoversTheOverlap() {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(), trackingNormal: true)
        #expect(map.wallSeenHeight(at: 0) == nil)
        // 0.3 m to the side: cells -1 and 0 are at most 0.41 m sideways, about 9 degrees off axis.
        let delta = map.observe(Self.frontCamera(x: 0.3), trackingNormal: true)
        #expect(delta.newlyCovered > 0)
        #expect(map.revision == 2)
        for index in [-1, 0] {
            #expect(map.level(.ground, index) == .covered, "ground \(index)")
            // Every wall row up to 1.9812 m now has two positions, the walking band's among them;
            // the two above it none.
            #expect(map.level(.wall, index) == .covered, "wall \(index)")
            #expect(nearlyEqual(map.wallSeenHeight(at: index) ?? .nan, 1.9812), "wall \(index)")
        }
        #expect(map.coveredCount == delta.newlyCovered)
    }

    /// The reported height is the covered rows' own, less the error of a guessed ground, and
    /// nothing when the error leaves none.
    @Test func aGuessedGroundComesOffTheSeenHeight() {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.3), trackingNormal: true)
        let revision = map.revision
        map.heightError = 0.3
        #expect(map.revision == revision + 1)
        #expect(nearlyEqual(map.wallSeenHeight(at: 0) ?? .nan, 1.6812))
        #expect(map.level(.wall, 0) == .covered)
        map.heightError = 2
        #expect(map.wallSeenHeight(at: 0) == nil)
        #expect(map.wallSeenSpans().isEmpty)
    }

    @Test func limitedTrackingChangesNothing() {
        var map = CoverageMap(wall: standardWall())
        #expect(!map.observe(Self.frontCamera(), trackingNormal: false).changed)
        #expect(map.revision == 0)
        #expect(map.seenExtent == nil)
        #expect(map.level(.wall, 0) == .unseen)
    }

    @Test func cameraBehindTheWallSeesNothing() {
        // At z = -3 looking at the wall's back: wall samples face +z, away from it, and the wall
        // hides the ground in front of it (the row at its foot would otherwise pass: cos = 1.4 / 3.31
        // = 0.4229 >= cos 65 = 0.4226).
        var map = CoverageMap(wall: standardWall())
        let behind = portraitCamera(at: SIMD3(0, 1.4, -3), lookingAt: SIMD3(0, 0.5, 0))
        #expect(map.newlySeenCount(from: behind) == 0)
        #expect(!map.observe(behind, trackingNormal: true).changed)
        #expect(map.seenExtent == nil)
    }

    @Test func grazingViewDoesNotSeeFarCells() throws {
        // Standing 1 m out and looking along the wall at s = -4: a wall sample there is at most
        // (4, 1.4, 1) from the camera, cos = 1 / 4.36 = 0.23, 77 degrees from the normal. The
        // nearest ground sample there is (4, 1.4, 1) away, cos = 1.4 / 4.36 = 0.32, 71 degrees.
        let map = CoverageMap(wall: standardWall())
        let camera = portraitCamera(at: SIMD3(0, 1.4, 1), lookingAt: SIMD3(-4, 0.8, 0))
        let index = map.cellIndex(forS: -4)
        #expect(index == -27)
        // The cell is in the picture, so only the angle rules it out.
        let pixel = try #require(camera.pixel(of: SIMD3(-4, 1.2, 0)))
        #expect(camera.contains(pixel: pixel, margin: 0.03))
        #expect(!map.isVisible(.wall, index, from: camera))
        #expect(!map.isVisible(.ground, index, from: camera))
    }

    @Test func tooFarSeesNothing() {
        // 7 m out: every wall sample is at least 7 m away, the nearest ground sample
        // sqrt(6^2 + 1.4^2) = 6.16 m. Both exceed maxDistance 6.
        var map = CoverageMap(wall: standardWall())
        let far = portraitCamera(at: SIMD3(0, 1.4, 7), lookingAt: SIMD3(0, 0.8, 0))
        #expect(map.newlySeenCount(from: far) == 0)
        #expect(!map.observe(far, trackingNormal: true).changed)
    }

    @Test func cellEdgesMapToExactlyTheirCell() {
        // A span built from cell edges (gap spans, seen extents) must not pick up a neighbour through
        // Float rounding: that inflated GapPlanner progress, e.g. 3 of 4 cells read as 4 of 5 (80 %).
        let map = CoverageMap(wall: standardWall())
        for index in -60...60 {
            #expect(map.indices(overlapping: map.cellRange(index)) == index...index, "cell \(index)")
        }
        let run = map.cellRange(-6).lowerBound...map.cellRange(5).upperBound
        #expect(map.indices(overlapping: run) == -6...5)
        // Ranges inside one cell, including a point, stay one cell.
        #expect(map.indices(overlapping: 0.05...0.1) == 0...0)
        #expect(map.indices(overlapping: 0...0) == 0...0)
        #expect(map.indices(overlapping: -3...3) == -20...19)
    }

    @Test func visibleRangeBeforeAnythingIsSeen() {
        let map = CoverageMap(wall: standardWall())
        #expect(map.visibleRange == -2.5...2.5)
    }

    @Test func endsClipObservationAndVisibleRange() throws {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.left, at: 0)
        #expect(map.revision == 1)
        map.observe(Self.frontCamera(), trackingNormal: true)
        // Cell -1 ends at 0, on the left end: excluded. Cell 0 starts there: allowed.
        #expect(map.level(.wall, -1) == .unseen)
        #expect(map.level(.ground, -1) == .unseen)
        #expect(map.level(.wall, 0) == .seen)
        let seen = try #require(map.seenExtent)
        #expect(seen.lowerBound == 0)
        #expect(nearlyEqual(map.visibleRange, 0...(seen.upperBound + 2.5)))
        map.setEnd(.right, at: 0.5)
        #expect(map.visibleRange == 0...0.5)
    }

    @Test func skippingNeverDowngradesCoveredCells() {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.3), trackingNormal: true)
        let revision = map.revision
        map.markSkipped(.ground, -3...3)
        #expect(map.revision == revision + 1)
        #expect(map.level(.ground, 0) == .covered)
        // Cell 19 is [2.90, 3.05]: about 2.7 m sideways of the cameras, out of their 24 degree half view.
        #expect(map.level(.ground, 19) == .skipped)
        #expect(map.level(.wall, 19) == .unseen)
        // A skipped cell is never counted as covered.
        #expect(map.coveredFraction(.ground, in: 2.95...3.0) == 0)
    }

    @Test func wallSeenSpansMergeCellsOfEqualHeight() throws {
        var map = CoverageMap(wall: standardWall())
        map.observe(Self.frontCamera(), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.3), trackingNormal: true)
        // Around the meter both views reach row 13; toward the image's sides, whose edges lean in
        // on the wall under the pitch, the top rows drop out first and the height steps down.
        let spans = map.wallSeenSpans()
        let middle = try #require(spans.first { $0.span.contains(-0.1524) && $0.span.contains(0.1524) })
        #expect(nearlyEqual(middle.out, 1.9812))
        #expect(spans.count > 1)
        // Joined exactly where neighbouring cells reach the same height.
        for (a, b) in zip(spans, spans.dropFirst()) {
            #expect(a.out != b.out || b.span.lowerBound - a.span.upperBound > 1e-3, "\(a) \(b)")
        }
        for span in spans {
            for index in map.indices(overlapping: span.span) {
                #expect(map.wallSeenHeight(at: index) == span.out, "cell \(index)")
            }
        }
        // Covered to the walking height, reported to what was seen.
        #expect(map.coveredIntervals(.wall).contains { $0.contains(0) })
    }

    /// Views that reach the top wall row cover the cell, and the height reported is that row's.
    @Test func coveredWallIsSeenToTheCaptureHeight() {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0), trackingNormal: true)
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        #expect(map.level(.wall, 0) == .covered)
        #expect(nearlyEqual(map.wallSeenHeight(at: 0) ?? .nan, map.config.wallCaptureHeight))
        #expect(map.wallRows.count == 16 && nearlyEqual(map.wallRows.last ?? 0, 2.286))
    }

    /// The meter anchor refined 0.3048 m (two cells) to the left: every seen cell and marked end
    /// is 0.3048 m further right of the meter than before.
    @Test func movingTheMeterShiftsCellsAndEnds() throws {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0), trackingNormal: true)
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        map.setEnd(.left, at: -0.5)
        let before = try #require(map.coveredIntervals(.wall).first)
        var moved = standardWall()
        moved.meter.x -= 0.3048
        map.updateWall(moved)
        let after = try #require(map.coveredIntervals(.wall).first)
        #expect(nearlyEqual(map.leftEnd ?? .nan, -0.1952))
        #expect(nearlyEqual(after.upperBound, before.upperBound + 0.3048, 1e-3))
        // Covered wall cells were -4 ... 5 (coveredIntervalsOfHandBuiltRuns); now -2 ... 7.
        #expect(map.level(.wall, 7) == .covered && map.level(.wall, 8) != .covered)
        // Cells are replayed from the cameras, so a move under a cell counts exactly: at 0.4048 m
        // the cameras sit at s = 0.4048 and 0.7048, covering L in [-0.2357, 1.1929], cells -1 ... 7.
        moved.meter.x -= 0.1
        map.updateWall(moved)
        #expect(map.level(.wall, -2) != .covered && map.level(.wall, -1) == .covered)
        #expect(map.level(.wall, 7) == .covered && map.level(.wall, 8) != .covered)
    }

    /// A level camera like `wallCamera`, 0.3 m higher: a wall sample at world height y lands on
    /// u = 320 + 250 (1.5 - y), inside the 620.8 margin only for y >= 0.2968.
    static func highWallCamera(s c: Float) -> CameraFrame {
        makeCamera(at: SIMD3(c, 1.5, 2.0), forward: SIMD3(0, 0, -1), right: SIMD3(1, 0, 0))
    }

    /// The ground was guessed 0.3 m too high, so the bottom wall row sat 0.3 m up the wall, where
    /// the cameras saw it. Measured at y = 0, that row is below every view: no cell stays covered.
    @Test func correctingTheGroundRechecksWhichHeightsWereSeen() throws {
        var guessed = standardWall()
        guessed.groundY = 0.3
        var map = CoverageMap(wall: guessed)
        map.observe(Self.highWallCamera(s: 0), trackingNormal: true)
        map.observe(Self.highWallCamera(s: 0.3), trackingNormal: true)
        map.observe(Self.highWallCamera(s: 0.6), trackingNormal: false)
        #expect(map.observedCameras.count == 2)
        // Cells -4 ... 5, as for wallCamera: v depends only on s.
        #expect(nearlyEqual(try #require(map.coveredIntervals(.wall).first), -0.6096...0.9144))
        let revision = map.revision

        map.updateWall(standardWall())
        #expect(map.revision > revision)
        #expect(map.coveredIntervals(.wall).isEmpty)
        #expect(map.coveredCount == 0)
        // Rows from 0.3048 m up are still seen from both positions; the two below are not, so
        // nothing is seen from the foot up.
        #expect(map.level(.wall, 0) == .seen)
        #expect(map.visibleRows(.wall, 0, from: Self.highWallCamera(s: 0)) == Set(2...15))
        #expect(map.wallSeenHeight(at: 0) == nil)
    }

    @Test func rebuildingKeepsSkippedCellsAndEnds() {
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0), trackingNormal: true)
        map.markSkipped(.ground, 1...2)
        map.setEnd(.right, at: 3)
        var lowered = standardWall()
        lowered.groundY = -0.1
        map.updateWall(lowered)
        #expect(map.level(.ground, 7) == .skipped)
        #expect(map.rightEnd == 3)
        #expect(map.level(.wall, 0) == .seen)
    }

    @Test func clearingAnEndReplaysSightingsPastIt() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.right, at: 0)
        map.observe(Self.frontCamera(), trackingNormal: true)
        #expect(map.level(.ground, 1) == .unseen)
        map.clearEnd(.right)
        #expect(map.level(.ground, 1) == .seen)
        #expect(map.level(.wall, 1) == .seen)
    }

    @Test func clearingAnEndPreservesPrecomputedSightingsWithoutKeptFrames() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.right, at: 0)
        let sighting = CoverageMap.Sighting(band: .ground, index: -1, rows: Set(0...2), hiddenRows: [])
        map.record([sighting], from: Self.front)
        #expect(map.level(.ground, -1) == .seen)
        map.clearEnd(.right)
        #expect(map.level(.ground, -1) == .seen)
        map.clearEnd(.right)
        #expect(map.level(.ground, -1) == .seen)
        #expect(map.coveredCount == 0)
    }

    @Test func clearingAnEndTwiceDoesNotCountSightingsTwice() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.right, at: 0)
        map.observe(Self.frontCamera(), trackingNormal: true)
        map.clearEnd(.right)
        let once = map
        map.clearEnd(.right)
        #expect(map.stateDifferences(from: once).filter { $0 != "revision" }.isEmpty)
        #expect(map.level(.ground, 1) == .seen)
        #expect(map.coveredCount == 0)
    }

    @Test func clearingAndRemarkingAnEndKeepsTheSameCoverage() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.right, at: 0)
        map.observe(Self.frontCamera(), trackingNormal: true)
        map.observe(Self.frontCamera(x: 0.3), trackingNormal: true)
        let neverCleared = map
        map.clearEnd(.right)
        map.setEnd(.right, at: 0)
        #expect(map.stateDifferences(from: neverCleared).filter { $0 != "revision" }.isEmpty)
        var expected = neverCleared
        expected.clearEnd(.right)
        map.clearEnd(.right)
        #expect(map.stateDifferences(from: expected).filter { $0 != "revision" }.isEmpty)
    }

    @Test func coveredIntervalsOfHandBuiltRuns() {
        // Wall cameras at 0 and 0.3 cover cells whose lower edge L satisfies both 0.3 <= L + 0.9405
        // and 0 >= L - 0.7881: L in [-0.6405, 0.7881], cells -4 ... 5, i.e. [-0.6096, 0.9144].
        var map = CoverageMap(wall: standardWall())
        map.observe(wallCamera(s: 0), trackingNormal: true)
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        let intervals = map.coveredIntervals(.wall)
        #expect(intervals.count == 1)
        #expect(nearlyEqual(intervals[0], -0.6096...0.9144))
        #expect(map.coveredIntervals(.ground).isEmpty)
        // A right end at 0.5 trims the last run.
        map.setEnd(.right, at: 0.5)
        #expect(nearlyEqual(map.coveredIntervals(.wall)[0], -0.6096...0.5))
    }
}
