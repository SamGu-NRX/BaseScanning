import CoreGraphics
import CryptoKit
import Foundation
import HouseScanKit
import ImageIO
import simd
import Testing

/// The capture packet's manifest schema, version 1.1: t3/packet at d5439cf
/// (packet/manifest.schema.json, sha256 44c9c9be…), plus one additive change made here, the
/// optional `attrs.inferred` on a wall end and `t`'s description (B-12, packet/README.md).
/// `vendoredManifestSchemaIsTheRecordedRevision` fails if the copy changes without this record.
/// There is no live copy to compare with: t3/packet retired 1.1 at 6a12700 in favour of the server
/// team's packet 0.4, which the writer moves to next.
enum PacketSchema {
    static let name = "manifest.schema.json"
    static let sha256 = "54d2a0d104162801a35599c5300acff09b2c2061aa8e88291f2621c04398bc27"

    static func validator() throws -> JSONSchemaValidator { try JSONSchemaValidator(schema: SceneSchemas.data(name)) }
}

/// A JPEG of `width` x `height` with a colour pattern, written by ImageIO. `orientation` sets the
/// EXIF orientation tag.
func makeJPEG(width: Int, height: Int, at url: URL, orientation: Int? = nil) throws {
    let space = CGColorSpaceCreateDeviceRGB()
    let context = try #require(CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 4 * width, space: space,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
    let pixels = try #require(context.data).bindMemory(to: UInt8.self, capacity: 4 * width * height)
    for y in 0..<height {
        for x in 0..<width {
            let p = 4 * (y * width + x)
            pixels[p] = UInt8((x * 9 + y * 3) % 256)
            pixels[p + 1] = UInt8((x * y) % 256)
            pixels[p + 2] = UInt8((y * 11) % 256)
            pixels[p + 3] = 255
        }
    }
    let image = try #require(context.makeImage())
    let destination = try #require(CGImageDestinationCreateWithURL(url as CFURL, "public.jpeg" as CFString, 1, nil))
    var properties: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: 0.9]
    if let orientation { properties[kCGImagePropertyOrientation] = orientation }
    CGImageDestinationAddImage(destination, image, properties as CFDictionary)
    #expect(CGImageDestinationFinalize(destination))
}

/// The JPEG decoded by ImageIO into RGBX, then Pillow's luma: what the app does before scoring.
func jpegLuma(_ url: URL) throws -> (luma: [UInt8], width: Int, height: Int) {
    let source = try #require(CGImageSourceCreateWithURL(url as CFURL, nil))
    let image = try #require(CGImageSourceCreateImageAtIndex(source, 0, nil))
    let (w, h) = (image.width, image.height)
    let context = try #require(CGContext(
        data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 4 * w, space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
    context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    let bytes = UnsafeRawBufferPointer(start: try #require(context.data), count: 4 * w * h)
    return (PacketSharpness.luma(rgbx: bytes, width: w, height: h, bytesPerRow: 4 * w), w, h)
}

private func temporaryFolder(_ name: String) -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent("\(name)-\(UUID().uuidString)")
}

