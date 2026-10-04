import Foundation
import simd

/// A photo the scan kept, as the app stores it: the JPEG on disk and the facts of the ARFrame
/// whose pixels it holds, in that frame's own terms (raw world pose, the camera image's
/// intrinsics and size).
public struct KeptPhoto: Sendable {
    public var t: Double
    public var cameraToWorld: simd_float4x4
    /// [fx, fy, cx, cy] in pixels of the camera image (`cameraImageSize`).
    public var cameraIntrinsics: SIMD4<Float>
    public var cameraImageSize: SIMD2<Float>
    /// Pixels of the stored JPEG.
    public var width: Int
    public var height: Int
    public var tracking: PacketTracking
    public var exposure: Packet04.Exposure?
    public var jpeg: URL
    /// The still's purpose ("meter_close"); nil for a keyframe.
    public var purpose: String?
    /// Reads the photo's ARKit depth. Called once, when the photo is kept, because the app writes a
    /// retaken close-up's depth over the same files.
    public var depth: @Sendable () -> Packet04Depth?

    public init(
        t: Double, cameraToWorld: simd_float4x4, cameraIntrinsics: SIMD4<Float>, cameraImageSize: SIMD2<Float>, width: Int, height: Int,
        tracking: PacketTracking, exposure: Packet04.Exposure?, jpeg: URL, purpose: String?, depth: @escaping @Sendable () -> Packet04Depth? = { nil }
    ) {
        self.t = t
        self.cameraToWorld = cameraToWorld
        self.cameraIntrinsics = cameraIntrinsics
        self.cameraImageSize = cameraImageSize
        self.width = width
        self.height = height
        self.tracking = tracking
        self.exposure = exposure
        self.jpeg = jpeg
        self.purpose = purpose
        self.depth = depth
    }

    /// The observation in the stored JPEG's pixels: intrinsics scaled when the JPEG is not the
    /// camera image's size.
    public var observation: Packet04Observation {
        let scale = SIMD2(Float(width), Float(height)) / cameraImageSize
        let k = cameraIntrinsics
        return Packet04Observation(
            t: t, cameraToWorld: cameraToWorld, intrinsics: SIMD4(k.x * scale.x, k.y * scale.y, k.z * scale.x, k.w * scale.y),
            width: width, height: height, tracking: tracking, exposure: exposure)
    }
}

/// The frame under the homeowner's tap, read when the tap happened.
public struct TapObservation: Sendable {
    public var t: Double
    public var cameraToWorld: simd_float4x4
    /// [fx, fy, cx, cy] in pixels of the camera image, which the JPEG stores unscaled.
    public var intrinsics: SIMD4<Float>
    public var width: Int
    public var height: Int
    public var tracking: PacketTracking
    /// The tap in the sensor image's pixels, (0, 0) its top-left corner.
    public var pixel: SIMD2<Double>
    /// Encodes the frame's image; called off the main actor.
    public var jpeg: @Sendable () -> Data?

    /// The same frame with the tap moved to where `world` (the raycast's hit) lands in its image,
    /// so pixel, ray and hit agree in this one frame even when it is a frame or two newer than
    /// the one the raycast used. Nil when the point is behind the camera or outside the image.
    public func pointing(at world: SIMD3<Float>) -> TapObservation? {
        let p = cameraToWorld.inverse * SIMD4(world, 1)
        guard p.z < -0.05 else { return nil }
        let u = Double(intrinsics.z + intrinsics.x * p.x / -p.z), v = Double(intrinsics.w - intrinsics.y * p.y / -p.z)
        guard (0...Double(width)).contains(u), (0...Double(height)).contains(v) else { return nil }
        var aimed = self
        aimed.pixel = SIMD2(u, v)
        return aimed
    }

    public init(
        t: Double, cameraToWorld: simd_float4x4, intrinsics: SIMD4<Float>, width: Int, height: Int, tracking: PacketTracking,
        pixel: SIMD2<Double>, jpeg: @escaping @Sendable () -> Data?
    ) {
        self.t = t
        self.cameraToWorld = cameraToWorld
        self.intrinsics = intrinsics
        self.width = width
        self.height = height
        self.tracking = tracking
        self.pixel = pixel
        self.jpeg = jpeg
    }
}

/// Where the coordinator reads the world's recorded streams: `CaptureRecorder` in the app.
public struct RecordingSource: Sendable {
    /// Uptime and wall clock of the world's first recorded frame.
    public var start: @Sendable () -> (uptime: Double, date: Date)?
    /// Every row so far; called off the main actor.
    public var rows: @Sendable () -> RecorderRows

