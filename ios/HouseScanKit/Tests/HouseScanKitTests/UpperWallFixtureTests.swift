import Foundation
@testable import HouseScanKit
import simd
import Testing

/// The synthetic replay reaches the wall above 6.5 ft only with its appended upper walk (frames
/// 42 to 61). Its first 41 frames, the recording before, stay the case that sees it only to
/// 6.5 ft: the walk's views stop at 2.007 m, short of the 7 ft row, and the three tilt-up views
/// share one position, so the upper rows never get a second.
@Suite struct UpperWallFixtureTests {
    static func frames(limit: Int? = nil) throws -> (wall: WallFrame, frames: [PlannedFrame]) {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../../HouseScanUITests/Fixtures/synthetic-wall")
            .standardizedFileURL
        let session = try ReplaySession.load(folder: folder)
        let declared = try #require(session.declaredWall)
        let wall = try #require(WallFrame(meter: declared.meter, outward: declared.outward, groundY: declared.groundY))
        let frames = session.frames.prefix(limit ?? session.frames.count).map { frame in
            PlannedFrame(
                camera: CameraFrame(cameraToWorld: frame.cameraToWorld, intrinsics: frame.intrinsics, imageSize: SIMD2(Float(frame.width), Float(frame.height))),
                timestamp: frame.timestamp, trackingNormal: frame.trackingNormal)
        }
        return (wall, frames)
    }

    /// Every frame through auto-capture as the app plays them: the walk, then the tilt-up run.
    static func seenHeight(over span: ClosedRange<Float>, limit: Int? = nil) throws -> Float {
        let (wall, frames) = try Self.frames(limit: limit)
        let map = ReplayPlanning.simulateWalk(frames, wall: wall)
        let spans = map.wallSeenSpans()
        let cells = map.indices(overlapping: span)
        return cells.map { index in
            let middle = (map.cellRange(index).lowerBound + map.cellRange(index).upperBound) / 2
            return spans.first { $0.span.contains(middle) }?.out ?? 0
        }.min() ?? 0
    }

    /// The server's wall request on this replay: -3.542852 to 6.542852 ft, out_ft 6.500001.
    static let request: ClosedRange<Float> = (-3.542852 * 0.3048)...(6.542852 * 0.3048)
    static let requestOut: Double = 6.500001

    @Test func theFirst41FramesSeeTheWallTo6Point5Feet() throws {
        let height = try Self.seenHeight(over: Self.request, limit: 41)
        #expect(SceneExport.feetDown(height) == 6.5)
        #expect(SceneExport.feetDown(height) < Self.requestOut)
    }

    @Test func theUpperWalkSeesItAboveTheRequest() throws {
        let height = try Self.seenHeight(over: Self.request)
        #expect(SceneExport.feetDown(height) > Self.requestOut)
        #expect(nearlyEqual(height, CoverageConfig().wallCaptureHeight))
        // The request plans and settles against the map the replay builds.
        let (wall, frames) = try Self.frames()
        let map = ReplayPlanning.simulateWalk(frames, wall: wall)
        let item = try JSONDecoder().decode(PlacementMissingEvidence.self, from: Data(
            #"{"kind":"band","band":"wall","span_ft":[-3.542852,6.542852],"out_ft":6.500001,"message":"m"}"#.utf8))
        let plan = try #require(GapPlanner().plan(for: item, leftEnd: nil, rightEnd: nil, limitEnds: []))
        #expect(plan.asksAboveTheWalk)
        #expect(GapPlanner().isSatisfied(plan, map))
    }

    /// The "Can't get there" flow ends the walk where the phone stood farthest each way and skips
    /// the ground by the meter. Planned for that flow, the held-back window leaves a gap 1 m
    /// inside those ends, which that walk (without the window) still asks for, and the window's
    /// frames close. Planned against the covered extremes it could lie past them, and then the
    /// flow never reached a gap request.
    @Test func theCantGetThereFlowsHeldBackGapIsInsideItsEnds() throws {
        let (wall, all) = try Self.frames()
        let walk = Array(all.prefix(38))
        let held = try #require(ReplayPlanning.heldBackWindow(frames: walk, wall: wall, ends: .walked(margin: 1)))
        var rest = walk
        rest.removeSubrange(held.frames)
        let stood = rest.map { wall.wallPoint($0.camera.position).s }
        let ends = (stood.min() ?? 0)...(stood.max() ?? 0)
        // On this replay the farthest frames track normally and stand inside the kept views'
        // reach, so the engine's chooser puts each end at the phone.
        #expect(held.ends == ends)
        #expect(ends.lowerBound + 1 <= held.gap.span.lowerBound && held.gap.span.upperBound <= ends.upperBound - 1)
        var walked = ReplayPlanning.simulateWalk(rest, wall: wall)
        walked.setEnd(.left, at: ends.lowerBound)
        walked.setEnd(.right, at: ends.upperBound)
        walked.markSkipped(.ground, -0.5...0.5)
        #expect(GapPlanner().plan(walked) == held.gap)
    }
}
