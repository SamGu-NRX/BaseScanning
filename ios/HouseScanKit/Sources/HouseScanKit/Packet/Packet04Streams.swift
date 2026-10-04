import Foundation
import simd

/// The 0.4 packet's two required streams as CSV, and the tracking changes read from the poses.
public enum Packet04Streams {
    public static let poseColumns = ["t", "epoch", "tracking", "tx", "ty", "tz", "qx", "qy", "qz", "qw", "fx", "fy", "cx", "cy"]
    public static let imuColumns = ["t", "ax", "ay", "az", "gx", "gy", "gz"]
    /// The contract's floors for `rateHz`.
    public static let minimumPoseHz = 10.0
    public static let minimumIMUHz = 50.0

    /// One ARFrame: raw camera-to-world and that frame's intrinsics at the video format's size.
    public struct PoseRow: Sendable, Equatable {
        public var t: Double
        public var tracking: PacketTracking
        public var cameraToWorld: simd_float4x4
        public var intrinsics: SIMD4<Float>

        public init(t: Double, tracking: PacketTracking, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>) {
            self.t = t
            self.tracking = tracking
            self.cameraToWorld = cameraToWorld
            self.intrinsics = intrinsics
        }
    }

    /// One Core Motion sample with its own timestamp: g for the accelerometer, rad/s for the gyro.
    public struct MotionRow: Sendable, Equatable {
        public var t: Double
        public var value: SIMD3<Double>

        public init(t: Double, value: SIMD3<Double>) {
            self.t = t
            self.value = value
        }
    }

    public static func arkitPoses(_ rows: [PoseRow], epoch: String) throws -> (csv: Data, rateHz: Double) {
        guard !rows.isEmpty else { throw Packet04Error.streamEmpty("arkitPoses") }
        var text = poseColumns.joined(separator: ",") + "\n"
        var times: [Double] = []
        for row in rows where row.t > (times.last ?? -.infinity) {
            guard PacketPose.isRigid(row.cameraToWorld) else { continue }
            let p = PacketPose.translation(row.cameraToWorld)
            let q = PacketPose.quaternion(PacketPose.rotation(row.cameraToWorld))
            let k = row.intrinsics
            // The column is free text: a limited state whose reason the contract can't name is
            // still limited, and the pose it came with is still a measurement.
            let cells = [row.t.description, epoch, Packet04Producer.trackingText(row.tracking) ?? "limited"]
                + [p.x, p.y, p.z].map { PacketNumber.double($0).description }
                + [q.x, q.y, q.z, q.w].map(\.description)
                + [k.x, k.y, k.z, k.w].map { PacketNumber.double($0).description }
            text += cells.joined(separator: ",") + "\n"
            times.append(row.t)
        }
        let rate = measuredRate(times)
        guard rate >= minimumPoseHz else { throw Packet04Error.streamTooSlow(stream: "arkitPoses", measuredHz: rate, minimumHz: minimumPoseHz) }
        return (Data(text.utf8), rate)
    }

    /// Accelerometer and gyro rows for `imuRaw`. Core Motion times the two sensors separately, and
    /// the contract's row holds both. Each row is an accelerometer sample paired with the gyro
    /// sample nearest in time, only when that gyro sample is within `maxPairingOffset`; an
    /// accelerometer sample with no gyro sample that close is left out rather than given one.
    /// How the server wants the two clocks joined is an open question to the server team, so the
    /// rule and its measured worst offset travel in the packet's `ext`.
    public static func imuRaw(
        accelerometer: [MotionRow], gyroscope: [MotionRow], maxPairingOffset: Double = 0.005
    ) throws -> (csv: Data, rateHz: Double, pairingNote: [String: String]) {
        let accel = accelerometer.sorted { $0.t < $1.t }
        let gyro = gyroscope.sorted { $0.t < $1.t }
        guard !accel.isEmpty, !gyro.isEmpty else { throw Packet04Error.streamEmpty("imuRaw") }
        var text = imuColumns.joined(separator: ",") + "\n"
        var times: [Double] = []
        var worst = 0.0
        var g = 0
        for a in accel where a.t > (times.last ?? -.infinity) {
            while g + 1 < gyro.count, abs(gyro[g + 1].t - a.t) <= abs(gyro[g].t - a.t) { g += 1 }
            let offset = abs(gyro[g].t - a.t)
            guard offset <= maxPairingOffset else { continue }
            worst = max(worst, offset)
            let cells = [a.t, a.value.x, a.value.y, a.value.z, gyro[g].value.x, gyro[g].value.y, gyro[g].value.z]
            text += cells.map(\.description).joined(separator: ",") + "\n"
            times.append(a.t)
        }
        guard !times.isEmpty else { throw Packet04Error.streamEmpty("imuRaw") }
        let rate = measuredRate(times)
        guard rate >= minimumIMUHz else { throw Packet04Error.streamTooSlow(stream: "imuRaw", measuredHz: rate, minimumHz: minimumIMUHz) }
        let note = [
            "imuRawRowClock": "accelerometer",
            "imuRawGyroPairing": "nearest gyro sample within \(maxPairingOffset) s; accelerometer samples without one are left out",
            "imuRawGyroOffsetMaxS": worst.description,
            "imuRawRowsKept": "\(times.count) of \(accel.count)",
            "imuRawPairingStatus": "proposal from the phone; the server has not agreed how accelerometer and gyro samples join into one row",
        ]
        return (Data(text.utf8), rate, note)
    }

