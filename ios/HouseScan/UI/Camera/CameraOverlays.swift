import SwiftUI

/// The overlays every walking screen draws over the camera, in paint order.
struct CameraOverlays: View {
    let state: ScanViewState
    var highlight: GapRequest?
    /// Set to the aim ring's legend while it shows, for the screen to draw under its card
    /// (`CameraChrome.legend`). Nil on screens with no legend.
    var cardLegend: Binding<String?>? = nil
    /// The open camera between the card and the actions (`CameraChrome.cameraWindow`), for the
    /// aim ring to stay inside. Passed through unread, so its changes don't redraw the fog.
    var cameraWindow: CameraWindow? = nil
    /// Set to what stands for the aim step's target on screen (`WayfindingOverlay.onAimMarker`),
    /// for the screen's card to name it. Nil on screens whose card doesn't.
    var aimMarker: Binding<AimMarker?>? = nil

    /// The step the aim ring's legend first showed with. The legend explains the first ring that
    /// fills and retires once that step ends (#81); it isn't needed on every ring after.
    @State private var legendStep: GuidanceStep? = nil
    @State private var legendRetired = false
    /// Whether a filling ring is on screen (`WayfindingOverlay.onFillingRingShown`): the legend
    /// explains the ring, so it shows only with it.
    @State private var fillingRingShown = false
    /// The aim step on screen, with its target, so the update that ends it can still score it.
    @State private var lastAim: AimSnapshot? = nil
    /// The target of an aim step that just completed, held on screen as a full green ring with a
    /// tick for `completedHold` (#81). The planner moves on in the same update that fills the
    /// strip, so without the hold the ring would vanish short of full and never turn green.
    @State private var completedTarget: SIMD3<Float>? = nil
    /// Counts completions, so a second one inside the hold restarts its timer.
    @State private var completions = 0

    /// How long a completed ring stays: long enough to read as done, short enough not to hold up
    /// the next step's marker. A display choice.
    private static let completedHold: Duration = .seconds(1)

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.self) private var environment

    var body: some View {
        ZStack {
            overlays
        }
        .animation(.easeOut(duration: 0.2), value: state.tracking == .normal)
        .onChange(of: AimSnapshot(step: state.guidance, target: state.target), initial: true) { old, new in
            if new.step != old.step {
                if let legendStep, new.step != legendStep { legendRetired = true }
                if let target = completed(old) {
                    completedTarget = target
                    completions += 1
                }
            }
            lastAim = state.aimProgress(for: new.step) == nil ? nil : new
        }
        .task(id: completions) {
            guard completedTarget != nil else { return }
            // A newer completion replaces this task; its cancellation must not clear the new ring.
            do { try await Task.sleep(for: Self.completedHold) } catch { return }
            completedTarget = nil
        }
        .onChange(of: shownLegend, initial: true) { _, line in
            if let cardLegend, cardLegend.wrappedValue != line { cardLegend.wrappedValue = line }
            // Counted as shown only once drawn, so it retires with the step it showed on.
            if line != nil, cardLegend != nil, legendStep == nil { legendStep = state.guidance }
        }
    }

    @ViewBuilder
    private var overlays: some View {
        // Hidden while tracking isn't normal: drawn from a pose the phone doesn't trust, the fog,
        // path and marks would sit in the wrong place (checklist T3). They fade back on recovery.
        if state.tracking == .normal, let projection = state.projection, let wall = state.wall {
            ZStack {
                fog(projection: projection, wall: wall)
                    .ignoresSafeArea()
                WallMarksOverlay(
                    projection: projection,
                    wall: wall,
                    features: state.features,
                    wallBandHeight: state.coverage.wallBandHeight
                )
                if state.marking == nil {
                    // While marking, the reticle is the only aim; the path and ring would compete.
                    WayfindingOverlay(
                        projection: projection,
                        wall: wall,
                        path: state.path,
                        target: state.target,
                        progress: state.aimProgress,
                        completed: heldTarget,
                        onFillingRingShown: { shown in fillingRingShown = shown },
                        cameraWindow: cameraWindow,
                        onAimMarker: { marker in
                            if let aimMarker, aimMarker.wrappedValue != marker { aimMarker.wrappedValue = marker }
                        }
                    )
                    .transition(.opacity)
                }
            }
        }
    }

    /// The live fog, with dots when the phone has depth. The frosted strip stands in while the
    /// shaders compile at launch, and for good if Metal fails, so unseen wall never shows clear.
    @ViewBuilder
    private func fog(projection: CameraProjection, wall: WallGeometry) -> some View {
        switch LiveFogSupport.shared.status {
        case let .ready(gpu):
            ZStack {
                LiveFogView(gpu: gpu, input: LiveFogInput(
                    projection: projection, wall: wall, coverage: state.coverage, highlight: highlight,
                    dots: state.depthAvailable ? state.liveDots : .empty, reduceMotion: reduceMotion,
                    requestedColor: requestedColor))
                FogMarks(coverage: state.coverage, wall: wall, projection: projection, highlight: highlight)
            }
            .transition(.opacity)
        case .preparing, .failed:
            FogOverlay(coverage: state.coverage, wall: wall, projection: projection, highlight: highlight)
        }
    }

    private var requestedColor: SIMD3<Float> {
        let resolved = Palette.caution.resolve(in: environment)
        return SIMD3(resolved.red, resolved.green, resolved.blue)
    }

    /// The completed ring to hold. The update that ends an aim step draws before `onChange`
    /// runs, so the outgoing step is scored here too; otherwise that frame would show the next
    /// step's marker between the filling ring and the green one.
    private var heldTarget: SIMD3<Float>? {
        if let completedTarget { return completedTarget }
        guard let lastAim, lastAim.step != state.guidance else { return nil }
        return completed(lastAim)
    }

    /// The target of `snapshot`'s aim step if the strip as it is now completes it. The strip
    /// holds the view that ended the step.
    private func completed(_ snapshot: AimSnapshot) -> SIMD3<Float>? {
        guard let target = snapshot.target, let progress = state.aimProgress(for: snapshot.step), progress >= 1 else {
            return nil
        }
        return target
    }

    /// The legend, while the first filling ring is on screen and short of full (#81).
    ///
    /// It goes under the instruction card, in the chrome's own stack, rather than beside the
    /// ring: beside it, it went behind the card at accessibility text sizes (the card grows
    /// and is drawn over the camera layers), and any layout that dropped it where there was no
    /// room left the people with the largest text without it. At the accessibility sizes it
    /// folds under the card's Details with the card's other how-to words (`CameraChrome.aims`).
    private var shownLegend: String? {
        guard fillingRingShown, !legendRetired, state.marking == nil, heldTarget == nil,
              let progress = state.aimProgress, progress < 1 else { return nil }
        return ScanCopy.aimRingLegend
    }
}

/// The step and its target together, so a change of step still has the target it ended with.
private struct AimSnapshot: Equatable {
    var step: GuidanceStep
    var target: SIMD3<Float>?
}