    public init(start: @escaping @Sendable () -> (uptime: Double, date: Date)?, rows: @escaping @Sendable () -> RecorderRows) {
        self.start = start
        self.rows = rows
    }
}

/// The recorder's raw rows: trajectory `[t, state, reason, 16 pose values]`, intrinsics
/// `[t, fx, fy, cx, cy, w, h]`, and Core Motion's `[t, x, y, z]`, each sensor at its own times.
public struct RecorderRows: Sendable {
    public var trajectory: [[Double]]
    public var intrinsics: [[Double]]
    public var accelerometer: [[Double]]
    public var gyroscope: [[Double]]

    public init(trajectory: [[Double]], intrinsics: [[Double]], accelerometer: [[Double]], gyroscope: [[Double]]) {
        self.trajectory = trajectory
        self.intrinsics = intrinsics
        self.accelerometer = accelerometer
        self.gyroscope = gyroscope
    }
}

/// What the integration build does with a scan: one 0.4 packet per ARKit world, each kept photo
/// sealed into it on the phone as it is kept, the packet frozen when the scan is sent for
/// placement. Sending is separate: only a build allowed to send device data (`sends`) sends, and
/// only after the homeowner's yes for this scan, asked before it starts. From the yes on, every
/// sealed file goes up, and later photos go up as they are kept. A new scan asks again; a world
/// reset within the scan keeps its answer for the scan's next packet.
///
/// Work for one world runs in order on that world's chain; a world reset or start over ends the
/// session, and nothing queued for it reaches the next one.
@MainActor
public final class CaptureSessionCoordinator {
    public struct Environment: Sendable {
        public var endpoint: URL
        /// Whether device captures may go to `endpoint` at all (`CaptureIntegrationMode.send`).
        public var sends: Bool
        public var http: any CaptureHTTP
        /// Each session writes to `<folder>/<packet id>`.
        public var capturesFolder: URL
        public var sessionInfo: @Sendable (_ packetID: String, _ video: SIMD2<Float>) -> Packet04SessionInfo
        public var device: CaptureAPI.Device
        public var tier: Packet04.Tier
        public var policy: CaptureUploader.Policy
        public var log: @Sendable (String) -> Void

        public init(
            endpoint: URL, sends: Bool, http: any CaptureHTTP, capturesFolder: URL,
            sessionInfo: @escaping @Sendable (_ packetID: String, _ video: SIMD2<Float>) -> Packet04SessionInfo,
            device: CaptureAPI.Device, tier: Packet04.Tier, policy: CaptureUploader.Policy = .init(), log: @escaping @Sendable (String) -> Void = { _ in }
        ) {
            self.endpoint = endpoint
            self.sends = sends
            self.http = http
            self.capturesFolder = capturesFolder
            self.sessionInfo = sessionInfo
            self.device = device
            self.tier = tier
            self.policy = policy
            self.log = log
        }
    }

    /// Checklist purposes the packet's `stills` take; any other still stays a keyframe only.
    public static let stillPurposes: Set<String> = ["meter_close", "meter_oblique"]

    /// The local code a capture ends with when the phone lost a photo or tap the scan accepted:
    /// the packet can't be prepared complete. The upload, if one is running, ends
    /// `.failed(step: "prepare", codes: [inputLostCode], status: 0)`; the session's
    /// `preparationFailure` holds it either way.
    public static let inputLostCode = "capture_input_lost"

    @MainActor
    public final class Session {
        /// This app's id for this session, new for every session even when two packets both call
        /// their ARKit epoch "e1": a result is bound to it, so a late result can't land on a newer scan.
        public let localID = UUID().uuidString
        public let packetID: String
        public let folder: URL
        /// Nil until the homeowner says yes on a build that sends.
        public fileprivate(set) var uploader: CaptureUploader?
        public private(set) var producer: Packet04Producer?
        let recording: RecordingSource
        var chain: Task<Void, Never>?
        var sealing = false
        var ended = false
        /// The homeowner said no after a yes: this capture is never sent again.
        var consentWithdrawn = false
        var withdrawalFailure: ConsentWithdrawalError?
        /// packet.json as frozen, once the scan was sent for placement.
        var frozen: Data?
        /// The server's result for this session's capture, decoded and bound to it.
        public fileprivate(set) var result: CaptureResult.Record?
        /// Set once a photo or tap the scan accepted couldn't be kept (`inputLostCode`). This
        /// session then never seals, and never starts an upload.
        public fileprivate(set) var preparationFailure: String?

        init(packetID: String, folder: URL, recording: RecordingSource) {
            self.packetID = packetID
            self.folder = folder
            self.recording = recording
        }

