import CoreGraphics
import HouseScanKit
import Testing

@Suite struct OpenCameraDirectionTests {
    private static let camera = CGSize(width: 402, height: 874)
    private static let window = CGRect(x: 24, y: 550, width: 354, height: 230)

    /// The card covers this spot, even though it is below the full camera's center. Measuring
    /// from that center used to point down, farther behind the card (review of #241).
    @Test func aSpotBelowCameraCenterButAboveTheWindowPointsUp() throws {
        let spot = CGPoint(x: Self.camera.width / 2, y: 500)
        #expect(spot.y > Self.camera.height / 2)
        #expect(spot.y < Self.window.minY)
        let direction = try #require(OpenCameraDirection.toward(spot, in: Self.window, fallback: CGVector(dx: 0, dy: 1)))
        #expect(direction == CGVector(dx: 0, dy: -1))
    }

    @Test func aSpotAtCameraCenterUnderTheCardStillHasADirection() {
        let spot = CGPoint(x: Self.camera.width / 2, y: Self.camera.height / 2)
        #expect(OpenCameraDirection.toward(spot, in: Self.window, fallback: nil) == CGVector(dx: 0, dy: -1))
    }

    @Test func aSpotUnderDonePointsDown() {
        let spot = CGPoint(x: Self.window.midX, y: 810)
        #expect(OpenCameraDirection.toward(spot, in: Self.window, fallback: nil) == CGVector(dx: 0, dy: 1))
    }

    @Test(arguments: [CGPoint(x: 10, y: 665), CGPoint(x: 390, y: 665)])
    func aSpotBesideTheWindowPointsToItsOwnSide(spot: CGPoint) {
        let expected: CGFloat = spot.x < Self.window.midX ? -1 : 1
        #expect(OpenCameraDirection.toward(spot, in: Self.window, fallback: nil) == CGVector(dx: expected, dy: 0))
    }

    @Test func aVisibleSpotNeedsNoArrow() {
        #expect(OpenCameraDirection.toward(CGPoint(x: 201, y: 600), in: Self.window, fallback: CGVector(dx: 1, dy: 0)) == nil)
    }

    @Test func aSpotBehindTheCameraKeepsTheCameraDirection() {
        let direction = CGVector(dx: -1, dy: 0)
        #expect(OpenCameraDirection.toward(nil, in: Self.window, fallback: direction) == direction)
        #expect(OpenCameraDirection.toward(nil, in: Self.window, fallback: nil) == nil)
    }
}
