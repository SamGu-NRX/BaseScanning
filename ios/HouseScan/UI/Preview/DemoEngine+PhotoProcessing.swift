import Foundation
import HouseScanKit

/// Photo processing in the UI demo. `-uiDemoPhase processing -uiDemoPhotoState <id>` shows one of
/// the screen's states (ids as in `ProcessingCopy.Screen.id`), and `-uiDemoPhotoConsent` on a
/// camera phase shows the consent question. The states are the types the engine publishes, with
/// the capture fixture's words, so every screen says its answer is a test one.
extension DemoEngine {
    func showPhotoProcessing(photoState id: String?, consent: Bool) {
        if consent {
            state.scanBackend = .photoProcessing
            state.photoProcessing = PhotoProcessingStatus(stage: .capturing(sent: 0), consent: .asking, standIn: true)
        }
        guard state.phase == .processing else { return }
        state.scanBackend = .photoProcessing
        state.photoProcessing = Self.photoExamples[id ?? "processing"] ?? Self.photoExamples["processing"]
    }

    /// The scan was sent: a yes ends on the fixture's possible spot, anything else on photos not
    /// sent, as the engine would.
    func finishPhotoProcessing() {
        let sent = state.photoProcessing?.consent == .granted
        state.gap = nil
        state.phase = .processing
        state.photoProcessing = Self.photoExamples[sent ? "candidate" : "consentNotGiven"]
    }

    func answerPhotoConsent(_ yes: Bool) {
        guard var status = state.photoProcessing, status.consent == .asking else { return }
        status.consent = yes ? .granted : .declined
        state.photoProcessing = status
    }

    func scanWithLegacyInstead() {
        startOver()
        finishOnboarding()
        state.scanBackend = .legacy
    }

    func stopSendingPhotos() {
        guard var status = state.photoProcessing, status.consent == .granted, !status.isFinal else { return }
        status.consent = .withdrawn(recorded: true)
        status.stage = .ended(.withdrawn(recorded: true))
        state.photoProcessing = status
    }

    static let photoExamples: [String: PhotoProcessingStatus] = {
        func sent(_ stage: PhotoProcessingStatus.Stage) -> PhotoProcessingStatus {
            PhotoProcessingStatus(stage: stage, consent: .granted, standIn: true)
        }
        let prompt = try? JSONDecoder().decode(
            CaptureResult.Prompt.self,
            from: Data(#"{"title": "Fixture: show the ground below the meter", "body": "Fixture: step back until the ground below the meter is in view."}"#.utf8))
        let answers = FixtureCaptureHTTP.Answer.self
        return [
            "queued": sent(.queued),
            "uploading": sent(.uploading(received: 9, kept: 14)),
            "processing": sent(.processing),
            "candidate": sent(.answered(.candidate(.init(
                message: answers.candidate.message ?? "", previewMade: false, ar: .withheld(.analysisUnverified))))),
            "needsMorePhotos": sent(.answered(.needsMorePhotos(.init(
                prompts: prompt.map { [$0] } ?? [], message: answers.needsViews.message, parent: nil)))),
            "installerReview": sent(.answered(.installerReview(message: answers.manualReview.message ?? ""))),
            "noCandidate": sent(.answered(.noCandidate(message: answers.notEligible.message ?? ""))),
            "unrecognized": sent(.answered(.unrecognized(kind: "fixture_kind", message: "Fixture answer: a kind of answer this app doesn't know."))),
            "notSetUp": PhotoProcessingStatus(stage: .ended(.notSetUp("demo")), consent: .notNeeded, standIn: false),
            "consentNotGiven": PhotoProcessingStatus(stage: .ended(.consentNotGiven), consent: .declined, standIn: true),
            "withdrawn": PhotoProcessingStatus(stage: .ended(.withdrawn(recorded: true)), consent: .withdrawn(recorded: true), standIn: true),
            "withdrawalNotRecorded": PhotoProcessingStatus(
                stage: .ended(.withdrawn(recorded: false)), consent: .withdrawn(recorded: false), standIn: true),
            "notPrepared": sent(.ended(.notPrepared)),
            "processingFailed": sent(.ended(.processingFailed)),
            "expired": sent(.ended(.expired)),
            "refused": sent(.ended(.refused(step: "create"))),
            // At the result step: the service may already have processed the scan.
            "setupRefused": sent(.ended(.setupRefused(step: "result", .credentialUnavailable))),
            "uploadStateUnsaved": sent(.ended(.uploadStateUnsaved)),
            "answerUnreadable": sent(.ended(.answerUnreadable)),
            "answerNotReady": sent(.ended(.answerNotReady)),
            "answerMismatch": sent(.ended(.answerMismatch(.run))),
        ]
    }()
}
