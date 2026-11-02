import Testing
@testable import MeasureGeometry

/// Each test starts from W1, a 4 m wall from x = 0 to x = 4 along z = 0, built from clean
/// contacts P1 and P2 and seen from z = +3.
struct MeasurementLedgerTests {
    var ledger = MeasurementLedger()

    init() throws {
        try addWall("W1", from: SIMD3(0, 0, 0), to: SIMD3(4, 0, 0), contacts: ("P1", "P2"))
    }

    private mutating func addWall(_ id: String, from start: SIMD3<Double>, to end: SIMD3<Double>, contacts: (String, String)) throws {
        ledger.addPoint(contacts.0, at: start, flags: [])
        ledger.addPoint(contacts.1, at: end, flags: [])
        let camera = (start + end) / 2 + SIMD3(0, 1.5, 3)
        ledger.addWall(id, try Wall(contact1: start, contact2: end, cameraPosition: camera), contacts: [contacts.0, contacts.1])
    }

    /// Adds a ground contact and checks it against the wall with the wall's own geometry.
    private mutating func check(
        _ wallID: String,
        with pointID: String,
        at position: SIMD3<Double>,
        flags: [MeasurementWarning] = []
    ) throws -> (outcome: WallCheck.Outcome, changedMeasurements: [String]) {
        ledger.addPoint(pointID, at: position, flags: flags)
        let wall = try #require(ledger.wall(wallID))
        return ledger.addCheck(wall.validate(contact: position), contact: pointID, toWall: wallID)
    }

    @Test mutating func `a failed check after saving turns dependent measurements into abstentions`() throws {
        #expect(try check("W1", with: "P3", at: SIMD3(3, 0, 0.01)).outcome == .confirmed)
        ledger.addPoint("G1", at: SIMD3(2, 0, 1.5), flags: [])
        ledger.addPoint("G2", at: SIMD3(2, 0, 4), flags: [])
        ledger.addPoint("V1", at: SIMD3(1, 1, 0), flags: [], onWall: "W1")
        // Gap to the wall, a wall point to a ground point, and two ground points.
        #expect(ledger.saveMeasurement("M1", from: "G1", to: .wall("W1"), referenceWall: nil, compared: .gapToWall) == [])
        #expect(ledger.saveMeasurement("M2", from: "V1", to: .point("G2"), referenceWall: nil, compared: .straight) == [])
        #expect(ledger.saveMeasurement("M3", from: "G1", to: .point("G2"), referenceWall: "W1", compared: .straight) == [])

        // 0.3 m off the plane, past the 2 in tolerance.
        let failed = try check("W1", with: "P4", at: SIMD3(1, 0, 0.3))

        #expect(failed.outcome == .failed)
        #expect(failed.changedMeasurements == ["M1", "M2"])
        #expect(ledger.savedWarnings("M1") == [.wallValidationFailed])
        #expect(ledger.savedWarnings("M2") == [.wallValidationFailed])
        // Straight distance between ground points never used the wall.
        #expect(ledger.savedWarnings("M3") == [])
        #expect(ledger.wallStatus("W1")?.warnings == [.wallValidationFailed])
    }

    @Test(arguments: [MeasurementWarning.estimatedPlane, .extendedPlane, .shallowLookDown])
    mutating func `a flagged third contact leaves the wall unvalidated`(flag: MeasurementWarning) throws {
        let result = try check("W1", with: "P3", at: SIMD3(3, 0, 0.01), flags: [flag])

        #expect(result.outcome == .unconfirmed)
        #expect(ledger.wallStatus("W1")?.warnings == [.wallNotValidated])
        ledger.addPoint("V1", at: SIMD3(1, 1, 0), flags: [], onWall: "W1")
        #expect(ledger.evidence(forPoint: "V1")?.warnings == [.wallNotValidated])
        ledger.addPoint("G1", at: SIMD3(2, 0, 1.5), flags: [])
        #expect(ledger.saveMeasurement("M1", from: "G1", to: .wall("W1"), referenceWall: nil, compared: .gapToWall) == [.wallNotValidated])
    }

    @Test mutating func `a later clean check does not upgrade a saved abstention`() throws {
        ledger.addPoint("G1", at: SIMD3(2, 0, 1.5), flags: [])
        #expect(ledger.saveMeasurement("M1", from: "G1", to: .wall("W1"), referenceWall: nil, compared: .gapToWall) == [.wallNotValidated])

        let confirmed = try check("W1", with: "P3", at: SIMD3(3, 0, 0.01))

        #expect(confirmed.outcome == .confirmed)
        #expect(confirmed.changedMeasurements.isEmpty)
        #expect(ledger.savedWarnings("M1") == [.wallNotValidated])
        // The same measurement saved now would be clean.
        #expect(ledger.warnings(from: "G1", to: .wall("W1"), referenceWall: nil, compared: .gapToWall) == [])
    }

