import CoreGraphics
import Foundation
import HouseScanKit
import OSLog
import simd
import SwiftUI

extension ScanEngine: ScanActions {
    func finishOnboarding() {
        guard state.phase == .onboarding else { return }
        startPracticeIfOn()
        beginScan()
        // A photo-processing scan this build can't send stops here, before the camera or any
        // request, and says so with a way to a Legacy scan.
        guard !photoScanCantRun else {
            go(.processing)
            return
        }
        leaveOnboarding()
    }

    func markMeter(at point: CGPoint?, viewSize: CGSize) {
        guard state.phase == .findMeter else { return }
        if let replay {
            // A replay has no live surfaces to raycast; its wall comes from the recording (or is
            // assumed from the trajectory, see ReplayPlayer.wallDescription).
            let wall = replay.wall
            meterTapCamera = nil
            guard setWall(meter: wall.meter, outward: wall.outward, groundY: wall.groundY, groundMeasured: replay.groundMeasured) else { return }
            markTimes[MarkKey.meter] = captureClock
            go(.meterCloseUp)
            return
        }
        guard let live = liveCapture, let frame = currentFrame else { return }
        guard frame.tracking == .normal else {
            state.guidance = .aimAtWallForMeter
            return
        }
        let viewPoint = point ?? CGPoint(x: viewSize.width / 2, y: viewSize.height / 2)
        // A detected vertical plane's own geometry first; failing that, ARKit's estimated vertical
        // plane, whose wider error the export records. Never an infinite plane, which extends a
        // fence or another wall past its edges.
        guard let hit = live.raycastVerticalPlane(from: viewPoint) else {
            state.guidance = .aimAtWallForMeter
            RuntimeLog.engine.info("meter tap refused: no vertical plane")
            return
        }
        // An estimated plane can put the wall far from the real one (#69): one out of reach,
        // turned from the phone, or behind a detected plane is refused, and the homeowner is asked
        // to let ARKit find the wall first.
        if let refusal = MeterTap.refusal(
            hit: hit.position, normal: hit.normal, source: hit.source, camera: frame.camera, planes: detectedWallPlanes
        ) {
            state.guidance = .aimAtWallForMeter
            RuntimeLog.engine.info("meter tap refused: \(refusal.description, privacy: .public)")
            return
        }
        meterPlaneSource = hit.source
        meterTapCamera = frame.camera.position
        var outward = SIMD3(hit.normal.x, 0, hit.normal.z)
        if simd_dot(outward, frame.camera.position - hit.position) < 0 { outward = -outward }
        // Until a horizontal plane shows up below the wall, the ground is a guess: a phone held at
        // chest height, 1.4 m above it. `refineGround` replaces the guess as planes arrive.
        let ground = groundBelow(hit.position, along: simd_normalize(simd_cross(-outward, SIMD3(0, 1, 0))), current: nil)
        guard setWall(meter: hit.position, outward: outward, groundY: ground?.plane.y ?? frame.camera.position.y - 1.4, groundMeasured: ground != nil) else {
            state.guidance = .aimAtWallForMeter
            return
        }
        let groundNote = ground.map(Self.describe) ?? "a guess"
        RuntimeLog.engine.info("meter marked on \(hit.source == .estimatedPlane ? "an estimated" : "a detected", privacy: .public) plane; ground from \(groundNote, privacy: .public)")
        // The map carries the line's source: the export writes it and the walked clearance
        // takes the server's error for it.
        updateCoverage { $0.setWallLineSource(meterLineSource) }
        setMeterAnchor(live.addMeterAnchor(at: hit.transform), pose: hit.transform)
        markTimes[MarkKey.meter] = captureClock
        go(.meterCloseUp)
    }

    /// The ground at the wall of the meter at `meter`, running along `along`: a detected plane
    /// the meter is a plausible height above, that reaches the wall's foot by the meter and isn't
    /// furniture, floor-classified first, then the one under where the phone stood to mark the
    /// meter, then the lowest (`GroundPlaneChoice`). With `current`, the ground already measured,
    /// it is never raised more than `GroundPlaneChoice.maximumRaise`. Nil when no plane qualifies;
    /// the ground stays as it is.
    func groundBelow(_ meter: SIMD3<Float>, along: SIMD3<Float>, current: Float?) -> GroundPlaneChoice.Choice? {
        GroundPlaneChoice.choose(meter: meter, along: along, phone: meterTapCamera, current: current, planes: detectedGroundPlanes)
    }

