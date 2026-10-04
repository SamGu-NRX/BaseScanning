import ARKit
import AVFoundation
import CoreGraphics
import Foundation
import HouseScanKit
import OSLog
import simd
import SwiftUI

/// The capture engine: the only writer of `ScanViewState`, and the implementation of the
/// homeowner's intents. Frames arrive from `LiveCapture` (ARKit) or `ReplayPlayer` (a recorded
/// session) and take the same path through auto-capture, coverage and guidance.
@MainActor
final class ScanEngine {
    let state = ScanViewState()
    let options: LaunchOptions

    // Sources
    private(set) var replay: ReplayPlayer?
    /// Which kind of plane the meter tap hit; an estimated plane widens the meter's error in the
    /// export. A replay's wall comes from the recording, so it counts as detected.
    var meterPlaneSource: MeterPlaneSource = .detectedPlane
    /// Where the phone was when the meter was marked: the ground choice prefers the plane under
    /// it, and a re-fit follows the tap's line of sight from it (`refitWallToDetectedPlane`). Nil
    /// on a replay.
    var meterTapCamera: SIMD3<Float>?
    private var live: LiveCapture?

    // Capture logic (HouseScanKit)
    private(set) var coverage: CoverageMap?
    private var autoCapture = AutoCapture()
    private var closeUpGate = CloseUpGate()
    private var planner = GuidancePlanner()
    let gapPlanner = GapPlanner()

    // Stored evidence
    private(set) var store: KeyframeStore
    private var keptSourceIDs: Set<String> = []
    /// Debounces "past the end of the wall" during the walk (see `walkCoaching`).
    private var gateProblem: (coaching: Coaching, since: Double)?
    private var gateClearSince: Double?
    /// Debounces the capture gate's own coaching during the walk (see `walkCoaching`).
    private var gateCoaching = CoachingDebouncer()
    private var closeUpPending = false
    /// Why the last close-up has to be retaken (from the meter-number reader, or "None of
    /// these"), and when that was said, in screen seconds. Shown until the next shot fires.
    private var closeUpRetake: (problem: CloseUpProblem, since: Double)?
    /// The reader's answer for the close-up on screen, for the advice after "None of these".
    private var meterReadout: MeterReadout?
    /// The close-up view coverage may take when the close-up step ends (`observeCloseUpView`):
    /// that of the photo on disk, once the reader's image checks passed it.
    var closeUpCredit = CloseUpCredit()
    /// Keyframe writes still in flight, by the `generation` they started in; the bundle waits
    /// for its own generation's. Keyed so a write finishing after a reset can't count against
    /// the new scan (a plain counter went negative when `resetAll` zeroed it mid-write).
    private var pendingSaves: [Int: Int] = [:]
    /// Bumped whenever the world frame or the whole scan is thrown away, so work that finishes
    /// afterwards (a keyframe write, an upload) can tell it belongs to a scan that no longer exists.
    private var generation = 0

    // Wall geometry inputs
    private var meterAnchorID: UUID?
    /// The meter anchor's pose the wall and everything captured agree with, and the corrections
    /// still to apply to them as ARKit refines it (`refreshMeterFromAnchor`). Nil on a replay.
    private var meterTracking: MeterAnchorTracking?
    /// Which frames carried the meter's anchor since it was anchored, for the drift log
    /// (`noteMeterAnchor`). Starts again with `meterTracking`.
    private var meterAnchorPresence = MeterAnchorPresence()
    /// Whether a frame source may start, and what a failure and Start over do to it.
    private var sourceState = CaptureSourceState()
    /// The planes ARKit tracked at the last frame that carried them (`SourceFrame.planes`).
    private var planes = PlaneSnapshot()
    /// Detected horizontal planes, with their classes and outlines.
    private var groundPlanes: [GroundPlaneEvidence] { planes.ground }
    /// Detected vertical planes, with their classes, normals and outlines.
    private var wallPlanes: [WallPlaneEvidence] { planes.walls }
    /// Measures the coverage map's far surface again when what it depends on changes
    /// (`noteFarSurface`).
    private var farSurface = FarSurfaceTracker()
    /// The detected plane the meter's wall was refit to (`refitWallToDetectedPlane`): the wall's
    /// own, never where the space in front of it ends.
    private var refitPlaneID: String?
    private var lastFrame: SourceFrame?
    /// Whether `WallFrame.groundY` comes from a detected plane (or a recording's wall taps) rather
    /// than the chest-height guess. The export widens position errors while it is a guess.
    private(set) var groundMeasured = false
    private var endKinds: [WallSide: EndKind] = [:]
    /// The side whose end turns a corner the walk is to follow, while it waits for the next wall
    /// to be marked (`GuidanceStep.markNextWall`), and why the last mark was refused.
    var nextWallSide: WallSide? {
        didSet { if nextWallSide == nil { pendingNextWall = nil } }
    }
    var nextWallRefusal: NextWallRefusal?
    /// "Can't get there" on the walk's card: when it last ended a side, and which ends it set
    /// before the homeowner walked that side (#82, #76). Reset with the wall.
    var walkRefusals = WalkRefusals()
    /// The wall marked as the next one, waiting for "Is this the next wall?" (#70); cleared with
    /// `nextWallSide`. Published as `ScanViewState.nextWallConfirm`.
    var pendingNextWall: PendingNextWall? {
        didSet {
            let confirm = pendingNextWall.map { NextWallConfirm(side: $0.side, fromEnd: $0.fromEnd) }
            if confirm != state.nextWallConfirm { state.nextWallConfirm = confirm }
        }
    }

    /// Meters up the wall the ring on a proposed corner sits: about chest height, where it shows
    /// in the view of a homeowner aiming at the next wall.
    static let cornerRingHeight: Float = 1

    /// What `confirmNextWall` needs to follow the corner: the marked point and facing, as
    /// `markNextWall` found them.
    struct PendingNextWall {
        var side: WallSide
        var point: SIMD3<Float>
        var outward: SIMD3<Float>
        var source: WallLineSource
        var detectedPlane: Bool
        var s: Float
        var fromEnd: Float
    }

    // Gap loop
    private(set) var gapPlan: GapPlan?
    private var gapCounter = 0
    /// Keyframes stored when the current gap request began: a request is closed only by new views.
    private var keyframesAtGapStart = 0
    /// Tilt-up views kept when the current gap request began: an overhead request is closed by a
    /// new one (`keepOverheadView`); counting keyframes would not do, since the walk keeps them too.
    private var overheadViewsAtGapStart = 0
    /// Requests the homeowner skipped or answered with something overhead: they go to installer
    /// review, and the result doesn't offer them as captures again. With them, the past_end
    /// request from a corner a request couldn't follow (`skipCurrentGap(deferring:)`).
    private(set) var skippedGaps: [GapPlan] = []
    /// The side of a server past_end request being captured: that end was cleared, and marking
    /// it again as where the wall stops settles the request (see `markWallEnd`, `answerWallEnd`).
    var pastEndSide: WallSide?
    /// The end the past_end request cleared: where it was, its kind and its stamp (marked, with
    /// when, or inferred), put back or moved on when the request ends without it marked again
    /// (`settleClearedEnd`).
    private var clearedEnd: (s: Float, kind: EndKind, stamp: WallEndStamp)?
    /// Server requests raised without a tap since the review was confirmed (`automaticGapQueue`),
    /// oldest first. Each is raised once, whether its view was taken or the homeowner couldn't
    /// get there; the result still offers it as a capture.
    private var automaticGaps: [GapPlan] = []
    /// Set when the homeowner taps "Show my result" on a server request (`stopGapRequests`):
    /// after the upload that follows, the result shows instead of the next request.
    private var automaticGapsStopped = false
    /// At most this many server requests are raised without a tap per confirmed review, so a
    /// server that keeps finding new gaps can't hold the homeowner in the loop. A guess, not
    /// measured: an answer usually lists one to three capturable items, and each round is a
    /// capture and an upload.
    static let maxAutomaticGaps = 5
    /// How long the upload screen shows "One more view to finish" before a request raised from
    /// the answer opens the camera: 1.5 s, about the time to read four words and see the new
    /// step appear, and what the UI lane asked for. Not measured with homeowners.
    static let followUpHold: Double = 1.5
    /// The least time the upload screen shows "Check clearances" at work before it ticks: 0.6 s,
    /// enough to see the step start. The server answers in the request that carries the scene,
    /// often a moment after the last byte, and the step was never drawn (issue #31). A guess
    /// like `followUpHold`, not measured with homeowners.
    static let analyzingMinimum: Double = 0.6
    /// How long the upload screen shows every step ticked before the result replaces it: 0.8 s,
    /// about the time to see "Clearances checked" land. A guess like `followUpHold`, not measured.
    static let resultHold: Double = 0.8
    /// When the upload screen moved to "Check clearances" in this upload, for `analyzingMinimum`.
    private var analyzingSince: ContinuousClock.Instant?

    // LiDAR
    /// The bands the see-behind step is about, while `state.guidance` is `.seeBehind`: those with
    /// hidden cells near its s (`hiddenCells(_:band:around:)`).
    private(set) var seeBehindBands: [SurfaceBand] = []

    // Tilt-up step and overhead requests
    /// Set once the tilt-up step is answered or skipped: the walk asks it once per scan.
    var tiltUpSettled = false
    /// The tilted-up view the overhead question is about, with the walked-path segment it was
    /// captured in. "Open sky or nothing overhead" keeps it as a keyframe and in the coverage map
    /// (`keepOverheadView`), which the export sends as the overhead band; "A roof edge, porch or
    /// stairs" keeps nothing, so the server treats that stretch as unseen.
    private var pendingOverhead: (frame: SourceFrame, segment: Int?)?

    // Tracking recovery
    private var relocalizingSince: Double?

    // AR result
    /// Watches whether the AR scene draws the result (`watchResultInCamera`) while "See it on
    /// your wall" is up on the live camera.
    private var resultWatch: Task<Void, Never>?
    /// The wall the model in the AR scene was built from; nil when none is in it.
    private var resultBuiltFor: WallGeometry?

    // Upload
    /// The scan under way: its id, backend profile and world, fixed when it starts
    /// (`beginScan`); nil before a scan starts.
    var scanContext: ScanContext?
    /// Photo processing's capture and answer, for scans that use it (`ScanEngine+Processing`).
    let photoProcessing: PhotoProcessingController
    /// DEBUG's synthetic capture, sent in place of the scan's photos; nil otherwise.
    let syntheticCapture: SyntheticCapture?
    let resultClient: any ResultClient
    private var uploadTask: Task<Void, Never>?
    /// The current upload's scene has fixed its ground; keep this true through answer pacing.
    private var scenePackaged = false
    /// The server's answers House Scan couldn't use since this scan was last sent from the review
    /// or a gap; "Try again" keeps counting, and a usable answer starts again at 0.
    private var unusableAnswers = 0
    private(set) var placement: PlacementResult?
    /// Whether a ground change took the answer down and no answer has been shown since
    /// (`answerAfter(_:)`).
    private var groundFreshness = GroundFreshness()
    /// The latest scan-bundle write (`saveBundle`), and a count of writes started, so only the
    /// latest one offers its bundle.
    private var bundleTask: Task<Void, Never>?
    private var bundleSerial = 0

    // Packet
    /// The packet's sensor streams for the current world frame.
    private(set) var recorder: CaptureRecorder
    private let motion = MotionSource()
    private var askingForPermissions = false

    /// LiDAR phones: the dots over the camera, fused off the main actor from the keyframes
    /// coverage observes. Guidance for the overlay only; nothing reads them back.
    private lazy var liveDots = LiveDotsFeed { [weak self] dots, scan in
        guard let self, scan == self.generation else { return }
        self.state.liveDots = dots
    }
    /// Every request the homeowner was shown, for the packet.
    var guidanceLog = GuidanceLog()
    /// The spot check (`ScanEngine+Confirm.swift`). What follows from its records is read from
    /// them on every change, so recording an answer sets it and a reset clears it:
    /// `ScanViewState.uncheckedAreaElsewhere`, and the UI tests' answer file
    /// (`-sampleResultAfterSpotAnswer`), which applies only once this scan has an answer other
    /// than "It's clear", so a scan after Start over or a new wall starts on the bundled sample.
    var spotConfirm = SpotConfirmState() {
        didSet {
            let elsewhere = spotConfirm.confirmations.leftUnchecked(besides: spotConfirm.shownArea)
            if state.uncheckedAreaElsewhere != elsewhere { state.uncheckedAreaElsewhere = elsewhere }
            if let file = options.sampleResultAfterSpotAnswer, let sample = resultClient as? SampleResultClient {
                sample.answerFile = spotConfirm.confirmations.records.contains { !$0.answer.keepsClaims } ? file : nil
            }
        }
    }
    /// When each mark was made, on the capture clock (`MarkKey`). Wall ends keep theirs in
    /// `endStamps`, with whether the homeowner marked them at all.
    var markTimes: [String: Double] = [:]
    /// Whether each wall end was marked by the homeowner, with when, or inferred from the walk
    /// (`WallEndStamp`, B-12). Written by every `setEnd`, so it never outlives its end's source.
    var endStamps: [WallSide: WallEndStamp] = [:]
    /// The packet's clock for guidance and marks: the latest frame's time, ARFrame.timestamp
    /// live. A replay plays parts of its recording more than once, so its clock is the latest
    /// frame time seen and never runs back. Nil until the first frame.
    private(set) var captureClock: Double?

    private var lastGuidanceLog = ""
    private var lastGateLog = ""
    /// Taps of the feature being marked, in wall coordinates.
    var pendingTaps: [WallPoint] = []

    enum EndKind {
        /// The homeowner marked the end and said something blocks the wall there (a fence, gate
        /// or property line).
        case limit
        /// The wall may go on past this end: it turns a corner, the walk stopped there ("I can't
        /// get there"), or the homeowner has not said what is there yet.
        case unexplored
    }

    init(options: LaunchOptions) {
        self.options = options
        store = KeyframeStore()
        recorder = Self.makeRecorder(store)
        if let url = options.serverURL, !options.sampleResult {
            resultClient = HTTPResultClient(serverURL: url, session: options.answersFromGate ? GateAnswerProtocol.session : .shared)
        } else {
            resultClient = SampleResultClient(pace: options.autopilot ? options.autopilotHold : 1.2)
        }
        let photoSetup = Self.photoProcessingSetup(options)
        photoProcessing = PhotoProcessingController(setup: photoSetup.setup)
        syntheticCapture = photoSetup.synthetic
        ProcessingBackendSetting.shared.photoProcessingAnswers = photoProcessing.profile.answers
        photoProcessing.onChange = { [weak self] status in self?.state.photoProcessing = status }
        photoProcessing.meterAnchor = { [weak self] in self?.meterTracking?.pose }
        state.isAutopilot = options.autopilot
        state.isReplay = options.replayFolder != nil
        state.usesSampleResult = resultClient.isSample
        state.photoCaptureIsSynthetic = syntheticCapture != nil
    }

    // MARK: Start

    func start() {
        RuntimeLog.state.info("STATE=\(self.state.phase.rawValue, privacy: .public)")
        if let folder = options.replayFolder {
            Task { await loadReplay(folder) }
        } else if !ARWorldTrackingConfiguration.isSupported {
            fail(.arUnsupported)
        }
    }

    /// Every failure the homeowner has to act on (no AR, camera denied, a failed session, an
    /// unreadable replay) ends on the failure screen, which reads `state.failure` for its words.
    /// Setting the failure alone left the flow on whatever screen was up.
    private func fail(_ failure: ScanFailure) {
        _ = sourceState.sourceFailed(failure == .arUnsupported ? .unsupported : .recoverable, afterCapture: false)
        state.failure = failure
        go(.unsupported)
    }

