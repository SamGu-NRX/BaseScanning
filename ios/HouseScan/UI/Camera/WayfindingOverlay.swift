import HouseScanKit
import simd
import SwiftUI

/// Where to stand and where to aim: a blue dotted path on the ground toward the next place to
/// stand, and a ring on the point to aim at (or an edge arrow when it is off screen).
///
/// Each part sets its own accessibility: the path is hidden, and the filling ring is read (#81).
/// An arrow standing in for a filling ring is read like the ring, with its direction ("Spot to
/// show, above"); other arrows are hidden (`ArrowAccessibility`). A new part is read by
/// VoiceOver unless it hides itself. The ring's legend is drawn under the instruction card
/// (`CameraChrome.legend`), not here.
struct WayfindingOverlay: View {
    var projection: CameraProjection
    var wall: WallGeometry?
    var path: [SIMD3<Float>]
    var target: SIMD3<Float>?
    /// How far the aim task is toward done, 0...1 (`ScanViewState.aimProgress`): the ring fills
    /// with it. Nil when the target only marks a place.
    var progress: Double? = nil
    /// An aim step's target that was just completed, drawn as a full ring in place of `target`
    /// while it is on screen (`CameraOverlays` holds it for a moment).
    var completed: SIMD3<Float>? = nil
    /// Called with whether an aim step's filling ring has its target on screen, each time that
    /// changes (`CameraOverlays` shows the ring's legend once one has). On screen, not in the
    /// open camera: at the largest text sizes the card can cover the ring's place until the
    /// chrome scrolls, and the legend under the card still says what the ring is for.
    var onFillingRingShown: ((Bool) -> Void)? = nil
    /// The open camera between the instruction card and the actions (`CameraChrome`). The ring
    /// shows only inside it and the edge arrows keep to it. Until the chrome has been laid out,
    /// fixed bands stand in.
    var cameraWindow: CameraWindow? = nil
    /// Called with what stands for an aim step's filling target on screen: its ring, or an arrow
    /// and the way it points; nil when neither is drawn. The card's title follows it, so the
    /// words name what is drawn: the camera's angle to the target and the open part of the
    /// screen disagree near the card and the screen's edges (review of #120).
    var onAimMarker: ((AimMarker?) -> Void)? = nil

    /// The marker last reported (`onAimMarker`). A ring stays a ring up to the edge of where it
    /// can be drawn, but an arrow becomes a ring only `ringEntryMargin` inside it, so a hand-held
    /// phone with the target near that edge doesn't swap the two, or the card's title, several
    /// times a second.
    @State private var reported: AimMarker? = nil