    func skipCloseUp() {
        guard state.phase == .meterCloseUp else { return }
        state.closeUp = .skipped
        state.meterNumber = .skipped
        // A photo on disk (one the reader turned down, or one confirmed just before the skip)
        // stays a plain photo of the scan: scene.json doesn't list it and the meter mark doesn't
        // link it.
        store.withdrawStill("meter_close.jpg")
        RuntimeLog.engine.info("close-up skipped after \(self.state.closeUpFailedAttempts) failed attempts")
        observeCloseUpView()
        go(.wallWalk)
    }

    /// The homeowner's pick of the meter number. The number stays on the phone, in
    /// `state.meterNumber`: scene.json has no field for it and its schema allows no extra
    /// properties, so the server never receives it.
    func chooseMeterNumber(_ candidate: MeterNumberCandidate?) {
        guard state.phase == .meterCloseUp, case .choose(let candidates) = state.meterNumber else { return }
        guard let candidate else {
            // "None of these": small characters mean the phone was too far for a clear read.
            retakeCloseUp(currentMeterReadout?.numberTooSmall == true ? .numberTooSmall : .noNumber)
            return
        }
        guard let chosen = candidates.first(where: { $0.id == candidate.id }) else { return }
        state.meterNumber = .confirmed(chosen.text)
        // The photo the number was read from is now the meter's close-up. A shot the reader or
        // the homeowner turned down never gets here, so it is never listed as one.
        store.acceptStill("meter_close.jpg")
        RuntimeLog.engine.info("meter number confirmed (\(chosen.barcodeConfirmed ? "barcode-confirmed" : "text only", privacy: .public)), brand \(self.state.meterBrand == nil ? "none" : "kept", privacy: .public)")
        observeCloseUpView()
        finishCloseUp()
    }

    func rejectMeterBrand() {
        guard state.phase == .meterCloseUp, case .choose = state.meterNumber else { return }
        state.meterBrand = nil
        RuntimeLog.engine.info("meter brand rejected")
    }

    /// Puts the close-up photo's view into coverage, under the same rules as a walk keyframe: a
    /// cell counts once seen from two positions at least 0.25 m apart, and the view is replayed
    /// with the kept keyframes whenever the wall moves (`CoverageMap.observedCameras`).
    ///
    /// Only when the close-up step ends (number confirmed, or skipped), because the photo on disk
    /// is final then, and only the view `closeUpCredit` holds: the shot whose photo is on disk,
    /// after the reader found it decodable and in focus. A skip after a photo the reader
    /// rejected as blurry, or while a retake's photo is still saving or being read, adds nothing.
    /// A photo in focus where only the number couldn't be read still counts. The map always
    /// exists here: the close-up follows the meter mark, which sets the wall (`setWall`). No time
    /// is passed, so the pose never joins the walked path: nothing is kept between the close-up
    /// and the walk's first frame.
    private func observeCloseUpView() {
        guard let view = closeUpCredit.take() else { return }
        var delta: CoverageMap.Delta?
        updateCoverage { delta = $0.observe(view.camera, trackingNormal: true, depth: view.depth) }
        RuntimeLog.capture.info("close-up view in coverage: \(delta?.newlySeen ?? 0) cells newly seen, \(delta?.newlyCovered ?? 0) newly covered")
    }

