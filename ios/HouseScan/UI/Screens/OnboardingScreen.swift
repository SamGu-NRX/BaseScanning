import SwiftUI

/// Four short pages before the camera opens: what happens and how long, the moves the walk asks
/// for, how the photos work, and staying safe. The last page asks for the camera.
struct OnboardingScreen: View {
    let state: ScanViewState
    let actions: any ScanActions

    @State private var page = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize

    private let pages: [OnboardingPage] = [
        OnboardingPage(
            title: "Let's find a spot for your battery",
            body: "Walk along the wall by your electric meter for about 2 minutes. Your phone measures as you go.",
            art: .walk
        ),
        // Testers met each move first as a card they couldn't decode (#85, #77, #81): this page
        // names them before the camera opens. The first two come first (`GuidancePlanner`); after
        // that the card asks for whatever the walk still needs, so no fixed order is promised.
        OnboardingPage(
            title: "What the walk asks for",
            body: "A card at the top of the screen asks for one of these at a time. After the first two, it asks for whatever the walk still needs.",
            moves: OnboardingScreen.moves,
            art: .moves
        ),
        // What leaves the phone is said before the camera is asked for: uploads carry the wall's
        // measurements; the photos stay in the app until the scan is started over.
        OnboardingPage(
            title: "Your phone takes the photos",
            body: "Just walk slowly. The haze on the wall clears as your phone sees it.",
            // The ring and the arrows carry most of the walk's guidance, and nothing said what
            // they were for (#81). Most rings only mark where to go or look; only a spot to show
            // fills, so the line covers both.
            guide: "A blue ring shows where to go or look. When it asks you to show a spot, it fills as your phone captures it and turns green when done. If it's off screen, an arrow at the edge points to it.",
            note: "Only the wall's measurements are sent. Completed scans and their photos stay on this phone until newer scans replace them.",
            art: .fog
        ),
        OnboardingPage(
            title: "Stay safe out there",
            body: nil,
            art: .safety
        ),
    ]

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if typeSize.isAccessibilitySize {
                    // The replay badge, Practice label and Skip squeezed the label into
                    // single syllables at AX5. Give Practice its own full-width row.
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            ModeBadge(isReplay: state.isReplay, isAutopilot: state.isAutopilot)
                            Spacer()
                            skipButton
                        }
                        DeveloperOptionsButton()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } else {
                    HStack {
                        ModeBadge(isReplay: state.isReplay, isAutopilot: state.isAutopilot)
                        DeveloperOptionsButton()
                        Spacer()
                        skipButton
                    }
                }
            }
            .frame(minHeight: Metrics.minTarget)
            .padding(.horizontal, 20)

            TabView(selection: $page) {
                ForEach(pages.indices, id: \.self) { index in
                    OnboardingPageView(page: pages[index], isActive: page == index)
                        .tag(index)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))

            VStack(spacing: 16) {
                PageDots(count: pages.count, current: page)
                if page == pages.count - 1 {
                    VStack(spacing: 8) {
                        Button {
                            actions.finishOnboarding()
                        } label: {
                            Text("\(Image(systemName: "camera.fill")) Allow camera")
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.vertical, 8)
                        }
                        .buttonStyle(.primary)
                        .accessibilityLabel("Allow camera")
                        .accessibilityHint("Your phone may ask to use the camera, then Motion & Fitness")
                        .accessibilityIdentifier("action.finishOnboarding")
                        if !typeSize.isAccessibilitySize {
                            OnboardingPermissionNote()
                        }
                    }
                    .transition(.opacity)
                } else {
                    Button("Next") {
                        withAnimation(reduceMotion ? nil : Motion.screen) { page += 1 }
                    }
                    .buttonStyle(.primary)
                    .accessibilityIdentifier("action.onboardingNext")
                    .transition(.opacity)
                }
            }
            .animation(Motion.screen, value: page)
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }

    @ViewBuilder
    private var skipButton: some View {
        if page < pages.count - 1 {
            Button("Skip") {
                withAnimation(reduceMotion ? nil : Motion.screen) { page = pages.count - 1 }
            }
            .font(Typeface.hint.weight(.semibold))
            .foregroundStyle(Palette.signalText)
            .frame(minWidth: Metrics.minTarget, minHeight: Metrics.minTarget)
            .accessibilityIdentifier("action.onboardingSkip")
        }
    }

    /// The moves page quotes the walk's own cards (`ScanCopy`), so a homeowner recognises each
    /// card as something they were told about (#85). "Wall ends here" is the button's label
    /// (`WallWalkScreen`). If the cards change, read these again.
    private static var moves: [Instruction] {
        let meter = ScanCopy.guidance(.holdOnMeter)
        let walk = ScanCopy.guidance(.walk(side: .right, remaining: nil))
        let stepBack = ScanCopy.guidance(.stepBack)
        let ground = ScanCopy.guidance(.aimAtGround(s: 0))
        let wall = ScanCopy.guidance(.aimAtWall(s: 0))
        let end = ScanCopy.guidance(.markEnd(side: .right))
        func sentences(_ parts: String?...) -> String {
            parts.compactMap(\.self).joined(separator: " ")
        }
        return [
            Instruction(title: "Aim at your meter", detail: meter.detail),
            // The ground by the meter comes before the walk (`GuidancePlanner.preferredTask`).
            // A ground cell counts only once it is seen from two places a step apart
            // (`coveringBaseline`); nothing said so, and the first tilt card blocked every run
            // of the field test (#77).
            Instruction(
                title: "Tilt down at your meter",
                detail: "\"\(ground.title).\" Later the card may ask for this again, or say \"\(wall.title).\" Some spots need a second look from a step to the side."
            ),
            Instruction(
                title: "Walk along the wall",
                detail: sentences(walk.detail, "If you're too close, the card says \"\(stepBack.title).\"")
            ),
            // The ring and the edge arrow in the picture are `TargetMarker` itself (`MovesArt`).
            Instruction(
                title: "Follow the blue ring",
                detail: "Put it on the spot the card names. An arrow at the screen edge means the spot is off screen."
            ),
            Instruction(title: "Tap where the wall ends", detail: sentences(end.detail, "The walk asks at both ends.")),
        ]
    }
}

