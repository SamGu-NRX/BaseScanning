import Foundation

/// The capture packet 0.4 that the capture API's finalize call takes as its body: `packet.json`,
/// camelCase, in the ARKit world frame of one epoch. This is the subset House Scan produces,
/// written from the published wire contract, not copied from the server's models. Optional
/// fields are left out when nil, which the server reads as absent.
///
/// Unlike the 1.1 packet (`PacketManifest`), poses here are the raw ARKit camera-to-world
/// transforms, never moved into the meter frame.
public enum Packet04 {
    public static let format = "house-capture-packet"
    public static let formatVersion = "0.4"

    public struct Packet: Codable, Sendable, Equatable {
        public var format = Packet04.format
        public var formatVersion = Packet04.formatVersion
        public var packetId: String
        public var createdAt: String
        public var units = Units()
        public var conventions = Conventions()
        public var source: Source
        public var session: Session
        public var epochs: [Epoch]
        public var keyframes: [Keyframe]
        public var stills: [Still]?
        public var taps: [Tap]?
        public var tracking: [TrackingEvent]
        public var streams: Streams
        public var scaleReference: ScaleReference
        public var files: [FileEntry]
        public var ext: [String: String]?
    }

    public struct Units: Codable, Sendable, Equatable {
        public var length = "meters"
        public var angle = "degrees"
        public var time = "seconds of device uptime (ARFrame.timestamp and Core Motion timestamp)"
        public var image = "pixels of the saved, unrotated landscape JPEG"
        public var rotationRate = "radians per second"
        public var acceleration = "g (9.80665 m/s^2)"
    }

    public struct Conventions: Codable, Sendable, Equatable {
        public var world = "ARKit world, worldAlignment .gravity: right-handed, +y up, origin where the epoch started"
        public var camera = "ARKit camera: +x right, +y up in the unrotated landscape image, looking along -z"
        public var pose = "camera-to-world 4x4, 16 numbers column by column (simd_float4x4 layout), meters"
        public var intrinsics = "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image"
        public var pixel = "[u, v] continuous: (0, 0) is the top-left corner of the top-left pixel, v grows down"
    }

    public struct Source: Codable, Sendable, Equatable {
        public enum Kind: String, Codable, Sendable { case device, replay, synthetic }
        public var kind: Kind
        public var appBuild: String?

        public init(kind: Kind, appBuild: String?) {
            self.kind = kind
            self.appBuild = appBuild
        }
    }

    public enum Tier: String, Codable, Sendable { case arkit, arkitLidar = "arkit_lidar" }

    public struct VideoFormat: Codable, Sendable, Equatable {
        public var imageResolution: [Int]
        public var framesPerSecond: Double?

        public init(width: Int, height: Int, framesPerSecond: Double?) {
            imageResolution = [width, height]
            self.framesPerSecond = framesPerSecond
        }
    }

    public struct Session: Codable, Sendable, Equatable {
        public var id: String
        public var startedAt: String
        public var startedAtUptime: Double
        public var endedAtUptime: Double?
        public var timeZone: String?
        public var appVersion: String
        public var deviceModel: String
        public var systemVersion: String
        public var tier: Tier
        public var lidarAvailable: Bool
        public var meshReconstructionSupported: Bool?
        public var sceneDepthEnabled: Bool
        public var sceneReconstruction: String?
        public var worldAlignment = "gravity"
        public var planeDetection: [String]?
        public var videoFormat: VideoFormat
        public var motionReferenceFrame: String?
    }

    public struct Epoch: Codable, Sendable, Equatable {
        public var id: String
        public var startTime: Double
        public var reason: String

        public init(id: String, startTime: Double, reason: String) {
            self.id = id
            self.startTime = startTime
            self.reason = reason
        }
    }

    public struct Exposure: Codable, Sendable, Equatable {
        public var duration: Double?
        public var offset: Double?
        public var iso: Double?
        public var fNumber: Double?

        public init(duration: Double?, offset: Double?, iso: Double?, fNumber: Double?) {
            self.duration = duration
            self.offset = offset
            self.iso = iso
            self.fNumber = fNumber
        }
    }

    public struct DepthRef: Codable, Sendable, Equatable {
        public var file: String
        public var confidenceFile: String?
        public var w: Int
        public var h: Int
    }

    public struct Keyframe: Codable, Sendable, Equatable {
        public var id: String
        public var img: String
        public var w: Int
        public var h: Int
        public var intrinsics: [Double]
        public var pose: [Double]
        public var timestamp: Double
        public var tracking: String
        public var epoch: String
        public var reason: String
        public var exposure: Exposure?
        public var depth: DepthRef?
        public var purpose: String?
    }