    /// Marks a wall end during the walk, or, during a server past_end request, marks that
    /// request's end again at wherever the wall really stops (B-12), nearer or farther than the
    /// end the request cleared: that end only said how far the walk had seen.
    /// The end at `point`, or with no point at the circle in the middle of the view: the
    /// "Wall ends here" button. The button answers the card, which names a side, so it marks only
    /// that side, only with the phone's place known, and only where the circle is on the wall;
    /// otherwise the card says why (`EndMarkRefusal`, B-06). Before, each of those did nothing,
    /// and aiming across the meter ended the other side. During a past_end request a tap at a
    /// point (the autopilot's) goes through the same check from that point; on the walk it marks
    /// the side it lands on, as before.
    func markWallEnd(at point: CGPoint?, viewSize: CGSize) {
        guard state.phase == .wallWalk || askedEndSide != nil,
              // During a request, once the end is marked its question is what is left to answer.
              state.phase != .gapRequest || state.endQuestion == nil,
              let map = coverage, let frame = currentFrame else { return }
        let hit: WallPoint
        if let asked = askedEndSide, point == nil || state.phase == .gapRequest {
            let pixel = point.map { frame.projection.imagePixel(forViewPoint: $0, in: viewSize) } ?? frame.camera.imageSize / 2
            switch aimedEnd(asked: asked, pixel: pixel, frame: frame, map: map) {
            case .success(let found): hit = found
            case .failure(let refusal): return refuseEndMark(refusal)
            }
        } else {
            guard let found = wallHit(point, viewSize: viewSize, frame: frame, wall: map.wall) else { return }
            hit = found
        }
        let side: WallSide = hit.s < 0 ? .left : .right
        state.endMarkRefusal = nil
        // Unexplored until the homeowner says something blocks the wall there: an unanswered
        // question must not tell the server the usable wall stops at this point.
        state.endQuestion = side
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
        setEnd(side, at: hit.s, kind: .unexplored, source: .homeowner)
        if state.phase == .gapRequest {
            RuntimeLog.engine.info("gap \(self.state.gap?.id ?? 0): the \(side.rawValue, privacy: .public) end marked again at s=\(hit.s)")
        }
    }

    private func refuseEndMark(_ refusal: EndMarkRefusal) {
        state.endMarkRefusal = refusal
        RuntimeLog.engine.info("wall ends here refused: \(String(describing: refusal), privacy: .public)")
    }

    func answerWallEnd(turnsCorner: Bool) {
        guard let side = state.endQuestion else { return }
        state.endQuestion = nil
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
        if wallEndKinds[side] != nil { setEndKind(side, turnsCorner ? .unexplored : .limit) }
        // During the walk a corner is followed: the next wall is marked, and the walk goes on
        // along it. Until then the end stays marked, as an unexplored corner.
        if turnsCorner, state.phase == .wallWalk, wallEndKinds[side] != nil {
            nextWallSide = side
            nextWallRefusal = nil
        }
        if state.phase == .gapRequest, side == pastEndSide {
            if turnsCorner {
                // The wall goes on round the corner, so its end stays unexplored, and the server
                // settles an unexplored end only by views past it (server/README.md, "unexplored").
                // This request can't follow a corner; the walk does that (`markNextWall`). So it
                // closes as cannot_reach, never met: the homeowner's corner stays where they
                // marked it (`PastEndSettlement.keepMarked`), and nothing past it counts as seen.
                // The next answer can ask again to walk past this side, now planned from the
                // corner: the same request this one couldn't serve, so it is recorded as skipped
                // too. Only from this end; a past_end from a different end later is a new view.
                let end = side == .left ? coverage?.leftEnd : coverage?.rightEnd
                skipCurrentGap(
                    because: "the wall turns a corner at the homeowner's end, and this request can't survey past a corner",
                    deferring: end.map { gapPlanner.pastEndPlan(side: side.walk, end: $0) })
            } else {
                settlePastEnd()
            }
        }
        if state.phase == .wallWalk, let frame = currentFrame {
            resetGuidanceAfterSkip(camera: frame.camera, time: frame.timestamp)
        }
    }