    /// The first state and every change. A limited state with no name in the contract is not
    /// listed; the rows around it still are.
    public static func trackingChanges(_ rows: [PoseRow], epoch: String) -> [Packet04.TrackingEvent] {
        var events: [Packet04.TrackingEvent] = []
        for row in rows {
            guard let state = Packet04Producer.trackingText(row.tracking), state != events.last?.state else { continue }
            events.append(.init(time: row.t, state: state, epoch: epoch))
        }
        return events
    }

    /// One sensor's samples exactly as Core Motion timed them, `t,x,y,z`: the raw evidence behind
    /// `imuRaw`, kept because the pairing rule there is a proposal the server has not agreed to.
    public static func rawMotionCSV(_ rows: [MotionRow], columns: [String]) throws -> Data {
        guard !rows.isEmpty else { throw Packet04Error.streamEmpty(columns.joined(separator: ",")) }
        var text = columns.joined(separator: ",") + "\n"
        for row in rows.sorted(by: { $0.t < $1.t }) {
            text += [row.t, row.value.x, row.value.y, row.value.z].map(\.description).joined(separator: ",") + "\n"
        }
        return Data(text.utf8)
    }

    /// Pose rows from the recorder's raw rows: trajectory rows `[t, tracking state, tracking
    /// reason, 16 camera-to-world values column by column]` and intrinsics rows `[t, fx, fy, cx,
    /// cy, width, height]` written for the same frame at the same t. A pose with no intrinsics row
    /// at its t is left out rather than given another frame's calibration.
    public static func poseRows(trajectory: [[Double]], intrinsics: [[Double]], tracking: (Int, Int) -> PacketTracking) -> [PoseRow] {
        var calibration: [Double: SIMD4<Float>] = [:]
        for row in intrinsics where row.count >= 5 { calibration[row[0]] = SIMD4(Float(row[1]), Float(row[2]), Float(row[3]), Float(row[4])) }
        return trajectory.compactMap { row in
            guard row.count == 19, let k = calibration[row[0]] else { return nil }
            let f = row[3...].map(Float.init)
            let m = simd_float4x4(SIMD4(f[0], f[1], f[2], f[3]), SIMD4(f[4], f[5], f[6], f[7]), SIMD4(f[8], f[9], f[10], f[11]), SIMD4(f[12], f[13], f[14], f[15]))
            return PoseRow(t: row[0], tracking: tracking(Int(row[1]), Int(row[2])), cameraToWorld: m, intrinsics: k)
        }
    }

    /// Motion rows from raw `[t, x, y, z]` rows.
    public static func motionRows(_ rows: [[Double]]) -> [MotionRow] {
        rows.filter { $0.count == 4 }.map { MotionRow(t: $0[0], value: SIMD3($0[1], $0[2], $0[3])) }
    }

    /// 1 / the median interval between rows, the rate actually recorded. 0 with fewer than two.
    public static func measuredRate(_ times: [Double]) -> Double {
        let deltas = zip(times.dropFirst(), times).map { $0 - $1 }.filter { $0 > 0 }.sorted()
        guard !deltas.isEmpty else { return 0 }
        return 1 / deltas[deltas.count / 2]
    }
}
