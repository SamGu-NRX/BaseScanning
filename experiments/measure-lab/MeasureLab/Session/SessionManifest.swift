import Foundation
import MeasureGeometry

/// The contents of session.json. The README's "Session format" section documents every field;
/// change both together and bump `formatVersion` when a field changes meaning.
///
/// Lengths are ARKit meters in the gravity-aligned world frame (y up). Vectors are [x, y, z].
struct SessionManifest: Codable, Sendable {
    var format = "measure-lab-session"
    var formatVersion = 2
    var units = Units()
    var conventions = Conventions()
    var session: SessionInfo
    var gates: GateValues
    var keyframes: [KeyframeRecord] = []
    var taps: [TapRecord] = []
    var points: [PointRecord] = []
    var walls: [WallRecord] = []
    var measurements: [MeasurementRecord] = []
    var refusals: [RefusalRecord] = []
    var tracking: [TrackingRecord] = []
}

struct Units: Codable, Sendable {
    var length = "meters"
    var angle = "degrees"
    var time = "seconds of device uptime, the clock of ARFrame.timestamp"
    var image = "pixels of the saved JPEG"
}

struct Conventions: Codable, Sendable {
    var world = "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), origin and heading fixed where the session started"
    var camera = "ARKit camera frame: +x right and +y up in the unrotated sensor image, the camera looks along -z"
    var pose = "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout)"
    var intrinsics = "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape JPEG"
    var pixel = "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the JPEG, v grows down"
    var ray = "origin is the camera position; direction is a unit vector through the tapped pixel"
}

struct SessionInfo: Codable, Sendable {
    let id: String
    /// Wall-clock start, ISO 8601 UTC.
    let startedAt: String
    /// Device uptime at `startedAt`, to line wall-clock time up with the other timestamps.
    let startedAtUptime: Double
    let appVersion: String
    let deviceModel: String
    let systemVersion: String
    /// `ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)`: the phone has LiDAR.
    let lidarAvailable: Bool
    /// `supportsSceneReconstruction(.mesh)`. The outdoor protocol expects false on the test phone.
    let meshReconstructionSupported: Bool
    /// Whether this session asked ARKit for LiDAR scene depth maps.
    let sceneDepthEnabled: Bool
}

/// The acceptance thresholds this session ran with, so a replay can apply the same ones.
struct GateValues: Codable, Sendable {
    let trackingStableSeconds: Double
    let minimumGroundLookDown: Double
    let minimumContactSeparation: Double
    let maximumAngleFromWallNormal: Double
    let wallValidationTolerance: Double
    let minimumCameraOffsetFromWall: Double
    let minimumRayAngle: Double
    let maximumRayGap: Double
    let keyframeSpacingMeters: Double
    let keyframeSpacingDegrees: Double
}

struct KeyframeRecord: Codable, Sendable, Identifiable {
    enum Reason: String, Codable, Sendable {
        /// The camera moved or turned past the spacing threshold.
        case motion
        /// Saved for a tap on the live view.
        case tap
        /// Saved when the frame was frozen for tapping.
        case freeze
    }

    struct Depth: Codable, Sendable {
        /// Float32 little-endian meters, row-major, `w` × `h`.
        let file: String
        /// UInt8 ARConfidenceLevel (0 low, 1 medium, 2 high), same layout, or nil if ARKit gave none.
        let confidenceFile: String?
        let w: Int
        let h: Int
    }

    let id: String
    /// Path of the JPEG relative to the session folder.
    let img: String
    let w: Int
    let h: Int
    /// [fx, fy, cx, cy] for this JPEG.
    let intrinsics: [Double]
    /// Camera-to-world, column-major.
    let pose: [Double]
    let timestamp: Double
    let tracking: String
    let reason: Reason
    let depth: Depth?
}

struct TapRecord: Codable, Sendable {
    let id: String
    let time: Double
    let tool: String
    /// Tool step, for example "firstContact" or "secondView".
    let step: String
    let keyframe: String
    /// True when the tap was on a frozen frame rather than the live view.
    let frozen: Bool
    let pixel: [Double]
    let rayOrigin: SIMD3<Double>
    let rayDirection: SIMD3<Double>
    /// Live taps only: pixel distance between ARKit's display transform and the app's own
    /// portrait mapping for the same screen point. Near zero means frozen-frame taps map correctly.
    let displayMappingCheck: Double?
    /// The point this tap produced, or nil when it was refused or only started a two-view pair.
    var point: String?
    var refusal: String?
}

