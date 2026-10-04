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
        // A synthetic capture is sent in place of the scan's photos, never mixed with them.
        if syntheticCapture == nil { attachKeptPhotos() }
        photoProcessing.beginScan(context, recording: photoRecording)
        if case .ended(.notSetUp(let reason))? = state.photoProcessing?.stage {
            RuntimeLog.engine.info("photo processing not set up: \(reason, privacy: .public)")
        }
    }

    /// The scan uses photo processing and this build can't send it.
    var photoScanCantRun: Bool {
        guard let profile = scanContext?.profile, profile.backend == .photoProcessing, case .notSetUp = profile.answers else { return false }
        return true
    }

    /// The homeowner's way out of a photo-processing scan this build can't run, before anything
    /// was captured: Legacy becomes the choice in Developer options, and a new scan starts with
    /// it. The old scan isn't rerouted; it ends.
    func scanWithLegacyInstead() {
        guard state.phase == .processing, photoScanCantRun else { return }
        RuntimeLog.engine.info("photo processing isn't set up: the homeowner chose a Legacy scan instead")
        ProcessingBackendSetting.shared.choose(.legacy)
        startOver()
        finishOnboarding()
    }

    /// Start over: the scan ends, and whatever photo processing it had ends with it. `recorder`
    /// is already the next scan's.
    func endScan() {
        scanContext = nil
        state.scanBackend = nil
        photoProcessing.endScan(recording: photoRecording)
    }

    /// A world reset: the same scan, backend and consent in a new spatial session. `recorder` has
    /// already restarted for the new world.
    func scanWorldReset() {
        guard let context = scanContext else { return }
        let next = context.inNewWorld(recordingSessionID: recorder.sessionID)
        scanContext = next
        if next.profile.backend == .photoProcessing {
            photoProcessing.worldReset(next, recording: photoRecording)
        }
    }

    /// The photo-processing scan was sent: once the photos still being written are kept, its
    /// packet is frozen, and the screen follows the upload to the answer. With DEBUG's synthetic
    /// capture, its photos are kept now instead, and its own close-up is the accepted one.
    func startPhotoProcessing() {
        go(.processing)
        let context = scanContext
        let synthetic = syntheticCapture
        Task {
            let saves = await drainPendingSaves()
            guard saves != .scanChanged, scanContext == context, state.phase == .processing else { return }
            if saves == .stillWriting {
                RuntimeLog.engine.error("photo processing: photos were still being saved after 10 s; the capture isn't sealed")
                photoProcessing.capturePreparationFailed()
                return
            }
            guard let synthetic else {
                let closeUp = state.closeUp == .skipped ? nil : store.stillFrames["meter_close"]?.t
                photoProcessing.captureEnded(acceptedCloseUpAt: closeUp)
                return
            }
            let folder = FileManager.default.temporaryDirectory.appending(path: "PhotoProcessingFixture/synthetic-photos", directoryHint: .isDirectory)
            let photos = (try? await Task.detached(priority: .userInitiated) { try synthetic.photos(in: folder) }.value) ?? []
            guard scanContext == context, state.phase == .processing else { return }
            if photos.isEmpty { RuntimeLog.engine.error("photo processing: the synthetic capture's photos couldn't be written") }
            for photo in photos { photoProcessing.kept(photo) }
            photoProcessing.captureEnded(acceptedCloseUpAt: synthetic.acceptedCloseUpAt)
        }
    }

    func answerPhotoConsent(_ yes: Bool) {
        guard state.photoProcessing?.consent == .asking else { return }
        RuntimeLog.engine.info("photo processing consent: \(yes ? "yes" : "no", privacy: .public)")
        photoProcessing.answerConsent(yes)
    }

    func stopSendingPhotos() {
        guard state.phase == .processing else { return }
        #if DEBUG
        if options.photoProcessingFixtureReadOnlyCapture, photoProcessing.profile.answers == .standIn, let folder = photoProcessing.captureFolder {
            try? FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        }
        #endif
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

    /// What photo processing records from: the synthetic capture's recording when there is one,
    /// otherwise the current world's.
    var photoRecording: RecordingSource {
        syntheticCapture?.recording ?? recordingSource
    }

    /// The current world's recording as the capture packet reads it: live, each ARFrame's pose with
    /// its own intrinsics at the same t, which the packet joins by exact time
    /// (`Packet04Streams.poseRows`). A replay records no poses, intrinsics or motion, so its packet
    /// can't pass its checks and the phone refuses it; nothing is made up to fill the gap.
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
                    trajectory: recorder.rows(.trajectory), intrinsics: recorder.rows(.frameIntrinsics),
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
    /// slice: the 0.4 clock and privacy contract, a scoped credential, the live meter tap, and
    /// the deployment aren't settled; per-frame intrinsics are recorded but unchecked on a phone. The one way to run it is DEBUG's fixture on a
    /// replay, which answers on the phone (`CaptureFixtureLaunch`); a release build can't, since
    /// `debugBuild` is decided at compile time and the launch options aren't even parsed there.
    static func photoProcessingSetup(_ options: LaunchOptions) -> (setup: PhotoProcessingController.Setup, synthetic: SyntheticCapture?) {
        #if DEBUG
        let debugBuild = true
        #else
        let debugBuild = false
        #endif
        let launch = CaptureFixtureLaunch.resolve(
            debugBuild: debugBuild, onReplay: options.replayFolder != nil, answer: options.photoProcessingFixture,
            syntheticCapture: options.photoProcessingSyntheticCapture)
        switch launch {
        case .off(let reason):
            return (.notSetUp(reason), nil)
        case .on(let answer, let synthetic):
            #if DEBUG
            return (.ready(fixtureEnvironment(answer, synthetic: synthetic, gate: options.autopilotGate), standIn: true), synthetic ? SyntheticCapture.standard : nil)
            #else
            return (.notSetUp("a release build has no capture fixture"), nil)
            #endif
        }
    }

    #if DEBUG
    /// The capture fixture: every request answered in this process (`FixtureCaptureHTTP`), its
    /// routes listed one per line in `<gate>/photo-transport.log` for the UI tests.
    private static func fixtureEnvironment(_ answer: FixtureCaptureHTTP.Answer, synthetic: Bool, gate: URL?) -> CaptureSessionCoordinator.Environment {
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
                // The synthetic capture says so in its packet; a replay's photos say replay.
                if synthetic { return SyntheticCapture.sessionInfo(packetID: packetID, video: video, appVersion: device.appVersion) }
                return Packet04SessionInfo(
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