private struct OnboardingPage {
    enum Art { case walk, moves, fog, safety }
    var title: String
    var body: String?
    /// Numbered steps under the body, one accessibility element each.
    var moves: [Instruction] = []
    /// A line about what the walk draws over the camera, set apart with the ring's icon.
    var guide: String? = nil
    /// A quieter line under the body, set apart with an icon.
    var note: String? = nil
    var art: Art
}

private struct OnboardingPageView: View {
    var page: OnboardingPage
    var isActive: Bool
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if page.art == .safety {
                    text
                    SafetyArt()
                    // At AX5 the note filled the fixed footer and squeezed out the safety
                    // content. Keep it in the page's scroll area at accessibility sizes.
                    if typeSize.isAccessibilitySize {
                        OnboardingPermissionNote()
                    }
                } else {
                    Group {
                        if page.art == .walk {
                            WalkArt(isActive: isActive)
                        } else if page.art == .moves {
                            MovesArt(isActive: isActive)
                        } else {
                            FogArt(isActive: isActive)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 260)
                    .accessibilityHidden(true)
                    text
                    if !page.moves.isEmpty {
                        MoveList(moves: page.moves)
                    }
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 8)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(page.title)
                .font(Typeface.screenTitle)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            if let body = page.body {
                Text(body)
                    .font(.system(.title3, design: .rounded, weight: .regular))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let guide = page.guide {
                Label(guide, systemImage: "scope")
                    .font(Typeface.hint)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                    .accessibilityIdentifier("onboarding.ring")
            }
            if let note = page.note {
                Label(note, systemImage: "lock.fill")
                    .font(Typeface.hint)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                    .accessibilityIdentifier("onboarding.privacy")
            }
        }
    }
}

private struct OnboardingPermissionNote: View {
    var body: some View {
        Text("Your phone may ask to use the camera, then Motion & Fitness, which lets it record air pressure with your scan.")
            .font(.footnote)
            .foregroundStyle(Palette.muted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("onboarding.permissions")
    }
}

private struct PageDots: View {
    var count: Int
    var current: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 8) {
            ForEach(0..<count, id: \.self) { index in
                Capsule()
                    .fill(index == current ? Palette.signal : Palette.unseen.opacity(0.45))
                    .frame(width: index == current ? 22 : 8, height: 8)
            }
        }
        // The current dot widens; with Reduce Motion it changes without the stretch.
        .animation(reduceMotion ? nil : Motion.settle, value: current)
        // The page view already announces its position; these dots are for sighted users.
        .accessibilityHidden(true)
    }
}

// MARK: - Illustrations

