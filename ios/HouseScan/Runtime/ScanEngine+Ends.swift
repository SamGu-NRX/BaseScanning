import Foundation
import HouseScanKit
import OSLog
import simd

/// Ending the wall during the walk without pointing at the end ("Can't get there", "Wall ends
/// here"), marking it at the circle, during the walk or a past-end request, and the preview of
/// where an end would land.
extension ScanEngine {
    /// The end a card asks the homeowner to mark at the circle: the walk's "Is this the right end
    /// of the wall?", or a past_end request's side while it is unsettled (B-12). "Wall ends here"
    /// marks only this side, through the same aim check either way (`circleEnd`).
    var askedEndSide: WallSide? {
        switch state.phase {
        case .wallWalk:
            if case .markEnd(let side) = state.guidance { return side }
            return nil
        case .gapRequest:
            guard let side = pastEndSide, state.gap?.isSatisfied == false else { return nil }
            return side
        default:
            return nil
        }
    }

    /// The side ending the wall applies to now, and whether the end lands at the reticle. The
    /// walk's own side while it asks to walk or to mark the end; during a request about the wall
    /// in front of the homeowner (tilt down, tilt up, step back), the side of the meter the phone
    /// is on (`WalkedEnd.side`). During a past_end request its side, at the reticle. Nil when both
    /// ends are marked or the walk is doing something else.
    private var endOnOffer: (side: WallSide, atReticle: Bool)? {
        if state.phase == .gapRequest {
            guard let side = askedEndSide, state.endQuestion == nil, !state.overheadQuestion, coverage != nil else { return nil }
            return (side, true)
        }
        guard state.phase == .wallWalk, state.marking == nil, state.endQuestion == nil, !state.overheadQuestion,
              nextWallSide == nil, let map = coverage else { return nil }
        switch state.guidance {
        case .walk(let side, _):
            return (side, false)
        case .markEnd(let side):
            return (side, true)
        case .aimAtGround, .aimAtWall, .stepBack:
            guard let side = WalkedEnd.side(phone: phonePosition, wall: map.wall, leftEnd: map.leftEnd, rightEnd: map.rightEnd) else { return nil }
            return (side == .left ? .left : .right, false)
        case .findMeter, .aimAtWallForMeter, .holdOnMeter, .walkComplete, .tiltUp, .markNextWall, .gap, .seeBehind:
            return nil
        }
    }

    /// Where ending the walk on `side` puts the end now (`WalkedEnd`): the phone's place along the
    /// wall on that side with tracking normal, else kept to what was walked on that side (#71).
    /// The phone's place doesn't count while it has lost it.
    func walkedEnd(_ side: WallSide) -> Float? {
        walkedEndChoice(side)?.s
    }

    /// `walkedEnd` with why it landed there, for the preview and the log.
    private func walkedEndChoice(_ side: WallSide) -> WalkedEnd.Choice? {
        guard let map = coverage else { return nil }
        // Not debounced (review of #147): waiting for tracking to settle put run 2's end back at
        // the meter after a brief drop. A pose that jumps as tracking returns is bounded by
        // `WalkedEnd.maxPastEvidence` instead.
        let trackingNormal = currentFrame?.tracking == .normal
        return WalkedEnd.choose(
            side.walk, phone: phonePosition, trackingNormal: trackingNormal, walked: map.walkedPositions, wall: map.wall,
            seen: map.seenExtent
        )
    }

    /// The strip's seen cells, when the cap decided the end: what an end short of them leaves
    /// out is said (`WalkedEnd.leftOut`), not dropped silently (#71). Nil when the end is where
    /// the phone stands, whose camera sees past it anyway; up to the phone when it is on that
    /// side (`WalkedEnd.countedSeen`).
    private func seenPastCap(_ side: WallSide, _ choice: WalkedEnd.Choice?, _ map: CoverageMap) -> ClosedRange<Float>? {
        guard let choice else { return nil }
        return WalkedEnd.countedSeen(side.walk, choice: choice, seen: map.seenExtent)
    }

    /// Where the phone is; nil while it has lost its place.
    private var phonePosition: SIMD3<Float>? {
        currentFrame.flatMap { $0.tracking.hasLostItsPlace ? nil : $0.camera.position }
    }

