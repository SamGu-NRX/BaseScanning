import HouseScanKit
import simd
import SwiftUI

/// "See it on your wall": the battery drawn onto the live camera at the chosen spot, with the
/// cable run from the meter and the clearance footprint tinted by outcome. This screen projects
/// it from the meter-anchored wall frame (`BatteryOverlay`) unless the engine has seen the AR
/// scene drawing it (`state.resultInCamera`). Either way it stays put as the homeowner moves.
/// While the spot is off screen a chevron at the edge points toward it.
struct ResultARScreen: View {
    let state: ScanViewState
    let actions: any ScanActions

    @State private var appeared = false
    /// The open camera between the card and Done, for the chevron (`CameraChrome`).
    @State private var cameraWindow = CameraWindow()
    /// What the chevron can do in the open camera without the card's note
    /// (`SpotDirection.Outcome`); the card's line follows from it and from Details.
    @State private var spotOutcome: SpotDirection.Outcome? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            if state.tracking == .normal, let projection = state.projection, let wall = state.wall, let result = state.result {
                if state.resultInCamera {
                    // The AR scene draws the result in the camera view; this only names it for
                    // VoiceOver and the UI tests, as the overlay below does.
                    Color.clear
                        .allowsHitTesting(false)
                        .accessibilityElement()
                        .accessibilityLabel(Self.overlayLabel(result))
                        .accessibilityAddTraits(.isImage)
                        .accessibilityIdentifier("ar.overlay")
                } else {
                    BatteryOverlay(projection: projection, wall: wall, result: result, rise: appeared ? 1 : 0)
                        .ignoresSafeArea()
                        .accessibilityElement()
                        .accessibilityLabel(Self.overlayLabel(result))
                        .accessibilityAddTraits(.isImage)
                        .accessibilityIdentifier("ar.overlay")
                }
                if let spot = result.spotCenter(on: wall) {
                    // Read in this body, as the walk reads its window, so a scroll or a closed
                    // Details always reaches the chevron and the card. Read inside the chevron's
                    // own geometry closure, a Details closed at AX5 left the card saying the
                    // camera was below for good (screenStates run 38687149601 at 602bd92).
                    SpotDirection(projection: projection, spot: spot, window: cameraWindow.noteFreeFrame) { outcome in
                        if spotOutcome != outcome { spotOutcome = outcome }
                    }
                }
            }
            CameraChrome(
                instruction: visibleInstruction,
                isReplay: state.isReplay,
                isAutopilot: state.isAutopilot,
                cameraWindow: cameraWindow,
                aims: showsSpot,
                noteTakesCameraRoom: true
            ) {
                Button("Done") { actions.closeAR() }
                    .buttonStyle(.primary)
                    .accessibilityIdentifier("action.closeAR")
            }
        }
        .onAppear {
            withAnimation(reduceMotion ? .easeOut(duration: 0.2) : .spring(duration: 0.7, bounce: 0.15)) { appeared = true }
        }
    }

    /// Whether the spot is on the wall in view: the phone tracks and the result has a spot. Then
    /// the homeowner is looking at the spot, so at the accessibility text sizes the card folds
    /// its detail (the placement, and what the fit still needs) under Details and leaves the
    /// camera open (`CameraChrome.aims`). At AX-XXXL the full card covered nearly the whole
    /// camera (run 37162669527, resultAR-candidate-settled-AX5). "Point at your meter" while
    /// tracking is lost keeps every word in view: it is what to do, and nothing is drawn yet.
    private var showsSpot: Bool {
        state.tracking == .normal && state.result?.spot != nil
    }

    private static func overlayLabel(_ result: ResultPresentation) -> String {
        guard result.spot != nil else { return "The cable run and clearances, drawn on your wall" }
        return result.spotIsClean
            ? ScanCopy.proposedSpotOverlay
            : "An outline of the spot an installer needs to check, drawn on your wall"
    }

    private var visibleInstruction: Instruction {
        var copy = instruction
        let note = SpotDirection.note(spotOutcome, detailsShown: cameraWindow.detailsShown)
        switch showsSpot ? note : nil {
        case .turn(let direction)?: copy.note = ScanCopy.proposedSpotOffScreen(turn: direction)
        case .cameraBelow?: copy.note = ScanCopy.cameraBelowDetails
        case nil: break
        }
        return copy
    }

    private var instruction: Instruction {
        if state.tracking != .normal {
            return Instruction(title: "Point at your meter", detail: "The battery comes back once your phone finds its place.")
        }
        guard let result = state.result, result.spot != nil else {
            return Instruction(title: "Point at your meter", detail: nil)
        }
        // The same answer as the result screen: a possible spot says it's possible and what it
        // still needs; any other spot an installer still has to check says so.
        let candidate = result.answer == .candidate
        let title = candidate ? ScanCopy.headline(.candidate) : "The spot an installer needs to check"
        let placement = ScanCopy.placement(result)
        guard !result.isSample else {
            // A sample spot drawn on the homeowner's real wall must not pass for their result.
            return Instruction(title: "Example spot, not your result", detail: ["No server checked this scan.", placement].compactMap { $0 }.joined(separator: " "))
        }
        let onSite = candidate ? "An installer needs to check the fit on site." : nil
        return Instruction(title: title, detail: [placement.map { "\($0)." }, onSite].compactMap { $0 }.joined(separator: " "))
    }
}

