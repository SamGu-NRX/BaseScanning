import CoreMotion
import Foundation
import HouseScanKit
import OSLog
import simd
import Synchronization

/// The packet's sensor streams, written to disk as they arrive: the camera trajectory at every
/// ARFrame (from the AR delegate queue), Core Motion's samples (from `MotionSource`'s queue), and
/// on LiDAR phones depth frames, one file pair each, a few a second (`DepthFrameBudget`).
/// Nothing here runs on the main actor or holds an ARFrame; each sample is copied into a fixed
/// row of Doubles and appended to one raw file per stream, in 64 KB writes.
///
/// Poses stay in ARKit's world frame on disk: the meter anchor, which defines the packet's frame,
/// is set after capture starts, and can move until the packet is written. Packaging reads the
/// rows back (`rows(_:)`) and converts them then, so samples from before the meter was marked are
/// kept too.
///
/// One recorder is one packet session, in one world frame. `restart` starts a new session in the
/// same folder when the world frame is thrown away (a failed relocalization).
final class CaptureRecorder: Sendable {
    /// The raw streams. Each row starts with `t`, seconds of device uptime.
    enum Stream: Int, CaseIterable, Sendable {
        /// t, tracking state code, tracking reason code, then camera-to-world as 16 floats,
        /// column by column (`TrackingCode`).
        case trajectory
        /// t, x, y, z in g.
        case accelerometer
        /// t, x, y, z in rad/s.
        case gyroscope
        /// t, x, y, z in µT, uncalibrated.
        case magnetometer
        /// t, quaternion x y z w, gravity x y z (g), user acceleration x y z (g), rotation rate
        /// x y z (rad/s), heading in degrees (negative: none).
        case deviceMotion
        /// t, pressure in kPa, relative altitude in m.
        case barometer

        var width: Int {
            switch self {
            case .trajectory: 19
            case .accelerometer, .gyroscope, .magnetometer: 4
            case .deviceMotion: 15
            case .barometer: 3
            }
        }

        var fileName: String { "\(self).f64" }
    }

    /// What a session looked like when packaging flushed it.
    struct Snapshot: Sendable {
        var sessionID: String
        /// Wall clock at the first trajectory row, from that row's uptime.
        var startedAt: Date?
        var firstUptime: Double?
        var lastUptime: Double?
    }

    /// A LiDAR depth map recorded between photos (`recordDepthFrame`). Its map and confidence are
    /// on disk; this is what the packet needs to find and place them.
    struct DepthFrame: Sendable {
        var t: Double
        var tracking: TrackingCode
        var cameraToWorld: simd_float4x4
        /// [fx, fy, cx, cy] in pixels of the depth map.
        var intrinsics: SIMD4<Float>
        var width: Int
        var height: Int
        /// Float32 meters and UInt8 confidence, relative to `directory`.
        var map: String
        var confidence: String
    }

    private struct State {
        var sessionID = UUID().uuidString
        var recording = true
        /// Depth frames record only while the camera is meant to be on the wall; the engine turns
        /// them on for the close-up, the walk and a gap request (`setRecording`).
        var recordingDepth = false
        /// Rows older than this are from a world frame `restart` discarded; ARKit can deliver a
        /// few after the reset.
        var since: Double = 0
        var startedAt: Date?
        var firstUptime: Double?
        var lastUptime: Double?
        var buffers: [Data] = Array(repeating: Data(), count: Stream.allCases.count)
        var lastT: [Double] = Array(repeating: -.infinity, count: Stream.allCases.count)
        var failed = false
        var depthBudget = DepthFrameBudget()
        var depthFrames: [DepthFrame] = []
        /// A failed depth-frame write stops depth frames only; the streams go on.
        var depthFailed = false
    }

    let directory: URL
    private let state = Mutex(State())
    /// 64 KB: about 450 trajectory rows (7 s at 60 Hz) or 2000 accelerometer rows per write.
    private static let flushBytes = 1 << 16
    private static let depthFolder = "depth_frames"

