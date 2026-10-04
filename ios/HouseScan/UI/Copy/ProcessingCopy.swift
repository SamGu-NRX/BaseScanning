import HouseScanKit

/// Words for the backend choice and photo processing. The service's own messages and prompts are
/// shown as written, never rewritten here; every answer stays provisional, as on the Legacy
/// result (`ScanCopy.installerConfirms`).
enum ProcessingCopy {
    static func name(_ backend: ProcessingBackend) -> String {
        switch backend {
        case .legacy: "Legacy (remote checker)"
        case .photoProcessing: "Photo processing (beta)"
        }
    }

    static func summary(_ backend: ProcessingBackend) -> String {
        switch backend {
        case .legacy:
            "Sends the scan's measurements to the House Scan server, which checks the placement rules. Photos stay on this phone."
        case .photoProcessing:
            "Sends the scan's photos and how the phone moved to House Scan's photo processing service, which suggests a spot. Asks before sending."
        }
    }

    /// What this build can do with a photo-processing scan, under its option; nil when a service
    /// answers.
    static func setupNote(_ answers: ProcessingProfile.Answers) -> String? {
        switch answers {
        case .service: nil
        case .standIn: "Test build: answers come from a stand-in on this phone, and nothing leaves it."
        case .notSetUp: "Not set up in this build. A scan with it stops before the camera starts and offers a Legacy scan instead."
        }
    }

    static func choiceFooter(scanBackend: ProcessingBackend?) -> String {
        guard let scanBackend else { return "Applies to the next scan you start." }
        return "This scan uses \(name(scanBackend)). A change applies to the next scan."
    }

    // MARK: Consent

    static let consentTitle = "Send this scan's photos?"

    static func consentBody(standIn: Bool, synthetic: Bool) -> String {
        let destination = standIn
            ? "In this test build, a stand-in on this phone receives them, so nothing leaves it."
            : "They go to House Scan's processing service while you scan."
        let body = "Photo processing uses the photos you take of the wall around your meter, and how the phone moved, to suggest a battery spot. \(destination)"
        return synthetic ? body + " " + syntheticCapture : body
    }

    /// Said wherever a synthetic capture stands in for the scan's photos.
    static let syntheticCapture = "This test sends a synthetic capture made on the phone instead of this scan's photos, so its answer says nothing about your wall."


    static let consentDeclineNote = "If you don't send them, nothing leaves this phone and this scan won't get an answer."
    static let consentSend = "Send photos"
    static let consentDecline = "Don't send"

    // MARK: Processing screen

    static let screenLabel = "Photo processing (beta)"
    static let standInBadge = "Test answer from this phone, not the processing service"
    static let serviceSaid = "The service says"
    static let stopSending = "Stop sending photos"
    static let stopQuestion = "Stop sending this scan's photos?"
    /// Stopping can come after the photos reached the service and its processing began, and the app
    /// can't cancel the service's job, so this says only what House Scan stops doing.
    static let stopDetail = "House Scan will stop sending from this scan and won't resume it. Photos already sent may still be processed by the service."
    static let confirmStop = "Stop sending"
    static let keepSending = "Keep sending"
    static let setupRefusedDetail = "House Scan couldn't complete its connection to the processing service. This is a setup problem, not a problem with your photos or marks."
    static let scanWithLegacy = "Scan with Legacy instead"
    static let scanWithLegacyHint = "Chooses Legacy in Developer options and starts a new scan with it."
    static let closedAppLimit = "House Scan can't come back to this scan after you close the app."
    static let retrying = "Connection trouble. Trying again."

    struct Screen: Equatable {
        /// A stable name for the state, for the UI tests (`photo.state.<id>`).
        var id: String
        var title: String
        var detail: String
        /// `detail` is the service's message, shown quoted, not House Scan's words.
        var fromService = false
        var working = false
        var tone: Tone = .neutral
        var symbol: String
        /// Further facts, each its own line.
        var notes: [String] = []
    }

    enum Tone {
        case neutral, proposed, attention
    }

    static func screen(_ status: PhotoProcessingStatus?, isReplay: Bool) -> Screen {
        guard let status else {
            return Screen(
                id: "none", title: "Nothing to show", detail: "This scan has no photo processing to show. Start over to scan again.",
                tone: .attention, symbol: "questionmark.circle")
        }
        switch status.stage {
        case .capturing, .queued:
            return Screen(
                id: "queued", title: "Sending your scan", detail: "Waiting for the processing service to open your scan.", working: true,
                symbol: "arrow.up.circle")
        case .uploading(let received, let kept):
            return Screen(
                id: "uploading", title: "Sending your scan", detail: "\(received) of \(kept) files received by the processing service.",
                working: true, symbol: "arrow.up.circle")
        case .processing:
            return Screen(
                id: "processing", title: "Processing your scan", detail: "The service has every file and is working on it.", working: true,
                symbol: "cube.transparent")
        case .answered(let answer):
            return screen(answer)
        case .ended(let end):
            return screen(end, sentSome: status.consent != .declined && status.consent != .asking, isReplay: isReplay)
        }
    }