        /// Runs `work` after everything queued before it, unless the session has ended by then.
        /// `cleanup` runs either way, after `work` when it ran.
        func enqueue(_ work: @escaping @MainActor (Session) async -> Void, cleanup: (@MainActor @Sendable () -> Void)? = nil) {
            let previous = chain
            chain = Task {
                await previous?.value
                defer { cleanup?() }
                guard !self.ended else { return }
                await work(self)
            }
        }

        func producer(first photo: (t: Double, video: SIMD2<Float>), info: (String, SIMD2<Float>) -> Packet04SessionInfo) throws -> Packet04Producer {
            if let producer { return producer }
            // The packet starts at the world's first recorded frame; a photo can't be older.
            let start = recording.start() ?? (photo.t, Date(timeIntervalSinceNow: photo.t - ProcessInfo.processInfo.systemUptime))
            let made = try Packet04Producer(folder: folder, info: info(packetID, photo.video), startedAtUptime: min(start.uptime, photo.t), startedAt: start.date)
            producer = made
            return made
        }
    }

    public let environment: Environment?
    /// The homeowner's answer for this scan: nil before they were asked.
    public private(set) var consent: Bool?
    private var consentedAt: Date?
    public private(set) var session: Session?
    private var recording: RecordingSource?
    /// The current session's status; nil when there is none or nothing is being sent.
    public var onStatus: (@MainActor (CaptureUploadStatus?) -> Void)?
    /// The current session's result, once the server has one. Never called for an ended session.
    public var onResult: (@MainActor (CaptureResult.Record) -> Void)?
    /// The packet's ARKit epoch: one packet is one world.
    public static let epoch = "e1"

    public init(environment: Environment?) {
        self.environment = environment
    }

    /// Whether the homeowner should be asked now: a build that sends, a scan under way, and no
    /// answer for it yet.
    public var needsConsent: Bool {
        environment?.sends == true && consent == nil && session != nil
    }

    public enum ConsentWithdrawalError: Error, Sendable, Equatable {
        /// Sending stopped in this process, but a relaunch may still find the old saved yes.
        case notRecorded(String)
    }

    /// A no after a yes stops this capture's upload for good: nothing kept later is sent, and a
    /// second yes in the same scan does not start it again. What the server already has stays there.
    /// Failure means sending stopped here but the withdrawal could not be saved for a relaunch.
    @discardableResult
    public func answerConsent(_ yes: Bool, at time: Date = Date()) -> Result<Void, ConsentWithdrawalError> {
        consent = yes
        consentedAt = yes ? time : nil
        guard let session else { return .success(()) }
        if let failure = session.withdrawalFailure { return .failure(failure) }
        if yes {
            startUploading(session)
        } else if let uploader = session.uploader {
            session.consentWithdrawn = true
            session.uploader = nil
            // The result distinguishes durable withdrawal from stopping only this live process.
            switch uploader.withdrawConsent() {
            case .marked: break
            case .savedStateRemoved:
                environment?.log("capture upload: the withdrawal marker could not be written; the saved upload was removed instead")
            case .notRecorded(let error):
                session.withdrawalFailure = .notRecorded(error)
                environment?.log("capture upload: the withdrawal could not be saved (\(error)); sending stopped in this process, but a relaunch could still find the saved consent")
            }
            Task { await uploader.abandon(CaptureUploader.withdrawnReason) }
        }
        if let failure = session.withdrawalFailure { return .failure(failure) }
        return .success(())
    }

    // MARK: Scan events

    /// The live source started recording a world.
    public func begin(recording: RecordingSource) {
        self.recording = recording
        if environment != nil, session == nil { startSession() }
    }

    /// The ARKit world was thrown away, or (`newScan`) the scan started over: this packet can't
    /// be finished, and the next one belongs to `recording`'s new world. A new scan asks again.
    public func newWorld(_ reason: String, recording: RecordingSource, newScan: Bool) {
        endSession(reason)
        if newScan {
            consent = nil
            consentedAt = nil
        }
        self.recording = recording
        if environment != nil { startSession() }
    }

