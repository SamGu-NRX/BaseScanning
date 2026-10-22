import Foundation
import HouseScanKit
import simd
import Testing

@Suite("ReplaySession")
struct ReplaySessionTests {
    /// Two keyframes listed out of time order. k2 has an identity rotation and translation (1, 2, 3);
    /// k1 has a distinct number in every slot so a row/column mix-up shows.
    static let twoFrames = """
    {
      "format": "measure-lab-session", "formatVersion": 2, "extraLabField": {"anything": [1, 2]},
      "session": {"id": "s1", "deviceModel": "iPhone15,4", "startedAt": "2026-01-01T00:00:00Z"},
      "keyframes": [
        {"id": "k2", "img": "keyframes/k2.jpg", "w": 1920, "h": 1440,
         "intrinsics": [1400, 1401, 960, 720],
         "pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 1,2,3,1],
         "timestamp": 12.5, "tracking": "normal", "reason": "motion", "depth": null},
        {"id": "k1", "img": "keyframes/k1.jpg", "w": 640, "h": 480,
         "intrinsics": [500, 501, 320, 240],
         "pose": [1,2,3,4, 5,6,7,8, 9,10,11,12, 13,14,15,16],
         "timestamp": 10.0, "tracking": "limited.excessiveMotion", "reason": "tap",
         "depth": {"file": "keyframes/k1.depth.f32", "confidenceFile": null, "w": 256, "h": 192}}
      ],
      "taps": [], "points": [], "walls": [], "measurements": [], "refusals": [], "tracking": []
    }
    """

    static func session(format: String = "measure-lab-session", version: Int = 2, keyframes: String) -> Data {
        Data("""
        {"format": "\(format)", "formatVersion": \(version),
         "session": {"id": "s", "deviceModel": "d"}, "keyframes": \(keyframes)}
        """.utf8)
    }

    static let oneFrame = """
    [{"id": "k1", "img": "a.jpg", "w": 4, "h": 3, "intrinsics": [1, 1, 2, 1.5],
      "pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1], "timestamp": 1, "tracking": "normal", "depth": null}]
    """

    @Test func decodesAndSortsFrames() throws {
        let session = try ReplaySession.decode(sessionJSON: Data(Self.twoFrames.utf8))
        #expect(session.id == "s1")
        #expect(session.deviceModel == "iPhone15,4")
        #expect(session.frames.map(\.id) == ["k1", "k2"])
        #expect(session.declaredWall == nil)

        let k1 = session.frames[0]
        #expect(k1.imagePath == "keyframes/k1.jpg")
        #expect(k1.width == 640 && k1.height == 480)
        #expect(k1.intrinsics == SIMD4(500, 501, 320, 240))
        #expect(k1.timestamp == 10.0)
        #expect(k1.trackingNormal == false)
        #expect(k1.cameraToWorld.columns.0 == SIMD4(1, 2, 3, 4))
        #expect(k1.cameraToWorld.columns.2 == SIMD4(9, 10, 11, 12))
        #expect(k1.cameraToWorld[3, 1] == 14)

        let k2 = session.frames[1]
        #expect(k2.trackingNormal)
        #expect(k2.cameraToWorld.columns.3 == SIMD4(1, 2, 3, 1))
        #expect(k2.cameraToWorld * SIMD4(0, 0, 0, 1) == SIMD4(1, 2, 3, 1))
    }

    @Test func sortsEqualTimestampsById() throws {
        let frames = """
        [{"id": "b", "img": "b.jpg", "w": 1, "h": 1, "intrinsics": [1,1,0,0],
          "pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1], "timestamp": 5, "tracking": "normal"},
         {"id": "a", "img": "a.jpg", "w": 1, "h": 1, "intrinsics": [1,1,0,0],
          "pose": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1], "timestamp": 5, "tracking": "normal"}]
        """
        let session = try ReplaySession.decode(sessionJSON: Self.session(keyframes: frames))
        #expect(session.frames.map(\.id) == ["a", "b"])
    }

