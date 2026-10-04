import Foundation
import HouseScanKit
import simd
import Testing

/// A synthetic ARKit capture for the 0.4 tests: a camera walking along +x at 1.5 m height,
/// 96 x 72 JPEGs with intrinsics that fit them, 60 Hz poses and 100 Hz IMU with the gyro 1 ms
/// behind the accelerometer. `source` is synthetic, so no packet from it is evidence.
struct SyntheticCapture04 {
    static let start = 1000.0
    let folder: URL
    let producer: Packet04Producer

    init(folder: URL, packetID: String = UUID().uuidString) throws {
        self.folder = folder
        producer = try Packet04Producer(folder: folder, info: Self.info(packetID: packetID), startedAtUptime: Self.start, startedAt: Date(timeIntervalSince1970: 1_790_000_000))
    }

    static func info(packetID: String) -> Packet04SessionInfo {
        Packet04SessionInfo(
            packetID: packetID, sessionID: "S-1", source: .synthetic, appVersion: "test (1)", deviceModel: "iPhone15,4", systemVersion: "26.0",
            lidarAvailable: false, sceneDepthEnabled: false, meshReconstructionSupported: false, sceneReconstruction: "none",
            planeDetection: ["horizontal", "vertical"], videoWidth: 1920, videoHeight: 1440, framesPerSecond: 60, timeZone: "America/Chicago")
    }

    static func pose(_ t: Double) -> simd_float4x4 {
        var m = matrix_identity_float4x4
        m.columns.3 = SIMD4(Float(t - start) * 0.5, 1.5, -2, 1)
        return m
    }

    static let intrinsics = SIMD4<Float>(80, 80, 48, 36)

    func observation(_ t: Double) -> Packet04Observation {
        Packet04Observation(t: t, cameraToWorld: Self.pose(t), intrinsics: Self.intrinsics, width: 96, height: 72, tracking: .normal)
    }

    static func jpeg(in folder: URL) throws -> Data {
        let url = folder.appending(path: "source-\(UUID().uuidString).jpg")
        try makeJPEG(width: 96, height: 72, at: url)
        defer { try? FileManager.default.removeItem(at: url) }
        return try Data(contentsOf: url)
    }

    /// Seals `count` keyframes and the head-on meter still on the first one; returns every file.
    func sealImages(count: Int = 3) async throws -> [SealedFile] {
        let scratch = folder.deletingLastPathComponent()
        var files: [SealedFile] = []
        for i in 0..<count {
            files += try await producer.sealKeyframe(jpeg: try Self.jpeg(in: scratch), observation: observation(Self.start + 0.2 + Double(i) * 0.5), reason: "motion")
        }
        files.append(try await producer.sealStill(purpose: "meter_close", keyframe: "k00001"))
        return files
    }

    static func poses(until end: Double = start + 2) -> [Packet04Streams.PoseRow] {
        stride(from: start, through: end, by: 1.0 / 60).map { .init(t: $0, tracking: .normal, cameraToWorld: pose($0), intrinsics: SIMD4(1440, 1440, 960, 720)) }
    }

    static func imu(until end: Double = start + 2) -> (accel: [Packet04Streams.MotionRow], gyro: [Packet04Streams.MotionRow]) {
        let times = Array(stride(from: start, through: end, by: 0.01))
        return (times.map { .init(t: $0, value: SIMD3(0, -1, 0)) }, times.map { .init(t: $0 + 0.001, value: SIMD3(0.01, 0, 0)) })
    }

    func finish() async throws -> (streams: [SealedFile], packet: Data) {
        let imu = Self.imu()
        return try await producer.finish(poses: Self.poses(), accelerometer: imu.accel, gyroscope: imu.gyro, endedAtUptime: Self.start + 2, acceptedCloseUpAt: Self.start + 0.2)
    }
}

func temporaryPacketFolder(_ name: String) -> URL {
    FileManager.default.temporaryDirectory.appending(path: "\(name)-\(UUID().uuidString)/packet")
}

