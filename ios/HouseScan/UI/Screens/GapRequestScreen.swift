import SwiftUI

/// One targeted request for a missing view. The requested cells turn amber on the camera and
/// on the tape map instead of fog, a bar fills as they're seen, and a check lands when done.
/// An overhead request asks what is above the wall once a tilted-up view covers it.
///
/// A request the finished check sent back (the scan came here from the upload, not from the
/// review) carries "One more view to finish" above the instruction, the words the upload screen
/// just said. "I can't get there" skips only this view and goes on to the check's next one;
/// "Show my result", on every such request, stops asking and leads to the result.
///
/// A past_end request (the walk stopped at an end, and the check asks to walk on past it) also
/// offers the walk's own "Wall ends here" for that end, with the circle, the end's preview on the
/// tape, the refusal when the circle isn't on that end, and the end question (B-12). Walking on
/// stays the request; marking the end is the other true answer.
struct GapRequestScreen: View {
    let state: ScanViewState
    let actions: any ScanActions

    @State private var cameraSize: CGSize = .zero
    /// The open camera between the card and the actions, for the aim ring (`CameraChrome`).
    @State private var cameraWindow = CameraWindow()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        ZStack {
            CameraSizeReader(size: $cameraSize)
            CameraOverlays(state: state, highlight: state.gap, cameraWindow: cameraWindow)
            if showsMeterPhoto, let meterPhoto {
                // "Point at the meter like this.": the saved close-up, as on the walk (B-23).
                SavedMeterPhoto(image: meterPhoto)
                    .transition(.opacity)
            }
            // The end lands under the circle, as on the walk's "Is this the right end?". It fades
            // with "Wall ends here" on the same spring, so the pair comes and goes together;
            // scoped to the circle, so nothing else on the camera animates with it. Under Reduce
            // Motion only its opacity changes, briefly: nothing moves.
            if state.gap?.pastEndSide != nil {
                Reticle(diameter: 56)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .ignoresSafeArea()
                    .opacity(offersEndMark ? 1 : 0)
                    .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.settle, value: offersEndMark)
            }
            if state.gap?.isSatisfied == true {
                SuccessBadge()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .ignoresSafeArea()
                    .transition(reduceMotion ? .opacity : .scale(scale: 0.85).combined(with: .opacity))
            }
            CameraChrome(
                instruction: instruction,
                tone: tone,
                reply: state.gap?.isSatisfied == true || asking || askingEnd ? nil : InstructionCard.Reply(
                    title: "I can't get there",
                    identifier: "action.skipGap",
                    hint: skipHint,
                    perform: { actions.skipGap() },
                    // The request, not the coaching that can show over it nor a walk-out's reading
                    // as the phone moves: neither locks the reply again (InstructionCard.replyLock).
                    task: state.gap.map { ScanCopy.gapTask($0) }
                ),
                eyebrow: followUps > 0 && state.gap?.isSatisfied != true ? ScanCopy.followUp(remaining: followUps) : nil,
                photoCount: state.captureCount,
                lastCaptureID: state.lastCapture?.id,
                isReplay: state.isReplay,
                isAutopilot: state.isAutopilot,
                cameraWindow: cameraWindow,
                aims: aims
            ) {
                VStack(spacing: 10) {
                    if asking {
                        OverheadAnswers(actions: actions)
                            .transition(.opacity)
                    }
                    // The end's controls fade on their own: the rows below them move into place at
                    // once rather than sliding with the stack, and under Reduce Motion nothing
                    // animates.
                    VStack(spacing: 10) {
                        if askingEnd {
                            EndQuestionAnswers(actions: actions)
                                .transition(.opacity)
                        }
                        if offersEndMark {
                            markEndButton
                        }
                    }
                    .animation(reduceMotion ? nil : Motion.settle, value: askingEnd)
                    .animation(reduceMotion ? nil : Motion.settle, value: offersEndMark)
                    if !typeSize.isAccessibilitySize {
                        showResult
                    }
                    if let gap = state.gap {
                        GapProgress(gap: gap)
                    }
                    if let wall = state.wall {
                        WallTape(
                            coverage: state.coverage,
                            wall: wall,
                            features: state.features,
                            cameraS: state.projection.map { WallProjection(projection: $0, wall: wall, size: cameraSize).cameraS },
                            highlight: state.gap,
                            depthChecked: state.depthAvailable,
                            endPreview: offersEndMark ? state.endPreview : nil
                        )
                    }
                    if typeSize.isAccessibilitySize {
                        showResult
                    }
                }
                .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.settle, value: asking)

            }
        }
        .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.pin, value: state.gap?.isSatisfied)
        .animation(.easeOut(duration: 0.2), value: showsMeterPhoto)
    }

    /// "Show my result", on every request the check sent back, even with one view left: the
    /// upload after "I can't get there" can bring a new request in the next answer, and this is
    /// the one way to stop asking (issue #39).
    ///
    /// It sits above the progress, within thumb reach, except at the accessibility text sizes.
    /// There the card reaches the bottom edge, with its "I can't get there" reply cut off by it.
    /// With "Show my result" directly under that reply, the audit failed the reply's contrast at
    /// AX5 (CI run 36276179058); with the progress under it, as before, it passed. So at those
    /// sizes it goes below the tape, the last thing on the screen, reached by scrolling like
    /// everything else below the card at that size.
    @ViewBuilder private var showResult: some View {
        if followUps > 0, state.gap?.isSatisfied != true, !asking, !askingEnd {
            Button {
                actions.showResultNow()
            } label: {
                Label("Show my result", systemImage: "checkmark.circle")
            }
            .buttonStyle(.secondary)
            .accessibilityHint("Stops asking for views and shows your result. An installer would need to look at the parts you skip.")
            .accessibilityIdentifier("action.showResult")
            .transition(.opacity)
        }
    }

    /// The overhead question is up: it replaces the instruction, coaching and "I can't get there"
    /// until answered, as on the walk.
    private var asking: Bool { state.overheadQuestion && state.gap?.isSatisfied != true }

    /// The end question after "Wall ends here" on a past_end request: it replaces the request,
    /// "I can't get there" and "Show my result" until answered. An end the wall stops at settles the
    /// request; a corner, which the request can't follow, moves on without settling it.
    private var askingEnd: Bool {
        guard let side = state.endQuestion, let gap = state.gap else { return false }
        return gap.pastEndSide == side && !gap.isSatisfied
    }

    /// "Wall ends here" with the circle, on a past_end request not yet settled or answered.
    private var offersEndMark: Bool {
        guard let gap = state.gap, gap.pastEndSide != nil, !gap.isSatisfied else { return false }
        return !asking && !askingEnd
    }

    /// The walk's "Wall ends here" at the circle, the same words and flag. Beside walking on, which
    /// stays the request, it is the other answer, so it is not the screen's primary button.
    private var markEndButton: some View {
        Button {
            actions.markWallEnd(at: nil, viewSize: cameraSize)
        } label: {
            Label("Wall ends here", systemImage: "flag.fill")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.secondaryProminent)
        .accessibilityHint("Marks the end of the wall at the circle in the middle of the screen")
        .accessibilityIdentifier("action.markEnd")
        .transition(.opacity)
    }

    /// A refusal on "Wall ends here" keeps its red triangle, as on the walk; otherwise coaching
    /// marks the card.
    private var tone: InstructionCard.Tone {
        if asking || askingEnd { return .normal }
        if refusal != nil, !(coaching.map(ScanCopy.coachingReplacesTask) ?? false) { return .refusal }
        return coaching.map { .coaching(symbol: ScanCopy.coachingSymbol($0)) } ?? .normal
    }

    /// Why the last "Wall ends here" marked nothing, while the request still asks for that end.
    private var refusal: EndMarkRefusal? {
        offersEndMark ? state.endMarkRefusal : nil
    }

    /// Views the finished check still wants, counting this one, when this request is one of
    /// them: a server request while the check's answer is in. Zero otherwise.
    private var followUps: Int {
        guard state.gap?.origin == .server, state.result != nil else { return 0 }
        return max(1, state.followUps)
    }

    /// What "I can't get there" leads to: the check's next view while it wants more than this
    /// one. With one left the scan is sent again, and that answer can still ask for a new view,
    /// so the hint doesn't promise the result; "Show my result" does.
    private var skipHint: String {
        switch followUps {
        case 0: "Skips this view. An installer would need to look at this part instead."
        case 1: "Skips this view and checks your scan again. An installer would need to look at this part instead."
        default: "Skips this view and goes on to the next one. An installer would need to look at this part instead."
        }
    }

    /// The phone's own request for ground by a likely spot has the homeowner aim the camera, and
    /// its second line only says why ("This might be a spot for the battery"), so it folds at the
    /// largest text sizes (`CameraChrome.aims`). Every other request's second line is the action
    /// or its extent: stepping back for ground further out, the walk out with its live "this
    /// needs" reading or "tap I can't get there", tilting up to the roof or sky, and a server's
    /// own words, which this screen can't judge. Nor the overhead question, a request already
    /// seen, or coaching that replaces the request.
    private var aims: Bool {
        guard let gap = state.gap, !asking, !askingEnd, refusal == nil, !gap.isSatisfied else { return false }
        if let coaching, ScanCopy.coachingReplacesTask(coaching) { return false }
        if case .groundNearCandidate = gap.reason { return true }
        return false
    }

    /// As on the walk: tracking problems and standing past an end replace the request, and the
    /// capture gate's coaching rides along under it (`ScanCopy.withCoaching`). The dark coaching
    /// can stay up for a whole night request, and replacing the card hid the request and "Got it,
    /// thanks" for as long as it did (field test 4.1, run 3).
    private var instruction: Instruction {
        if asking { return ScanCopy.overheadQuestion }
        if askingEnd, let side = state.endQuestion { return ScanCopy.endQuestion(side) }
        if let coaching, ScanCopy.coachingReplacesTask(coaching) { return ScanCopy.coaching(coaching, meterPhoto: meterPhoto != nil) }
        guard let gap = state.gap else { return ScanCopy.withCoaching(ScanCopy.guidance(.gap), coaching) }
        if gap.isSatisfied {
            return Instruction(title: "Got it, thanks", detail: followUps > 0 ? "Updating your result." : "That's the view we needed.")
        }
        // "Wall ends here" marked nothing: why, and what to do (B-06, B-12).
        if let refusal, let side = gap.pastEndSide {
            return ScanCopy.withCoaching(ScanCopy.endMarkRefusal(refusal, asked: side), coaching)
        }
        return ScanCopy.withCoaching(ScanCopy.gap(gap), coaching)
    }

    /// The meter close-up, shown with "Point at the meter like this." while the phone finds its
    /// place again; nil when the close-up was skipped.
    private var meterPhoto: CGImage? {
        if case .captured(let image) = state.closeUp { return image }
        return nil
    }

    /// The close-up shows only while the card says "Point at the meter like this.", not under
    /// the overhead question, which outranks the coaching.
    private var showsMeterPhoto: Bool {
        meterPhoto != nil && instruction == ScanCopy.coaching(.relocalizing, meterPhoto: true)
    }

    /// The coaching on the card, marked with its symbol (`tone`). Once the view is in, the
    /// capture gate's coaching has nothing left to ask for, so only coaching that replaces the
    /// card still shows.
    private var coaching: Coaching? {
        guard let coaching = state.coaching else { return nil }
        if state.gap?.isSatisfied == true, !ScanCopy.coachingReplacesTask(coaching) { return nil }
        return coaching
    }
}

