@testable import HouseScanKit
import Testing

@Suite struct AimActionabilityTests {
    private let aim = GuidanceTask.aimAtGround(s: 0)

    @Test(arguments: AimActionability.Screen.allCases)
    func replacementScreensAreNotActionable(screen: AimActionability.Screen) {
        #expect(AimActionability.canAct(on: aim, coverage: CoverageMap(wall: standardWall()),
            screen: screen, coaching: nil, interrupted: false) == (screen == .guidance))
    }

    @Test(arguments: [AimCoaching.tooDark, .tooDarkToMeasure, .holdSteady, .needsTexture, .slowDown, .turnSlowly])
    func coachingBesideTheAimAllowsProgress(coaching: AimCoaching) {
        #expect(AimActionability.canAct(on: aim, coverage: CoverageMap(wall: standardWall()),
            screen: .guidance, coaching: coaching, interrupted: false))
    }

    @Test(arguments: [AimCoaching.initializing, .relocalizing, .trackingLost, .pastWallEnd])
    func replacementCoachingPausesTheAim(coaching: AimCoaching) {
        #expect(!AimActionability.canAct(on: aim, coverage: CoverageMap(wall: standardWall()),
            screen: .guidance, coaching: coaching, interrupted: false))
    }

    @Test func twoOfFourHiddenCellsReplaceAnAimWithoutCoaching() {
        var map = CoverageMap(wall: standardWall())
        map.record([-1, 0].map { .init(band: .ground, index: $0, rows: [], hiddenRows: [1]) }, from: .zero)
        let cells = map.indices(overlapping: -0.3...0.3)
        #expect(cells.count == 4)
        #expect(cells.filter { map.level(.ground, $0) == .hidden }.count == 2)
        #expect(!AimActionability.canAct(on: aim, coverage: map, screen: .guidance, coaching: nil, interrupted: false))
        #expect(AimActionability.canAct(on: .aimAtWall(s: 0), coverage: map,
            screen: .guidance, coaching: nil, interrupted: false))
        #expect(!AimActionability.canAct(on: .seeBehind(s: 0), coverage: map,
            screen: .guidance, coaching: nil, interrupted: false))
    }

    @Test func aTrimmedAimIgnoresHiddenCellsOutsideItsRequest() {
        var map = CoverageMap(wall: standardWall())
        map.record([-1, 0].map { .init(band: .ground, index: $0, rows: [], hiddenRows: [1]) }, from: .zero)
        let visibleWindow: ClosedRange<Float> = -0.3...(-map.config.cellWidth).nextDown
        #expect(AimActionability.canAct(on: aim, coverage: map, screen: .guidance,
            coaching: nil, interrupted: false, aimWindow: visibleWindow))
        #expect(!AimActionability.mostlyHidden(.ground, around: 0, coverage: map, aimWindow: visibleWindow))
    }

    @Test func oneOfFourHiddenCellsLeavesTheAimActionable() {
        var map = CoverageMap(wall: standardWall())
        map.record([.init(band: .ground, index: 0, rows: [], hiddenRows: [1])], from: .zero)
        #expect(AimActionability.canAct(on: aim, coverage: map,
            screen: .guidance, coaching: nil, interrupted: false))
    }

    @Test func aHiddenAimKeepsItsRemainingBudgetWithoutCoaching() {
        var map = CoverageMap(wall: standardWall())
        let camera = GuidancePlannerTests.homeowner()
        var planner = GuidancePlanner()
        _ = planner.update(coverage: map, camera: camera, time: 0)
        map.record([-1, 0].map { .init(band: .ground, index: $0, rows: [], hiddenRows: [1]) }, from: .zero)
        for time: Double in [5, 35] {
            let actionable = AimActionability.canAct(on: planner.current, coverage: map,
                screen: .guidance, coaching: nil, interrupted: false)
            #expect(!actionable)
            #expect(planner.update(coverage: map, camera: camera, time: time, aimActionable: actionable).task == aim)
        }
        map = CoverageMap(wall: map.wall)
        let resumed = AimActionability.canAct(on: planner.current, coverage: map,
            screen: .guidance, coaching: nil, interrupted: false)
        #expect(planner.update(coverage: map, camera: camera, time: 40, aimActionable: resumed).task == aim)
        #expect(planner.update(coverage: map, camera: camera, time: 54.9).stalled == nil)
        #expect(planner.update(coverage: map, camera: camera, time: 55).stalled == aim)
    }

    @Test(arguments: [AimActionability.Screen.tiltUp, .question, .nextWall, .marking])
    func aReplacementKeepsTheRemainingStallBudget(screen: AimActionability.Screen) {
        let map = CoverageMap(wall: standardWall())
        let camera = GuidancePlannerTests.homeowner()
        var planner = GuidancePlanner()
        #expect(planner.update(coverage: map, camera: camera, time: 0).task == aim)
        planner.recordAimActionability(AimActionability.canAct(on: aim, coverage: map,
            screen: screen, coaching: nil, interrupted: false), time: 5)
        // No planner updates while the replacement screen is shown.
        #expect(planner.current == aim)
        #expect(planner.update(coverage: map, camera: camera, time: 40).task == aim)
        #expect(planner.update(coverage: map, camera: camera, time: 54.9).stalled == nil)
        #expect(planner.update(coverage: map, camera: camera, time: 55).stalled == aim)
    }

    @Test func interruptionWithoutFramesKeepsTheRemainingStallBudget() {
        let map = CoverageMap(wall: standardWall())
        let camera = GuidancePlannerTests.homeowner()
        var planner = GuidancePlanner()
        _ = planner.update(coverage: map, camera: camera, time: 0)
        planner.recordAimActionability(AimActionability.canAct(on: aim, coverage: map,
            screen: .guidance, coaching: nil, interrupted: true), time: 5)
        // The first resumed frame is normal, so no coaching remains to record the interruption.
        let resumed = AimActionability.canAct(on: aim, coverage: map,
            screen: .guidance, coaching: nil, interrupted: false)
        #expect(resumed)
        #expect(planner.update(coverage: map, camera: camera, time: 40, aimActionable: resumed).task == aim)
        #expect(planner.update(coverage: map, camera: camera, time: 54.9).stalled == nil)
        #expect(planner.update(coverage: map, camera: camera, time: 55).stalled == aim)
    }
}