struct PointRecord: Codable, Sendable, Identifiable {
    enum Kind: String, Codable, Sendable {
        case ground
        case wall
        case twoView
    }

    struct Ground: Codable, Sendable {
        /// "detectedPlane" (inside a found plane), "extendedPlane" (a found plane extended past its
        /// edge) or "estimatedPlane" (ARKit's guess without a found plane).
        let surface: String
        let planeAnchor: String?
        let lookDown: Double
    }

    struct OnWall: Codable, Sendable {
        let wall: String
        let range: Double
        let angleFromNormal: Double
    }

    struct TwoView: Codable, Sendable {
        let firstTap: String
        let secondTap: String
        let rayAngle: Double
        let gap: Double
        let baseline: Double
        let t1: Double
        let t2: Double
    }

    /// Position relative to the most recent wall when the point was made.
    struct WallCoordinates: Codable, Sendable {
        let wall: String
        let along: Double
        let heightAboveGround: Double
        let offset: Double
        let withinContacts: Bool
    }

    let id: String
    let kind: Kind
    let position: SIMD3<Double>
    let taps: [String]
    let ground: Ground?
    let onWall: OnWall?
    let twoView: TwoView?
    let wallCoordinates: WallCoordinates?
    /// Warnings about this point's own observation: "estimatedPlane", "extendedPlane",
    /// "shallowLookDown", "outsideWallContacts".
    let flags: [MeasurementWarning]
    /// For a point on a wall: that wall's warnings when the point was made. The wall's final
    /// state is in `walls[].warnings`.
    let wallWarnings: [MeasurementWarning]
}

struct WallRecord: Codable, Sendable, Identifiable {
    struct Validation: Codable, Sendable {
        let point: String
        let residual: Double
        let tolerance: Double
        let passes: Bool
    }

    let id: String
    let contacts: [String]
    let start: SIMD3<Double>
    let end: SIMD3<Double>
    let direction: SIMD3<Double>
    let normal: SIMD3<Double>
    let length: Double
    let cameraPosition: SIMD3<Double>
    var validations: [Validation]
    /// "wallContactWarning", "wallNotValidated", "wallValidationFailed"; updated after each check.
    /// A passing check whose point has its own flags leaves "wallNotValidated" in place.
    var warnings: [MeasurementWarning]
}

struct MeasurementRecord: Codable, Sendable, Identifiable {
    struct Tape: Codable, Sendable {
        let feet: Double
        let inches: Double
        let meters: Double
    }

    let id: String
    let time: Double
    let from: String
    /// A point id or a wall id.
    let to: String
    /// The wall used for along-wall distance, if any.
    let referenceWall: String?
    /// Every quantity that applies, keyed by name ("straight", "horizontal", "vertical",
    /// "alongWall", "gapToWall", "heightAboveGround"). Height above ground is signed.
    let values: [String: Double]
    /// The quantity compared with the tape.
    let compared: String
    let tape: Tape?
    /// App minus tape, meters and inches.
    let errorMeters: Double?
    let errorInches: Double?
    /// Every warning inherited from the points and walls this value depends on, plus
    /// "outsideWallContacts" for a wall read beyond its contacts and "belowGround" for a negative
    /// height. A later wall check can add warnings; none is ever removed.
    var warnings: [MeasurementWarning]
    /// True only with no warnings. Scoring counts any other measurement as an abstention.
    var accepted: Bool
}

struct RefusalRecord: Codable, Sendable {
    let id: String
    let time: Double
    let tool: String
    let tap: String?
    /// Stable machine-readable reason, for example "grazingRay" or "rayAngleTooSmall".
    let reason: String
    let message: String
    /// Numbers behind the refusal, for example the measured angle and its limit.
    let values: [String: Double]
}

struct TrackingRecord: Codable, Sendable {
    let time: Double
    let state: String
}