/// A synthetic scan: the standard wall (meter (0, 1.5, 0), outward +z, ground 0) seen from a
/// camera walking left to right 2.5 m out, 60 Hz for 2 s from uptime 100, three photos on the
/// trajectory (two with ARKit depth, one with estimated depth), two depth frames between them (one
/// ARKit, one estimated), IMU at 100 Hz, a barometer, a two-triangle mesh, two planes, marks and
/// guidance.
struct SyntheticPacket {
    static let start = 100.0
    let wall = SceneWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0)
    let frame: MeterFrame
    let session: PacketSessionInfo

    init() throws {
        frame = try #require(MeterFrame(wall: wall))
        session = PacketSessionInfo(
            id: "synthetic-swift", producer: .init(kind: .app, name: "HouseScanKit tests", version: "0"),
            device: .init(
                model: "synthetic", iosVersion: "26.0", lidar: true, sceneDepthEnabled: true, meshEnabled: true,
                meshClassificationEnabled: true),
            startedAt: Date(timeIntervalSince1970: 1_790_000_000), startedAtUptime: Self.start, meterFrame: frame, groundWorldY: 0)
    }

    /// World camera at frame `i` (60 Hz): x from -1 to 1 over 120 frames, 1.25 m up, 2.5 m out,
    /// facing the wall.
    func worldCamera(_ i: Int) -> simd_float4x4 {
        let x = Float(-1) + Float(i) / 60
        return simd_float4x4(SIMD4(1, 0, 0, 0), SIMD4(0, 1, 0, 0), SIMD4(0, 0, 1, 0), SIMD4(x, 1.25, 2.5, 1))
    }

    func t(_ i: Int) -> Double { Self.start + Double(i) / 60 }

    /// Writes the whole packet into `folder` and returns the finished folder.
    func write(to folder: URL, jpegs: URL) throws -> URL {
        var writer = try PacketWriter(folder: folder, session: session)
        for i in 0...120 {
            try writer.appendTrajectory(t: t(i), tracking: i < 3 ? .limited(.initializing) : .normal, pose: frame.pose(worldCamera(i)))
        }
        try writer.setNominalRate(60, for: .trajectory)
        for i in 0..<200 {
            let t = Self.start + Double(i) / 100
            try writer.appendAccelerometer(t: t, g: SIMD3(-1, 0, 0))
            try writer.appendGyroscope(t: t, radiansPerSecond: SIMD3(0.01, 0, -0.02))
            try writer.appendMagnetometer(t: t, microtesla: SIMD3(20, -5, -40))
            try writer.appendDeviceMotion(DeviceMotionSample(
                t: t, attitude: SIMD4(0, 0, 0, 1), gravity: SIMD3(-1, 0, 0), userAcceleration: .zero, rotationRate: .zero,
                headingDegrees: -1))
        }
        for stream in [PacketStream.accelerometer, .gyroscope, .magnetometer, .deviceMotion] { try writer.setNominalRate(100, for: stream) }
        try writer.appendBarometer(t: 100.5, pressureKPa: 101.3, relativeAltitudeM: 0)
        try writer.appendBarometer(t: 101.5, pressureKPa: 101.29, relativeAltitudeM: 0.08)
        try writer.setNominalRate(1, for: .barometer)

        // Photos at frames 90, 15 and 60, added out of order; 96 x 72 with fx = 80.
        for (number, i) in [(3, 90), (1, 15), (2, 60)] {
            let id = PacketPhoto.id(number: number)
            let jpeg = jpegs.appendingPathComponent("\(id).jpg")
            try makeJPEG(width: 96, height: 72, at: jpeg)
            let luma = try jpegLuma(jpeg)
            var meters = [Float](repeating: 2.5, count: 24 * 18)
            meters[0] = .nan
            meters[1] = -1
            let depth = number == 3
                ? DepthPacket.estimated(meters: meters, sigma: [Float](repeating: 0.1, count: 24 * 18), width: 24, height: 18)
                : DepthPacket(
                    meters: meters, width: 24, height: 18, confidence: [UInt8](repeating: 2, count: 24 * 18), source: .arkitSceneDepth)
            try writer.addPhoto(PacketPhoto(
                id: id, jpeg: jpeg, width: 96, height: 72, t: t(i), pose: frame.pose(worldCamera(i)),
                intrinsics: SIMD4(80, 80, 48, 36), tracking: .normal,
                exposure: PacketExposure(durationS: 0.002, iso: 50), lens: PacketLens(focalLengthMM: 5.1, fNumber: 1.8, camera: "wide"),
                sharpness: PacketSharpness.laplacianVarianceLuma640(luma: luma.luma, width: luma.width, height: luma.height),
                depth: depth))
        }

        // Depth frames at frames 45 and 30, added out of order: a 32 x 24 grid over the photos'
        // field of view, so the photos' intrinsics scaled by a third.
        for (number, i) in [(2, 45), (1, 30)] {
            let meters = [Float](repeating: 2.5, count: 32 * 24)
            let depth = number == 1
                ? DepthPacket(meters: meters, width: 32, height: 24, confidence: [UInt8](repeating: 1, count: 32 * 24), source: .arkitSceneDepth)
                : DepthPacket.estimated(meters: meters, sigma: [Float](repeating: 0.2, count: 32 * 24), width: 32, height: 24)
            try writer.addDepthFrame(PacketDepthFrame(
                id: PacketDepthFrame.id(number: number), t: t(i), pose: frame.pose(worldCamera(i)),
                intrinsics: DepthImage.intrinsics(scaling: SIMD4(80, 80, 48, 36), from: SIMD2(96, 72), toWidth: 32, height: 24),
                tracking: .normal, depth: depth))
        }

        let worldMesh = TriangleMesh(
            vertices: [SIMD3(-3, 0, 0), SIMD3(3, 0, 0), SIMD3(3, 2.5, 0), SIMD3(-3, 2.5, 0)], indices: [0, 1, 2, 0, 2, 3])
        try writer.setMesh(frame.mesh(worldMesh), classification: [1, 1])
        // The wall's anchor: its +y (the plane's normal) is world +z, out of the wall, and its -z is
        // world up. ARKit put the anchor 0.5 m left of the extent's centre, and the extent is
        // turned a quarter turn in the anchor's x-z plane: its x runs up the wall (2.4 m) and its z
        // along it (6 m). The boundary traces an L, the extent's rectangle less its upper right
        // quarter.
        let wallAnchor = simd_float4x4(SIMD4(1, 0, 0, 0), SIMD4(0, 0, 1, 0), SIMD4(0, -1, 0, 0), SIMD4(-0.5, 1.25, 0, 1))
        let center = SIMD3<Float>(0.5, 0, 0)
        let outline: [SIMD2<Float>] = [SIMD2(-1.2, -3), SIMD2(1.2, -3), SIMD2(1.2, 1), SIMD2(0, 1), SIMD2(0, 3), SIMD2(-1.2, 3)]
        let extentFrame = PacketPlane.extentInAnchor(center: center, rotationOnYAxis: .pi / 2)
        let boundary = outline.map { xz -> SIMD3<Float> in
            let p = extentFrame * SIMD4(xz.x, 0, xz.y, 1)
            return SIMD3(p.x, p.y, p.z)
        }
        try writer.addPlane(PacketPlane(
            id: "wall", alignment: .vertical, classification: .wall, anchorToMeter: frame.pose(wallAnchor), center: center,
            rotationOnYAxis: .pi / 2, extent: SIMD2(2.4, 6), boundaryVertices: boundary))
        try writer.addPlane(PacketPlane(
            id: "ground", alignment: .horizontal, classification: nil, pose: frame.pose(matrix_identity_float4x4), extent: SIMD2(6, 3)))
        try writer.setMarks([
            .meter(id: "m1", t: 100.1, photoIDs: ["p00001"]),
            // Placed from the walk ("Can't get there"): no mark time, attrs.inferred.
            .wallEnd(id: "m2", side: .left, endKind: .unexplored, s: -3, wall: wall, frame: frame, stamp: .inferred),
            .wallEnd(id: "m3", side: .right, endKind: .limit, s: 3, wall: wall, frame: frame, stamp: .marked(at: 101.9)),
            try .from(.opening(kind: .window, span: -2 ... -1.25, bottom: 1, top: 2, operable: true), id: "m4", wall: wall, frame: frame, t: 100.4),
            try .from(.pointObject(kind: .gasMeter, tap: SIMD3(1.5, 1, 0.1), bottom: nil, top: nil), id: "m5", wall: wall, frame: frame),
            try .from(.driveway(edge: [SIMD3(2, 0, 0.5), SIMD3(2, 0, 4)]), id: "m6", wall: wall, frame: frame, t: 101.5),
        ])
        try writer.setGuidance([
            PacketGuidanceEntry(id: "g1", kind: .walk, origin: .phone, message: "Walk slowly to your left", tShown: 100, tResolved: 100.8, outcome: .met),
            PacketGuidanceEntry(
                id: "g2", kind: .gapBand, origin: .server, message: "Show the ground around your meter", band: .ground, span: 1 ... 2.5,
                tShown: 101.6, tResolved: 101.9, outcome: .cannotReach),
            PacketGuidanceEntry(id: "g3", kind: .stepBack, origin: .phone, message: nil, tShown: 101.95, tResolved: nil, outcome: .unresolved),
        ])
        try writer.setScene(Data(#"{"schema_version":"1.0","meter":{"pos":[0,4.921,0],"wall_id":"wall"}}"#.utf8))
        return try writer.finish()
    }
}

@Suite struct PacketWriterTests {
    private func decodedManifest(_ folder: URL) throws -> PacketManifest {
        try JSONDecoder().decode(PacketManifest.self, from: Data(contentsOf: folder.appendingPathComponent("manifest.json")))
    }

    /// Every file named in the manifest, with where it is named.
    private func files(_ m: PacketManifest) -> [PacketManifest.File] {
        var out = m.photos.flatMap { p in [p.image] + [p.depth?.map, p.depth?.confidence, p.depth?.sigma].compactMap { $0 } }
        out += (m.depthFrames ?? []).flatMap { f in [f.map] + [f.confidence, f.sigma].compactMap { $0 } }
        let streams = [m.streams?.trajectory, m.streams?.accelerometer, m.streams?.gyroscope, m.streams?.magnetometer, m.streams?.deviceMotion, m.streams?.barometer]
        out += streams.compactMap { $0.map { PacketManifest.File(path: $0.path, bytes: $0.bytes, sha256: $0.sha256) } }
        out += [m.lidar?.mesh].compactMap { $0 }
        out += [m.scene.map { PacketManifest.File(path: $0.path, bytes: $0.bytes, sha256: $0.sha256) }].compactMap { $0 }
        return out
    }

    /// Writes the synthetic packet and checks what packet/validate.py checks that can be checked
    /// here. For the validator itself (manual, not a test dependency):
    ///
    ///     HOUSESCAN_PACKET_OUT=/tmp/hs-swift-packet swift test --package-path ios/HouseScanKit \
    ///         --filter PacketWriterTests/syntheticPacketIsComplete
    ///     cd <t3/packet checkout>/packet && uv run python -m packet validate /tmp/hs-swift-packet
    ///
    /// With HOUSESCAN_PACKET_OUT set, the packet is written there (it must not exist or be
    /// empty) and kept.
    @Test func syntheticPacketIsComplete() throws {
        let keep = ProcessInfo.processInfo.environment["HOUSESCAN_PACKET_OUT"].map { URL(fileURLWithPath: $0) }
        let folder = keep ?? temporaryFolder("packet")
        let jpegs = temporaryFolder("packet-jpegs")
        try FileManager.default.createDirectory(at: jpegs, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(at: jpegs)
            if keep == nil { try? FileManager.default.removeItem(at: folder) }
        }
        let synthetic = try SyntheticPacket()
        let written = try synthetic.write(to: folder, jpegs: jpegs)
        #expect(written == folder)

        // The schema.
        let manifestData = try Data(contentsOf: folder.appendingPathComponent("manifest.json"))
        #expect(try PacketSchema.validator().validate(manifestData) == [])

        // Every file's size and hash, and no file the manifest does not name.
        let manifest = try decodedManifest(folder)
        let named = files(manifest)
        for file in named {
            let data = try Data(contentsOf: folder.appendingPathComponent(file.path))
            #expect(data.count == file.bytes, "\(file.path)")
            #expect(PacketFiles.sha256(data) == file.sha256, "\(file.path)")
        }
        let onDisk = try #require(FileManager.default.enumerator(at: folder, includingPropertiesForKeys: nil))
            .compactMap { $0 as? URL }.filter { !$0.hasDirectoryPath }
            .map { String($0.standardizedFileURL.path.dropFirst(folder.standardizedFileURL.path.count + 1)) }
        #expect(Set(onDisk) == Set(named.map(\.path) + ["manifest.json"]))
        #expect(Set(named.map(\.path)).count == named.count)

        // Session.
        let s = manifest.session
        #expect(manifest.packetVersion == "1.1" && s.worldAlignment == "gravity" && s.consent == nil)
        #expect(s.device.meshClassificationEnabled == true)
        #expect(s.capture.startedAt == "2026-09-21T14:13:20.000Z")
        #expect(s.capture.startedAtUptime == 100 && s.capture.endedAtUptime == synthetic.t(120))
        #expect(s.meterAnchor.poseInWorld == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1.5, 0, 1])
        #expect(s.meterAnchor.groundYM == -1.5)
        // The camera walks x from -1 to 1: 2 m, to Float rounding.
        #expect(abs(try #require(s.capture.distanceWalkedM) - 2) < 1e-5)

        // Photos: sorted by t, on the trajectory, depth cleaned.
        #expect(manifest.photos.map(\.id) == ["p00001", "p00002", "p00003"])
        #expect(manifest.photos.map(\.image.path) == ["photos/p00001.jpg", "photos/p00002.jpg", "photos/p00003.jpg"])
        let first = manifest.photos[0]
        #expect(first.t == synthetic.t(15))
        #expect(first.pose == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -0.75, -0.25, 2.5, 1])
        #expect(first.intrinsics == [80, 80, 48, 36])
        #expect(first.tracking?.state == "normal" && first.tracking?.reason == nil)
        #expect(first.sharpness?.method == "laplacian_variance_luma_640")
        #expect(first.depth?.map.path == "depth/p00001.f32" && first.depth?.confidence?.path == "depth/p00001.conf.u8")
        #expect(first.depth?.width == 24 && first.depth?.source == .arkitSceneDepth && first.depth?.sigma == nil)
        let depth = try Data(contentsOf: folder.appendingPathComponent("depth/p00001.f32"))
        let noMeasurement = [UInt8](repeating: 0, count: 8)
        #expect([UInt8](depth.prefix(8)) == noMeasurement)
        // Estimated depth: sigma, no confidence, and sigma 0 where the depth has no measurement.
        let estimated = try #require(manifest.photos[2].depth)
        #expect(estimated.source == .estimated && estimated.confidence == nil && estimated.sigma?.path == "depth/p00003.sigma.f32")
        let sigma = try Data(contentsOf: folder.appendingPathComponent("depth/p00003.sigma.f32"))
        #expect(sigma.count == 24 * 18 * 4 && [UInt8](sigma.prefix(8)) == noMeasurement)
        #expect(sigma.subdata(in: 8..<12) == PacketFiles.depthData(meters: [0.1]))

        // Depth frames: sorted by t, on the trajectory, intrinsics of their own grid.
        let frames = try #require(manifest.depthFrames)
        #expect(frames.map(\.id) == ["d00001", "d00002"] && frames.map(\.t) == [synthetic.t(30), synthetic.t(45)])
        #expect(frames[0].pose == PacketPose.columnMajor(synthetic.frame.pose(synthetic.worldCamera(30))))
        // The photos' intrinsics scaled by a third, each Float written as its shortest decimal.
        let grid = DepthImage.intrinsics(scaling: SIMD4(80, 80, 48, 36), from: SIMD2(96, 72), toWidth: 32, height: 24)
        let gridValues: [Float] = [grid.x, grid.y, grid.z, grid.w]
        let gridNumbers: [Double] = gridValues.map { (value: Float) -> Double in Double(value.description) ?? .nan }
        #expect(frames[0].intrinsics == gridNumbers)
        #expect(abs(frames[0].intrinsics[0] - 80.0 / 3) < 1e-5 && frames[0].intrinsics[2] == 16 && frames[0].intrinsics[3] == 12)
        #expect(frames[0].width == 32 && frames[0].height == 24 && frames[0].tracking?.state == "normal")
        #expect(frames[0].source == .arkitSceneDepth && frames[0].confidence?.path == "depth_frames/d00001.conf.u8" && frames[0].sigma == nil)
        #expect(frames[1].source == .estimated && frames[1].sigma?.path == "depth_frames/d00002.sigma.f32" && frames[1].confidence == nil)
        #expect(frames[0].map.bytes == 32 * 24 * 4 && frames[0].confidence?.bytes == 32 * 24)

        // Streams: rows, rates, trajectory values at the photo.
        let streams = try #require(manifest.streams)
        #expect(streams.trajectory?.rows == 121 && streams.trajectory?.nominalRateHz == 60)
        #expect(streams.accelerometer?.rows == 200 && streams.barometer?.rows == 2 && streams.location == nil)
        let trajectory = parseCSV(try Data(contentsOf: folder.appendingPathComponent("streams/trajectory.csv")))
        #expect(trajectory.rows[0] == ["100.0", "limited", "-1.0", "-0.25", "2.5", "0.0", "0.0", "0.0", "1.0"])
        #expect(trajectory.rows[15][0] == "100.25" && trajectory.rows[15][2] == "-0.75")
        let accelerometer = parseCSV(try Data(contentsOf: folder.appendingPathComponent("streams/accelerometer.csv")))
        #expect(accelerometer.rows[0] == ["100.0", "-9.80665", "0.0", "0.0"])

        // LiDAR: the wall mesh sits on z = 0 in the meter frame, 1.5 m below to 1 m above the meter.
        let mesh = try parsePLY(Data(contentsOf: folder.appendingPathComponent("lidar/mesh.ply")))
        let corners: [SIMD3<Float>] = [SIMD3(-3, -1.5, 0), SIMD3(3, -1.5, 0), SIMD3(3, 1, 0), SIMD3(-3, 1, 0)]
        #expect(mesh.vertices == corners)
        #expect(mesh.faces.map(\.classification) == [1, 1])
        #expect(manifest.lidar?.planes == nil)
        let planes = try #require(manifest.planes)
        // Worked by hand: the extent's centre is 0.5 m right of the anchor, at the meter's x and
        // 0.25 m below it; its x axis runs up the wall (anchor -z, world +y), its y out (anchor y)
        // and its z along the wall (anchor x).
        let wallPose = PacketPose.columnMajor(planes[0].pose)
        let expectedPose: [Double] = [0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, -0.25, 0, 1]
        #expect(zip(wallPose, expectedPose).allSatisfy { abs($0 - $1) < 1e-6 }, "\(wallPose)")
        let wallBoundary = try #require(planes[0].boundary)
        #expect(wallBoundary.count == 6)
        #expect(zip(wallBoundary, [SIMD2<Float>(-1.2, -3), SIMD2(1.2, -3), SIMD2(1.2, 1), SIMD2(0, 1), SIMD2(0, 3), SIMD2(-1.2, 3)])
            .allSatisfy { simd_distance($0, $1) < 1e-5 }, "\(wallBoundary)")
        #expect(planes[1].classification == nil && planes[1].boundary == nil)

        // Marks and guidance come back as written.
        let marks = try #require(manifest.marks)
        #expect(marks.map(\.kind) == [.meter, .wallEnd, .wallEnd, .window, .gasMeter, .driveEdge])
        #expect(marks[1].inferred && marks[1].t == nil, "the inferred left end")
        #expect(!marks[2].inferred && marks[2].t == 101.9, "the marked right end")
        let window: [SIMD3<Float>] = [SIMD3(-2, -0.5, 0), SIMD3(-1.25, 0.5, 0)]
        let drive: [SIMD3<Float>] = [SIMD3(2, -1.5, 0.5), SIMD3(2, -1.5, 4)]
        #expect(marks[3].points == window)
        #expect(marks[5].points == drive)
        #expect(manifest.guidance?.map(\.outcome) == [.met, .cannotReach, .unresolved])
        #expect(manifest.scene?.schemaVersion == "1.0" && manifest.scene?.path == "scene.json")
    }

    /// Optional fields are left out, never written as null; nested keys are the schema's.
    @Test func manifestJSONShape() throws {
        let folder = temporaryFolder("packet-shape")
        let jpegs = temporaryFolder("packet-shape-jpegs")
        try FileManager.default.createDirectory(at: jpegs, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(at: folder)
            try? FileManager.default.removeItem(at: jpegs)
        }
        _ = try SyntheticPacket().write(to: folder, jpegs: jpegs)
        let text = try String(contentsOf: folder.appendingPathComponent("manifest.json"), encoding: .utf8)
        #expect(!text.contains("null"))
        #expect(!text.contains("\\/"))
        let json = try #require(JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
        #expect(Set(json.keys) == ["packet_version", "session", "photos", "depth_frames", "streams", "planes", "lidar", "marks", "guidance", "scene"])
        #expect(Set(try #require(json["lidar"] as? [String: Any]).keys) == ["mesh"])
        let session = try #require(json["session"] as? [String: Any])
        #expect(Set(session.keys) == ["id", "producer", "device", "capture", "world_alignment", "meter_anchor"])
        let device = try #require(session["device"] as? [String: Any])
        #expect(Set(device.keys) == ["model", "ios_version", "lidar", "scene_depth_enabled", "mesh_enabled", "mesh_classification_enabled"])
        let capture = try #require(session["capture"] as? [String: Any])
        #expect(Set(capture.keys) == ["started_at", "started_at_uptime", "ended_at_uptime", "distance_walked_m"])
        let photo = try #require((json["photos"] as? [[String: Any]])?.first)
        #expect(Set(photo.keys) == ["id", "image", "width", "height", "t", "pose", "intrinsics", "tracking", "exposure", "lens", "sharpness", "depth"])
        #expect(Set(try #require(photo["exposure"] as? [String: Any]).keys) == ["duration_s", "iso"])
        #expect(Set(try #require(photo["depth"] as? [String: Any]).keys) == ["map", "confidence", "width", "height", "source"])
        let frames = try #require(json["depth_frames"] as? [[String: Any]])
        #expect(Set(frames[0].keys) == ["id", "t", "pose", "intrinsics", "tracking", "map", "confidence", "width", "height", "source"])
        #expect(Set(frames[1].keys) == ["id", "t", "pose", "intrinsics", "tracking", "map", "sigma", "width", "height", "source"])
        let planes = try #require(json["planes"] as? [[String: Any]])
        #expect(Set(planes[0].keys) == ["id", "alignment", "classification", "pose", "extent_m", "boundary_m"])
        #expect(Set(planes[1].keys) == ["id", "alignment", "pose", "extent_m"])
        let marks = try #require(json["marks"] as? [[String: Any]])
        #expect(Set(marks[0].keys) == ["id", "kind", "points", "t", "photo_ids"])
        // The left end is inferred: the flag, no mark time. The right end is the homeowner's.
        #expect(Set(marks[1].keys) == ["id", "kind", "points", "side", "end_kind", "attrs"])
        #expect(Set(marks[2].keys) == ["id", "kind", "points", "t", "side", "end_kind"])
        #expect((marks[3]["attrs"] as? [String: Bool]) == ["operable": true])
        let guidance = try #require(json["guidance"] as? [[String: Any]])
        #expect(Set(guidance[1].keys) == ["id", "kind", "origin", "message", "band", "span_m", "t_shown", "t_resolved", "outcome"])
        #expect(Set(guidance[2].keys) == ["id", "kind", "origin", "t_shown", "outcome"])
        let streams = try #require(json["streams"] as? [String: Any])
        #expect(Set(streams.keys) == ["trajectory", "accelerometer", "gyroscope", "magnetometer", "device_motion", "barometer"])
    }

    @Test func refusesInputsTheValidatorWouldReject() throws {
        let synthetic = try SyntheticPacket()
        let folder = temporaryFolder("packet-refuse")
        let jpegs = temporaryFolder("packet-refuse-jpegs")
        try FileManager.default.createDirectory(at: jpegs, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(at: folder)
            try? FileManager.default.removeItem(at: jpegs)
        }
        var writer = try PacketWriter(folder: folder, session: synthetic.session)
        #expect(throws: PacketError.noPhotos) { try writer.finish() }

        let jpeg = jpegs.appendingPathComponent("a.jpg")
        try makeJPEG(width: 96, height: 72, at: jpeg)
        let pose = synthetic.frame.pose(synthetic.worldCamera(0))
        func photo(id: String = "p00001", t: Double = 100.5, width: Int = 96, height: Int = 72, jpeg: URL = jpeg, depth: DepthPacket? = nil) -> PacketPhoto {
            PacketPhoto(
                id: id, jpeg: jpeg, width: width, height: height, t: t, pose: pose, intrinsics: SIMD4(80, 80, 48, 36),
                tracking: .normal, sharpness: 1, depth: depth)
        }
        #expect(throws: PacketError.invalidID("p/1")) { try writer.addPhoto(photo(id: "p/1")) }
        #expect(throws: PacketError.timeBeforeStart(where: "photo p00001", t: 99, start: 100)) { try writer.addPhoto(photo(t: 99)) }
        #expect(throws: PacketError.invalidPhoto(id: "p00001", reason: "the JPEG is 96x72, not 100x75")) {
            try writer.addPhoto(PacketPhoto(
                id: "p00001", jpeg: jpeg, width: 100, height: 75, t: 100.5, pose: pose, intrinsics: SIMD4(80, 80, 48, 36),
                tracking: .normal, sharpness: 1))
        }
        let portrait = jpegs.appendingPathComponent("portrait.jpg")
        try makeJPEG(width: 72, height: 96, at: portrait)
        #expect(throws: PacketError.self) { try writer.addPhoto(photo(width: 72, height: 96, jpeg: portrait)) }
        let turned = jpegs.appendingPathComponent("turned.jpg")
        try makeJPEG(width: 96, height: 72, at: turned, orientation: 6)
        #expect(throws: PacketError.invalidPhoto(id: "p00001", reason: "EXIF orientation 6; store the unrotated sensor image")) {
            try writer.addPhoto(photo(jpeg: turned))
        }
        let square = DepthPacket(
            meters: [Float](repeating: 1, count: 24 * 24), width: 24, height: 24, confidence: [UInt8](repeating: 2, count: 24 * 24),
            source: .arkitSceneDepth)
        #expect(throws: PacketError.invalidDepth(id: "p00001", reason: "24x24 does not have the 96x72 photo's aspect")) {
            try writer.addPhoto(photo(depth: square))
        }
        let empty = DepthPacket(
            meters: [Float](repeating: 0, count: 24 * 18), width: 24, height: 18, confidence: [UInt8](repeating: 2, count: 24 * 18),
            source: .arkitSceneDepth)
        #expect(throws: PacketError.invalidDepth(id: "p00001", reason: "0 of 432 pixels measured; under 1% is empty, leave the depth out")) {
            try writer.addPhoto(photo(depth: empty))
        }
        // From 1.1 ARKit's depth needs its confidence.
        let unsure = DepthPacket(meters: [Float](repeating: 1, count: 24 * 18), width: 24, height: 18, confidence: nil, source: .arkitSmoothedSceneDepth)
        #expect(throws: PacketError.invalidDepth(id: "p00001", reason: "ARKit depth (arkit_smoothed_scene_depth) needs its confidence (packet 1.1)")) {
            try writer.addPhoto(photo(depth: unsure))
        }
        let badConfidence = DepthPacket(
            meters: [Float](repeating: 1, count: 24 * 18), width: 24, height: 18, confidence: [UInt8](repeating: 3, count: 24 * 18),
            source: .arkitSceneDepth)
        #expect(throws: PacketError.invalidDepth(id: "p00001", reason: "confidence must be 0, 1 or 2")) { try writer.addPhoto(photo(depth: badConfidence)) }
        // Nothing refused left a file behind.
        #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).isEmpty)

        try writer.addPhoto(photo())
        #expect(throws: PacketError.duplicateID("p00001")) { try writer.addPhoto(photo()) }
        #expect(throws: PacketError.self) { try writer.addPhoto(photo(id: "p00002")) }  // same t

        let lonely = PacketMark.meter(id: "m1", photoIDs: ["p00009"])
        try writer.setMarks([lonely])
        #expect(throws: PacketError.invalidMark(id: "m1", reason: "photo_ids names p00009, which is not a photo in the packet")) { try writer.finish() }
        #expect(throws: PacketError.duplicateID("m1")) { try writer.setMarks([.meter(id: "m1"), .meter(id: "m1")]) }
        let early = PacketGuidanceEntry(id: "g1", kind: .walk, origin: .phone, message: nil, tShown: 101, tResolved: 100.5, outcome: .met)
        #expect(throws: PacketError.self) { try writer.setGuidance([early]) }
        let open = PacketGuidanceEntry(id: "g1", kind: .walk, origin: .phone, message: nil, tShown: 101, tResolved: nil, outcome: .skipped)
        #expect(throws: PacketError.invalidGuidance(id: "g1", reason: "outcome skipped needs t_resolved")) { try writer.setGuidance([open]) }
        #expect(throws: PacketError.self) { try writer.appendTrajectory(t: 100, tracking: .normal, pose: simd_float4x4(diagonal: SIMD4(2, 1, 1, 1))) }
        #expect(throws: PacketError.invalidScene("not a JSON object")) { try writer.setScene(Data("[1]".utf8)) }
    }

    /// A mesh needs the session to say whether ARKit classified it, and an unclassified mesh has
    /// every class 0.
    @Test func meshNeedsTheClassificationFlag() throws {
        let synthetic = try SyntheticPacket()
        let mesh = TriangleMesh(vertices: [SIMD3(0, 0, 0), SIMD3(1, 0, 0), SIMD3(0, 1, 0)], indices: [0, 1, 2])
        let cases: [(flag: Bool?, classes: [UInt8], refusal: String)] = [
            (nil, [1], "session.device.mesh_classification_enabled must say whether ARKit classified the mesh"),
            (false, [1], "classification was off, but face 0 has class 1; every class must be 0"),
        ]
        for (flag, classes, refusal) in cases {
            var session = synthetic.session
            session.device.meshClassificationEnabled = flag
            let folder = temporaryFolder("packet-mesh")
            defer { try? FileManager.default.removeItem(at: folder) }
            var writer = try PacketWriter(folder: folder, session: session)
            #expect(throws: PacketError.invalidMesh(refusal)) { try writer.setMesh(mesh, classification: classes) }
            #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).isEmpty)
            if flag == false { try writer.setMesh(mesh, classification: [0]) }
        }
    }

    @Test func refusesAFolderThatHoldsFiles() throws {
        let folder = temporaryFolder("packet-busy")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try Data("old".utf8).write(to: folder.appendingPathComponent("stale.jpg"))
        #expect(throws: PacketError.folderNotEmpty(folder.path)) { try PacketWriter(folder: folder, session: SyntheticPacket().session) }
    }

    @Test func photoIDs() {
        #expect(PacketPhoto.id(number: 1) == "p00001")
        #expect(PacketPhoto.id(number: 12345) == "p12345")
        #expect(PacketPhoto.id(number: 123456) == "p123456")
        #expect(PacketDepthFrame.id(number: 7) == "d00007")
    }
}

