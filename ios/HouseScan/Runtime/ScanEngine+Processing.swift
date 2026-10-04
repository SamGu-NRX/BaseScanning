import Foundation
import HouseScanKit
import OSLog

/// The scan's backend, and photo processing (HouseScanKit's `PhotoProcessingController`).
///
/// A scan reads the backend setting once, when it starts (`beginScan`), into its `ScanContext`,
/// and keeps that profile to the end: a change in Developer options applies to the next scan.
/// Start over ends the scan; a world reset keeps the scan and its backend and starts a new spatial
/// session. Sending (`startUpload`) goes to the scan's own backend: a photo-processing scan never
/// reaches the Legacy checker, not even from a resend.
extension ScanEngine {
    /// The homeowner started a scan. Runs once per scan, before anything of it is kept.
    func beginScan() {
        guard scanContext == nil else { return }
        // App Store installs have no developer options, so they ignore a stored choice.
        let backend = DeveloperSettings.shared.isAvailable ? ProcessingBackendSetting.shared.selection : .operationalDefault
        let profile = switch backend {
        case .legacy: legacyProfile
        case .photoProcessing: photoProcessing.profile
        }
        let context = ScanContext(scanID: store.directory.lastPathComponent, profile: profile, recordingSessionID: recorder.sessionID)
        scanContext = context
        state.scanBackend = backend
        RuntimeLog.engine.info("scan \(context.scanID, privacy: .public) uses \(backend.rawValue, privacy: .public) processing")
        guard backend == .photoProcessing else { return }
        attachKeptPhotos()
        photoProcessing.beginScan(context, recording: recordingSource)
        if case .ended(.notSetUp(let reason))? = state.photoProcessing?.stage {
            RuntimeLog.engine.info("photo processing not set up: \(reason, privacy: .public)")
        }
    }

    /// Start over: the scan ends, and whatever photo processing it had ends with it. `recorder`
    /// is already the next scan's.
    func endScan() {
        scanContext = nil
        state.scanBackend = nil
        photoProcessing.endScan(recording: recordingSource)
    }

    /// A world reset: the same scan, backend and consent in a new spatial session. `recorder` has
    /// already restarted for the new world.
    func scanWorldReset() {
        guard let context = scanContext else { return }
        let next = context.inNewWorld(recordingSessionID: recorder.sessionID)
        scanContext = next
        if next.profile.backend == .photoProcessing {
            photoProcessing.worldReset(next, recording: recordingSource)
        }
    }

    /// The photo-processing scan was sent: once the photos still being written are kept, its
    /// packet is frozen, and the screen follows the upload to the answer.
    func startPhotoProcessing() {
        go(.processing)
        let context = scanContext
        Task {
            guard await drainPendingSaves(), scanContext == context, state.phase == .processing else { return }
            let closeUp = state.closeUp == .skipped ? nil : store.stillFrames["meter_close"]?.t
            photoProcessing.captureEnded(acceptedCloseUpAt: closeUp)
        }
    }

    func answerPhotoConsent(_ yes: Bool) {
        guard state.photoProcessing?.consent == .asking else { return }
        RuntimeLog.engine.info("photo processing consent: \(yes ? "yes" : "no", privacy: .public)")
        photoProcessing.answerConsent(yes)
    }

    func stopSendingPhotos() {
        guard state.phase == .processing else { return }
        photoProcessing.stopSending()
        if case .withdrawn(let recorded)? = state.photoProcessing?.consent {
            RuntimeLog.engine.info("photo processing: sending stopped; the phone \(recorded ? "saved" : "could not save", privacy: .public) the withdrawal")
        }
    }

    // MARK: Translation

    private var legacyProfile: ProcessingProfile {
        if let client = resultClient as? HTTPResultClient {
            return ProcessingProfile(backend: .legacy, answers: .service, origin: client.serverURL)
        }
        return ProcessingProfile(backend: .legacy, answers: .standIn, origin: nil)
    }

    /// The current world's recording as the capture packet reads it. A replay records no poses or
    /// motion, and this recorder writes no per-frame intrinsics, so the packet can't pass its
    /// checks and the phone refuses it; nothing is made up to fill the gap.
    var recordingSource: RecordingSource {
        let recorder = recorder
        return RecordingSource(
            start: {
                let snapshot = recorder.flush()
                guard let uptime = snapshot.firstUptime, let date = snapshot.startedAt else { return nil }
                return (uptime, date)
            },
            rows: {
                _ = recorder.flush()
                return RecorderRows(
                    trajectory: recorder.rows(.trajectory), intrinsics: [],
                    accelerometer: recorder.rows(.accelerometer), gyroscope: recorder.rows(.gyroscope))
            })
    }

