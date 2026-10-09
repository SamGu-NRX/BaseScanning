import Foundation

/// manifest.json of a capture packet, version 1.1: the field names and nesting of
/// packet/manifest.schema.json on t3/packet (d5439cf), vendored in the tests as
/// Schemas/manifest.schema.json. Optional fields are left out when nil, never written as null.
/// Lengths are meters, times seconds of device uptime, poses 16 numbers column by column in the
/// meter frame (`MeterFrame`).
public struct PacketManifest: Codable, Sendable, Equatable {
    public static let version = "1.1"

    /// The packet format version this manifest claims, `PacketManifest.version` when the app
    /// wrote it. A reader takes it before anything else: 1.0 keeps planes inside `lidar`, and
    /// only 1.1 has `depth_frames` and top-level `planes`.
    public var packetVersion: String
    /// Who wrote the packet, on what device, when, and in what frame of reference (`Session`).
    public var session: Session
    /// The kept stills. Each names its JPEG in the packet and carries the camera that took it:
    /// pose, intrinsics, tracking, and the depth taken with it when the phone had depth.
    public var photos: [Photo]
    /// Depth recorded between photos, without an image (1.1).
    public var depthFrames: [DepthFrame]?
    /// The sensor streams' CSVs (`PacketStream`), with row counts and each sensor's nominal
    /// rate. Absent when no stream was recorded.
    public var streams: Streams?
    /// `ARPlaneAnchor`s, at the top level from 1.1.
    public var planes: [PacketPlane]?
    /// The LiDAR mesh, when the session captured one (`PacketWriter.setMesh`).
    public var lidar: Lidar?
    /// What the homeowner marked: the meter, the wall's ends, openings, gas meter, AC unit,
    /// fence and drive edge (`PacketMark`).
    public var marks: [PacketMark]?
    /// The guidance requests the homeowner was shown and how each ended (`PacketGuidanceEntry`).
    public var guidance: [PacketGuidanceEntry]?
    /// scene.json, the survey's result, carried as a packet file (`PacketWriter.setScene`).
    public var scene: SceneFile?
    /// Where a converted sample came from. The app never writes it.
    public var provenance: Provenance?

    enum CodingKeys: String, CodingKey {
        case packetVersion = "packet_version"
        case depthFrames = "depth_frames"
        case session, photos, streams, planes, lidar, marks, guidance, scene, provenance
    }

    /// A file in the packet: its path relative to manifest.json, size and SHA-256 (lowercase hex).
    public struct File: Codable, Sendable, Equatable {
        public var path: String
        public var bytes: Int
        public var sha256: String

        public init(path: String, bytes: Int, sha256: String) {
            self.path = path
            self.bytes = bytes
            self.sha256 = sha256
        }
    }

    /// The manifest's session section: who produced the packet, on what device, when the capture
    /// ran, and where the meter frame sits in the world.
    ///
    /// `worldAlignment` is always "gravity", and that is what the rest of the packet leans on:
    /// the world ARKit's poses come in has +y up, which is the y the meter frame keeps.
    public struct Session: Codable, Sendable, Equatable {
        public var id: String
        public var producer: Producer
        public var device: Device
        public var capture: Capture
        /// Always "gravity".
        public var worldAlignment: String
        public var meterAnchor: MeterAnchor
        /// Absent: location and heading are not recorded.
        public var consent: Consent?

        enum CodingKeys: String, CodingKey {
            case id, producer, device, capture, consent
            case worldAlignment = "world_alignment"
            case meterAnchor = "meter_anchor"
        }
    }

    /// The program that wrote the packet: the capture app, or a converter that packaged a
    /// capture from outside the app (`kind`).
    public struct Producer: Codable, Sendable, Equatable {
        public enum Kind: String, Codable, Sendable {
            case app
            case converter
        }

        public var kind: Kind
        public var name: String
        public var version: String
        public var commit: String?

        public init(kind: Kind, name: String, version: String, commit: String? = nil) {
            self.kind = kind
            self.name = name
            self.version = version
            self.commit = commit
        }
    }

    /// The phone the capture was made on, named by hardware model, with the capabilities that
    /// decide what the packet may hold: no `lidar`, no ARKit scene depth; and a mesh needs
    /// `meshClassificationEnabled` to say whether ARKit classified it.
    public struct Device: Codable, Sendable, Equatable {
        /// Hardware identifier such as "iPhone16,1" (`utsname.machine`), never the phone's name.
        public var model: String
        public var iosVersion: String?
        /// The device supports `.sceneDepth`.
        public var lidar: Bool
        public var sceneDepthEnabled: Bool?
        public var meshEnabled: Bool?
        /// The session's `sceneReconstruction` included `.meshWithClassification` (1.1). The writer
        /// refuses a mesh while this is nil, and a mesh with any face classified while it is false:
        /// with classification off every class is 0, which a reader could not otherwise tell from
        /// ARKit's own "none".
        public var meshClassificationEnabled: Bool?

