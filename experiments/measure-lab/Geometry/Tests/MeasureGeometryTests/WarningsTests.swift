import Testing
@testable import MeasureGeometry

let cleanPass = WallCheck(passes: true, contactWarnings: [])
let cleanFail = WallCheck(passes: false, contactWarnings: [])

struct WarningsTests {
    @Test func `a wall point inherits its wall's contact warnings`() {
        let wall = WallStatus(contactWarnings: [[.shallowLookDown], []], checks: [cleanPass])
        let point = PointEvidence(own: [], wall: wall)
        #expect(point.warnings == [.wallContactWarning])
    }

    @Test func `a failed validation reaches later wall points`() {
        let wall = WallStatus(contactWarnings: [[], []], checks: [cleanFail])
        #expect(PointEvidence(own: [], wall: wall).warnings == [.wallValidationFailed])
        // A second, passing check does not clear the failure.
        let rechecked = WallStatus(contactWarnings: [[], []], checks: [cleanFail, cleanPass])
        #expect(rechecked.warnings == [.wallValidationFailed])
    }

    @Test func `a wall status lists its own qualification`() {
        #expect(WallStatus(contactWarnings: [[], []], checks: [cleanPass]).warnings.isEmpty)
        #expect(WallStatus(contactWarnings: [[], []], checks: []).warnings == [.wallNotValidated])
        #expect(
            WallStatus(contactWarnings: [[.estimatedPlane], [.extendedPlane]], checks: [])
                .warnings == [.wallContactWarning, .wallNotValidated]
        )
    }

    @Test(arguments: [MeasurementWarning.estimatedPlane, .extendedPlane, .shallowLookDown])
    func `a passing check from a flagged contact does not validate the wall`(flag: MeasurementWarning) {
        let check = WallCheck(passes: true, contactWarnings: [flag])
        #expect(check.outcome == .unconfirmed)
        #expect(WallStatus(contactWarnings: [[], []], checks: [check]).warnings == [.wallNotValidated])
        // A later clean pass confirms it.
        #expect(WallStatus(contactWarnings: [[], []], checks: [check, cleanPass]).warnings.isEmpty)
    }

    @Test(arguments: [MeasurementWarning.estimatedPlane, .extendedPlane, .shallowLookDown])
    func `a saved passing check reads as unconfirmed when its contact is flagged`(flag: MeasurementWarning) {
        #expect(WallCheck.outcome(passes: true, contactWarnings: [flag]) == .unconfirmed)
        #expect(WallCheck.outcome(passes: false, contactWarnings: [flag]) == .failed)
    }

    @Test func `a saved check reads as confirmed only from a clean contact`() {
        #expect(WallCheck.outcome(passes: true, contactWarnings: []) == .confirmed)
        #expect(WallCheck.outcome(passes: false, contactWarnings: []) == .failed)
    }

    @Test func `a saved check whose contact has no record cannot confirm the wall`() {
        #expect(WallCheck.outcome(passes: true, contactWarnings: nil) == .unconfirmed)
        #expect(WallCheck.outcome(passes: false, contactWarnings: nil) == .failed)
    }

    @Test func `a failing check from a flagged contact still fails the wall`() {
        let check = WallCheck(passes: false, contactWarnings: [.estimatedPlane])
        #expect(check.outcome == .failed)
        #expect(WallStatus(contactWarnings: [[], []], checks: [cleanPass, check]).warnings == [.wallValidationFailed])
    }

    @Test func `point warnings combine own and wall, sorted, without repeats`() {
        let wall = WallStatus(contactWarnings: [[.shallowLookDown], []], checks: [cleanFail])
        let point = PointEvidence(own: [.outsideWallContacts, .outsideWallContacts], wall: wall)
        #expect(point.warnings == [.outsideWallContacts, .wallContactWarning, .wallValidationFailed])
    }

    @Test func `a measurement inherits both points and the walls behind them`() {
        let flaggedWall = WallStatus(contactWarnings: [[.shallowLookDown], []], checks: [cleanPass])
        let warnings = measurementWarnings(
            from: PointEvidence(own: [], wall: flaggedWall),
            to: PointEvidence(own: [.estimatedPlane]),
            targetWall: nil,
            referenceWall: nil,
            compared: .horizontal,
            value: 1.2,
            beyondWallContacts: false
        )
        #expect(warnings == [.estimatedPlane, .wallContactWarning])
    }

    @Test func `the reference wall matters only for along-wall distance`() {
        let unvalidated = WallStatus(contactWarnings: [[], []], checks: [])
        let clean = PointEvidence(own: [])
        #expect(measurementWarnings(
            from: clean, to: clean, targetWall: nil, referenceWall: unvalidated, compared: .horizontal, value: 2,
            beyondWallContacts: false
        ).isEmpty)
        #expect(measurementWarnings(
            from: clean, to: clean, targetWall: nil, referenceWall: unvalidated, compared: .alongWall, value: 2,
            beyondWallContacts: false
        ) == [.wallNotValidated])
    }

    @Test func `a point-to-wall measurement inherits the target wall`() {
        let failed = WallStatus(contactWarnings: [[], []], checks: [cleanFail])
        #expect(measurementWarnings(
            from: PointEvidence(own: []), to: nil, targetWall: failed, referenceWall: nil, compared: .gapToWall, value: 1,
            beyondWallContacts: false
        ) == [.wallValidationFailed])
    }

    @Test func `a quantity read beyond the wall's contacts is flagged`() {
        let good = WallStatus(contactWarnings: [[], []], checks: [cleanPass])
        #expect(measurementWarnings(
            from: PointEvidence(own: []), to: nil, targetWall: good, referenceWall: nil, compared: .heightAboveGround,
            value: 1, beyondWallContacts: true
        ) == [.outsideWallContacts])
    }

    @Test func `a negative height above ground is flagged`() {
        let good = WallStatus(contactWarnings: [[], []], checks: [cleanPass])
        let clean = PointEvidence(own: [])
        #expect(measurementWarnings(
            from: clean, to: nil, targetWall: good, referenceWall: nil, compared: .heightAboveGround, value: -1,
            beyondWallContacts: false
        ) == [.belowGround])
        #expect(measurementWarnings(
            from: clean, to: nil, targetWall: good, referenceWall: nil, compared: .heightAboveGround, value: 1,
            beyondWallContacts: false
        ).isEmpty)
    }
}
