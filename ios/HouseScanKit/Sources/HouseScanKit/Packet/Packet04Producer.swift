import CryptoKit
import Foundation
import ImageIO
import simd

/// A file of the 0.4 packet that is on disk and will never change: its bytes, digests and, for
/// an image, the record that describes it. The same value registers the file with the capture API
/// and lists it in the final packet, so the two can't disagree.
public struct SealedFile: Codable, Sendable, Equatable {
    public var path: String
    public var bytes: Int
    public var sha256: String
    /// Base64 of the 16-byte MD5, as the register call and the storage PUT's Content-MD5 want it.
    public var md5: String
    public var role: Packet04.Role
    public var contentType: String
    public var meta: Packet04.ImageRecord?
    /// Lower goes first: the meter close-ups before keyframes before streams.
    public var priority: Int

    public var entry: Packet04.FileEntry { .init(path: path, bytes: bytes, sha256: sha256, role: role) }

    static func seal(_ data: Data, path: String, role: Packet04.Role, contentType: String, meta: Packet04.ImageRecord?, priority: Int) -> SealedFile {
        SealedFile(
            path: path, bytes: data.count, sha256: PacketFiles.sha256(data),
            md5: Data(Insecure.MD5.hash(data: data)).base64EncodedString(), role: role, contentType: contentType, meta: meta,
            priority: priority)
    }
}

/// What the capture observed for one saved camera image, read from its own ARFrame when the
/// pixels were taken: the raw ARKit camera-to-world pose, never one corrected into the meter frame.
public struct Packet04Observation: Sendable, Equatable {
    public var t: Double
    public var cameraToWorld: simd_float4x4
    /// [fx, fy, cx, cy] in pixels of the stored JPEG.
    public var intrinsics: SIMD4<Float>
    public var width: Int
    public var height: Int
    public var tracking: PacketTracking
    public var exposure: Packet04.Exposure?

    public init(t: Double, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, width: Int, height: Int, tracking: PacketTracking, exposure: Packet04.Exposure? = nil) {
        self.t = t
        self.cameraToWorld = cameraToWorld
        self.intrinsics = intrinsics
        self.width = width
        self.height = height
        self.tracking = tracking
        self.exposure = exposure
    }
}

/// ARKit depth for one keyframe: Float32 meters and UInt8 confidence, row by row.
public struct Packet04Depth: Sendable {
    public var meters: [Float]
    public var confidence: [UInt8]
    public var width: Int
    public var height: Int

    public init(meters: [Float], confidence: [UInt8], width: Int, height: Int) {
        self.meters = meters
        self.confidence = confidence
        self.width = width
        self.height = height
    }
}

/// Fixed facts about the capture, known when the ARKit session starts and the homeowner consents,
/// so the create request can be sent before the first photo.
public struct Packet04SessionInfo: Codable, Sendable, Equatable {
    public var packetID: String
    public var sessionID: String
    public var source: Packet04.Source.Kind
    public var appVersion: String
    public var deviceModel: String
    public var systemVersion: String
    public var lidarAvailable: Bool
    public var sceneDepthEnabled: Bool
    public var meshReconstructionSupported: Bool?
    public var sceneReconstruction: String?
    public var planeDetection: [String]?
    public var videoWidth: Int
    public var videoHeight: Int
    public var framesPerSecond: Double?
    public var timeZone: String?

    public init(
        packetID: String, sessionID: String, source: Packet04.Source.Kind, appVersion: String, deviceModel: String, systemVersion: String,
        lidarAvailable: Bool, sceneDepthEnabled: Bool, meshReconstructionSupported: Bool?, sceneReconstruction: String?,
        planeDetection: [String]?, videoWidth: Int, videoHeight: Int, framesPerSecond: Double?, timeZone: String?
    ) {
        self.packetID = packetID
        self.sessionID = sessionID
        self.source = source
        self.appVersion = appVersion
        self.deviceModel = deviceModel
        self.systemVersion = systemVersion
        self.lidarAvailable = lidarAvailable
        self.sceneDepthEnabled = sceneDepthEnabled
        self.meshReconstructionSupported = meshReconstructionSupported
        self.sceneReconstruction = sceneReconstruction
        self.planeDetection = planeDetection
        self.videoWidth = videoWidth
        self.videoHeight = videoHeight
        self.framesPerSecond = framesPerSecond
        self.timeZone = timeZone
    }