@Suite struct PacketDepthFrameTests {
    private let grid = SIMD4<Float>(80.0 / 3, 80.0 / 3, 16, 12)

    private func arkit(_ meters: [Float] = [Float](repeating: 2, count: 32 * 24), confidence: [UInt8]? = [UInt8](repeating: 2, count: 32 * 24)) -> DepthPacket {
        DepthPacket(meters: meters, width: 32, height: 24, confidence: confidence, source: .arkitSceneDepth)
    }

    @Test func refusesFramesTheValidatorWouldReject() throws {
        let synthetic = try SyntheticPacket()
        let folder = temporaryFolder("packet-frames")
        defer { try? FileManager.default.removeItem(at: folder) }
        var writer = try PacketWriter(folder: folder, session: synthetic.session)
        let pose = synthetic.frame.pose(synthetic.worldCamera(30))
        func frame(id: String = "d00001", t: Double = 100.5, intrinsics: SIMD4<Float>? = nil, depth: DepthPacket? = nil) -> PacketDepthFrame {
            PacketDepthFrame(id: id, t: t, pose: pose, intrinsics: intrinsics ?? grid, tracking: .normal, depth: depth ?? arkit())
        }
        #expect(throws: PacketError.invalidDepthFrame(id: "d00001", reason: "ARKit depth (arkit_scene_depth) needs its confidence (packet 1.1)")) {
            try writer.addDepthFrame(frame(depth: arkit(confidence: nil)))
        }
        // Intrinsics must fit the depth map's own grid: the camera image's do not.
        #expect(throws: PacketError.self) { try writer.addDepthFrame(frame(intrinsics: SIMD4(80, 80, 48, 36))) }
        let portrait = DepthPacket(
            meters: [Float](repeating: 2, count: 24 * 32), width: 24, height: 32, confidence: [UInt8](repeating: 2, count: 24 * 32),
            source: .arkitSceneDepth)
        #expect(throws: PacketError.invalidDepthFrame(
            id: "d00001", reason: "intrinsics for the 24x32 depth map: 24x32 is not landscape; store the unrotated sensor image")) {
            try writer.addDepthFrame(frame(intrinsics: SIMD4(26.7, 26.7, 12, 16), depth: portrait))
        }
        #expect(throws: PacketError.timeBeforeStart(where: "depth frame d00001", t: 99, start: 100)) { try writer.addDepthFrame(frame(t: 99)) }
        #expect(throws: PacketError.notRigid("depth frame d00001")) {
            try writer.addDepthFrame(PacketDepthFrame(
                id: "d00001", t: 100.5, pose: simd_float4x4(diagonal: SIMD4(2, 1, 1, 1)), intrinsics: grid, tracking: nil, depth: arkit()))
        }
        // Nothing refused left a file behind.
        #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path).isEmpty)

        try writer.addDepthFrame(frame())
        #expect(throws: PacketError.duplicateID("d00001")) { try writer.addDepthFrame(frame(t: 101)) }
        #expect(throws: PacketError.invalidDepthFrame(id: "d00002", reason: "another depth frame has the same t 100.5")) {
            try writer.addDepthFrame(frame(id: "d00002"))
        }
        let files = try FileManager.default.contentsOfDirectory(atPath: folder.appendingPathComponent("depth_frames").path).sorted()
        #expect(files == ["d00001.conf.u8", "d00001.f32"])
    }

    /// A depth frame with no source (a replay's, which says nothing of where it came from) needs
    /// neither confidence nor sigma, and keeps what it has.
    @Test func unlabelledDepthKeepsWhatItHas() throws {
        let synthetic = try SyntheticPacket()
        let folder = temporaryFolder("packet-unlabelled")
        defer { try? FileManager.default.removeItem(at: folder) }
        var writer = try PacketWriter(folder: folder, session: synthetic.session)
        let pose = synthetic.frame.pose(synthetic.worldCamera(30))
        let bare = DepthPacket(meters: [Float](repeating: 2, count: 32 * 24), width: 32, height: 24, confidence: nil, source: nil)
        try writer.addDepthFrame(PacketDepthFrame(id: "d00001", t: 100.5, pose: pose, intrinsics: grid, tracking: nil, depth: bare))
        var sure = bare
        sure.confidence = [UInt8](repeating: 1, count: 32 * 24)
        try writer.addDepthFrame(PacketDepthFrame(id: "d00002", t: 101, pose: pose, intrinsics: grid, tracking: nil, depth: sure))
        try writer.addPhoto(PacketPhoto(
            id: "p00001", jpeg: try photoJPEG(), width: 96, height: 72, t: 100.25, pose: pose,
            intrinsics: SIMD4(80, 80, 48, 36), tracking: .normal, sharpness: 1))
        let frames = try #require(try writer.manifest().depthFrames)
        #expect(frames.map(\.source) == [nil, nil] && frames[0].confidence == nil && frames[1].confidence != nil && frames[0].tracking == nil)
    }
}