    /// The wall round the corner: a raycast on a vertical plane under `point`, the same one the
    /// meter tap uses. Where its line meets the current wall's line on the ground is the corner.
    /// A corner that passes the checks (`CoverageMap.proposeCorner`) isn't followed yet: the
    /// walk asks "Is this the next wall?" (`ScanViewState.nextWallConfirm`, #70) and
    /// `confirmNextWall` follows it.
    func markNextWall(at point: CGPoint?, viewSize: CGSize) {
        guard state.phase == .wallWalk, let side = nextWallSide, pendingNextWall == nil,
              let map = coverage, let frame = currentFrame else { return }
        guard frame.tracking == .normal else { return refuseNextWall(side, .trackingNotReady, "tracking not normal") }
        // A replay has no live surfaces to raycast, so it can't mark the next wall.
        let viewPoint = point ?? CGPoint(x: viewSize.width / 2, y: viewSize.height / 2)
        guard let hit = liveCapture?.raycastVerticalPlane(from: viewPoint) else { return refuseNextWall(side, .noSurface, "no vertical plane") }
        var outward = SIMD3(hit.normal.x, 0, hit.normal.z)
        if simd_dot(outward, frame.camera.position - hit.position) < 0 { outward = -outward }
        // The new piece's line runs through the hit point facing the hit plane's normal, as the
        // meter's does, so its source follows the same rule (`meterLineSource`).
        let source = Self.lineSource(of: hit.source)
        let proposed: Result<CoverageMap.CornerProposal, CornerRefusal> = Result { () throws(CornerRefusal) in
            try map.proposeCorner(side.walk, meeting: hit.position, outward: outward, source: source)
        }
        let proposal: CoverageMap.CornerProposal
        switch proposed {
        case .success(let found): proposal = found
        case .failure(let refusal): return refuseNextWall(side, refusal)
        }
        let detected = hit.source == .detectedPlane
        nextWallRefusal = nil
        pendingNextWall = PendingNextWall(
            side: side, point: hit.position, outward: outward, source: source,
            detectedPlane: detected, s: proposal.corner.s, fromEnd: proposal.fromEnd
        )
        state.guidance = .markNextWall(side: side, refusal: nil)
        // The ring goes on the corner, not on the point marked: a surface behind the end post
        // puts the corner past the post, where the homeowner can see it is wrong (#70, 3C).
        state.target = map.wall.world(s: proposal.corner.s, height: Self.cornerRingHeight)
        RuntimeLog.engine.info("next wall marked on the \(side.rawValue, privacy: .public): corner at s=\(proposal.corner.s), \(proposal.fromEnd) m from the end (\(detected ? "detected" : "estimated", privacy: .public) plane); asking to confirm")
    }

    /// "Is this the next wall?": yes follows the corner, and the end on that side opens again and
    /// the walk goes on along the new wall; no drops the marked wall and keeps looking (#70).
    func confirmNextWall(_ isNextWall: Bool) {
        guard state.phase == .wallWalk, let side = nextWallSide, let pending = pendingNextWall, pending.side == side else { return }
        pendingNextWall = nil
        let plane = pending.detectedPlane ? "detected" : "estimated"
        guard isNextWall else {
            state.target = nil
            RuntimeLog.engine.info("next wall not confirmed on the \(side.rawValue, privacy: .public): corner at s=\(pending.s), \(pending.fromEnd) m from the end (\(plane, privacy: .public) plane); looking again")
            return
        }
        var turned: Result<WallCorner, CornerRefusal> = .failure(.notAWall)
        updateCoverage { map in
            turned = Result { () throws(CornerRefusal) in
                try map.turnCorner(side.walk, meeting: pending.point, outward: pending.outward, source: pending.source)
            }
        }
        let corner: WallCorner
        switch turned {
        case .success(let turn): corner = turn
        case .failure(let refusal): return refuseNextWall(side, refusal)
        }
        // The end moves on with the walk; `clearEnd` also publishes the chain for the overlays.
        clearEnd(side)
        nextWallSide = nil
        nextWallRefusal = nil
        RuntimeLog.engine.info("corner followed on the \(side.rawValue, privacy: .public) at s=\(corner.s), \(pending.fromEnd) m from the end (\(plane, privacy: .public) plane), confirmed")
        // Features tapped past the corner were placed on the old wall's line.
        reprojectFeatures()
        if let frame = currentFrame { resetGuidanceAfterSkip(camera: frame.camera, time: frame.timestamp) }
    }

