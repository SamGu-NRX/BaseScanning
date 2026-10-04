import SwiftUI

/// The root of every screen, switched on `state.phase`. Each screen's root view carries the
/// accessibility identifier `screen.<phase.rawValue>`.
///
/// The camera stays mounted across the camera phases, so moving from finding the meter to the
/// walk to a gap request never blinks the feed; only the chrome above it crossfades.
struct ScanRootView: View {
    let state: ScanViewState
    let actions: any ScanActions

    init(state: ScanViewState, actions: any ScanActions) {
        self.state = state
        self.actions = actions
    }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        stack
            .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.screen, value: state.phase)
            .preferredColorScheme(Self.showsCamera(state.phase) ? .dark : nil)
            .modifier(ScanHaptics(state: state))
            .environment(\.isPracticeScan, state.isPracticeScan)
    }

    private var stack: some View {
        ZStack {
            if Self.showsCamera(state.phase) {
                CameraBackdrop(feed: state.feed, actions: actions)
                    .transition(.opacity)
                if state.isPracticeScan {
                    PracticeMeterOverlay(state: state)
                }
                CameraEdgeShade()
            }
            // Camera screens crossfade over the live feed. The light screens cut: a crossfade
            // between two light screens leaves the incoming text half-transparent on a light
            // background, under 4.5:1 until it lands (the audit caught it on the result).
            screen
                .id(state.phase)
                .transition(Self.showsCamera(state.phase) ? .opacity : .identity)
        }
    }

    @ViewBuilder
    private var screen: some View {
        switch state.phase {
        case .onboarding:
            OnboardingScreen(state: state, actions: actions)
                .screenIdentifier(.onboarding)
        case .findMeter:
            FindMeterScreen(state: state, actions: actions)
                .screenIdentifier(.findMeter)
        case .meterCloseUp:
            MeterCloseUpScreen(state: state, actions: actions)
                .screenIdentifier(.meterCloseUp)
        case .wallWalk:
            WallWalkScreen(state: state, actions: actions)
                .screenIdentifier(.wallWalk)
        case .markFeatures:
            MarkFeaturesScreen(state: state, actions: actions)
                .screenIdentifier(.markFeatures)
        case .gapRequest:
            GapRequestScreen(state: state, actions: actions)
                .screenIdentifier(.gapRequest)
        case .uploading:
            UploadingScreen(state: state, actions: actions)
                .screenIdentifier(.uploading)
        case .spotConfirm:
            SpotConfirmScreen(state: state, actions: actions)
                .screenIdentifier(.spotConfirm)
        case .result:
            ResultScreen(state: state, actions: actions)
                .screenIdentifier(.result)
        case .resultAR:
            ResultARScreen(state: state, actions: actions)
                .screenIdentifier(.resultAR)
        case .unsupported:
            UnsupportedScreen(state: state, actions: actions)
                .screenIdentifier(.unsupported)
        }
    }

    static func showsCamera(_ phase: ScanPhase) -> Bool {
        switch phase {
        case .findMeter, .meterCloseUp, .wallWalk, .gapRequest, .resultAR, .markFeatures: true
        case .onboarding, .uploading, .spotConfirm, .result, .unsupported: false
        }
    }
}

/// Haptics for the moments that matter, fired on the same state change the screen animates:
/// a deliberate photo, the meter pinned, a mark placed or refused, a requested view done, the
/// result.
///
/// The result arrives with a light tap, whatever it says. `.success` there celebrated every
/// answer alike, a rejected wall included, and on a possible spot it said "done, it fits" when
/// the scan couldn't confirm the space (B17, B26). Coming back from the AR view taps nothing.
private struct ScanHaptics: ViewModifier {
    let state: ScanViewState

    // Steps of at most three: the five-modifier chain took 215 ms to type-check on Swift 6.4, and
    // CI's Swift 6.2 has failed on slower expressions before.
    func body(content: Content) -> some View {
        let captures = content
            .sensoryFeedback(.impact(weight: .light, intensity: 0.7), trigger: deliberateCaptureID) { _, new in new != nil }
            .sensoryFeedback(.success, trigger: state.phase, condition: Self.meterPinned)
            .sensoryFeedback(.impact(weight: .light), trigger: state.phase, condition: Self.resultArrived)
        return captures
            .sensoryFeedback(.success, trigger: state.gap?.isSatisfied ?? false) { _, new in new }
            .sensoryFeedback(.impact(weight: .medium), trigger: state.features.count) { old, new in new > old }
            .sensoryFeedback(.warning, trigger: state.marking?.refusal) { _, new in new != nil }
    }

    /// The walk takes a photo about every second; a tap for each would become noise the
    /// homeowner learns to ignore, so walk photos get only the counter's flash. The close-up
    /// and requested views are moments the homeowner is working toward, so they get a tap.
    private var deliberateCaptureID: Int? {
        guard let capture = state.lastCapture, capture.kind != .walk else { return nil }
        return capture.id
    }

    private static func meterPinned(_ old: ScanPhase, _ new: ScanPhase) -> Bool {
        old == .findMeter && new == .meterCloseUp
    }

    private static func resultArrived(_ old: ScanPhase, _ new: ScanPhase) -> Bool {
        old != .resultAR && new == .result
    }
}

private extension View {
    func screenIdentifier(_ phase: ScanPhase) -> some View {
        accessibilityElement(children: .contain)
            .accessibilityIdentifier("screen.\(phase.rawValue)")
    }
}