/// A 96 x 72 JPEG in its own temporary folder, which the caller need not remove: the writer
/// copies it, and the system clears the temporary directory.
private func photoJPEG() throws -> URL {
    let folder = temporaryFolder("packet-photo")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let url = folder.appendingPathComponent("photo.jpg")
    try makeJPEG(width: 96, height: 72, at: url)
    return url
}

/// The entry point S6's on-device depth model calls: `DepthPacket.estimated`, on a photo or a
/// depth frame.
@Suite struct EstimatedDepthTests {
    @Test func estimatedDepthNeedsSigmaAndNoConfidence() throws {
        let synthetic = try SyntheticPacket()
        let folder = temporaryFolder("packet-estimated")
        defer { try? FileManager.default.removeItem(at: folder) }
        var writer = try PacketWriter(folder: folder, session: synthetic.session)
        let pose = synthetic.frame.pose(synthetic.worldCamera(30))
        let grid = SIMD4<Float>(80.0 / 3, 80.0 / 3, 16, 12)
        let count = 32 * 24
        func frame(_ depth: DepthPacket) -> PacketDepthFrame {
            PacketDepthFrame(id: "d00001", t: 100.5, pose: pose, intrinsics: grid, tracking: .normal, depth: depth)
        }
        var noSigma = DepthPacket.estimated(meters: [Float](repeating: 3, count: count), sigma: [], width: 32, height: 24)
        noSigma.sigma = nil
        #expect(throws: PacketError.invalidDepthFrame(id: "d00001", reason: "estimated depth needs sigma")) { try writer.addDepthFrame(frame(noSigma)) }
        var withConfidence = DepthPacket.estimated(meters: [Float](repeating: 3, count: count), sigma: [Float](repeating: 0.3, count: count), width: 32, height: 24)
        withConfidence.confidence = [UInt8](repeating: 2, count: count)
        #expect(throws: PacketError.invalidDepthFrame(id: "d00001", reason: "estimated depth carries sigma, not ARKit's confidence")) {
            try writer.addDepthFrame(frame(withConfidence))
        }
        let short = DepthPacket.estimated(meters: [Float](repeating: 3, count: count), sigma: [0.3], width: 32, height: 24)
        #expect(throws: PacketError.invalidDepthFrame(id: "d00001", reason: "1 sigma values for 32x24")) { try writer.addDepthFrame(frame(short)) }
        var badSigma = [Float](repeating: 0.3, count: count)
        badSigma[5] = .nan
        #expect(throws: PacketError.invalidDepthFrame(id: "d00001", reason: "sigma nan at pixel 5, which has depth; it must be finite and >= 0")) {
            try writer.addDepthFrame(frame(.estimated(meters: [Float](repeating: 3, count: count), sigma: badSigma, width: 32, height: 24)))
        }

        // Where the model gave no depth, sigma is written as 0 whatever it was.
        var meters = [Float](repeating: 3, count: count)
        meters[5] = .infinity
        try writer.addDepthFrame(frame(.estimated(meters: meters, sigma: badSigma, width: 32, height: 24)))
        try writer.addPhoto(PacketPhoto(
            id: "p00001", jpeg: try photoJPEG(), width: 96, height: 72, t: 100.25, pose: pose, intrinsics: SIMD4(80, 80, 48, 36),
            tracking: .normal, sharpness: 1))
        let entry = try #require(try writer.manifest().depthFrames?.first)
        #expect(entry.source == .estimated && entry.confidence == nil && entry.sigma?.bytes == count * 4)
        let sigma = try Data(contentsOf: folder.appendingPathComponent("depth_frames/d00001.sigma.f32"))
        #expect(sigma.subdata(in: 20..<24) == PacketFiles.depthData(meters: [0]))
        #expect(sigma.subdata(in: 0..<4) == PacketFiles.depthData(meters: [0.3]))
    }
}