    /// "Back" on the next-wall step: the end stays marked, and the question about it comes back,
    /// so "Something blocks it" or "The wall just ends" can still be picked (#70).
    func cancelNextWall() {
        guard state.phase == .wallWalk, let side = nextWallSide else { return }
        // The next-wall request didn't happen: resolved before `nextWallSide` clears, which the
        // log would read as met (`closingOutcome`), and withdrawn so a second "It turns a
        // corner" logs a new one.
        withdrawGuidance(.superseded)
        nextWallSide = nil
        nextWallRefusal = nil
        state.target = nil
        state.endQuestion = side
        state.endQuestionLeavesOut = nil
        state.endQuestionLeavesOutSeen = false
        RuntimeLog.engine.info("next wall: back to the question about the \(side.rawValue, privacy: .public) end")
    }

    private func refuseNextWall(_ side: WallSide, _ refusal: CornerRefusal) {
        switch refusal {
        case .nearlyParallel: refuseNextWall(side, .sameWall, "nearly parallel to the current wall")
        case .notAWall: refuseNextWall(side, .noSurface, "not a wall")
        case .implausible(let s): refuseNextWall(side, .notAtCorner, "the walls meet at s=\(s)")
        }
    }

    private func refuseNextWall(_ side: WallSide, _ refusal: NextWallRefusal, _ reason: String) {
        nextWallRefusal = refusal
        state.guidance = .markNextWall(side: side, refusal: refusal)
        RuntimeLog.engine.info("next wall refused: \(reason, privacy: .public)")
    }

    /// The export sends a type as patches over the ground the coverage saw, and "Not sure" as no
    /// patch (`sceneJSON`). Every upload reads the latest answer.
    func answerGround(_ answer: GroundAnswer) {
        guard state.phase == .markFeatures else { return }
        state.groundAnswer = answer
        RuntimeLog.engine.info("ground answered: \(String(describing: answer), privacy: .public)")
    }

    /// "Open sky or nothing overhead" records the tilt-up view for the export; "A roof edge,
    /// porch or stairs" records nothing, so the server treats the stretch as unseen. During an
    /// overhead gap request the answer settles the request either way (`settleOverheadGap`).
    func answerOverhead(clear: Bool) {
        guard state.overheadQuestion else { return }
        switch state.phase {
        case .wallWalk:
            // Something overhead is an answer, not a view: the stretch goes to review unseen.
            resolveGuidance(clear ? .met : .skipped)
            settleTiltUp(clear: clear)
            if let frame = currentFrame {
                resetGuidanceAfterSkip(camera: frame.camera, time: frame.timestamp)
            }
        case .gapRequest:
            settleOverheadGap(clear: clear)
        default:
            return
        }
    }

    func beginMarking(_ kind: FeatureKind) {
        guard state.phase == .wallWalk || state.phase == .markFeatures else { return }
        // A mark is a tap into the world frame. The review hides "Add something" while the phone
        // has lost its place; this holds if a tap races the change.
        guard state.phase != .markFeatures || !state.tracking.hasLostItsPlace else { return }
        state.marking = MarkingState(kind: kind, step: 0, refusal: nil)
        pendingTaps = []
    }

