@testable import HouseScan
@testable import HouseScanKit
import simd
import Testing

@Suite @MainActor struct GuidancePauseTests {
    private func engine() throws -> ScanEngine {
        let engine = ScanEngine(options: LaunchOptions(arguments: ["HouseScan", "-sampleResult"], defaultServerURL: nil))
        try #require(engine.setWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0, groundMeasured: true))
        engine.state.phase = .wallWalk
        return engine
    }

    private var camera: CameraFrame {
        let pitch: Float = 20 * .pi / 180
        let forward = SIMD3<Float>(0, -sin(pitch), -cos(pitch))
        let right = SIMD3<Float>(1, 0, 0)
        return CameraFrame(
            cameraToWorld: simd_float4x4(SIMD4(-simd_cross(right, forward), 0), SIMD4(right, 0),
                                       SIMD4(-forward, 0), SIMD4(0, 1.4, 2.6, 1)),
            intrinsics: SIMD4(500, 500, 320, 240), imageSize: SIMD2(640, 480)
        )
    }

    private func update(_ engine: ScanEngine, at time: Double) {
        engine.updateGuidance(camera: camera, time: time, screenTime: time)
    }

    @Test func tiltUpPausesTheAimAcrossEarlyReturns() throws {
        let engine = try engine()
        update(engine, at: 0)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        engine.updateCoverage {
            $0.setEnd(.left, at: -3)
            $0.setEnd(.right, at: 3)
        }
        update(engine, at: 5)
        #expect(engine.state.guidance == .tiltUp(span: -1.5...1.5))
        update(engine, at: 35)
        engine.tiltUpSettled = true
        engine.updateCoverage {
            $0.clearEnd(.left)
            $0.clearEnd(.right)
        }
        update(engine, at: 40)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 54.9)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 55)
        #expect(engine.state.guidance != .aimAtGround(s: 0))
    }

    enum Replacement: CaseIterable {
        case end, overhead, endScan, nextWall, nextWallConfirmation, marking, coaching
    }

    @Test(arguments: Replacement.allCases)
    func replacementScreensPauseWithoutSelectingAnotherTask(screen: Replacement) throws {
        let engine = try engine()
        update(engine, at: 0)
        switch screen {
        case .end: engine.state.endQuestion = .right
        case .overhead: engine.state.overheadQuestion = true
        case .endScan: engine.state.endScanQuestion = true
        case .nextWall: engine.nextWallSide = .right
        case .nextWallConfirmation: engine.state.nextWallConfirm = NextWallConfirm(side: .right, fromEnd: 3)
        case .marking: engine.state.marking = MarkingState(kind: .window, step: 0)
        case .coaching: engine.state.coaching = .initializing
        }
        update(engine, at: 5)
        update(engine, at: 35)
        engine.state.endQuestion = nil
        engine.state.overheadQuestion = false
        engine.state.endScanQuestion = false
        engine.nextWallSide = nil
        engine.state.nextWallConfirm = nil
        engine.state.marking = nil
        engine.state.coaching = nil
        update(engine, at: 40)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 54.9)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 55)
        #expect(engine.state.guidance != .aimAtGround(s: 0))
    }

    @Test func twoOfFourHiddenCellsPauseTheAimWithoutCoaching() throws {
        let engine = try engine()
        update(engine, at: 0)
        engine.updateCoverage {
            let hidden = [-1, 0].map {
                CoverageMap.Sighting(band: .ground, index: $0, rows: [], hiddenRows: [1])
            }
            $0.record(hidden, from: camera.position)
        }
        let map = try #require(engine.coverage)
        let cells = map.indices(overlapping: -0.3...0.3)
        #expect(cells.count == 4)
        #expect(cells.filter { map.level(.ground, $0) == .hidden }.count == 2)
        #expect(engine.state.coaching == nil)
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: camera, time: 5).task == .aimAtGround(s: 0))
        update(engine, at: 5)
        #expect(engine.state.guidance == .seeBehind(s: 0))
        update(engine, at: 35)
        #expect(engine.state.guidance == .seeBehind(s: 0))

        // Remove the obstruction while leaving its aim unsatisfied, so deferral is observable.
        engine.updateCoverage { $0 = CoverageMap(wall: $0.wall) }
        update(engine, at: 40)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 54.9)
        #expect(engine.state.guidance == .aimAtGround(s: 0))
        update(engine, at: 55)
        #expect(engine.state.guidance != .aimAtGround(s: 0))
    }

    @Test(arguments: [Coaching.tooDark, .tooDarkToMeasure, .holdSteady, .needsTexture])
    func coachingBesideTheAimStillAllowsAStall(coaching: Coaching) throws {
        let engine = try engine()
        engine.state.coaching = coaching
        update(engine, at: 0)
        update(engine, at: 20)
        #expect(engine.state.guidance != .aimAtGround(s: 0))
    }
}