@Suite struct DepthFrameBudgetTests {
    /// Every ARFrame of 10 s at 60 Hz offered: 2 Hz keeps one every 30 frames, 20 in all, each
    /// at least the interval less the slack after the last.
    @Test func keepsTwoAFrameSecondFromSixtyHertz() {
        var budget = DepthFrameBudget()
        var kept: [Double] = []
        for i in 0..<600 {
            let t = 100 + Double(i) / 60
            if budget.admit(t: t) { kept.append(t) }
        }
        #expect(DepthFrameBudget.rateHz == 2 && DepthFrameBudget.maxFrames == 300)
        #expect(kept.count == 20 && budget.admitted == 20)
        #expect(zip(kept, kept.dropFirst()).allSatisfy { abs(($1 - $0) - 0.5) < 1e-9 })
    }

    /// A replay stepping exactly 0.5 s loses no frame to rounding (125.4 - 124.9 is 0.49999...).
    @Test func roundingDoesNotThinAnIntervalStream() {
        var budget = DepthFrameBudget()
        let times = [124.4, 124.9, 125.4, 125.9]
        #expect(times.allSatisfy { budget.admit(t: $0) })
        #expect(!budget.admit(t: 126.2) && !budget.admit(t: 125.9) && !budget.admit(t: .nan))
    }