    func markFeaturePoint(at point: CGPoint?, viewSize: CGSize) {
        guard var marking = state.marking, let wall = coverage?.wall, let frame = currentFrame else { return }
        guard frame.tracking == .normal else {
            marking.refusal = .trackingNotReady
            state.marking = marking
            return
        }
        let onGround = marking.kind == .driveway || marking.kind == .fence
        let pixel = frame.projection.imagePixel(forViewPoint: point ?? CGPoint(x: viewSize.width / 2, y: viewSize.height / 2), in: viewSize)
        let ray = frame.camera.ray(throughPixel: pixel)
        if wall.wallPoint(frame.camera.position).out <= 0 {
            marking.refusal = .wrongSide
            state.marking = marking
            return
        }
        let hit: WallPoint
        if onGround {
            guard let ground = wall.intersectGround(ray) else {
                marking.refusal = .noSurface
                state.marking = marking
                return
            }
            // Past 8 m out a ground tap is not about this wall any more.
            if ground.out > ObjectTap.maxGroundOut || ground.out < 0 {
                marking.refusal = .tooFarFromWall
                state.marking = marking
                return
            }
            hit = ground
        } else {
            // A wall hit under the floor, or far along the wall from the phone, is where a ray aimed
            // at the ground or nearly along the wall met the wall's plane: nothing the homeowner
            // pointed at (#140). An AC unit stands on the ground in front of the wall, so a ray
            // aimed at it meets the ground first: that's where it lands (#163).
            let placement = ObjectTap.place(
                ray, standsOnGround: marking.kind == .acUnit, camera: frame.camera.position, wall: wall,
                reach: coverage?.config.maxDistance ?? CoverageConfig().maxDistance, groundError: coverage?.heightError ?? 0)
            switch placement {
            case .wall(let point):
                hit = point
            case .ground(let point):
                RuntimeLog.engine.info("object tap on the ground: s=\(point.s) out=\(point.out)")
                hit = point
            case .refused(let refused):
                RuntimeLog.engine.info("object tap refused: \(refused.description, privacy: .public)")
                if case .tooFarOut = refused {
                    marking.refusal = .tooFarFromWall
                } else {
                    marking.refusal = .noSurface
                }
                state.marking = marking
                return
            }
        }
        pendingTaps.append(hit)
        marking.refusal = nil
        marking.step += 1
        if marking.step < marking.kind.tapCount {
            state.marking = marking
            return
        }
        let marked = feature(marking.kind, taps: pendingTaps, wall: wall)
        markTimes[MarkKey.feature(marked.id)] = captureClock
        state.features.append(marked)
        publishFeaturesPastEnds()
        state.marking = nil
        pendingTaps = []
    }

    private func feature(_ kind: FeatureKind, taps: [WallPoint], wall: WallFrame) -> MarkedFeature {
        var marked = MarkedFeature(id: UUID(), kind: kind, span: 0...0, bottom: nil, top: nil, out: nil, points: taps.map { wall.world($0) }, opens: nil)
        Self.project(&marked, onto: wall)
        return marked
    }

    /// Sets a feature's wall coordinates from its tapped world points. Run again whenever the
    /// wall frame moves (meter anchor refined, ground measured), since only the world points
    /// are what was tapped.
    static func project(_ feature: inout MarkedFeature, onto wall: WallFrame) {
        let taps = feature.points.map { wall.wallPoint($0) }
        let ss = taps.map(\.s)
        let span = (ss.min() ?? 0)...(ss.max() ?? 0)
        switch feature.kind {
        case .door, .window:
            let heights = taps.map(\.height)
            feature.span = span
            feature.bottom = max(0, heights.min() ?? 0)
            feature.top = heights.max()
        case .gasMeter, .acUnit:
            // One tap marks the object's middle; 0.3 m is a nominal width, not a measurement.
            let s = ss.first ?? 0
            feature.span = (s - 0.15)...(s + 0.15)
        case .driveway, .fence:
            feature.span = span
            // The nearer tap, as the export uses: the narrow end must not be overstated.
            feature.out = taps.map(\.out).min() ?? 0
        }
    }

    func cancelMarking() {
        state.marking = nil
        pendingTaps = []
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
        guard state.phase == .wallWalk, bothEndsMarked else { return }
        if let map = coverage, map.endsTooClose, let left = map.leftEnd, let right = map.rightEnd {
            // Ends closer than a battery is wide (`WallFrame.minWallLength`): both sides ended
            // without walking, as "Wall ends here" or "Can't get there" at the meter does.
            // Nothing between them to scan, and an end can't be moved, so both go and the walk
            // goes on; the card says why.
            RuntimeLog.engine.info("finish refused: ends at s=\(left) and s=\(right) are closer than \(WallFrame.minWallLength) m")
            clearEnd(.left)
            clearEnd(.right)
            state.wallTooShort = true
            if let frame = currentFrame { resetGuidanceAfterSkip(camera: frame.camera, time: frame.timestamp) }
            return
        }
        // Done while a request is still up passes it by.
        resolveGuidance(.superseded)
        state.marking = nil
        nextWallSide = nil
        nextWallRefusal = nil
        // Leaving with the overhead question unanswered records nothing.
        if !tiltUpSettled { settleTiltUp(clear: false) }
        go(.markFeatures)
    }

