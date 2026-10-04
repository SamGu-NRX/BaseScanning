import SwiftUI

/// Asks, once per scan and before anything is sent, whether this scan's photos may go to photo
/// processing. It covers the camera and blocks it until answered, because photos kept from the
/// meter close-up on would go up under the answer. Both choices are large, and "Don't send"
/// is a real answer: the scan goes on and ends saying it wasn't processed.
struct PhotoConsentCard: View {
    var standIn: Bool
    let actions: any ScanActions

    var body: some View {
        ZStack(alignment: .bottom) {
            Palette.ink.opacity(0.6)
                .ignoresSafeArea()
                .accessibilityHidden(true)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(ProcessingCopy.screenLabel)
                        .font(Typeface.caption)
                        .textCase(.uppercase)
                        .tracking(0.6)
                        .foregroundStyle(Palette.chalk.opacity(0.8))
                    Text(ProcessingCopy.consentTitle)
                        .font(Typeface.instruction)
                        .foregroundStyle(Palette.chalk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("photoConsent.title")
                    Text(ProcessingCopy.consentBody(standIn: standIn))
                        .font(Typeface.hint)
                        .foregroundStyle(Palette.chalk)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(ProcessingCopy.consentDeclineNote)
                        .font(Typeface.hint)
                        .foregroundStyle(Palette.chalk.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                    VStack(spacing: 12) {
                        Button(ProcessingCopy.consentSend) { actions.answerPhotoConsent(true) }
                            .buttonStyle(.primary)
                            .accessibilityIdentifier("photoConsent.send")
                        Button(ProcessingCopy.consentDecline) { actions.answerPhotoConsent(false) }
                            .buttonStyle(.secondaryProminent)
                            .frame(maxWidth: .infinity)
                            .accessibilityIdentifier("photoConsent.decline")
                    }
                    .padding(.top, 6)
                }
                .padding(22)
                .background(Palette.ink, in: .rect(cornerRadius: Metrics.cardRadius))
                .padding(Metrics.edge)
            }
            .scrollBounceBehavior(.basedOnSize)
            .defaultScrollAnchor(.bottom)
        }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
        .accessibilityIdentifier("photoConsent")
    }
}