    @Test func rejectsWrongFormat() {
        #expect(throws: ReplayError.wrongFormat(found: "scene")) {
            try ReplaySession.decode(sessionJSON: Self.session(format: "scene", keyframes: Self.oneFrame))
        }
    }

    @Test func rejectsVersionOne() {
        #expect(throws: ReplayError.unsupportedVersion(1)) {
            try ReplaySession.decode(sessionJSON: Self.session(version: 1, keyframes: Self.oneFrame))
        }
    }

    @Test func rejectsEmptyKeyframes() {
        #expect(throws: ReplayError.noFrames) {
            try ReplaySession.decode(sessionJSON: Self.session(keyframes: "[]"))
        }
    }

    @Test func rejectsShortPose() {
        let frames = Self.oneFrame.replacingOccurrences(of: "0,0,0,1]", with: "0,0,1]")
        #expect(throws: ReplayError.badPose(frameID: "k1")) {
            try ReplaySession.decode(sessionJSON: Self.session(keyframes: frames))
        }
    }

    @Test func rejectsZeroFocalLength() {
        let frames = Self.oneFrame.replacingOccurrences(of: "[1, 1, 2, 1.5]", with: "[0, 1, 2, 1.5]")
        #expect(throws: ReplayError.badIntrinsics(frameID: "k1")) {
            try ReplaySession.decode(sessionJSON: Self.session(keyframes: frames))
        }
    }

    @Test(arguments: [(3, 4, "portrait keyframe; replay requires a landscape sensor image"),
                      (4, 4, "square keyframe; replay requires a landscape sensor image"),
                      (0, 3, "width and height must be integers of at least 1"),
                      (-1, 3, "width and height must be integers of at least 1"),
                      (4, 0, "width and height must be integers of at least 1"),
                      (4, -1, "width and height must be integers of at least 1")])
    func rejectsInvalidImageSize(width: Int, height: Int, reason: String) {
        let frames = Self.oneFrame
            .replacingOccurrences(of: "\"w\": 4", with: "\"w\": \(width)")
            .replacingOccurrences(of: "\"h\": 3", with: "\"h\": \(height)")
        #expect(throws: ReplayError.badImageSize(frameID: "k1", reason: reason)) {
            try ReplaySession.decode(sessionJSON: Self.session(keyframes: frames))
        }
    }

    @Test func namesMissingField() {
        let frames = Self.oneFrame.replacingOccurrences(of: "\"img\": \"a.jpg\", ", with: "")
        #expect(throws: ReplayError.missingField("keyframes[0].img")) {
            try ReplaySession.decode(sessionJSON: Self.session(keyframes: frames))
        }
    }

    @Test func missingFolderIsUnreadable() {
        let folder = URL(fileURLWithPath: "/nonexistent-replay-\(UUID().uuidString)")
        #expect {
            try ReplaySession.load(folder: folder)
        } throws: { error in
            guard case .unreadable(let path, _) = error as? ReplayError else { return false }
            return path.hasSuffix("session.json")
        }
    }

    @Test func declaredWallFromFirstWallAndItsMeter() throws {
        // Normal tilted slightly upward and pointing -x: outward must drop the y part and renormalize.
        // p1 is a wall point on another wall, so the meter must come from p2.
        let json = """
        {"format": "measure-lab-session", "formatVersion": 2,
         "session": {"id": "s", "deviceModel": "d"},
         "keyframes": \(Self.oneFrame),
         "points": [
           {"id": "p0", "kind": "ground", "position": [0, 0.1, 0], "onWall": null},
           {"id": "p1", "kind": "wall", "position": [9, 9, 9], "onWall": {"wall": "w9", "range": 1, "angleFromNormal": 0}},
           {"id": "p2", "kind": "wall", "position": [2, 1.4, -1], "onWall": {"wall": "w1", "range": 2, "angleFromNormal": 5}}
         ],
         "walls": [
           {"id": "w1", "contacts": ["p0", "p5"], "start": [2, 0.1, -3], "end": [2, 0.3, 3],
            "direction": [0, 0, 1], "normal": [-0.6, 0.8, 0], "length": 6},
           {"id": "w9", "start": [0, 0, 0], "end": [1, 0, 0], "normal": [0, 0, 1]}
         ]}
        """
        let wall = try #require(try ReplaySession.decode(sessionJSON: Data(json.utf8)).declaredWall)
        #expect(wall.meter == SIMD3(2, 1.4, -1))
        #expect(simd_distance(wall.outward, SIMD3(-1, 0, 0)) < 1e-6)
        #expect(abs(wall.groundY - 0.2) < 1e-6)
    }

    @Test func noDeclaredWallWithoutMeterOnFirstWall() throws {
        let json = """
        {"format": "measure-lab-session", "formatVersion": 2,
         "session": {"id": "s", "deviceModel": "d"}, "keyframes": \(Self.oneFrame),
         "points": [{"id": "p1", "kind": "wall", "position": [1, 1, 1], "onWall": {"wall": "w2"}}],
         "walls": [{"id": "w1", "start": [0, 0, 0], "end": [1, 0, 0], "normal": [0, 0, 1]}]}
        """
        #expect(try ReplaySession.decode(sessionJSON: Data(json.utf8)).declaredWall == nil)
    }

    /// The fixture written by ios/Tools/make-synthetic-replay.swift: 61 frames (38 for the close-up
    /// and the walk, 3 tilted up for the tilt-up step, then 20 more tilted up along the wall for
    /// the upper rows), wall on z = 0 facing +z, meter at
    /// (0, 1.5, 0), and the first frame aimed straight at the meter.
    @Test func decodesSyntheticWallFixture() throws {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../../HouseScanUITests/Fixtures/synthetic-wall")
            .standardizedFileURL
        let session = try ReplaySession.load(folder: folder)
        #expect(session.id == "synthetic-wall")
        #expect(session.frames.count == 61)
        #expect(session.frames.allSatisfy { $0.trackingNormal })

        let wall = try #require(session.declaredWall)
        #expect(wall.meter == SIMD3(0, 1.5, 0))
        #expect(wall.outward == SIMD3(0, 0, 1))
        #expect(wall.groundY == 0)

        let frame = session.frames[0]
        let p = frame.cameraToWorld.inverse * SIMD4(wall.meter, 1)
        let k = frame.intrinsics
        let u = k[2] + k[0] * p.x / -p.z
        let v = k[3] - k[1] * p.y / -p.z
        #expect(abs(u - 320) < 2 && abs(v - 240) < 2, "meter projects to (\(u), \(v))")
    }

    /// The app plays the fixture's closing run of tilted-up frames for the tilt-up step and for
    /// overhead requests: views reaching at least 0.7 m above the wall band's 2.286 m top
    /// (ScanEngine.tiltUpAbove). The closing 23 frames all do, and the three from the tilt-up
    /// position (38 to 40) over the stretch by the meter. The walk before them (frames 7 to 37,
    /// pitched 20 degrees down) reaches about 2.0 m, short of the band's top, so the run starts
    /// at frame 38. The level views of the meter before the walk may reach it.
    @Test func syntheticWallFixtureEndsWithTiltUpViews() throws {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../../HouseScanUITests/Fixtures/synthetic-wall")
            .standardizedFileURL
        let session = try ReplaySession.load(folder: folder)
        let wall = try #require(session.declaredWall.flatMap { WallFrame(meter: $0.meter, outward: $0.outward, groundY: $0.groundY) })
        let map = CoverageMap(wall: wall)
        let reaches = session.frames.map { frame in
            map.overheadReach(from: CameraFrame(
                cameraToWorld: frame.cameraToWorld, intrinsics: frame.intrinsics,
                imageSize: SIMD2(Float(frame.width), Float(frame.height))))
        }
        let tilted = map.config.wallCaptureHeight + 0.7
        let walkIsNotTiltedUp = reaches[7..<38].allSatisfy { reach in reach.allSatisfy { $0.out < tilted } }
        #expect(walkIsNotTiltedUp)
        for reach in reaches[38..<41] {
            let overMeter = reach.contains { $0.out >= tilted && $0.span.contains(0) && $0.span.contains(1) }
            #expect(overMeter, "\(reach)")
        }
        #expect(reaches[38...].allSatisfy { reach in reach.contains { $0.out >= tilted } })
    }
}
