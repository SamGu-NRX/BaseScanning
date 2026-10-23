import SwiftUI

/// "Anything else near your meter?": say what the ground along the wall is, review what was
/// marked, answer the one question a camera can't (does this window open?), add anything
/// missed, then confirm.
///
/// "Looks complete" is a soft gate (#65): with a question still unanswered, the first tap
/// scrolls to the first one and outlines it, and the next tap sends anyway. The ground question
/// and every window's question count; "Not sure" is an answer to both. Once every question has
/// an answer the gate re-arms, so a window added afterwards gets its own first-tap warning
/// (review of #131).
///
/// A panel over the dimmed camera rather than a new page: the homeowner is still standing at
/// the wall, and the list refers to things they can see.
///
/// "Add something" starts a mark without leaving this phase (the engine keeps `.markFeatures`
/// and sets `state.marking`), so while a mark is open the panel gives way to the walk's marking
/// view: the camera, the prompt, the circle and Mark. When the mark is placed or cancelled,
/// `marking` clears and the list comes back with the new item in it.
struct MarkFeaturesScreen: View {
    let state: ScanViewState
    let actions: any ScanActions

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The first "Looks complete" found a question unanswered and pointed to it; while set, the
    /// outline follows whichever question is now first without an answer, and the next tap
    /// sends. Cleared once every question has an answer.
    @State private var warned = false

    var body: some View {
        ZStack {
            if state.marking != nil {
                WallWalkScreen(state: state, actions: actions)
                    .transition(.opacity)
            } else {
                review
                    .transition(.opacity)
            }
        }
        .animation(reduceMotion ? .easeOut(duration: 0.15) : Motion.screen, value: state.marking == nil)
        // Re-arm here rather than in the review panel: the panel is swapped out while a mark
        // from "Add something" is open, and that mark can be the window that needs the warning.
        .onChange(of: firstUnanswered == nil) { _, allAnswered in
            if allAnswered { warned = false }
        }
    }

    private var review: some View {
        ZStack(alignment: .bottom) {
            Color.black.opacity(0.45)
                .ignoresSafeArea()
                .accessibilityHidden(true)
            VStack(spacing: 0) {
                HStack {
                    ModeBadge(isReplay: state.isReplay, isAutopilot: state.isAutopilot)
                    Spacer()
                }
                .padding(.horizontal, Metrics.edge)
                Spacer(minLength: 40)
                panel
            }
        }
    }

    /// The first question still without an answer, in the order the panel lists them.
    private var firstUnanswered: ReviewQuestion? {
        if state.groundAnswer == nil { return .ground }
        return state.features.first(where: \.awaitsOpensAnswer).map { .window($0.id) }
    }

    /// Outlined: the question "Looks complete" is waiting on. It moves to the next one when
    /// this one is answered, so a second unanswered window is not left for the homeowner to find.
    private func isOutlined(_ question: ReviewQuestion) -> Bool {
        warned && firstUnanswered == question
    }

    private var panel: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        // First, so it is seen before "Looks complete"; skipping it counts as not sure.
                        GroundQuestion(answer: state.groundAnswer, actions: actions,
                                       highlighted: isOutlined(.ground))
                            .id(ReviewQuestion.ground)
                        heading
                        featureList
                        addSomething
                    }
                    .padding(20)
                }
                .scrollBounceBehavior(.basedOnSize)

                if warned, firstUnanswered != nil {
                    Text(ScanCopy.reviewUnanswered)
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 20)
                        .padding(.top, 8)
                        .accessibilityIdentifier("review.unanswered")
                        .transition(.opacity)
                }

                Button {
                    confirm(proxy)
                } label: {
                    Text("Looks complete")
                }
                .buttonStyle(.primary)
                .accessibilityIdentifier("action.confirmFeatures")
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            .animation(Motion.text, value: warned)
        }
        .background {
            UnevenRoundedRectangle(topLeadingRadius: 28, topTrailingRadius: 28, style: .continuous)
                .fill(Palette.surface)
                .ignoresSafeArea(edges: .bottom)
        }
        .environment(\.colorScheme, .light)
    }

    /// Sends, unless a question is unanswered and the homeowner hasn't been warned since the last
    /// time every question had an answer: then scrolls to the first and outlines it. The phone
    /// having lost its place changes nothing here, so the second tap still sends the scan.
    private func confirm(_ proxy: ScrollViewProxy) {
        guard !warned, let question = firstUnanswered else {
            actions.confirmFeatures()
            return
        }
        warned = true
        withAnimation(reduceMotion ? nil : Motion.settle) {
            proxy.scrollTo(question, anchor: .top)
        }
        AccessibilityNotification.Announcement(ScanCopy.reviewUnanswered).post()
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Anything else near your meter?")
                .font(Typeface.screenTitle)
                .foregroundStyle(.primary)
                .accessibilityAddTraits(.isHeader)
            Text("Gas meters, doors, windows, AC units, other batteries, boxes on the wall, driveways and fences all change where a battery can go.")
                .font(Typeface.hint)
                .foregroundStyle(Palette.muted)
        }
    }

    @ViewBuilder
    private var featureList: some View {
        if state.features.isEmpty {
            Text("Nothing marked yet.")
                .font(Typeface.hint)
                .foregroundStyle(Palette.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .background(Palette.canvas, in: .rect(cornerRadius: 16, style: .continuous))
        } else {
            VStack(spacing: 10) {
                ForEach(state.features) { feature in
                    FeatureRow(feature: feature, pastEnd: state.featuresPastEnds.contains(feature.id),
                               highlighted: isOutlined(.window(feature.id)), actions: actions)
                        .id(ReviewQuestion.window(feature.id))
                        .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.98)))
                }
            }
            // A row added or removed moves the rows under it; with Reduce Motion they move at once.
            .animation(reduceMotion ? nil : Motion.settle, value: state.features)
        }
    }

    /// A mark is a tap into the scene, so while the phone has lost its place the chips give way
    /// to a line that says so; "Looks complete" still finishes the review.
    private var addSomething: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Add something")
                .font(Typeface.sectionTitle)
                .accessibilityAddTraits(.isHeader)
            if state.tracking.hasLostItsPlace {
                Label {
                    Text(ScanCopy.reviewLostPlace)
                } icon: {
                    Image(systemName: "location.slash.fill")
                        .foregroundStyle(Palette.muted)
                        .accessibilityHidden(true)
                }
                .font(Typeface.hint)
                .foregroundStyle(.primary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .background(Palette.canvas, in: .rect(cornerRadius: 16, style: .continuous))
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("review.lostPlace")
                .transition(.opacity)
            } else {
                FlowChips { kind in actions.beginMarking(kind) }
                    .transition(.opacity)
            }
        }
        // Opacity only, so it stays with reduced motion too.
        .animation(Motion.text, value: state.tracking.hasLostItsPlace)
    }
}