    private func loadReplay(_ folder: URL) async {
        do {
            let loaded = try await Task.detached(priority: .userInitiated) { try ReplayPlayer.load(folder: folder) }.value
            let player = ReplayPlayer(folder: folder, loaded: loaded) { [weak self] frame in self?.ingest(frame) }
            replay = player
            sourceState.sourceStarted()
            state.spatialResultAvailable = true
            let withDepth = player.frames.filter { $0.depth != nil }.count
            state.depthAvailable = withDepth > 0
            player.show(index: 0)
            RuntimeLog.engine.info("replay \(player.session.id, privacy: .public): \(player.frames.count) frames, \(withDepth) with depth, wall \(player.wallDescription, privacy: .public)")
        } catch {
            RuntimeLog.engine.error("replay unreadable: \(String(describing: error), privacy: .public)")
            fail(.replayUnreadable(String(describing: error)))
        }
    }

    // MARK: Phases

    func go(_ phase: ScanPhase) {
        guard state.phase != phase else { return }
        if state.phase == .wallWalk || state.phase == .gapRequest {
            breakWalkedPath(because: "the walk paused (\(state.phase.rawValue) -> \(phase.rawValue))")
        }
        if state.phase == .resultAR { hideResultInCamera() }
        // "End the scan here?" belongs to the walk it was asked on.
        state.endScanQuestion = false
        state.endScanTooShort = false
        let previous = state.phase
        state.phase = phase
        if phase == .result, state.result != nil {
            groundFreshness.answerShown()
            injectGroundForTest()
        }
        RuntimeLog.state.info("STATE=\(phase.rawValue, privacy: .public)")
        logMeterAnchorSummary(from: previous, to: phase)
        switch phase {
        case .findMeter:
            state.guidance = .findMeter
            startSourceIfNeeded()
            live?.setMode(.idle)
        case .meterCloseUp:
            state.guidance = .holdOnMeter
            state.closeUp = .aiming(hold: 0, problem: nil)
            closeUpGate = CloseUpGate()
            closeUpPending = false
            closeUpRetake = nil
            meterReadout = nil
            closeUpCredit = CloseUpCredit()
            state.meterNumber = nil
            state.closeUpFailedAttempts = 0
            live?.setMode(.closeUp)
            if let replay { replay.play(range: 0..<replay.frames.count, speed: replaySpeed) }
        case .wallWalk:
            live?.setMode(.walk)
            planner.reset()
            if let replay, let map = coverage {
                // The recording's closing tilt-up frames wait for the tilt-up step.
                let walkEnd = Self.tiltUpFrames(in: replay, map: map).lowerBound
                replay.play(range: 0..<walkEnd, excluding: replay.heldBack?.frames, speed: replaySpeed)
            }
        case .gapRequest:
            live?.setMode(.walk)
            if let replay {
                autoCapture.reset()
                // An overhead request, or one for the wall above what the walk saw, is answered
                // by tilting up, which the tilt-up frames show; other requests by the frames held
                // back from the walk.
                if gapPlan?.asksAboveTheWalk == true, let map = coverage, !Self.tiltUpFrames(in: replay, map: map).isEmpty {
                    replay.play(range: Self.tiltUpFrames(in: replay, map: map), speed: replaySpeed)
                } else {
                    let range = replay.heldBack.map { ReplayPlanning.gapReplayRange($0.frames) } ?? 0..<replay.frames.count
                    replay.play(range: range, speed: replaySpeed)
                }
            }
        case .markFeatures, .uploading, .spotConfirm, .result, .processing:
            live?.setMode(.idle)
            replay?.stop()
        case .resultAR:
            live?.setMode(.idle)
            if let replay, let index = bestFrameForResult() { replay.show(index: index) }
            showResultInCamera(rising: true)
        case .onboarding, .unsupported:
            break
        }
        updateRecording()
        noteGuidance()
    }

    private var replaySpeed: Double { options.autopilot ? 3 : 1 }

    /// How long a finished step (close-up taken, gap closed) stays on screen before the next.
    private var autoAdvanceDelay: Double { options.autopilot ? max(1.2, options.autopilotHold) : 1.2 }

    /// With `-autopilotGate`, waits until the UI test has finished with `phase` (its file exists),
    /// for at most four minutes so a lost gate file can't hang the app. While it waits, the file
    /// `<phase>.held` says the autopilot has finished with the screen and holds it still, for a UI
    /// test that must audit it settled rather than while the autopilot drives it. A test starts
    /// that audit only once `.held` appears, then reads the screen (the walk's map check can wait
    /// up to 90 s), so the wait must outlast all of that; at two minutes a slow runner could have
    /// let the app leave mid-audit.
    func waitForGate(_ phase: ScanPhase) async {
        guard options.autopilot, let gate = options.autopilotGate else { return }
        let file = gate.appending(path: phase.rawValue)
        if !FileManager.default.fileExists(atPath: file.path) {
            try? Data().write(to: gate.appending(path: "\(phase.rawValue).held"))
        }
        let deadline = ContinuousClock.now + .seconds(240)
        // A cancelled upload (`answerAfter`) stops waiting: its sleeps would return at once.
        while ContinuousClock.now < deadline, !Task.isCancelled, !FileManager.default.fileExists(atPath: file.path) {
            try? await Task.sleep(for: .milliseconds(100))
        }
    }

    /// The watch behind `-injectGroundRise`, started at the first upload of a scan.
    private var groundInjection: Task<Void, Never>?

    /// With `-injectGroundRise`, feeds the ground refine a detected floor above the current
    /// ground each time the UI test drops `inject-ground` in the gate folder, through `ingest` as
    /// a frame from ARKit would. It only supplies the evidence: what the answer does about it is
    /// the engine's own path under test.
    private func injectGroundForTest() {
        guard groundInjection == nil, replay != nil, options.autopilot, let rise = options.injectGroundRise,
              let gate = options.autopilotGate else { return }
        let scan = generation
        let trigger = gate.appending(path: "inject-ground")
        groundInjection = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.generation == scan else { return }
                if FileManager.default.fileExists(atPath: trigger.path) {
                    try? FileManager.default.removeItem(at: trigger)
                    guard let wall = self.coverage?.wall, var frame = self.lastFrame else { return }
                    let foot = SIMD2<Float>(wall.meter.x, wall.meter.z)
                    // A snapshot as a live frame carries one, with the injected floor for ground
                    // and the vertical planes as they stand. Pose-only, so nothing else in
                    // `ingest` runs for it. The replay frame it copies carries no planes.
                    frame.planes = PlaneSnapshot(
                        ground: [GroundPlaneEvidence(
                            y: wall.groundY + rise, kind: .floor,
                            boundary: [foot + SIMD2(-1, -1), foot + SIMD2(1, -1), foot + SIMD2(1, 1), foot + SIMD2(-1, 1)],
                            id: "injected-ground")],
                        walls: self.wallPlanes)
                    frame.isPoseOnly = true
                    RuntimeLog.engine.info("test: injecting a floor \(rise) m above the ground on \(self.state.phase.rawValue, privacy: .public)")
                    self.ingest(frame)
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }

    /// Leaves the onboarding for the meter search. Live, the camera and then Motion & Fitness are
    /// asked for first, while the onboarding that says why is still on screen; otherwise the AR
    /// session and the barometer raise both prompts over "Find your electric meter". Without the
    /// camera there is no scan, so motion is not asked for and the camera failure screen shows.
    /// An unanswered motion request goes on too, with the barometer held so it can't raise the
    /// prompt over the meter search (`CapturePermissions`).
    func leaveOnboarding() {
        let camera: CapturePermissions.Camera = switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: .allowed
        case .denied, .restricted: .refused
        case .notDetermined: .undecided
        @unknown default: .undecided
        }
        let exit = CapturePermissions.exit(
            replay: options.replayFolder != nil, camera: camera, motionUndecided: MotionSource.needsPermission)
        switch exit {
        case .findMeter:
            go(.findMeter)
        case .cameraFailure:
            fail(.cameraDenied)
        case .ask(let askCamera, let askMotion):
            guard !askingForPermissions else { return }
            askingForPermissions = true
            Task {
                defer { askingForPermissions = false }
                if askCamera, !(await AVCaptureDevice.requestAccess(for: .video)) {
                    if state.phase == .onboarding { fail(.cameraDenied) }
                    return
                }
                if askMotion, await motion.requestPermission() == .unanswered {
                    RuntimeLog.capture.info("Motion & Fitness unanswered; the barometer waits until it is decided")
                }
                if state.phase == .onboarding { go(.findMeter) }
            }
        }
    }

    /// Camera access may have been turned on in Settings while the failure screen said it was
    /// off: the screen asks again each time the app comes back to the foreground. With access
    /// granted the failed source is let go and the scan goes on without Start over. Before the
    /// meter is marked nothing is lost: the onboarding's permission step runs again (Motion &
    /// Fitness, if it was never asked) and the meter search follows. After it, a new camera
    /// session is a new world frame, so the flow goes back to the meter as after a lost
    /// relocalization (`resetSpatialState`), keeping what describes the house. Still denied,
    /// nothing changes.
    func recheckCameraAccess() {
        guard state.phase == .unsupported, state.failure == .cameraDenied, options.replayFolder == nil else { return }
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { return }
        RuntimeLog.engine.info("camera access granted: resuming the scan")
        releaseFailedSource()
        if coverage != nil {
            resetSpatialState(reason: "camera access granted after a camera failure")
        } else {
            go(.onboarding)
            leaveOnboarding()
        }
    }

    private func startSourceIfNeeded() {
        // A replay is its own source (`loadReplay`); a failed source waits for Start over.
        guard replay == nil, options.replayFolder == nil, live == nil, sourceState.mayStartSource else { return }
        sourceState.sourceStarted()
        state.spatialResultAvailable = true
        let capture = LiveCapture(
            onFrame: { [weak self] frame in self?.ingest(frame) },
            onEvent: { [weak self] event in self?.handle(event) }
        )
        live = capture
        state.feed = .live
        state.depthAvailable = LiveCapture.supportsDepth
        capture.setRecorder(recorder)
        capture.start()
        updateRecording()
    }

    private static func makeRecorder(_ store: KeyframeStore) -> CaptureRecorder {
        CaptureRecorder(directory: store.directory.appending(path: "streams-raw", directoryHint: .isDirectory))
    }

    /// Streams record while a capture is under way, from the meter search through the upload that
    /// sends it. They stop on the result, and when an upload fails or is refused, since the phone
    /// may then sit idle for minutes; a request raised from the result, going back to the review
    /// or sending again starts them again. Depth frames record only where the camera is meant to
    /// be on the wall (the close-up, the walk, a gap request), so the meter search and the review
    /// don't spend `DepthFrameBudget`. Core Motion runs only with the live camera: a replay's
    /// frames were recorded by another phone at another time.
    private func updateRecording() {
        let capturing = switch state.phase {
        case .findMeter, .meterCloseUp, .wallWalk, .markFeatures, .gapRequest: true
        case .uploading:
            switch state.upload {
            case .failed, .rejected, .unusableAnswer: false
            case .idle, .packaging, .uploading, .analyzing, .done: true
            }
        case .onboarding, .spotConfirm, .result, .resultAR, .processing, .unsupported: false
        }
        let depthFrames = switch state.phase {
        case .meterCloseUp, .wallWalk, .gapRequest: true
        case .onboarding, .findMeter, .markFeatures, .uploading, .spotConfirm, .result, .resultAR, .processing, .unsupported: false
        }
        recorder.setRecording(capturing, depthFrames: depthFrames)
        guard live != nil else { return }
        if capturing { motion.start(into: recorder) } else { motion.stop() }
    }

    var motionRunsLive: Bool { live != nil }
    var motionAvailable: Set<CaptureRecorder.Stream> { motion.available }

    // MARK: Frames

    func ingest(_ frame: SourceFrame) {
        captureClock = max(captureClock ?? frame.timestamp, frame.timestamp)
        if !frame.isPoseOnly { lastFrame = frame }
        if let still = frame.still { state.feed = .still(still) }
        state.projection = frame.projection
        if state.tracking != frame.tracking {
            RuntimeLog.capture.info("tracking \(Self.name(self.state.tracking), privacy: .public) -> \(Self.name(frame.tracking), privacy: .public)")
            if state.tracking == .normal { breakWalkedPath(because: "tracking left normal") }
            state.tracking = frame.tracking
            live?.setResultVisible(frame.tracking == .normal)
            // The circle's end checks this tracking (`aimedEnd`), and a pose-only frame returns
            // below before guidance republishes the preview, so the tape would keep an end that
            // "Wall ends here" now refuses (review of #213).
            publishEndPreview()
        }
        noteMeterAnchor(frame)
        // A frame made before the meter was anchored again carries the old anchor's pose. First,
        // so the ground refine compares this frame's planes with a wall already in this frame's
        // world: the other way round, a correction that moved the planes and the anchor together
        // read as a ground change, and then moved the refined ground a second time.
        if frame.meterAnchorID == meterAnchorID { refreshMeterFromAnchor(frame) }
        // Only a frame that carries planes says anything about them. A live snapshot with none
        // left means ARKit stopped tracking them, so a far wall it lost stops ending the space.
        // Before, an empty list was ignored, whether or not the frame carried planes (review of
        // #168).
        let changed = planes.update(from: frame.planes)
        if changed.ground {
            // The correction still too small to apply. It turns only about gravity, which leaves
            // heights alone, so its translation's y is how far every point rises.
            var pendingRise: Float = 0
            if frame.meterAnchorID == meterAnchorID, let tracked = meterTracking?.pose, let anchor = frame.meterAnchor {
                pendingRise = YawCorrection(from: tracked, to: anchor).translation.y
            }
            refineGround(pendingRise: pendingRise)
        }
        if changed.walls {
            refitWallToDetectedPlane()
        }
        guard !frame.isPoseOnly else { return }
        noteFarSurface()
        trackRelocalization(frame)
        guard !frame.isReview else {
            refreshCues(camera: frame.camera)
            return
        }

        switch state.phase {
        case .meterCloseUp:
            closeUp(frame)
        case .wallWalk, .gapRequest:
            walk(frame)
        case .findMeter:
            state.coaching = coaching(for: frame.tracking, skip: nil)
        default:
            break
        }
    }

    /// Re-runs the ground lookup as ARKit adds or grows horizontal planes, so a plane below the
    /// wall always replaces the guess, and a better plane replaces an earlier one. A measured
    /// ground is never raised by more than `GroundPlaneChoice.maximumRaise`.
    ///
    /// `pendingRise` is how far the meter's anchor has risen since the wall last followed it,
    /// below the size `MeterAnchorTracking` applies. The planes have moved with it, so the choice
    /// sees the meter and the current ground raised by it, and the ground is set in the wall's
    /// own frame: a rigid move then reads as no ground change, the correction that follows
    /// doesn't add the rise a second time, and `maximumRaise` and the meter's height above the
    /// plane are judged as they would be once it applied.
    private func refineGround(pendingRise: Float) {
        guard var wall = coverage?.wall else { return }
        let meter = wall.meter + SIMD3(0, pendingRise, 0)
        guard let choice = groundBelow(meter, along: wall.along, current: groundMeasured ? wall.groundY + pendingRise : nil) else { return }
        let y = choice.plane.y - pendingRise
        // 1 cm: far under tap error, and it keeps plane jitter from republishing every frame.
        guard !groundMeasured || abs(y - wall.groundY) > 0.01 else { return }
        RuntimeLog.engine.info("ground at y=\(y) from \(Self.describe(choice), privacy: .public) (was \(wall.groundY), \(self.groundMeasured ? "measured" : "estimated", privacy: .public))")
        wall.groundY = y
        groundMeasured = true
        coverage?.heightError = 0
        // Rebuilds coverage from the kept cameras: the rows now sit at other heights.
        coverage?.updateWall(wall)
        // Before the wall is published, which would redraw the AR result on the new ground.
        answerAfter(.ground)
        publishWall()
        publishCoverage()
        reprojectFeatures()
    }