    /// Past the limit nothing is admitted, and `wants` says so before any copy is made.
    @Test func stopsAtTheLimit() {
        var budget = DepthFrameBudget(rateHz: 10, limit: 3)
        #expect((0..<10).filter { budget.admit(t: Double($0)) }.count == 3)
        #expect(budget.isFull && !budget.wants(t: 100))
    }
}

@Suite struct PacketPlaneTests {
    private func near(_ a: simd_float4x4, _ b: simd_float4x4) -> Bool {
        [(a.columns.0, b.columns.0), (a.columns.1, b.columns.1), (a.columns.2, b.columns.2), (a.columns.3, b.columns.3)]
            .allSatisfy { simd_distance($0.0, $0.1) <= 1e-6 }
    }

    /// An anchor at (1, 2, 3) in the meter frame, unrotated, with its extent's centre at
    /// (0.5, 0, -0.25) and turned 30 degrees: worked by hand, the pose's translation is
    /// (1.5, 2, 2.75) and its x and z axes are (cos 30°, 0, -sin 30°) and (sin 30°, 0, cos 30°).
    @Test func poseIncludesTheExtentCentreAndRotation() {
        var anchor = matrix_identity_float4x4
        anchor.columns.3 = SIMD4(1, 2, 3, 1)
        let plane = PacketPlane(
            id: "p", alignment: .horizontal, classification: .floor, anchorToMeter: anchor, center: SIMD3(0.5, 0, -0.25),
            rotationOnYAxis: .pi / 6, extent: SIMD2(2, 1), boundaryVertices: [])
        let expected = simd_float4x4(
            SIMD4(0.8660254, 0, -0.5, 0), SIMD4(0, 1, 0, 0), SIMD4(0.5, 0, 0.8660254, 0), SIMD4(1.5, 2, 2.75, 1))
        #expect(near(plane.pose, expected), "\(plane.pose)")
        #expect(plane.boundary == nil && PacketPose.isRigid(plane.pose))
        // The last row is exact, as the validator checks it to 1e-9.
        #expect(plane.pose.columns.3.w == 1 && plane.pose.columns.0.w == 0)
    }

