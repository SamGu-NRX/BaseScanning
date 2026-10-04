import CoreGraphics
import Foundation
import ImageIO
import simd

/// A made-up capture whose packet says so (`source.kind: synthetic`), for running photo
/// processing end to end without a phone's sensors: the DEBUG app's
/// `-photoProcessingSyntheticCapture` and the package tests.
///
/// The numbers are #192's synthetic recording (`NativeCaptureFixture` in the package tests): a
/// camera 2 m from a wall at 1.5 m height walking +x at 0.1 m/s for 12 s, a 60 Hz trajectory with
/// each frame's intrinsics beside it, and accelerometer and gyro at about 100 Hz on their own
/// clocks with a fixed jitter. Photos are pattern images, no house, taken at times on that
/// trajectory with its poses and intrinsics. Everything is computed from fixed constants, so two
/// runs give the same rows and the same JPEG bytes, and no wall clock enters the data.
///
/// It is never a recording of the scan on screen: a replay keeps no motion, and nothing here is
/// added to one. Like `FixtureCaptureHTTP`, the type is in every build; only the app's activation
/// is limited to debug builds on a replay.
public struct SyntheticCapture: Sendable {
    public static let standard = SyntheticCapture()

    /// Sensor image size and its intrinsics [fx, fy, cx, cy].
    public static let imageSize = SIMD2<Float>(1920, 1440)
    public static let intrinsics = SIMD4<Float>(1445, 1445, 960, 720)
    /// The stored JPEGs are a quarter of the sensor size each way, with the intrinsics scaled to
    /// match (`KeptPhoto.observation`), which keeps writing them fast.
    public static let jpegSize = (width: 480, height: 360)
    /// A fixed wall-clock start, so the packet holds no date from the phone running it.
    public static let startedAt = Date(timeIntervalSince1970: 1_790_000_000)

    public let start = 5000.0
    public let end = 5012.0

    public init() {}

    public func pose(_ t: Double) -> simd_float4x4 {
        var m = matrix_identity_float4x4
        m.columns.3 = SIMD4(Float(t - start) * 0.1, 1.5, 0, 1)
        return m
    }

    /// The close-up the scan accepted, as the coordinator wants it at the capture's end.
    public var acceptedCloseUpAt: Double { start + 2.5 }

    /// The photos, in the order they are kept: the meter close-up, then five walk frames.
    public var photoTimes: [(t: Double, purpose: String?)] {
        var times: [(t: Double, purpose: String?)] = [(start + 2.5, "meter_close")]
        for i in 0..<5 { times.append((start + 3 + Double(i) * 1.5, nil)) }
        return times
    }

    public var recording: RecordingSource {
        let capture = self
        return RecordingSource(start: { (capture.start, Self.startedAt) }, rows: { capture.rows })
    }

    public var rows: RecorderRows {
        var trajectory: [[Double]] = [], intrinsics: [[Double]] = []
        let k = Self.intrinsics
        for i in 0...Int((end - start) * 60) {
            let t = start + Double(i) / 60
            let m = pose(t)
            let columns: [SIMD4<Float>] = [m.columns.0, m.columns.1, m.columns.2, m.columns.3]
            let values: [Double] = columns.flatMap { (c: SIMD4<Float>) -> [Double] in [Double(c.x), Double(c.y), Double(c.z), Double(c.w)] }
            trajectory.append([t, 0, 0] + values)
            intrinsics.append([t, Double(k.x), Double(k.y), Double(k.z), Double(k.w), Double(Self.imageSize.x), Double(Self.imageSize.y)])
        }
        // The fixed jitter keeps the two sensors from ever sharing a timestamp.
        var accel: [[Double]] = [], gyro: [[Double]] = []
        for i in 0..<Int((end - start) * 100) {
            let jitter = Double((i * 7919) % 11) * 0.0001
            accel.append([start + Double(i) * 0.01 + jitter, 0.01, -0.99, 0.02])
            gyro.append([start + Double(i) * 0.01 + 0.0037 - jitter, 0.001, -0.002, 0.0005])
        }
        return RecorderRows(trajectory: trajectory, intrinsics: intrinsics, accelerometer: accel, gyroscope: gyro)
    }