    private static func screen(_ answer: PhotoProcessingAnswer) -> Screen {
        switch answer {
        case .candidate(let candidate):
            let ar = switch candidate.ar {
            case .withheld: "Showing it on your wall isn't available for this answer."
            case .notShownInThisBuild: "Showing it on your wall isn't available for photo processing yet."
            }
            let preview = candidate.previewMade ? "The service made a preview, which this beta doesn't show yet." : "The service made no preview for this scan."
            return Screen(
                id: "candidate", title: "A possible battery spot", detail: candidate.message, fromService: true, tone: .proposed,
                symbol: "mappin.and.ellipse",
                notes: [ScanCopy.installerConfirms, ar, preview])
        case .needsMorePhotos(let more):
            return Screen(
                id: "needsMorePhotos", title: "One more look needed",
                detail: more.message ?? "The processing service needs more views of the wall.", fromService: more.message != nil,
                tone: .attention,
                symbol: "camera.viewfinder",
                notes: ["Adding photos to a scan isn't supported in this beta yet. To try again, start over and include these views."])
        case .installerReview(let message):
            return Screen(
                id: "installerReview", title: "Needs an installer's review", detail: message, fromService: true,
                symbol: "person.crop.circle.badge.checkmark")
        case .noCandidate(let message):
            return Screen(
                id: "noCandidate", title: "No possible spot found", detail: message, fromService: true, symbol: "mappin.slash",
                notes: [ScanCopy.installerConfirms])
        case .unrecognized(_, let message):
            return Screen(
                id: "unrecognized", title: "An answer this version can't read",
                detail: message.isEmpty ? "The processing service sent a kind of answer House Scan doesn't know yet." : message,
                fromService: !message.isEmpty, tone: .attention, symbol: "questionmark.bubble",
                notes: ["House Scan doesn't know this kind of answer yet, so it shows the service's words as they are."])
        }
    }

    private static func screen(_ end: PhotoProcessingEnd, sentSome: Bool, isReplay: Bool) -> Screen {
        let kept = "Photos already sent stay with the processing service."
        switch end {
        case .notSetUp:
            return Screen(
                id: "notSetUp", title: "Photo processing isn't set up in this build",
                detail: "Nothing has been captured or sent. Scan with \(name(.legacy)) instead, or choose another method in Developer options.",
                tone: .attention, symbol: "wrench.and.screwdriver")
        case .consentNotGiven:
            return Screen(
                id: "consentNotGiven", title: "Photos weren't sent",
                detail: "You didn't agree to send this scan's photos, so nothing left this phone and the scan wasn't processed.",
                symbol: "hand.raised")
        case .withdrawn(let recorded):
            guard !recorded else {
                return Screen(
                    id: "withdrawn", title: "You stopped sending photos",
                    // Stopping can follow sending, and the app can't cancel the service's job.
                    detail: "House Scan won't send anything more from this scan. Photos already sent may still be processed by the service.",
                    symbol: "hand.raised", notes: [kept])
            }
            return Screen(
                id: "withdrawalNotRecorded", title: "You stopped sending photos",
                detail: "Sending stopped, but House Scan couldn't save your choice on this phone. Don't use this scan again: start over for a new one.",
                tone: .attention, symbol: "exclamationmark.triangle", notes: [kept])
        case .notPrepared:
            var notes = sentSome ? [kept] : []
            if isReplay { notes.insert("Replays carry no motion data, which photo processing needs.", at: 0) }
            return Screen(
                id: "notPrepared", title: "This scan couldn't be prepared",
                // Some photos may already have been sent; the phone knows only that it couldn't send the scan.
                detail: "House Scan couldn't put this scan together for photo processing on this phone, so it couldn't be sent for processing.",
                tone: .attention, symbol: "exclamationmark.triangle", notes: notes)
        case .processingFailed:
            return Screen(
                id: "processingFailed", title: "Processing didn't finish", detail: "The processing service couldn't finish this scan.",
                tone: .attention, symbol: "exclamationmark.triangle")
        case .expired:
            return Screen(
                id: "expired", title: "This scan expired", detail: "The processing service stopped waiting before this scan was finished.",
                tone: .attention, symbol: "clock.badge.exclamationmark")
        case .refused:
            return Screen(
                id: "refused", title: "The processing service refused this scan", detail: "Sending it again wouldn't change that.",
                tone: .attention, symbol: "exclamationmark.triangle")
        case .setupRefused:
            // The boundary's four codes read the same to the homeowner: this build's connection
            // isn't right, nothing they did. The code itself goes to the log. It can come at the
            // events or result step, after the service already has the scan, so the words don't
            // say whether it was processed, and an unavailable credential can be temporary.
            return Screen(
                id: "setupRefused", title: "Photo processing isn't connected correctly",
                detail: setupRefusedDetail, tone: .attention, symbol: "wrench.and.screwdriver",
                notes: ["The connection needs to be fixed before you try again. Start over to scan again, or choose \(name(.legacy)) in Developer options first."])
        case .answerUnreadable:
            return Screen(
                id: "answerUnreadable", title: "House Scan couldn't read the answer",
                detail: "The processing service answered, but not in a form this version can read.", tone: .attention,
                symbol: "exclamationmark.bubble")
        case .answerNotReady:
            return Screen(
                id: "answerNotReady", title: "The answer wasn't ready",
                detail: "The service finished, but its answer didn't become available in time.", tone: .attention, symbol: "clock")
        case .answerMismatch:
            return Screen(
                id: "answerMismatch", title: "The answer didn't match this scan",
                detail: "House Scan received an answer for a different capture, so it isn't shown.", tone: .attention,
                symbol: "exclamationmark.triangle")
        }
    }
}
