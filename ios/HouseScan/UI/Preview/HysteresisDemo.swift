import simd
import SwiftUI

/// Controlled poses for ScreenStatesUITests, using the real marker and result screen. Each
/// button advances one state so the test observes it before changing the target or tracking.
struct AimHysteresisDemo: View {
    @State private var stage = 0
    @State private var marker: AimMarker?

    private var target: SIMD3<Float> {
        // Exact changes smaller than a displayed distance must still start a fresh aim.
        SIMD3(stage >= 6 ? 0.002 : stage >= 2 ? 0.001 : 0, 0, 0)
    }

    private var step: GuidanceStep {
        stage >= 9 ? .aimAtWall(s: 0.4) : .aimAtGround(s: 0.4)
    }

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Palette.ink
                WayfindingOverlay(
                    projection: HysteresisDemoProjection.camera(showing: target, at: point(in: proxy.size), in: proxy.size),
                    path: [], target: target, step: step, progress: 0.5,
                    onAimMarker: { marker = $0 }
                )
                VStack {
                    Text(markerName)
                        .accessibilityIdentifier("demo.marker")
                    Spacer()
                    Button("Next pose") { stage += 1 }
                        .frame(minHeight: Metrics.minTarget)
                        .accessibilityIdentifier("demo.nextPose")
                }
                .padding(.vertical, 80)
            }
        }
        .ignoresSafeArea()
    }

    private func point(in size: CGSize) -> CGPoint {
        switch stage {
        case 0, 4, 7:
            CGPoint(x: -100, y: size.height / 2)
        case 1, 2, 8, 9:
            // Vertical is 1.1 times horizontal: above without memory, left with left-axis bias.
            CGPoint(x: -100, y: size.height / 2 - (size.width / 2 + 100) * 1.1)
        case 3:
            CGPoint(x: size.width / 2, y: size.height / 2)
        case 5, 6:
            // Inside the unmeasured ring bounds, but not the previous arrow's 12 pt margin.
            CGPoint(x: 40, y: size.height / 2)
        default:
            preconditionFailure("Aim hysteresis fixture has no pose \(stage)")
        }
    }

    private var markerName: String {
        switch marker {
        case .ring?: "ring"
        case .covered?: "covered"
        case .arrow(let direction)?: String(describing: direction)
        case nil: "none"
        }
    }
}

struct SpotHysteresisDemo: View {
    @State private var engine = DemoEngine(arguments: ["-uiDemoPhase", "resultAR", "-uiDemoFreeze"])
    @State private var stage = 0

    var body: some View {
        GeometryReader { proxy in
            let size = CGSize(width: proxy.size.width, height: proxy.size.height + proxy.safeAreaInsets.top + proxy.safeAreaInsets.bottom)
            ResultARScreen(state: engine.state, actions: engine)
                .background(Palette.ink)
                .overlay(alignment: .topTrailing) {
                    Button("Next pose") {
                        stage += 1
                        apply(in: size)
                    }
                    .frame(minHeight: Metrics.minTarget)
                    .accessibilityIdentifier("demo.nextPose")
                }
                .onAppear { apply(in: size) }
        }
    }

    private func apply(in size: CGSize) {
        guard let wall = engine.state.wall, let spot = engine.state.result?.spotCenter(on: wall) else {
            preconditionFailure("Spot hysteresis fixture requires a result with a spot")
        }
        // Exercise both ways a layout becomes unavailable, then a valid in-view layout.
        switch stage {
        case 0, 7:
            engine.state.tracking = .normal
            engine.state.projection = camera(x: -100, spot: spot, size: size)
        case 1, 8:
            engine.state.tracking = .limited(.excessiveMotion)
        case 2, 4, 6, 10:
            engine.state.tracking = .normal
            // Inside the clear window's x = 24 edge but outside its re-entry margin at x = 36.
            engine.state.projection = camera(x: 30, spot: spot, size: size)
        case 3:
            engine.state.projection = nil
        case 5, 9:
            engine.state.tracking = .normal
            engine.state.projection = camera(x: size.width / 2, spot: spot, size: size)
        default:
            preconditionFailure("Spot hysteresis fixture has no pose \(stage)")
        }
    }

    private func camera(x: CGFloat, spot: SIMD3<Float>, size: CGSize) -> CameraProjection {
        HysteresisDemoProjection.camera(showing: spot, at: CGPoint(x: x, y: size.height * 0.65), in: size)
    }
}

@MainActor
private enum HysteresisDemoProjection {
    /// Invert the portrait projection at a fixed 3 m depth, moving only the synthetic camera.
    static func camera(showing target: SIMD3<Float>, at point: CGPoint, in size: CGSize) -> CameraProjection {
        var projection = DemoScene.projection
        let pixel = projection.imagePixel(forViewPoint: point, in: size)
        let local = SIMD4<Float>(
            (pixel.x - projection.intrinsics.z) * 3 / projection.intrinsics.x,
            (projection.intrinsics.w - pixel.y) * 3 / projection.intrinsics.y,
            -3, 0)
        projection.cameraToWorld.columns.3 = SIMD4(target, 1) - projection.cameraToWorld * local
        return projection
    }
}