private struct GapProgress: View {
    var gap: GapRequest

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: gap.isSatisfied ? "checkmark.circle.fill" : "scope")
                .font(.title3.weight(.bold))
                .foregroundStyle(gap.isSatisfied ? Palette.covered : Palette.caution)
                .contentTransition(.symbolEffect(.replace))
            ProgressView(value: min(max(gap.progress, 0), 1))
                .tint(gap.isSatisfied ? Palette.covered : Palette.caution)
                .animation(.easeOut(duration: 0.25), value: gap.progress)
            Text("\(Int((min(max(gap.progress, 0), 1) * 100).rounded()))%")
                .font(Typeface.caption.monospacedDigit())
                .foregroundStyle(Palette.chalk)
                .frame(minWidth: 40, alignment: .trailing)
        }
        .padding(.horizontal, 16)
        .frame(minHeight: 52)
        .background(ScrimShape.capsule)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(gap.isSatisfied ? "View captured" : "Captured so far")
        .accessibilityValue("\(Int((min(max(gap.progress, 0), 1) * 100).rounded())) percent")
        .accessibilityIdentifier("gap.progress")
    }
}

/// The big green check that lands when a requested view is complete.
struct SuccessBadge: View {
    var body: some View {
        Image(systemName: "checkmark")
            .font(.system(size: 60, weight: .black))
            .foregroundStyle(.white)
            .frame(width: 128, height: 128)
            .background(Palette.covered, in: .circle)
            .overlay(Circle().strokeBorder(.white.opacity(0.8), lineWidth: 4))
            .shadow(color: .black.opacity(0.3), radius: 16, y: 6)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}