extension ResultPresentation {
    /// The middle of the battery on `wall`, or of the footprint outline on the ground for a spot
    /// that isn't a clean fit (`ResultMarkLayout.focusHeight`), in world meters; nil without a
    /// spot. The point "See it on your wall" has to get on screen: the edge chevron points to it
    /// while it is off screen, and the engine checks the AR scene puts it in view
    /// (`LiveCapture.resultIsDrawn`).
    func spotCenter(on wall: WallGeometry) -> SIMD3<Float>? {
        guard let spot else { return nil }
        let middle = (spot.span.lowerBound + spot.span.upperBound) / 2
        let height = ResultMarkLayout.focusHeight(mark: ResultMarkLayout.spotMark(spotIsClean: spotIsClean), batteryHeight: spot.height)
        return wall.world(s: middle, height: height, out: spot.offsetFromWall + spot.depth / 2)
    }
}

/// An edge chevron toward the battery spot while its middle is off screen, with words saying
/// what it points at. Nothing while the spot is in view.
private struct SpotDirection: View {
    /// The card's line when the camera can't show the spot or the chevron.
    enum Note: Equatable {
        /// Details is closed and the open camera is too short for the chevron and its words:
        /// the way to turn, which the card shows outside Details at every text size.
        case turn(AimDirection)
        /// Details is open and the camera it leaves can't show the spot or the chevron: less
        /// camera on screen than the chevron's disc, as on the walk
        /// (`WallWalkScreen.detailsCoverCamera`), or too little for the chevron's words.
        /// Closing Details brings the camera back, so the card says that and gives no direction.
        case cameraBelow
    }

    /// What the chevron can do in the open camera without the card's note.
    struct Outcome: Equatable {
        /// The way to turn when the spot is off screen and the chevron can't be drawn.
        var unshown: AimDirection?
        /// Less of that camera on screen than the chevron's disc.
        var cameraCovered: Bool
    }

    var projection: CameraProjection
    var spot: SIMD3<Float>
    /// The open camera between the card and Done as it would be without the card's note
    /// (`CameraWindow.noteFreeFrame`), in global coordinates; nil until the chrome measures it.
    /// The spot counts as in view only there, and the chevron keeps to it. Fixed bands stand in
    /// until then. Equal to the measured camera while the card has no note.
    var window: CGRect?
    /// Called with what the chevron can do (`Outcome`), nil once this view is gone.
    var onOutcome: (Outcome?) -> Void

    /// The chevron's words as laid out at the current text size. Measured from an unseen copy
    /// that is always laid out, so the chevron can be placed before it first shows; the guess
    /// stands in for the first layout pass only.
    @State private var labelHeight: CGFloat = 36
    @State private var previousDirection: AimDirection?
    /// This view's frame, the whole screen, in global coordinates.
    @State private var screenFrame: CGRect = .zero

    enum Layout: Equatable {
        case none
        case chevron(CGPoint, angle: Angle, label: CGPoint, direction: AimDirection)
        case unshown(AimDirection)

        var direction: AimDirection? {
            switch self {
            case .none: nil
            case .chevron(_, _, _, let direction), .unshown(let direction): direction
            }
        }

        var unshownDirection: AimDirection? {
            if case .unshown(let direction) = self { direction } else { nil }
        }
    }