    init(directory: URL) {
        self.directory = directory
        Self.truncate(directory)
    }

    /// A new session in a new world frame: the files start empty, and rows timed before now are
    /// dropped.
    func restart() {
        state.withLock { state in
            state = State()
            state.since = ProcessInfo.processInfo.systemUptime
            Self.truncate(directory)
        }
    }

    /// Off while no capture is under way (the result screens, a failed upload): rows and depth
    /// frames are dropped. `depthFrames` narrows depth frames further, to the phases that aim the
    /// camera at the wall.
    func setRecording(_ on: Bool, depthFrames: Bool) {
        state.withLock { state in
            state.recording = on
            state.recordingDepth = on && depthFrames
        }
    }

    func recordPose(t: Double, tracking: TrackingCode, cameraToWorld m: simd_float4x4) {
        let columns = [m.columns.0, m.columns.1, m.columns.2, m.columns.3]
        let pose = columns.flatMap { [Double($0.x), Double($0.y), Double($0.z), Double($0.w)] }
        append(.trajectory, [t, Double(tracking.state), Double(tracking.reason)] + pose)
    }

    /// Appends one row. A row not later than the stream's last one is dropped: every stream's `t`
    /// strictly increases.
    func append(_ stream: Stream, _ row: [Double]) {
        precondition(row.count == stream.width, "\(stream) row has \(row.count) values, expected \(stream.width)")
        let t = row[0]
        state.withLock { state in
            guard state.recording, !state.failed, t >= state.since, t > state.lastT[stream.rawValue] else { return }
            state.lastT[stream.rawValue] = t
            if stream == .trajectory {
                if state.firstUptime == nil {
                    state.firstUptime = t
                    // The same instant on the wall clock: the frame is `now - t` seconds old.
                    state.startedAt = Date(timeIntervalSinceNow: t - ProcessInfo.processInfo.systemUptime)
                }
                state.lastUptime = t
            }
            row.withUnsafeBufferPointer { values in
                for value in values { withUnsafeBytes(of: value.bitPattern.littleEndian) { state.buffers[stream.rawValue].append(contentsOf: $0) } }
            }
            if state.buffers[stream.rawValue].count >= Self.flushBytes {
                write(stream, &state)
            }
        }
    }

    /// Whether a depth frame at `t` would be recorded now: the AR delegate asks before copying the
    /// depth map, so a frame the budget would drop costs nothing.
    func wantsDepthFrame(at t: Double) -> Bool {
        state.withLock { state in
            state.recordingDepth && !state.depthFailed && t >= state.since && state.depthBudget.wants(t: t)
        }
    }

    /// Writes one LiDAR depth map with its confidence to `depth_frames/` as it arrives, so the
    /// maps are never held in memory, and remembers where. At most `DepthFrameBudget.rateHz` a
    /// second and `DepthFrameBudget.maxFrames` in a session; frames past either are dropped.
    /// Called from the AR delegate queue: two 50 to 200 KB writes, twice a second at most.
    func recordDepthFrame(t: Double, tracking: TrackingCode, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, depth: DepthPacket) {
        guard let confidence = depth.confidence, depth.meters.count == depth.width * depth.height, confidence.count == depth.meters.count else { return }
        state.withLock { state in
            guard state.recordingDepth, !state.depthFailed, t >= state.since, state.depthBudget.admit(t: t) else { return }
            let stem = "\(Self.depthFolder)/\(PacketDepthFrame.id(number: state.depthBudget.admitted))"
            let frame = DepthFrame(
                t: t, tracking: tracking, cameraToWorld: cameraToWorld, intrinsics: intrinsics, width: depth.width, height: depth.height,
                map: "\(stem).f32", confidence: "\(stem).conf.u8"
            )
            do {
                try PacketFiles.depthData(meters: depth.meters).write(to: directory.appending(path: frame.map))
                try Data(confidence).write(to: directory.appending(path: frame.confidence))
                state.depthFrames.append(frame)
            } catch {
                state.depthFailed = true
                RuntimeLog.capture.error("depth frames not recorded from here on: \(String(describing: error), privacy: .public)")
                return
            }
            if state.depthBudget.isFull {
                let limit = state.depthBudget.limit
                RuntimeLog.capture.info("depth frames: the limit of \(limit) was reached at t=\(t); no more are recorded")
            }
        }
    }

