import Foundation
import HouseScanKit
import OSLog
import simd
import UIKit

/// The capture packet (packet/README.md on t3/packet, version 1.1): what Share scan hands over.
/// Everything in it is in meters, seconds of device uptime and the meter frame, except
/// scene.json, which travels inside unchanged. Nothing uploads it: photos leave the phone only
/// when the homeowner shares the scan.
extension ScanEngine {
    /// Everything the packet needs from the main actor, read in one turn so it describes one
    /// moment of the scan. The files are written from it off the main actor (`writePacket`).
    struct PacketInputs: Sendable {
        var folder: URL
        var zip: URL
        var storeDirectory: URL
        var recorder: CaptureRecorder
        /// A replay's own frames, which stand in for the live trajectory: nil live.
        var replayTrajectory: [ReplayPose]?
        /// A replay's frames that recorded depth, the source of its depth frames: nil live, where
        /// the recorder holds them.
        var replayDepth: ReplayDepth?
        var producer: PacketManifest.Producer
        var device: PacketManifest.Device
        var meterFrame: MeterFrame
        var groundWorldY: Float
        /// Keyframes and stills; `writePacket` orders them by time.
        var photos: [StoredKeyframe]
        var mesh: LiveCapture.MeshSnapshot?
        var planes: [PacketPlane]
        /// `photoIDs` hold store ids ("meter_close"); `writePacket` swaps in packet ids.
        var marks: [PacketMark]
        var guidance: [PacketGuidanceEntry]
        /// The wall the marks and guidance were placed with, in world meters, captured with them,
        /// so a later write that adds guidance places it in the same frame (`withCurrentGuidance`).
        var sceneWall: SceneWall
        /// The scene.json the upload sent, attached once serialized (`UploadPackaging`).
        var scene: Data
        /// The camera's frame rate live; nil on a replay, whose rate comes from its frames.
        var trajectoryRate: Double?
        var motionStreams: Set<CaptureRecorder.Stream>
    }

    struct ReplayPose: Sendable {
        var t: Double
        var normal: Bool
        var cameraToWorld: simd_float4x4
    }

    struct ReplayDepth: Sendable {
        var folder: URL
        var frames: [ReplayFrame]
    }

    /// Nil before there is a wall: the meter frame is built from it. The scene is attached by the
    /// upload once it is serialized from the same capture (`captureUpload`).
    func packetInputs(mesh: LiveCapture.MeshSnapshot?) -> PacketInputs? {
        guard let map = coverage else { return nil }
        let wall = map.wall
        // The wall in world meters, as scene.json's wall type describes it, for the marks.
        let sceneWall = SceneWall(meter: wall.meter, outward: wall.outward, groundY: wall.groundY, leftCorners: wall.leftCorners, rightCorners: wall.rightCorners)
        guard let frame = MeterFrame(meter: wall.meter, outward: wall.outward) else { return nil }
        let settings = liveCapture?.settings
        let info = Bundle.main.infoDictionary ?? [:]
        let version = [info["CFBundleShortVersionString"], info["CFBundleVersion"]].compactMap { $0 as? String }
        return PacketInputs(
            folder: store.packetFolder,
            zip: store.bundleURL,
            storeDirectory: store.directory,
            recorder: recorder,
            replayTrajectory: replay.map { player in
                player.frames.map { ReplayPose(t: $0.timestamp, normal: $0.trackingNormal, cameraToWorld: $0.cameraToWorld) }
            },
            replayDepth: replay.map { ReplayDepth(folder: $0.folder, frames: $0.frames.filter { $0.depth != nil }) },
            producer: PacketManifest.Producer(
                kind: .app, name: info["CFBundleName"] as? String ?? "HouseScan",
                version: version.count == 2 ? "\(version[0]) (\(version[1]))" : version.first ?? "unknown",
                // The TestFlight job's "Stamp the git commit" step writes it (.github/workflows/testflight.yml).
                commit: (info["HouseScanGitCommit"] as? String).flatMap { $0 == "unknown" ? nil : $0 }
            ),
            device: PacketManifest.Device(
                model: Self.hardwareModel(), iosVersion: UIDevice.current.systemVersion, lidar: LiveCapture.supportsDepth,
                // A replay runs no ARKit session, so none of them ran.
                sceneDepthEnabled: settings?.sceneDepth ?? false, meshEnabled: settings?.mesh ?? false,
                meshClassificationEnabled: settings?.meshClassification ?? false
            ),
            meterFrame: frame,
            groundWorldY: wall.groundY,
            photos: store.keyframes + store.stillFrames.keys.sorted().compactMap { store.stillFrames[$0] },
            mesh: mesh,
            planes: (liveCapture?.planeSnapshot() ?? []).map { plane in
                PacketPlane(
                    id: plane.id, alignment: plane.vertical ? .vertical : .horizontal, classification: plane.classification,
                    anchorToMeter: frame.pose(plane.anchorToWorld), center: plane.center, rotationOnYAxis: plane.rotationOnYAxis,
                    extent: plane.extent, boundaryVertices: plane.boundary
                )
            },
            marks: packetMarks(map, wall: sceneWall, frame: frame),
            guidance: guidanceLog.entries.map { Self.packetEntry($0, wall: sceneWall, frame: frame) },
            sceneWall: sceneWall,
            scene: Data(),
            trajectoryRate: settings.map { Double($0.framesPerSecond) },
            motionStreams: motionRunsLive ? motionAvailable : []
        )
    }