    var body: some View {
        // Read here, in this view's body, so a change redraws this overlay alone.
        let clearArea = cameraWindow?.frame
        GeometryReader { proxy in
            let size = proxy.size
            let band = Self.verticalBand(clearArea, origin: proxy.frame(in: .global).origin, height: size.height)
            ZStack {
                Canvas { context, size in
                    drawPath(in: &context, size: size)
                }
                .accessibilityHidden(true)
                // One marker either way, so a ring that completes keeps its identity and animates
                // from its last fill to green.
                if let shown = marker(in: size, band: band) {
                    TargetMarker(placement: shown.placement, progress: shown.progress)
                }
                if fillingTargetOnScreen(in: size) {
                    Color.clear
                        .frame(width: 0, height: 0)
                        .onAppear { onFillingRingShown?(true) }
                        .onDisappear { onFillingRingShown?(false) }
                }
                Color.clear
                    .frame(width: 0, height: 0)
                    .onChange(of: aimMarker(in: size, band: band), initial: true) { _, marker in
                        reported = marker
                        onAimMarker?(marker)
                    }
                    .onDisappear { onAimMarker?(nil) }
            }
            .frame(width: size.width, height: size.height)
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    // MARK: Lane

    /// The top and bottom of the camera left open by the instruction card above and the
    /// buttons and map below, which are drawn over this layer, in this view's coordinates.
    struct Band: Equatable {
        var top: CGFloat
        var bottom: CGFloat
    }

    /// The edge arrow's disc (`TargetMarker`).
    static let arrowDiameter: CGFloat = 52
    /// How far inside the ring's bounds an arrow's target must come before the ring replaces it
    /// (`reported`). A display choice, about a fingertip; not measured on a phone.
    static let ringEntryMargin: CGFloat = 12
    /// How far inside the band the edge arrows sit: half the arrow's disc and a margin.
    static let arrowInset: CGFloat = 32

    /// The measured window in this view's coordinates, cut to the part on screen: scrolling the
    /// chrome at large text sizes can carry the window partly past an edge.
    static func verticalBand(_ clearArea: CGRect?, origin: CGPoint, height: CGFloat) -> Band? {
        clearArea.map {
            let top = min(max($0.minY - origin.y, 0), height)
            return Band(top: top, bottom: max(min($0.maxY - origin.y, height), top))
        }
    }

    // MARK: Path

    /// Dot spacing on the ground. A display choice: dense enough to read as a line at 3 m,
    /// sparse enough that each dot is distinct up close.
    private static let dotSpacing: Float = 0.22

    private func drawPath(in context: inout GraphicsContext, size: CGSize) {
        guard path.count >= 2 else { return }
        var samples: [SIMD3<Float>] = []
        var carry: Float = 0
        for (a, b) in zip(path, path.dropFirst()) {
            let length = simd_distance(a, b)
            guard length > 0 else { continue }
            var d = carry
            while d < length {
                samples.append(a + (b - a) * (d / length))
                d += Self.dotSpacing
            }
            carry = d - length
        }
        samples.append(path[path.count - 1])

        for (index, sample) in samples.enumerated() {
            let local = projection.cameraSpace(sample)
            guard local.z < -0.15, let point = projection.viewPoint(for: sample, in: size) else { continue }
            let pointsPerMeter = CGFloat(projection.intrinsics.x) * projection.scale(in: size) / CGFloat(-local.z)
            // Dots are 7 cm across on the ground, so they shrink with distance like paint would.
            let radius = min(14, max(2.5, pointsPerMeter * 0.035))
            let fade = index == samples.count - 1 ? 1 : min(1, 0.55 + Double(index) / Double(max(samples.count, 1)))
            let rect = CGRect(x: point.x - radius, y: point.y - radius * 0.62, width: radius * 2, height: radius * 1.24)
            context.fill(Path(ellipseIn: rect.insetBy(dx: -2, dy: -1.5)), with: .color(.white.opacity(0.7 * fade)))
            context.fill(Path(ellipseIn: rect), with: .color(Palette.signal.opacity(fade)))
        }
    }

    // MARK: Target

    /// Whether an aim step's target is where a filling ring would show without the card: the
    /// fixed rectangle used before the chrome reported its open camera.
    private func fillingTargetOnScreen(in size: CGSize) -> Bool {
        guard progress != nil, completed == nil, let target, let point = projection.viewPoint(for: target, in: size) else {
            return false
        }
        return Self.ringBounds(in: size, band: nil, radius: 0).contains(point)
    }

    /// The completed ring while it is on screen, else the target's ring or arrow.
    private func marker(in size: CGSize, band: Band?) -> (placement: TargetMarker.Placement, progress: Double?)? {
        if let completed {
            let held = placement(for: completed, in: size, band: band)
            if case .onScreen = held { return (placement: held, progress: 1.0) }
        }
        guard let target else { return nil }
        return (placement: placement(for: target, in: size, band: band, entryMargin: targetEntryMargin), progress: progress)
    }

    /// No margin while the target's ring is up, so it keeps its place to the edge, and none off
    /// an aim step, whose marker the card doesn't follow.
    private var targetEntryMargin: CGFloat {
        progress == nil || reported == .ring ? 0 : Self.ringEntryMargin
    }

    /// What the card can name for an aim target (`onAimMarker`). `.covered` means the chrome
    /// hides it; nil while a completed ring holds the screen or off an aim step.
    private func aimMarker(in size: CGSize, band: Band?) -> AimMarker? {
        guard progress != nil, let target else { return nil }
        // A completed ring holding the screen belongs to the step before (`marker`).
        if let completed, case .onScreen = placement(for: completed, in: size, band: band) { return nil }
        switch placement(for: target, in: size, band: band, entryMargin: targetEntryMargin) {
        case .onScreen:
            return .ring
        case .offScreen(_, let angle):
            let previous: AimDirection? = if case .arrow(let direction)? = reported { direction } else { nil }
            return .arrow(Self.direction(of: angle, previous: previous))
        case .hidden:
            return .covered
        }
    }

    /// The way an arrow at `angle` (screen, y down) points, in the card's terms. With the
    /// direction named before, the other axis must be `AimHint.axisBias` times larger to change
    /// it, as for the hint itself.
    static func direction(of angle: Angle, previous: AimDirection?) -> AimDirection {
        let prior: AimHint? = previous.flatMap {
            switch $0 {
            case .above: .above
            case .below: .below
            case .left: .left
            case .right: .right
            case .onScreen, .behind: nil
            }
        }
        switch OpenCameraDirection.classify(CGVector(dx: cos(angle.radians), dy: sin(angle.radians)), previous: prior) {
        case .above: return .above
        case .below: return .below
        case .left: return .left
        case .right: return .right
        case .onScreen, .behind: preconditionFailure("A screen direction must name an axis")
        }
    }

    private func placement(for target: SIMD3<Float>, in size: CGSize, band: Band?, entryMargin: CGFloat = 0) -> TargetMarker.Placement {
        // Less of the open camera on screen than an arrow's disc: at the largest text sizes the
        // chrome has scrolled it away, and anything drawn would sit on the card or the actions.
        if let band, band.bottom - band.top < Self.arrowDiameter { return .hidden }
        if let point = projection.viewPoint(for: target, in: size) {
            let scale = wall.flatMap { WallProjection(projection: projection, wall: $0, size: size).pointsPerMeter(at: target) }
            let radius = min(64, max(30, (scale ?? 90) * 0.28))
            if Self.ringBounds(in: size, band: band, radius: radius).insetBy(dx: entryMargin, dy: entryMargin).contains(point) {
                return .onScreen(point, radius: radius)
            }
            // Near the card or the actions a smaller ring can still fit whole.
            if let band, let fitted = Self.fittedRadius(at: point, in: size, band: band, margin: entryMargin), fitted < radius {
                return .onScreen(point, radius: fitted)
            }
            // On screen but under the card or the actions: the arrow in the open camera points
            // to where the spot is drawn, as the edge arrow does for a spot off screen, so the
            // homeowner tilts toward it and the ring comes into the open. The camera's own
            // direction to it starts at the optical axis, which can be the spot itself. An arrow
            // here was taken out at 22f78017 on a guess about the audit; the audit's finding
            // stayed without it (run 36768462318).
            if let band, point.y >= 0, point.y <= size.height, point.y < band.top || point.y > band.bottom {
                let lane = Self.lane(in: size, band: band)
                let offset = CGVector(dx: point.x - lane.midX, dy: point.y - lane.midY)
                let length = (offset.dx * offset.dx + offset.dy * offset.dy).squareRoot()
                guard length > 1 else { return .hidden }
                let direction = CGVector(dx: offset.dx / length, dy: offset.dy / length)
                // Stops a disc short of the spot, so it never lands past it pointing back.
                let edge = Self.arrowPoint(toward: direction, in: size, band: band)
                let toEdge = ((edge.x - lane.midX) * (edge.x - lane.midX) + (edge.y - lane.midY) * (edge.y - lane.midY)).squareRoot()
                let reach = min(toEdge, max(length - Self.arrowDiameter, 0))
                let arrow = CGPoint(x: lane.midX + direction.dx * reach, y: lane.midY + direction.dy * reach)
                return .offScreen(arrow, angle: .radians(atan2(direction.dy, direction.dx)))
            }
        }
        guard let direction = projection.screenDirection(toward: target) else {
            return .hidden
        }
        return .offScreen(Self.arrowPoint(toward: direction, in: size, band: band), angle: .radians(atan2(direction.dy, direction.dx)))
    }

    /// Where a ring's centre may be for the ring to show: with the whole ring, at the largest
    /// point of its pulse, on screen and in the open camera. Near the screen's edges the arrow
    /// takes over, since a half-visible ring would be ambiguous. Without a band, the fixed
    /// rectangle used before the chrome reported one, sized for the default text.
    static func ringBounds(in size: CGSize, band: Band?, radius: CGFloat) -> CGRect {
        guard let band else {
            return CGRect(x: 36, y: 150, width: size.width - 72, height: size.height - 330)
        }
        // `TargetMarker`'s plain ring pulses to 108 percent.
        let reach = radius * 1.08
        let inset = max(36, reach)
        return CGRect(
            x: inset,
            y: band.top + reach,
            width: max(size.width - 2 * inset, 0),
            height: max(band.bottom - band.top - 2 * reach, 0)
        )
    }

    /// The largest ring radius, down to the smallest ring's 30 pt, that fits whole at `point` in
    /// the band and on screen, pulse included, `margin` clear of their edges. Nil when not even
    /// that fits.
    static func fittedRadius(at point: CGPoint, in size: CGSize, band: Band, margin: CGFloat = 0) -> CGFloat? {
        let clearance = min(point.y - band.top, band.bottom - point.y, point.x, size.width - point.x) - margin
        let radius = clearance / 1.08
        return radius >= 30 ? radius : nil
    }

    /// Where the edge arrows keep: the band inset by `arrowInset`. A band too short for that
    /// leaves a lane of no height on the band's middle line, where the arrow overlaps the band
    /// least. Without a band, the fixed lane used before the chrome reported one.
    static func lane(in size: CGSize, band: Band?) -> CGRect {
        guard let band else {
            return CGRect(x: 40, y: 260, width: size.width - 80, height: max(size.height - 560, 80))
        }
        let middle = (band.top + band.bottom) / 2
        let top = min(band.top + arrowInset, middle)
        let bottom = max(band.bottom - arrowInset, middle)
        return CGRect(x: 40, y: top, width: max(size.width - 80, 0), height: bottom - top)
    }

    /// The edge arrow's centre: where a line from the middle of the lane toward the target
    /// leaves the lane.
    static func arrowPoint(toward direction: CGVector, in size: CGSize, band: Band?) -> CGPoint {
        let lane = lane(in: size, band: band)
        let center = CGPoint(x: lane.midX, y: lane.midY)
        let tx = direction.dx == 0 ? CGFloat.infinity : (lane.width / 2) / abs(direction.dx)
        let ty = direction.dy == 0 ? CGFloat.infinity : (lane.height / 2) / abs(direction.dy)
        let t = min(tx, ty)
        return CGPoint(x: center.x + direction.dx * t, y: center.y + direction.dy * t)
    }
}

/// What stands for an aim step's target on screen (`WayfindingOverlay.onAimMarker`).
enum AimMarker: Equatable {
    case covered
    case ring
    case arrow(AimDirection)
}

/// The aim ring, or an arrow at the screen edge pointing toward it. Also the arrow toward the
/// battery spot on "See it on your wall" (`ResultARScreen`).
struct TargetMarker: View {
    enum Placement: Equatable {
        case onScreen(CGPoint, radius: CGFloat)
        case offScreen(CGPoint, angle: Angle)
        case hidden
    }

    var placement: Placement
    /// 0...1 toward the aim task being done. The ring fills with it, then turns green with a tick
    /// at 1, like the meter photo's ring; nil draws the plain pulsing ring. A ring that never
    /// changed while it was held on target gave no sign of what it wanted (#81).
    var progress: Double? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        switch placement {
        case .onScreen(let point, let radius):
            Group {
                if let progress {
                    progressRing(min(max(progress, 0), 1), radius: radius)
                } else {
                    plainRing(radius: radius)
                }
            }
            .position(point)
            .transition(.opacity)
        case .offScreen(let point, let angle):
            // An arrow with a shaft: a bare chevron in a blue disc, turned to point down, can
            // read as a tick (#81).
            Image(systemName: "arrow.right")
                .font(.system(size: 22, weight: .heavy))
                .foregroundStyle(.white)
                .rotationEffect(angle)
                .frame(width: 52, height: 52)
                .background(Palette.signal, in: .circle)
                .overlay(Circle().strokeBorder(.white.opacity(0.85), lineWidth: 2))
                .shadow(color: .black.opacity(0.3), radius: 6, y: 2)
                .modifier(ArrowAccessibility(progress: progress, angle: angle))
                .position(point)
                .transition(.opacity)
        case .hidden:
            EmptyView()
        }
    }

    private func plainRing(radius: CGFloat) -> some View {
        ZStack {
            Circle()
                .strokeBorder(.white.opacity(0.9), lineWidth: 7)
            Circle()
                .strokeBorder(Palette.signal, lineWidth: 4)
            Circle()
                .fill(Palette.signal)
                .frame(width: 8, height: 8)
        }
        .frame(width: radius * 2, height: radius * 2)
        .phaseAnimator(reduceMotion ? [1.0] : [1.0, 1.08]) { ring, scale in
            ring.scaleEffect(scale)
        } animation: { _ in
            .easeInOut(duration: 0.9)
        }
        .accessibilityHidden(true)
    }

    /// No pulse: the fill is what moves. VoiceOver (and the UI tests) read its percent.
    private func progressRing(_ progress: Double, radius: CGFloat) -> some View {
        let done = progress >= 1
        return ZStack {
            Circle()
                .strokeBorder(.black.opacity(0.3), lineWidth: 11)
            Circle()
                .strokeBorder(.white.opacity(0.85), lineWidth: 7)
            Circle()
                .inset(by: 3.5)
                .trim(from: 0, to: done ? 1 : progress)
                .stroke(done ? Palette.covered : Palette.signal, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(reduceMotion ? nil : .linear(duration: 0.2), value: progress)
            if done {
                Image(systemName: "checkmark")
                    .font(.system(size: max(14, radius * 0.5), weight: .black))
                    .foregroundStyle(.white)
                    .padding(radius * 0.2)
                    .background(Palette.covered, in: .circle)
                    .transition(reduceMotion ? .opacity : .scale(scale: 0.8).combined(with: .opacity))
            } else {
                Circle()
                    .fill(Palette.signal)
                    .frame(width: 8, height: 8)
            }
        }
        .frame(width: radius * 2, height: radius * 2)
        .animation(Motion.pin, value: done)
        .accessibilityElement()
        .accessibilityLabel("Spot to show")
        .accessibilityValue(done ? "Captured" : "\(Int((progress * 100).rounded())) percent captured")
        .accessibilityIdentifier("aim.ring")
    }
}

/// An arrow standing in for a filling ring reads as the ring does, with the way to turn: at the
/// largest text sizes the ring is often under the card, and hiding the arrow too left VoiceOver
/// without the spot's progress. Other arrows (a plain target, the AR result's spot) stay hidden.
private struct ArrowAccessibility: ViewModifier {
    var progress: Double?
    var angle: Angle

    func body(content: Content) -> some View {
        if let progress {
            content
                .accessibilityElement()
                .accessibilityLabel("Spot to show, \(way)")
                .accessibilityValue(progress >= 1 ? "Captured" : "\(Int((min(max(progress, 0), 1) * 100).rounded())) percent captured")
                .accessibilityAddTraits(.updatesFrequently)
                .accessibilityIdentifier("aim.arrow")
        } else {
            content.accessibilityHidden(true)
        }
    }

    /// The arrow's direction in words, by its larger component. Screen y grows downward.
    private var way: String {
        let dx = cos(angle.radians), dy = sin(angle.radians)
        if abs(dx) >= abs(dy) { return dx > 0 ? "to the right" : "to the left" }
        return dy > 0 ? "below" : "above"
    }
}