    @Test mutating func `along-wall distance is flagged against the selected reference wall only`() throws {
        // W2 runs from x = 10 to x = 14, so both ground points below lie before its first contact.
        try addWall("W2", from: SIMD3(10, 0, 0), to: SIMD3(14, 0, 0), contacts: ("P5", "P6"))
        _ = try check("W1", with: "P3", at: SIMD3(3, 0, 0.01))
        _ = try check("W2", with: "P7", at: SIMD3(12, 0, 0.01))
        ledger.addPoint("A", at: SIMD3(1, 0, 2), flags: [])
        ledger.addPoint("B", at: SIMD3(3, 0, 2), flags: [])

        #expect(ledger.warnings(from: "A", to: .point("B"), referenceWall: "W1", compared: .alongWall) == [])
        #expect(ledger.warnings(from: "A", to: .point("B"), referenceWall: "W2", compared: .alongWall) == [.outsideWallContacts])
        // The 3D distances of the same pair ignore the reference wall.
        for quantity in [MeasuredQuantity.straight, .horizontal, .vertical] {
            #expect(ledger.warnings(from: "A", to: .point("B"), referenceWall: "W2", compared: quantity) == [])
        }
    }

    @Test mutating func `along-wall distance is flagged past either end of the reference wall`() throws {
        _ = try check("W1", with: "P3", at: SIMD3(3, 0, 0.01))
        ledger.addPoint("A", at: SIMD3(1, 0, 2), flags: [])
        ledger.addPoint("Before", at: SIMD3(-1, 0, 2), flags: [])
        ledger.addPoint("Past", at: SIMD3(5, 0, 2), flags: [])

        #expect(ledger.saveMeasurement("M1", from: "Before", to: .point("A"), referenceWall: "W1", compared: .alongWall) == [.outsideWallContacts])
        #expect(ledger.saveMeasurement("M2", from: "A", to: .point("Past"), referenceWall: "W1", compared: .alongWall) == [.outsideWallContacts])
        // The defining contacts of this 4 m wall stay clean.
        #expect(ledger.saveMeasurement("M3", from: "P1", to: .point("P2"), referenceWall: "W1", compared: .alongWall) == [])
    }

    @Test mutating func `the protocol's 30 foot contact span stays accepted`() throws {
        try addWall("W30", from: SIMD3(0, 0, 0), to: SIMD3(9.144, 0, 0), contacts: ("A30", "B30"))
        _ = try check("W30", with: "C30", at: SIMD3(4.572, 0, 0))
        let values = ledger.values(from: "A30", to: .point("B30"), referenceWall: "W30")
        #expect(abs((values[.alongWall] ?? .nan) - 9.144) < 1e-12)
        let warnings = ledger.saveMeasurement("M30", from: "A30", to: .point("B30"), referenceWall: "W30", compared: .alongWall)
        #expect(warnings == [])
    }

    @Test mutating func `height and gap are flagged for ground and two-view points past either end`() throws {
        _ = try check("W1", with: "P3", at: SIMD3(3, 0, 0.01))
        ledger.addPoint("Before", at: SIMD3(-0.5, 0, 1), flags: [])
        // A two-view point (no flags of its own) 1 m past the second contact, 2 m up.
        ledger.addPoint("Overhead", at: SIMD3(5, 2, 0.2), flags: [])
        ledger.addPoint("Inside", at: SIMD3(2, 2, 0.2), flags: [])

        for quantity in [MeasuredQuantity.gapToWall, .heightAboveGround] {
            #expect(ledger.warnings(from: "Before", to: .wall("W1"), referenceWall: nil, compared: quantity) == [.outsideWallContacts])
            #expect(ledger.warnings(from: "Overhead", to: .wall("W1"), referenceWall: nil, compared: quantity) == [.outsideWallContacts])
            #expect(ledger.warnings(from: "Inside", to: .wall("W1"), referenceWall: nil, compared: quantity) == [])
        }
    }

    @Test mutating func `a quantity that does not apply is not saved`() {
        ledger.addPoint("A", at: SIMD3(1, 0, 2), flags: [])
        ledger.addPoint("B", at: SIMD3(3, 0, 2), flags: [])
        #expect(ledger.saveMeasurement("M1", from: "A", to: .point("B"), referenceWall: nil, compared: .alongWall) == nil)
        #expect(ledger.savedWarnings("M1") == nil)
    }
}