    public func kept(_ photo: KeptPhoto) {
        guard let session, !session.sealing, let environment else { return }
        // The app writes a retaken close-up (JPEG and depth) over the same files, so both are taken
        // now, the JPEG as an APFS clone, not when the queued work gets to them.
        let depth = photo.depth()
        let staged = environment.capturesFolder.appending(path: "staging/\(UUID().uuidString).jpg")
        do {
            try FileManager.default.createDirectory(at: staged.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: photo.jpeg, to: staged)
        } catch {
            inputLost(session, "a kept photo could not be copied: \(error)")
            return
        }
        // The copy is deleted even when a reset ends the session before this work runs.
        session.enqueue({ session in
            do {
                let producer = try session.producer(first: (photo.t, photo.cameraImageSize), info: environment.sessionInfo)
                let data = try await Task.detached(priority: .utility) { try Data(contentsOf: staged) }.value
                var files: [SealedFile] = []
                if await producer.keyframeID(at: photo.t) == nil {
                    files += try await producer.sealKeyframe(
                        jpeg: data, observation: photo.observation, reason: photo.purpose == nil ? "auto" : "still", purpose: photo.purpose, depth: depth)
                }
                if let purpose = photo.purpose, Self.stillPurposes.contains(purpose), let keyframe = await producer.keyframeID(at: photo.t) {
                    files.append(try await producer.sealStill(purpose: purpose, keyframe: keyframe))
                }
                await session.uploader?.add(files)
            } catch {
                self.inputLost(session, "a kept photo was not sealed: \(error)")
            }
        }, cleanup: { try? FileManager.default.removeItem(at: staged) })
    }

    /// The homeowner marked the meter: its frame becomes a keyframe and the tap goes on it.
    public func meterTapped(_ tap: TapObservation, hit: Packet04.TapHit?) {
        guard let session, !session.sealing, let environment else { return }
        session.enqueue { session in
            do {
                let producer = try session.producer(first: (tap.t, SIMD2(Float(tap.width), Float(tap.height))), info: environment.sessionInfo)
                guard let jpeg = await Task.detached(priority: .userInitiated, operation: { tap.jpeg() }).value else {
                    self.inputLost(session, "the meter tap's frame could not be encoded")
                    return
                }
                let observation = Packet04Observation(
                    t: tap.t, cameraToWorld: tap.cameraToWorld, intrinsics: tap.intrinsics, width: tap.width, height: tap.height, tracking: tap.tracking)
                let files = try await producer.sealTap(id: "meter", label: "meter", jpeg: jpeg, observation: observation, pixel: tap.pixel, hit: hit)
                await session.uploader?.add(files)
            } catch {
                self.inputLost(session, "the meter tap was not recorded: \(error)")
            }
        }
    }

    /// The scan was sent for placement: freeze the packet with the streams recorded so far.
    /// `acceptedCloseUpAt` is the frame time of the close-up the scan accepted, nil after a skip.
    /// Later sends of the same scan (a retry, one more view) change nothing.
    public func captureEnded(acceptedCloseUpAt: Double?) {
        guard let session, !session.sealing, let environment else { return }
        session.sealing = true
        // A packet missing an accepted photo or tap is never frozen; its upload already ended.
        guard session.preparationFailure == nil else { return }
        session.enqueue { session in
            guard let producer = session.producer else {
                await session.uploader?.abandon("no photos were kept")
                return
            }
            let recording = session.recording
            let rows = await Task.detached(priority: .utility) { recording.rows() }.value
            let poses = Packet04Streams.poseRows(trajectory: rows.trajectory, intrinsics: rows.intrinsics, tracking: PacketTracking.init(recorderState:reason:))
            do {
                let finished = try await producer.finish(
                    poses: poses, accelerometer: Packet04Streams.motionRows(rows.accelerometer),
                    gyroscope: Packet04Streams.motionRows(rows.gyroscope), endedAtUptime: poses.last?.t ?? 0, acceptedCloseUpAt: acceptedCloseUpAt)
                session.frozen = finished.packet
                await session.uploader?.seal(packet: finished.packet, files: await producer.sealedFiles)
            } catch {
                environment.log("capture packet not finished: \(error)")
                await session.uploader?.abandon("packet refused on the phone")
            }
        }
    }

    /// Waits until the current session's queued work and uploads are idle. For tests and the
    /// evidence run.
    public func settle() async {
        guard let session else { return }
        while let chain = session.chain {
            await chain.value
            if session.chain == chain { break }
        }
        await session.uploader?.settled()
    }

    /// After a relaunch: captures frozen before the app quit finish uploading, each only toward the
    /// endpoint it was created on and with the yes it recorded for that endpoint. Returns them.
    public func resumeSealedCaptures() -> [CaptureUploader] {
        guard let environment, environment.sends,
              let folders = try? FileManager.default.contentsOfDirectory(at: environment.capturesFolder, includingPropertiesForKeys: nil)
        else { return [] }
        var resumed: [CaptureUploader] = []
        for folder in folders {
            guard let saved = try? CaptureUploadState.load(from: CaptureUploader.stateURL(in: folder)), saved.end == nil, saved.packet != nil,
                  let uploader = try? CaptureUploader.resume(folder: folder, base: environment.endpoint, http: environment.http, policy: environment.policy)
            else { continue }
            resumed.append(uploader)
            Task { await uploader.kick() }
        }
        return resumed
    }