/// A question on the review "Looks complete" can point to.
private enum ReviewQuestion: Hashable {
    case ground
    case window(UUID)
}

private struct FeatureRow: View {
    var feature: MarkedFeature
    var pastEnd: Bool
    /// "Looks complete" pointed here: the window's question is unanswered.
    var highlighted = false
    var actions: any ScanActions

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: ScanCopy.symbol(feature.kind))
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Palette.signal)
                    .frame(width: 44, height: 44)
                    .background(Palette.signal.opacity(0.1), in: .circle)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(ScanCopy.name(feature.kind))
                        .font(Typeface.hint.weight(.semibold))
                    Text(Distance.aroundFromMeter(feature.span).prefix(1).uppercased() + Distance.aroundFromMeter(feature.span).dropFirst())
                        .font(.subheadline)
                        .foregroundStyle(Palette.muted)
                    if feature.kind == .acUnit {
                        Text(ScanCopy.acAssumedSize)
                            .font(.subheadline)
                            .foregroundStyle(Palette.muted)
                            .accessibilityLabel(ScanCopy.acAssumedSizeSpoken)
                    }
                    if feature.kind.depthUnmeasured {
                        Text(ScanCopy.depthNotMeasured)
                            .font(.subheadline)
                            .foregroundStyle(Palette.muted)
                    }
                    if pastEnd {
                        Label {
                            Text(ScanCopy.featurePastEnd)
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(Palette.caution)
                                .accessibilityHidden(true)
                        }
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier(pastEnd ? "review.featurePastEnd" : "review.feature")
                Button(role: .destructive) {
                    actions.deleteFeature(feature.id)
                } label: {
                    Image(systemName: "trash")
                        .font(.body.weight(.semibold))
                        .frame(width: Metrics.minTarget, height: Metrics.minTarget)
                }
                .foregroundStyle(Palette.danger)
                .accessibilityLabel("Remove \(ScanCopy.noun(feature.kind))")
                .accessibilityIdentifier("action.deleteFeature")
            }
            if feature.kind == .window {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Does this window open?")
                        .font(.subheadline.weight(.semibold))
                    // Stacked, so both answers stay full width at every text size (checklist I4).
                    VStack(spacing: 8) {
                        AnswerButton(title: "It opens", selected: feature.opens == true) { actions.setWindowOpens(feature.id, opens: true) }
                            .accessibilityIdentifier("window.opens.yes")
                        AnswerButton(title: "It stays shut", selected: feature.opens == false) { actions.setWindowOpens(feature.id, opens: false) }
                            .accessibilityIdentifier("window.opens.no")
                        AnswerButton(title: ScanCopy.windowNotSure, selected: feature.opensNotSure) { actions.setWindowOpens(feature.id, opens: nil) }
                            .accessibilityIdentifier("window.opens.notSure")
                    }
                }
            }
        }
        .padding(14)
        .background(Palette.canvas, in: .rect(cornerRadius: 18, style: .continuous))
        .overlay { UnansweredOutline(shown: highlighted) }
    }
}

/// Quick-add chips in as many columns as the text size allows.
private struct FlowChips: View {
    var onPick: (FeatureKind) -> Void
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let columns = typeSize.isAccessibilitySize ? 1 : 2
        Grid(horizontalSpacing: 8, verticalSpacing: 8) {
            ForEach(Array(stride(from: 0, to: FeatureKind.allCases.count, by: columns)), id: \.self) { start in
                GridRow {
                    ForEach(FeatureKind.allCases[start..<min(start + columns, FeatureKind.allCases.count)]) { kind in
                        chip(kind)
                    }
                }
            }
        }
    }

    private func chip(_ kind: FeatureKind) -> some View {
        Button {
            onPick(kind)
        } label: {
            Label(ScanCopy.name(kind), systemImage: ScanCopy.symbol(kind))
                .font(Typeface.caption)
                .foregroundStyle(Palette.signalText)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, minHeight: Metrics.minTarget, alignment: .leading)
                .background(Palette.signal.opacity(0.1), in: .rect(cornerRadius: 14, style: .continuous))
                .contentShape(.rect(cornerRadius: 14))
        }
        .buttonStyle(PressableStyle())
        .accessibilityLabel("Add \(ScanCopy.noun(kind))")
        .accessibilityIdentifier("feature.\(kind.rawValue)")
    }
}