    /// A ground choice for the log: the plane's id, its class and why it was chosen.
    nonisolated static func describe(_ choice: GroundPlaneChoice.Choice) -> String {
        "plane \(choice.plane.id) (\(choice.plane.kind), \(choice.reason.rawValue))"
    }

    /// Moves the wall to a detected wall plane that disagrees with a meter tap on an estimated
    /// plane (`MeterTap.refit`): the meter goes where the tap's line of sight meets the plane, the
    /// wall faces the plane's normal, and the meter is anchored again there. Only during the
    /// close-up, before the walk has kept any view: coverage can't turn a wall it has already
    /// seen (`CoverageMap.updateWall`), so a wall found wrong later stays wrong.
    private func refitWallToDetectedPlane() {
        guard state.phase == .meterCloseUp, replay == nil, meterPlaneSource == .estimatedPlane,
              let live, let tapCamera = meterTapCamera, let wall = coverage?.wall,
              let refit = MeterTap.refit(meter: wall.meter, outward: wall.outward, tapCamera: tapCamera, planes: wallPlanes) else { return }
        let along = simd_normalize(simd_cross(-refit.outward, SIMD3(0, 1, 0)))
        let choice = groundBelow(refit.meter, along: along, current: nil)
        let measured = choice != nil || groundMeasured
        guard setWall(meter: refit.meter, outward: refit.outward, groundY: choice?.plane.y ?? wall.groundY, groundMeasured: measured) else { return }
        meterPlaneSource = .detectedPlane
        refitPlaneID = refit.planeID
        updateCoverage { $0.setWallLineSource(meterLineSource) }
        meterAnchorID.map { live.removeAnchor($0) }
        // Axes as a wall hit's: y the wall's normal, z up the wall.
        let x = simd_normalize(simd_cross(refit.outward, SIMD3(0, 1, 0)))
        let pose = simd_float4x4(SIMD4(x, 0), SIMD4(refit.outward, 0), SIMD4(simd_cross(x, refit.outward), 0), SIMD4(refit.meter, 1))
        setMeterAnchor(live.addMeterAnchor(at: pose), pose: pose)
        let degrees = refit.turned * 180 / .pi
        let ground = choice.map(Self.describe) ?? "kept"
        RuntimeLog.engine.info("wall re-fitted to detected plane \(refit.planeID, privacy: .public): meter moved \(refit.moved) m, wall turned \(degrees) degrees; ground \(ground, privacy: .public)")
    }

    /// Door and window heights, spans and fence distances follow the wall frame; the tapped world
    /// points stay put. An anchor correction moves the points and the wall together instead
    /// (`refreshMeterFromAnchor`), so it needs no reprojection.
    func reprojectFeatures() {
        guard let wall = coverage?.wall, !state.features.isEmpty else { return }
        var features = state.features
        for index in features.indices { Self.project(&features[index], onto: wall) }
        if features != state.features { state.features = features }
        publishFeaturesPastEnds()
    }

    /// ARKit's correction to the meter's anchor, turn and move alike, applied to everything the
    /// scan captured in the old world: the wall and its corners, the kept cameras and the tapped
    /// marks move with the anchor as one body (`CoverageMap.apply`), so what was seen of the wall
    /// stays as it was and no correction undoes an earlier one. Small ones wait until they add up
    /// (`MeterAnchorTracking`).
    private func refreshMeterFromAnchor(_ frame: SourceFrame) {
        guard let anchor = frame.meterAnchor, coverage != nil, let correction = meterTracking?.update(to: anchor) else { return }
        // How far this correction moves the meter (`WallFrame.apply`).
        let step = coverage.map { simd_distance(correction.point($0.wall.meter), $0.wall.meter) } ?? 0
        coverage?.apply(correction)
        if let wall = coverage?.wall { liveDots.apply(correction, wall: wall, generation: generation) }
        var features = state.features
        for index in features.indices { features[index].points = features[index].points.map(correction.point) }
        if features != state.features { state.features = features }
        answerAfter(.anchorCorrection)
        publishWall()
        publishCoverage()
        logAnchorCorrection(correction, step: step, frame: frame)
    }

    // The drift log (#73). Its lines are `.notice`, which the unified log keeps on the device, so
    // `log collect` after a walk and Console without "Include Info Messages" both show them. On a
    // walk away and back, the lines that are there settle it, never a line being absent:
    // corrections with the marks on their objects mean ARKit corrected its map and the marks
    // followed; frames that had the anchor, no corrections, and marks off their objects mean
    // drift ARKit never corrected, which no anchor can fix; frames that lost the anchor mean
    // nothing could follow it. `t` is the capture clock the packet's manifest times use.

    /// One line per correction applied, with the total since the meter was anchored.
    /// `MeterAnchorTracking` already limits the rate: a correction is applied only once the anchor
    /// has moved 2 cm or turned 0.4 degrees since the last one.
    private func logAnchorCorrection(_ correction: YawCorrection, step: Float, frame: SourceFrame) {
        guard let tracking = meterTracking else { return }
        let total = tracking.sinceAnchored
        let turned = correction.yaw * 180 / .pi
        let totalMoved = simd_length(total.moved)
        let totalTurned = total.yaw * 180 / .pi
        let count = tracking.corrections
        let marks = state.features.count
        let t = frame.timestamp
        let fromMeter = coverage.map { simd_distance(frame.camera.position, $0.wall.meter) } ?? 0
        let phase = state.phase.rawValue
        RuntimeLog.capture.notice(
            "meter anchor corrected at t=\(t, format: .fixed(precision: 2)) s, phone \(fromMeter, format: .fixed(precision: 1)) m from the meter: moved \(step, format: .fixed(precision: 3)) m, turned \(turned, format: .fixed(precision: 2)) degrees; since anchored (\(count) corrections): moved \(totalMoved, format: .fixed(precision: 3)) m (x \(total.moved.x, format: .fixed(precision: 3)), y \(total.moved.y, format: .fixed(precision: 3)), z \(total.moved.z, format: .fixed(precision: 3))), turned \(totalTurned, format: .fixed(precision: 2)) degrees; \(marks) marks moved with it (phase \(phase, privacy: .public))"
        )
    }

    /// One line when the frames stop carrying the meter's anchor, and one when they carry it
    /// again: while it is missing, no correction can reach the wall or the marks. The first frame
    /// after the meter is anchored writes one too. Live only: a replay has no anchor.
    private func noteMeterAnchor(_ frame: SourceFrame) {
        guard let id = meterAnchorID, let tracking = meterTracking else { return }
        let sighting: MeterAnchorPresence.Sighting =
            frame.meterAnchorID != id ? .otherAnchor : frame.meterAnchor == nil ? .missing : .present
        guard let change = meterAnchorPresence.observe(sighting) else { return }
        let t = frame.timestamp
        let fromMeter = coverage.map { simd_distance(frame.camera.position, $0.wall.meter) } ?? 0
        let count = tracking.corrections
        let phase = state.phase.rawValue
        RuntimeLog.capture.notice(
            "meter anchor in frames: \(change.rawValue, privacy: .public) at t=\(t, format: .fixed(precision: 2)) s, phone \(fromMeter, format: .fixed(precision: 1)) m from the meter (\(count) corrections so far, phase \(phase, privacy: .public))"
        )
    }

    /// The tracking's totals on every phase change once the meter is anchored, zero corrections
    /// included, and whether the latest frame carried the anchor. Sending the scan and opening the
    /// AR result always write one, with no anchor too (a replay), so a walk's log ends with it.
    private func logMeterAnchorSummary(from previous: ScanPhase, to phase: ScanPhase) {
        let from = previous.rawValue, to = phase.rawValue
        let t = captureClock.map { String(format: "%.2f", $0) } ?? "none"
        guard let tracking = meterTracking else {
            guard phase == .uploading || phase == .resultAR else { return }
            RuntimeLog.capture.notice(
                "meter anchor at \(from, privacy: .public) -> \(to, privacy: .public), t=\(t, privacy: .public): not tracked (no meter anchor)"
            )
            return
        }
        let total = tracking.sinceAnchored
        let totalMoved = simd_length(total.moved)
        let totalTurned = total.yaw * 180 / .pi
        let count = tracking.corrections
        let frames = meterAnchorPresence
        let last = frames.last?.rawValue ?? "no frame yet"
        let wall = coverage == nil ? "no wall" : "wall set"
        let marks = state.features.count
        RuntimeLog.capture.notice(
            "meter anchor at \(from, privacy: .public) -> \(to, privacy: .public), t=\(t, privacy: .public): \(count) corrections since anchored, moved \(totalMoved, format: .fixed(precision: 3)) m (x \(total.moved.x, format: .fixed(precision: 3)), y \(total.moved.y, format: .fixed(precision: 3)), z \(total.moved.z, format: .fixed(precision: 3))), turned \(totalTurned, format: .fixed(precision: 2)) degrees; latest frame: \(last, privacy: .public); frames with the anchor \(frames.present), without it \(frames.missing), naming another \(frames.otherAnchor); \(wall, privacy: .public), \(marks) marks"
        )
    }