@Suite struct Packet04Tests {
    @Test func finishedPacketPassesItsChecksAndListsEverySealedFile() async throws {
        let folder = temporaryPacketFolder("p04-normal")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        let images = try await capture.sealImages()
        let (streams, data) = try await capture.finish()
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: data)

        #expect(Packet04Check.problems(packet, folder: folder).isEmpty)
        #expect(Set(packet.files.map(\.path)) == Set((images + streams).map(\.path)))
        #expect(packet.formatVersion == "0.4")
        #expect(packet.session.tier == .arkit)
        #expect(packet.epochs == [.init(id: "e1", startTime: SyntheticCapture04.start, reason: "sessionStart")])
        #expect(packet.keyframes.allSatisfy { $0.epoch == "e1" })
        #expect(packet.scaleReference.meterCloseUp == .init(captured: true, still: "meter_close", arkitDistanceM: nil, side: nil))
        #expect(packet.scaleReference.ext.obliqueCloseUp == .notCaptured)
        #expect(packet.tracking == [.init(time: SyntheticCapture04.start, state: "normal", epoch: "e1")])
        // The exact bytes finish returned are what is on disk.
        #expect(try Data(contentsOf: folder.appending(path: "packet.json")) == data)
    }

    @Test func keyframeKeepsTheRawWorldPoseAndItsOwnIntrinsics() async throws {
        let folder = temporaryPacketFolder("p04-pose")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        let t = SyntheticCapture04.start + 0.7
        let files = try await capture.producer.sealKeyframe(jpeg: try SyntheticCapture04.jpeg(in: folder.deletingLastPathComponent()), observation: capture.observation(t), reason: "motion")
        guard case .keyframe(let record)? = files.first?.meta else { Issue.record("no keyframe record"); return }
        #expect(record.pose == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.35, 1.5, -2, 1])
        #expect(record.intrinsics == [80, 80, 48, 36])
        #expect(record.timestamp == t)
        #expect(files.first?.path == "keyframes/k00001.jpg")
        #expect(files.first?.role == .keyframe)
    }

    @Test func sealedDigestsMatchTheBytesOnDisk() async throws {
        let folder = temporaryPacketFolder("p04-digest")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        for file in try await capture.sealImages() {
            let data = try Data(contentsOf: folder.appending(path: file.path))
            #expect(file.bytes == data.count)
            #expect(file.sha256 == PacketFiles.sha256(data))
            #expect(Data(base64Encoded: file.md5)?.count == 16)
        }
    }

    @Test func refusesARotatedImageAndIntrinsicsOfAnotherSize() async throws {
        let folder = temporaryPacketFolder("p04-refuse")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        var portrait = capture.observation(SyntheticCapture04.start + 1)
        (portrait.width, portrait.height) = (72, 96)
        await #expect(throws: Packet04Error.self) {
            _ = try await capture.producer.sealKeyframe(jpeg: try SyntheticCapture04.jpeg(in: folder.deletingLastPathComponent()), observation: portrait, reason: "motion")
        }
        var wrong = capture.observation(SyntheticCapture04.start + 1)
        wrong.intrinsics = SIMD4(1440, 1440, 960, 720)
        await #expect(throws: Packet04Error.self) {
            _ = try await capture.producer.sealKeyframe(jpeg: try SyntheticCapture04.jpeg(in: folder.deletingLastPathComponent()), observation: wrong, reason: "motion")
        }
    }

    @Test func nothingIsSealedAfterFinishAndFinishReturnsTheSameBytes() async throws {
        let folder = temporaryPacketFolder("p04-frozen")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        _ = try await capture.sealImages()
        let first = try await capture.finish()
        let second = try await capture.finish()
        #expect(first.packet == second.packet)
        await #expect(throws: Packet04Error.sealedAfterFinish("keyframe")) {
            _ = try await capture.producer.sealKeyframe(jpeg: try SyntheticCapture04.jpeg(in: folder.deletingLastPathComponent()), observation: capture.observation(SyntheticCapture04.start + 1.9), reason: "motion")
        }
    }

    @Test func tapRayReplaysFromItsKeyframe() async throws {
        let folder = temporaryPacketFolder("p04-tap")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        _ = try await capture.sealImages(count: 1)
        try await capture.producer.addTap(id: "tap1", label: "meter", keyframe: "k00001", pixel: SIMD2(48, 36), time: SyntheticCapture04.start + 0.2, hit: nil)
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: try await capture.finish().packet)
        let tap = try #require(packet.taps?.first)
        // The principal point looks straight down -z from the camera.
        #expect(tap.rayOrigin == [0.1, 1.5, -2])
        #expect(tap.rayDirection == [0, 0, -1])

        var broken = packet
        broken.taps?[0].rayDirection = [0, 0.01, -1]
        #expect(Packet04Check.problems(broken, folder: nil).contains { $0.hasPrefix("tap tap1 ray replays with error") })
    }

    /// A tap ray must be two 3-vectors of finite numbers. Joined and compared pairwise, a missing
    /// or misshaped vector, or a NaN, could otherwise pass the replay check unseen.
    @Test(arguments: [
        ([0.1, 1.5, -2], []),
        ([0.1, 1.5], [-2, 0, 0, -1]),
        ([0.1, 1.5, -2], [0, 0, -1, 0]),
        ([0.1, 1.5, -2], [0, Double.nan, -1]),
        ([0.1, .infinity, -2], [0, 0, -1]),
    ] as [([Double], [Double])])
    func aTapRayThatIsNotTwoFinite3VectorsIsRefused(origin: [Double], direction: [Double]) async throws {
        let folder = temporaryPacketFolder("p04-tap-shape")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        _ = try await capture.sealImages(count: 1)
        try await capture.producer.addTap(id: "tap1", label: "meter", keyframe: "k00001", pixel: SIMD2(48, 36), time: SyntheticCapture04.start + 0.2, hit: nil)
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: try await capture.finish().packet)
        #expect(!Packet04Check.problems(packet, folder: nil).contains { $0.hasPrefix("tap tap1") })

        var broken = packet
        broken.taps?[0].rayOrigin = origin
        broken.taps?[0].rayDirection = direction
        #expect(Packet04Check.problems(broken, folder: nil).contains { $0.hasPrefix("tap tap1") })
    }

    @Test func checksCatchAMalformedPacket() async throws {
        let folder = temporaryPacketFolder("p04-malformed")
        defer { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) }
        let capture = try SyntheticCapture04(folder: folder)
        _ = try await capture.sealImages()
        let good = try JSONDecoder().decode(Packet04.Packet.self, from: try await capture.finish().packet)

        var unlisted = good
        unlisted.files.removeAll { $0.path == "keyframes/k00002.jpg" }
        #expect(Packet04Check.problems(unlisted, folder: folder) == ["keyframe k00002 image keyframes/k00002.jpg is not in files"])

        var badPath = good
        badPath.files[0].path = "../escape.jpg"
        #expect(Packet04Check.problems(badPath, folder: nil).contains("file path ../escape.jpg is not a RelPath"))

        var badStill = good
        badStill.stills?[0].keyframe = "k00009"
        #expect(Packet04Check.problems(badStill, folder: nil).contains("still meter_close names unknown keyframe k00009"))

        try Data("changed".utf8).write(to: folder.appending(path: "keyframes/k00003.jpg"))
        #expect(Packet04Check.problems(good, folder: folder).contains { $0.hasPrefix("file keyframes/k00003.jpg is 7 bytes") })
    }

    @Test func imuRowsPairOnlyCloseGyroSamplesAndReportTheMeasuredRate() throws {
        let accel = (0..<100).map { Packet04Streams.MotionRow(t: 10 + Double($0) * 0.01, value: SIMD3(0, -1, 0)) }
        // A gyro sample 1 ms after each accelerometer sample, except the 50th's, so the nearest to
        // that accelerometer sample is 9 ms away: it is left out, not given a far reading.
        var gyro = accel.map { Packet04Streams.MotionRow(t: $0.t + 0.001, value: SIMD3(0.5, 0, 0)) }
        gyro.remove(at: 50)
        let out = try Packet04Streams.imuRaw(accelerometer: accel, gyroscope: gyro)
        let lines = String(decoding: out.csv, as: UTF8.self).split(separator: "\n")
        #expect(lines.first == "t,ax,ay,az,gx,gy,gz")
        #expect(lines.count == 1 + 99)
        #expect(!lines.contains { $0.hasPrefix("10.5,") })
        #expect(abs(out.rateHz - 100) < 0.01)
        #expect(out.pairingNote["imuRawRowsKept"] == "99 of 100")
    }

    @Test func poseRowsJoinEachFrameToItsOwnIntrinsics() {
        func trajectory(_ t: Double, x: Double) -> [Double] { [t, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 1.5, -2, 1] }
        let rows = Packet04Streams.poseRows(
            trajectory: [trajectory(1, x: 0.1), trajectory(2, x: 0.2), trajectory(3, x: 0.3)],
            // Frame 2 wrote no intrinsics row: it is left out, not given frame 1's or frame 3's.
            intrinsics: [[1, 1440, 1441, 960, 720, 1920, 1440], [3, 1500, 1501, 961, 721, 1920, 1440]],
            tracking: { state, _ in state == 0 ? .normal : .notAvailable })
        #expect(rows.map(\.t) == [1, 3])
        #expect(rows[1].intrinsics == SIMD4(1500, 1501, 961, 721))
        #expect(rows[1].cameraToWorld.columns.3 == SIMD4(0.3, 1.5, -2, 1))
        #expect(rows[0].tracking == .normal)
    }

    @Test func aSlowIMUIsRefusedNotRelabelled() {
        let accel = (0..<20).map { Packet04Streams.MotionRow(t: Double($0) * 0.05, value: SIMD3(0, -1, 0)) }
        #expect(throws: Packet04Error.streamTooSlow(stream: "imuRaw", measuredHz: 1 / 0.05, minimumHz: 50)) {
            _ = try Packet04Streams.imuRaw(accelerometer: accel, gyroscope: accel)
        }
    }

    @Test func gzipStreamsDecompressWithTheSystemGzip() throws {
        let text = Data((0..<2000).map { "\($0),0.5,-1\n" }.joined().utf8)
        let url = FileManager.default.temporaryDirectory.appending(path: "p04-\(UUID().uuidString).csv.gz")
        defer { try? FileManager.default.removeItem(at: url) }
        try Gzip.compress(text).write(to: url)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/gzip")
        process.arguments = ["-dc", url.path]
        let pipe = Pipe()
        process.standardOutput = pipe
        try process.run()
        let out = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        #expect(process.terminationStatus == 0)
        #expect(out == text)
    }

    @Test func idAndPathPatternsMatchTheServer() {
        #expect(Packet04.isStorageID(UUID().uuidString))
        #expect(!Packet04.isStorageID("short"))
        #expect(!Packet04.isStorageID("has.dot-in-it"))
        #expect(Packet04.isSegmentID("k00001") && Packet04.isSegmentID("meter_close"))
        #expect(!Packet04.isSegmentID("-lead") && !Packet04.isSegmentID("a.b"))
        #expect(Packet04.isRelPath("keyframes/k00001.depth.f32") && Packet04.isRelPath("streams/imu_raw.csv.gz"))
        #expect(!Packet04.isRelPath("a/../b") && !Packet04.isRelPath("a//b") && !Packet04.isRelPath("foo..bar") && !Packet04.isRelPath(".hidden"))
    }
}
