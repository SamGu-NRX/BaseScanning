import Foundation
import simd

/// The streams the app records, each a CSV file with a header row and fixed columns
/// (packet/README.md, "Streams"; `STREAM_COLUMNS` in packet/validate.py). Location and heading
/// are left out: they need the homeowner's consent and are not recorded this round.
public enum PacketStream: String, CaseIterable, Sendable {
    case trajectory
    case accelerometer
    case gyroscope
    case magnetometer
    case deviceMotion = "device_motion"
    case barometer

    /// The stream's header row and column order, `STREAM_COLUMNS` in packet/validate.py; every
    /// row follows it.
    public var columns: [String] {
        switch self {
        case .trajectory: ["t", "tracking", "px", "py", "pz", "qx", "qy", "qz", "qw"]
        case .accelerometer, .gyroscope, .magnetometer: ["t", "x", "y", "z"]
        case .deviceMotion:
            [
                "t", "qx", "qy", "qz", "qw", "gravity_x", "gravity_y", "gravity_z",
                "user_accel_x", "user_accel_y", "user_accel_z",
                "rotation_rate_x", "rotation_rate_y", "rotation_rate_z", "heading_deg",
            ]
        case .barometer: ["t", "pressure_kpa", "relative_altitude_m"]
        }
    }

    /// The CSV's place in the packet folder: `streams/<rawValue>.csv`.
    public var path: String { "streams/\(rawValue).csv" }

    /// Standard gravity, m/s² per g: the packet's accelerometer is in m/s² and Core Motion
    /// reports g.
    public static let standardGravity = 9.80665
}

/// ARKit's tracking state as the packet names it.
public enum PacketTracking: Sendable, Equatable {
    case normal
    /// Nil when ARKit's reason has no name in the format (a reason added after iOS 26).
    case limited(Reason?)
    case notAvailable

    /// The limited-tracking reasons the packet format names. A reason ARKit added later has no
    /// case here and is written with no reason at all (`PacketTracking.limited`).
    public enum Reason: String, Sendable {
        case initializing
        case relocalizing
        case excessiveMotion = "excessive_motion"
        case insufficientFeatures = "insufficient_features"
    }

    /// The trajectory's `tracking` column and the photo's `tracking.state`.
    public var state: String {
        switch self {
        case .normal: "normal"
        case .limited: "limited"
        case .notAvailable: "not_available"
        }
    }

    var manifest: PacketManifest.Tracking {
        if case .limited(let reason) = self { return .init(state: state, reason: reason?.rawValue) }
        return .init(state: state, reason: nil)
    }
}

/// One `CMDeviceMotion` sample, in Core Motion's units: gravity and user acceleration in g,
/// rotation rate in rad/s, heading in degrees (Core Motion reports -1 without a north reference).
public struct DeviceMotionSample: Sendable, Equatable {
    /// Device uptime when the sample was taken.
    public var t: Double
    /// `CMAttitude.quaternion` (x, y, z, w).
    public var attitude: SIMD4<Double>
    /// Gravity in g, as Core Motion reports it.
    public var gravity: SIMD3<Double>
    /// User acceleration in g, as Core Motion reports it.
    public var userAcceleration: SIMD3<Double>
    /// Rotation rate in rad/s, as Core Motion reports it.
    public var rotationRate: SIMD3<Double>
    /// Heading in degrees; Core Motion reports -1 without a north reference.
    public var headingDegrees: Double

    /// One sample's fields as Core Motion reported them; `PacketWriter.appendDeviceMotion`
    /// writes them to the device_motion stream.
    public init(
        t: Double, attitude: SIMD4<Double>, gravity: SIMD3<Double>, userAcceleration: SIMD3<Double>,
        rotationRate: SIMD3<Double>, headingDegrees: Double
    ) {
        self.t = t
        self.attitude = attitude
        self.gravity = gravity
        self.userAcceleration = userAcceleration
        self.rotationRate = rotationRate
        self.headingDegrees = headingDegrees
    }
}

/// A stream's CSV text as it is recorded. Every value must be finite and `t` must strictly
/// increase, the validator's rules; a row that breaks them is refused, not written.
struct PacketCSV: Sendable, Equatable {
    let stream: PacketStream
    private(set) var rows = 0
    private(set) var lastT: Double?
    private var text: String

    init(_ stream: PacketStream) {
        self.stream = stream
        text = stream.columns.joined(separator: ",") + "\n"
    }

    var data: Data { Data(text.utf8) }

    /// Appends `t` and `fields` (already formatted, finite) as one row.
    mutating func append(t: Double, _ fields: [String]) throws(PacketError) {
        precondition(fields.count + 1 == stream.columns.count, "\(stream.rawValue): \(fields.count + 1) values for \(stream.columns.count) columns")
        guard t.isFinite, t >= 0 else { throw .invalidTime(where: stream.rawValue, t: t) }
        if let lastT, !(t > lastT) { throw .timeNotIncreasing(stream: stream.rawValue, previous: lastT, t: t) }
        text += PacketNumber.csv(t)
        for field in fields {
            text += ","
            text += field
        }
        text += "\n"
        rows += 1
        lastT = t
    }

    mutating func append(t: Double, values: [Double]) throws(PacketError) {
        guard values.allSatisfy(\.isFinite) else { throw .nonFiniteValue(where: "\(stream.rawValue) at t = \(t)") }
        try append(t: t, values.map(PacketNumber.csv))
    }
}
