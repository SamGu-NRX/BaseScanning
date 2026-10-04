import Foundation
import HouseScanKit
import simd
import Testing

/// Two ARFrames that differ in pose, intrinsics and image size, and a meter hit both can see.
enum TwoFrames {
    static let hit = SIMD3<Float>(0.4, 1.2, -2)

    static let a = MeterTapFrame(
        t: 10, cameraToWorld: pose(x: 0, y: 1.5, z: 0, yawDegrees: 0), intrinsics: SIMD4(1445, 1445, 960, 720), width: 1920, height: 1440,
        tracking: .normal)
    static let b = MeterTapFrame(
        t: 10.033, cameraToWorld: pose(x: 0.3, y: 1.4, z: 0.1, yawDegrees: 10), intrinsics: SIMD4(1001, 1003, 641, 479), width: 1280,
        height: 960, tracking: .normal)

    static func pose(x: Float, y: Float, z: Float, yawDegrees: Float) -> simd_float4x4 {
        let r = yawDegrees * .pi / 180
        return simd_float4x4(
            SIMD4(cos(r), 0, -sin(r), 0), SIMD4(0, 1, 0, 0), SIMD4(sin(r), 0, cos(r), 0), SIMD4(x, y, z, 1))
    }

    static func columns(_ m: simd_float4x4) -> [Double] {
        [m.columns.0, m.columns.1, m.columns.2, m.columns.3].flatMap { [Double($0.x), Double($0.y), Double($0.z), Double($0.w)] }
    }

    /// How far `point` lies from the ray, meters.
    static func miss(_ ray: (origin: [Double], direction: [Double]), _ point: SIMD3<Float>) -> Double {
        let o = SIMD3(ray.origin[0], ray.origin[1], ray.origin[2]), d = simd_normalize(SIMD3(ray.direction[0], ray.direction[1], ray.direction[2]))
        let v = SIMD3<Double>(point) - o
        return simd_length(v - simd_dot(v, d) * d)
    }

    static func ray(_ frame: MeterTapFrame, pixel: SIMD2<Double>) -> (origin: [Double], direction: [Double]) {
        let k = frame.intrinsics
        return Packet04Check.ray(pose: columns(frame.cameraToWorld), intrinsics: [k.x, k.y, k.z, k.w].map(Double.init), pixel: [pixel.x, pixel.y])
    }
}

@Suite struct MeterTapFrameTests {
    func observation(_ frame: MeterTapFrame) throws -> TapObservation {
        try frame.observation(hit: TwoFrames.hit, jpeg: { Data([0xFF]) }).get()
    }

    /// The tap carries one frame's time, pose, intrinsics and image size, and its pixel is the hit
    /// seen through that frame's camera: the ray rebuilt the way the packet rebuilds it reaches the
    /// hit. The same pixel with the other frame's camera, or this frame's pose with the other's
    /// intrinsics, misses it by centimeters, so pairing frames would fail this test.
    @Test func thePixelAndRayComeFromTheSnapshotsOwnCamera() throws {
        let a = try observation(TwoFrames.a), b = try observation(TwoFrames.b)
        #expect(a.t == TwoFrames.a.t && a.cameraToWorld == TwoFrames.a.cameraToWorld && a.intrinsics == TwoFrames.a.intrinsics)
        #expect(a.width == 1920 && a.height == 1440)
        #expect(TwoFrames.miss(TwoFrames.ray(TwoFrames.a, pixel: a.pixel), TwoFrames.hit) < 1e-4)
        #expect(TwoFrames.miss(TwoFrames.ray(TwoFrames.b, pixel: b.pixel), TwoFrames.hit) < 1e-4)
        #expect(simd_distance(a.pixel, b.pixel) > 50, "the two frames should see the hit at different pixels")
        // Mixed: B's camera with A's pixel, and A's pose with B's intrinsics.
        #expect(TwoFrames.miss(TwoFrames.ray(TwoFrames.b, pixel: a.pixel), TwoFrames.hit) > 0.05)
        var mixed = TwoFrames.a
        mixed.intrinsics = TwoFrames.b.intrinsics
        #expect(TwoFrames.miss(TwoFrames.ray(mixed, pixel: a.pixel), TwoFrames.hit) > 0.05)
    }