/// A wall drawn in the app's own vocabulary: siding lines, the meter as a blue dot.
private struct WallDrawing: View {
    var body: some View {
        Canvas { context, size in
            let wall = CGRect(x: 0, y: 0, width: size.width, height: size.height * 0.72)
            context.fill(Path(roundedRect: wall, cornerRadius: 18), with: .color(Color(red: 0.93, green: 0.91, blue: 0.87)))
            var siding = Path()
            var y = wall.minY + 22
            while y < wall.maxY - 6 {
                siding.move(to: CGPoint(x: wall.minX + 12, y: y))
                siding.addLine(to: CGPoint(x: wall.maxX - 12, y: y))
                y += 18
            }
            context.stroke(siding, with: .color(.black.opacity(0.06)), lineWidth: 1.5)
            let ground = CGRect(x: 0, y: wall.maxY + 8, width: size.width, height: size.height - wall.maxY - 8)
            context.fill(Path(roundedRect: ground, cornerRadius: 14), with: .color(Color(red: 0.80, green: 0.86, blue: 0.76)))
            // Meter
            let meter = CGRect(x: size.width * 0.44, y: wall.height * 0.38, width: size.width * 0.12, height: size.width * 0.15)
            context.fill(Path(roundedRect: meter, cornerRadius: 8), with: .color(Color(white: 0.62)))
            let dial = meter.insetBy(dx: meter.width * 0.2, dy: meter.height * 0.25).offsetBy(dx: 0, dy: -meter.height * 0.08)
            context.fill(Path(ellipseIn: dial), with: .color(Palette.signal))
        }
    }
}

private struct WalkArt: View {
    var isActive: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: reduceMotion || !isActive)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            // A slow walk from one end to the other and back: one lap every 6 s.
            let phase = reduceMotion ? 0.5 : (sin(t * .pi / 3) + 1) / 2
            GeometryReader { proxy in
                let size = proxy.size
                ZStack {
                    WallDrawing()
                    Canvas { context, size in
                        let y = size.height * 0.86
                        for i in 0..<11 {
                            let x = size.width * (0.08 + 0.084 * Double(i))
                            context.fill(Path(ellipseIn: CGRect(x: x - 4, y: y - 3, width: 8, height: 6)), with: .color(Palette.signal.opacity(0.85)))
                        }
                    }
                    Image(systemName: "iphone.gen3")
                        .font(.system(size: 34, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                        .padding(8)
                        .background(.white, in: .rect(cornerRadius: 12, style: .continuous))
                        .shadow(color: .black.opacity(0.15), radius: 8, y: 3)
                        .position(x: size.width * (0.14 + 0.72 * phase), y: size.height * 0.72)
                    Text("About 2 min")
                        .font(Typeface.caption)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Palette.ink, in: .capsule)
                        // Anchor the edge, not the centre: a growing caption otherwise
                        // extends past the illustration at accessibility text sizes.
                        .padding(12)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                }
            }
        }
    }
}

private struct FogArt: View {
    var isActive: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: reduceMotion || !isActive)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            // Cells clear left to right over 4 s, hold clear for 1.5 s, then fog rolls back in.
            let cycle = reduceMotion ? 2.4 : t.truncatingRemainder(dividingBy: 6.5)
            ZStack {
                WallDrawing()
                Canvas { context, size in
                    let wallHeight = size.height * 0.72
                    let columns = 8
                    let width = size.width / CGFloat(columns)
                    context.drawLayer { layer in
                        layer.addFilter(.blur(radius: 8))
                        for column in 0..<columns {
                            let clearAt = 0.4 + Double(column) * 0.45
                            let fade: Double
                            if cycle < clearAt { fade = 1 }
                            else if cycle < clearAt + 0.7 { fade = 1 - (cycle - clearAt) / 0.7 }
                            else if cycle > 5.8 { fade = min(1, (cycle - 5.8) / 0.7) }
                            else { fade = 0 }
                            let rect = CGRect(x: CGFloat(column) * width, y: -6 - (1 - fade) * 8, width: width + 1, height: wallHeight + 6)
                            layer.fill(Path(rect), with: .color(Color(white: 0.99).opacity(0.9 * fade)))
                        }
                    }
                }
                .clipShape(.rect(cornerRadius: 18))
            }
        }
    }
}

private struct SafetyArt: View {
    private let items: [(symbol: String, title: String, detail: String)] = [
        ("figure.stand", "Stay on the ground", "No ladders, roofs or climbing."),
        ("flame.fill", "Keep clear of gas pipes", "Don't lean on or step over the gas meter."),
        ("hand.raised.fill", "Don't open the meter", "Leave covers closed and wires alone."),
    ]