    /// Every depth frame recorded in this session, in time order.
    func depthFrames() -> [DepthFrame] {
        state.withLock { $0.depthFrames }
    }

    /// A recorded depth frame's map and confidence, read back for the packet: ARKit's scene depth.
    /// Nil when the files are gone or the wrong size. Call off the main actor.
    func loadDepth(_ frame: DepthFrame) -> DepthPacket? {
        let count = frame.width * frame.height
        guard let map = try? Data(contentsOf: directory.appending(path: frame.map)), map.count == count * 4,
              let confidence = try? Data(contentsOf: directory.appending(path: frame.confidence)), confidence.count == count
        else { return nil }
        return DepthPacket(
            meters: PacketFiles.floats(littleEndian: map), width: frame.width, height: frame.height, confidence: [UInt8](confidence),
            source: .arkitSceneDepth
        )
    }

    /// The id of the session recording now, which `restart` makes new for every world.
    var sessionID: String {
        state.withLock { $0.sessionID }
    }

    /// Writes every buffered row and returns the session as it stands.
    func flush() -> Snapshot {
        state.withLock { state in
            for stream in Stream.allCases { write(stream, &state) }
            return Snapshot(sessionID: state.sessionID, startedAt: state.startedAt, firstUptime: state.firstUptime, lastUptime: state.lastUptime)
        }
    }

    /// Every row written for `stream`, after `flush`. Call off the main actor.
    func rows(_ stream: Stream) -> [[Double]] {
        guard let data = try? Data(contentsOf: directory.appending(path: stream.fileName)) else { return [] }
        let values = data.withUnsafeBytes { raw in
            (0..<(raw.count / 8)).map { Double(bitPattern: UInt64(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 8, as: UInt64.self))) }
        }
        return stride(from: 0, to: values.count - stream.width + 1, by: stream.width).map { Array(values[$0..<($0 + stream.width)]) }
    }

    /// A failed write stops the recorder rather than leave a stream with a hole in it; packaging
    /// then writes what reached the disk before the failure.
    private func write(_ stream: Stream, _ state: inout State) {
        let buffer = state.buffers[stream.rawValue]
        guard !buffer.isEmpty else { return }
        state.buffers[stream.rawValue] = Data()
        let url = directory.appending(path: stream.fileName)
        do {
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: buffer)
        } catch {
            state.failed = true
            RuntimeLog.capture.error("stream \(String(describing: stream), privacy: .public) not recorded from here on: \(String(describing: error), privacy: .public)")
        }
    }

    private static func truncate(_ directory: URL) {
        let files = FileManager.default
        try? files.createDirectory(at: directory, withIntermediateDirectories: true)
        for stream in Stream.allCases {
            files.createFile(atPath: directory.appending(path: stream.fileName).path, contents: Data())
        }
        let depth = directory.appending(path: depthFolder, directoryHint: .isDirectory)
        try? files.removeItem(at: depth)
        try? files.createDirectory(at: depth, withIntermediateDirectories: true)
    }
}

/// Tracking state and limited reason as numbers for a raw trajectory row.
struct TrackingCode: Sendable, Equatable {
    /// 0 normal, 1 limited, 2 not available.
    var state: Int
    /// 0 none or unknown, 1 initializing, 2 relocalizing, 3 excessive motion, 4 insufficient
    /// features.
    var reason: Int

    init(_ tracking: TrackingQuality) {
        switch tracking {
        case .normal: (state, reason) = (0, 0)
        case .notAvailable: (state, reason) = (2, 0)
        case .limited(let why):
            state = 1
            reason = switch why {
            case .initializing: 1
            case .relocalizing: 2
            case .excessiveMotion: 3
            case .insufficientFeatures: 4
            case .unknown: 0
            }
        }
    }