    var body: some View {
        // Worked out in this body from its inputs, not in a geometry closure, so every change of
        // the window reaches the outcome.
        let band = WayfindingOverlay.verticalBand(window, origin: screenFrame.origin, height: screenFrame.height)
        let layout = screenFrame.isEmpty ? Layout.none
            : Self.layout(projection: projection, spot: spot, in: screenFrame.size, band: band, labelHeight: labelHeight, previous: previousDirection)
        // Invariant: the card's line follows from this outcome and Details only, and the
        // outcome from the spot and the camera without the note, so showing the line changes
        // nothing it was decided from and its own height can't keep it up. Judged against the
        // camera it shrinks, a five-line note at AX5 covered the spot, stayed, and pushed Done
        // off screen (CI run of #241 at d5d137ed).
        let outcome = Outcome(unshown: layout.unshownDirection,
                              cameraCovered: !screenFrame.isEmpty && band.map { $0.bottom - $0.top < WayfindingOverlay.arrowDiameter } == true)
        ZStack {
            label
                .hidden()
                .accessibilityHidden(true)
                .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { height in
                    if abs(height - labelHeight) > 0.5 { labelHeight = height }
                }
            if case let .chevron(point, angle, label, direction) = layout {
                ZStack {
                    TargetMarker(placement: .offScreen(point, angle: angle))
                    self.label
                        .position(label)
                }
                .frame(width: screenFrame.width, height: screenFrame.height)
                .accessibilityElement(children: .ignore)
                // The way to turn in words: below AX5 the card no longer says it beside a drawn
                // chevron, so this is all VoiceOver has.
                .accessibilityLabel(ScanCopy.proposedSpotOffScreen(turn: direction))
                .accessibilityIdentifier("ar.spotDirection")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .ignoresSafeArea()
        .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { screenFrame = $0 }
        .onChange(of: layout.direction, initial: true) { _, direction in previousDirection = direction }
        .onChange(of: outcome, initial: true) { _, outcome in onOutcome(outcome) }
        .onDisappear { onOutcome(nil) }
        .allowsHitTesting(false)
    }

    private var label: some View {
        Text(ScanCopy.proposedSpotThisWay)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Palette.chalk)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            // Solid, as the aim ring's legend: see-through black over a bright wall failed the
            // accessibility audit's contrast check there.
            .background(ScrimShape.capsule)
            .frame(maxWidth: 220)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// The gap between the chevron's disc and its words.
    static let labelGap: CGFloat = 6

    /// The card's line for `outcome`. With Details closed, the way to turn whenever the chevron
    /// can't be drawn, never folded away. With Details open, only `Note.cameraBelow`: turning
    /// can't bring back a camera that Details covers. It shows whenever the chevron can't be
    /// drawn as well, or an off-screen spot with Details open and a camera between 52 pt and
    /// the chevron's height had neither (review of #241).
    static func note(_ outcome: Outcome?, detailsShown: Bool) -> Note? {
        guard let outcome else { return nil }
        guard detailsShown else { return outcome.unshown.map(Note.turn) }
        return outcome.unshown != nil || outcome.cameraCovered ? .cameraBelow : nil
    }

    /// Where the chevron goes, which way it points and where its words sit; `.none` while the
    /// spot is in view, `.unshown` with the way to turn when the open camera can't hold them.
    /// `labelHeight` is the words' measured height: at the accessibility sizes they wrap to
    /// several lines, and a fixed allowance let them run under Done (review of #241).
    ///
    /// In view means in the open camera between the card and Done, as measured
    /// (`CameraWindow`): a spot under the card or the button can't be seen, so it gets the
    /// chevron. The fixed bands before the chrome reports were right only at the default text
    /// size; from xxxLarge up the card passes them and covered both the spot and the chevron
    /// (review of #132 and #137). The chevron keeps to the same window, inset as the walk's
    /// edge arrows are (`WayfindingOverlay.lane`), with room for its words left under it.
    static func layout(
        projection: CameraProjection, spot: SIMD3<Float>, in size: CGSize, band: WayfindingOverlay.Band?, labelHeight: CGFloat, previous: AimDirection?
    ) -> Layout {
        let disc = WayfindingOverlay.arrowDiameter
        let labelRoom = labelGap + labelHeight
        let clear = band.map { CGRect(x: 24, y: $0.top, width: size.width - 48, height: $0.bottom - $0.top) }
            ?? CGRect(x: 24, y: 260, width: size.width - 48, height: max(size.height - 260 - 160, 80))
        // A spot that was off screen (`previous`) comes back into view only a margin inside the
        // window, so at its edge the card's note doesn't come and go every frame.
        guard let direction = OpenCameraDirection.toward(
            projection.viewPoint(for: spot, in: size), in: clear, fallback: projection.screenDirection(toward: spot),
            wasOffScreen: previous != nil) else { return .none }
        let namedDirection = WayfindingOverlay.direction(of: .radians(atan2(direction.dy, direction.dx)), previous: previous)
        let lane: CGRect
        if let band {
            guard band.bottom - band.top >= disc + labelRoom else {
                return .unshown(namedDirection)
            }
            // The words end by the band's bottom. A band too short for the inset lane leaves its
            // middle line, where the guard above still fits the disc and the words.
            let bottomLimit = band.bottom - labelRoom
            let middle = (band.top + bottomLimit) / 2
            let top = min(band.top + WayfindingOverlay.arrowInset, middle)
            let bottom = max(bottomLimit - WayfindingOverlay.arrowInset, middle)
            lane = CGRect(x: 40, y: top, width: max(size.width - 80, 0), height: bottom - top)
        } else {
            lane = CGRect(x: 40, y: 260, width: size.width - 80, height: max(size.height - 260 - 300, 80))
        }
        let tx = direction.dx == 0 ? CGFloat.infinity : lane.width / 2 / abs(direction.dx)
        let ty = direction.dy == 0 ? CGFloat.infinity : lane.height / 2 / abs(direction.dy)
        let t = min(tx, ty)
        let point = CGPoint(x: lane.midX + direction.dx * t, y: lane.midY + direction.dy * t)
        let label = CGPoint(x: min(max(point.x, 120), max(size.width - 120, 120)), y: point.y + disc / 2 + labelGap + labelHeight / 2)
        return .chevron(point, angle: .radians(atan2(direction.dy, direction.dx)), label: label, direction: namedDirection)
    }
}

/// Canvas drawing of the battery box, cable and footprint. `rise` 0...1 lifts the box out of the
/// ground for the entrance. A spot that isn't a clean fit gets only the outline of its footprint.
struct BatteryOverlay: View, Animatable {
    var projection: CameraProjection
    var wall: WallGeometry
    var result: ResultPresentation
    var rise: Double

    /// With Reduce Motion the box stands at full height from the start and `rise` only fades it in.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Lets `withAnimation` interpolate the rise; a Canvas alone would jump to the end value.
    var animatableData: Double {
        get { rise }
        set { rise = newValue }
    }

    var body: some View {
        Canvas { context, size in
            let geometry = WallProjection(projection: projection, wall: wall, size: size)
            drawClearances(in: &context, geometry)
            drawCable(in: &context, geometry)
            if let spot = result.spot {
                switch ResultMarkLayout.spotMark(spotIsClean: result.spotIsClean) {
                case .battery:
                    drawShadow(spot, in: &context, geometry)
                    drawBox(spot, in: &context, geometry)
                case .outline:
                    drawOutline(spot, in: &context, geometry)
                }
            }
        }
        .allowsHitTesting(false)
    }

    private func drawClearances(in context: inout GraphicsContext, _ geometry: WallProjection) {
        for zone in result.clearances {
            guard let quad = geometry.groundQuad(s: zone.span, out: 0...zone.depth, height: 0.01) else { continue }
            let color = Palette.zone(zone.outcome)
            context.fill(quad, with: .color(color.opacity(0.28 * rise)))
            context.stroke(quad, with: .color(color.opacity(0.9 * rise)), style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
        }
    }

    private func drawCable(in context: inout GraphicsContext, _ geometry: WallProjection) {
        let points = result.cableRoute.compactMap { geometry.point(s: $0.x, height: $0.y, out: 0.03) }
        guard points.count >= 2 else { return }
        var line = Path()
        line.addLines(points)
        context.stroke(line, with: .color(.white.opacity(0.9)), style: StrokeStyle(lineWidth: 8, lineCap: .round, lineJoin: .round))
        context.stroke(line, with: .color(Palette.signal), style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
    }

    /// A spot that isn't a clean fit: the dashed outline of its footprint on the ground and no
    /// battery, as the result card draws it (`ResultScene3D`).
    private func drawOutline(_ spot: BatterySpot, in context: inout GraphicsContext, _ geometry: WallProjection) {
        let out = spot.offsetFromWall...(spot.offsetFromWall + spot.depth)
        guard let quad = geometry.groundQuad(s: spot.span, out: out, height: 0.02) else { return }
        let dash: [CGFloat] = [14, 9]
        context.stroke(quad, with: .color(.white.opacity(0.9 * rise)), style: StrokeStyle(lineWidth: 7, dash: dash))
        context.stroke(quad, with: .color(Palette.outcome(result.workingSpaceOutcome).opacity(rise)),
                       style: StrokeStyle(lineWidth: 4, dash: dash))
    }

    private func drawShadow(_ spot: BatterySpot, in context: inout GraphicsContext, _ geometry: WallProjection) {
        let out0 = spot.offsetFromWall - 0.05
        let out1 = spot.offsetFromWall + spot.depth + 0.12
        let span = (spot.span.lowerBound - 0.06)...(spot.span.upperBound + 0.1)
        guard let shadow = geometry.groundQuad(s: span, out: out0...out1, height: 0.005) else { return }
        context.drawLayer { layer in
            layer.addFilter(.blur(radius: 14))
            layer.fill(shadow, with: .color(.black.opacity(0.45 * rise)))
        }
    }

    private func drawBox(_ spot: BatterySpot, in context: inout GraphicsContext, _ geometry: WallProjection) {
        let height = reduceMotion ? spot.height : spot.height * Float(max(rise, 0.02))
        let fade = reduceMotion ? rise : 1
        let s0 = spot.span.lowerBound, s1 = spot.span.upperBound
        let o0 = spot.offsetFromWall, o1 = spot.offsetFromWall + spot.depth
        func corner(_ s: Float, _ h: Float, _ o: Float) -> SIMD3<Float> { wall.world(s: s, height: h, out: o) }

        struct Face {
            var corners: [SIMD3<Float>]
            /// Outward normal in world space.
            var normal: SIMD3<Float>
            var shade: Double
            var isFront: Bool
        }
        let up = SIMD3<Float>(0, 1, 0)
        // The piece of wall the box stands against, which is round a corner when the walk followed one.
        let middle = (s0 + s1) / 2
        let outward = wall.outward(atS: middle), along = wall.along(atS: middle)
        let faces = [
            Face(corners: [corner(s0, 0, o1), corner(s1, 0, o1), corner(s1, height, o1), corner(s0, height, o1)],
                 normal: outward, shade: 1.0, isFront: true),
            Face(corners: [corner(s0, height, o0), corner(s1, height, o0), corner(s1, height, o1), corner(s0, height, o1)],
                 normal: up, shade: 0.93, isFront: false),
            Face(corners: [corner(s0, 0, o0), corner(s0, 0, o1), corner(s0, height, o1), corner(s0, height, o0)],
                 normal: -along, shade: 0.8, isFront: false),
            Face(corners: [corner(s1, 0, o0), corner(s1, 0, o1), corner(s1, height, o1), corner(s1, height, o0)],
                 normal: along, shade: 0.8, isFront: false),
            Face(corners: [corner(s0, 0, o0), corner(s1, 0, o0), corner(s1, height, o0), corner(s0, height, o0)],
                 normal: -outward, shade: 0.7, isFront: false),
        ]
        // The box is convex, so drawing only the faces that point at the camera needs no depth
        // sorting (sorting by face centers can paint a hidden face over a visible one).
        for face in faces {
            let center = face.corners.reduce(SIMD3<Float>.zero, +) / Float(face.corners.count)
            guard simd_dot(projection.cameraPosition - center, face.normal) > 0,
                  let path = geometry.polygon(face.corners) else { continue }
            let base = Color(white: 0.97 * face.shade)
            context.fill(path, with: .color(base.opacity(0.96 * fade)))
            context.stroke(path, with: .color(.black.opacity(0.18 * fade)), lineWidth: 1)
            if face.isFront {
                // The blue light bar across the front, a third of the way down.
                let barTop = height * 0.72, barBottom = height * 0.66
                if let bar = geometry.polygon([
                    corner(s0 + (s1 - s0) * 0.2, barBottom, o1 + 0.002), corner(s1 - (s1 - s0) * 0.2, barBottom, o1 + 0.002),
                    corner(s1 - (s1 - s0) * 0.2, barTop, o1 + 0.002), corner(s0 + (s1 - s0) * 0.2, barTop, o1 + 0.002),
                ]) {
                    context.fill(bar, with: .color(Palette.signal.opacity(fade)))
                }
            }
        }
    }
}