    // MARK: Sessions

    private func startSession() {
        guard let environment, let recording else { return }
        let packetID = UUID().uuidString
        let session = Session(packetID: packetID, folder: environment.capturesFolder.appending(path: packetID, directoryHint: .isDirectory), recording: recording)
        self.session = session
        environment.log("capture packet: new packet for this world")
        if consent == true { startUploading(session) }
    }

    /// Opens the capture on the server and queues everything sealed so far, then the frozen
    /// packet if the scan was already sent. Photos kept later go up as they are sealed.
    private func startUploading(_ session: Session) {
        guard let environment, environment.sends, consent == true, let consentedAt, session.uploader == nil, !session.ended,
              !session.consentWithdrawn, session.preparationFailure == nil else { return }
        do {
            let uploader = try CaptureUploader.start(
                folder: session.folder, base: environment.endpoint, http: environment.http,
                create: .init(packetId: session.packetID, tier: environment.tier, device: environment.device), consentedAt: consentedAt,
                policy: environment.policy)
            session.uploader = uploader
            // Status and results from an ended session's uploader never reach the screen.
            let publish: @Sendable (CaptureUploadStatus) -> Void = { [weak self, weak session] status in
                Task { @MainActor in
                    guard let self, let session, self.session === session else { return }
                    self.onStatus?(status)
                    if status.resultAvailable, session.result == nil { await self.bindResult(of: session) }
                }
            }
            let log = environment.log
            Task {
                await uploader.observe(publish, log: log)
                await uploader.kick()
            }
            session.enqueue { session in
                guard let producer = session.producer else { return }
                let files = await producer.sealedFiles
                if let frozen = session.frozen {
                    await uploader.seal(packet: frozen, files: files)
                } else {
                    await uploader.add(files)
                }
            }
            environment.log("capture upload: sending this world's packet")
        } catch {
            environment.log("capture upload not started: \(error)")
        }
    }

    /// Decodes the uploader's result and binds it to the session, capture and run that asked for
    /// it. The analysis stays unverified: nothing in the response proves the server reconstructed
    /// anything, so the spatial result is withheld (`CaptureResult.Record.placement`).
    private func bindResult(of session: Session) async {
        guard let uploader = session.uploader else { return }
        let state = await uploader.snapshot
        guard self.session === session, session.result == nil, let body = state.result, let captureID = state.captureID,
              let runID = state.finalized?.runID else { return }
        do {
            let response = try JSONDecoder().decode(CaptureResult.Response.self, from: body)
            let record = CaptureResult.Record(
                response: response,
                association: .init(sessionID: session.localID, captureID: captureID, runID: runID, epoch: Self.epoch))
            session.result = record
            onResult?(record)
        } catch {
            environment?.log("capture result could not be read: \(error)")
        }
    }

    /// A photo or tap the scan accepted couldn't be kept, so `session`'s packet can't be complete.
    /// Admission closes now, before the uploader's own turn, so its loop sends nothing more; the
    /// upload then ends with `inputLostCode`. A session that already ended (a reset world's late
    /// work) is left alone: its upload was abandoned, and the next world isn't this one.
    private func inputLost(_ session: Session, _ detail: String) {
        environment?.log("capture packet: \(detail); this capture can't be prepared")
        guard !session.ended, session.preparationFailure == nil else { return }
        session.preparationFailure = Self.inputLostCode
        guard let uploader = session.uploader else { return }
        uploader.stopSending()
        Task { await uploader.failPreparation(Self.inputLostCode) }
    }

    private func endSession(_ reason: String) {
        guard let session else { return }
        self.session = nil
        session.ended = true
        onStatus?(nil)
        if let uploader = session.uploader {
            uploader.stopSending()
            Task { await uploader.abandon(reason) }
        }
    }
}

extension PacketTracking {
    /// The recorder's codes: state 0 normal, 1 limited, 2 not available; reason 0 none or
    /// unknown, 1 initializing, 2 relocalizing, 3 excessive motion, 4 insufficient features.
    public init(recorderState state: Int, reason: Int) {
        let why: Reason? = switch reason {
        case 1: .initializing
        case 2: .relocalizing
        case 3: .excessiveMotion
        case 4: .insufficientFeatures
        default: nil
        }
        self = switch state {
        case 0: .normal
        case 1: .limited(why)
        default: .notAvailable
        }
    }
}