    /// `arkit_lidar` needs depth the session actually recorded, not a phone that could have.
    public var tier: Packet04.Tier { lidarAvailable && sceneDepthEnabled ? .arkitLidar : .arkit }
}

public enum Packet04Error: Error, Equatable, CustomStringConvertible {
    case invalidID(String)
    case sealedAfterFinish(String)
    case duplicate(String)
    case invalidImage(id: String, reason: String)
    case notRigid(String)
    case unknownKeyframe(String)
    case streamTooSlow(stream: String, measuredHz: Double, minimumHz: Double)
    case streamEmpty(String)
    case noKeyframes
    case invalidPacket([String])

    public var description: String {
        switch self {
        case .invalidID(let id): "\(id) does not fit the server's id pattern"
        case .sealedAfterFinish(let what): "\(what) arrived after the packet was finished"
        case .duplicate(let id): "\(id) was sealed twice"
        case .invalidImage(let id, let reason): "image \(id): \(reason)"
        case .notRigid(let place): "\(place): pose is not a rotation plus a translation"
        case .unknownKeyframe(let id): "no keyframe \(id)"
        case .streamTooSlow(let stream, let hz, let minimum): "\(stream) measured \(hz) Hz; the contract requires at least \(minimum) Hz"
        case .streamEmpty(let stream): "\(stream) has no rows"
        case .noKeyframes: "an ARKit packet needs at least one keyframe"
        case .invalidPacket(let problems): "packet fails its own checks: \(problems.joined(separator: "; "))"
        }
    }
}