    /// The end preview for this moment (`ScanViewState.endPreview`).
    func currentEndPreview() -> EndPreview? {
        guard let offer = endOnOffer, let map = coverage, let frame = currentFrame else { return nil }
        let side: WallSide
        let s: Float
        var seen: ClosedRange<Float>?
        if offer.atReticle {
            // Where the button would put it: nothing when the button would refuse (`circleEnd`).
            guard case .success(let hit) = circleEnd(asked: offer.side, frame: frame, map: map) else { return nil }
            side = hit.s < 0 ? .left : .right
            s = hit.s
        } else {
            guard let choice = walkedEndChoice(offer.side) else { return nil }
            side = offer.side
            s = choice.s
            seen = seenPastCap(offer.side, choice, map)
        }
        // What this end leaves out of the walk shows only while the walk asks to walk this side
        // or mark its end, and not with the phone far out from the wall (#66). During a tilt or
        // step-back request the dashed line and "Wall ends here" stay; the end question says what
        // pressing it left out (`endWallHere`).
        let onWalkTask: Bool
        switch state.guidance {
        case .walk, .markEnd:
            onWalkTask = true
        default:
            onWalkTask = false
        }
        // The phone's distance from the wall matters only when the end is its place along it.
        let phoneOut: Float? = offer.atReticle ? nil : phonePosition.map { map.wall.wallPoint($0).out }
        let leavesOut = WalkedEnd.leavesOut(
            side: side.walk, s: s, walked: map.walkedPositions, wall: map.wall,
            phoneOut: phoneOut, onWalkTask: onWalkTask, seen: seen
        )
        let isSeen = leavesOut != nil && WalkedEnd.leftOutIsSeen(side.walk, s: s, walked: map.walkedPositions, wall: map.wall, seen: seen)
        return EndPreview(side: side, s: s, atReticle: offer.atReticle, leavesOutWalked: leavesOut, leavesOutSeen: isSeen)
    }

    /// Where "Wall ends here" at the circle puts the end the card asks for on `asked`, or why it
    /// can't (`EndAim`, B-06). The circle is the middle of the view, which is the sensor image's
    /// middle whatever the view's size. Shared by the button and its preview, so the tape never
    /// shows an end the button would refuse. Only normal tracking counts, as for feature marks.
    func circleEnd(asked: WallSide, frame: SourceFrame, map: CoverageMap) -> Result<WallPoint, EndMarkRefusal> {
        aimedEnd(asked: asked, pixel: frame.camera.imageSize / 2, frame: frame, map: map)
    }

    /// `circleEnd` for the ray through `pixel` of the sensor image. A tap at a point (the
    /// autopilot's) during a past_end request goes through the same check as the circle.
    ///
    /// During a past_end request only, an end that would leave less wall than the walk's minimum
    /// (`WallFrame.minWallLength`, `CoverageMap.endWouldLeaveTooLittle`) is refused here, so the
    /// button, its refusal and the tape's preview agree. The walk applies that minimum when it
    /// finishes ("Done with this wall") and keeps doing so; a request settles without that step.
    /// Whether 0.79 m is the right minimum is an open product question, not settled here.
    func aimedEnd(asked: WallSide, pixel: SIMD2<Float>, frame: SourceFrame, map: CoverageMap) -> Result<WallPoint, EndMarkRefusal> {
        let hit = map.wall.intersectWall(frame.camera.ray(throughPixel: pixel))
        switch EndAim.verdict(
            hit: hit, camera: frame.camera.position, wall: map.wall, reach: map.config.maxDistance,
            // The frame and the phone now: a pose-only frame after it can report tracking lost
            // without replacing the sampled frame whose ray this is.
            groundError: map.heightError, askedLeft: asked == .left, trackingNormal: frame.tracking == .normal && state.tracking == .normal
        ) {
        case .end(let point):
            if state.phase == .gapRequest, map.endWouldLeaveTooLittle(asked.walk, at: point.s) { return .failure(.tooLittleWall) }
            return .success(point)
        case .trackingLimited: return .failure(.trackingNotReady)
        case .offWall: return .failure(.noWall)
        case .otherSide: return .failure(.otherSide(asked == .left ? .right : .left))
        }
    }

    /// Republishes the end preview; called with every guidance update, since the phone moves.
    func publishEndPreview() {
        let preview = currentEndPreview()
        if preview != state.endPreview { state.endPreview = preview }
        // A refusal stands until the circle is on the end the card asks for, or the card moves on.
        if state.endMarkRefusal != nil {
            var fixed = true
            if let side = askedEndSide { fixed = preview?.atReticle == true && preview?.side == side }
            if fixed { state.endMarkRefusal = nil }
        }
    }

