import HouseScanKit
import SwiftUI

/// The frame shared by every camera screen: status row and one instruction at the top, the
/// camera open in the middle, actions within thumb reach at the bottom.
struct CameraChrome<Bottom: View>: View {
    var instruction: Instruction
    var tone: InstructionCard.Tone = .normal
    var reply: InstructionCard.Reply?
    /// See `InstructionCard.eyebrow`.
    var eyebrow: String?
    var photoCount: Int?
    var lastCaptureID: Int?
    var isReplay: Bool
    var isAutopilot: Bool
    /// Taps on the open camera area, in the camera view's coordinates (full screen, which is
    /// the window's global space). Nil when the screen has nothing to tap.
    var onCameraTap: ((CGPoint) -> Void)?
    /// A line under the card about something drawn on the camera: the aim ring's legend
    /// (`CameraOverlays`). On an aiming step at the accessibility sizes it folds under the
    /// card's Details instead (`aims`).
    var legend: String? = nil
    /// Given the open camera between the card and the actions, for the aim ring to stay inside
    /// (`WayfindingOverlay`). It moves when the chrome scrolls.
    var cameraWindow: CameraWindow? = nil
    /// Whether this step has the homeowner aim the camera, so at the accessibility text sizes
    /// the card folds its detail and the legend under "Details" (`InstructionCard.foldsDetail`)
    /// and leaves the camera open. Questions and refusals keep every word in view.
    var aims = false
    /// Whether the card's note takes its room out of the least open camera instead of pushing
    /// the actions down. For a screen whose note shows only when the camera has nothing to draw
    /// ("See it on your wall"): at AX5 its off-screen note is about 290 pt, and pushed below it
    /// Done left the screen (CI run of #241 at d5d137ed).
    var noteTakesCameraRoom = false
    @ViewBuilder var bottom: Bottom

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    /// The card's note's height, kept only for `noteTakesCameraRoom`.
    @State private var noteRoom: CGFloat = 0

    private var folds: Bool { aims && typeSize.isAccessibilitySize }

    /// The least open camera without a note (`CameraChromeLayout.minCameraWindow` when folded).
    private var leastCameraWindow: CGFloat { folds ? CameraChromeLayout.minCameraWindow : 0 }

    var body: some View {
        // One layout at every text size: the chrome fills the screen and, only when the largest
        // text makes it taller than the screen, scrolls instead of squeezing the words. The
        // scroll view would swallow taps meant for the camera, so camera taps are caught inside
        // it, behind the chrome.
        GeometryReader { proxy in
            ScrollView {
                stack
                    .frame(width: proxy.size.width)
                    .frame(minHeight: proxy.size.height)
                    .background {
                        if let onCameraTap {
                            Color.clear
                                .contentShape(.rect)
                                .onTapGesture(coordinateSpace: .global) { point in onCameraTap(point) }
                                .accessibilityHidden(true)
                        }
                    }
                    .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { content in
                        cameraWindow?.layout.content = content
                    }
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollIndicators(.hidden)
            .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { viewport in
                cameraWindow?.layout.viewport = viewport
            }
        }
    }

    private var stack: some View {
        VStack(spacing: 10) {
            HStack(alignment: .center) {
                ModeBadge(isReplay: isReplay, isAutopilot: isAutopilot)
                Spacer(minLength: 8)
                if let photoCount {
                    PhotoCounter(count: photoCount, lastCaptureID: lastCaptureID)
                }
            }
            .frame(minHeight: 36)
            InstructionCard(
                instruction: instruction, tone: tone, reply: reply, eyebrow: eyebrow,
                foldsDetail: folds, legend: folds ? legend : nil,
                onDetailsShown: { shown in cameraWindow?.detailsShown = shown },
                onNoteRoom: cameraWindow == nil ? nil : { room in
                    cameraWindow?.layout.noteRoom = room
                    if noteTakesCameraRoom, noteRoom != room { noteRoom = room }
                }
            )
            if let legend, !folds {
                legendLine(legend)
            }
            // The open camera between the card and the actions; the aim ring shows only inside
            // it (B-36). On an aiming step at the accessibility sizes it keeps at least
            // `minCameraWindow`, and the actions below scroll into reach.
            Spacer(minLength: max(leastCameraWindow - (noteTakesCameraRoom ? noteRoom : 0), 0))
                .onGeometryChange(for: CGRect.self, of: { $0.frame(in: .global) }) { window in
                    guard var layout = cameraWindow?.layout else { return }
                    layout.window = window
                    layout.minimumHeight = leastCameraWindow
                    cameraWindow?.layout = layout
                }
            bottom
        }
        // The legend's arrival resizes the stack. With Reduce Motion it lands at once, since a
        // shorter resize still moves everything under it.
        .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: legend)
        .padding(.horizontal, Metrics.edge)
        .padding(.top, 4)
        .padding(.bottom, 8)
    }

