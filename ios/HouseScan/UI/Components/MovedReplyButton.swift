import SwiftUI

/// A card's reply ("I can't get there", "The wall keeps going") shown among the actions instead,
/// while the card folds around the circle at the largest text sizes (`GapRequestScreen`,
/// `WallWalkScreen`). It keeps the card reply's input lock: after its task changes it takes no
/// taps for `InstructionCard.replyLock`, so a tap meant for the step that just left can't answer
/// this one (#82). Coaching or a refusal within the same task keeps it unlocked, as on the card.
/// The secondary style doesn't read `isEnabled`, so the lock doesn't change how it looks;
/// VoiceOver and UI tests read it as dimmed.
struct MovedReplyButton: View {
    let title: String
    let hint: String
    let identifier: String
    /// The step the reply answers; a new one locks the button again.
    let task: Instruction
    let perform: () -> Void

    @State private var unlockedTask: Instruction?

    var body: some View {
        Button(action: perform) {
            Text(title)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.secondary)
        .disabled(unlockedTask != task)
        .accessibilityHint(hint)
        .accessibilityIdentifier(identifier)
        .task(id: task) {
            // Cleared first, so a task that comes back within the lock is locked again.
            let shown = task
            unlockedTask = nil
            do { try await Task.sleep(for: InstructionCard.replyLock) } catch { return }
            unlockedTask = shown
        }
    }
}
