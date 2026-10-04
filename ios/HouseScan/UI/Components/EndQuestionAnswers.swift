import SwiftUI

/// The three answers to "What's at the right end?" (`ScanViewState.endQuestion`), shared by the
/// walk and a past-end request, where marking the end again asks the same question (B-12). One
/// question, three equal full-width answers that say what they mean (checklist I4).
struct EndQuestionAnswers: View {
    let actions: any ScanActions

    var body: some View {
        VStack(spacing: 8) {
            Button {
                actions.answerWallEnd(turnsCorner: true)
            } label: {
                Label("It turns a corner", systemImage: "arrow.turn.up.right")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.secondaryProminent)
            .accessibilityIdentifier("action.endCorner")
            Button {
                actions.answerWallEnd(turnsCorner: false)
            } label: {
                Label("Something blocks it", systemImage: "xmark.octagon")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.secondaryProminent)
            .accessibilityHint("A fence, gate, or your neighbor's yard")
            .accessibilityIdentifier("action.endBlocked")
            // A garden wall or a fence can just stop, with no corner and nothing in the way,
            // and neither answer above fits it (#70). Ends the wall as "Something blocks it"
            // does: the usable wall stops here.
            Button {
                actions.answerWallEnd(turnsCorner: false)
            } label: {
                Label("The wall just ends", systemImage: "arrow.right.to.line")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.secondaryProminent)
            .accessibilityHint("No corner and nothing in the way. The wall stops here.")
            .accessibilityIdentifier("action.endEnds")
        }
    }
}