/// Builds one 0.4 packet while the capture runs. Each image is written into `folder` and sealed
/// the moment it is observed, so it can be uploaded during the capture; `finish` adds the streams
/// and returns the exact `packet.json` bytes, which are frozen from then on.
///
/// One producer is one ARKit epoch. A world reset ends the packet: the app starts a new producer
/// with a new packet id rather than mixing two worlds under one epoch.
public actor Packet04Producer {
    public nonisolated let folder: URL
    public nonisolated let info: Packet04SessionInfo
    public nonisolated let epoch = "e1"
    private let startedAtUptime: Double
    private let startedAt: Date
    private var keyframes: [Packet04.Keyframe] = []
    private var stills: [Packet04.Still] = []
    private var taps: [Packet04.Tap] = []
    private var files: [SealedFile] = []
    private var finished: Data?

    /// `startedAtUptime` is the uptime of the session's first ARFrame and `startedAt` the wall clock
    /// at that instant.
    public init(folder: URL, info: Packet04SessionInfo, startedAtUptime: Double, startedAt: Date) throws {
        guard Packet04.isStorageID(info.packetID) else { throw Packet04Error.invalidID(info.packetID) }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        self.folder = folder
        self.info = info
        self.startedAtUptime = startedAtUptime
        self.startedAt = startedAt
    }

    public var sealedFiles: [SealedFile] { files }
    public var keyframeCount: Int { keyframes.count }
    public var isFinished: Bool { finished != nil }

    /// The keyframe sealed from the frame at `t`, if there is one: a still taken from a frame that
    /// was already kept links to it instead of repeating it.
    public func keyframeID(at t: Double) -> String? { keyframes.first { $0.timestamp == t }?.id }

    /// "k00001" for 1.
    public static func keyframeID(_ number: Int) -> String {
        let digits = String(number)
        return "k" + String(repeating: "0", count: max(0, 5 - digits.count)) + digits
    }

    // MARK: Images

    /// Writes the JPEG as keyframes/<id>.jpg with, when given, its depth and confidence, and seals
    /// them. `reason` is the contract's keyframe reason (motion, tap, still, request, auto).
    public func sealKeyframe(
        jpeg: Data, observation o: Packet04Observation, reason: String, purpose: String? = nil, depth: Packet04Depth? = nil
    ) throws -> [SealedFile] {
        guard finished == nil else { throw Packet04Error.sealedAfterFinish("keyframe") }
        let id = Self.keyframeID(keyframes.count + 1)
        try check(o, id: id, jpeg: jpeg)
        guard let tracking = Self.trackingText(o.tracking) else {
            throw Packet04Error.invalidImage(id: id, reason: "tracking is limited for a reason the contract has no name for")
        }
        var sealed: [SealedFile] = []
        var depthRef: Packet04.DepthRef?
        if let depth, depth.meters.count == depth.width * depth.height, depth.confidence.count == depth.meters.count, depth.width > 0 {
            let map = try write(PacketFiles.depthData(meters: depth.meters), "keyframes/\(id).depth.f32", role: .depth, type: "application/octet-stream", priority: 20)
            let confidence = try write(Data(depth.confidence), "keyframes/\(id).confidence.u8", role: .confidence, type: "application/octet-stream", priority: 20)
            sealed += [map, confidence]
            depthRef = .init(file: map.path, confidenceFile: confidence.path, w: depth.width, h: depth.height)
        }
        let record = Packet04.Keyframe(
            id: id, img: "keyframes/\(id).jpg", w: o.width, h: o.height, intrinsics: Self.numbers(o.intrinsics),
            pose: PacketPose.columnMajor(o.cameraToWorld), timestamp: o.t, tracking: tracking, epoch: epoch,
            reason: reason, exposure: o.exposure, depth: depthRef, purpose: purpose)
        let image = try write(jpeg, record.img, role: .keyframe, type: "image/jpeg", meta: .keyframe(record), priority: 10)
        keyframes.append(record)
        return [image] + sealed
    }

    /// A checklist still taken from an ARKit keyframe already sealed: the same pixels under
    /// stills/<id>.jpg, linked to the keyframe so it keeps its pose. Stills go first.
    ///
    /// A retake of the same purpose is a new still ("meter_close-2"): sealed files never change,
    /// and `finish` says which one the scale reference names.
    public func sealStill(purpose: String, keyframe keyframeID: String) throws -> SealedFile {
        guard finished == nil else { throw Packet04Error.sealedAfterFinish("still") }
        guard Packet04.isSegmentID(purpose) else { throw Packet04Error.invalidID(purpose) }
        guard let frame = keyframes.first(where: { $0.id == keyframeID }) else { throw Packet04Error.unknownKeyframe(keyframeID) }
        if let same = stills.last(where: { $0.purpose == purpose }), same.keyframe == keyframeID {
            throw Packet04Error.duplicate(same.id)
        }
        let taken = stills.filter { $0.purpose == purpose }.count
        let id = taken == 0 ? purpose : "\(purpose)-\(taken + 1)"
        let jpeg = try Data(contentsOf: folder.appending(path: frame.img))
        let still = Packet04.Still(
            id: id, purpose: purpose, img: "stills/\(id).jpg", w: frame.w, h: frame.h, timestamp: frame.timestamp,
            orientation: 1, keyframe: frame.id, intrinsics: frame.intrinsics, intrinsicsSource: "arkit")
        let file = try write(jpeg, still.img, role: .still, type: "image/jpeg", meta: .still(still), priority: 0)
        stills.append(still)
        return file
    }

    /// A tap on a sealed keyframe's pixel, with the ray rebuilt from that keyframe's own pose and
    /// intrinsics, so the server's replay reproduces it exactly.
    public func addTap(id: String, label: String, keyframe keyframeID: String, pixel: SIMD2<Double>, time: Double, hit: Packet04.TapHit?) throws {
        guard finished == nil else { throw Packet04Error.sealedAfterFinish("tap") }
        guard let frame = keyframes.first(where: { $0.id == keyframeID }) else { throw Packet04Error.unknownKeyframe(keyframeID) }
        let ray = Packet04Check.ray(pose: frame.pose, intrinsics: frame.intrinsics, pixel: [pixel.x, pixel.y])
        taps.append(.init(
            id: id, time: time, label: label, keyframe: keyframeID, pixel: [pixel.x, pixel.y], rayOrigin: ray.origin,
            rayDirection: ray.direction, hit: hit, epoch: epoch))
    }

    /// The frame the homeowner tapped, kept as a keyframe (reason `tap`) unless it already is one,
    /// and the tap on it: `pixel` in that frame's stored image, the ray rebuilt from its own pose
    /// and intrinsics, and what the phone's raycast hit.
    public func sealTap(
        id: String, label: String, jpeg: Data, observation: Packet04Observation, pixel: SIMD2<Double>, hit: Packet04.TapHit?
    ) throws -> [SealedFile] {
        var files: [SealedFile] = []
        if keyframeID(at: observation.t) == nil {
            files = try sealKeyframe(jpeg: jpeg, observation: observation, reason: "tap")
        }
        guard let keyframe = keyframeID(at: observation.t) else { throw Packet04Error.unknownKeyframe("at \(observation.t)") }
        try addTap(id: id, label: label, keyframe: keyframe, pixel: pixel, time: observation.t, hit: hit)
        return files
    }

    // MARK: Finish

    /// Writes the two required streams, then returns their sealed files and the exact packet
    /// bytes. The packet is checked against the contract's rules first; a failure names each
    /// problem instead of sending a packet the server would reject.
    /// `acceptedCloseUpAt` is the frame time of the head-on close-up the scan accepted, nil when
    /// none was (skipped, or every shot refused): only that still is the scale reference.
    public func finish(
        poses: [Packet04Streams.PoseRow], accelerometer: [Packet04Streams.MotionRow], gyroscope: [Packet04Streams.MotionRow],
        endedAtUptime: Double, acceptedCloseUpAt: Double?, closeUpDistanceM: Double? = nil, createdAt: Date = Date()
    ) throws -> (streams: [SealedFile], packet: Data) {
        if let finished { return (files.filter { $0.role == .stream }, finished) }
        guard !keyframes.isEmpty else { throw Packet04Error.noKeyframes }
        let posesCSV = try Packet04Streams.arkitPoses(poses, epoch: epoch)
        let imu = try Packet04Streams.imuRaw(accelerometer: accelerometer, gyroscope: gyroscope)
        let poseFile = try write(try Gzip.compress(posesCSV.csv), "streams/arkit_poses.csv.gz", role: .stream, type: "application/gzip", priority: 30)
        let imuFile = try write(try Gzip.compress(imu.csv), "streams/imu_raw.csv.gz", role: .stream, type: "application/gzip", priority: 30)
        // Each sensor as Core Motion timed it, so the server can re-pair them its own way.
        let accelFile = try write(
            try Gzip.compress(try Packet04Streams.rawMotionCSV(accelerometer, columns: ["t", "ax", "ay", "az"])),
            "streams/accelerometer_raw.csv.gz", role: .stream, type: "application/gzip", priority: 31)
        let gyroFile = try write(
            try Gzip.compress(try Packet04Streams.rawMotionCSV(gyroscope, columns: ["t", "gx", "gy", "gz"])),
            "streams/gyroscope_raw.csv.gz", role: .stream, type: "application/gzip", priority: 31)
        var ext = imu.pairingNote
        ext["rawAccelerometerFile"] = accelFile.path
        ext["rawGyroscopeFile"] = gyroFile.path

        let close = acceptedCloseUpAt.flatMap { t in stills.last { $0.purpose == "meter_close" && $0.timestamp == t } }
        // The app takes no oblique close-up yet, so none is ever accepted.
        let oblique: Packet04.Still? = nil
        let packet = Packet04.Packet(
            packetId: info.packetID, createdAt: PacketWriter.iso8601(createdAt),
            source: .init(kind: info.source, appBuild: info.appVersion),
            session: .init(
                id: info.sessionID, startedAt: PacketWriter.iso8601(startedAt), startedAtUptime: startedAtUptime,
                endedAtUptime: max(endedAtUptime, startedAtUptime), timeZone: info.timeZone, appVersion: info.appVersion,
                deviceModel: info.deviceModel, systemVersion: info.systemVersion, tier: info.tier, lidarAvailable: info.lidarAvailable,
                meshReconstructionSupported: info.meshReconstructionSupported, sceneDepthEnabled: info.sceneDepthEnabled,
                sceneReconstruction: info.sceneReconstruction, planeDetection: info.planeDetection,
                videoFormat: .init(width: info.videoWidth, height: info.videoHeight, framesPerSecond: info.framesPerSecond),
                motionReferenceFrame: nil),
            epochs: [.init(id: epoch, startTime: startedAtUptime, reason: "sessionStart")],
            keyframes: keyframes, stills: stills.isEmpty ? nil : stills, taps: taps.isEmpty ? nil : taps,
            tracking: Packet04Streams.trackingChanges(poses, epoch: epoch),
            streams: .init(
                imuRaw: .init(file: imuFile.path, columns: Packet04Streams.imuColumns, rateHz: imu.rateHz),
                arkitPoses: .init(file: poseFile.path, columns: Packet04Streams.poseColumns, rateHz: posesCSV.rateHz)),
            scaleReference: .init(
                meterCloseUp: close.map { .init(captured: true, still: $0.id, arkitDistanceM: closeUpDistanceM, side: nil) } ?? .notCaptured,
                ext: .init(obliqueCloseUp: oblique.map { .init(captured: true, still: $0.id, arkitDistanceM: nil, side: nil) } ?? .notCaptured)),
            files: files.map(\.entry),
            ext: ext)
        let problems = Packet04Check.problems(packet, folder: folder)
        guard problems.isEmpty else { throw Packet04Error.invalidPacket(problems) }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(packet)
        try data.write(to: folder.appending(path: "packet.json"), options: .atomic)
        finished = data
        return ([poseFile, imuFile, accelFile, gyroFile], data)
    }

    // MARK: Helpers

    private func check(_ o: Packet04Observation, id: String, jpeg: Data) throws {
        guard PacketPose.isRigid(o.cameraToWorld) else { throw Packet04Error.notRigid("keyframe \(id)") }
        guard o.t.isFinite, o.t >= 0 else { throw Packet04Error.invalidImage(id: id, reason: "timestamp \(o.t)") }
        if let problem = PacketWriter.intrinsicsProblem(o.intrinsics, width: o.width, height: o.height) {
            throw Packet04Error.invalidImage(id: id, reason: problem)
        }
        if let problem = Self.jpegProblem(jpeg, width: o.width, height: o.height) {
            throw Packet04Error.invalidImage(id: id, reason: problem)
        }
        if let same = keyframes.first(where: { $0.timestamp == o.t }) {
            throw Packet04Error.invalidImage(id: id, reason: "keyframe \(same.id) already has timestamp \(o.t)")
        }
    }

    private func write(_ data: Data, _ path: String, role: Packet04.Role, type: String, meta: Packet04.ImageRecord? = nil, priority: Int) throws -> SealedFile {
        guard Packet04.isRelPath(path) else { throw Packet04Error.invalidID(path) }
        guard !files.contains(where: { $0.path == path }) else { throw Packet04Error.duplicate(path) }
        let url = folder.appending(path: path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        let file = SealedFile.seal(data, path: path, role: role, contentType: type, meta: meta, priority: priority)
        files.append(file)
        return file
    }

    /// The bytes must be a JPEG of the stated size, stored unrotated.
    static func jpegProblem(_ jpeg: Data, width: Int, height: Int) -> String? {
        guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil), CGImageSourceGetType(source) as String? == "public.jpeg",
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        else { return "not a readable JPEG" }
        let w = properties[kCGImagePropertyPixelWidth] as? Int, h = properties[kCGImagePropertyPixelHeight] as? Int
        guard w == width, h == height else { return "the JPEG is \(w ?? 0)x\(h ?? 0), not \(width)x\(height)" }
        if let orientation = properties[kCGImagePropertyOrientation] as? Int, orientation != 1 {
            return "EXIF orientation \(orientation); store the unrotated sensor image"
        }
        return nil
    }

    static func numbers(_ k: SIMD4<Float>) -> [Double] { [k.x, k.y, k.z, k.w].map(PacketNumber.double) }

    /// The contract's tracking state. Nil for a limited state whose reason the contract has no
    /// name for (a reason added after iOS 26): no value there would be an observation.
    public static func trackingText(_ tracking: PacketTracking) -> String? {
        switch tracking {
        case .normal: "normal"
        case .notAvailable: "notAvailable"
        case .limited(let why):
            switch why {
            case .initializing?: "limited.initializing"
            case .excessiveMotion?: "limited.excessiveMotion"
            case .insufficientFeatures?: "limited.insufficientFeatures"
            case .relocalizing?: "limited.relocalizing"
            case nil: nil
            }
        }
    }
}