    func confirmFeatures() {
        guard state.phase == .markFeatures else { return }
        runGapCheck()
    }

    func skipGap() {
        // With the end just marked, the end question answers the request; the screen hides this
        // reply until it is answered.
        guard state.phase == .gapRequest, state.endQuestion == nil else { return }
        // The card said the space ends short of the walk-out line (#164): the same answer, with
        // why in the log.
        if let ends = state.gap?.spaceEnds {
            skipCurrentGap(because: String(format: "the space ends about %.2f m out, short of the walk-out line at %.2f m", ends.at, ends.needed))
        } else {
            skipCurrentGap()
        }
    }

    func showResultNow() {
        guard state.phase == .gapRequest, state.endQuestion == nil else { return }
        stopGapRequests()
    }

    func cannotAccessArea() {
        switch state.phase {
        case .gapRequest:
            skipCurrentGap()
        case .wallWalk:
            guard coverage != nil, !state.endScanQuestion else { return }
            let task = ScanEngine.name(state.guidance)
            // Only the walk's own "Can't get there": "The wall keeps going" on the end card answers
            // a question rather than refusing one, so it never asks to end the scan.
            if case .walk = state.guidance, state.endQuestion == nil, state.marking == nil, !state.overheadQuestion,
               state.nextWallConfirm == nil, walkRefusals.asksToEndScan(at: ScanEngine.refusalClock) {
                // A second "Can't get there" on a walk card soon after the last ended a side:
                // the homeowner may be trying to stop, so ask before ending this side too (#82).
                // The task stays unresolved until the answer.
                RuntimeLog.engine.info("cannot access area again during \(task, privacy: .public): asking to end the scan")
                if let map = coverage {
                    // With nothing walked the ends would land too close to finish, and "Yes, end
                    // here" would only put the homeowner back on the walk.
                    state.endScanTooShort = WalkRefusals.endsTooClose(left: map.leftEnd ?? walkedEnd(.left), right: map.rightEnd ?? walkedEnd(.right))
                }
                state.endScanQuestion = true
                return
            }
            switch state.guidance {
            case .aimAtGround, .aimAtWall, .seeBehind, .walk, .markEnd, .tiltUp, .markNextWall:
                resolveGuidance(.cannotReach)
            default:
                return
            }
            switch state.guidance {
            case .aimAtGround(let s):
                updateCoverage { $0.markSkipped(.ground, (s - 0.5)...(s + 0.5)) }
            case .aimAtWall(let s):
                updateCoverage { $0.markSkipped(.wall, (s - 0.5)...(s + 0.5)) }
            case .seeBehind(let s):
                // Whatever is in the way can't be seen past: its hidden cells go to review. Only
                // those: open cells beside them can still be seen and stay asked for.
                updateCoverage { map in
                    for band in seeBehindBands {
                        for index in ScanEngine.hiddenCells(map, band: band, around: s) { map.markSkipped(band, map.cellRange(index)) }
                    }
                }
            case .walk(let side, _):
                // The walk can't continue this way: stop the wall where the phone is, as an
                // unexplored end (`WalkedEnd`), where the strip's preview showed it.
                endWalkCannotGoOn(side)
            case .markEnd(let side):
                // "The wall keeps going": the same unexplored end where the phone is, but not a
                // refusal, so it doesn't count toward "End the scan here?" (#82).
                endWalkCannotGoOn(side, refused: false)
            case .tiltUp:
                settleTiltUp(clear: false)
            case .markNextWall:
                // Not following the corner: the end stays marked, as an unexplored corner.
                nextWallSide = nil
                nextWallRefusal = nil
            default:
                return
            }
            RuntimeLog.engine.info("cannot access area during \(task, privacy: .public)")
            if let frame = currentFrame {
                resetGuidanceAfterSkip(camera: frame.camera, time: frame.timestamp)
            }
        default:
            return
        }
    }

