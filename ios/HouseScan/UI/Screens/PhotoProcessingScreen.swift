import HouseScanKit
import SwiftUI

/// A photo-processing scan after it was sent: how far the upload got, then the service's answer or
/// why there is none. Separate facts, never a percentage or a time estimate. The service's words
/// are quoted as written and every answer stays provisional. Nothing here offers AR, more photos or
/// sending again, because this build can do none of them for photo processing; the screen says so
/// instead of showing a button that leads nowhere.
struct PhotoProcessingScreen: View {
    let state: ScanViewState
    let actions: any ScanActions
    @State private var confirmingStop = false

    init(state: ScanViewState, actions: any ScanActions) {
        self.state = state
        self.actions = actions
    }

    var body: some View {
        let status = state.photoProcessing
        let copy = ProcessingCopy.screen(status, isReplay: state.isReplay)
        CenteredScroll {
            VStack(spacing: 24) {
                ProcessingEmblem(symbol: copy.symbol, tone: copy.tone, working: copy.working)
                VStack(spacing: 10) {
                    Text(ProcessingCopy.screenLabel)
                        .font(Typeface.caption)
                        .textCase(.uppercase)
                        .tracking(0.6)
                        .foregroundStyle(Palette.muted)
                    Text(copy.title)
                        .font(Typeface.screenTitle)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("photo.state.\(copy.id)")
                    if !copy.fromService {
                        Text(copy.detail)
                            .font(Typeface.hint)
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if status?.standIn == true {
                    StandInNote()
                }
                if copy.fromService {
                    ServiceWords(text: copy.detail)
                }
                if case .answered(.needsMorePhotos(let more))? = status?.stage, !more.prompts.isEmpty {
                    RequestedViews(prompts: more.prompts)
                }
                if !copy.notes.isEmpty || copy.working || state.photoCaptureIsSynthetic {
                    ProcessingNotes(lines: notes(copy, retrying: status?.retrying == true))
                }
                VStack(spacing: 16) {
                    if copy.working, status?.consent == .granted {
                        Button(ProcessingCopy.stopSending) { confirmingStop = true }
                            .buttonStyle(.quiet)
                            .accessibilityIdentifier("action.stopSendingPhotos")
                    }
                    // The scan is kept: shared from here only when its archive exists.
                    if copy.id == "setupRefused", let scan = state.shareableScan {
                        ShareScanButton(url: scan)
                    }
                    if copy.id == "notSetUp" {
                        Button(ProcessingCopy.scanWithLegacy) { actions.scanWithLegacyInstead() }
                            .buttonStyle(.primary)
                            .accessibilityHint(ProcessingCopy.scanWithLegacyHint)
                            .accessibilityIdentifier("action.scanWithLegacy")
                    }
                    Group {
                        if copy.working || copy.id == "notSetUp" {
                            Button("Start over") { actions.startOver() }.buttonStyle(.quiet)
                        } else {
                            Button("Start over") { actions.startOver() }.buttonStyle(.primary)
                        }
                    }
                    .accessibilityHint("Starts a new scan. This scan isn't processed any further.")
                    .accessibilityIdentifier("action.startOver")
                }
            }
            .padding(24)
            .frame(maxWidth: 520)
        }
        .safeAreaInset(edge: .top) {
            // Stacked, each on its own full-width row, so the options chip's words can wrap rather
            // than be squeezed beside the badge (the audit found them clipped there).
            VStack(alignment: .leading, spacing: 4) {
                ModeBadge(isReplay: state.isReplay, isAutopilot: state.isAutopilot)
                DeveloperOptionsButton(scanBackend: state.scanBackend)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 24)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .confirmationDialog(ProcessingCopy.stopQuestion, isPresented: $confirmingStop, titleVisibility: .visible) {
            Button(ProcessingCopy.confirmStop, role: .destructive) { actions.stopSendingPhotos() }
                .accessibilityIdentifier("photo.confirmStop")
            Button(ProcessingCopy.keepSending, role: .cancel) {}
        } message: {
            Text(ProcessingCopy.stopDetail)
        }
    }

    private func notes(_ copy: ProcessingCopy.Screen, retrying: Bool) -> [String] {
        var lines = copy.notes
        if retrying { lines.insert(ProcessingCopy.retrying, at: 0) }
        let sent = switch state.photoProcessing?.consent {
        case .granted?, .withdrawn(_)?: true
        default: false
        }
        if state.photoCaptureIsSynthetic, sent { lines.insert(ProcessingCopy.syntheticCapture, at: 0) }
        if copy.working { lines.append(ProcessingCopy.closedAppLimit) }
        return lines
    }
}

/// The state's symbol in a tinted disc, as on the upload screen. It pulses only while the service
/// is working, and not at all with Reduce Motion.
private struct ProcessingEmblem: View {
    var symbol: String
    var tone: ProcessingCopy.Tone
    var working: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Circle()
                .fill(tint.opacity(0.12))
                .frame(width: 120, height: 120)
            Image(systemName: symbol)
                .font(.system(size: 46, weight: .semibold))
                .foregroundStyle(tint)
                .symbolEffect(.pulse, options: .repeating, isActive: working && !reduceMotion)
                .contentTransition(.symbolEffect(.replace))
        }
        .accessibilityHidden(true)
    }

    private var tint: Color {
        switch tone {
        case .attention: Palette.caution
        case .neutral, .proposed: Palette.signal
        }
    }
}

/// Says the answer is a fixture's, wherever one is on screen, as the Legacy sample says it is one.
private struct StandInNote: View {
    var body: some View {
        Label(ProcessingCopy.standInBadge, systemImage: "testtube.2")
            .font(Typeface.caption)
            .foregroundStyle(Palette.ink)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Palette.caution, in: .rect(cornerRadius: 12))
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("photo.standIn")
    }
}

/// The service's message, quoted and attributed, so it never reads as House Scan's own judgment.
private struct ServiceWords: View {
    var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(ProcessingCopy.serviceSaid)
                .font(Typeface.caption)
                .foregroundStyle(Palette.muted)
            Text(text)
                .font(Typeface.hint)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Palette.surface, in: .rect(cornerRadius: 16))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("photo.serviceMessage")
    }
}

/// The views the service asked for, each prompt as written.
private struct RequestedViews: View {
    var prompts: [CaptureResult.Prompt]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(prompts.enumerated()), id: \.offset) { _, prompt in
                VStack(alignment: .leading, spacing: 4) {
                    Text(prompt.title)
                        .font(Typeface.sectionTitle)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(prompt.body)
                        .font(Typeface.hint)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .background(Palette.surface, in: .rect(cornerRadius: 16))
                .accessibilityElement(children: .combine)
            }
        }
        .accessibilityIdentifier("photo.requestedViews")
    }
}

private struct ProcessingNotes: View {
    var lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(lines, id: \.self) { line in
                Text(line)
                    .font(Typeface.hint)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