    @Test func aFrameWithoutNormalTrackingGivesNoTap() {
        for tracking in [PacketTracking.limited(.relocalizing), .limited(nil), .notAvailable] {
            var frame = TwoFrames.a
            frame.tracking = tracking
            #expect(frame.observation(hit: TwoFrames.hit, jpeg: { Data() }).failureValue == .trackingLimited)
        }
    }

    @Test func aHitBehindTheCameraOrOutsideTheImageGivesNoTap() {
        // Behind: the camera looks along -z from z = 0.
        #expect(TwoFrames.a.observation(hit: SIMD3(0.4, 1.2, 2), jpeg: { Data() }).failureValue == .hitOutsideImage)
        // In front, far to the side: past the image's right edge.
        #expect(TwoFrames.a.observation(hit: SIMD3(5, 1.2, -2), jpeg: { Data() }).failureValue == .hitOutsideImage)
    }

    @Test func theHitRecordsItsDistanceFromTheSnapshotsCamera() {
        let hit = TwoFrames.a.tapHit(TwoFrames.hit, estimatedPlane: true)
        #expect(hit.target == "estimatedPlane" && hit.alignment == "vertical")
        #expect(abs((hit.distance ?? 0) - Double(simd_distance(TwoFrames.hit, SIMD3(0, 1.5, 0)))) < 1e-6)
        #expect(TwoFrames.a.tapHit(TwoFrames.hit, estimatedPlane: false).target == "existingPlaneGeometry")
    }
}

extension Result {
    var failureValue: Failure? {
        if case .failure(let failure) = self { return failure }
        return nil
    }
}