    func endWallHere() {
        guard let preview = currentEndPreview(), !preview.atReticle, let map = coverage else { return }
        // Whatever the walk was asking, the end question says how much of the walk this end
        // leaves out, now that the homeowner chose to end the wall here (#66).
        let seen = seenPastCap(preview.side, walkedEndChoice(preview.side), map)
        let leavesOut = WalkedEnd.leftOut(preview.side.walk, s: preview.s, walked: map.walkedPositions, wall: map.wall, seen: seen)
        // The walk toward that side is met: the homeowner got to its end.
        if case .walk(let side, _) = state.guidance, side == preview.side { resolveGuidance(.met) }
        logEnd("wall ends here", side: preview.side, at: preview.s)
        // Unexplored until the homeowner says something blocks the wall there, as for a marked end.
        state.endQuestion = preview.side
        state.endQuestionLeavesOut = leavesOut
        state.endQuestionLeavesOutSeen = leavesOut != nil
            && WalkedEnd.leftOutIsSeen(preview.side.walk, s: preview.s, walked: map.walkedPositions, wall: map.wall, seen: seen)
        // The homeowner said the wall ends where they stand: their mark.
        setEnd(preview.side, at: preview.s, kind: .unexplored, source: .homeowner)
    }

    /// "Can't get there" while the walk asks to walk `side` or to mark its end: the end goes where
    /// the preview showed, as an unexplored end. `walkRefusals` notes when, and whether the side
    /// had been walked (#82, #76).
    func endWalkCannotGoOn(_ side: WallSide, refused: Bool = true) {
        guard let s = walkedEnd(side), let map = coverage else { return }
        logEnd(refused ? "can't get there" : "the wall keeps going", side: side, at: s)
        let walked = map.walkedFarthest(side.walk)
        // Where the walk reached, not a place the homeowner marked: inferred (B-12).
        setEnd(side, at: s, kind: .unexplored, source: .inferred)
        guard refused else { return }
        walkRefusals.ended(side.walk, at: s, walked: walked, time: ScanEngine.refusalClock)
        if walkRefusals.wasRefused(side.walk, end: s) {
            RuntimeLog.engine.info("the \(side.rawValue, privacy: .public) side ended before it was walked (\(walked) m walked, end at s=\(s))")
        }
    }

    /// Seconds for `WalkRefusals`: the time since boot, which a replay's frame clock doesn't
    /// hold back while its playback is paused.
    static var refusalClock: TimeInterval { ProcessInfo.processInfo.systemUptime }

    /// Where the end went, and why (#71): the phone's place, the kept-photo cap, whether the cap
    /// decided it, tracking, how many meters of cells the strip showed past the end, and what the
    /// old rule, the unbroken covered reach, would have said.
    private func logEnd(_ action: String, side: WallSide, at s: Float) {
        guard let map = coverage else { return }
        let phonePoint = currentFrame.map { map.wall.wallPoint($0.camera.position) }
        let phone = phonePoint?.s ?? .nan
        let out = phonePoint?.out ?? .nan
        let tracking = currentFrame.map { String(describing: $0.tracking) } ?? "none"
        let choice = walkedEndChoice(side)
        let cap = choice?.cap ?? .nan
        // "phone": at the phone; "2 m past evidence": short of it, `WalkedEnd.maxPastEvidence`
        // past the farthest kept view or seen cell; "cap": the kept-photo cap.
        let capped = choice?.pastEvidence == true ? "2 m past evidence" : choice?.capped == true ? "cap" : "phone"
        let shown = WalkedEnd.shownPast(side.walk, s: s, seen: map.seenExtent, minimum: 0) ?? 0
        let covered = GuidancePlanner().reach(side.walk, coverage: map)
        RuntimeLog.engine.info("\(action, privacy: .public) on the \(side.rawValue, privacy: .public): end at s=\(s), chose \(capped, privacy: .public) (phone at s=\(phone) \(out) m out, cap s=\(cap), tracking \(tracking, privacy: .public)); strip showed \(shown) m past it; covered reach \(covered) m")
    }

    /// `ScanViewState.featuresPastEnds`, refreshed when the ends move (`publishWall`), the spans
    /// follow the wall (`reprojectFeatures`) and a mark is added.
    func publishFeaturesPastEnds() {
        let left = coverage?.leftEnd
        let right = coverage?.rightEnd
        let past = Set(state.features.filter { WalkedEnd.liesPastAnEnd($0.span, leftEnd: left, rightEnd: right) }.map(\.id))
        if past != state.featuresPastEnds { state.featuresPastEnds = past }
    }
}

extension WallSide {
    var walk: WalkSide { self == .left ? .left : .right }
}
