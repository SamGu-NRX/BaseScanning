import Foundation
import HouseScanKit

/// How the app's steps map onto the packet's guidance kinds (`GuidanceLog`), and how a request
/// that leaves the screen without an action settling it is judged.
///
/// | App step | Packet kind | band, span |
/// | --- | --- | --- |
/// | hold on the meter (close-up) | closeup | |
/// | walk to a side; walk round a corner and mark the next wall | walk | |
/// | mark the end on a side | mark_end | |
/// | tilt down to the ground at s | tilt_to_ground | ground, s ± 0.3 m |
/// | tilt up to show more wall at s | gap_band | wall, s ± 0.3 m |
/// | step back | step_back | |
/// | tilt up above the meter's stretch | gap_band | overhead, its stretch |
/// | something in front of the wall at s (LiDAR) | gap_band | its hidden band, s ± 1 m |
/// | a gap request, phone's or server's | gap_band | wall, ground, overhead, or facing for a walk-out |
/// | a server past_end request | gap_past_end | its span |
/// | the spot check before the result | gap_band | ground, the spot's clearance area |
///
/// Finding and marking the meter, "that's the whole wall" and the questions (what is at an end,
/// what is overhead) are not requests of any packet kind and are not logged. Coaching (slow
/// down, hold steady) is not logged either: it pauses a request rather than replacing it.
extension ScanEngine {
    /// The request on screen now, or nil.
    func guidanceRequest() -> GuidanceLog.Request? {
        switch state.phase {
        case .meterCloseUp:
            guard state.guidance == .holdOnMeter else { return nil }
            return request(.closeUp, .closeup, ScanCopy.guidance(.holdOnMeter))
        case .wallWalk:
            return walkRequest(state.guidance)
        case .gapRequest:
            guard let gap = state.gap, let plan = gapPlan else { return nil }
            let band: GuidanceLog.Band = switch plan.need {
            case .overhead: .overhead
            case .walkOut: .facing
            case .groundOut: .ground
            case .cells, .wallUp: plan.band == .ground ? .ground : .wall
            }
            return GuidanceLog.Request(
                topic: .gap(id: gap.id), kind: pastEndSide == nil ? .gapBand : .gapPastEnd, origin: gap.origin == .server ? .server : .phone,
                message: Self.text(ScanCopy.gapTask(gap)), band: pastEndSide == nil ? band : nil, span: plan.span
            )
        case .spotConfirm:
            return spotCheckGuidance
        case .onboarding, .findMeter, .markFeatures, .uploading, .result, .resultAR, .unsupported:
            return nil
        }
    }

    /// The request's message is the step's copy without `ScanViewState.guidanceHint`: the hint
    /// follows the camera frame by frame, and one request stays one entry. The title on screen
    /// can differ from the logged message while a hint is showing.
    private func walkRequest(_ step: GuidanceStep) -> GuidanceLog.Request? {
        let copy = ScanCopy.guidance(step)
        let cell = { (s: Float) in self.coverage?.cellIndex(forS: s) ?? 0 }
        switch step {
        case .walk(let side, _):
            return request(.walk(side), .walk, copy)
        case .markNextWall(let side, _):
            return request(.nextWall(side), .walk, copy)
        case .markEnd(let side):
            return request(.markEnd(side), .markEnd, copy)
        case .aimAtGround(let s):
            // The planner's own window for an aim task (`GuidancePlanner.isSatisfied`).
            return request(.aimAtGround(cell: cell(s)), .tiltToGround, copy, band: .ground, span: (s - 0.3)...(s + 0.3))
        case .aimAtWall(let s):
            return request(.aimAtWall(cell: cell(s)), .gapBand, copy, band: .wall, span: (s - 0.3)...(s + 0.3))
        case .stepBack:
            return request(.stepBack, .stepBack, copy)
        case .tiltUp(let span):
            return request(.tiltUp, .gapBand, copy, band: .overhead, span: span)
        case .seeBehind(let s):
            let band: GuidanceLog.Band = seeBehindBands.contains(.wall) ? .wall : .ground
            return request(.seeBehind(cell: cell(s)), .gapBand, copy, band: band, span: (s - Self.seeBehindReach)...(s + Self.seeBehindReach))
        case .findMeter, .aimAtWallForMeter, .holdOnMeter, .walkComplete, .gap:
            return nil
        }
    }

    private func request(
        _ topic: GuidanceLog.Topic, _ kind: GuidanceLog.Kind, _ copy: Instruction, band: GuidanceLog.Band? = nil, span: ClosedRange<Float>? = nil
    ) -> GuidanceLog.Request {
        GuidanceLog.Request(topic: topic, kind: kind, origin: .phone, message: Self.text(copy), band: band, span: span)
    }

    /// The words on the card: the title, then the detail.
    static func text(_ copy: Instruction) -> String {
        guard let detail = copy.detail else { return copy.title }
        let ends = copy.title.last.map { ".?!".contains($0) } ?? false
        return copy.title + (ends ? " " : ". ") + detail
    }

    /// The outcome of a request that left the screen without an action closing it (`resolve`
    /// covers "I can't get there", answered questions and closed gaps). Met when what it asked for
    /// is now there, superseded when another request took its place first.
    func closingOutcome(_ old: GuidanceLog.Request, next: GuidanceLog.Request?) -> GuidanceLog.Outcome {
        let s = old.span.map { ($0.lowerBound + $0.upperBound) / 2 }
        switch old.topic {
        case .closeUp:
            switch state.closeUp {
            case .captured: return .met
            case .skipped: return .skipped
            case .aiming: return .superseded
            }
        case .walk(let side):
            // Walked as far as that side goes: the planner asks for its end, or the walk is done.
            return next?.topic == .markEnd(side) || state.guidance == .walkComplete ? .met : .superseded
        case .nextWall:
            // Marking the next wall clears the question; "I can't get there" and Done resolve it
            // before it leaves.
            return nextWallSide == nil ? .met : .superseded
        case .markEnd(let side):
            // Met only by the homeowner's mark: an end the walk inferred answered another request
            // ("Can't get there", or "The wall keeps going", closed as cannot_reach) (B-12).
            guard wallEndKinds[side] != nil, let stamp = endStamps[side] else { return .superseded }
            return stamp.markEndOutcome
        case .stepBack:
            guard let camera = currentFrame?.camera, let wall = coverage?.wall else { return .superseded }
            return wall.wallPoint(camera.position).out > GuidancePlanner().config.tooClose ? .met : .superseded
        case .aimAtGround, .aimAtWall:
            // Met as the planner meets it (`GuidancePlanner.isSatisfied`): enough of the stretch
            // covered. The cell at s alone could be covered while the stretch wasn't.
            guard let span = old.span, let map = coverage else { return .superseded }
            let band: SurfaceBand = old.band == .ground ? .ground : .wall
            return map.coveredFraction(band, in: span) >= GuidancePlanner.aimSatisfied ? .met : .superseded
        case .seeBehind:
            guard let s, let map = coverage else { return .superseded }
            let band: SurfaceBand = old.band == .wall ? .wall : .ground
            return Self.hiddenCells(map, band: band, around: s).isEmpty ? .met : .superseded
        case .tiltUp, .gap, .spotCheck:
            // These close through their answers (`resolveGuidance`); leaving any other way means
            // something else took over.
            return .superseded
        }
    }
}
