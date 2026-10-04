import HouseScanKit
import SwiftUI

/// A fake engine for the UI demo: implements `ScanActions` by scripting `ScanViewState` over
/// the made-up wall in `DemoScene`. It mimics the real engine's phases and timing closely enough
/// to exercise every screen; it measures nothing.
@MainActor
final class DemoEngine: ScanActions {
    let state = ScanViewState()

    private let freeze: Bool
    private let noFeed: Bool
    /// `-uiDemoCloseUpSkipped`: the meter close-up was skipped.
    private let closeUpSkipped: Bool
    private let offline: Bool
    private let passResult: Bool
    /// `-uiDemoOverlap`: the sample's spot overlaps the meter's working space (#40).
    private let overlapResult: Bool
    /// `-uiDemoWallNotMeasured`: neither side of the meter was walked, so the result shows no
    /// spot (#76).
    private let wallNotMeasured: Bool
    /// `-uiDemoResultFile <path>` (debug builds only): a server answer in a JSON file, read
    /// through the engine's own mapping, in place of the hand-made samples.
    private let resultFile: String?
    private let rejectUpload: Bool
    /// `-uiDemoUnusableAnswer <n>`: how many answers in a row House Scan can't use; 0 when off.
    private let unusableAnswers: Int
    /// Which request the gap screen shows (`-uiDemoGap`); the phone's ground request by default.
    private let gapKind: String?
    /// The tilt-up step was answered or skipped.
    private var tiltUpSettled = false
    /// Walk-script ticks spent on the tilt-up step, standing in for the phone being tilted up.
    private var tiltUpTicks = 0
    private var script: Task<Void, Never>?
    private var nextCaptureID = 1
    /// How far the walk has seen to each side of the meter, meters.
    private var reachedLeft: Float = 0.3
    private var reachedRight: Float = 0.3
    /// Stretches the homeowner skipped, kept apart so a later skip doesn't undo an earlier one.
    private var skippedSpans: [ClosedRange<Float>] = []
    /// `-uiDemoEndPreview`: the homeowner walked back 1.5 m, so ending the wall now leaves part
    /// of the walk out.
    private var walkedBack: Float?
    /// Things standing in front of the wall, as depth would find them: a looked-at cell in the
    /// span is hidden, or skipped once the homeowner said they can't see past it.
    private var obstructions: [(span: ClosedRange<Float>, skipped: Bool)] = []
    /// Walk-script ticks spent on the see-behind step, standing in for stepping round the bush.
    private var seeBehindTicks = 0
    /// The check's first answer asked for a view and the scan went back to the camera for it,
    /// like the real engine does; the next answer goes to the result.
    private var followedUp = false
    private var followUpSkipped = false
    /// The spot check was answered; the next answer goes straight to the result, as the engine's
    /// does for a spot an earlier answer settles.
    private var spotChecked = false
    private var failedUploads = 0
    private var rejectedUploads = 0
    private var answersUnused = 0

    private static let cellWidth: Float = 0.1524
    /// Where the demo wall ends on each side, meters of s. Following a corner moves the end on.
    private var demoLeftEnd: Float = -2.9
    private var demoRightEnd: Float = 4.3
    /// How far the made-up wall goes on past a corner the walk follows, meters.
    private static let pastCorner: Float = 1.5

