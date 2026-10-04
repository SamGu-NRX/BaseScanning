import Foundation
import HouseScanKit
import simd
import Synchronization
import Testing

/// A scan shaped like the app's live capture, for the coordinator: 1920 x 1440 sensor JPEGs, the
/// camera 2 m from a wall at 1.5 m height walking +x, the recorder's raw rows (a 60 Hz trajectory
/// with each frame's intrinsics beside it, and Core Motion's accelerometer and gyro at about
/// 100 Hz on their own jittered clocks), the meter tap's frame and pixel, and the meter close-up
/// from its own frame. Pattern images, no house; the packet says `source.kind: synthetic`.
struct NativeCaptureFixture: Sendable {
    static let width = 1920, height = 1440
    static let k = SIMD4<Float>(1445, 1445, 960, 720)
    static let meter = SIMD3<Float>(0.4, 1.2, -2)
    let start = 5000.0
    let end = 5012.0
    let folder: URL

    func pose(_ t: Double) -> simd_float4x4 {
        var m = matrix_identity_float4x4
        m.columns.3 = SIMD4(Float(t - start) * 0.1, 1.5, 0, 1)
        return m
    }

    /// Where `world` lands in the sensor image of the frame at `t`.
    func pixel(of world: SIMD3<Float>, at t: Double) -> SIMD2<Double> {
        let p = world - SIMD3(pose(t).columns.3.x, pose(t).columns.3.y, pose(t).columns.3.z)
        let depth = -p.z
        return SIMD2(Double(Self.k.z + Self.k.x * p.x / depth), Double(Self.k.w - Self.k.y * p.y / depth))
    }

    var recording: RecordingSource {
        let fixture = self
        return RecordingSource(start: { (fixture.start, Date(timeIntervalSince1970: 1_790_000_000)) }, rows: { fixture.rows })
    }

    var rows: RecorderRows {
        var trajectory: [[Double]] = [], intrinsics: [[Double]] = []
        for i in 0...Int((end - start) * 60) {
            let t = start + Double(i) / 60
            let m = pose(t)
            let columns: [SIMD4<Float>] = [m.columns.0, m.columns.1, m.columns.2, m.columns.3]
            let values: [Double] = columns.flatMap { (c: SIMD4<Float>) -> [Double] in [Double(c.x), Double(c.y), Double(c.z), Double(c.w)] }
            trajectory.append([t, 0, 0] + values)
            intrinsics.append([t, 1445, 1445, 960, 720, 1920, 1440])
        }
        // Two sensors on their own clocks: neither shares a timestamp with the other.
        var accel: [[Double]] = [], gyro: [[Double]] = []
        for i in 0..<Int((end - start) * 100) {
            let jitter = Double((i * 7919) % 11) * 0.0001
            accel.append([start + Double(i) * 0.01 + jitter, 0.01, -0.99, 0.02])
            gyro.append([start + Double(i) * 0.01 + 0.0037 - jitter, 0.001, -0.002, 0.0005])
        }
        return RecorderRows(trajectory: trajectory, intrinsics: intrinsics, accelerometer: accel, gyroscope: gyro)
    }