    /// The extent's rotation turns the same way as `simd_quatf(angle:axis:)` about +y, the
    /// convention of the existing plane snapshot this replaces.
    @Test func rotationMatchesSimdQuaternions() {
        for angle in stride(from: Float(-3), through: 3, by: 0.5) {
            let turned = PacketPlane.extentInAnchor(center: .zero, rotationOnYAxis: angle)
            #expect(near(turned, simd_float4x4(simd_quatf(angle: angle, axis: SIMD3(0, 1, 0)))), "\(angle)")
        }
    }

    /// The extent's corners, given in the anchor's coordinates as ARKit gives boundary vertices,
    /// come back as (±width/2, ±height/2), for any centre and rotation.
    @Test func boundaryComesBackInTheExtentFrame() throws {
        let extent = SIMD2<Float>(3, 1.2)
        for angle in stride(from: Float(-3), through: 3, by: 0.75) {
            let center = SIMD3<Float>(0.7, 0, -1.9)
            let frame = PacketPlane.extentInAnchor(center: center, rotationOnYAxis: angle)
            let corners: [SIMD2<Float>] = [SIMD2(-1.5, -0.6), SIMD2(1.5, -0.6), SIMD2(1.5, 0.6), SIMD2(-1.5, 0.6)]
            let inAnchor = corners.map { xz -> SIMD3<Float> in
                let p = frame * SIMD4(xz.x, 0, xz.y, 1)
                return SIMD3(p.x, p.y, p.z)
            }
            let plane = PacketPlane(
                id: "p", alignment: .vertical, classification: .wall, anchorToMeter: matrix_identity_float4x4, center: center,
                rotationOnYAxis: angle, extent: extent, boundaryVertices: inAnchor)
            let boundary = try #require(plane.boundary)
            #expect(zip(boundary, corners).allSatisfy { simd_distance($0, $1) < 1e-5 }, "\(angle): \(boundary)")
            // The same vertices in the meter frame are the pose applied to (x, 0, z).
            for (xz, anchorPoint) in zip(boundary, inAnchor) {
                let meter = plane.pose * SIMD4(xz.x, 0, xz.y, 1)
                #expect(simd_distance(SIMD3(meter.x, meter.y, meter.z), anchorPoint) < 1e-5)
            }
        }
    }