    /// The packet's session facts, labelled synthetic.
    public static func sessionInfo(packetID: String, video: SIMD2<Float>, appVersion: String) -> Packet04SessionInfo {
        Packet04SessionInfo(
            packetID: packetID, sessionID: packetID, source: .synthetic, appVersion: appVersion, deviceModel: "synthetic",
            systemVersion: "synthetic", lidarAvailable: false, sceneDepthEnabled: false, meshReconstructionSupported: nil,
            sceneReconstruction: nil, planeDetection: nil, videoWidth: Int(video.x), videoHeight: Int(video.y), framesPerSecond: nil,
            timeZone: nil)
    }

    /// Writes the photos' JPEGs into `folder` (again only when missing) and returns them as kept
    /// photos.
    public func photos(in folder: URL) throws -> [KeptPhoto] {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return try photoTimes.enumerated().map { index, photo in
            let url = folder.appending(path: "synthetic-\(index).jpg")
            if !FileManager.default.fileExists(atPath: url.path) { try Self.writeJPEG(pattern: index, to: url) }
            return KeptPhoto(
                t: photo.t, cameraToWorld: pose(photo.t), cameraIntrinsics: Self.intrinsics, cameraImageSize: Self.imageSize,
                width: Self.jpegSize.width, height: Self.jpegSize.height, tracking: .normal,
                exposure: .init(duration: 0.004, offset: 0, iso: 64, fNumber: 1.6), jpeg: url, purpose: photo.purpose)
        }
    }

    public enum ImageError: Error, Equatable {
        case notWritten
    }

    /// A colour pattern, shifted per photo so no two are the same image.
    static func writeJPEG(pattern: Int, to url: URL) throws {
        let (width, height) = jpegSize
        guard let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 4 * width,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue),
            let data = context.data
        else { throw ImageError.notWritten }
        let pixels = data.bindMemory(to: UInt8.self, capacity: 4 * width * height)
        for y in 0..<height {
            for x in 0..<width {
                let p = 4 * (y * width + x)
                pixels[p] = UInt8((x * 9 + y * 3 + pattern * 37) % 256)
                pixels[p + 1] = UInt8((x * y) % 256)
                pixels[p + 2] = UInt8((y * 11) % 256)
                pixels[p + 3] = 255
            }
        }
        guard let image = context.makeImage(),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.jpeg" as CFString, 1, nil)
        else { throw ImageError.notWritten }
        let properties: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: 0.9]
        CGImageDestinationAddImage(destination, image, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw ImageError.notWritten }
    }
}

/// Whether a run uses the capture fixture, from its launch options. Only a debug build on a replay
/// may: a release build, or the live camera, gets photo processing as the build has it, which for
/// now means not set up. The app passes `debugBuild` from `#if DEBUG`, which is the compile-time
/// half of the guard; this decides the rest and is what the package tests check.
public enum CaptureFixtureLaunch: Sendable, Equatable {
    case off(String)
    /// The fixture answers with `answer`. `syntheticCapture`: the packet is `SyntheticCapture`'s,
    /// not the replay's photos.
    case on(FixtureCaptureHTTP.Answer, syntheticCapture: Bool)

    public static func resolve(debugBuild: Bool, onReplay: Bool, answer: FixtureCaptureHTTP.Answer?, syntheticCapture: Bool) -> Self {
        guard debugBuild else { return .off("a release build has no capture fixture") }
        guard let answer else {
            return .off(syntheticCapture ? "the synthetic capture needs the capture fixture" : "sending captures is off in this build")
        }
        guard onReplay else { return .off("the capture fixture runs only on a replay, never the live camera") }
        return .on(answer, syntheticCapture: syntheticCapture)
    }
}
