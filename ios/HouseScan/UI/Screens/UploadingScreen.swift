import SwiftUI

/// "Checking your wall": a calm wait. Three steps tick off in order; progress is shown
/// separately from how complete the scan was (docs/05 section 2). A failure that sending again
/// can fix (offline, a server error) offers "Try again". A refused scan can't be fixed by sending
/// the same thing again, so it offers the review and a fresh start instead. Both offer "Share
/// scan" once the scan is packaged, so a scan the server never took can still reach the team.
///
/// When the check answers but still wants views the camera can take now, the engine goes
/// straight back to the camera for them. This screen says so first ("One more view to
/// finish") and adds that view as a fourth step, so the camera that follows reads as the next
/// step of the same list rather than a jump backward.
struct UploadingScreen: View {
    let state: ScanViewState
    let actions: any ScanActions

    /// Views the finished check wants from the camera before the result.
    private var followUps: Int {
        state.upload == .done ? state.followUps : 0
    }

    var body: some View {
        let copy = ScanCopy.upload(state.upload, sample: state.usesSampleResult, followUps: followUps)
        CenteredScroll {
            VStack(spacing: 28) {
                UploadEmblem(upload: state.upload, followsUp: followUps > 0)
                // The words swap at once and only the layout eases, as on `InstructionCard`. A
                // crossfade showed the old and new headlines half-transparent over each other on
                // every follow-up, when "Checking your wall" becomes "One more view to finish".
                VStack(spacing: 8) {
                    Text(copy.title)
                        .font(Typeface.screenTitle)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    if let detail = copy.detail {
                        Text(detail)
                            .font(Typeface.hint)
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .id(copy)
                .transition(.identity)
                .animation(Motion.text, value: copy)

                switch state.upload {
                case .failed:
                    VStack(spacing: 16) {
                        // Packets stay on disk, but the app has no way to reopen them after
                        // relaunch. Offer export only once the shareable packet exists.
                        Text(state.shareableScan == nil
                             ? "Keep this scan open to try again. House Scan can't reopen it after you close the app."
                             : "Keep this scan open to try again, or use Share scan to save a copy. House Scan can't reopen it after you close the app.")
                            .font(Typeface.hint)
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("upload.recoveryLimit")
                        Button("Try again") { actions.retryUpload() }
                            .buttonStyle(.primary)
                            .accessibilityIdentifier("action.retryUpload")
                        if let scan = state.shareableScan {
                            ShareScanButton(url: scan)
                        }
                    }
                case .rejected:
                    VStack(spacing: 16) {
                        Button("Back to review") { actions.backToReview() }
                            .buttonStyle(.primary)
                            .accessibilityHint("Your scan is kept. Check what you marked, then send it again.")
                            .accessibilityIdentifier("action.backToReview")
                        if let scan = state.shareableScan {
                            ShareScanButton(url: scan)
                        }
                        Button("Start over") { actions.startOver() }
                            .buttonStyle(.quiet)
                            .accessibilityHint("Starts a new scan and keeps your two most recent completed scans on this phone.")
                            .accessibilityIdentifier("action.startOver")
                    }
                default:
                    UploadSteps(upload: state.upload, sample: state.usesSampleResult, followUps: followUps)
                }
            }
            .padding(24)
            .frame(maxWidth: 520)
        }
        .overlay(alignment: .topLeading) {
            ModeBadge(isReplay: state.isReplay, isAutopilot: state.isAutopilot)
                .padding(.horizontal, 24)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }
}

private struct UploadEmblem: View {
    var upload: UploadState
    var followsUp: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Circle()
                .fill(tint.opacity(0.12))
                .frame(width: 132, height: 132)
            Image(systemName: symbol)
                .font(.system(size: 50, weight: .semibold))
                .foregroundStyle(tint)
                .symbolEffect(.pulse, options: .repeating, isActive: isWorking && !reduceMotion)
                .contentTransition(.symbolEffect(.replace))
        }
        .accessibilityHidden(true)
    }

    private var isWorking: Bool {
        switch upload {
        case .failed, .rejected, .done: false
        case .idle, .packaging, .uploading, .analyzing: true
        }
    }

    private var symbol: String {
        if followsUp { return "camera.viewfinder" }
        return switch upload {
        case .idle, .packaging: "list.bullet.rectangle"
        case .uploading: "arrow.up.circle"
        case .analyzing: "ruler"
        case .failed(_, let offline): offline ? "wifi.slash" : "exclamationmark.triangle"
        case .rejected: "exclamationmark.triangle"
        case .done: "checkmark.circle"
        }
    }

    private var tint: Color {
        switch upload {
        case .failed, .rejected: Palette.caution
        case .idle, .packaging, .uploading, .analyzing, .done: Palette.signal
        }
    }
}

private struct UploadSteps: View {
    var upload: UploadState
    /// No server: nothing is sent or checked, so the steps must not claim it.
    var sample: Bool
    /// Views the finished check wants next: they appear as a fourth step, the current one.
    var followUps: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            step(index: 0)
            step(index: 1)
            step(index: 2)
            if followUps > 0 {
                step(index: 3)
                    .transition(reduceMotion ? .opacity : .opacity.combined(with: .offset(y: -6)))
            }
        }
        .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.settle, value: followUps > 0)
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.surface, in: .rect(cornerRadius: 20, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("upload.steps")
    }

    private var current: Int {
        switch upload {
        case .idle, .packaging: 0
        case .uploading: 1
        case .analyzing: 2
        case .done: 3
        case .failed, .rejected: 1
        }
    }

    /// Each step says what it will do until it's done, then what it did.
    private func title(_ index: Int) -> String {
        switch index {
        case 0:
            return index < current ? "Measurements ready" : "Get measurements ready"
        case 1 where sample:
            return index < current ? "Sample loaded" : "Load the sample result"
        case 1:
            if case .uploading(let fraction) = upload {
                return "Sending measurements, \(Int((min(max(fraction, 0), 1) * 100).rounded()))%"
            }
            return index < current ? "Measurements sent" : "Send measurements"
        case 2:
            if sample { return index < current ? "Sample ready" : "Show the example" }
            return index < current ? "Clearances checked" : "Check clearances"
        default:
            return followUps == 1 ? "Take one more view" : "Take \(followUps) more views"
        }
    }

    private func step(index: Int) -> some View {
        HStack(spacing: 12) {
            ZStack {
                if index == 3 {
                    // The next step happens on the camera, not here: its symbol, not a spinner.
                    Image(systemName: "camera.viewfinder")
                        .foregroundStyle(Palette.signalText)
                } else if index < current {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Palette.covered)
                        .transition(reduceMotion ? .opacity : .scale(scale: 0.8).combined(with: .opacity))
                } else if index == current {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "circle")
                        .foregroundStyle(.tertiary)
                }
            }
            .font(.title3)
            .frame(width: 28, height: 28)
            .animation(Motion.settle, value: current)
            Text(title(index))
                .font(Typeface.hint)
                .foregroundStyle(index <= current ? Color.primary : Palette.muted)
                // The rolling digits move; with Reduce Motion they crossfade.
                .contentTransition(reduceMotion ? .opacity : .numericText())
        }
    }
}
