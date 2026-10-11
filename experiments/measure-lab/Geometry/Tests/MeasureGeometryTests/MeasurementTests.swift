import Testing
@testable import MeasureGeometry

struct MeasurementTests {
    @Test func `separation between two points`() {
        // Δ = (3, 4, −4): straight √41, horizontal √(3² + (−4)²) = 5, and rise is Δy = 4.
        let separation = PointSeparation(from: SIMD3(1, 2, 3), to: SIMD3(4, 6, -1))
        #expect(isClose(separation.straight, 41.0.squareRoot()))
        #expect(separation.horizontal == 5)
        #expect(separation.rise == 4)
        #expect(PointSeparation(from: SIMD3(4, 6, -1), to: SIMD3(1, 2, 3)).rise == -4)
    }

    @Test func `tape comparison reports app minus tape`() {
        // Tape 6 ft 3.5 in = 75.5 in = 1.9177 m; the app read 1.95 m.
        let comparison = TapeComparison(measured: 1.95, tape: 1.9177)
        #expect(isClose(comparison.error, 0.0323))
        #expect(isClose(comparison.errorInches, 0.0323 / 0.0254))
        #expect(TapeComparison(measured: 1.0, tape: 1.0254).errorInches < 0)
    }
}

struct MeasuredValuesTests {
    let wall: Wall

    init() throws {
        wall = try Wall(contact1: SIMD3(0, 0, 0), contact2: SIMD3(4, 0, 0), cameraPosition: SIMD3(2, 1.5, 3))
    }

    @Test func `point to point without a wall`() {
        let values = measuredValues(from: SIMD3(1, 2, 3), to: .point(SIMD3(4, 6, -1)))
        #expect(values.count == 3)
        #expect(isClose(values[.straight] ?? .nan, 41.0.squareRoot()))
        #expect(values[.horizontal] == 5)
        #expect(values[.vertical] == 4)
    }

    @Test func `point to point along a wall`() {
        // Along-wall distance is the difference in x for a wall on the x axis, sign dropped.
        let values = measuredValues(from: SIMD3(3.5, 0, 2), to: .point(SIMD3(1, 1, 0.5)), referenceWall: wall)
        #expect(values[.alongWall] == 2.5)
        #expect(values[.vertical] == 1)
    }

    @Test func `height above ground keeps its sign`() throws {
        // Ground rises 0.4 m over 4 m, so at along-wall 2 it is at y = 0.2; y = −0.8 is 1 m below.
        let sloped = try Wall(contact1: SIMD3(0, 0, 0), contact2: SIMD3(4, 0.4, 0), cameraPosition: SIMD3(2, 1.5, 3))
        let values = measuredValues(from: SIMD3(2, -0.8, 0), to: .wall(sloped))
        #expect(isClose(values[.heightAboveGround] ?? .nan, -1))
    }

    @Test func `point to wall gives the facing gap and height`() {
        // A fence post 1.2 m out from the wall, 0.9 m up.
        let values = measuredValues(from: SIMD3(2, 0.9, 1.2), to: .wall(wall))
        #expect(values.count == 2)
        #expect(isClose(values[.gapToWall] ?? .nan, 1.2))
        #expect(isClose(values[.heightAboveGround] ?? .nan, 0.9))
    }
}

/// `wall` runs from x = 0 to x = 4 along z = 0.
struct BeyondContactsTests {
    let wall: Wall

    init() throws {
        wall = try Wall(contact1: SIMD3(0, 0, 0), contact2: SIMD3(4, 0, 0), cameraPosition: SIMD3(2, 1.5, 3))
    }

    @Test(arguments: [MeasuredQuantity.gapToWall, .heightAboveGround])
    func `point-to-wall quantities past either end are beyond the contacts`(quantity: MeasuredQuantity) {
        #expect(readsWallBeyondContacts(from: SIMD3(-0.5, 1, 1), to: .wall(wall), referenceWall: nil, compared: quantity))
        #expect(readsWallBeyondContacts(from: SIMD3(4.5, 1, 1), to: .wall(wall), referenceWall: nil, compared: quantity))
        #expect(!readsWallBeyondContacts(from: SIMD3(2, 1, 1), to: .wall(wall), referenceWall: nil, compared: quantity))
    }

    @Test func `along-wall distance checks both points against the reference wall`() {
        let inside = SIMD3<Double>(1, 0, 2)
        let past = readsWallBeyondContacts(from: inside, to: .point(SIMD3(5, 0, 2)), referenceWall: wall, compared: .alongWall)
        let before = readsWallBeyondContacts(from: SIMD3(-1, 0, 2), to: .point(inside), referenceWall: wall, compared: .alongWall)
        let within = readsWallBeyondContacts(from: inside, to: .point(SIMD3(3, 0, 2)), referenceWall: wall, compared: .alongWall)
        #expect(past)
        #expect(before)
        #expect(!within)
    }

    @Test(arguments: [MeasuredQuantity.straight, .horizontal, .vertical])
    func `distances that use no wall are never beyond it`(quantity: MeasuredQuantity) {
        #expect(!readsWallBeyondContacts(from: SIMD3(-3, 0, 2), to: .point(SIMD3(9, 0, 2)), referenceWall: wall, compared: quantity))
    }

    @Test func `the defining contacts themselves are within`() throws {
        // For this wall, along(end) = run·run / |run| rounds 4.4e-16 m past length.
        let start = SIMD3<Double>(-1.9667586519159475, 0, -3.7980858426399475)
        let end = SIMD3<Double>(-5.331733838114573, 0.05, -5.225922527733432)
        let diagonal = try Wall(contact1: start, contact2: end, cameraPosition: SIMD3(0, 1.5, 3))
        #expect(diagonal.along(end) > diagonal.length)
        #expect(!readsWallBeyondContacts(from: start, to: .point(end), referenceWall: diagonal, compared: .alongWall))
        #expect(!readsWallBeyondContacts(from: end, to: .wall(diagonal), referenceWall: nil, compared: .heightAboveGround))
    }
}