    func jpeg(named name: String) throws -> URL {
        let url = folder.appending(path: "\(name).jpg")
        if !FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try makeJPEG(width: Self.width, height: Self.height, at: url)
        }
        return url
    }

    func photo(at t: Double, purpose: String? = nil) throws -> KeptPhoto {
        KeptPhoto(
            t: t, cameraToWorld: pose(t), cameraIntrinsics: Self.k, cameraImageSize: SIMD2(1920, 1440), width: Self.width, height: Self.height,
            tracking: .normal, exposure: .init(duration: 0.004, offset: 0, iso: 64, fNumber: 1.6), jpeg: try jpeg(named: "frame-\(t)"), purpose: purpose)
    }

    /// The raycast ran on the frame at `t`; the frame the app records is two frames newer, as
    /// ARKit's current frame can be, and the tap is re-aimed at the hit in that frame.
    func tap(at t: Double) throws -> (TapObservation, Packet04.TapHit) {
        let data = try Data(contentsOf: try jpeg(named: "tap-\(t)"))
        let recorded = t + 2.0 / 60
        let camera = pose(recorded).columns.3
        let stale = TapObservation(
            t: recorded, cameraToWorld: pose(recorded), intrinsics: Self.k, width: Self.width, height: Self.height, tracking: .normal,
            pixel: pixel(of: Self.meter, at: t), jpeg: { data })
        let tap = try #require(stale.pointing(at: Self.meter))
        let hit = Packet04.TapHit(
            position: [Self.meter.x, Self.meter.y, Self.meter.z].map(Double.init), target: "estimatedPlane", alignment: "vertical",
            distance: Double(simd_distance(Self.meter, SIMD3(camera.x, camera.y, camera.z))))
        return (tap, hit)
    }

    static func environment(
        endpoint: URL, http: any CaptureHTTP, captures: URL, sends: Bool = true, eventsWait: Int = 0, log: @escaping @Sendable (String) -> Void = { _ in }
    ) -> CaptureSessionCoordinator.Environment {
        var policy = CaptureUploader.Policy()
        policy.eventsWait = eventsWait
        let device = CaptureAPI.Device(model: "iPhone15,4", systemVersion: "26.0", appVersion: "0.1.0 (1)")
        return .init(
            endpoint: endpoint, sends: sends, http: http, capturesFolder: captures,
            sessionInfo: { packetID, video in
                Packet04SessionInfo(
                    packetID: packetID, sessionID: packetID, source: .synthetic, appVersion: device.appVersion, deviceModel: device.model,
                    systemVersion: device.systemVersion, lidarAvailable: false, sceneDepthEnabled: false, meshReconstructionSupported: nil,
                    sceneReconstruction: nil, planeDetection: ["horizontal", "vertical"], videoWidth: Int(video.x), videoHeight: Int(video.y),
                    framesPerSecond: nil, timeZone: "America/Chicago")
            },
            device: device, tier: .arkit, policy: policy, log: log)
    }

    /// The whole scan through `coordinator`: the tap, the close-up, keyframes (one of them the
    /// close-up's own frame again), then the capture ends.
    @MainActor
    func run(_ coordinator: CaptureSessionCoordinator, consent: Bool? = true, beforeEnd: @MainActor () async -> Void = {}) async throws {
        coordinator.begin(recording: recording)
        // The integration build asks before the scan starts.
        if let consent { coordinator.answerConsent(consent) }
        let (tap, hit) = try self.tap(at: start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.kept(try photo(at: start + 2.5, purpose: "meter_close"))
        for i in 0..<5 { coordinator.kept(try photo(at: start + 3 + Double(i) * 1.5)) }
        // The app keeps the close-up's frame twice when it is also a walk frame.
        coordinator.kept(try photo(at: start + 2.5))
        await coordinator.settle()
        await beforeEnd()
        coordinator.captureEnded(acceptedCloseUpAt: start + 2.5)
        coordinator.captureEnded(acceptedCloseUpAt: start + 2.5)
        await coordinator.settle()
    }
}