    /// `inputs` with the guidance log as it is now, placed with the wall and meter frame the
    /// inputs were captured with: what the spot check's answer adds to an upload's packet, without
    /// rereading the engine's geometry since.
    func withCurrentGuidance(_ inputs: PacketInputs) -> PacketInputs {
        var inputs = inputs
        inputs.guidance = guidanceLog.entries.map { Self.packetEntry($0, wall: inputs.sceneWall, frame: inputs.meterFrame) }
        return inputs
    }

    /// The marks and guidance log of the last upload's packet, as `writePacket` writes them into
    /// manifest.json (`{"marks": [...], "guidance": [...]}`): `withCurrentGuidance` of the inputs
    /// the upload captured, the same projection `saveBundle` is given. Only the autopilot's UI-test
    /// gate reads it (`Autopilot.writeSceneForTest`); nil before an upload. Debug builds only.
    #if DEBUG
    func packetMarksAndGuidance() -> Data? {
        guard let inputs = spotConfirm.lastPacket.map(withCurrentGuidance) else { return nil }
        struct Projection: Encodable {
            var marks: [PacketMark]
            var guidance: [PacketGuidanceEntry]
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try? encoder.encode(Projection(marks: inputs.marks, guidance: inputs.guidance))
    }
    #endif

    /// `utsname.machine`, such as "iPhone16,1": the hardware, never the phone's name. "arm64" in
    /// the Simulator.
    nonisolated static func hardwareModel() -> String {
        var system = utsname()
        uname(&system)
        return withUnsafeBytes(of: system.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
    }

    // MARK: Marks

    /// The meter, the marked wall ends and every marked feature, in the meter frame.
    /// The meter links the close-up only when the homeowner accepted it (`KeyframeStore.stills`);
    /// a rejected or skipped shot stays among the photos, linked to nothing.
    private func packetMarks(_ map: CoverageMap, wall: SceneWall, frame: MeterFrame) -> [PacketMark] {
        var marks = [PacketMark.meter(
            id: "meter", t: markTimes[MarkKey.meter], photoIDs: store.stills["meter_close"] == nil ? nil : ["meter_close"]
        )]
        for (side, s) in [(WallSide.left, map.leftEnd), (.right, map.rightEnd)] {
            guard let s else { continue }
            marks.append(.wallEnd(
                id: "wall_end_\(side.rawValue)", side: side == .left ? .left : .right,
                endKind: wallEndKinds[side] == .limit ? .limit : .unexplored, s: s, wall: wall, frame: frame,
                // No stamp is never read as the homeowner's mark (B-12).
                stamp: endStamps[side] ?? .inferred
            ))
        }
        for feature in state.features {
            let id = feature.id.uuidString.lowercased()
            let t = markTimes[MarkKey.feature(feature.id)]
            let points = feature.points.map { frame.point($0) }
            switch feature.kind {
            case .door, .window:
                let bottom = feature.bottom ?? 0
                marks.append(.opening(
                    feature.kind == .door ? .door : .window, id: id, span: feature.span, bottom: bottom, top: feature.top ?? bottom,
                    operable: feature.kind == .window ? feature.opens : nil, wall: wall, frame: frame, t: t
                ))
            case .gasMeter, .acUnit:
                guard let point = points.first else { continue }
                marks.append(.pointObject(feature.kind == .gasMeter ? .gasMeter : .ac, id: id, point: point, t: t))
            case .driveway, .fence:
                guard points.count == 2 else {
                    RuntimeLog.engine.error("packet: \(feature.kind.rawValue, privacy: .public) mark has \(points.count) taps, not 2; left out")
                    continue
                }
                marks.append(feature.kind == .fence
                    ? .fence(id: id, from: points[0], to: points[1], t: t)
                    : .driveEdge(id: id, from: points[0], to: points[1], t: t))
            }
        }
        return marks
    }

    /// Keys of `markTimes`.
    enum MarkKey {
        static let meter = "meter"
        static func feature(_ id: UUID) -> String { id.uuidString }
    }

    // MARK: Guidance

    /// A logged request with its span moved from s to meter-frame x.
    private static func packetEntry(_ entry: GuidanceLog.Entry, wall: SceneWall, frame: MeterFrame) -> PacketGuidanceEntry {
        let span = entry.request.span.map { span -> ClosedRange<Float> in
            let low = frame.point(on: wall, s: span.lowerBound, height: 0, out: 0).x
            let high = frame.point(on: wall, s: span.upperBound, height: 0, out: 0).x
            return min(low, high)...max(low, high)
        }
        return PacketGuidanceEntry(
            id: entry.id, kind: entry.request.kind, origin: entry.request.origin, message: entry.request.message,
            band: entry.request.band, span: span, tShown: entry.shown, tResolved: entry.resolved, outcome: entry.outcome
        )
    }

    // MARK: Writing

    /// Writes the packet folder, zips it to `inputs.zip` and removes the folder. Returns the zip
    /// and a one-line summary for the log. Off the main actor: it copies every photo and hashes
    /// every file.
    ///
    /// A photo, a depth map, a trajectory row, a plane or a section the writer refuses is left
    /// out and logged, so one bad input doesn't cost the homeowner the whole packet; the writer's
    /// checks are the validator's, so what is written validates.
    nonisolated static func writePacket(_ inputs: PacketInputs) throws -> (url: URL, summary: String) {
        let frame = inputs.meterFrame
        let trajectory = trajectoryRows(inputs)
        guard let started = trajectory.rows.first?.t, let ended = trajectory.rows.last?.t, ended > started else {
            throw PacketBuildError.noTrajectory
        }

        try? FileManager.default.removeItem(at: inputs.folder)
        var writer = try PacketWriter(folder: inputs.folder, session: PacketSessionInfo(
            id: trajectory.sessionID, producer: inputs.producer, device: inputs.device, startedAt: trajectory.startedAt,
            startedAtUptime: started, meterFrame: frame, groundWorldY: inputs.groundWorldY
        ))

        // Photos in time order, one per source frame, inside the capture.
        var packetID: [String: String] = [:]
        var last: (t: Double, id: String)?
        var added = 0
        // Times of photos written with depth: a depth frame at one of them would repeat it.
        var photoDepthTimes: Set<Double> = []
        for stored in inputs.photos.sorted(by: { $0.t < $1.t }) {
            if let last, last.t == stored.t {
                // The same frame kept twice (a close-up that was also a walk frame, or a view kept
                // again for the overhead answer): one photo.
                packetID[stored.id] = last.id
                continue
            }
            guard (started...ended).contains(stored.t), let sharpness = stored.sharpness else {
                RuntimeLog.engine.error("packet: \(stored.id, privacy: .public) at t=\(stored.t) left out: outside the capture \(started)...\(ended) or no sharpness")
                continue
            }
            let id = PacketPhoto.id(number: added + 1)
            do {
                var depth = stored.depth.flatMap { KeyframeStore.loadDepth($0, in: inputs.storeDirectory) }.flatMap(Self.measured)
                do {
                    try writer.addPhoto(Self.photo(stored, id: id, sharpness: sharpness, depth: depth, inputs: inputs))
                } catch PacketError.invalidDepth(_, let reason) {
                    // The photo is worth more than its depth.
                    RuntimeLog.engine.error("packet: \(stored.id, privacy: .public) depth left out: \(reason, privacy: .public)")
                    depth = nil
                    try writer.addPhoto(Self.photo(stored, id: id, sharpness: sharpness, depth: nil, inputs: inputs))
                }
                packetID[stored.id] = id
                last = (stored.t, id)
                added += 1
                if depth != nil { photoDepthTimes.insert(stored.t) }
            } catch {
                RuntimeLog.engine.error("packet: \(stored.id, privacy: .public) left out: \(String(describing: error), privacy: .public)")
            }
        }

        var skippedRows = 0
        try writer.setNominalRate(trajectory.rate, for: .trajectory)
        for row in trajectory.rows {
            do { try writer.appendTrajectory(t: row.t, tracking: row.tracking, pose: frame.pose(row.cameraToWorld)) } catch { skippedRows += 1 }
        }
        skippedRows += writeMotion(inputs, window: started...ended, into: &writer)
        if skippedRows > 0 { RuntimeLog.engine.error("packet: \(skippedRows) stream rows refused and left out") }

        let depthFrames = writeDepthFrames(inputs, window: started...ended, skipping: photoDepthTimes, into: &writer)

        if let mesh = inputs.mesh {
            do { try writer.setMesh(frame.mesh(mesh.mesh), classification: mesh.classification) } catch { Self.logLeftOut("mesh", error) }
        }
        var planes = 0
        for plane in inputs.planes {
            do {
                try writer.addPlane(plane)
                planes += 1
            } catch let error where plane.boundary != nil {
                // A boundary outside its extent means the two disagree; the extent still holds.
                Self.logLeftOut("plane \(plane.id) boundary", error)
                var bare = plane
                bare.boundary = nil
                do {
                    try writer.addPlane(bare)
                    planes += 1
                } catch {
                    Self.logLeftOut("plane \(plane.id)", error)
                }
            } catch {
                Self.logLeftOut("plane \(plane.id)", error)
            }
        }
        let clamp = { (t: Double) in min(max(t, started), ended) }
        do {
            try writer.setMarks(inputs.marks.map { mark in
                var mark = mark
                let ids = (mark.photoIDs ?? []).compactMap { packetID[$0] }
                mark.photoIDs = ids.isEmpty ? nil : ids
                mark.t = mark.t.map(clamp)
                return mark
            })
        } catch { Self.logLeftOut("marks", error) }
        do {
            try writer.setGuidance(inputs.guidance.map { entry in
                var entry = entry
                entry.tShown = clamp(entry.tShown)
                entry.tResolved = entry.tResolved.map { max(clamp($0), entry.tShown) }
                return entry
            })
        } catch { Self.logLeftOut("guidance", error) }
        try writer.setScene(inputs.scene)
        let folder = try writer.finish()
        try KeyframeStore.zipPacket(folder, to: inputs.zip)
        let summary = "\(added) photos (\(photoDepthTimes.count) with depth), \(depthFrames) depth frames, "
            + "\(trajectory.rows.count) trajectory rows over \(String(format: "%.1f", ended - started)) s, "
            + "\(inputs.motionStreams.count) motion streams, \(inputs.mesh == nil ? "no mesh" : "mesh"), \(planes) planes, "
            + "\(inputs.marks.count) marks, \(inputs.guidance.count) guidance entries"
        return (inputs.zip, summary)
    }

    private struct Trajectory {
        var sessionID: String
        var startedAt: Date?
        /// Nominal rate, Hz.
        var rate: Double
        var rows: [(t: Double, tracking: PacketTracking, cameraToWorld: simd_float4x4)]
    }

    /// The camera path, world frame: every recorded ARFrame live, or one row per replay frame.
    /// Its first and last times are the capture's window.
    private nonisolated static func trajectoryRows(_ inputs: PacketInputs) -> Trajectory {
        if let poses = inputs.replayTrajectory {
            var rows: [(t: Double, tracking: PacketTracking, cameraToWorld: simd_float4x4)] = []
            for pose in poses.sorted(by: { $0.t < $1.t }) where pose.t > (rows.last?.t ?? -.infinity) {
                rows.append((pose.t, pose.normal ? .normal : .limited(nil), pose.cameraToWorld))
            }
            // The recording's own rate. It has no wall-clock start.
            let span = (rows.last?.t ?? 0) - (rows.first?.t ?? 0)
            let rate = rows.count > 1 && span > 0 ? Double(rows.count - 1) / span : 1
            return Trajectory(sessionID: UUID().uuidString, startedAt: nil, rate: rate, rows: rows)
        }
        let snapshot = inputs.recorder.flush()
        let rows = inputs.recorder.rows(.trajectory).map { row in
            let pose = simd_float4x4(
                SIMD4(Float(row[3]), Float(row[4]), Float(row[5]), Float(row[6])),
                SIMD4(Float(row[7]), Float(row[8]), Float(row[9]), Float(row[10])),
                SIMD4(Float(row[11]), Float(row[12]), Float(row[13]), Float(row[14])),
                SIMD4(Float(row[15]), Float(row[16]), Float(row[17]), Float(row[18]))
            )
            return (t: row[0], tracking: TrackingCode(state: Int(row[1]), reason: Int(row[2])).packetTracking, cameraToWorld: pose)
        }
        return Trajectory(sessionID: snapshot.sessionID, startedAt: snapshot.startedAt, rate: inputs.trajectoryRate ?? 60, rows: rows)
    }

    private nonisolated static func photo(_ stored: StoredKeyframe, id: String, sharpness: Double, depth: DepthPacket?, inputs: PacketInputs) -> PacketPhoto {
        // Intrinsics belong to the stored image; a JPEG of another size than the camera reported
        // scales them with it.
        let scale = SIMD2(Float(stored.width), Float(stored.height)) / stored.camera.imageSize
        let k = stored.camera.intrinsics
        return PacketPhoto(
            id: id, jpeg: inputs.storeDirectory.appending(path: "\(stored.id).jpg"), width: stored.width, height: stored.height,
            t: stored.t, pose: inputs.meterFrame.pose(stored.camera.cameraToWorld),
            intrinsics: SIMD4(k.x * scale.x, k.y * scale.y, k.z * scale.x, k.w * scale.y),
            tracking: TrackingCode(stored.tracking).packetTracking,
            exposure: stored.exposure.map { PacketExposure(durationS: $0.durationS, iso: $0.iso, offsetEV: $0.offsetEV) },
            // ARKit's frames come from the back wide camera; a replay says nothing about its lens.
            lens: stored.exposure.map { PacketLens(focalLengthMM: $0.focalLengthMM, fNumber: $0.fNumber, camera: "wide") },
            sharpness: sharpness, depth: depth
        )
    }

    /// Nil for a depth map with under 1% of pixels measured, which the validator calls empty: the
    /// photo goes in without it.
    private nonisolated static func measured(_ depth: DepthPacket) -> DepthPacket? {
        let count = depth.meters.reduce(0) { $0 + ($1.isFinite && $1 > 0 ? 1 : 0) }
        return Double(count) >= 0.01 * Double(depth.meters.count) ? depth : nil
    }

    /// Depth recorded between photos, inside the capture: the live recorder's LiDAR frames, or a
    /// replay's recorded depth under the same `DepthFrameBudget` and the same rule of normal
    /// tracking. A frame at the time of a photo written with depth would repeat it and is left
    /// out. Each map is read, written and dropped in turn, so memory holds one at a time. Returns
    /// how many were written.
    private nonisolated static func writeDepthFrames(
        _ inputs: PacketInputs, window: ClosedRange<Double>, skipping photoDepthTimes: Set<Double>, into writer: inout PacketWriter
    ) -> Int {
        var written = 0
        var refused: [String] = []
        func add(t: Double, tracking: PacketTracking, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, depth: DepthPacket?) {
            guard let depth else {
                refused.append("t=\(t): its files are missing or the wrong size")
                return
            }
            do {
                try writer.addDepthFrame(PacketDepthFrame(
                    id: PacketDepthFrame.id(number: written + 1), t: t, pose: inputs.meterFrame.pose(cameraToWorld), intrinsics: intrinsics,
                    tracking: tracking, depth: depth
                ))
                written += 1
            } catch {
                refused.append(String(describing: error))
            }
        }
        if let replay = inputs.replayDepth {
            var budget = DepthFrameBudget()
            for frame in replay.frames where frame.trackingNormal && window.contains(frame.timestamp) && budget.admit(t: frame.timestamp) {
                guard !photoDepthTimes.contains(frame.timestamp), let file = frame.depth else { continue }
                let intrinsics = DepthImage.intrinsics(
                    scaling: frame.intrinsics, from: SIMD2(Float(frame.width), Float(frame.height)), toWidth: file.width, height: file.height
                )
                add(t: frame.timestamp, tracking: .normal, cameraToWorld: frame.cameraToWorld, intrinsics: intrinsics, depth: replayDepth(file, in: replay.folder))
            }
        } else {
            for frame in inputs.recorder.depthFrames() where window.contains(frame.t) && !photoDepthTimes.contains(frame.t) {
                add(
                    t: frame.t, tracking: frame.tracking.packetTracking, cameraToWorld: frame.cameraToWorld, intrinsics: frame.intrinsics,
                    depth: inputs.recorder.loadDepth(frame)
                )
            }
        }
        if let first = refused.first {
            RuntimeLog.engine.error("packet: \(refused.count) depth frames left out; the first: \(first, privacy: .public)")
        }
        return written
    }

    /// A replay frame's recorded depth as the packet takes it: meters and, when recorded,
    /// confidence, with no source, since the recording says neither which ARKit depth it saved
    /// nor whether it was rendered (the synthetic fixtures' is). Nil when the files are missing
    /// or the wrong size.
    private nonisolated static func replayDepth(_ file: ReplayDepthFile, in folder: URL) -> DepthPacket? {
        let count = file.width * file.height
        guard let map = try? Data(contentsOf: folder.appending(path: file.file)), map.count == count * 4 else { return nil }
        var confidence: [UInt8]?
        if let path = file.confidenceFile {
            guard let data = try? Data(contentsOf: folder.appending(path: path)), data.count == count else { return nil }
            confidence = [UInt8](data)
        }
        return DepthPacket(meters: PacketFiles.floats(littleEndian: map), width: file.width, height: file.height, confidence: confidence, source: nil)
    }

    /// Core Motion's streams, rows inside the capture only. Returns how many rows were refused.
    private nonisolated static func writeMotion(_ inputs: PacketInputs, window: ClosedRange<Double>, into writer: inout PacketWriter) -> Int {
        var refused = 0
        // Motion streams only: the trajectory is written elsewhere, and frame intrinsics have no
        // column in the 1.1 packet (`CaptureRecorder.Stream.motionPacketStream`).
        for stream in CaptureRecorder.Stream.allCases where inputs.motionStreams.contains(stream) {
            guard let packetStream = stream.motionPacketStream else { continue }
            let rows = inputs.recorder.rows(stream).filter { window.contains($0[0]) }
            guard !rows.isEmpty else { continue }
            // CMAltimeter sets its own rate (about 1 Hz), so the barometer claims none.
            if stream != .barometer { try? writer.setNominalRate(MotionSource.rate, for: packetStream) }
            for r in rows {
                do {
                    switch stream {
                    case .accelerometer: try writer.appendAccelerometer(t: r[0], g: SIMD3(r[1], r[2], r[3]))
                    case .gyroscope: try writer.appendGyroscope(t: r[0], radiansPerSecond: SIMD3(r[1], r[2], r[3]))
                    case .magnetometer: try writer.appendMagnetometer(t: r[0], microtesla: SIMD3(r[1], r[2], r[3]))
                    case .deviceMotion:
                        try writer.appendDeviceMotion(DeviceMotionSample(
                            t: r[0], attitude: SIMD4(r[1], r[2], r[3], r[4]), gravity: SIMD3(r[5], r[6], r[7]),
                            userAcceleration: SIMD3(r[8], r[9], r[10]), rotationRate: SIMD3(r[11], r[12], r[13]), headingDegrees: r[14]
                        ))
                    case .barometer: try writer.appendBarometer(t: r[0], pressureKPa: r[1], relativeAltitudeM: r[2])
                    case .trajectory, .frameIntrinsics: break
                    }
                } catch {
                    refused += 1
                }
            }
        }
        return refused
    }

    private nonisolated static func logLeftOut(_ section: String, _ error: any Error) {
        RuntimeLog.engine.error("packet: \(section, privacy: .public) left out: \(String(describing: error), privacy: .public)")
    }

    enum PacketBuildError: Error, CustomStringConvertible {
        case noTrajectory

        var description: String {
            switch self {
            case .noTrajectory: "no trajectory was recorded, so the capture has no time span"
            }
        }
    }
}