        public init(
            model: String, iosVersion: String?, lidar: Bool, sceneDepthEnabled: Bool?, meshEnabled: Bool?,
            meshClassificationEnabled: Bool?
        ) {
            self.model = model
            self.iosVersion = iosVersion
            self.lidar = lidar
            self.sceneDepthEnabled = sceneDepthEnabled
            self.meshEnabled = meshEnabled
            self.meshClassificationEnabled = meshClassificationEnabled
        }

        enum CodingKeys: String, CodingKey {
            case model, lidar
            case iosVersion = "ios_version"
            case sceneDepthEnabled = "scene_depth_enabled"
            case meshEnabled = "mesh_enabled"
            case meshClassificationEnabled = "mesh_classification_enabled"
        }
    }

    /// When the capture ran. The uptime times are the packet's clock — every other time in the
    /// packet is a device uptime too; the wall-clock start and the walked distance are
    /// conveniences for readers.
    public struct Capture: Codable, Sendable, Equatable {
        /// ISO 8601 UTC wall clock at `startedAtUptime`.
        public var startedAt: String?
        public var startedAtUptime: Double
        public var endedAtUptime: Double
        public var distanceWalkedM: Double?

        enum CodingKeys: String, CodingKey {
            case startedAt = "started_at"
            case startedAtUptime = "started_at_uptime"
            case endedAtUptime = "ended_at_uptime"
            case distanceWalkedM = "distance_walked_m"
        }
    }

    /// Where the meter frame sits in the world: the transform every pose in the packet is
    /// measured against, and the ground height at the meter when it was measured.
    public struct MeterAnchor: Codable, Sendable, Equatable {
        /// Meter frame to world, 16 numbers column by column.
        public var poseInWorld: [Double]
        /// The ground at the meter on the meter frame's y (negative).
        public var groundYM: Double?

        enum CodingKeys: String, CodingKey {
            case poseInWorld = "pose_in_world"
            case groundYM = "ground_y_m"
        }
    }

    /// What the homeowner consented to record. Only recording location and heading needs it,
    /// and the app records neither this round, so the app writes no consent at all.
    public struct Consent: Codable, Sendable, Equatable {
        public var location: Bool?
    }

    /// One kept photo: the JPEG stored in the packet, the camera that took it, and its depth
    /// when the phone had depth.
    ///
    /// `t` is device uptime, the frame clock every time in the packet is on; `pose` is camera to
    /// meter frame; `intrinsics` are in the stored JPEG's pixels, which the writer checks are
    /// the unrotated sensor image of the size given (`PacketWriter.addPhoto`).
    public struct Photo: Codable, Sendable, Equatable {
        public var id: String
        public var image: File
        public var width: Int
        public var height: Int
        public var t: Double
        /// Camera to meter frame.
        public var pose: [Double]
        /// [fx, fy, cx, cy] in pixels of the stored JPEG.
        public var intrinsics: [Double]
        public var tracking: Tracking?
        public var exposure: PacketExposure?
        public var lens: PacketLens?
        public var sharpness: Sharpness?
        public var depth: Depth?
    }

    /// ARKit's tracking state at one moment, as the packet names the states (`PacketTracking`).
    public struct Tracking: Codable, Sendable, Equatable {
        /// "normal", "limited" or "not_available".
        public var state: String
        /// A limited state's reason; absent when ARKit gave none this format names.
        public var reason: String?
    }

    /// A photo's sharpness score and the method that scored it, `PacketSharpness.method`.
    public struct Sharpness: Codable, Sendable, Equatable {
        public var method: String
        public var value: Double
    }

    /// A photo's depth map: the photo's field of view at lower resolution and the photo's aspect
    /// to 1% (`DepthPacket`).
    public struct Depth: Codable, Sendable, Equatable {
        public var map: File
        public var confidence: File?
        /// Float32 meters, one standard deviation per pixel (1.1).
        public var sigma: File?
        public var width: Int
        public var height: Int
        public var source: DepthPacket.Source?
    }

    /// Depth recorded between photos (1.1): the depth fields of `Depth` with the camera that took
    /// it, and intrinsics in pixels of the depth map itself.
    public struct DepthFrame: Codable, Sendable, Equatable {
        public var id: String
        public var t: Double
        /// Camera to meter frame.
        public var pose: [Double]
        /// [fx, fy, cx, cy] in pixels of the depth map.
        public var intrinsics: [Double]
        public var tracking: Tracking?
        public var map: File
        public var confidence: File?
        public var sigma: File?
        public var width: Int
        public var height: Int
        public var source: DepthPacket.Source?
    }