@Suite(.serialized) @MainActor struct CaptureSessionCoordinatorTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "coordinator-\(UUID().uuidString)")
    var fixture: NativeCaptureFixture { NativeCaptureFixture(folder: root.appending(path: "store")) }
    var captures: URL { root.appending(path: "Captures") }

    @Test func nativeScanExportsAValidPacketAndUploadsItDuringTheScan() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures))
        let committedBeforeEnd = Mutex(0)
        try await fixture.run(coordinator) {
            let uploader = coordinator.session!.uploader!
            committedBeforeEnd.withLock { $0 = 0 }
            let count = await uploader.snapshot.committedCount
            committedBeforeEnd.withLock { $0 = count }
        }
        let session = try #require(coordinator.session)
        let state = await session.uploader!.snapshot
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: try #require(state.packet))

        #expect(Packet04Check.problems(packet, folder: session.folder).isEmpty)
        // Tap frame, close-up frame and five walk frames; the repeated close-up frame is not a new keyframe.
        #expect(packet.keyframes.count == 7)
        #expect(packet.keyframes.map(\.reason) == ["tap", "still", "auto", "auto", "auto", "auto", "auto"])
        #expect(packet.stills?.map(\.purpose) == ["meter_close"])
        #expect(packet.scaleReference.meterCloseUp.still == "meter_close")
        let tap = try #require(packet.taps?.first)
        #expect(tap.label == "meter" && tap.keyframe == "k00001" && tap.epoch == "e1")
        // The ray through the tap pixel reaches the meter the raycast hit.
        let toMeter = simd_normalize(SIMD3(tap.hit!.position[0] - tap.rayOrigin[0], tap.hit!.position[1] - tap.rayOrigin[1], tap.hit!.position[2] - tap.rayOrigin[2]))
        #expect(simd_distance(toMeter, SIMD3(tap.rayDirection[0], tap.rayDirection[1], tap.rayDirection[2])) < 1e-5)
        // Raw world poses, straight from the frames.
        #expect(packet.keyframes[2].pose[12...14].map { $0 } == [0.3, 1.5, 0])
        // Both sensors' own times travel beside the proposed pairing.
        #expect(packet.ext?["imuRawPairingStatus"]?.hasPrefix("proposal") == true)
        #expect(Set(packet.files.filter { $0.role == .stream }.map(\.path))
            == ["streams/arkit_poses.csv.gz", "streams/imu_raw.csv.gz", "streams/accelerometer_raw.csv.gz", "streams/gyroscope_raw.csv.gz"])

        #expect(committedBeforeEnd.withLock { $0 } == packet.files.count - 4, "every image was received before the scan ended")
        #expect(state.end == .finished(status: "manual_review"))
        #expect(server.requests("POST captures").count == 1)
        #expect(server.requests("POST captures/finalize").count == 1)

        if let dir = ProcessInfo.processInfo.environment["HOUSESCAN_EXPORT_EVIDENCE_DIR"] {
            let target = URL(fileURLWithPath: dir).appending(path: "native-export-local")
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: session.folder, to: target)
        }
    }

    @Test func onlyTheIntegrationBuildRecordsAndOnlyAnAllowedBuildSends() {
        let url = "https://capture.example.com/v1"
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "NO", endpoint: url, sendDeviceData: "YES") == .off("not the integration build"))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: nil, endpoint: url, sendDeviceData: "YES") == .off("not the integration build"))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "$(HOUSESCAN_INTEGRATION_BUILD)", endpoint: url, sendDeviceData: "YES") == .off("not the integration build"))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: "", sendDeviceData: "YES") == .off("no capture endpoint in this build"))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: url, sendDeviceData: "NO") == .recordOnly(URL(string: url)!))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: url, sendDeviceData: nil) == .recordOnly(URL(string: url)!))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: url, sendDeviceData: "YES") == .send(URL(string: url)!))
        // A replay goes only to a receiver on this machine, only when asked, whatever the device switch says.
        let local = "http://127.0.0.1:8765/v1"
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: local, sendDeviceData: "NO", source: .replay(sendToLocalReceiver: true)) == .send(URL(string: local)!))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: local, sendDeviceData: "YES", source: .replay(sendToLocalReceiver: false)) == .recordOnly(URL(string: local)!))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "YES", endpoint: url, sendDeviceData: "YES", source: .replay(sendToLocalReceiver: true)) == .recordOnly(URL(string: url)!))
        #expect(CaptureIntegrationMode.resolve(integrationBuild: "NO", endpoint: local, sendDeviceData: "YES", source: .replay(sendToLocalReceiver: true)) == .off("not the integration build"))
    }

    /// A build that may not send device data records the capture on the phone and never sends it,
    /// even with a yes.
    @Test func aRecordOnlyBuildKeepsTheCaptureOnThePhone() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures, sends: false))
        try await fixture.run(coordinator)
        #expect(coordinator.needsConsent == false)
        coordinator.answerConsent(true)
        await coordinator.settle()
        let session = try #require(coordinator.session)
        #expect(session.uploader == nil)
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: Data(contentsOf: session.folder.appending(path: "packet.json")))
        #expect(Packet04Check.problems(packet, folder: session.folder).isEmpty)
        #expect(server.state.withLock { $0.log.isEmpty })
    }

    /// No answer or a no sends nothing; the next scan asks again. A yes before the scan sends
    /// during it, and a world reset inside the scan keeps that yes for the new world's packet.
    @Test func consentIsPerScanAndOnlyAYesSends() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let environment = NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures)

        let unanswered = CaptureSessionCoordinator(environment: environment)
        try await fixture.run(unanswered, consent: nil)
        #expect(unanswered.needsConsent)
        #expect(unanswered.session?.uploader == nil)

        let declining = CaptureSessionCoordinator(environment: environment)
        try await fixture.run(declining, consent: false)
        #expect(declining.session?.uploader == nil)
        #expect(server.state.withLock { $0.log.isEmpty })
        declining.newWorld("start over", recording: fixture.recording, newScan: true)
        #expect(declining.consent == nil && declining.needsConsent)

        let agreeing = CaptureSessionCoordinator(environment: environment)
        agreeing.begin(recording: fixture.recording)
        agreeing.answerConsent(true)
        let firstWorld = try #require(agreeing.session)
        agreeing.newWorld("world reset", recording: fixture.recording, newScan: false)
        #expect(agreeing.consent == true)
        let secondWorld = try #require(agreeing.session)
        #expect(secondWorld.uploader != nil && secondWorld.packetID != firstWorld.packetID)
        agreeing.newWorld("start over", recording: fixture.recording, newScan: true)
        #expect(agreeing.consent == nil)
        #expect(agreeing.session?.uploader == nil)
    }

    /// After a relaunch a frozen capture resumes only with the yes it recorded and only toward the
    /// endpoint it was created on.
    @Test func resumeNeedsTheCapturesYesAndTheSameEndpoint() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let original = try LoopbackCaptureAPI()
        let other = try LoopbackCaptureAPI()
        // A capture frozen on the phone for `original` whose upload never started.
        let folder = captures.appending(path: "frozen")
        let capture = try SyntheticCapture04(folder: folder)
        let images = try await capture.sealImages()
        let (streams, packet) = try await capture.finish()
        var saved = CaptureUploadState(
            attemptID: "old-process", packetID: capture.producer.info.packetID,
            createBody: try JSONEncoder().encode(CaptureAPI.CreateRequest(packetId: capture.producer.info.packetID, tier: .arkit, device: .init(model: "m", systemVersion: "s", appVersion: "a"))))
        saved.destination = original.base.absoluteString
        // First without a recorded yes: never resumed.
        for (i, file) in (images + streams).enumerated() { saved.files[file.path] = .init(sealed: file, sequence: i) }
        saved.packet = packet
        try saved.save(to: CaptureUploader.stateURL(in: folder))
        let http = URLSessionCaptureHTTP.ephemeral(timeout: 10)

        #expect(CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(endpoint: original.base, http: http, captures: captures))
            .resumeSealedCaptures().isEmpty)
        saved.consent = .init(grantedAt: Date(), destination: original.base.absoluteString)
        try saved.save(to: CaptureUploader.stateURL(in: folder))
        #expect(CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(endpoint: other.base, http: http, captures: captures))
            .resumeSealedCaptures().isEmpty)
        #expect(CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(endpoint: original.base, http: http, captures: captures, sends: false))
            .resumeSealedCaptures().isEmpty)
        try await Task.sleep(for: .milliseconds(200))
        #expect(original.state.withLock { $0.log.isEmpty } && other.state.withLock { $0.log.isEmpty })

        let resumed = CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(endpoint: original.base, http: http, captures: captures))
            .resumeSealedCaptures()
        #expect(resumed.count == 1)
        for uploader in resumed { await uploader.kick(); await uploader.settled() }
        #expect(await resumed.first?.snapshot.end == .finished(status: "manual_review"))
        #expect(other.state.withLock { $0.log.isEmpty })
    }

    /// A refused close-up and its accepted retake, written over the same file as the app does:
    /// both stay sealed with their own pixels, and the scale reference names the accepted one,
    /// with its pixels and pose. A skipped close-up names none.
    @Test func onlyTheAcceptedCloseUpIsTheScaleReference() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        for accepted in [true, false] {
            let coordinator = CaptureSessionCoordinator(environment: NativeCaptureFixture.environment(
                endpoint: URL(string: "https://capture.invalid/v1")!, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures, sends: false))
            coordinator.begin(recording: fixture.recording)
            let source = fixture.folder.appending(path: "meter_close.jpg")
            try FileManager.default.createDirectory(at: fixture.folder, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: source)
            try makeJPEG(width: NativeCaptureFixture.width, height: NativeCaptureFixture.height, at: source, shift: 7)
            let refusedBytes = try Data(contentsOf: source)
            // Its depth too lives in files the retake overwrites: 4 x 3 meters and confidence.
            let depthFile = fixture.folder.appending(path: "meter_close.f32"), confidenceFile = fixture.folder.appending(path: "meter_close.conf.u8")
            func writeDepth(_ meters: Float, _ confidence: UInt8) throws {
                try PacketFiles.depthData(meters: [Float](repeating: meters, count: 12)).write(to: depthFile)
                try Data(repeating: confidence, count: 12).write(to: confidenceFile)
            }
            try writeDepth(1.5, 2)
            func closeUp(at t: Double) -> KeptPhoto {
                KeptPhoto(
                    t: t, cameraToWorld: fixture.pose(t), cameraIntrinsics: NativeCaptureFixture.k, cameraImageSize: SIMD2(1920, 1440),
                    width: NativeCaptureFixture.width, height: NativeCaptureFixture.height, tracking: .normal, exposure: nil, jpeg: source, purpose: "meter_close",
                    depth: {
                        guard let map = try? Data(contentsOf: depthFile), let confidence = try? Data(contentsOf: confidenceFile) else { return nil }
                        return Packet04Depth(meters: PacketFiles.floats(littleEndian: map), confidence: [UInt8](confidence), width: 4, height: 3)
                    })
            }
            coordinator.kept(closeUp(at: fixture.start + 2))
            // The retake overwrites the same files before the first shot's sealing has run.
            try FileManager.default.removeItem(at: source)
            try makeJPEG(width: NativeCaptureFixture.width, height: NativeCaptureFixture.height, at: source, shift: 101)
            try writeDepth(0.9, 1)
            let retakeBytes = try Data(contentsOf: source)
            coordinator.kept(closeUp(at: fixture.start + 4))
            coordinator.captureEnded(acceptedCloseUpAt: accepted ? fixture.start + 4 : nil)
            await coordinator.settle()

            let session = try #require(coordinator.session)
            let packet = try JSONDecoder().decode(Packet04.Packet.self, from: Data(contentsOf: session.folder.appending(path: "packet.json")))
            #expect(packet.stills?.map(\.id) == ["meter_close", "meter_close-2"])
            #expect(try Data(contentsOf: session.folder.appending(path: "stills/meter_close.jpg")) == refusedBytes)
            #expect(try Data(contentsOf: session.folder.appending(path: "stills/meter_close-2.jpg")) == retakeBytes)
            // Each shot keeps its own depth and confidence.
            #expect(try Data(contentsOf: session.folder.appending(path: "keyframes/k00001.depth.f32")) == PacketFiles.depthData(meters: [Float](repeating: 1.5, count: 12)))
            #expect(try Data(contentsOf: session.folder.appending(path: "keyframes/k00001.confidence.u8")) == Data(repeating: 2, count: 12))
            #expect(try Data(contentsOf: session.folder.appending(path: "keyframes/k00002.depth.f32")) == PacketFiles.depthData(meters: [Float](repeating: 0.9, count: 12)))
            #expect(Packet04Check.problems(packet, folder: session.folder).isEmpty)
            if accepted {
                #expect(packet.scaleReference.meterCloseUp.still == "meter_close-2")
                let frame = try #require(packet.keyframes.first { $0.id == packet.stills?.last?.keyframe })
                #expect(frame.timestamp == fixture.start + 4 && frame.pose[12] == 0.4)
            } else {
                #expect(packet.scaleReference.meterCloseUp == .notCaptured)
            }
        }
    }

    /// A world reset while the first world's photos are still going up: that packet is abandoned,
    /// the new world gets its own packet id, and nothing kept after the reset joins the old one.
    @Test func aWorldResetStartsANewPacketAndDropsTheOld() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/files"] }
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: captures))
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        let first = try #require(coordinator.session)
        for _ in 0..<500 where server.requests("POST captures/files").isEmpty { try await Task.sleep(for: .milliseconds(10)) }

        coordinator.newWorld("world reset", recording: fixture.recording, newScan: false)
        coordinator.kept(try fixture.photo(at: fixture.start + 4))
        server.release("POST captures/files")
        server.state.withLock { $0.held = [] }
        await coordinator.settle()
        await first.uploader!.settled()

        let second = try #require(coordinator.session)
        #expect(second.packetID != first.packetID)
        let old = await first.uploader!.snapshot
        #expect(old.end == .abandoned("world reset"))
        #expect(old.files.values.allSatisfy { $0.phase == .queued })
        #expect(await second.uploader!.snapshot.files.keys.sorted() == ["keyframes/k00001.jpg"])
        #expect(await second.uploader!.snapshot.committedCount == 1)
        let creates = server.requests("POST captures").compactMap { try? JSONDecoder().decode(CaptureAPI.CreateRequest.self, from: $0.body).packetId }
        #expect(Set(creates) == [first.packetID, second.packetID])
    }

    /// A reset that ends the session before its kept photos are sealed still deletes their staged
    /// copies, so no copy of a home photo outlives the packet it was kept for.
    @Test func aResetDeletesPhotosStagedForTheEndedSession() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures, sends: false))
        let staging = captures.appending(path: "staging")
        func staged() -> [String] { (try? FileManager.default.contentsOfDirectory(atPath: staging.path)) ?? [] }

        coordinator.begin(recording: fixture.recording)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        coordinator.kept(try fixture.photo(at: fixture.start + 3))
        #expect(staged().count == 2)
        // No suspension between keeping and the reset, so the queued work finds the session ended.
        coordinator.newWorld("start over", recording: fixture.recording, newScan: true)
        coordinator.kept(try fixture.photo(at: fixture.start + 4))
        await coordinator.settle()
        for _ in 0..<500 where !staged().isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        #expect(staged().isEmpty)
    }

    /// A no after a yes stops sending: the upload is abandoned, nothing kept later goes up, the
    /// packet is never finalized, and a second yes in the same scan does not start it again.
    @Test func aNoAfterAYesStopsSending() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures))
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        await coordinator.settle()
        let session = try #require(coordinator.session)
        let uploader = try #require(session.uploader)
        await uploader.settled()
        let putsBefore = server.requests("PUT upload").count
        #expect(putsBefore == 1)

        coordinator.answerConsent(false)
        coordinator.kept(try fixture.photo(at: fixture.start + 4))
        coordinator.answerConsent(true)
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.captureEnded(acceptedCloseUpAt: nil)
        await coordinator.settle()
        await uploader.settled()

        #expect(await uploader.snapshot.end == .abandoned("consent withdrawn"))
        #expect(coordinator.session === session && session.uploader == nil)
        #expect(server.requests("PUT upload").count == putsBefore)
        #expect(server.requests("POST captures").count == 1)
        #expect(server.requests("POST captures/finalize").isEmpty)
    }

    /// A withdrawal is saved before `answerConsent(false)` returns: a relaunch straight after it
    /// does not resume the sealed capture, even though the uploader has not saved its end yet.
    @Test func aWithdrawnCaptureIsNotResumedAfterARelaunch() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/finalize"] }
        let environment = NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures)
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        for i in 0..<3 { coordinator.kept(try fixture.photo(at: fixture.start + 3 + Double(i) * 1.5)) }
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        let session = try #require(coordinator.session)
        let uploader = try #require(session.uploader)
        let stateURL = CaptureUploader.stateURL(in: session.folder)
        for _ in 0..<500 where (try? CaptureUploadState.load(from: stateURL))?.packet == nil { try await Task.sleep(for: .milliseconds(10)) }
        #expect(try CaptureUploadState.load(from: stateURL).packet != nil)

        coordinator.answerConsent(false)
        // The same main-actor turn: the abandon task has not run yet.
        #expect(CaptureSessionCoordinator(environment: environment).resumeSealedCaptures().isEmpty)
        #expect(try CaptureUploader.resume(folder: session.folder, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10)) == nil)

        server.release("POST captures/finalize")
        server.state.withLock { $0.held = [] }
        // The withdrawal cancelled the loop; the abandon task ends the upload in its own time.
        for _ in 0..<500 where await uploader.snapshot.end == nil { try await Task.sleep(for: .milliseconds(10)) }
        #expect(await uploader.snapshot.end == .abandoned("consent withdrawn"))
    }

    /// A relaunch scan in the live process must not create a second sender for the sealed
    /// folder. On the old code B survives A's withdrawal and sends the remaining files.
    @Test func withdrawalCoversTheFolderDuringResumeDiscovery() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/finalize"] }
        let environment = NativeCaptureFixture.environment(
            endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: captures)
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        for i in 0..<3 { coordinator.kept(try fixture.photo(at: fixture.start + 3 + Double(i) * 1.5)) }
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        let uploader = try #require(coordinator.session?.uploader)
        try await waitUntilParked(server, "POST captures/finalize")

        let duplicates = CaptureSessionCoordinator(environment: environment).resumeSealedCaptures()
        for duplicate in duplicates { await duplicate.kick() }
        if !duplicates.isEmpty {
            for _ in 0..<500 where server.requests("POST captures/finalize").count < 2 {
                try await Task.sleep(for: .milliseconds(10))
            }
            try #require(server.requests("POST captures/finalize").count == 2)
        }
        #expect(duplicates.isEmpty)
        coordinator.answerConsent(false)
        let puts = server.requests("PUT upload").count
        let commits = server.requests("POST captures/files:commit").count
        server.state.withLock { $0.held = [] }
        server.release("POST captures/finalize")
        try #require(await CaptureUploaderTests.settles(uploader))
        for duplicate in duplicates { try #require(await CaptureUploaderTests.settles(duplicate)) }
        #expect(server.requests("PUT upload").count == puts)
        #expect(server.requests("POST captures/files:commit").count == commits)
        #expect(server.requests("GET captures/result").isEmpty)
        #expect(CaptureSessionCoordinator(environment: environment).resumeSealedCaptures().isEmpty)
    }

    @Test func droppingAnIdleCoordinatorReleasesFolderOwnership() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let environment = NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures)
        var coordinator: CaptureSessionCoordinator? = CaptureSessionCoordinator(environment: environment)
        coordinator?.begin(recording: fixture.recording)
        coordinator?.answerConsent(true)
        coordinator?.kept(try fixture.photo(at: fixture.start + 2))
        await coordinator?.settle()
        let folder = try #require(coordinator?.session?.folder)
        weak let oldSession = coordinator?.session
        weak let oldUploader = coordinator?.session?.uploader
        coordinator = nil
        for _ in 0..<500 where oldSession != nil || oldUploader != nil { try await Task.sleep(for: .milliseconds(10)) }
        try #require(oldSession == nil && oldUploader == nil)
        let resumed = try #require(try CaptureUploader.resume(folder: folder, base: server.base, http: environment.http))
        await resumed.kick()
        try #require(await CaptureUploaderTests.settles(resumed))
        #expect(server.requests("POST captures").count == 1)
    }

    @Test func anUnsavedWithdrawalIsReturnedToTheCaller() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/files"] }
        let environment = NativeCaptureFixture.environment(
            endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: captures)
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        let session = try #require(coordinator.session)
        let uploader = try #require(session.uploader)
        try await waitUntilParked(server, "POST captures/files")
        let stateURL = CaptureUploader.stateURL(in: session.folder)
        let before = try Data(contentsOf: stateURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: session.folder.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: session.folder.path) }

        let result = coordinator.answerConsent(false)
        guard case .failure(.notRecorded(let detail)) = result else {
            Issue.record("The caller must receive the persistence failure")
            server.state.withLock { $0.held = [] }
            server.release("POST captures/files")
            return
        }
        #expect(detail.contains("marker:") && detail.contains("state removal:"))
        #expect(try Data(contentsOf: stateURL) == before)
        // Repeated answers must not turn the failed withdrawal into a reported success.
        if case .success = coordinator.answerConsent(false) { Issue.record("Repeated no lost the failure") }
        if case .success = coordinator.answerConsent(true) { Issue.record("Later yes lost the failure") }
        #expect(session.uploader == nil)
        #expect(try CaptureUploader.resume(folder: session.folder, base: server.base, http: environment.http) == nil)
        #expect(CaptureSessionCoordinator(environment: environment).resumeSealedCaptures().isEmpty)
        server.state.withLock { $0.held = [] }
        server.release("POST captures/files")
        try #require(await CaptureUploaderTests.settles(uploader))
        #expect(server.requests("PUT upload").isEmpty)
        #expect(server.requests("POST captures/files:commit").isEmpty)
        // All writes still fail, so the app cannot promise the persisted yes was removed.
        #expect(try Data(contentsOf: stateURL) == before)
    }

    /// Waits, suspending, until the loopback has parked an answer for `route`.
    func waitUntilParked(_ server: LoopbackCaptureAPI, _ route: String) async throws {
        for _ in 0..<1000 where server.state.withLock({ $0.parked[route]?.isEmpty ?? true }) { try await Task.sleep(for: .milliseconds(10)) }
        #expect(server.state.withLock { !($0.parked[route]?.isEmpty ?? true) })
    }

    /// Blocks the main actor until `stateURL` holds something other than `before`, so nothing
    /// queued on the main actor (the abandon task a withdrawal schedules) can run meanwhile.
    func blockUntilSaved(_ stateURL: URL, differentFrom before: Data?) {
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline, (try? Data(contentsOf: stateURL)) == before { usleep(10_000) }
    }

    /// A reply that lands after the homeowner withdrew, but before the uploader is abandoned,
    /// can't save a capture a relaunch would resume, whether or not the withdrawal marker could
    /// be written. The transport lets the reply arrive even though the request's task was
    /// cancelled, as a background session does. The main actor is held from the withdrawal to the
    /// checks, so the abandon task the withdrawal schedules can't run first.
    @Test(arguments: [false, true]) func aReplyAfterWithdrawalCannotSaveAResumableCapture(markerBlocked: Bool) async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/files"] }
        let environment = NativeCaptureFixture.environment(
            endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: captures)
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        let session = try #require(coordinator.session)
        let uploader = try #require(session.uploader)
        let stateURL = CaptureUploader.stateURL(in: session.folder)
        try await waitUntilParked(server, "POST captures/files")
        if markerBlocked {
            // A directory where the marker goes makes writing it fail.
            try FileManager.default.createDirectory(
                at: CaptureUploadState.withdrawnURL(in: session.folder).appending(path: "blocked"), withIntermediateDirectories: true)
        }

        coordinator.answerConsent(false)
        // No suspension from here to the checks.
        let before = try? Data(contentsOf: stateURL)
        #expect((before == nil) == markerBlocked)
        server.release("POST captures/files")
        // The uploader saves the register reply on its own executor.
        blockUntilSaved(stateURL, differentFrom: before)
        let saved = try CaptureUploadState.load(from: stateURL)
        #expect(saved.end == .abandoned("consent withdrawn"))
        if markerBlocked {
            // The blocking directory sits at the marker's path and would stop a resume by itself;
            // without it only the saved end can.
            try FileManager.default.removeItem(at: CaptureUploadState.withdrawnURL(in: session.folder))
        } else {
            #expect(try CaptureUploader.resume(folder: session.folder, base: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10)) == nil)
        }
        #expect(CaptureSessionCoordinator(environment: environment).resumeSealedCaptures().isEmpty)
        #expect(server.requests("PUT upload").isEmpty)

        server.state.withLock { $0.held = [] }
        await uploader.settled()
        #expect(server.requests("PUT upload").isEmpty)
    }

    /// A result that arrives after the withdrawal can't save the upload as finished: the
    /// withdrawal outranks the end a late reply sets.
    @Test func aResultAfterWithdrawalSavesTheUploadAsAbandoned() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["GET captures/result"] }
        let environment = NativeCaptureFixture.environment(
            endpoint: server.base, http: UncancellableHTTP(URLSessionCaptureHTTP.ephemeral(timeout: 10)), captures: captures)
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        let (tap, hit) = try fixture.tap(at: fixture.start + 1)
        coordinator.meterTapped(tap, hit: hit)
        coordinator.kept(try fixture.photo(at: fixture.start + 2.5, purpose: "meter_close"))
        for i in 0..<3 { coordinator.kept(try fixture.photo(at: fixture.start + 3 + Double(i) * 1.5)) }
        coordinator.captureEnded(acceptedCloseUpAt: fixture.start + 2.5)
        let session = try #require(coordinator.session)
        let uploader = try #require(session.uploader)
        let stateURL = CaptureUploader.stateURL(in: session.folder)
        try await waitUntilParked(server, "GET captures/result")

        coordinator.answerConsent(false)
        // No suspension from here to the check.
        let before = try? Data(contentsOf: stateURL)
        server.release("GET captures/result")
        blockUntilSaved(stateURL, differentFrom: before)
        #expect(try CaptureUploadState.load(from: stateURL).end == .abandoned("consent withdrawn"))

        server.state.withLock { $0.held = [] }
        await uploader.settled()
        #expect(await uploader.snapshot.end == .abandoned("consent withdrawn"))
    }

    /// Withdrawing cancels the request the uploader is waiting on before the abandon task runs:
    /// a held register request is given up at once instead of waiting for its answer, and no
    /// photo is uploaded.
    @Test func withdrawalCancelsTheRequestInFlight() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let logs = Mutex<[String]>([])
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.held = ["POST captures/files"] }
        let environment = NativeCaptureFixture.environment(
            endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures,
            log: { line in logs.withLock { $0.append(line) } })
        let coordinator = CaptureSessionCoordinator(environment: environment)
        coordinator.begin(recording: fixture.recording)
        coordinator.answerConsent(true)
        coordinator.kept(try fixture.photo(at: fixture.start + 2))
        let uploader = try #require(coordinator.session?.uploader)
        try await waitUntilParked(server, "POST captures/files")

        coordinator.answerConsent(false)
        // The main actor stays busy, so only the withdrawal itself can end the wait.
        let deadline = Date().addingTimeInterval(5)
        // The cut-short request ends as a withdrawal, not as a network failure to retry.
        func gaveUp() -> Bool { logs.withLock { $0.contains { $0.contains("capture-upload withdrawn step=register") } } }
        while Date() < deadline, !gaveUp() { usleep(10_000) }
        #expect(gaveUp())

        server.release("POST captures/files")
        server.state.withLock { $0.held = [] }
        await uploader.settled()
        #expect(server.requests("PUT upload").isEmpty)
    }

    /// Photos kept after the scan was sent don't change the frozen packet or start more uploads.
    @Test func photosAfterTheScanWasSentAreNotAdded() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let server = try LoopbackCaptureAPI()
        let coordinator = CaptureSessionCoordinator(
            environment: NativeCaptureFixture.environment(endpoint: server.base, http: URLSessionCaptureHTTP.ephemeral(timeout: 10), captures: captures))
        try await fixture.run(coordinator)
        let before = await coordinator.session!.uploader!.snapshot
        coordinator.kept(try fixture.photo(at: fixture.end - 0.5))
        await coordinator.settle()
        let after = await coordinator.session!.uploader!.snapshot
        #expect(after.packet == before.packet)
        #expect(after.files.count == before.files.count)
    }
}