    init(arguments: [String]) {
        func value(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        freeze = arguments.contains("-uiDemoFreeze")
        noFeed = arguments.contains("-uiDemoNoFeed")
        closeUpSkipped = arguments.contains("-uiDemoCloseUpSkipped")
        offline = arguments.contains("-uiDemoOffline")
        passResult = arguments.contains("-uiDemoPass")
        overlapResult = arguments.contains("-uiDemoOverlap")
        wallNotMeasured = arguments.contains("-uiDemoWallNotMeasured")
        #if DEBUG
        resultFile = value("-uiDemoResultFile")
        #else
        resultFile = nil
        #endif
        rejectUpload = arguments.contains("-uiDemoRejected")
        unusableAnswers = arguments.contains("-uiDemoUnusableAnswer") ? max(value("-uiDemoUnusableAnswer").flatMap(Int.init) ?? 1, 1) : 0
        gapKind = value("-uiDemoGap")
        state.feed = DemoScene.image.map(CameraFeed.still) ?? .none
        state.isReplay = true
        state.tracking = .normal

        if let failure = value("-uiDemoFailure") {
            state.failure = switch failure {
            case "cameraDenied": .cameraDenied
            case "sessionFailed": .sessionFailed("The operation couldn't be completed. (demo error 102.)")
            case "replayUnreadable": .replayUnreadable("demo: frames.jsonl not found")
            default: .arUnsupported
            }
            state.phase = .unsupported
            return
        }
        // The ground question and its answer live on the feature review, so either argument opens it.
        let groundAnswer = value("-uiDemoGroundAnswer").flatMap(Self.groundAnswer)
        let opensReview = arguments.contains("-uiDemoGroundQuestion") || groundAnswer != nil
        let phase: ScanPhase = opensReview ? .markFeatures : value("-uiDemoPhase").flatMap(ScanPhase.init(rawValue:)) ?? .onboarding
        jump(to: phase)
        state.groundAnswer = groundAnswer
        if let raw = value("-uiDemoMarking"), let kind = FeatureKind(rawValue: raw) {
            state.marking = MarkingState(kind: kind, step: 0, refusal: arguments.contains("-uiDemoRefusal") ? .noSurface : nil)
        }
        if let raw = value("-uiDemoCoaching") {
            state.coaching = Self.coaching(raw)
            // The real engine reports these two only while the phone has lost its place, which
            // also hides the overlays drawn from its position.
            switch state.coaching {
            case .relocalizing: state.tracking = .limited(.relocalizing)
            case .trackingLost: state.tracking = .notAvailable
            default: break
            }
        }
        if arguments.contains("-uiDemoMarkEnd") {
            // The walk has reached the right end and asks whether it ends there. With
            // `-uiDemoEndMarkRefusal`, "Wall ends here" was just pressed with the circle off the wall.
            reachedRight = demoRightEnd
            refreshCoverage()
            refreshGuidance()
            if arguments.contains("-uiDemoEndMarkRefusal") {
                // As in the real engine, a circle off the wall has no end preview (`circleEnd`).
                state.endMarkRefusal = .noWall
                state.endPreview = nil
            }
        }
        if arguments.contains("-uiDemoEndPreview") {
            walkedBack = 1.5
            refreshGuidance()
        }
        if arguments.contains("-uiDemoEndQuestion") {
            if walkedBack != nil {
                // With `-uiDemoEndPreview`: "Wall ends here" pressed short of the farthest view,
                // so the question says what the end leaves out of the walk (#66).
                endWallHere()
            } else {
                state.endQuestion = .left
            }
        }
        if arguments.contains("-uiDemoEndScanQuestion") {
            // "Can't get there" again soon after it ended the right side (#82).
            state.endScanQuestion = true
            // `-uiDemoEndScanTooShort`: too little walked to finish, so it offers "Start over".
            state.endScanTooShort = arguments.contains("-uiDemoEndScanTooShort")
        }
        if arguments.contains("-uiDemoNextWall") {
            state.wall?.rightEnd = demoRightEnd
            reachedRight = demoRightEnd
            refreshCoverage()
            state.guidance = .markNextWall(side: .right, refusal: arguments.contains("-uiDemoRefusal") ? .notAtCorner : nil)
            state.target = nil
            state.path = []
            // With `-uiDemoNextWallConfirm`: the next wall marked, "Is this the next wall?" up (#70).
            if arguments.contains("-uiDemoNextWallConfirm") { markNextWall(at: nil, viewSize: .zero) }
        }
        if arguments.contains("-uiDemoOverheadQuestion"), state.phase == .gapRequest {
            // An overhead request whose tilted-up view just came in.
            script?.cancel()
            state.overheadQuestion = true
        } else if arguments.contains("-uiDemoTiltUp") || arguments.contains("-uiDemoOverheadQuestion") {
            enterTiltUp()
            state.overheadQuestion = arguments.contains("-uiDemoOverheadQuestion")
        }
        if arguments.contains("-uiDemoSample") {
            state.usesSampleResult = true
        }
        if arguments.contains("-uiDemoDepth") {
            state.depthAvailable = true
        }
        if arguments.contains("-uiDemoHidden") || arguments.contains("-uiDemoSeeBehind") {
            // A bush right of the meter and a bin left of it, on a phone with depth.
            state.depthAvailable = true
            reachedRight = max(reachedRight, 2.4)
            obstructions = [(1.2...1.8, false), (-0.55 ... -0.25, false)]
            refreshCoverage()
            refreshGuidance()
            if arguments.contains("-uiDemoSeeBehind") {
                state.guidance = .seeBehind(s: 1.5)
                state.target = DemoScene.wall.world(s: 1.5, height: 0.6)
                state.path = DemoScene.path(toward: 2.4, out: 1.8)
            }
        }
        if (arguments.contains("-uiDemoAim") || arguments.contains("-uiDemoAimOffScreen")), state.phase == .wallWalk {
            enterAim(offScreen: arguments.contains("-uiDemoAimOffScreen"))
        }
        if arguments.contains("-uiDemoCorner") {
            // The walk followed an outside corner right of the meter, between the battery spot and
            // the window, and went on 1.6 m along the next wall: that wall runs away from the
            // homeowner and faces right.
            let corner: Float = 1.8
            state.wall?.cornerSegments = [WallGeometry.Segment(
                span: corner...Float.infinity, along: SIMD3(0, 0, -1), outward: SIMD3(1, 0, 0),
                anchor: SIMD3(corner, 0, 0), anchorS: corner)]
            state.wall?.rightEnd = corner + 1.6
        }
        if let raw = value("-uiDemoSpotAnswered") {
            let answer = Self.spotAnswer(raw)
            switch state.phase {
            case .spotConfirm:
                state.spotCheck?.answer = answer
            case .result:
                // The result after that answer, which the result's notice reads. With a
                // `-uiDemoResultFile` answer that names no spot there is no check to keep, and
                // only the wall-wide notice after "I can't check this area" remains.
                // `-uiDemoUncheckedElsewhere` adds an earlier area left unchecked.
                state.spotCheck = sampleSpotCheck(answer: answer)
                state.uncheckedAreaElsewhere = (answer == .cannotCheck && state.spotCheck == nil) || arguments.contains("-uiDemoUncheckedElsewhere")
            default:
                break
            }
        }
        // The spot check without an outline: no kept photo shows the area (`-uiDemoSpotNoPhoto`),
        // or a photo but no wall to draw the area on it with (`-uiDemoSpotNoWall`).
        if arguments.contains("-uiDemoSpotNoPhoto"), state.phase == .spotConfirm {
            state.spotCheck?.photo = nil
        }
        if arguments.contains("-uiDemoSpotNoWall"), state.phase == .spotConfirm {
            state.wall = nil
        }
        if arguments.contains("-uiDemoFollowUp") {
            enterFollowUp(at: state.phase)
        }
        if arguments.contains("-uiDemoNoFeed") {
            state.feed = .none
        }
        if arguments.contains("-uiDemoCloseUpFailed") {
            state.closeUpFailedAttempts = 2
            state.closeUp = .aiming(hold: 0.2, problem: .tooBright)
        }
        if arguments.contains("-uiDemoMeterChoose") {
            state.closeUp = .captured(DemoScene.meterThumbnail)
            state.meterBrand = Self.demoBrand
            state.meterNumber = .choose(Self.demoCandidates)
        }
        // Frozen, the upload script never runs, so show where it would end.
        if freeze, state.phase == .uploading {
            if rejectUpload || offline || unusableAnswers > 0 {
                state.shareableScan = Self.demoScan
            }
            if rejectUpload {
                state.upload = .rejected(message: Self.rejection)
            } else if unusableAnswers > 0 {
                state.upload = .unusableAnswer(attempts: unusableAnswers)
            } else if offline {
                state.upload = .failed(message: "No internet connection.", offline: true)
            }
        }
    }

    // MARK: Phase set-up

    /// Puts the state where the real engine would be on arriving at `phase`.
    private func jump(to phase: ScanPhase) {
        switch phase {
        case .onboarding:
            state.phase = .onboarding
        case .findMeter:
            enterFindMeter()
        case .meterCloseUp:
            enterCloseUp()
        case .wallWalk:
            placeMeter()
            reachedLeft = 0.6
            reachedRight = 0.9
            enterWalk()
        case .markFeatures:
            placeMeter()
            finishedWalkState()
            state.phase = .markFeatures
        case .gapRequest:
            placeMeter()
            finishedWalkState()
            enterGap()
        case .uploading:
            placeMeter()
            finishedWalkState()
            enterUpload()
        case .spotConfirm:
            placeMeter()
            finishedWalkState()
            enterSpotCheck()
        case .result:
            placeMeter()
            finishedWalkState()
            showResult()
        case .resultAR:
            placeMeter()
            finishedWalkState()
            showResult()
            state.phase = .resultAR
        case .unsupported:
            state.failure = .arUnsupported
            state.phase = .unsupported
        }
    }

    private func enterFindMeter() {
        state.phase = .findMeter
        state.guidance = .findMeter
        state.feed = noFeed ? .none : DemoScene.meterImage.map(CameraFeed.still) ?? .none
        state.projection = DemoScene.meterProjection
        state.wall = nil
        state.coverage = .empty
    }

    private func placeMeter() {
        state.feed = noFeed ? .none : DemoScene.image.map(CameraFeed.still) ?? .none
        state.projection = DemoScene.projection
        state.wall = DemoScene.wall
        state.captureCount = max(state.captureCount, 1)
    }

    private func enterCloseUp() {
        placeMeter()
        state.feed = noFeed ? .none : DemoScene.closeUpImage.map(CameraFeed.still) ?? .none
        state.projection = DemoScene.closeUpProjection
        state.phase = .meterCloseUp
        state.guidance = .holdOnMeter
        state.closeUp = .aiming(hold: 0, problem: nil)
        state.meterNumber = nil
        run { engine in await engine.closeUpScript() }
    }

    private func enterWalk() {
        state.phase = .wallWalk
        state.closeUp = closeUpSkipped ? .skipped : .captured(DemoScene.meterThumbnail)
        state.captureCount = max(state.captureCount, 6)
        refreshCoverage()
        refreshGuidance()
        run { engine in await engine.walkScript() }
    }

    private func finishedWalkState() {
        // The walk came after the close-up, as in the real flow; `-uiDemoCloseUpSkipped` says
        // the homeowner skipped it.
        state.closeUp = closeUpSkipped ? .skipped : .captured(DemoScene.meterThumbnail)
        reachedLeft = -demoLeftEnd
        reachedRight = demoRightEnd
        state.wall?.leftEnd = demoLeftEnd
        state.wall?.rightEnd = demoRightEnd
        state.captureCount = max(state.captureCount, 42)
        if state.features.isEmpty {
            state.features = [Self.demoFeature(.gasMeter), Self.demoFeature(.window)]
        }
        refreshCoverage()
        state.guidance = .walkComplete
        state.path = []
        state.target = nil
    }

    /// Both ends are marked and answered: the walk asks to tilt up over the stretch by the meter.
    private func enterTiltUp() {
        placeMeter()
        finishedWalkState()
        state.phase = .wallWalk
        refreshGuidance()
        run { engine in await engine.walkScript() }
    }

    /// `-uiDemoAim`: the ground lags the wall right of the meter, so the walk asks to tilt down
    /// there, and the ring is half full: 2 of the 5 ground cells within 0.3 m of it are covered,
    /// and 80 % completes it. `-uiDemoAimOffScreen`: the same ask for ground left of the view, so
    /// the edge arrow stands in for the ring.
    private func enterAim(offScreen: Bool) {
        let s: Float = offScreen ? -2.4 : 0.4
        state.guidance = .aimAtGround(s: s)
        state.target = DemoScene.wall.world(s: s, height: 0, out: state.coverage.groundBandDepth / 2)
        state.path = DemoScene.path(toward: s)
        state.endPreview = nil
    }

    /// A gap request: the phone's own by default, or `serverItem` from the check's answer.
    private func enterGap(serverItem: MissingEvidence? = nil) {
        // Leave a hole in the ground right of the likely spot, as the phone's planner would find.
        state.phase = .gapRequest
        state.guidance = .gap
        let span: ClosedRange<Float> = 1.3...2.1
        setGround(span, to: .seen)
        state.gap = GapRequest(id: 1, origin: .phone, reason: .groundNearCandidate, band: .ground, span: span, progress: 0, isSatisfied: false)
        state.target = DemoScene.wall.world(s: 1.7, height: 0, out: 0.5)
        state.path = DemoScene.path(toward: 1.7)
        if let serverItem {
            // The demo's check asks for one view at most.
            state.followUps = 1
            state.gap?.origin = .server
            state.gap?.reason = .server(detail: serverItem.text)
            run { engine in await engine.gapScript(span: span) }
            return
        }
        // The server's requests, with the numbers its public rules would give (feet in the
        // README, meters here): ground out to D + r + e = 5.1 ft, a walk past D + r = 4.83 ft.
        switch gapKind {
        case "groundOut":
            state.gap?.origin = .server
            state.gap?.reason = .groundOut(out: 1.56)
            state.target = DemoScene.wall.world(s: 1.7, height: 0, out: 1.56)
        case "walkOut":
            let out: Float = 1.47
            // As the engine draws it: from the homeowner to the stretch's far end, the requested
            // distance plus the position error there (0.09 m + 0.16 m per meter from the meter)
            // plus 0.3 m out.
            let standOut = out + 0.09 + 0.16 * span.upperBound + 0.3
            state.gap?.origin = .server
            state.gap?.reason = .walkOut(out: out)
            state.target = DemoScene.wall.world(s: (span.lowerBound + span.upperBound) / 2, height: 1.2)
            state.path = [span.lowerBound - 0.8, span.upperBound].map {
                DemoScene.wall.world(s: $0, height: 0, out: standOut)
            }
        case "overhead":
            state.gap?.origin = .server
            state.gap?.reason = .overhead
            state.gap?.band = .wall
            state.target = DemoScene.wall.world(s: 1.7, height: 3.0)
            state.path = []
        default:
            break
        }
        if gapKind == "overhead" {
            run { engine in await engine.overheadGapScript() }
        } else {
            run { engine in await engine.gapScript(span: span) }
        }
    }

    private func enterUpload() {
        state.phase = .uploading
        state.gap = nil
        state.path = []
        state.target = nil
        state.upload = .packaging
        state.followUps = 0
        run { engine in await engine.uploadScript() }
    }

    /// The spot check of the answer's spot, before the result. The made-up zones don't hold the
    /// spot, so the area is the footprint alone, as the engine would draw it.
    private func enterSpotCheck() {
        state.shareableScan = Self.demoScan
        state.upload = .done
        state.result = sample
        guard let check = sampleSpotCheck(answer: nil) else { return showResult() }
        state.spotCheck = check
        state.phase = .spotConfirm
    }

    /// The check of the sample's spot, nil when the sample names none.
    private func sampleSpotCheck(answer: SpotCheckAnswer?) -> SpotCheck? {
        let result = sample
        guard let spot = result.spot else { return nil }
        let out = spot.offsetFromWall...(spot.offsetFromWall + spot.depth)
        return SpotCheck(
            id: 1, spot: spot.span, spotOut: out, spotHeight: spot.height, area: spot.span, areaDepth: out.upperBound,
            photo: DemoScene.image.map { SpotCheck.Photo(image: $0, projection: DemoScene.projection) },
            answer: answer, isSample: result.isSample)
    }

    /// `-uiDemoSpotAnswered`'s value. Anything else is a typo in a test's arguments.
    private static func spotAnswer(_ raw: String) -> SpotCheckAnswer {
        switch raw {
        case "clear": .clear
        case "somethingThere": .somethingThere
        case "cannotCheck": .cannotCheck
        default: preconditionFailure("-uiDemoSpotAnswered takes clear, somethingThere or cannotCheck, got \(raw)")
        }
    }

    private func showResult() {
        state.shareableScan = Self.demoScan
        state.upload = .done
        state.result = sample
        state.phase = .result
    }

    // MARK: Scripts

    /// Runs a timed script unless frozen; a new script replaces the old one.
    private func run(_ body: @escaping @MainActor (DemoEngine) async -> Void) {
        script?.cancel()
        guard !freeze else { return }
        script = Task { [weak self] in
            guard let self else { return }
            await body(self)
        }
    }

    private func pause(_ seconds: Double) async -> Bool {
        try? await Task.sleep(for: .seconds(seconds))
        return !Task.isCancelled
    }

    private func closeUpScript() async {
        guard await pause(0.6) else { return }
        for step in 1...6 {
            state.closeUp = .aiming(hold: Double(step) * 0.09, problem: nil)
            guard await pause(0.12) else { return }
        }
        state.closeUp = .aiming(hold: 0.54, problem: .blurry)
        guard await pause(1.4) else { return }
        for step in 6...11 {
            state.closeUp = .aiming(hold: Double(step) * 0.09, problem: nil)
            guard await pause(0.12) else { return }
        }
        capture(.closeUp, thumbnail: DemoScene.meterThumbnail)
        state.closeUp = .captured(DemoScene.meterThumbnail)
        state.meterNumber = .reading
        guard await pause(1.2) else { return }
        // Waits here for the homeowner's pick (`chooseMeterNumber`).
        state.meterBrand = Self.demoBrand
        state.meterNumber = .choose(Self.demoCandidates)
    }

    private func walkScript() async {
        while !Task.isCancelled {
            guard await pause(0.45) else { return }
            guard state.marking == nil else { continue }
            if case .tiltUp = state.guidance, !state.overheadQuestion {
                // About two seconds of tilting up, then the kept view raises the question.
                tiltUpTicks += 1
                if tiltUpTicks >= 4 {
                    capture(.walk)
                    state.overheadQuestion = true
                }
            }
            if case .seeBehind(let s) = state.guidance {
                // A few steps to the side, then depth sees the wall behind the bush.
                seeBehindTicks += 1
                if seeBehindTicks >= 5 {
                    seeBehindTicks = 0
                    obstructions.removeAll { $0.span.contains(s) }
                    capture(.walk)
                    refreshCoverage()
                    refreshGuidance()
                }
            }
            if case .walk(let side, _) = state.guidance {
                if side == .right {
                    reachedRight = min(reachedRight + 0.3, demoRightEnd)
                } else {
                    reachedLeft = min(reachedLeft + 0.3, -demoLeftEnd)
                }
                capture(.walk)
                refreshCoverage()
                refreshGuidance()
            }
            if case .aimAtGround(let s) = state.guidance {
                // A step to the side at a time: one more ground cell by the ring is covered each
                // tick, from its middle out. Like the real engine, the walk goes on in the same
                // update that fills the ring, so the demo shows the hold a phone shows (#81).
                capture(.walk)
                coverGroundCell(nearest: s)
                if (state.aimProgress ?? 0) >= 1 {
                    refreshGuidance()
                }
            }
        }
    }

    private func gapScript(span: ClosedRange<Float>) async {
        guard await pause(1.2) else { return }
        let cells = Int(((span.upperBound - span.lowerBound) / Self.cellWidth).rounded(.up))
        for step in 1...cells {
            let covered = span.lowerBound...(span.lowerBound + Float(step) * Self.cellWidth)
            setGround(covered, to: .covered)
            state.gap?.progress = Double(step) / Double(cells)
            capture(.gap)
            guard await pause(0.45) else { return }
        }
        state.gap?.isSatisfied = true
        guard await pause(1.6) else { return }
        enterUpload()
    }

    /// About two seconds of tilting up, then the view covers the request and the question comes up.
    private func overheadGapScript() async {
        guard await pause(2.4) else { return }
        capture(.gap)
        state.overheadQuestion = true
    }

    private func uploadScript() async {
        state.upload = .packaging
        guard await pause(1.0) else { return }
        state.shareableScan = Self.demoScan
        for step in 0...10 {
            state.upload = .uploading(fraction: Double(step) / 10)
            guard await pause(0.22) else { return }
            if offline, failedUploads == 0, step == 4 {
                failedUploads += 1
                state.upload = .failed(message: "No internet connection.", offline: true)
                return
            }
        }
        state.upload = .analyzing
        guard await pause(1.8) else { return }
        // Refused once; after the review the same scan goes through.
        if rejectUpload, rejectedUploads == 0 {
            rejectedUploads += 1
            state.upload = .rejected(message: Self.rejection)
            return
        }
        // An answer the engine couldn't use; "Try again" asks again.
        if answersUnused < unusableAnswers {
            answersUnused += 1
            state.upload = .unusableAnswer(attempts: answersUnused)
            return
        }
        // Like the real engine: an answer that lists a view the camera can take goes back to
        // the camera for it, after the upload screen has said so; the next answer is the result.
        if !followedUp, let item = sample.missing.first(where: \.capturable) {
            state.shareableScan = Self.demoScan
            state.result = sample
            state.followUps = 1
            state.upload = .done
            followedUp = true
            guard await pause(1.6) else { return }
            enterGap(serverItem: item)
            return
        }
        // Like the real engine's `resultHold`: every step ticked before the result (#31).
        state.upload = .done
        guard await pause(0.8) else { return }
        if spotChecked { showResult() } else { enterSpotCheck() }
    }

    /// The check's answer, before or after its follow-up view.
    private var sample: ResultPresentation {
        if wallNotMeasured { return Self.reviewSample.withWallNotMeasured() }
        if passResult { return Self.passSample }
        if overlapResult { return Self.overlapSample }
        if let fileResult { return fileResult }
        guard followedUp else { return Self.reviewSample }
        return followUpSkipped ? Self.reviewSample.withFollowUpSkipped : Self.reviewSample.withFollowUpTaken
    }

    /// The answer in `-uiDemoResultFile`, mapped as `ScanEngine` maps a server's. A file that
    /// doesn't decode stops the demo with the decoding error rather than showing a sample.
    private var fileResult: ResultPresentation? {
        guard let resultFile else { return nil }
        let result: PlacementResult
        do {
            result = try PlacementResult.decode(Data(contentsOf: URL(fileURLWithPath: resultFile)))
        } catch {
            fatalError("-uiDemoResultFile \(resultFile): \(error)")
        }
        let planner = GapPlanner()
        let wall = state.wall
        return ScanEngine.presentation(of: result, isSample: true, wall: nil) { item in
            planner.plan(for: item, leftEnd: wall?.leftEnd, rightEnd: wall?.rightEnd) != nil
        }
    }

    /// `-uiDemoFollowUp`: the check has answered and asked for a view. On the upload screen,
    /// the moment before the camera opens; on the gap screen, the view itself.
    private func enterFollowUp(at phase: ScanPhase) {
        guard phase == .uploading || phase == .gapRequest, let item = Self.reviewSample.missing.first(where: \.capturable) else { return }
        script?.cancel()
        state.shareableScan = Self.demoScan
        state.result = Self.reviewSample
        state.followUps = 1
        state.upload = .done
        followedUp = true
        if phase == .gapRequest {
            enterGap(serverItem: item)
        } else {
            run { engine in
                guard await engine.pause(1.6) else { return }
                engine.enterGap(serverItem: item)
            }
        }
    }

    // MARK: Coverage

    private func refreshCoverage() {
        let wallRange = min(DemoScene.wallRange.lowerBound, demoLeftEnd - 0.3)...max(DemoScene.wallRange.upperBound, demoRightEnd + 0.1)
        let count = Int(((wallRange.upperBound - wallRange.lowerBound) / Self.cellWidth).rounded(.up))
        var wallCells: [CellState] = []
        var groundCells: [CellState] = []
        for index in 0..<count {
            let center = wallRange.lowerBound + (Float(index) + 0.5) * Self.cellWidth
            wallCells.append(Self.state(at: center, left: reachedLeft, right: reachedRight, lag: 0))
            groundCells.append(Self.state(at: center, left: reachedLeft, right: reachedRight, lag: 0.35))
        }
        for skippedSpan in skippedSpans {
            for index in 0..<count {
                let center = wallRange.lowerBound + (Float(index) + 0.5) * Self.cellWidth
                if skippedSpan.contains(center), wallCells[index] != .covered { wallCells[index] = .skipped }
                if skippedSpan.contains(center), groundCells[index] != .covered { groundCells[index] = .skipped }
            }
        }
        for obstruction in obstructions {
            for index in 0..<count {
                let center = wallRange.lowerBound + (Float(index) + 0.5) * Self.cellWidth
                guard obstruction.span.contains(center) else { continue }
                // Hidden needs a look: a cell the camera never pointed at stays unseen.
                let cell: CellState = obstruction.skipped ? .skipped : .hidden
                if wallCells[index] != .unseen { wallCells[index] = cell }
                if groundCells[index] != .unseen { groundCells[index] = cell }
            }
        }
        let fogAhead: Float = 1.2
        var lower = max(-reachedLeft - fogAhead, wallRange.lowerBound)
        var upper = min(reachedRight + fogAhead, wallRange.upperBound)
        if let left = state.wall?.leftEnd { lower = max(lower, left) }
        if let right = state.wall?.rightEnd { upper = min(upper, right) }
        state.coverage = CoverageStrip(
            cellWidth: Self.cellWidth,
            firstCellS: wallRange.lowerBound,
            wall: wallCells,
            ground: groundCells,
            wallBandHeight: 2.4,
            groundBandDepth: 1.2,
            visibleRange: lower...upper,
            revision: state.coverage.revision + 1
        )
    }

    private static func state(at s: Float, left: Float, right: Float, lag: Float) -> CellState {
        let reach = s < 0 ? left - lag : right - lag
        let distance = abs(s)
        if distance <= reach - 0.3 { return .covered }
        if distance <= reach + 0.3 { return .seen }
        return .unseen
    }

    private func setGround(_ span: ClosedRange<Float>, to cell: CellState) {
        var coverage = state.coverage
        for index in coverage.ground.indices where span.contains(coverage.cellRange(index).lowerBound + Self.cellWidth / 2) {
            coverage.ground[index] = cell
        }
        coverage.revision += 1
        state.coverage = coverage
    }

    /// Covers the ground cell nearest `s` that isn't covered yet.
    private func coverGroundCell(nearest s: Float) {
        let strip = state.coverage
        let center = { (index: Int) -> Float in
            let range = strip.cellRange(index)
            return (range.lowerBound + range.upperBound) / 2
        }
        let open = strip.ground.indices.filter { strip.ground[$0] != .covered }
        guard let index = open.min(by: { abs(center($0) - s) < abs(center($1) - s) }) else { return }
        var coverage = strip
        coverage.ground[index] = .covered
        coverage.revision += 1
        state.coverage = coverage
    }

    private func refreshGuidance() {
        guard let wall = state.wall else { return }
        defer { refreshEndPreview() }
        if wall.rightEnd == nil {
            if reachedRight >= demoRightEnd - 0.01 {
                state.guidance = .markEnd(side: .right)
                state.target = wall.world(s: demoRightEnd, height: 1.0)
                state.path = []
            } else {
                state.guidance = .walk(side: .right, remaining: demoRightEnd - reachedRight)
                state.target = wall.world(s: min(reachedRight + 0.9, demoRightEnd), height: 0.2, out: 0.3)
                state.path = DemoScene.path(toward: min(reachedRight + 1.2, demoRightEnd))
            }
        } else if wall.leftEnd == nil {
            if reachedLeft >= -demoLeftEnd - 0.01 {
                state.guidance = .markEnd(side: .left)
                state.target = wall.world(s: demoLeftEnd, height: 1.0)
                state.path = []
            } else {
                state.guidance = .walk(side: .left, remaining: -demoLeftEnd - reachedLeft)
                state.target = wall.world(s: max(-reachedLeft - 0.9, demoLeftEnd), height: 0.2, out: 0.3)
                state.path = DemoScene.path(toward: max(-reachedLeft - 1.2, demoLeftEnd))
            }
        } else if !tiltUpSettled {
            // Like the real engine: 1.5 m each side of the meter, inside the marked ends.
            let span = max(wall.leftEnd ?? -1.5, -1.5)...min(wall.rightEnd ?? 1.5, 1.5)
            state.guidance = .tiltUp(span: span)
            state.target = wall.world(s: (span.lowerBound + span.upperBound) / 2, height: 3.0)
            state.path = []
        } else {
            state.guidance = .walkComplete
            state.target = nil
            state.path = []
        }
    }

    /// Like the real engine: during the walk the end goes where the walk has reached on that
    /// side; while the walk asks for the end, at the demo wall's end, which the reticle is on.
    private func refreshEndPreview() {
        state.endPreview = switch state.guidance {
        case .walk(let side, _):
            EndPreview(side: side, s: side == .right ? reachedRight : -reachedLeft, atReticle: false, leavesOutWalked: walkedBack)
        case .markEnd(let side):
            EndPreview(side: side, s: side == .right ? demoRightEnd : demoLeftEnd, atReticle: true, leavesOutWalked: nil)
        default:
            nil
        }
    }

    private func capture(_ kind: CaptureEvent.Kind, thumbnail: CGImage? = nil) {
        state.captureCount += 1
        state.lastCapture = CaptureEvent(id: nextCaptureID, kind: kind, thumbnail: thumbnail)
        nextCaptureID += 1
    }

    // MARK: ScanActions

    func finishOnboarding() {
        enterFindMeter()
    }

    func markMeter(at point: CGPoint?, viewSize: CGSize) {
        // Taps near the top of the screen land on the eave, not the wall: ask to step closer,
        // the way the real engine refuses a tap with no vertical surface under it.
        if let point, point.y < viewSize.height * 0.18 {
            state.guidance = .aimAtWallForMeter
            return
        }
        capture(.closeUp)
        enterCloseUp()
    }

    func skipCloseUp() {
        state.closeUp = .skipped
        state.meterNumber = .skipped
        enterWalk()
    }

    func rejectMeterBrand() {
        guard case .choose = state.meterNumber else { return }
        state.meterBrand = nil
    }

    func chooseMeterNumber(_ candidate: MeterNumberCandidate?) {
        guard case .choose = state.meterNumber else { return }
        guard let candidate else {
            // "None of these": another photo, with the advice the reader gives for small text.
            state.meterNumber = nil
            state.closeUpFailedAttempts += 1
            state.closeUp = .aiming(hold: 0, problem: .numberTooSmall)
            run { engine in await engine.closeUpScript() }
            return
        }
        state.meterNumber = .confirmed(candidate.text)
        run { engine in
            guard await engine.pause(1.0) else { return }
            engine.reachedLeft = 0.3
            engine.reachedRight = 0.3
            engine.enterWalk()
        }
    }

    func markWallEnd(at point: CGPoint?, viewSize: CGSize) {
        guard case .markEnd(let side) = state.guidance else { return }
        state.endMarkRefusal = nil
        if side == .right {
            state.wall?.rightEnd = demoRightEnd
        } else {
            state.wall?.leftEnd = demoLeftEnd
        }
        state.endQuestion = side
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
        refreshCoverage()
        refreshGuidance()
    }

    func endWallHere() {
        guard case .walk(let side, _) = state.guidance, let preview = state.endPreview, !preview.atReticle else { return }
        if side == .right {
            demoRightEnd = preview.s
            state.wall?.rightEnd = preview.s
        } else {
            demoLeftEnd = preview.s
            state.wall?.leftEnd = preview.s
        }
        state.endQuestion = side
        state.endQuestionLeavesOut = preview.leavesOutWalked
        state.endQuestionLeavesOutSeen = preview.leavesOutSeen
        refreshCoverage()
        refreshGuidance()
    }

    /// Like the real engine: during the walk a corner asks for the next wall, once per side here.
    func answerWallEnd(turnsCorner: Bool) {
        guard let side = state.endQuestion else { return }
        state.endQuestion = nil
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
        let followed = state.wall?.cornerSegments.contains { side == .right ? $0.span.lowerBound > 0 : $0.span.upperBound < 0 } ?? true
        if turnsCorner, !followed {
            state.guidance = .markNextWall(side: side, refusal: nil)
            state.target = nil
            state.path = []
            return
        }
        refreshGuidance()
    }

    /// Like the real engine: a marked next wall waits for "Is this the next wall?" (#70). The
    /// made-up corner is where the end was marked.
    func markNextWall(at point: CGPoint?, viewSize: CGSize) {
        guard case .markNextWall(let side, _) = state.guidance, state.nextWallConfirm == nil else { return }
        let end = side == .right ? demoRightEnd : demoLeftEnd
        state.nextWallConfirm = NextWallConfirm(side: side, fromEnd: 0.4)
        // On the corner, where the next wall meets the demo wall's line.
        state.target = SIMD3(end, 1, 0)
    }

    func confirmNextWall(_ isNextWall: Bool) {
        guard let confirm = state.nextWallConfirm else { return }
        state.nextWallConfirm = nil
        state.target = nil
        if isNextWall { followCorner(confirm.side) }
    }

    /// Like the real engine: back to the question about that end.
    func cancelNextWall() {
        guard case .markNextWall(let side, _) = state.guidance else { return }
        state.nextWallConfirm = nil
        state.target = nil
        state.endQuestion = side
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
    }

    /// The made-up wall turns toward the homeowner at the marked end and goes on `pastCorner`.
    private func followCorner(_ side: WallSide) {
        guard var wall = state.wall else { return }
        let end = side == .right ? demoRightEnd : demoLeftEnd
        // Facing back across the demo wall's front, running toward the camera (+z).
        let outward = SIMD3<Float>(side == .right ? -1 : 1, 0, 0)
        wall.cornerSegments.append(WallGeometry.Segment(
            span: side == .right ? end...Float.infinity : -Float.infinity...end,
            along: side == .right ? SIMD3(0, 0, 1) : SIMD3(0, 0, -1), outward: outward,
            anchor: SIMD3(end, 0, 0), anchorS: end))
        if side == .right {
            wall.rightEnd = nil
            demoRightEnd += Self.pastCorner
        } else {
            wall.leftEnd = nil
            demoLeftEnd -= Self.pastCorner
        }
        state.wall = wall
        refreshCoverage()
        refreshGuidance()
    }

    /// Like the real engine: marking from the review stays in `.markFeatures`, and the review
    /// screen shows the marking view while `marking` is set.
    func beginMarking(_ kind: FeatureKind) {
        guard state.phase == .wallWalk || state.phase == .markFeatures else { return }
        state.marking = MarkingState(kind: kind, step: 0, refusal: nil)
    }

    func markFeaturePoint(at point: CGPoint?, viewSize: CGSize) {
        guard var marking = state.marking else { return }
        if marking.step + 1 < marking.kind.tapCount {
            marking.step += 1
            marking.refusal = nil
            state.marking = marking
            return
        }
        state.features.append(Self.demoFeature(marking.kind))
        state.marking = nil
    }

    func cancelMarking() {
        state.marking = nil
    }

    func deleteFeature(_ id: UUID) {
        state.features.removeAll { $0.id == id }
    }

    func setWindowOpens(_ id: UUID, opens: Bool?) {
        guard let index = state.features.firstIndex(where: { $0.id == id }) else { return }
        state.features[index].opens = opens
        state.features[index].opensNotSure = opens == nil
    }

    func finishWalk() {
        guard state.wall?.leftEnd != nil, state.wall?.rightEnd != nil else { return }
        state.overheadQuestion = false
        tiltUpSettled = true
        script?.cancel()
        state.phase = .markFeatures
    }

    /// The demo never asks on its own (its "Can't get there" ends the side at once); the question
    /// shows only with `-uiDemoEndScanQuestion`. "Yes, end here" finishes the walk with the demo's
    /// ends.
    func answerEndScan(_ end: Bool) {
        guard state.endScanQuestion else { return }
        state.endScanQuestion = false
        state.endScanTooShort = false
        guard end else { return }
        finishedWalkState()
        finishWalk()
    }

    func confirmFeatures() {
        enterGap()
    }

    func skipGap() {
        if let gap = state.gap { setGround(gap.span, to: .skipped) }
        if state.gap?.origin == .server, followedUp { followUpSkipped = true }
        enterUpload()
    }

    /// The demo's check asks for one view at most, so stopping early is skipping it.
    func showResultNow() {
        skipGap()
    }

    func cannotAccessArea() {
        if case .seeBehind(let s) = state.guidance {
            obstructions = obstructions.map { (span: $0.span, skipped: $0.skipped || $0.span.contains(s)) }
            refreshCoverage()
            refreshGuidance()
            return
        }
        if case .markNextWall = state.guidance {
            refreshGuidance()
            return
        }
        if case .tiltUp = state.guidance {
            tiltUpSettled = true
            refreshGuidance()
            return
        }
        switch state.guidance {
        case .aimAtGround(let s), .aimAtWall(let s):
            // Like the real engine: that stretch goes to review and the walk moves on.
            skippedSpans.append((s - 0.5)...(s + 0.5))
            refreshCoverage()
            refreshGuidance()
            return
        default:
            break
        }
        if case .markEnd(let side) = state.guidance {
            // "The wall keeps going": like the real engine, the end goes where the walk reached,
            // unexplored, and nothing asks what is there.
            if side == .right { state.wall?.rightEnd = reachedRight } else { state.wall?.leftEnd = -reachedLeft }
            state.endMarkRefusal = nil
            refreshCoverage()
            refreshGuidance()
            return
        }
        guard case .walk(let side, _) = state.guidance else { return }
        if side == .right {
            skippedSpans.append((reachedRight + 0.1)...demoRightEnd)
            reachedRight = demoRightEnd
        } else {
            skippedSpans.append(demoLeftEnd...(-reachedLeft - 0.1))
            reachedLeft = -demoLeftEnd
        }
        refreshCoverage()
        refreshGuidance()
    }

    func retryUpload() {
        enterUpload()
    }

    func backToReview() {
        script?.cancel()
        state.upload = .idle
        state.phase = .markFeatures
    }

    /// Like the real engine: the answer stays while its request is captured.
    func captureMissing(_ id: String) {
        guard let item = state.result?.missing.first(where: { $0.id == id && $0.capturable }) else { return }
        followedUp = true
        enterGap(serverItem: item)
    }

    func showAR() {
        guard state.result?.wallNotMeasured != true else { return }
        state.phase = .resultAR
    }

    func closeAR() {
        state.phase = .result
    }

    /// The demo has no camera, so there is no access to recheck: its failure screen stays.
    func recheckCameraAccess() {}

    func startOver() {
        script?.cancel()
        state.phase = .onboarding
        state.features = []
        state.wall = nil
        state.coverage = .empty
        state.result = nil
        state.gap = nil
        state.captureCount = 0
        state.lastCapture = nil
        state.closeUpFailedAttempts = 0
        state.meterNumber = nil
        state.upload = .idle
        state.shareableScan = nil
        state.marking = nil
        reachedLeft = 0.3
        reachedRight = 0.3
        demoLeftEnd = -2.9
        demoRightEnd = 4.3
        skippedSpans = []
        obstructions = []
        seeBehindTicks = 0
        followedUp = false
        followUpSkipped = false
        spotChecked = false
        state.spotCheck = nil
        state.uncheckedAreaElsewhere = false
        tiltUpSettled = false
        tiltUpTicks = 0
        state.overheadQuestion = false
        state.endScanQuestion = false
        state.endScanTooShort = false
        state.groundAnswer = nil
    }

    func liveCameraView() -> AnyView {
        AnyView(Color.black)
    }

    // MARK: Sample data

    /// The made-up meter's maker, as `MeterBrand.read` would name it.
    static let demoBrand = "Itron"

    /// Made-up readings of a made-up meter: the barcode-confirmed one first, then two near
    /// misses the way a reader confuses 8 with 6 and 3 with 8.
    static let demoCandidates = [
        MeterNumberCandidate(id: 0, text: "80417362", barcodeConfirmed: true),
        MeterNumberCandidate(id: 1, text: "60417362", barcodeConfirmed: false),
        MeterNumberCandidate(id: 2, text: "80417862", barcodeConfirmed: false),
    ]

    /// Stands in for the scan bundle so "Share scan" has a real file to hand the share sheet: a
    /// few made-up lines in tmp, no photos or measurements. Nil if tmp can't be written, which
    /// hides the button.
    static let demoScan: URL? = {
        let url = URL.temporaryDirectory.appending(path: "HouseScan-demo-scan.txt")
        let text = "A made-up scan from the House Scan UI demo (-uiDemo). It holds no photos or measurements.\n"
        do {
            try Data(text.utf8).write(to: url, options: .atomic)
            return url
        } catch {
            return nil
        }
    }()

    /// A refusal in the homeowner's words, as the engine sends it.
    static let rejection = "Some of the wall's measurements were missing. Check what you marked, then send it again."

    /// `-uiDemoGroundAnswer`: a `GroundType` raw value, or `notSure`.
    private static func groundAnswer(_ raw: String) -> GroundAnswer? {
        raw == "notSure" ? .notSure : GroundType(rawValue: raw).map(GroundAnswer.type)
    }

    private static func coaching(_ raw: String) -> Coaching? {
        switch raw {
        case "initializing": .initializing
        case "slowDown": .slowDown
        case "needsTexture": .needsTexture
        case "tooDark": .tooDark
        case "tooDarkToMeasure": .tooDarkToMeasure
        case "holdSteady": .holdSteady
        case "turnSlowly": .turnSlowly
        case "relocalizing": .relocalizing
        case "trackingLost": .trackingLost
        case "pastWallEnd": .pastWallEnd
        default: nil
        }
    }

    static func demoFeature(_ kind: FeatureKind) -> MarkedFeature {
        let wall = DemoScene.wall
        switch kind {
        case .gasMeter:
            return MarkedFeature(id: UUID(), kind: kind, span: DemoScene.gasMeterSpan, bottom: 0.35, top: 0.72, out: 0.15,
                                 points: [wall.world(s: -1.4, height: 0.55, out: 0.15)], opens: nil)
        case .window:
            return MarkedFeature(id: UUID(), kind: kind, span: DemoScene.windowSpan, bottom: DemoScene.windowHeights.lowerBound,
                                 top: DemoScene.windowHeights.upperBound, out: nil,
                                 points: [wall.world(s: 2.2, height: 0.95), wall.world(s: 3.0, height: 2.15)], opens: nil)
        case .door:
            return MarkedFeature(id: UUID(), kind: kind, span: -2.6 ... -1.8, bottom: 0, top: 2.03, out: nil,
                                 points: [wall.world(s: -2.6, height: 0), wall.world(s: -1.8, height: 2.03)], opens: nil)
        case .acUnit:
            return MarkedFeature(id: UUID(), kind: kind, span: DemoScene.acSpan, bottom: 0, top: 0.8, out: 0.25,
                                 points: [wall.world(s: 3.75, height: 0.8, out: 0.6)], opens: nil)
        case .driveway:
            return MarkedFeature(id: UUID(), kind: kind, span: -1.0...1.0, bottom: nil, top: nil, out: 2.4,
                                 points: [wall.world(s: -1.0, height: 0, out: 2.4), wall.world(s: 1.0, height: 0, out: 2.4)], opens: nil)
        case .fence:
            return MarkedFeature(id: UUID(), kind: kind, span: -2.0...2.0, bottom: 0, top: 1.2, out: 3.0,
                                 points: [wall.world(s: -2.0, height: 0, out: 3.0), wall.world(s: 2.0, height: 0, out: 3.0)], opens: nil)
        }
    }

    /// The offline sample result. Placeholder rules, so the decision is manual review.
    static let reviewSample = ResultPresentation(
        decision: .manualReview,
        summary: "The best spot, 3 ft right of your meter, is close to a window, so it needs an installer's review.",
        policyApproved: false,
        spot: BatterySpot(span: 0.55...1.34, depth: 0.56, height: 1.1, offsetFromWall: 0.03),
        cableRoute: [SIMD2(0.16, 1.45), SIMD2(0.95, 1.45), SIMD2(0.95, 1.1)],
        cableLength: 1.14,
        checks: [
            CheckRow(id: "wall", title: "Wall behind the spot", outcome: .pass,
                     reason: "Flat, solid wall behind the whole spot."),
            CheckRow(id: "gas", title: "Distance from the gas meter", outcome: .pass,
                     reason: "The gas meter is well to the left of the spot."),
            CheckRow(id: "window", title: "Distance from the window", outcome: .unsure,
                     reason: "The window is close to the spot's right edge.",
                     needsPerson: true, measured: 0.86, threshold: 0.91, plusMinus: 0.1, comparison: .atLeast),
            CheckRow(id: "ground", title: "Ground under the spot", outcome: .unsure,
                     reason: "Part of the ground was only seen from one place.", needsPerson: false, settledBy: "ground-right"),
            CheckRow(id: "ac", title: "Distance from the AC unit", outcome: .pass,
                     reason: "The AC unit is far enough to the right."),
        ],
        clearances: [
            ClearanceZone(id: "meter", label: "In front of the meter", outcome: .pass, span: -0.38...0.38, depth: 0.9),
            ClearanceZone(id: "window", label: "Window", outcome: .unsure, span: 1.3...2.2, depth: 0.9),
        ],
        missing: [
            MissingEvidence(id: "ground-right", text: "A second look at the ground just right of the spot.", capturable: true,
                            checkIDs: ["ground"]),
            MissingEvidence(id: "window-opens", text: "Whether the window next to the spot opens.", capturable: false,
                            checkIDs: ["window"]),
        ],
        // The walk stopped short on the left, nearer the meter than the spot (#83). Fixed copy
        // for the result screen, not worked out from the demo walk: that walk marks its left end
        // at -2.9 m, and the gas meter above is "well to the left".
        unseenEnd: UnseenEnd(side: .left, s: -0.4),
        isSample: true
    )

    /// A made-up working-space line: the spot overlaps the meter's working space by 1 ft
    /// (measured_ft -1.0), within the measurement's 1 ft 6 in error (#40).
    static let overlapSample: ResultPresentation = {
        var sample = reviewSample
        sample.checks.insert(
            CheckRow(id: "meter_working_space", title: "Clear of the meter's working space", outcome: .unsure,
                     reason: "The battery is within measurement error of the meter's 2 ft 6 in wide by 3 ft 0 in deep working space.",
                     needsPerson: true, measured: -0.3048, threshold: 0, plusMinus: 0.4572, comparison: .atLeast),
            at: 0
        )
        return sample
    }()

    static let passSample: ResultPresentation = {
        var sample = reviewSample
        sample.decision = .pass
        sample.policyApproved = true
        sample.summary = "The spot fits every check we could measure."
        sample.unseenEnd = nil
        sample.checks = sample.checks.map { row in
            var row = row
            row.outcome = .pass
            return row
        }
        sample.missing = []
        sample.clearances = sample.clearances.map { zone in
            var zone = zone
            zone.outcome = .pass
            return zone
        }
        return sample
    }()
}

private extension ResultPresentation {
    /// The ground right of the spot was seen again: its check settles and nothing is left to take.
    var withFollowUpTaken: ResultPresentation {
        var result = self
        result.checks = checks.map { row in
            guard row.id == "ground" else { return row }
            var row = row
            row.outcome = .pass
            row.reason = "Seen from two places."
            return row
        }
        result.missing = missing.filter { !$0.capturable }
        return result
    }