    /// A boundary left in the anchor's coordinates is off by the centre (here 1.5 m) and leaves
    /// the extent; the writer refuses the plane, and takes it again without the boundary. A
    /// vertex up to 1 cm past the edge passes, as the validator allows.
    @Test func boundaryOutsideTheExtentIsRefused() throws {
        let folder = temporaryFolder("packet-planes")
        defer { try? FileManager.default.removeItem(at: folder) }
        var writer = try PacketWriter(folder: folder, session: SyntheticPacket().session)
        let extent = SIMD2<Float>(2, 1)
        let rectangle: [SIMD3<Float>] = [SIMD3(0.5, 0, 0), SIMD3(2.5, 0, 0), SIMD3(2.5, 0, 1), SIMD3(0.5, 0, 1)]
        let wrong = PacketPlane(
            id: "wrong", alignment: .horizontal, classification: nil, pose: matrix_identity_float4x4, extent: extent,
            boundary: rectangle.map { SIMD2($0.x, $0.z) })
        #expect(throws: PacketError.invalidPlane(
            id: "wrong",
            reason: "boundary vertex (2.5, 0.0) leaves the 2.0 x 1.0 m extent centred on the pose; is it still in the anchor's coordinates?")) {
            try writer.addPlane(wrong)
        }
        let right = PacketPlane(
            id: "right", alignment: .horizontal, classification: nil, anchorToMeter: matrix_identity_float4x4, center: SIMD3(1.5, 0, 0.5),
            rotationOnYAxis: 0, extent: extent, boundaryVertices: rectangle)
        try writer.addPlane(right)
        var bare = wrong
        bare.boundary = nil
        bare.id = "bare"
        try writer.addPlane(bare)

        let edge: (Float) -> PacketPlane = { past in
            PacketPlane(
                id: "edge\(past)", alignment: .horizontal, classification: nil, pose: matrix_identity_float4x4, extent: extent,
                boundary: [SIMD2(-1 - past, 0), SIMD2(1, -0.5), SIMD2(1, 0.5)])
        }
        try writer.addPlane(edge(0.009))
        #expect(throws: PacketError.self) { try writer.addPlane(edge(0.02)) }
        #expect(throws: PacketError.invalidPlane(id: "few", reason: "boundary has 2 vertices; an outline needs at least 3")) {
            try writer.addPlane(PacketPlane(
                id: "few", alignment: .vertical, classification: nil, pose: matrix_identity_float4x4, extent: extent,
                boundary: [.zero, SIMD2(0.1, 0)]))
        }
        #expect(throws: PacketError.duplicateID("right")) { try writer.addPlane(right) }
    }

    @Test func planesRoundTripThroughJSON() throws {
        let plane = PacketPlane(
            id: "p", alignment: .vertical, classification: .door, anchorToMeter: matrix_identity_float4x4, center: SIMD3(0.25, 0, 0),
            rotationOnYAxis: 0.3, extent: SIMD2(1, 2), boundaryVertices: [SIMD3(0, 0, 0), SIMD3(0.5, 0, 0), SIMD3(0.25, 0, 0.5)])
        let data = try JSONEncoder().encode([plane])
        #expect(try JSONDecoder().decode([PacketPlane].self, from: data) == [plane])
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        #expect((json[0]["boundary_m"] as? [[Double]])?.count == 3)
    }
}

@Suite struct PacketMarkTests {
    private let wall = SceneWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0)

    @Test func pointCountsPerKind() {
        let counts = Dictionary(uniqueKeysWithValues: PacketMark.Kind.allCases.map { ($0.rawValue, $0.pointCount) })
        #expect(counts == [
            "meter": 1, "wall_end": 1, "gas_meter": 1, "ac": 1,
            "door": 2, "window": 2, "garage_door": 2, "drive_edge": 2, "fence": 2,
        ])
    }

    /// Scene features become marks in the meter frame: an opening's corners from its span and
    /// heights (y is height less the meter's 1.5 m), taps moved from the world.
    @Test func sceneFeaturesBecomeMarks() throws {
        let frame = try #require(MeterFrame(wall: wall))
        let door = try PacketMark.from(.opening(kind: .door, span: 0.5 ... 1.4, bottom: 0, top: 2.25, operable: nil), id: "d", wall: wall, frame: frame)
        #expect(door.kind == .door && door.operable == nil)
        #expect(door.points == [SIMD3(0.5, -1.5, 0), SIMD3(1.4, 0.75, 0)])
        let ac = try PacketMark.from(.pointObject(kind: .ac, tap: SIMD3(-2, 0.5, 0.6), bottom: nil, top: nil), id: "a", wall: wall, frame: frame)
        #expect(ac.kind == .ac && ac.points == [SIMD3(-2, -1, 0.6)])
        let fence = try PacketMark.from(.fence(foot: [SIMD3(-1, 0, 3), SIMD3(1, 0, 3)]), id: "f", wall: wall, frame: frame)
        #expect(fence.kind == .fence && fence.points == [SIMD3(-1, -1.5, 3), SIMD3(1, -1.5, 3)])
        #expect(throws: PacketError.self) { try PacketMark.from(.driveway(edge: [SIMD3(0, 0, 1)]), id: "x", wall: wall, frame: frame) }
        let end = PacketMark.wallEnd(id: "e", side: .right, endKind: .limit, s: 2.5, wall: wall, frame: frame, stamp: .marked(at: nil))
        #expect(end.points == [SIMD3(2.5, 0, 0)] && end.side == .right && end.endKind == .limit)
        #expect(PacketMark.meter(id: "m").points == [.zero])
    }

    @Test func marksAndGuidanceRoundTripThroughJSON() throws {
        let frame = try #require(MeterFrame(wall: wall))
        let marks: [PacketMark] = [
            .opening(.garageDoor, id: "g", span: -1 ... 1, bottom: 0, top: 2.2, operable: false, wall: wall, frame: frame, t: 3, photoIDs: ["p00001"]),
            .fence(id: "f", from: SIMD3(0.1, -1.5, 2), to: SIMD3(0.2, -1.5, 3)),
        ]
        let entries = [
            PacketGuidanceEntry(id: "g", kind: .gapPastEnd, origin: .server, message: "Go past the corner", band: .facing, span: -0.5 ... 0.25, tShown: 4, tResolved: 5, outcome: .superseded),
        ]
        let encoder = JSONEncoder()
        #expect(try JSONDecoder().decode([PacketMark].self, from: encoder.encode(marks)) == marks)
        #expect(try JSONDecoder().decode([PacketGuidanceEntry].self, from: encoder.encode(entries)) == entries)
    }

    /// Every guidance kind and outcome name is the spec's.
    @Test func guidanceNames() {
        #expect(PacketGuidanceEntry.Kind.allCases.map(\.rawValue) == ["walk", "tilt_to_ground", "step_back", "mark_end", "closeup", "gap_band", "gap_past_end"])
        #expect(PacketGuidanceEntry.Outcome.allCases.map(\.rawValue) == ["met", "skipped", "cannot_reach", "superseded", "unresolved"])
    }
}

@Suite struct PacketSchemaTests {
    @Test func vendoredManifestSchemaIsTheRecordedRevision() throws {
        let digest = SHA256.hash(data: try SceneSchemas.data(PacketSchema.name)).map { String(format: "%02x", $0) }.joined()
        #expect(digest == PacketSchema.sha256, "Schemas/\(PacketSchema.name) is not t3/packet d5439cf plus attrs.inferred")
    }

    /// The schema is not a rubber stamp: a manifest missing required fields or breaking a pattern
    /// fails.
    @Test func schemaRejectsBrokenManifests() throws {
        let v = try PacketSchema.validator()
        #expect(try v.validate(Data(#"{"packet_version": "2.0", "session": {}, "photos": []}"#.utf8)).count >= 3)
        let badPath = #"{"path": "../x.jpg", "bytes": 1, "sha256": "00"}"#
        let manifest = #"{"packet_version": "1.0", "session": {}, "photos": [], "scene": \#(badPath)}"#
        let errors = try v.validate(Data(manifest.utf8))
        #expect(errors.contains { $0.hasPrefix("$.scene.path") })
        #expect(errors.contains { $0.hasPrefix("$.scene.sha256") })
    }
}