/// The meter tap through the controller and the real coordinator, against the in-process fixture.
@Suite(.serialized) @MainActor struct MeterTapControllerTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "meter-tap-\(UUID().uuidString)")
    var fixture: NativeCaptureFixture { NativeCaptureFixture(folder: root.appending(path: "store")) }

    func controller(_ http: FixtureCaptureHTTP) -> PhotoProcessingController {
        var environment = NativeCaptureFixture.environment(endpoint: FixtureCaptureHTTP.base, http: http, captures: root.appending(path: "Captures"))
        environment.policy.firstDelay = 0.01
        environment.policy.maxDelay = 0.02
        return PhotoProcessingController(setup: .ready(environment, standIn: true))
    }

    func begin(_ controller: PhotoProcessingController, scan: String = "scan-1", recording: String = "rec-1") -> ScanContext {
        let context = ScanContext(scanID: scan, profile: controller.profile, recordingSessionID: recording)
        controller.beginScan(context, recording: fixture.recording)
        return context
    }

    /// The fixture's frame one second in, as the snapshot would read it, with its image encoded.
    func snapshotTap() throws -> (TapObservation, Packet04.TapHit) {
        let t = fixture.start + 1
        let frame = MeterTapFrame(
            t: t, cameraToWorld: fixture.pose(t), intrinsics: NativeCaptureFixture.k, width: NativeCaptureFixture.width,
            height: NativeCaptureFixture.height, tracking: .normal)
        let jpeg = try Data(contentsOf: try fixture.jpeg(named: "tap-\(t)"))
        return (try frame.observation(hit: NativeCaptureFixture.meter, jpeg: { jpeg }).get(), frame.tapHit(NativeCaptureFixture.meter, estimatedPlane: true))
    }

    func until(_ seconds: Double = 30, _ condition: () -> Bool) async throws -> Bool {
        let deadline = ContinuousClock.now + .seconds(seconds)
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
        return condition()
    }

    /// The forwarded tap is sealed with a ray, rebuilt from its keyframe's pose and intrinsics,
    /// that reaches the hit within the server's replay tolerance; the packet passes its checks, and
    /// every request went to the in-process fixture.
    @Test func aForwardedTapIsSealedWithARayThroughTheHit() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        let scan = begin(controller)
        controller.answerConsent(true)
        let (tap, hit) = try snapshotTap()
        controller.meterTapped(tap, hit: hit, scan: scan)
        #expect(controller.meterTap == .forwarded)
        controller.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        for i in 0..<5 { controller.kept(try fixture.photo(at: fixture.start + 3 + Double(i) * 1.5)) }
        await controller.settle()
        controller.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        try #require(try await until { controller.status?.isFinal == true }, "\(String(describing: controller.status))")
        let folder = try #require(controller.captureFolder)
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: Data(contentsOf: folder.appending(path: "packet.json")))
        #expect(Packet04Check.problems(packet, folder: folder).isEmpty)
        let sealed = try #require(packet.taps?.first)
        #expect(sealed.label == "meter" && sealed.time == tap.t)
        let toHit = simd_normalize(SIMD3<Double>(NativeCaptureFixture.meter) - SIMD3(sealed.rayOrigin[0], sealed.rayOrigin[1], sealed.rayOrigin[2]))
        let error = zip([toHit.x, toHit.y, toHit.z], sealed.rayDirection).map { abs($0 - $1) }.max() ?? .infinity
        #expect(error <= Packet04Check.tapReplayTolerance, "ray misses the hit by \(error)")
        #expect(http.routes.allSatisfy { $0.hasPrefix("POST captures") || $0.hasPrefix("GET captures") || $0 == "PUT upload" })
        controller.endScan(recording: fixture.recording)
    }

    /// After a yes, a failed tap ends the packet as not prepared at once, and nothing more of it
    /// is sealed or finalized.
    @Test(arguments: MeterTapFailure.allCases)
    func aFailedTapAfterAYesEndsThePacketNotPrepared(reason: MeterTapFailure) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        let scan = begin(controller)
        controller.answerConsent(true)
        controller.meterTapFailed(reason, scan: scan)
        #expect(controller.meterTap == .failed(reason))
        #expect(controller.status?.stage == .ended(.notPrepared))
        controller.kept(try fixture.photo(at: fixture.start + 3))
        controller.captureEnded(acceptedCloseUpAt: nil)
        try await Task.sleep(for: .milliseconds(500))
        #expect(controller.status?.stage == .ended(.notPrepared))
        #expect(!http.routes.contains("POST captures/finalize"))
        controller.endScan(recording: fixture.recording)
    }

    /// Before an answer, a failed tap is kept: a later yes ends the packet as not prepared, and a
    /// no keeps its own end.
    @Test func aFailedTapBeforeAnAnswerIsKeptForIt() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let yes = controller(FixtureCaptureHTTP(answer: .candidate))
        let scan = begin(yes)
        yes.meterTapFailed(.encodeFailed, scan: scan)
        #expect(yes.status?.stage == .capturing(sent: 0))
        yes.answerConsent(true)
        #expect(yes.status?.stage == .ended(.notPrepared))
        yes.endScan(recording: fixture.recording)

        let no = controller(FixtureCaptureHTTP(answer: .candidate))
        let other = begin(no, scan: "scan-2")
        no.meterTapFailed(.noFrame, scan: other)
        no.answerConsent(false)
        no.captureEnded(acceptedCloseUpAt: nil)
        #expect(no.status?.stage == .ended(.consentNotGiven))
        no.endScan(recording: fixture.recording)
    }

    /// A tap result that arrives after a world reset or Start over belongs to a packet that no
    /// longer exists: it is neither forwarded nor allowed to end the new packet.
    @Test func aLateTapAfterAWorldResetOrStartOverIsDropped() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let controller = controller(FixtureCaptureHTTP(answer: .candidate))
        let first = begin(controller)
        controller.answerConsent(true)
        let (tap, hit) = try snapshotTap()

        let reset = first.inNewWorld(recordingSessionID: "rec-2")
        controller.worldReset(reset, recording: fixture.recording)
        controller.meterTapFailed(.encodeFailed, scan: first)
        controller.meterTapped(tap, hit: hit, scan: first)
        #expect(controller.meterTap == .none)
        #expect(controller.status?.stage == .capturing(sent: 0))

        controller.endScan(recording: fixture.recording)
        let next = begin(controller, scan: "scan-2", recording: "rec-3")
        controller.answerConsent(true)
        controller.meterTapFailed(.trackingLimited, scan: reset)
        controller.meterTapped(tap, hit: hit, scan: first)
        #expect(controller.meterTap == .none)
        #expect(controller.status?.stage == .capturing(sent: 0))
        #expect(controller.context == next)
        controller.endScan(recording: fixture.recording)
    }

    /// One tap per packet: a second result for the same packet changes nothing.
    @Test func onlyThePacketsFirstTapResultCounts() throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let controller = controller(FixtureCaptureHTTP(answer: .candidate))
        let scan = begin(controller)
        let (tap, hit) = try snapshotTap()
        controller.meterTapped(tap, hit: hit, scan: scan)
        controller.meterTapFailed(.encodeFailed, scan: scan)
        #expect(controller.meterTap == .forwarded)
        controller.endScan(recording: fixture.recording)
    }
}