    var body: some View {
        VStack(spacing: 12) {
            ForEach(items, id: \.title) { item in
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: item.symbol)
                        .font(.title2.weight(.semibold))
                        .foregroundStyle(Palette.signal)
                        .frame(width: 48, height: 48)
                        .background(Palette.signal.opacity(0.1), in: .circle)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(item.title)
                            .font(Typeface.sectionTitle)
                        Text(item.detail)
                            .font(Typeface.hint)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(16)
                .background(Palette.surface, in: .rect(cornerRadius: 20, style: .continuous))
                .accessibilityElement(children: .combine)
            }
        }
    }
}

/// The moves page's picture: the walk's own marks on the onboarding wall. A path on the ground to
/// a ring that fills, an arrow at the edge for a spot off screen, and a marked wall end on the
/// right. The ring and the arrow are `TargetMarker`, so they follow any change to the camera's.
private struct MovesArt: View {
    var isActive: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: reduceMotion || !isActive)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            // The ring fills over 3.5 s, holds green for 1.5 s, then starts again: every 6 s.
            let cycle = t.truncatingRemainder(dividingBy: 6)
            let progress = reduceMotion ? 0.5 : min(1, max(0, (cycle - 0.5) / 3.5))
            GeometryReader { proxy in
                let size = proxy.size
                // On the ground strip along the wall, where the tilt-down card points.
                let ring = CGPoint(x: size.width * 0.66, y: size.height * 0.86)
                let endX = size.width * 0.9
                ZStack {
                    WallDrawing()
                    Canvas { context, size in
                        // The path toward the spot, as `WayfindingOverlay` draws it: blue dots on
                        // a white rim, stronger toward the far end.
                        let y = size.height * 0.86
                        let count = 5
                        for i in 0..<count {
                            let x = size.width * (0.3 + 0.056 * Double(i))
                            let rect = CGRect(x: x - 5, y: y - 3.5, width: 10, height: 7)
                            let fade = 0.55 + 0.45 * Double(i) / Double(count - 1)
                            context.fill(Path(ellipseIn: rect.insetBy(dx: -2, dy: -1.5)), with: .color(.white.opacity(0.7 * fade)))
                            context.fill(Path(ellipseIn: rect), with: .color(Palette.signal.opacity(fade)))
                        }
                        // The wall's end, as `WallMarksOverlay` draws a marked end: a white line
                        // with a dark rim and a cap at the ground.
                        let bottom = CGPoint(x: endX, y: size.height * 0.72 - 4)
                        var line = Path()
                        line.move(to: bottom)
                        line.addLine(to: CGPoint(x: endX, y: 20))
                        context.stroke(line, with: .color(.black.opacity(0.35)), style: StrokeStyle(lineWidth: 7, lineCap: .round))
                        context.stroke(line, with: .color(.white), style: StrokeStyle(lineWidth: 4, lineCap: .round))
                        context.fill(Path(ellipseIn: CGRect(x: bottom.x - 7, y: bottom.y - 7, width: 14, height: 14)), with: .color(.white))
                    }
                    // "Wall ends here" carries a flag.
                    Image(systemName: "flag.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(Palette.signal, in: .circle)
                        .position(x: endX, y: 20)
                    TargetMarker(placement: .onScreen(ring, radius: 30), progress: progress)
                    TargetMarker(placement: .offScreen(CGPoint(x: 34, y: size.height * 0.36), angle: .degrees(180)))
                }
            }
        }
    }
}

/// The moves in order, numbered like a briefing. Each is one accessibility element.
private struct MoveList: View {
    var moves: [Instruction]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(moves.indices, id: \.self) { index in
                HStack(alignment: .top, spacing: 14) {
                    Text("\(index + 1)")
                        .font(Typeface.sectionTitle)
                        .foregroundStyle(Palette.signalText)
                        // Grows with Dynamic Type rather than clipping the number.
                        .frame(minWidth: 40, minHeight: 40)
                        .padding(2)
                        .background(Palette.signal.opacity(0.1), in: .circle)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(moves[index].title)
                            .font(Typeface.sectionTitle)
                            .fixedSize(horizontal: false, vertical: true)
                        if let detail = moves[index].detail {
                            Text(detail)
                                .font(Typeface.hint)
                                .foregroundStyle(Palette.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("onboarding.move.\(index + 1)")
            }
        }
        .padding(.bottom, 8)
    }
}