    private func attachKeptPhotos() {
        let current = store
        current.onKept = { [weak self, weak current] photo, purpose, jpeg in
            guard let self, let current, current === self.store, self.scanContext?.profile.backend == .photoProcessing else { return }
            self.photoProcessing.kept(ScanEngine.keptPhoto(photo, purpose: purpose, jpeg: jpeg, storeDirectory: current.directory))
        }
    }

    private static func keptPhoto(_ photo: StoredKeyframe, purpose: String?, jpeg: URL, storeDirectory: URL) -> KeptPhoto {
        let stored = photo.depth
        return KeptPhoto(
            // The pose as ARKit reported it; anchor corrections belong to scene.json and the 1.1 packet.
            t: photo.t, cameraToWorld: photo.camera.cameraToWorld, cameraIntrinsics: photo.camera.intrinsics,
            cameraImageSize: photo.camera.imageSize, width: photo.width, height: photo.height,
            tracking: TrackingCode(photo.tracking).packetTracking,
            exposure: photo.exposure.map { Packet04.Exposure(duration: $0.durationS, offset: $0.offsetEV, iso: $0.iso, fNumber: $0.fNumber) },
            jpeg: jpeg, purpose: purpose,
            depth: {
                // Only ARKit's own depth with its confidence; a replay's depth isn't ARKit's.
                guard let stored, let depth = KeyframeStore.loadDepth(stored, in: storeDirectory), let confidence = depth.confidence,
                      depth.source == .arkitSceneDepth || depth.source == .arkitSmoothedSceneDepth else { return nil }
                return Packet04Depth(meters: depth.meters, confidence: confidence, width: depth.width, height: depth.height)
            })
    }

    // MARK: Setup

    /// What photo processing can do in this build. Sending a capture off the phone is off in this
    /// slice: the 0.4 clock and privacy contract, the credential and the deployment aren't
    /// settled. The one way to run it is DEBUG's fixture on a replay, which answers on the phone.
    static func photoProcessingSetup(_ options: LaunchOptions) -> PhotoProcessingController.Setup {
        #if DEBUG
        if let answer = options.photoProcessingFixture {
            guard options.replayFolder != nil else { return .notSetUp("the capture fixture runs only on a replay") }
            return .ready(fixtureEnvironment(answer, gate: options.autopilotGate), standIn: true)
        }
        #endif
        return .notSetUp("sending captures is off in this build")
    }

    #if DEBUG
    /// The capture fixture: every request answered in this process (`FixtureCaptureHTTP`), its
    /// routes listed one per line in `<gate>/photo-transport.log` for the UI tests.
    private static func fixtureEnvironment(_ answer: FixtureCaptureHTTP.Answer, gate: URL?) -> CaptureSessionCoordinator.Environment {
        let log = gate?.appending(path: "photo-transport.log")
        let http = FixtureCaptureHTTP(answer: answer) { routes in
            guard let log else { return }
            try? Data(routes.joined(separator: "\n").utf8).write(to: log, options: .atomic)
        }
        var policy = CaptureUploader.Policy()
        // A retry against a fixture costs nothing, and the UI tests shouldn't wait out the
        // service's back-off.
        policy.firstDelay = 0.1
        policy.maxDelay = 0.5
        let device = CaptureAPI.Device(model: "fixture", systemVersion: "fixture", appVersion: "fixture")
        let folder = FileManager.default.temporaryDirectory.appending(path: "PhotoProcessingFixture", directoryHint: .isDirectory)
        return .init(
            endpoint: FixtureCaptureHTTP.base, sends: true, http: http, capturesFolder: folder,
            sessionInfo: { packetID, video in
                Packet04SessionInfo(
                    packetID: packetID, sessionID: packetID, source: .replay, appVersion: device.appVersion, deviceModel: device.model,
                    systemVersion: device.systemVersion, lidarAvailable: false, sceneDepthEnabled: false, meshReconstructionSupported: nil,
                    sceneReconstruction: nil, planeDetection: nil, videoWidth: Int(video.x), videoHeight: Int(video.y), framesPerSecond: nil,
                    timeZone: nil)
            },
            device: device, tier: .arkit, policy: policy,
            log: { line in RuntimeLog.engine.info("\(line, privacy: .public)") })
    }
    #endif
}