    init(state: Int, reason: Int) {
        self.state = state
        self.reason = reason
    }

    var packetTracking: PacketTracking {
        let why: PacketTracking.Reason? = switch reason {
        case 1: .initializing
        case 2: .relocalizing
        case 3: .excessiveMotion
        case 4: .insufficientFeatures
        default: nil
        }
        return switch state {
        case 0: .normal
        case 1: .limited(why)
        default: .notAvailable
        }
    }
}

/// Core Motion for the packet: accelerometer, gyroscope, magnetometer and device motion at
/// `rate`, and the barometer at whatever rate CMAltimeter delivers. Samples go straight to the
/// recorder from a private queue. Location and heading are not recorded (no consent prompt this
/// round), so device motion runs in an arbitrary-yaw frame and its heading is Core Motion's
/// negative "none".
@MainActor
final class MotionSource {
    /// 100 Hz, the packet's nominal rate for the four Core Motion streams. A choice, not
    /// measured: ADVIO's iPhone IMU runs at 100 Hz, and it is well under Core Motion's limit.
    nonisolated static let rate: Double = 100

    private let manager = CMMotionManager()
    private let altimeter = CMAltimeter()
    private let queue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "dev.housescanning.housescan.motion"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .utility
        return queue
    }()
    private(set) var isRunning = false
    private let activity = CMMotionActivityManager()

    /// Whether Motion & Fitness is still to be asked for. Only the barometer needs it; declined,
    /// the barometer records no rows and the other streams run as before.
    static var needsPermission: Bool {
        CMAltimeter.isRelativeAltitudeAvailable() && CMAltimeter.authorizationStatus() == .notDetermined
            && CMMotionActivityManager.isActivityAvailable()
    }

    /// Holds the barometer after an unanswered request, and starts it once the permission is
    /// decided, mid-recording too.
    private var barometerGate = BarometerGate()
    /// Checks the permission once a second while the barometer is held.
    private var permissionWatch: Task<Void, Never>?

    private static var motionUndecided: Bool { CMAltimeter.authorizationStatus() == .notDetermined }

    /// Shows the Motion & Fitness prompt and reports the answer. CMAltimeter has no call that only
    /// asks; an activity query asks for the same permission and calls back once it is answered.
    /// The handler runs on the main queue, so it may be a main-actor closure. Every answer lets
    /// the scan go on; a denial only leaves the barometer without rows.
    @discardableResult
    func requestPermission() async -> CapturePermissions.Motion {
        let answer = await askForPermission()
        barometerGate.answer = answer
        return answer
    }

    /// The answer comes from the authorization status, not the callback's error: a "not
    /// authorized" error while the status is still undecided is no answer.
    private func askForPermission() async -> CapturePermissions.Motion {
        guard Self.needsPermission else { return .notNeeded }
        let now = Date()
        let error = await withCheckedContinuation { (continuation: CheckedContinuation<(any Error)?, Never>) in
            activity.queryActivityStarting(from: now.addingTimeInterval(-60), to: now, to: .main) { _, error in
                continuation.resume(returning: error)
            }
        }
        if let error {
            RuntimeLog.capture.info("Motion & Fitness query error: \(String(describing: error), privacy: .public)")
        }
        switch CMMotionActivityManager.authorizationStatus() {
        case .authorized: return .allowed
        case .denied, .restricted: return .denied
        case .notDetermined: return .unanswered
        @unknown default: return .unanswered
        }
    }

    /// Which streams this phone has; the packet lists only streams with rows.
    var available: Set<CaptureRecorder.Stream> {
        var streams: Set<CaptureRecorder.Stream> = []
        if manager.isAccelerometerAvailable { streams.insert(.accelerometer) }
        if manager.isGyroAvailable { streams.insert(.gyroscope) }
        if manager.isMagnetometerAvailable { streams.insert(.magnetometer) }
        if manager.isDeviceMotionAvailable { streams.insert(.deviceMotion) }
        if CMAltimeter.isRelativeAltitudeAvailable() { streams.insert(.barometer) }
        return streams
    }

    func start(into recorder: CaptureRecorder) {
        guard !isRunning else { return }
        isRunning = true
        let barometer = barometerGate.recordingStarted(undecided: Self.motionUndecided)
        Self.startUpdates(manager, altimeter, queue: queue, recorder: recorder, interval: 1 / Self.rate, barometer: barometer)
        RuntimeLog.capture.info("motion recording on: \(self.available.map { "\($0)" }.sorted().joined(separator: ", "), privacy: .public)\(barometer ? "" : " (barometer held: Motion & Fitness unanswered)", privacy: .public)")
        if barometerGate.held { watchPermission(into: recorder) }
    }

    /// While the barometer is held, starts it in this recording as soon as the permission is
    /// decided (the alert answered late, or a change in Settings).
    private func watchPermission(into recorder: CaptureRecorder) {
        permissionWatch?.cancel()
        permissionWatch = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled, barometerGate.held else { return }
                if barometerGate.permissionChecked(undecided: Self.motionUndecided) {
                    Self.startBarometer(altimeter, queue: queue, recorder: recorder)
                    RuntimeLog.capture.info("Motion & Fitness decided; barometer recording on")
                    return
                }
            }
        }
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        permissionWatch?.cancel()
        permissionWatch = nil
        barometerGate.recordingStopped()
        manager.stopAccelerometerUpdates()
        manager.stopGyroUpdates()
        manager.stopMagnetometerUpdates()
        manager.stopDeviceMotionUpdates()
        altimeter.stopRelativeAltitudeUpdates()
        RuntimeLog.capture.info("motion recording off")
    }

    /// Nonisolated so the handlers are not main-actor closures: Core Motion calls them on `queue`.
    private nonisolated static func startUpdates(_ manager: CMMotionManager, _ altimeter: CMAltimeter, queue: OperationQueue, recorder: CaptureRecorder, interval: Double, barometer: Bool) {
        // Raw, as Core Motion reports it: the packet writer converts g to m/s².
        if manager.isAccelerometerAvailable {
            manager.accelerometerUpdateInterval = interval
            manager.startAccelerometerUpdates(to: queue) { data, _ in
                guard let data else { return }
                let a = data.acceleration
                recorder.append(.accelerometer, [data.timestamp, a.x, a.y, a.z])
            }
        }
        if manager.isGyroAvailable {
            manager.gyroUpdateInterval = interval
            manager.startGyroUpdates(to: queue) { data, _ in
                guard let data else { return }
                let r = data.rotationRate
                recorder.append(.gyroscope, [data.timestamp, r.x, r.y, r.z])
            }
        }
        if manager.isMagnetometerAvailable {
            manager.magnetometerUpdateInterval = interval
            manager.startMagnetometerUpdates(to: queue) { data, _ in
                guard let data else { return }
                let m = data.magneticField
                recorder.append(.magnetometer, [data.timestamp, m.x, m.y, m.z])
            }
        }
        if manager.isDeviceMotionAvailable {
            manager.deviceMotionUpdateInterval = interval
            manager.startDeviceMotionUpdates(using: .xArbitraryCorrectedZVertical, to: queue) { motion, _ in
                guard let motion else { return }
                let q = motion.attitude.quaternion
                let g = motion.gravity
                let a = motion.userAcceleration
                let r = motion.rotationRate
                recorder.append(.deviceMotion, [motion.timestamp, q.x, q.y, q.z, q.w, g.x, g.y, g.z, a.x, a.y, a.z, r.x, r.y, r.z, motion.heading])
            }
        }
        if barometer { startBarometer(altimeter, queue: queue, recorder: recorder) }
    }

    private nonisolated static func startBarometer(_ altimeter: CMAltimeter, queue: OperationQueue, recorder: CaptureRecorder) {
        guard CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeter.startRelativeAltitudeUpdates(to: queue) { data, _ in
            guard let data else { return }
            recorder.append(.barometer, [data.timestamp, data.pressure.doubleValue, data.relativeAltitude.doubleValue])
        }
    }
}