    /// One Text with the symbol inline, as the result's sample badge is: the audit reported a
    /// Label with fixedSize as partially supporting Dynamic Type.
    private func legendLine(_ text: String) -> some View {
        Text("\(Image(systemName: "scope")) \(text)")
            .font(Typeface.hint)
            .foregroundStyle(Palette.chalk)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(ScrimShape.rounded(14))
            .accessibilityLabel(text)
            .accessibilityIdentifier("aim.legend")
            .transition(.opacity)
    }
}

/// The open camera between the instruction card and the actions, in global coordinates.
/// `CameraChrome` measures it and writes its disclosure state. The overlay uses the frame; the
/// walk uses both to explain when open Details has pushed the camera below the screen.
@MainActor
@Observable
final class CameraWindow {
    private(set) var frame: CGRect?
    /// `frame` as it would be if the card had no note (`OpenCameraWithoutNote`). A note shown
    /// because of what the camera holds is judged against this, so its own height can't keep it
    /// up. Equal to `frame` while the card has no note.
    private(set) var noteFreeFrame: CGRect?
    var detailsShown = false

    /// What `CameraChrome` measures. Not observed itself: it changes on every scroll, and
    /// readers follow `frame` and `noteFreeFrame`.
    struct Layout: Equatable {
        var window: CGRect?
        var content: CGRect?
        var viewport: CGRect?
        /// How much taller the card's note makes the card (`InstructionCard.onNoteRoom`).
        var noteRoom: CGFloat = 0
        /// The least open camera the chrome keeps without a note.
        var minimumHeight: CGFloat = 0
    }

    @ObservationIgnored var layout = Layout() {
        didSet {
            guard layout != oldValue else { return }
            if frame != layout.window { frame = layout.window }
            let noteFree = layout.window.map { window in
                guard let content = layout.content, let viewport = layout.viewport else { return window }
                return OpenCameraWithoutNote.frame(window: window, content: content, viewport: viewport,
                                                   noteRoom: layout.noteRoom, minimumHeight: layout.minimumHeight)
            }
            if noteFreeFrame != noteFree { noteFreeFrame = noteFree }
        }
    }
}

/// `CameraChrome`'s layout constants, outside it because a generic type can't hold stored
/// static properties.
enum CameraChromeLayout {
    /// The least open camera on an aiming step at the accessibility sizes: room for the largest
    /// aim ring (64 pt radius, 108 percent at its pulse) with a margin. A layout choice checked
    /// against the iPhone 17 frames, not measured on a phone.
    static let minCameraWindow: CGFloat = 180
}

/// Reads the full-screen camera view size so buttons can send "the reticle" (nil point)
/// with the right `viewSize`.
struct CameraSizeReader: View {
    @Binding var size: CGSize

    var body: some View {
        GeometryReader { proxy in
            Color.clear
                .onAppear { size = proxy.size }
                .onChange(of: proxy.size) { _, newValue in size = newValue }
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Crosshair ring at the center of the camera: "the phone is pointing here".
struct Reticle: View {
    var diameter: CGFloat = 64

    var body: some View {
        ZStack {
            Circle().strokeBorder(.black.opacity(0.35), lineWidth: 6)
            Circle().strokeBorder(.white, lineWidth: 3)
            Circle().fill(.white).frame(width: 6, height: 6)
        }
        .frame(width: diameter, height: diameter)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