    private func closeUp(_ frame: SourceFrame) {
        guard let wall = coverage?.wall else { return }
        if case .captured = state.closeUp { return }
        if case .skipped = state.closeUp { return }
        state.coaching = coaching(for: frame.tracking, skip: nil)
        // After a retake request the shutter waits long enough for the reason to be read (and,
        // for "move closer", acted on) before the hold can start again.
        if let retake = closeUpRetake, screenTime - retake.since < currentRetakeNotice {
            state.closeUp = .aiming(hold: 0, problem: retake.problem)
            return
        }
        let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: frame.captureTracking, quality: frame.quality)
        let status = closeUpGate.evaluate(sample, meter: wall.meter)
        state.closeUpFailedAttempts = status.failedAttempts
        if let issue = status.issue {
            logGate("close-up held back: \(issue)")
        } else if status.fire || closeUpPending, !frame.jpeg.isAvailable {
            logGate("close-up waiting: no photo on this frame")
        } else if !status.fire, !closeUpPending {
            logGate("close-up holding")
        }
        if status.fire || closeUpPending, status.issue == nil, frame.jpeg.isAvailable {
            logGate("close-up taken from \(frame.id)", always: true)
            closeUpPending = false
            closeUpRetake = nil
            captureCloseUp(frame)
        } else {
            // A hold that finished on a frame without a photo waits for the next photo, but only
            // while the gates keep passing; any problem restarts the hold.
            closeUpPending = status.issue == nil && (closeUpPending || status.fire)
            state.closeUp = .aiming(hold: status.hold, problem: status.issue.map(Self.problem) ?? closeUpRetake?.problem)
        }
    }

    /// Seconds a retake reason stays up before the next close-up can be taken. A guess to try
    /// on a phone, not measured: long enough to read one short line.
    private static let retakeNotice: Double = 2
    /// Under `-failCloseUpSave` the reason stays up 10 s, so the UI test's query can't miss it:
    /// the replay is close to the meter for under a second, and after the 2 s notice the gate's
    /// "Move closer" replaces it.
    private var currentRetakeNotice: Double { options.failCloseUpSave && replay != nil ? 10 : Self.retakeNotice }

    private var screenTime: Double { Double(DispatchTime.now().uptimeNanoseconds) / 1_000_000_000 }

    /// Back to aiming because the close-up photo was not usable (not saved, no number read, or
    /// "None of these"). Counts as a failed attempt, so "Can't get a clear shot" appears from
    /// the second one on.
    func retakeCloseUp(_ problem: CloseUpProblem) {
        guard state.phase == .meterCloseUp else { return }
        RuntimeLog.engine.info("close-up retake: \(String(describing: problem), privacy: .public)")
        closeUpGate.photoRejected()
        state.closeUpFailedAttempts = closeUpGate.failedAttempts
        closeUpPending = false
        closeUpRetake = (problem, screenTime)
        meterReadout = nil
        state.meterNumber = nil
        state.closeUp = .aiming(hold: 0, problem: problem)
    }

    /// The meter number was confirmed: on to the walk once the confirmation has been seen.
    func finishCloseUp() {
        let scan = generation
        Task {
            try? await Task.sleep(for: .seconds(autoAdvanceDelay))
            await waitForGate(.meterCloseUp)
            if scan == generation, state.phase == .meterCloseUp { go(.wallWalk) }
        }
    }

    /// The reader's answer for the close-up on screen, or nil while none is showing.
    var currentMeterReadout: MeterReadout? { meterReadout }

    private func captureCloseUp(_ frame: SourceFrame) {
        state.closeUp = .captured(nil)
        // This shot's save replaces the photo on disk: the last one's view no longer counts.
        closeUpCredit.shotStarted()
        let view = frame.tracking == .normal ? CloseUpView(camera: frame.camera, depth: frame.depth) : nil
        let scan = generation
        let store = store
        Task {
            // The task can start after a reset or after the flow left the close-up.
            guard scan == generation, state.phase == .meterCloseUp else { return }
            var photo = await closeUpPhoto(frame)
            if options.failCloseUpSave, replay != nil { photo.jpeg = .none }
            // Drawing a practice photo suspends: a reset meanwhile must not save into the new scan.
            guard scan == generation, state.phase == .meterCloseUp else { return }
            let saved = await store.saveStill(photo, name: "meter_close.jpg")
            guard scan == generation, state.phase == .meterCloseUp else { return }
            switch saved {
            case .saved:
                break
            case .notWritten:
                retakeCloseUp(.photoNotSaved)
                return
            case .worldDiscarded:
                // The discard sent the flow back to finding the meter, so the phase guard above
                // returns first. Past it, the meter was found again while this file was written:
                // the shot belongs to the old frame, and the new close-up is left to take its own.
                RuntimeLog.engine.info("close-up from a discarded world frame dropped")
                return
            }
            // Read back once, before the capture is acknowledged: the thumbnail and the meter
            // reader both use these bytes, and a photo that can't be read back wasn't kept.
            let file = store.directory.appending(path: "meter_close.jpg")
            let readBack = await Task.detached(priority: .userInitiated) { () -> (jpeg: Data, thumbnail: CGImage?)? in
                guard let jpeg = try? Data(contentsOf: file) else { return nil }
                return (jpeg, ImageWork.uprightThumbnail(jpeg: jpeg))
            }.value
            guard scan == generation, state.phase == .meterCloseUp else { return }
            guard let readBack else {
                RuntimeLog.engine.error("close-up photo could not be read back")
                closeUpCredit.photoChecked(view, passed: false)
                retakeCloseUp(.photoNotSaved)
                return
            }
            state.closeUp = .captured(readBack.thumbnail)
            state.captureCount += 1
            state.lastCapture = CaptureEvent(id: state.captureCount, kind: .closeUp, thumbnail: readBack.thumbnail)
            await readMeterNumber(readBack.jpeg, scan: scan, view: view)
        }
    }

    /// Reads the meter number from the saved close-up's bytes, off the main actor, then offers
    /// the candidates for the homeowner to pick from (never filling one in) or asks for a retake.
    /// `view` is the shot's, which coverage may take once the reader has passed its photo.
    private func readMeterNumber(_ jpeg: Data, scan: Int, view: CloseUpView?) async {
        state.meterNumber = .reading
        let reader = MeterNumberReaders.make()
        let readout = await Task.detached(priority: .userInitiated) { await reader.read(jpeg: jpeg) }.value
        guard scan == generation, state.phase == .meterCloseUp, state.meterNumber == .reading else { return }
        closeUpCredit.photoChecked(view, passed: readout.photoPassedChecks)
        guard !readout.candidates.isEmpty else {
            retakeCloseUp(readout.retake ?? .noNumber)
            return
        }
        RuntimeLog.engine.info("meter number: \(readout.candidates.count) candidates to choose from")
        meterReadout = readout
        state.meterBrand = readout.brand
        state.meterNumber = .choose(readout.candidates)
    }

    private func walk(_ frame: SourceFrame) {
        guard let map = coverage else { return }
        let sample = FrameSample(timestamp: frame.timestamp, camera: frame.camera, tracking: frame.captureTracking, quality: frame.quality)
        let decision = autoCapture.evaluate(sample, newlySeenCells: map.newlySeenCount(from: frame.camera))
        // Past an end it can't see back from, a photo adds nothing: it is refused and the screen says so.
        // Not past the end whose next wall is being looked for: that is where the walk asked the
        // homeowner to go (#70).
        let pastEnd = map.unexploredEndPassed(by: frame.camera, ignoring: nextWallSide?.walk)
        var skip: CaptureDecision.SkipReason?
        switch decision {
        case .skip(let reason):
            skip = reason
            logGate("skipped: \(reason)")
        case .keep where pastEnd != nil:
            logGate("refused: past the \(pastEnd?.rawValue ?? "") end, nothing between the ends in view")
        // The gate judges sharpness and exposure from this frame's own quality when it has one,
        // else from the last measured frame's. A kept photo must have been judged itself.
        case .keep where frame.quality == nil:
            logGate("refused: no quality measured on this frame")
        case .keep where !frame.jpeg.isAvailable:
            logGate("refused: no photo on this frame")
        case .keep where keptSourceIDs.contains(frame.id):
            logGate("refused: frame already kept")
        case .keep(let reason):
            logGate("kept \(frame.id) (\(reason))", always: true)
            autoCapture.didKeep(sample)
            keptSourceIDs.insert(frame.id)
            keep(frame)
        }
        state.coaching = walkCoaching(tracking: frame.tracking, skip: skip, meanLuma: frame.quality?.meanLuma, pastEnd: pastEnd != nil, time: frame.timestamp)
        afterCoverageChange(camera: frame.camera, time: frame.timestamp)
        askOverheadIfTiltedUp(frame)
    }

    /// Tracking problems show at once. The capture gate's problems go through `gateCoaching`
    /// (`CoachingDebouncer`): each has its own clock, darkness is judged from the frames' luma
    /// with hysteresis, and "Slow down" is kept for walking, so while the homeowner stands and
    /// aims (`isAiming`) only turning and blur are said. Blur reads as "Slow down" while walking
    /// and "Hold steady" while aiming. Standing past an end the phone can't see back from
    /// (`pastEnd`) shows once it has lasted `showAfter` seconds, clears after `clearAfter`
    /// seconds without it, and comes before the gate's problems: no photo is kept there whatever
    /// the gate says. Both durations are guesses to try on a phone, not measured.
    private func walkCoaching(tracking: TrackingQuality, skip: CaptureDecision.SkipReason?, meanLuma: Double?, pastEnd: Bool, time: Double) -> Coaching? {
        let showAfter = 0.7
        let clearAfter = 0.5
        let aiming = isAiming
        let gate = gateCoaching.update(time: time, skip: skip, meanLuma: meanLuma, aiming: aiming)
        guard tracking == .normal else {
            gateProblem = nil
            gateClearSince = nil
            // The light is what it was, but the motion from before the phone lost its place is not.
            gateCoaching.forgetMotion()
            return coaching(for: tracking, skip: nil)
        }
        let candidate: Coaching? = pastEnd ? .pastWallEnd : nil
        if let problem = gateProblem, time < problem.since { gateProblem = nil }  // replay restarted
        if let candidate {
            gateClearSince = nil
            if gateProblem?.coaching != candidate {
                gateProblem = (candidate, time)
            }
        } else if let problem = gateProblem {
            let clearSince = gateClearSince ?? time
            gateClearSince = clearSince
            if time - clearSince >= clearAfter || time - problem.since < showAfter {
                gateProblem = nil
                gateClearSince = nil
            }
        }
        if let problem = gateProblem, time - problem.since >= showAfter { return problem.coaching }
        return gate.map { Self.coaching(for: $0, aiming: aiming) }
    }

    /// Whether the homeowner is asked to stand and aim rather than walk: an aim, tilt, step-back,
    /// see-behind or mark-the-end step, marking, a question on screen, or a gap request's view.
    /// Walking too fast is not coached then (#26). Two steps ask for walking and so still say
    /// "Slow down": the corner ("walk round it, aim at the next wall and mark it"; the mark
    /// itself is `state.marking`, which aims) and a request to walk the stretch far enough out
    /// (`GapPlan.Need.asksToWalk`). Read before this frame's guidance update, so it is the step
    /// on screen when the frame arrived.
    private var isAiming: Bool {
        if state.marking != nil || state.endQuestion != nil || state.overheadQuestion { return true }
        switch state.guidance {
        case .aimAtGround, .aimAtWall, .tiltUp, .stepBack, .seeBehind, .markEnd: return true
        case .gap: return !(gapPlan?.need.asksToWalk ?? false)
        case .findMeter, .aimAtWallForMeter, .holdOnMeter, .walk, .walkComplete, .markNextWall: return false
        }
    }

    /// The coaching for a gate problem. Blur reads as "Slow down" while walking and "Hold
    /// steady" while aiming, where "Slow down" would tell someone standing still to walk slower.
    static func coaching(for problem: CoachingDebouncer.GateProblem, aiming: Bool) -> Coaching {
        switch problem {
        case .tooDark: .tooDark
        case .persistentlyDark: .tooDarkToMeasure
        case .movingFast: .slowDown
        case .turningFast: .turnSlowly
        case .blurry: aiming ? .holdSteady : .slowDown
        }
    }

    private func afterCoverageChange(camera: CameraFrame?, time: Double) {
        publishCoverage()
        if state.phase == .wallWalk, let camera {
            updateGuidance(camera: camera, time: time)
        } else if state.phase == .gapRequest {
            updateGap(camera: camera)
        }
    }

    /// Stores a kept frame; coverage and the capture count move only once its photo is on disk,
    /// so the strip never claims a view the bundle lacks. The photo, pose and tracking all come
    /// from the one `SourceFrame`, so what is credited is the pose of the stored image. With
    /// `overhead`, the stored view is also kept as a clear overhead view. `segment` is the
    /// walked-path segment the frame was captured in, when it was captured before now.
    private func keep(_ frame: SourceFrame, overhead: Bool = false, capturedIn segment: Int? = nil) {
        let kind: CaptureEvent.Kind = state.phase == .gapRequest ? .gap : .walk
        let index = store.nextKeyframeIndex()
        let scan = generation
        let store = store
        // The walked-path segment the frame was captured in: a break while its photo stores must
        // not join it to frames captured after the break.
        let segment = segment ?? coverage?.pathSegment
        pendingSaves[scan, default: 0] += 1
        Task {
            // Every exit drains this generation's count, so the upload never waits on a write
            // that was refused or failed.
            defer {
                let left = (pendingSaves[scan] ?? 1) - 1
                pendingSaves[scan] = left > 0 ? left : nil
            }
            // A frame queued before a reset must not be written into the new scan's store.
            guard scan == generation else { return }
            let saved = await store.saveKeyframe(frame, index: index)
            guard scan == generation else { return }
            guard saved.stored else {
                // Not stored, so not kept: a later pass over the same replay frame may keep it.
                keptSourceIDs.remove(frame.id)
                if overhead { RuntimeLog.engine.error("overhead: the view asked about was not stored; nothing recorded") }
                return
            }
            // Coverage only moves on kept frames with normal tracking (checklist R3).
            // The frame's own time lets the walked path join only poses kept close together in time.
            // With LiDAR depth, a cell counts only where depth confirms the camera saw it.
            let delta = coverage?.observe(frame.camera, trackingNormal: frame.tracking == .normal, time: frame.timestamp, depth: frame.depth, segment: segment)
            if let depth = frame.depth, frame.tracking == .normal, let wall = coverage?.wall {
                liveDots.integrate(camera: frame.camera, depth: depth, wall: wall, generation: scan)
            }
            RuntimeLog.capture.info("stored \(frame.id, privacy: .public) as keyframe \(index)\(frame.depth == nil ? "" : " with depth", privacy: .public): \(delta?.newlySeen ?? 0) cells newly seen, \(delta?.newlyCovered ?? 0) newly covered, \(delta?.newlyHidden ?? 0) newly hidden")
            if overhead { recordOverhead(frame) }
            state.captureCount += 1
            state.lastCapture = CaptureEvent(id: state.captureCount, kind: kind, thumbnail: saved.thumbnail)
            afterCoverageChange(camera: lastFrame?.camera, time: lastFrame?.timestamp ?? frame.timestamp)
        }
    }

    // MARK: Guidance

    /// The planner's dwell runs on the screen's clock, not on `time` (the frame's timestamp).
    /// "Don't change the instruction for 3 s" is about what the person looking at the screen
    /// reads. Live, the two clocks agree; a replay at 3x speed on frame time changed the card
    /// every second or so, which the verify lane's wall-clock check (I6) and the accessibility
    /// audit both caught.
    private func updateGuidance(camera: CameraFrame, time _: Double) {
        // A question is on screen: the next step waits for its answer.
        guard let map = coverage, state.endQuestion == nil, !state.overheadQuestion else { return }
        if let side = nextWallSide {
            state.guidance = .markNextWall(side: side, refusal: nextWallRefusal)
            state.guidanceHint = nil
            // While "Is this the next wall?" is up, the ring shows the corner: where the marked
            // wall meets this one, on the wall chain so it moves with the meter's anchor.
            state.target = pendingNextWall.map { map.wall.world(s: $0.s, height: Self.cornerRingHeight) }
            state.path = []
            logGuidance()
            return
        }
        if let span = tiltUpSpanIfDue(map, camera: camera) {
            state.guidance = .tiltUp(span: span)
            state.guidanceHint = nil
            state.target = map.wall.world(s: (span.lowerBound + span.upperBound) / 2, height: Self.tiltUpHeight(map))
            state.path = []
            logGuidance()
            return
        }
        let screenTime = Double(DispatchTime.now().uptimeNanoseconds) / 1_000_000_000
        let output = planner.update(coverage: map, camera: camera, time: screenTime)
        logPlannerSwitch(output)
        if let hidden = hiddenBlock(output.task, map, camera: camera) {
            seeBehindBands = hidden.bands
            state.guidance = .seeBehind(s: hidden.s)
            state.guidanceHint = nil
            state.target = hidden.bands.contains(.wall)
                ? map.wall.world(s: hidden.s, height: 1)
                : map.wall.world(s: hidden.s, height: 0, out: map.config.groundBandDepth / 2)
            // Where to stand for another angle depends on what is in the way, which the map
            // doesn't know; no path.
            state.path = []
        } else {
            seeBehindBands = []
            let step = Self.step(output.task)
            // The frame before's direction makes the new one sticky near the view's edges
            // (`AimHint.classify`), but only for the same step and so the same target.
            let previous = state.guidance == step ? state.guidanceHint?.aim : nil
            state.guidance = step
            state.guidanceHint = Self.hint(output, camera: camera, previous: previous)
            state.target = output.target
            state.path = output.path
        }
        logGuidance()
    }

    /// What the card of an aim step says beside the step (`GuidanceHint`); nil for other steps.
    /// `previous` is the direction shown for the same step on the frame before, if any.
    static func hint(_ output: GuidanceOutput, camera: CameraFrame, previous: AimDirection? = nil) -> GuidanceHint? {
        switch output.task {
        case .aimAtGround, .aimAtWall:
            return GuidanceHint(
                aim: output.target.map { direction(AimHint.classify(target: $0, camera: camera, previous: previous.map(Self.aimHint))) },
                needsSecondPosition: output.needsSecondPosition,
                stepBack: output.stepBack
            )
        case .walk, .markEnd, .stepBack, .seeBehind, .complete:
            return nil
        }
    }

    /// Why the planner changed the walk's task, and a stalled request, in the guidance log
    /// channel: the build 4.1 logs couldn't say why a card changed (#84). The packet's guidance
    /// entries keep their outcome only (`closingOutcome`).
    private func logPlannerSwitch(_ output: GuidanceOutput) {
        if let stalled = output.stalled {
            RuntimeLog.guidance.info("stalled: \(String(describing: stalled), privacy: .public) gained nothing for \(self.planner.config.stallTimeout) s; deferred until both ends are marked")
        }
        guard let reason = output.switched else { return }
        RuntimeLog.guidance.info("switch (\(reason.rawValue, privacy: .public)) to \(String(describing: output.task), privacy: .public)")
    }

    // MARK: Where the space ends

    /// Measures where the space in front of the wall ends from the vertical planes ARKit found
    /// (`FarSurface.spans`) and gives it to the coverage map, again whenever the planes, the wall
    /// or where kept frames look change (`FarSurfaceTracker`): a corridor's far wall or a side
    /// yard's fence is then the end of the space, not something to look past (#160), and a
    /// walk-out line behind it is not asked for as if it could be walked (#164).
    private func noteFarSurface() {
        guard let map = coverage,
              let spans = farSurface.spans(for: map, planes: wallPlanes, excluding: Set(refitPlaneID.map { [$0] } ?? [])) else { return }
        if spans.isEmpty != map.farSurface.isEmpty {
            let found = spans.map(\.out).min().map { "found, nearest \($0) m out, over \(spans.count) stretches" } ?? "none"
            RuntimeLog.engine.info("far surface: \(found, privacy: .public)")
        }
        coverage?.setFarSurface(spans)
    }

    // MARK: See-behind step (LiDAR)

    /// How far past a hidden stretch the camera must be before the walk asks to look behind it
    /// instead of walking on: 1 m, about two strides, so the stretch has had a chance to show from
    /// the angles the walk passes through. A guess to try on a phone, not measured.
    static let seeBehindPassed: Float = 1

    /// LiDAR phones: the stretch the walk's task is waiting on, when what keeps it open is hidden
    /// cells (the camera looked, and depth showed something nearer) rather than unseen ones.
    /// Walking up to it again gives the same view; another angle, or stepping round, can show it.
    ///
    /// An aim task's stretch counts as hidden when at least half of its cells still open (neither
    /// covered nor skipped) within 0.3 m of it are hidden: the planner's own window for the task
    /// (`GuidancePlanner.isSatisfied`), and "half" is a guess. A walk counts as blocked when the
    /// first open cell on its side, where the planner's reach stops, is hidden and the camera is
    /// `seeBehindPassed` beyond it: without this, a bush hiding the whole band leaves the walk
    /// saying "walk on" while its reach never moves.
    ///
    /// The planner raises `.seeBehind` itself when hidden cells lie near the camera; the step is
    /// then about the bands holding hidden cells near its s. Nil when there are none, which leaves
    /// no cell for "Can't see past it" to settle.
    private func hiddenBlock(_ task: GuidanceTask, _ map: CoverageMap, camera: CameraFrame) -> (s: Float, bands: [SurfaceBand])? {
        func open(_ band: SurfaceBand, _ index: Int) -> Bool {
            let level = map.level(band, index)
            return level != .covered && level != .skipped
        }
        func mostlyHidden(_ band: SurfaceBand, around s: Float) -> Bool {
            let cells = map.indices(overlapping: (s - 0.3)...(s + 0.3)).filter { map.isWithinEnds($0) && open(band, $0) }
            let hidden = cells.filter { map.level(band, $0) == .hidden }.count
            return hidden > 0 && hidden * 2 >= cells.count
        }
        switch task {
        case .seeBehind(let s):
            let bands = SurfaceBand.allCases.filter { !Self.hiddenCells(map, band: $0, around: s).isEmpty }
            return bands.isEmpty ? nil : (s, bands)
        case .aimAtGround(let s):
            return mostlyHidden(.ground, around: s) ? (s, [.ground]) : nil
        case .aimAtWall(let s):
            return mostlyHidden(.wall, around: s) ? (s, [.wall]) : nil
        case .walk(let side):
            let reach = planner.reach(side, coverage: map)
            let index = map.cellIndex(forS: side.sign * (reach + map.config.cellWidth / 2))
            let bands = SurfaceBand.allCases.filter { map.level($0, index) == .hidden }
            let beyond = side.sign * map.wall.wallPoint(camera.position).s - reach
            guard !bands.isEmpty, beyond >= Self.seeBehindPassed else { return nil }
            let cell = map.cellRange(index)
            return ((cell.lowerBound + cell.upperBound) / 2, bands)
        case .markEnd, .stepBack, .complete:
            return nil
        }
    }

    /// How far either side of a see-behind step's s its hidden cells are looked for: 1 m, the
    /// half-width of the window around the camera in which the planner looks for them
    /// (`GuidancePlanner.preferredTask`).
    static let seeBehindReach: Float = 1

    /// The hidden cells of `band` within `seeBehindReach` of `s`: what the see-behind step asks
    /// to see past, and what "Can't see past it" hands to review.
    static func hiddenCells(_ map: CoverageMap, band: SurfaceBand, around s: Float) -> [Int] {
        map.indices(overlapping: (s - seeBehindReach)...(s + seeBehindReach)).filter { map.level(band, $0) == .hidden }
    }

    /// A frame shown for review (not captured) keeps the current task but re-aims its target and
    /// path from the camera now on screen; otherwise the arrow points from where the walk last was.
    private func refreshCues(camera: CameraFrame) {
        guard state.phase == .wallWalk, state.endQuestion == nil, !state.overheadQuestion, nextWallSide == nil,
              let map = coverage, let task = planner.current else { return }
        if case .tiltUp = state.guidance { return }
        if case .seeBehind = state.guidance { return }
        let output = planner.cues(for: task, coverage: map, camera: camera)
        state.target = output.target
        state.path = output.path
        // The card follows the target on the frame shown, as the ring does.
        if var hint = state.guidanceHint {
            let previous = hint.aim.map(Self.aimHint)
            hint.aim = output.target.map { Self.direction(AimHint.classify(target: $0, camera: camera, previous: previous)) }
            state.guidanceHint = hint
        }
    }

    /// "I can't get there" or an answered end question settled the current task: choose the next
    /// one now. Stretches that stalled earlier stay deferred (`GuidancePlanner.settleCurrentTask`).
    func resetGuidanceAfterSkip(camera: CameraFrame, time: Double) {
        planner.settleCurrentTask()
        updateGuidance(camera: camera, time: time)
    }

    // MARK: Tilt-up step

    /// Half the stretch the tilt-up step asks about: 1.5 m (about 5 ft) each side of the meter,
    /// inside the marked ends. The spots the server tries first sit beside the meter, where the
    /// cable run is shortest, and a 2.58 ft battery fits on either side within it. 3 m is also
    /// about what one upright view takes in from 3 m out. A judgment call, not measured: a spot
    /// farther along gets its own `.overhead` request from the server.
    static let tiltUpReach: Float = 1.5

    /// The stretch to ask about, once both ends are marked and answered, the step hasn't been
    /// answered or skipped, and the phone is within reach of the stretch (`tiltUpInReach`) or the
    /// step is already showing; nil otherwise, and when the marked ends leave no stretch. Out of
    /// reach the walk's own tasks go on, and the step comes up when the phone comes back; still
    /// unsettled when the walk ends, it is settled with nothing recorded (`finishWalk`).
    private func tiltUpSpanIfDue(_ map: CoverageMap, camera: CameraFrame) -> ClosedRange<Float>? {
        guard !tiltUpSettled, let left = map.leftEnd, let right = map.rightEnd else { return nil }
        let low = max(left, -Self.tiltUpReach)
        let high = min(right, Self.tiltUpReach)
        guard low < high else { return nil }
        if case .tiltUp = state.guidance { return low...high }
        return Self.tiltUpInReach(low...high, cameraS: map.wall.wallPoint(camera.position).s) ? low...high : nil
    }

    /// Whether a phone at `cameraS` along the wall is near enough the tilt-up stretch to be asked
    /// about it: within `tiltUpReach` plus 1 m of its middle, so within about 1 m of the stretch
    /// when it is the full 3 m. On build 4.1 the step came up the moment both ends were marked,
    /// at the right end, 19 ft from the meter (#64). The 1 m is a guess, not measured.
    static func tiltUpInReach(_ span: ClosedRange<Float>, cameraS: Float) -> Bool {
        abs(cameraS - (span.lowerBound + span.upperBound) / 2) <= tiltUpReach + 1
    }

    /// How far above the top wall row (`CoverageConfig.wallCaptureHeight`, 7.5 ft) a view must
    /// reach to count as tilted up: 0.7 m, so about 9.8 ft above the ground, past a one-storey
    /// eave, where the view shows whether one is there. It is a height, not a pitch: a
    /// level view from 2 m out reaches about 8.5 ft and does not count, one from 2.6 m out
    /// reaches about 10 ft and does, and it shows what is overhead as well as a tilted one. A
    /// guess to try on a phone, not measured.
    static let tiltUpAbove: Float = 0.7

    /// The height the tilt-up step and an overhead request aim at, meters above the ground.
    static func tiltUpHeight(_ map: CoverageMap) -> Float { map.config.wallCaptureHeight + tiltUpAbove }

    /// The stretches a view shows at least `tiltUpHeight` up the wall (`CoverageMap.overheadReach`):
    /// empty unless the camera is tilted up at the wall.
    static func tiltedUp(_ camera: CameraFrame, _ map: CoverageMap) -> [ClosedRange<Float>] {
        let height = tiltUpHeight(map)
        return map.overheadReach(from: camera).filter { $0.out >= height }.map(\.span)
    }

    /// Raises the overhead question when a frame with normal tracking is tilted up where the scan
    /// needs it: over any of the tilt-up step's stretch during the walk, or, for an overhead gap
    /// request, over enough of the requested span that recording it settles the request
    /// (`GapPlanner.overheadViewSettles`), so "nothing overhead" always closes it. Only the
    /// homeowner can say whether what is above is open sky or an eave. Any frame with a photo
    /// counts, not only frames auto-capture kept: "nothing overhead" stores that photo as a
    /// keyframe, and the view counts only once it is stored (`keepOverheadView`).
    private func askOverheadIfTiltedUp(_ frame: SourceFrame) {
        guard !state.overheadQuestion, frame.tracking == .normal, frame.jpeg.isAvailable, let map = coverage else { return }
        let wanted: ClosedRange<Float>
        switch state.phase {
        case .wallWalk:
            guard case .tiltUp(let span) = state.guidance else { return }
            wanted = span
        case .gapRequest:
            guard let plan = gapPlan, case .overhead = plan.need, state.gap?.isSatisfied == false else { return }
            wanted = plan.span
        default:
            return
        }
        let seen = Self.tiltedUp(frame.camera, map)
        guard seen.contains(where: { $0.overlaps(wanted) }) else { return }
        if state.phase == .gapRequest, let plan = gapPlan, !gapPlanner.overheadViewSettles(plan, map, camera: frame.camera) { return }
        // The segment now: the answer can come after a tracking break.
        pendingOverhead = (frame, coverage?.pathSegment)
        state.overheadQuestion = true
        RuntimeLog.engine.info("tilt-up view over s \(seen.first?.lowerBound ?? 0)...\(seen.last?.upperBound ?? 0): asking what is overhead")
    }

    /// Ends the tilt-up step. `clear` is the homeowner's "Open sky or nothing overhead", which
    /// keeps the view the question was about; false for "A roof edge, porch or stairs", "Can't
    /// get there" or leaving the walk, which keep nothing.
    func settleTiltUp(clear: Bool) {
        let pending = pendingOverhead
        pendingOverhead = nil
        state.overheadQuestion = false
        tiltUpSettled = true
        // After settling, so the guidance recomputed once it is stored moves past the tilt-up step.
        let storing = clear && pending.map { keepOverheadView($0.frame, capturedIn: $0.segment) } == true
        RuntimeLog.engine.info("tilt-up step settled: \(storing ? "storing the clear overhead view" : "nothing recorded", privacy: .public)")
    }

    /// The answer to the overhead question during an overhead gap request. "Nothing overhead"
    /// keeps the view, which was checked to settle the request when the question was raised, so
    /// the request closes through `updateGap`. Something overhead means no view can settle it:
    /// the request goes to installer review and the gap loop moves on to the upload.
    func settleOverheadGap(clear: Bool) {
        let pending = pendingOverhead
        pendingOverhead = nil
        state.overheadQuestion = false
        guard clear else {
            skipCurrentGap(because: "something is overhead", refused: false)
            return
        }
        if pending.map({ keepOverheadView($0.frame, capturedIn: $0.segment) }) != true {
            RuntimeLog.engine.error("overhead answer: the view asked about could not be kept")
        }
    }

    /// The replay's closing run of tilted-up frames (`tiltedUp`): the walk leaves them out, and
    /// the tilt-up step and overhead gap requests play them (`playReplayTiltUp`). Empty at the end
    /// when the recording has none.
    static func tiltUpFrames(in replay: ReplayPlayer, map: CoverageMap) -> Range<Int> {
        var start = replay.frames.count
        while start > 0, !tiltedUp(replay.camera(at: start - 1), map).isEmpty { start -= 1 }
        return start..<replay.frames.count
    }

    /// Plays the replay's tilt-up frames, as a homeowner tilting up would show them. False when
    /// the recording has none.
    func playReplayTiltUp() -> Bool {
        guard let replay, let map = coverage else { return false }
        let frames = Self.tiltUpFrames(in: replay, map: map)
        guard !frames.isEmpty else { return false }
        replay.play(range: frames, speed: replaySpeed)
        return true
    }

    private func updateGap(camera: CameraFrame?) {
        guard let map = coverage, let plan = gapPlan, var request = state.gap else { return }
        request.progress = gapPlanner.progress(of: plan, map)
        noteWalkOut(plan, map, camera: camera, into: &request)
        let fresh = if case .overhead = plan.need {
            map.overheadCameras.count > overheadViewsAtGapStart
        } else {
            store.keyframes.count > keyframesAtGapStart
        }
        // Not while the end question is up: the homeowner marked the end and is saying what is
        // there, and that answer closes the request (`answerWallEnd`).
        let satisfied = gapPlanner.isSatisfied(plan, map) && fresh && state.endQuestion == nil
        state.guidance = .gap
        let center = (plan.span.lowerBound + plan.span.upperBound) / 2
        let cue = gapCue(plan, map, center: center)
        state.target = cue.target
        if request.spaceEnds != nil {
            // The walk-out line lies behind where the space ends (#164): no dotted line to it.
            state.path = []
        } else if let camera {
            let from = map.wall.wallPoint(camera.position).s
            // Every request but an overhead one needs its whole span seen or walked, and the
            // server's can run 20 ft or more (ground out to a pool's clearance), so the line runs
            // on to the span's far end. One tilted-up view from the middle covers an overhead one.
            let farEnd = abs(plan.span.lowerBound - from) > abs(plan.span.upperBound - from) ? plan.span.lowerBound : plan.span.upperBound
            let to = if case .overhead = plan.need { center } else { farEnd }
            state.path = [from, to].map { map.wall.world(s: $0, height: 0, out: cue.standOut) }
        }
        logGuidance()
        if satisfied, !request.isSatisfied {
            resolveGuidance(.met)
            request.isSatisfied = true
            request.progress = max(request.progress, gapPlanner.config.satisfiedFraction)
            state.gap = request
            RuntimeLog.engine.info("gap \(request.id) satisfied")
            let id = request.id
            Task {
                try? await Task.sleep(for: .seconds(autoAdvanceDelay))
                await waitForGate(.gapRequest)
                // Only if this same request is still showing (not skipped or replaced meanwhile).
                guard state.phase == .gapRequest, state.gap?.id == id else { return }
                afterGapResolved()
            }
        } else if !request.isSatisfied {
            state.gap = request
        }
    }

    /// A walk-out request's reading and whether the space ends short of its line (#164): the
    /// card then gives the distance that counts where the phone is, or says the space ends
    /// before the line and "I can't get there" is the answer. The reading is rounded to 3 in, its
    /// out down and what counts up, so it changes every few strides rather than every frame and
    /// never asks for less than counts; a guess at what reads calmly. Where the space ends is
    /// kept for the request once shown: ARKit refines its planes about ten times a second, and a
    /// headline that moved or came and went with them would re-arm the reply's lock each time
    /// (`InstructionCard.replyLock`).
    private func noteWalkOut(_ plan: GapPlan, _ map: CoverageMap, camera: CameraFrame?, into request: inout GapRequest) {
        guard case .walkOut = plan.need else { return }
        let step: Float = 0.0762
        let block = gapPlanner.walkOutBlock(plan, map)
        let ends = block.map { GapRequest.SpaceEnds(at: ($0.spaceEnds / step).rounded(.down) * step, needed: ($0.needed / step).rounded(.up) * step) }
        if request.spaceEnds == nil, let block, let ends {
            let found = String(
                format: "the space ends %.2f m out over s %.2f...%.2f, short of the line at up to %.2f m",
                block.spaceEnds, block.span.lowerBound, block.span.upperBound, block.needed)
            // The log's message is an escaping autoclosure, which can't capture `request`.
            let id = request.id
            RuntimeLog.engine.info("gap \(id) walk-out: \(found, privacy: .public)")
            request.spaceEnds = ends
        }
        request.walkOut = camera.flatMap { camera in
            let at = map.wall.wallPoint(camera.position)
            return gapPlanner.walkOutNeeded(plan, map, atS: at.s).map {
                GapRequest.WalkOutReading(out: (max(0, at.out) / step).rounded(.down) * step, needed: ($0 / step).rounded(.up) * step)
            }
        }
    }

    /// Where a gap request points the camera, and how far out from the wall to walk for it.
    private func gapCue(_ plan: GapPlan, _ map: CoverageMap, center: Float) -> (target: SIMD3<Float>, standOut: Float) {
        let standOff = planner.config.standOff
        switch plan.need {
        case .cells:
            let target = plan.band == .ground
                ? map.wall.world(s: center, height: 0, out: map.config.groundBandDepth / 2)
                : map.wall.world(s: center, height: 1.2)
            return (target, standOff)
        case .groundOut(let out):
            // 1 m beyond the requested depth: a phone at chest height (about 1.4 m) tilted down
            // there sees the ground from about 2 m nearer the wall out to that depth, within the
            // 65 degree view limit. Geometry only; not tried on a device.
            return (map.wall.world(s: center, height: 0, out: out), max(standOff, out + 1))
        case .walkOut(let out):
            // The walk has to pass `out` plus the wall's position error at the span's end where it
            // is larger (farther from the meter, or on a piece with a larger default); 0.3 m more
            // leaves room for drifting toward the wall. The 0.3 m is a guess.
            let error = max(map.positionError(atS: plan.span.lowerBound), map.positionError(atS: plan.span.upperBound))
            return (map.wall.world(s: center, height: 1.2), max(standOff, out + error + 0.3))
        case .overhead(let height):
            // Aim where a tilted-up view reaches, or at the height asked for when that is higher.
            let aim = max(Self.tiltUpHeight(map), height ?? 0)
            return (map.wall.world(s: center, height: aim), standOff)
        case .wallUp(let height):
            // At the height the view must pass, walked along the whole span like a cell request:
            // each stretch needs it from two places.
            return (map.wall.world(s: center, height: height), standOff)
        }
    }

    private func logGuidance() {
        noteGuidance()
        let name = Self.name(state.guidance)
        guard name != lastGuidanceLog else { return }
        lastGuidanceLog = name
        RuntimeLog.guidance.info("GUIDANCE=\(name, privacy: .public)")
    }

    /// Logs a capture-gate decision. The gate judges about ten frames a second, so a reason is
    /// logged when it differs from the last one logged; `always` logs every time (a kept frame,
    /// a shot taken). `decision` holds only enum reasons and frame ids, never image content.
    private func logGate(_ decision: String, always: Bool = false) {
        guard always || decision != lastGateLog else { return }
        lastGateLog = decision
        RuntimeLog.capture.info("gate \(decision, privacy: .public)")
    }

    /// The homeowner's path is not continuous across this point (tracking left normal, the
    /// session was interrupted, or the walk paused), so walked-path evidence must not join the
    /// poses on either side.
    private func breakWalkedPath(because reason: String) {
        guard coverage != nil else { return }
        coverage?.breakWalkedPath()
        RuntimeLog.capture.info("walked path broken: \(reason, privacy: .public)")
    }

    // MARK: Tracking recovery

    private func trackRelocalization(_ frame: SourceFrame) {
        guard live != nil else { return }
        guard case .limited(.relocalizing) = frame.tracking else {
            if let since = relocalizingSince {
                RuntimeLog.capture.info("relocalization ended after \(frame.timestamp - since, format: .fixed(precision: 1)) s: tracking \(Self.name(frame.tracking), privacy: .public)")
            }
            relocalizingSince = nil
            return
        }
        if relocalizingSince == nil {
            RuntimeLog.capture.info("relocalization started (phase \(self.state.phase.rawValue, privacy: .public))")
        }
        let since = relocalizingSince ?? frame.timestamp
        relocalizingSince = since
        // After 20 s ARKit is unlikely to relocalize; the old world frame is gone (checklist R5).
        guard frame.timestamp - since > 20 else { return }
        switch state.phase {
        case .findMeter, .meterCloseUp, .wallWalk:
            // The walk still needs the world frame: start again from the meter.
            RuntimeLog.capture.info("relocalization timed out after 20 s: resetting to the meter")
            resetSpatialState(reason: "relocalization timed out")
        case .gapRequest:
            // The scan so far is whole; only this request needs the lost frame. It goes to installer
            // review as if the homeowner couldn't get there, and the upload that follows keeps the
            // scan (and, for a request raised from the result, replaces the result with the new
            // answer). The clock restarts, so a later request gets its own 20 s.
            relocalizingSince = nil
            // A request already met is on its way to the upload (`afterGapResolved`).
            guard state.gap?.isSatisfied != true else { return }
            RuntimeLog.capture.info("relocalization timed out after 20 s: leaving the gap for installer review")
            skipCurrentGap(because: "the phone lost its place for 20 s")
        case .markFeatures:
            // The review needs no live frame: "Looks complete" uploads the scan as it is, and
            // "Add something", which taps into the world frame, waits for tracking to return
            // (`beginMarking`). Nothing is thrown away.
            break
        case .uploading, .spotConfirm, .result, .resultAR, .processing, .onboarding, .unsupported:
            // The bundle is already packed and the server's answer does not depend on the live
            // world frame, so the scan and the result stay. The AR result hides its overlay while
            // tracking is not normal and shows it again if ARKit does relocalize.
            relocalizingSince = nil
        }
    }

    private func handle(_ event: LiveEvent) {
        switch event {
        case .interrupted:
            // The phase, captures and strip stay as they are; ARKit relocalizes into the same
            // world frame when the session resumes (checklist R4).
            RuntimeLog.capture.info("session interrupted")
            breakWalkedPath(because: "session interrupted")
            state.coaching = .relocalizing
        case .interruptionEnded:
            RuntimeLog.capture.info("session interruption ended")
            state.coaching = .relocalizing
        case .cameraDenied:
            // As for a failed session: once the scan is sent, the answer stays on screen.
            switch state.phase {
            case .uploading, .spotConfirm, .result, .resultAR, .processing:
                RuntimeLog.engine.error("camera access lost after capture")
                _ = sourceState.sourceFailed(.recoverable, afterCapture: true)
                loseSpatialResult()
            default:
                fail(.cameraDenied)
            }
        case .failed(let message):
            // Once the scan is sent, the upload, the spot check's saved photo and the result no
            // longer need the camera: keep them on screen. Only the AR view needs it, and it
            // already hides the battery while the camera isn't tracking.
            switch state.phase {
            case .uploading, .spotConfirm, .result, .resultAR, .processing:
                RuntimeLog.engine.error("camera session failed after capture: \(message, privacy: .public)")
                _ = sourceState.sourceFailed(.recoverable, afterCapture: true)
                loseSpatialResult()
            default:
                // The screen shows plain words, so the camera's own error is only recorded here.
                RuntimeLog.engine.error("camera session failed: \(message, privacy: .public)")
                fail(.sessionFailed(message))
            }
        }
    }

    /// Forgets everything tied to the old world frame and asks for the meter again. What
    /// describes the house rather than a place in the old frame stays: the ground answer. The
    /// close-up's own state (gate, readout, view) is reset when the close-up starts again
    /// (`go(.meterCloseUp)`).
    func resetSpatialState(reason: String) {
        RuntimeLog.engine.info("spatial reset: \(reason, privacy: .public)")
        generation += 1
        // A new world frame is a new packet session: what was recorded is in the old frame.
        recorder.restart()
        scanWorldReset()
        resetPacketLog()
        relocalizingSince = nil
        planes = PlaneSnapshot()
        farSurface.reset()
        refitPlaneID = nil
        groundMeasured = false
        lastFrame = nil
        // A fresh map: the old world frame is gone, so its anchors and planes are meaningless.
        live?.restart()
        coverage = nil
        // The dot field lives in the old world frame too; kept, new depth would fuse into it.
        liveDots.reset()
        state.liveDots = .empty
        meterAnchorID.map { live?.removeAnchor($0) }
        meterAnchorID = nil
        meterTracking = nil
        meterAnchorPresence = MeterAnchorPresence()
        meterPlaneSource = .detectedPlane
        state.wall = nil
        state.coverage = .empty
        state.target = nil
        state.path = []
        state.features = []
        // A mark half placed holds taps in the old frame's wall coordinates.
        state.marking = nil
        pendingTaps = []
        state.gap = nil
        gapPlan = nil
        pastEndSide = nil
        clearedEnd = nil
        // Skipped requests name spans along the old wall.
        skippedGaps = []
        automaticGaps = []
        automaticGapsStopped = false
        seeBehindBands = []
        endKinds = [:]
        state.endQuestion = nil
        state.wallTooShort = false
        nextWallSide = nil
        nextWallRefusal = nil
        walkRefusals = WalkRefusals()
        resetTiltUp()
        resetSpotChecks()
        // An answer describes a scan that no longer exists; the next upload brings a new one.
        uploadTask?.cancel()
        groundFreshness = GroundFreshness()
        groundInjection?.cancel()
        groundInjection = nil
        placement = nil
        state.result = nil
        state.upload = .idle
        // The keyframes and stills, the meter close-up included, were taken in the old frame.
        store.discardKeyframes()
        // A bundle packed before this holds keyframes of the world frame just discarded.
        state.shareableScan = nil
        keptSourceIDs = []
        state.captureCount = 0
        state.lastCapture = nil
        gateProblem = nil
        gateClearSince = nil
        gateCoaching = CoachingDebouncer()
        autoCapture.reset()
        planner.reset()
        go(.findMeter)
    }

    // MARK: Wall

    /// Sets the wall from a meter point and the wall's outward normal, and starts coverage.
    func setWall(meter: SIMD3<Float>, outward: SIMD3<Float>, groundY: Float, groundMeasured: Bool) -> Bool {
        guard let frame = WallFrame(meter: meter, outward: outward, groundY: groundY) else { return false }
        coverage = CoverageMap(wall: frame)
        coverage?.heightError = groundMeasured ? 0 : Self.estimatedGroundError
        self.groundMeasured = groundMeasured
        endKinds = [:]
        endStamps = [:]
        state.endQuestion = nil
        state.wallTooShort = false
        nextWallSide = nil
        nextWallRefusal = nil
        walkRefusals = WalkRefusals()
        resetTiltUp()
        publishWall()
        publishCoverage()
        return true
    }

    func publishWall() {
        guard let map = coverage else { state.wall = nil; return }
        let wall = map.wall
        state.wall = WallGeometry(
            meter: wall.meter, along: wall.along, outward: wall.outward, groundY: wall.groundY,
            leftEnd: map.leftEnd, rightEnd: map.rightEnd,
            cornerSegments: wall.segments.indices.filter { $0 != wall.meterSegmentIndex }.map { index in
                let piece = wall.segments[index]
                return WallGeometry.Segment(span: piece.span, along: piece.along, outward: piece.outward, anchor: piece.anchor, anchorS: piece.anchorS)
            }
        )
        if state.phase == .resultAR { showResultInCamera(rising: false) }
        publishFeaturesPastEnds()
    }

    /// Takes the answer down when a ground change leaves it describing a ground the phone no
    /// longer has (`GroundFreshness`), before `publishWall` can draw it again on the new one.
    private func answerAfter(_ change: GroundFreshness.Change) {
        // A photo-processing packet doesn't follow the phone's ground after it is sent, and
        // nothing here may send that scan to the Legacy checker.
        guard scanContext?.profile.backend != .photoProcessing else { return }
        let screen: GroundFreshness.Screen = switch state.phase {
        case .uploading:
            switch state.upload {
            case .failed, .rejected, .unusableAnswer: .stopped
            case .idle, .packaging, .uploading, .analyzing, .done: .sending
            }
        case .spotConfirm: .spotCheck
        case .result: .result
        case .resultAR: .resultInCamera
        default: .noAnswer
        }
        switch groundFreshness.after(change, on: screen, scenePackaged: scenePackaged) {
        case .keep:
            return
        case .sendAgain:
            RuntimeLog.engine.info("ground changed on \(self.state.phase.rawValue, privacy: .public): answer taken down, sending the scan again")
            withdrawAnswer()
            startUpload()
        case .fail:
            RuntimeLog.engine.error("ground changed again on \(self.state.phase.rawValue, privacy: .public) before a new answer: none shown")
            withdrawAnswer()
            uploadTask?.cancel()
            uploadTask = nil
            state.upload = Self.groundKeptChanging
            // Already on the upload screen when the resend was under way, where `go` does nothing.
            go(.uploading)
            updateRecording()
        }
    }

    /// The upload screen when the ground changed again before the resend's answer was shown.
    /// "Try again" sends the scan as it is then.
    static let groundKeptChanging = UploadState.failed(
        message: "The phone was still measuring the ground under your wall, so we didn't show an answer. Your scan is saved on this phone, so you can try again.",
        offline: false)

    /// Takes the answer off the spot check, the result, its 3D preview and the camera view. The
    /// homeowner's earlier spot answers stay: they name stretches along the wall, which a ground
    /// change doesn't move.
    private func withdrawAnswer() {
        placement = nil
        state.result = nil
        state.spotCheck = nil
        spotConfirm.pending = nil
        spotConfirm.request = nil
        spotConfirm.shownArea = nil
        // A spot photo still loading for this answer (`presentAnswer`) must not open its check
        // once a retry brings back an equal answer.
        spotConfirm.asked += 1
        state.followUps = 0
        state.upload = .packaging
        hideResultInCamera()
    }

    /// "See it on your wall" on the live camera. The screen draws the result over the camera
    /// itself (`BatteryOverlay`) unless the AR scene is seen drawing it (`watchResultInCamera`).
    /// Build 4.1 trusted the AR scene as soon as it had the model and showed no battery at all
    /// (#67). Only a phone with LiDAR tries the AR scene, where the mesh can hide the result
    /// behind things in front of it; without LiDAR the screen draws it, as build 3.1 did. A
    /// replay, or a meter without an anchor, leaves it to the screen too.
    private func showResultInCamera(rising: Bool) {
        guard LiveCapture.supportsMesh, let live, let wall = state.wall, let result = state.result,
              let pose = meterTracking?.pose, state.spatialResultAvailable else { return }
        if rising || resultWatch == nil { watchResultInCamera() }
        // Each rebuild takes the model out of the scene and puts a new one in, so a wall that
        // moved by a centimeter or two (the ground or the meter's anchor refined) keeps the one
        // it has. It hangs on the meter's anchor, so it follows the anchor's corrections anyway.
        if !rising, let built = resultBuiltFor, Self.movedLittle(from: built, to: wall) { return }
        let model = ResultARModel.build(wall: wall, result: result)
        // The battery's middle (the outline's, on the ground, for a spot that isn't clean), or the
        // meter without a spot, in the model's coordinates.
        let focus = (result.spotCenter(on: wall) ?? wall.meter) - wall.meter
        guard live.showResult(model, builtFor: pose, focus: focus) else {
            resultBuiltFor = nil
            return
        }
        resultBuiltFor = wall
        live.setResultVisible(state.tracking == .normal)
        if rising { ResultARModel.rise(model) }
    }

    /// Looks at the AR scene about ten times a second while "See it on your wall" is up, and
    /// lets the screen's drawing step aside only while the scene is seen drawing the result
    /// (`ResultOverlayPolicy`). On its own clock, not in `ingest`: it has to notice a scene that
    /// stopped drawing whether frames arrive or not.
    private func watchResultInCamera() {
        resultWatch?.cancel()
        resultWatch = Task { [weak self] in
            var policy = ResultOverlayPolicy()
            while !Task.isCancelled {
                guard let self, self.state.phase == .resultAR, let live = self.live else { return }
                let look = live.resultIsDrawn()
                // While tracking is limited the model is disabled and the screen draws neither
                // layer, so the AR scene keeps the result: when tracking comes back the two switch
                // on together, rather than the Canvas showing until the next look.
                let held = look.held || (look.anchored && self.state.tracking != .normal)
                let usesRealityKit = policy.update(drawn: look.drawn, held: held, time: self.screenTime)
                if self.state.resultInCamera != usesRealityKit {
                    RuntimeLog.engine.info("AR result drawn by \(usesRealityKit ? "the AR scene" : "the screen overlay", privacy: .public)")
                    self.state.resultInCamera = usesRealityKit
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }

    private func hideResultInCamera() {
        resultWatch?.cancel()
        resultWatch = nil
        resultBuiltFor = nil
        live?.hideResult()
        state.resultInCamera = false
    }

    /// A wall change under these leaves the AR result's model as built: a few centimeters is
    /// within what the ground and the meter's anchor are known to. Display choices, not measured.
    private static let resultRebuildDistance: Float = 0.05
    private static let resultRebuildTurnCosine: Float = cos(2 * Float.pi / 180)

    /// True when `new` is `old` moved by less than `resultRebuildDistance` and turned by less
    /// than 2° in every part the model is built from: the meter, the ground, the ends and each
    /// piece of wall.
    private static func movedLittle(from old: WallGeometry, to new: WallGeometry) -> Bool {
        func closePoints(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Bool {
            simd_distance(a, b) < Self.resultRebuildDistance
        }
        func closeValues(_ a: Float?, _ b: Float?) -> Bool {
            guard let a, let b else { return a == nil && b == nil }
            // Equal first: a corner piece's span is infinite toward its open end.
            return a == b || abs(a - b) < Self.resultRebuildDistance
        }
        func closeDirections(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Bool {
            simd_dot(simd_normalize(a), simd_normalize(b)) > Self.resultRebuildTurnCosine
        }
        guard closePoints(old.meter, new.meter), closeValues(old.groundY, new.groundY),
              closeValues(old.leftEnd, new.leftEnd), closeValues(old.rightEnd, new.rightEnd),
              closeDirections(old.along, new.along), closeDirections(old.outward, new.outward),
              old.cornerSegments.count == new.cornerSegments.count else { return false }
        return zip(old.cornerSegments, new.cornerSegments).allSatisfy { a, b in
            closeValues(a.span.lowerBound, b.span.lowerBound) && closeValues(a.span.upperBound, b.span.upperBound)
                && closePoints(a.anchor, b.anchor) && closeValues(a.anchorS, b.anchorS)
                && closeDirections(a.along, b.along) && closeDirections(a.outward, b.outward)
        }
    }

    func publishCoverage() {
        guard let map = coverage else { return }
        guard map.revision != state.coverage.revision || state.coverage.wall.isEmpty else { return }
        let range = map.visibleRange
        let indices = map.indices(overlapping: range)
        state.coverage = CoverageStrip(
            cellWidth: map.config.cellWidth,
            firstCellS: map.cellRange(indices.lowerBound).lowerBound,
            wall: indices.map { Self.cell(map.level(.wall, $0)) },
            ground: indices.map { Self.cell(map.level(.ground, $0)) },
            // The band the walk asks for; heights above it are still reported when seen.
            wallBandHeight: map.config.wallWalkHeight,
            groundBandDepth: map.config.groundBandDepth,
            visibleRange: range,
            revision: map.revision
        )
    }

    /// Sets the end on `side`. `source` says who put it there: the homeowner's mark is stamped
    /// with the capture clock; an end inferred from the walk has no mark time (B-12).
    func setEnd(_ side: WallSide, at s: Float, kind: EndKind, source: WallEndSource) {
        setEnd(side, at: s, kind: kind, stamp: source == .homeowner ? .marked(at: captureClock) : .inferred)
    }

    /// Sets the end on `side` with a given stamp: a restored end keeps the one it had.
    func setEnd(_ side: WallSide, at s: Float, kind: EndKind, stamp: WallEndStamp) {
        guard var map = coverage else { return }
        endStamps[side] = stamp
        map.setEnd(side == .left ? .left : .right, at: s)
        // Ground past a limit end still counts toward clearances (server contract, "Ends and
        // corners"); past an unexplored end it doesn't.
        map.setEndIsLimit(side == .left ? .left : .right, kind == .limit)
        coverage = map
        endKinds[side] = kind
        state.wallTooShort = false
        state.endMarkRefusal = nil
        publishWall()
        publishCoverage()
        RuntimeLog.engine.info("end \(side.rawValue, privacy: .public) at s=\(s) (\(kind == .limit ? "limit" : "unexplored", privacy: .public), \(stamp.isInferred ? "inferred" : "marked", privacy: .public))")
        if let camera = lastFrame?.camera, state.phase == .wallWalk {
            updateGuidance(camera: camera, time: lastFrame?.timestamp ?? 0)
        }
    }

    /// What the homeowner said is at an already marked end.
    func setEndKind(_ side: WallSide, _ kind: EndKind) {
        endKinds[side] = kind
        updateCoverage { $0.setEndIsLimit(side == .left ? .left : .right, kind == .limit) }
        RuntimeLog.engine.info("end \(side.rawValue, privacy: .public) is \(kind == .limit ? "limit" : "unexplored", privacy: .public)")
    }

    func clearEnd(_ side: WallSide) {
        endStamps[side] = nil
        updateCoverage { $0.clearEnd(side == .left ? .left : .right) }
        endKinds[side] = nil
        if state.endQuestion == side { state.endQuestion = nil }
        publishWall()
    }

    var bothEndsMarked: Bool { coverage?.leftEnd != nil && coverage?.rightEnd != nil }

    // MARK: Gap loop

    /// After the review: ask for the planner's gap, or upload. Confirming the review starts a new
    /// pass of server requests raised without a tap.
    func runGapCheck() {
        automaticGaps = []
        automaticGapsStopped = false
        // A request needs the camera in the scan's world frame; while the phone has lost its
        // place, the scan goes as it is and the server's answer lists what is still unseen.
        guard !state.tracking.hasLostItsPlace else {
            RuntimeLog.engine.info("gap check skipped: the phone has lost its place; uploading the scan as it is")
            startUpload()
            return
        }
        guard let map = coverage, let plan = gapPlanner.plan(map), !skippedGaps.contains(plan) else {
            startUpload()
            return
        }
        beginGap(plan, origin: .phone, reason: plan.band == .ground ? .groundNearCandidate : .wallAboveCandidate)
    }

    func beginGap(_ plan: GapPlan, origin: GapRequest.Origin, reason: GapRequest.Reason) {
        // A request with a reach says what to do in its own terms, whatever the caller passed.
        let reason: GapRequest.Reason = switch plan.need {
        case .cells: reason
        case .groundOut(let out): .groundOut(out: out)
        case .walkOut(let out): .walkOut(out: out)
        case .overhead: .overhead
        // The server's own words name the height ("seen at least 6 ft 6 in up the wall").
        case .wallUp: reason
        }
        gapCounter += 1
        gapPlan = plan
        keyframesAtGapStart = store.keyframes.count
        overheadViewsAtGapStart = coverage?.overheadCameras.count ?? 0
        let progress = coverage.map { gapPlanner.progress(of: plan, $0) } ?? 0
        state.gap = GapRequest(
            id: gapCounter, origin: origin, reason: reason, band: plan.band == .ground ? .ground : .wall, span: plan.span,
            progress: progress, isSatisfied: false, pastEndSide: pastEndSide)
        state.guidance = .gap
        go(.gapRequest)
        updateGap(camera: lastFrame?.camera)
    }

    /// A closed or skipped gap goes straight to the upload, which re-runs the server's checks
    /// with the new evidence (the closed loop: gap, instruction, capture, updated result). The
    /// answer then leads to the next capturable request or to the result (`upload`).
    private func afterGapResolved() {
        // A request left with its end question unanswered (the phone lost its place, say): the
        // end stays the homeowner's mark, unexplored as for any unanswered end, and the question
        // must not outlive the request and hold the next one.
        if let side = state.endQuestion, side == pastEndSide {
            state.endQuestion = nil
            state.endQuestionLeavesOut = nil
            state.endQuestionLeavesOutSeen = false
        }
        state.endMarkRefusal = nil
        settleClearedEnd()
        gapPlan = nil
        pastEndSide = nil
        pendingOverhead = nil
        state.overheadQuestion = false
        state.gap = nil
        startUpload()
    }

    /// The past_end request's end was marked again and the homeowner said the wall stops there:
    /// the request is settled, so the scan goes to the upload like a closed gap. A corner doesn't
    /// settle it (`answerWallEnd`).
    func settlePastEnd() {
        guard state.phase == .gapRequest, var request = state.gap, !request.isSatisfied else { return }
        resolveGuidance(.met)
        request.isSatisfied = true
        request.progress = 1
        state.gap = request
        RuntimeLog.engine.info("gap \(request.id) settled by marking the end again")
        let id = request.id
        Task {
            try? await Task.sleep(for: .seconds(autoAdvanceDelay))
            await waitForGate(.gapRequest)
            guard state.phase == .gapRequest, state.gap?.id == id else { return }
            afterGapResolved()
        }
    }

    /// Keeps the tilted-up view the overhead question was about as overhead evidence. Call it
    /// only once the homeowner answered that nothing is overhead (`answerOverhead(clear: true)`):
    /// the camera can't tell open sky from an eave. The view's photo is stored as a keyframe
    /// first, and it counts as overhead evidence only once stored (`recordOverhead`), like every
    /// other view. Returns false when it can't be kept: no photo, tracking not normal, or the
    /// view doesn't show the wall from the top of the sampled rows (7.5 ft, `wallCaptureHeight`) upward.
    /// `segment` is the walked-path segment the view was captured in.
    func keepOverheadView(_ frame: SourceFrame, capturedIn segment: Int?) -> Bool {
        guard let map = coverage, frame.jpeg.isAvailable, frame.tracking == .normal,
              !map.overheadReach(from: frame.camera).isEmpty else { return false }
        keep(frame, overhead: true, capturedIn: segment)
        return true
    }

    /// Records a stored tilted-up view in the coverage map; the export then sends each stretch it
    /// reached as an overhead entry with the height seen.
    private func recordOverhead(_ frame: SourceFrame) {
        guard var map = coverage else { return }
        let reach = map.recordOverhead(frame.camera, trackingNormal: frame.tracking == .normal)
        guard !reach.isEmpty else {
            RuntimeLog.engine.error("overhead: the stored view no longer reaches above the wall band; nothing recorded")
            return
        }
        coverage = map
        RuntimeLog.engine.info("overhead: kept a view reaching \(reach.map(\.out).min() ?? 0) m over s=\(reach.first?.span.lowerBound ?? 0)...\(reach.last?.span.upperBound ?? 0)")
    }

    /// Ends the current request without the view it asked for ("I can't get there", something
    /// overhead, or "Show my result"): recorded for installer review, then on to the upload. The
    /// answer that follows raises the next item it lists, never this one again
    /// (`automaticGapQueue`); only "Show my result" (`stopGapRequests`) ends the requests.
    ///
    /// `deferring` is a request this one stands for that nobody was shown: the past_end request
    /// from the corner the homeowner just marked (`answerWallEnd`), which this request can't
    /// follow. Recorded with the skipped requests only, so the next answer doesn't raise it again
    /// (`GapPlan.asksForSameView`); it logs no guidance and marks no cells, since no view was
    /// asked for or taken there. A past_end from a different end later, and other requests, can
    /// still be raised.
    func skipCurrentGap(because reason: String = "the homeowner can't get there", refused: Bool = true, deferring: GapPlan? = nil) {
        guard let plan = gapPlan else { return }
        // Something overhead is an answer: the request goes to review without its view.
        resolveGuidance(refused ? .cannotReach : .skipped)
        // Only a cell request marks cells: skipping a deeper, walked or overhead view says
        // nothing about the band the strip draws.
        if plan.need == .cells { coverage?.markSkipped(plan.band, plan.span) }
        skippedGaps.append(plan)
        if let deferring { skippedGaps.append(deferring) }
        publishCoverage()
        RuntimeLog.engine.info("gap \(self.gapCounter) left for installer review: \(reason, privacy: .public)")
        afterGapResolved()
    }

    /// "Show my result" on a server request: this view goes to installer review like one the
    /// homeowner can't get to, and after the upload that follows the result shows instead of
    /// the answer's next request.
    func stopGapRequests() {
        guard gapPlan != nil, state.gap?.origin == .server else { return }
        automaticGapsStopped = true
        skipCurrentGap(because: "the homeowner asked for the result", refused: false)
    }

    // MARK: Upload

    enum PendingSaves { case done, scanChanged, stillWriting }

    /// Waits, for at most 10 s, for the keyframe writes this world started, so the capture holds
    /// every photo kept before the send. `stillWriting` when some are still being written then: a
    /// capture sealed now would leave them out, and their late `onKept` would be refused.
    func drainPendingSaves() async -> PendingSaves {
        let scan = generation
        for _ in 0..<200 where (pendingSaves[scan] ?? 0) > 0 {
            try? await Task.sleep(for: .milliseconds(50))
        }
        guard scan == generation else { return .scanChanged }
        return (pendingSaves[scan] ?? 0) > 0 ? .stillWriting : .done
    }

    func startUpload() {
        // The scan's own backend only: a photo-processing scan never reaches the Legacy checker.
        if scanContext?.profile.backend == .photoProcessing {
            startPhotoProcessing()
            return
        }
        scenePackaged = false
        // "Try again" sends from the upload screen; anything else is a new send of this scan.
        if state.phase != .uploading { unusableAnswers = 0 }
        go(.uploading)
        injectGroundForTest()
        uploadTask?.cancel()
        uploadTask = Task { await upload() }
    }

    private func upload() async {
        let scan = generation
        // A ground change can cancel a resend before it starts (`answerAfter`); its failure stays.
        guard !Task.isCancelled else { return }
        state.upload = .packaging
        // Sending again from a failed upload stays on this phase, so `go` doesn't restart them.
        updateRecording()
        // Keyframe writes still in flight belong in the scene's keyframe list.
        for _ in 0..<200 where (pendingSaves[scan] ?? 0) > 0 {
            try? await Task.sleep(for: .milliseconds(50))
        }
        guard scan == generation, !Task.isCancelled else { return }
        // The scene, the wall in world meters, the LiDAR mesh and the packet's inputs, read in
        // this one turn (`captureUpload`). From here the upload's ground is fixed: a ground change
        // while the mesh is measured withdraws this upload and sends again, once, and an anchor
        // correction leaves it as captured (`GroundFreshness`, `answerAfter`).
        let capture: UploadPackaging<PacketInputs>
        do {
            capture = try captureUpload()
        } catch {
            RuntimeLog.engine.error("scene.json export failed: \(String(describing: error), privacy: .public)")
            state.upload = UploadFailure.packaging(error)
            updateRecording()
            return
        }
        scenePackaged = true
        // LiDAR phones: the mesh measured against the captured wall for what faces it and what is
        // overhead, off the main actor, then the captured scene serialized with those measurements
        // and the same bytes given to the captured packet. A withdrawn, cancelled or reset upload
        // comes back with nothing, so it schedules no bundle and no submit.
        let packaged: UploadPackaging<PacketInputs>.Packaged
        do {
            guard let done = try await capture.package(
                attachScene: { $0.scene = $1 },
                isCurrent: { scan == generation && !Task.isCancelled }
            ) else { return }
            packaged = done
        } catch {
            RuntimeLog.engine.error("scene.json export failed: \(String(describing: error), privacy: .public)")
            state.upload = UploadFailure.packaging(error)
            updateRecording()
            return
        }
        if let mesh = capture.mesh {
            RuntimeLog.engine.info("mesh: \(mesh.vertices.count) vertices, \(mesh.indices.count / 3) triangles; \(packaged.measurement.facing.count) facing and \(packaged.measurement.overheads.count) overhead measurements")
        }
        let scene = packaged.scene
        writeScanStamp(answer: placement)
        saveBundle(packaged.packet)
        guard !Task.isCancelled else { return }
        state.upload = .uploading(fraction: 0)
        analyzingSince = nil
        do {
            let data = try await resultClient.submit(scene: scene) { [weak self] fraction in
                Task { @MainActor in
                    guard let self, scan == self.generation, case .uploading = self.state.upload else { return }
                    if fraction >= 1 { self.analyzingSince = ContinuousClock.now }
                    self.state.upload = fraction >= 1 ? .analyzing : .uploading(fraction: fraction)
                }
            }
            // A cancelled upload (a ground change during a resend) must not show its answer.
            try Task.checkCancellation()
            guard scan == generation else { return }
            if analyzingSince == nil { analyzingSince = ContinuousClock.now }
            state.upload = .analyzing
            let result = try PlacementResult.decode(data)
            // "Check clearances" stays up for `analyzingMinimum` however fast the answer came,
            // so the step and then its tick are drawn (issue #31).
            let dwell = UploadPacing.remaining(since: analyzingSince, minimum: .seconds(Self.analyzingMinimum), now: ContinuousClock.now)
            if dwell > .zero {
                try await Task.sleep(for: dwell)
                // A sleep that ended before the cancel doesn't throw.
                guard scan == generation, state.phase == .uploading, !Task.isCancelled else { return }
            }
            placement = result
            unusableAnswers = 0
            noteExchange(scene: scene, answer: data, packet: packaged.packet)
            writeScanStamp(answer: result)
            state.result = presentation(of: result, isSample: resultClient.isSample)
            state.followUps = automaticGapQueue(result).count
            state.upload = .done
            await waitForGate(.uploading)
            guard scan == generation else { return }
            if let next = automaticGapQueue(result).first {
                // The upload screen says "One more view to finish" once the answer is in
                // (`state.result` set, upload `.done`, both kept through the request): long
                // enough to read before the camera takes over.
                try await Task.sleep(for: .seconds(Self.followUpHold))
                guard scan == generation, state.phase == .uploading, !Task.isCancelled else { return }
                automaticGaps.append(next.plan)
                RuntimeLog.engine.info("answer lists capturable evidence: asking for it (\(self.automaticGaps.count) of at most \(Self.maxAutomaticGaps))")
                beginServerGap(next.item, plan: next.plan)
                return
            }
            // Every step ticked, "Clearances checked" last, for `resultHold` before the result
            // replaces the screen: going on in the same turn never drew the tick (issue #31).
            try await Task.sleep(for: .seconds(Self.resultHold))
            guard scan == generation, state.phase == .uploading, !Task.isCancelled else { return }
            // Keep the completion tick, then ask about the proposed spot before showing it.
            presentAnswer()
        } catch is CancellationError {
            return
        } catch {
            // URLSession reports a cancelled request as a URLError, which would replace the
            // failure the canceller already shows.
            guard scan == generation, !Task.isCancelled else { return }
            RuntimeLog.engine.error("upload failed: \(String(describing: error), privacy: .public)")
            state.upload = UploadFailure.state(for: error, sample: resultClient.isSample, unusableAnswers: &unusableAnswers)
            updateRecording()
        }
    }

    /// The answer's items this pass will still raise without a tap, in order, with their
    /// requests: those a capture can settle (the result's "capturable"), not skipped and not yet
    /// raised in this pass (`GapPlanner.serverRequests`), up to `maxAutomaticGaps` requests.
    /// Empty once the homeowner tapped "Show my result". `asking`, a request about to be raised,
    /// counts as raised.
    private func automaticGapQueue(_ result: PlacementResult, asking: GapPlan? = nil) -> [(item: PlacementMissingEvidence, plan: GapPlan)] {
        // A request raised while the phone has lost its place could only time out: show the result.
        guard !automaticGapsStopped, !state.tracking.hasLostItsPlace, let map = coverage else { return [] }
        let asked = automaticGaps + (asking.map { [$0] } ?? [])
        // A new view cannot settle an area the homeowner has already said is obstructed, or
        // couldn't check. Filter before the request limit so those areas do not consume the
        // remaining slots.
        let capturable = result.missingEvidence.filter { item in
            gapPlanner.plan(for: item, leftEnd: map.leftEnd, rightEnd: map.rightEnd, limitEnds: map.limitEnds)
                .map { captureCanSettle($0) } ?? false
        }
        return gapPlanner.serverRequests(
            in: capturable, leftEnd: map.leftEnd, rightEnd: map.rightEnd, limitEnds: map.limitEnds,
            asked: asked, skipped: skippedGaps, limit: Self.maxAutomaticGaps - automaticGaps.count)
    }

    /// Starts the capture for an item of the server's missing evidence, whether tapped on the
    /// result or raised after an upload.
    func beginServerGap(_ item: PlacementMissingEvidence, plan: GapPlan) {
        // Counted before a past_end request clears its end, which would change the other requests.
        state.followUps = 1 + (placement.map { automaticGapQueue($0, asking: plan).count } ?? 0)
        var pastEnd: WallSide?
        if item.kind == .pastEnd, let side = item.side {
            // The walk has to go past the end it stopped at; that end is no longer a limit. It
            // stays cleared until the homeowner marks it again (markWallEnd) or the request ends
            // (settleClearedEnd).
            let wallSide: WallSide = side == .left ? .left : .right
            pastEnd = wallSide
            let old = wallSide == .left ? coverage?.leftEnd : coverage?.rightEnd
            // An end without a stamp comes back as inferred: no source is ever made up for it.
            clearedEnd = old.map { (s: $0, kind: endKinds[wallSide] ?? EndKind.unexplored, stamp: endStamps[wallSide] ?? .inferred) }
            clearEnd(wallSide)
        }
        // Set first, so the guidance log records the request as a past-end one.
        pastEndSide = pastEnd
        beginGap(plan, origin: .server, reason: .server(detail: item.message))
    }

    /// A past_end request leaving the screen settles the end it cleared (`PastEndSettlement`).
    /// Marked again during the request ("Wall ends here"), the homeowner's end stands, nearer or
    /// farther than the cleared one, which only said how far the walk had seen. Met by views,
    /// the end moves on past the ground the request showed, unexplored and inferred
    /// (`GapPlanner.endAfterPastEnd`). Skipped, the cleared end comes back with its kind, source
    /// and mark time. Left without an end, the export would run the wall out to whatever the fog
    /// saw, and the next past_end request would be planned from the meter (issue #35).
    private func settleClearedEnd() {
        guard let side = pastEndSide, let old = clearedEnd, let plan = gapPlan else { return }
        clearedEnd = nil
        let marked = (side == .left ? coverage?.leftEnd : coverage?.rightEnd) != nil
        switch PastEndSettlement.when(endMarkedDuringRequest: marked, met: state.gap?.isSatisfied == true) {
        case .keepMarked:
            return
        case .moveOn:
            setEnd(side, at: gapPlanner.endAfterPastEnd(plan, side: side == .left ? .left : .right, clearedAt: old.s), kind: .unexplored, source: .inferred)
        case .restore:
            setEnd(side, at: old.s, kind: old.kind, stamp: old.stamp)
        }
    }

    /// Writes the capture packet (`ScanEngine+Packet.swift`) with this scene.json inside, zipped
    /// into the scan folder's `scan.zip`: the bundle "Share scan" offers (`state.shareableScan`)
    /// whatever the upload then does: it fails, is refused or answers. Nothing uploads the
    /// packet, and the upload never waits for it or fails because of it. The zip is rewritten in
    /// place, so it is not offered while a write is under way, and writes run one after another:
    /// a retry's write waits for the last one, and a write already superseded is skipped.
    /// `inputs` were captured with the upload's scene (`captureUpload`); this rereads nothing.
    ///
    /// Inputs read from an earlier store write into that store's folder, which the current
    /// store's cleanup may delete; they are refused before they change what this scan offers.
    /// The callers already drop a capture from before Start over; this keeps that true here.
    ///
    /// Start over doesn't wait for a write in flight: the next store's cleanup waits for
    /// `bundleTask` before deleting the folder, so the write's bundle lands and is kept.
    func saveBundle(_ inputs: PacketInputs?) {
        if let inputs, inputs.storeDirectory != store.directory {
            RuntimeLog.engine.error("scan bundle not written: its capture belongs to a scan already started over")
            return
        }
        state.shareableScan = nil
        bundleSerial += 1
        let serial = bundleSerial
        let scan = generation
        let previous = bundleTask
        guard let inputs else {
            RuntimeLog.engine.error("scan bundle not written: no wall")
            return
        }
        bundleTask = Task {
            await previous?.value
            guard scan == generation, serial == bundleSerial else { return }
            do {
                let written = try await Task.detached(priority: .userInitiated) { try Self.writePacket(inputs) }.value
                RuntimeLog.engine.info("bundle \(written.url.path, privacy: .public): packet \(PacketManifest.version, privacy: .public) with \(written.summary, privacy: .public) (kept on the phone)")
                guard scan == generation, serial == bundleSerial else { return }
                state.shareableScan = written.url
            } catch {
                RuntimeLog.engine.error("scan bundle not written: \(String(describing: error), privacy: .public)")
            }
        }
    }

    // MARK: Result for AR

    /// The replay frame that sees the chosen spot best, for the AR result on a replay.
    private func bestFrameForResult() -> Int? {
        guard let replay, let wall = coverage?.wall else { return nil }
        let spot = state.result?.spot
        let s = spot.map { ($0.span.lowerBound + $0.span.upperBound) / 2 } ?? 0
        let point = wall.world(s: s, height: 0.5, out: 0.3)
        return replay.bestFrame(showing: point)
    }

    // MARK: Start over

    func resetAll() {
        generation += 1
        uploadTask?.cancel()
        replay?.stop()
        coverage = nil
        farSurface.reset()
        refitPlaneID = nil
        liveDots.reset()
        state.liveDots = .empty
        meterAnchorID.map { live?.removeAnchor($0) }
        meterAnchorID = nil
        meterTracking = nil
        meterAnchorPresence = MeterAnchorPresence()
        meterPlaneSource = .detectedPlane
        // The old scan's bundle may still be packing in the folder the new store's cleanup lists
        // as never packaged. Start over doesn't wait for it; the cleanup's deletion does, for this
        // chain head (every write waits for the one before it). `generation` was raised above,
        // so a queued write for the old scan still skips, and a finished one isn't offered here.
        store = KeyframeStore(deletingAfter: bundleTask)
        recorder = Self.makeRecorder(store)
        live?.setRecorder(recorder)
        endScan()
        motion.stop()
        resetPacketLog()
        keptSourceIDs = []
        autoCapture.reset()
        gateCoaching = CoachingDebouncer()
        planner.reset()
        closeUpGate = CloseUpGate()
        gapPlan = nil
        skippedGaps = []
        pastEndSide = nil
        clearedEnd = nil
        automaticGaps = []
        automaticGapsStopped = false
        seeBehindBands = []
        endKinds = [:]
        state.endQuestion = nil
        state.wallTooShort = false
        nextWallSide = nil
        nextWallRefusal = nil
        walkRefusals = WalkRefusals()
        resetTiltUp()
        resetSpotChecks()
        groundFreshness = GroundFreshness()
        groundInjection?.cancel()
        groundInjection = nil
        placement = nil
        // The bundle belongs to the scan being thrown away; `generation` stops a write in flight
        // from offering it again.
        state.shareableScan = nil
        state.wall = nil
        state.coverage = .empty
        state.features = []
        state.groundAnswer = nil
        state.marking = nil
        state.gap = nil
        state.result = nil
        state.upload = .idle
        state.captureCount = 0
        state.lastCapture = nil
        state.target = nil
        state.path = []
        state.coaching = nil
        state.guidance = .findMeter
        state.guidanceHint = nil
        state.closeUp = .aiming(hold: 0, problem: nil)
        state.closeUpFailedAttempts = 0
        state.meterNumber = nil
        closeUpRetake = nil
        meterReadout = nil
        state.isPracticeScan = false
        go(.onboarding)
        replay?.show(index: 0)
    }

    /// The overhead views belong to the world frame and the scan they were seen in.
    private func resetTiltUp() {
        tiltUpSettled = false
        pendingOverhead = nil
        state.overheadQuestion = false
    }

    // MARK: Internal accessors for marking and export

    var currentFrame: SourceFrame? { lastFrame }
    var liveCapture: LiveCapture? { live }
    var wallEndKinds: [WallSide: EndKind] { endKinds }

    /// The meter's anchor, and the pose the wall set with it agrees with.
    func setMeterAnchor(_ id: UUID?, pose: simd_float4x4?) {
        meterAnchorID = id
        meterTracking = pose.map(MeterAnchorTracking.init)
        meterAnchorPresence = MeterAnchorPresence()
    }

    var detectedGroundPlanes: [GroundPlaneEvidence] { groundPlanes }
    var detectedWallPlanes: [WallPlaneEvidence] { wallPlanes }

    /// The camera failed after the scan was sent: no frame will come to say tracking was lost, so
    /// the last one's "normal" would keep the AR result up and offered. The answer stays; the
    /// spatial result goes.
    private func loseSpatialResult() {
        state.tracking = .notAvailable
        state.spatialResultAvailable = false
        hideResultInCamera()
        if state.phase == .resultAR { go(.result) }
    }

    /// Start over after a failure: the failed source is let go and the failure cleared, so the
    /// next scan starts a new session (`startSourceIfNeeded`) or reads the replay again. A
    /// device that can't run world tracking stays failed.
    func releaseFailedSource() {
        let discard = sourceState.startOver()
        if sourceState.failure == nil { state.failure = nil }
        guard discard else { return }
        live?.pause()
        live = nil
        state.feed = .none
        if replay == nil, let folder = options.replayFolder { Task { await loadReplay(folder) } }
    }

    func updateCoverage(_ body: (inout CoverageMap) -> Void) {
        guard var map = coverage else { return }
        body(&map)
        coverage = map
        publishCoverage()
    }

    // MARK: Packet log

    /// Forgets the guidance log, the mark times and the clock: they belong to the packet session
    /// being thrown away.
    private func resetPacketLog() {
        guidanceLog = GuidanceLog()
        markTimes = [:]
        endStamps = [:]
        captureClock = nil
    }

    /// Records what request is on screen now in the guidance log.
    func noteGuidance() {
        publishEndPreview()
        guard let t = captureClock else { return }
        guidanceLog.show(guidanceRequest(), at: t) { old, next in closingOutcome(old, next: next) }
    }

    /// Closes the open request with an outcome an action settled.
    func resolveGuidance(_ outcome: GuidanceLog.Outcome) {
        guard let t = captureClock else { return }
        guidanceLog.resolve(outcome, at: t)
    }

    /// Closes the open request and takes it off the log's screen, so the same step shown again
    /// is a new request: after Back on the next-wall step, "It turns a corner" asks for the next
    /// wall again while `state.guidance` never left it (review of #136).
    func withdrawGuidance(_ outcome: GuidanceLog.Outcome) {
        guard let t = captureClock else { return }
        guidanceLog.resolve(outcome, at: t)
        guidanceLog.show(nil, at: t) { _, _ in outcome }
    }
}

// MARK: - Mapping to contract values

extension ScanEngine {
    static func cell(_ level: CoverageLevel) -> CellState {
        switch level {
        case .unseen: .unseen
        case .seen: .seen
        case .covered: .covered
        case .skipped: .skipped
        case .hidden: .hidden
        }
    }

    static func step(_ task: GuidanceTask) -> GuidanceStep {
        switch task {
        case .walk(let side): .walk(side: side == .left ? .left : .right, remaining: nil)
        case .markEnd(let side): .markEnd(side: side == .left ? .left : .right)
        case .aimAtGround(let s): .aimAtGround(s: s)
        case .aimAtWall(let s): .aimAtWall(s: s)
        case .stepBack: .stepBack
        case .seeBehind(let s): .seeBehind(s: s)
        case .complete: .walkComplete
        }
    }

    static func direction(_ hint: AimHint) -> AimDirection {
        switch hint {
        case .onScreen: .onScreen
        case .above: .above
        case .below: .below
        case .left: .left
        case .right: .right
        case .behind: .behind
        }
    }

    /// The inverse of `direction(_:)`.
    static func aimHint(_ direction: AimDirection) -> AimHint {
        switch direction {
        case .onScreen: .onScreen
        case .above: .above
        case .below: .below
        case .left: .left
        case .right: .right
        case .behind: .behind
        }
    }

    static func name(_ tracking: TrackingQuality) -> String {
        switch tracking {
        case .notAvailable: "notAvailable"
        case .normal: "normal"
        case .limited(let reason): "limited.\(reason)"
        }
    }

    static func name(_ step: GuidanceStep) -> String {
        switch step {
        case .findMeter: "findMeter"
        case .aimAtWallForMeter: "aimAtWallForMeter"
        case .holdOnMeter: "holdOnMeter"
        case .walk(let side, _): "walk.\(side.rawValue)"
        case .markEnd(let side): "markEnd.\(side.rawValue)"
        case .aimAtGround: "aimAtGround"
        case .aimAtWall: "aimAtWall"
        case .stepBack: "stepBack"
        case .walkComplete: "walkComplete"
        case .tiltUp: "tiltUp"
        case .markNextWall(let side, _): "markNextWall.\(side.rawValue)"
        case .gap: "gap"
        case .seeBehind: "seeBehind"
        }
    }

    static func problem(_ issue: CloseUpIssue) -> CloseUpProblem {
        switch issue {
        case .blurry: .blurry
        case .tooDark: .tooDark
        case .tooBright: .tooBright
        case .notCentered: .meterNotCentered
        case .tooFar: .tooFar
        case .tracking: .tracking
        }
    }

    /// Tracking problems first (they block everything), then the capture gate's reason.
    func coaching(for tracking: TrackingQuality, skip: CaptureDecision.SkipReason?) -> Coaching? {
        switch tracking {
        case .notAvailable: return .trackingLost
        case .limited(.initializing): return .initializing
        case .limited(.excessiveMotion): return .slowDown
        case .limited(.insufficientFeatures): return .needsTexture
        case .limited(.relocalizing): return .relocalizing
        case .limited(.unknown): return .trackingLost
        case .normal:
            switch skip {
            case .movingFast?, .blurry?: return .holdSteady
            case .turningFast?: return .turnSlowly
            case .tooDark?: return .tooDark
            default: return nil
            }
        }
    }
}