    /// "I can't get there": the view stays on the list for the installer, not as a capture.
    var withFollowUpSkipped: ResultPresentation {
        var result = self
        result.missing = missing.map { item in
            var item = item
            item.capturable = false
            return item
        }
        return result
    }
}

extension DemoEngine {
    /// Like the engine: the answer stays up a moment, then the result, or the check again.
    func answerSpotCheck(_ answer: SpotCheckAnswer) {
        guard state.phase == .spotConfirm, state.spotCheck?.answer == nil else { return }
        state.spotCheck?.answer = answer
        spotChecked = true
        run { engine in
            guard await engine.pause(1.2) else { return }
            if answer == .clear { engine.showResult() } else { engine.enterUpload() }
        }
    }

    /// Nothing to record in the demo: either answer ends the step, as in the real engine.
    func answerGround(_ answer: GroundAnswer) {
        guard state.phase == .markFeatures else { return }
        state.groundAnswer = answer
    }

    func answerOverhead(clear: Bool) {
        guard state.overheadQuestion else { return }
        state.overheadQuestion = false
        if state.phase == .gapRequest {
            // "Nothing overhead" closes the request; anything overhead leaves it to the installer.
            guard clear else { return enterUpload() }
            state.gap?.progress = 1
            state.gap?.isSatisfied = true
            run { engine in
                guard await engine.pause(1.6) else { return }
                engine.enterUpload()
            }
            return
        }
        tiltUpSettled = true
        refreshGuidance()
    }
}