    /// A stream's CSV file.
    public struct Stream: Codable, Sendable, Equatable {
        public var path: String
        public var bytes: Int
        public var sha256: String
        public var rows: Int
        public var nominalRateHz: Double?

        enum CodingKeys: String, CodingKey {
            case path, bytes, sha256, rows
            case nominalRateHz = "nominal_rate_hz"
        }
    }

    /// The packet's stream files, one per sensor that recorded (`PacketStream`). A sensor with
    /// no rows is absent, not an empty file.
    public struct Streams: Codable, Sendable, Equatable {
        public var trajectory: Stream?
        public var accelerometer: Stream?
        public var gyroscope: Stream?
        public var magnetometer: Stream?
        public var deviceMotion: Stream?
        public var barometer: Stream?
        /// Not recorded by the app this round; here so a reader of any 1.0 packet decodes it.
        public var location: Stream?
        public var heading: Stream?

        enum CodingKeys: String, CodingKey {
            case trajectory, accelerometer, gyroscope, magnetometer, barometer, location, heading
            case deviceMotion = "device_motion"
        }

        subscript(stream: PacketStream) -> Stream? {
            get {
                switch stream {
                case .trajectory: trajectory
                case .accelerometer: accelerometer
                case .gyroscope: gyroscope
                case .magnetometer: magnetometer
                case .deviceMotion: deviceMotion
                case .barometer: barometer
                }
            }
            set {
                switch stream {
                case .trajectory: trajectory = newValue
                case .accelerometer: accelerometer = newValue
                case .gyroscope: gyroscope = newValue
                case .magnetometer: magnetometer = newValue
                case .deviceMotion: deviceMotion = newValue
                case .barometer: barometer = newValue
                }
            }
        }
    }

    /// The LiDAR mesh. Planes lived here in 1.0; from 1.1 they go at the manifest's top level,
    /// and a packet uses one place or the other, not both (`PacketManifest.planes`).
    public struct Lidar: Codable, Sendable, Equatable {
        public var mesh: File?
        /// Where 1.0 put planes. Decoded so a 1.0 packet reads; the writer puts planes at the top
        /// level and leaves this out, since a packet may not use both.
        public var planes: [PacketPlane]?
    }

    /// scene.json as a packet file, with the schema version the scene claims when the producer
    /// noted it (`PacketWriter.setScene`).
    public struct SceneFile: Codable, Sendable, Equatable {
        public var path: String
        public var bytes: Int
        public var sha256: String
        public var schemaVersion: String?

        enum CodingKeys: String, CodingKey {
            case path, bytes, sha256
            case schemaVersion = "schema_version"
        }
    }

    /// Where a converted packet's capture came from: the dataset or file it was cut from, its
    /// licence, and the converter's notes. The app writes none of it.
    public struct Provenance: Codable, Sendable, Equatable {
        public var dataset: String?
        public var license: String?
        public var source: String?
        public var notes: [String]?
    }
}

/// `ARCamera.exposureDuration` and `exposureOffset`, and ISO from a still's metadata. Each is
/// optional; `durationS` and `iso` must be positive.
public struct PacketExposure: Codable, Sendable, Equatable {
    /// `ARCamera.exposureDuration`, seconds.
    public var durationS: Double?
    /// ISO, from the still's metadata.
    public var iso: Double?
    /// `ARCamera.exposureOffset`, EV.
    public var offsetEV: Double?

    /// All fields default to nil; pass what the session reported.
    public init(durationS: Double? = nil, iso: Double? = nil, offsetEV: Double? = nil) {
        self.durationS = durationS
        self.iso = iso
        self.offsetEV = offsetEV
    }

    enum CodingKeys: String, CodingKey {
        case iso
        case durationS = "duration_s"
        case offsetEV = "offset_ev"
    }
}

/// From a still's EXIF. `camera` is "wide", "ultra_wide" or "telephoto".
public struct PacketLens: Codable, Sendable, Equatable {
    /// The still's EXIF focal length, millimetres.
    public var focalLengthMM: Double?
    /// The still's EXIF f-number.
    public var fNumber: Double?
    /// Which lens took the still: "wide", "ultra_wide" or "telephoto".
    public var camera: String?

    /// All fields default to nil; pass what the still's EXIF carried.
    public init(focalLengthMM: Double? = nil, fNumber: Double? = nil, camera: String? = nil) {
        self.focalLengthMM = focalLengthMM
        self.fNumber = fNumber
        self.camera = camera
    }

    enum CodingKeys: String, CodingKey {
        case camera
        case focalLengthMM = "focal_length_mm"
        case fNumber = "f_number"
    }
}