    public struct Still: Codable, Sendable, Equatable {
        public var id: String
        public var purpose: String
        public var img: String
        public var w: Int
        public var h: Int
        public var timestamp: Double
        /// EXIF orientation of the stored pixels. House Scan stores the sensor image unrotated.
        public var orientation: Int
        public var keyframe: String?
        public var intrinsics: [Double]?
        public var intrinsicsSource: String?
    }

    public struct TapHit: Codable, Sendable, Equatable {
        public var position: [Double]
        public var target: String
        public var alignment: String?
        public var distance: Double?

        public init(position: [Double], target: String, alignment: String?, distance: Double?) {
            self.position = position
            self.target = target
            self.alignment = alignment
            self.distance = distance
        }
    }

    public struct Tap: Codable, Sendable, Equatable {
        public var id: String
        public var time: Double
        public var label: String
        public var keyframe: String
        public var pixel: [Double]
        public var rayOrigin: [Double]
        public var rayDirection: [Double]
        public var hit: TapHit?
        public var epoch: String?
    }

    public struct TrackingEvent: Codable, Sendable, Equatable {
        public var time: Double
        public var state: String
        public var epoch: String?

        public init(time: Double, state: String, epoch: String?) {
            self.time = time
            self.state = state
            self.epoch = epoch
        }
    }

    public struct StreamRef: Codable, Sendable, Equatable {
        public var file: String
        public var columns: [String]
        public var rateHz: Double
    }

    public struct Streams: Codable, Sendable, Equatable {
        public var imuRaw: StreamRef
        public var arkitPoses: StreamRef
    }

    public struct CloseUp: Codable, Sendable, Equatable {
        public var captured: Bool
        public var still: String?
        public var arkitDistanceM: Double?
        public var side: String?

        public init(captured: Bool, still: String?, arkitDistanceM: Double?, side: String?) {
            self.captured = captured
            self.still = still
            self.arkitDistanceM = arkitDistanceM
            self.side = side
        }

        public static let notCaptured = CloseUp(captured: false, still: nil, arkitDistanceM: nil, side: nil)
    }

    public struct ScaleExt: Codable, Sendable, Equatable {
        public var method = "a9-meter-model"
        public var obliqueCloseUp: CloseUp
        /// Head-on views of doors on the meter side. House Scan does not detect doors yet, so this
        /// is empty, which the contract allows.
        public var doorViews: [String] = []
    }

    public struct ScaleReference: Codable, Sendable, Equatable {
        public var meterCloseUp: CloseUp
        public var ext: ScaleExt
    }

    public enum Role: String, Codable, Sendable, CaseIterable {
        case keyframe, depth, confidence, features, still, stream, other
    }

    public struct FileEntry: Codable, Sendable, Equatable {
        public var path: String
        public var bytes: Int
        public var sha256: String
        public var role: Role
    }

    /// The `meta` a register call sends with an image: exactly one record, the same one the final
    /// packet lists.
    public enum ImageRecord: Codable, Sendable, Equatable {
        case keyframe(Keyframe)
        case still(Still)

        public var path: String {
            switch self {
            case .keyframe(let k): k.img
            case .still(let s): s.img
            }
        }

        private enum CodingKeys: String, CodingKey { case keyframe, still }

        public func encode(to encoder: any Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            switch self {
            case .keyframe(let k): try container.encode(k, forKey: .keyframe)
            case .still(let s): try container.encode(s, forKey: .still)
            }
        }

        public init(from decoder: any Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            if let k = try container.decodeIfPresent(Keyframe.self, forKey: .keyframe) {
                self = .keyframe(k)
            } else {
                self = .still(try container.decode(Still.self, forKey: .still))
            }
        }
    }

    // MARK: Id and path patterns the server enforces

    /// `^[A-Za-z0-9_-]{8,64}$`: packetId.
    public static func isStorageID(_ s: String) -> Bool {
        (8...64).contains(s.count) && s.unicodeScalars.allSatisfy { isAlnum($0) || $0 == "_" || $0 == "-" }
    }

    /// `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`: keyframe and still ids.
    public static func isSegmentID(_ s: String) -> Bool {
        guard let first = s.unicodeScalars.first, isAlnum(first), s.count <= 128 else { return false }
        return s.unicodeScalars.allSatisfy { isAlnum($0) || $0 == "_" || $0 == "-" }
    }

    /// Every segment starts with an alphanumeric, and a dot always starts a new alphanumeric token.
    public static func isRelPath(_ s: String) -> Bool {
        guard !s.isEmpty else { return false }
        return s.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { segment in
            segment.split(separator: ".", omittingEmptySubsequences: false).allSatisfy { token in
                guard let first = token.unicodeScalars.first, isAlnum(first) else { return false }
                return token.unicodeScalars.allSatisfy { isAlnum($0) || $0 == "_" || $0 == "-" }
            }
        }
    }

    private static func isAlnum(_ c: Unicode.Scalar) -> Bool {
        ("a"..."z").contains(c) || ("A"..."Z").contains(c) || ("0"..."9").contains(c)
    }
}