    func answerEndScan(_ end: Bool) {
        guard state.phase == .wallWalk, state.endScanQuestion else { return }
        state.endScanQuestion = false
        state.endScanTooShort = false
        guard end else {
            RuntimeLog.engine.info("end the scan here? keep walking")
            walkRefusals.keepWalking()
            return
        }
        RuntimeLog.engine.info("end the scan here? yes")
        // The walk task the second "Can't get there" answered is met as refused.
        if isWalkTask { resolveGuidance(.cannotReach) }
        // Each side without an end ends where "Can't get there" would put it (`WalkedEnd`).
        for side in [WallSide.left, .right] where (side == .left ? coverage?.leftEnd : coverage?.rightEnd) == nil {
            endWalkCannotGoOn(side)
        }
        walkRefusals.keepWalking()
        // Ends closer than a battery is wide are refused here as after "Done with this wall",
        // and the walk goes on with the card saying so.
        finishWalk()
    }

    /// The walk asks to walk a side or to mark its end: the steps whose "Can't get there" ends
    /// the wall on that side (`endWalkCannotGoOn`).
    private var isWalkTask: Bool {
        switch state.guidance {
        case .walk, .markEnd: true
        default: false
        }
    }

    /// Sends the same scan again, only when the homeowner asks: after a network or server failure,
    /// or an answer House Scan couldn't use. Never after a refusal, which the review has to fix.
    func retryUpload() {
        guard state.phase == .uploading else { return }
        switch state.upload {
        case .failed, .unusableAnswer: startUpload()
        case .idle, .packaging, .uploading, .analyzing, .rejected, .done: break
        }
    }

    /// Back to the feature review after a rejected upload. The scan (wall, coverage, keyframes,
    /// features) stays; confirming the review runs the gap check and the upload again.
    func backToReview() {
        guard state.phase == .uploading, case .rejected = state.upload else { return }
        state.upload = .idle
        go(.markFeatures)
    }

    func captureMissing(_ id: String) {
        // Once the camera failed after the scan was sent, a capture would get no frames; the
        // result screen doesn't offer one then either (ResultCardActions).
        guard state.phase == .result || state.phase == .gapRequest || state.phase == .uploading,
              state.spatialResultAvailable,
              let missing = placement?.missingEvidence,
              let index = Int(id.replacingOccurrences(of: "missing-", with: "")),
              missing.indices.contains(index) else { return }
        let item = missing[index]
        guard let map = coverage, let plan = gapPlanner.plan(for: item, leftEnd: map.leftEnd, rightEnd: map.rightEnd, limitEnds: map.limitEnds) else { return }
        beginServerGap(item, plan: plan)
    }

    func showAR() {
        // A wall neither side of which was walked has no spot to show (#76).
        guard state.phase == .result, state.spatialResultAvailable, state.result?.wallNotMeasured != true else { return }
        go(.resultAR)
    }

    func closeAR() {
        guard state.phase == .resultAR else { return }
        go(.result)
    }

    func startOver() {
        releaseFailedSource()
        resetAll()
    }

    func liveCameraView() -> AnyView {
        guard let live = liveCapture else { return AnyView(Color.black) }
        return AnyView(LiveCameraView(arView: live.arView))
    }

    // MARK: Helpers

    /// Where a tap lands on the wall plane, in wall coordinates.
    func wallHit(_ point: CGPoint?, viewSize: CGSize, frame: SourceFrame, wall: WallFrame) -> WallPoint? {
        let pixel = frame.projection.imagePixel(forViewPoint: point ?? CGPoint(x: viewSize.width / 2, y: viewSize.height / 2), in: viewSize)
        return nearbyWallHit(frame.camera.ray(throughPixel: pixel), camera: frame.camera, wall: wall)
    }

    /// Where `ray` meets the wall, or nil when that is farther along the wall from the camera than
    /// the coverage map lets a camera see (`maxDistance`). A ray nearly parallel to the wall meets
    /// its line hundreds of meters away; an end placed there, or drawn on the strip, would be
    /// nothing the homeowner pointed at.
    func nearbyWallHit(_ ray: Ray, camera: CameraFrame, wall: WallFrame) -> WallPoint? {
        guard let hit = wall.intersectWall(ray) else { return nil }
        let reach = coverage?.config.maxDistance ?? CoverageConfig().maxDistance
        return abs(hit.s - wall.wallPoint(camera.position).s) <= reach ? hit : nil
    }
}
