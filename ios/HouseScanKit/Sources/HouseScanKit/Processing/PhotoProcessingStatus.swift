import Foundation
import simd

/// Photo processing's side of one scan, as the screens show it: separate facts, never a
/// percentage or a time estimate, and nothing the service didn't say.
public struct PhotoProcessingStatus: Sendable, Equatable {
    public enum Consent: Sendable, Equatable {
        /// Not answered yet. Nothing is sent before a yes.
        case asking
        case granted
        case declined
        /// A no after a yes. `recorded` is false when sending stopped in this process but the phone
        /// couldn't save the no (`CaptureSessionCoordinator.ConsentWithdrawalError.notRecorded`).
        case withdrawn(recorded: Bool)
        /// This scan can't send anything, so nobody is asked.
        case notNeeded
    }

    public enum Stage: Sendable, Equatable {
        /// The scan is still being taken. `sent` counts files the service has acknowledged.
        case capturing(sent: Int)
        /// The capture ended and the service hasn't opened it yet.
        case queued
        /// Files still going up: `received` of `kept` acknowledged by the service.
        case uploading(received: Int, kept: Int)
        /// Every file is in and the service is working on it.
        case processing
        case answered(PhotoProcessingAnswer)
        case ended(PhotoProcessingEnd)
    }

    public var stage: Stage
    public var consent: Consent
    /// A stand-in on the phone answers rather than a service (`ProcessingProfile.Answers.standIn`).
    public var standIn: Bool
    /// The upload is waiting to try again after a failure a later try may fix.
    public var retrying: Bool

    public init(stage: Stage, consent: Consent, standIn: Bool, retrying: Bool = false) {
        self.stage = stage
        self.consent = consent
        self.standIn = standIn
        self.retrying = retrying
    }

    /// Nothing more will change for this scan.
    public var isFinal: Bool {
        switch stage {
        case .answered, .ended: true
        case .capturing, .queued, .uploading, .processing: false
        }
    }
}

/// What the service answered. Every answer is provisional: an installer confirms any spot on site.
public enum PhotoProcessingAnswer: Sendable, Equatable {
    /// The service proposed a spot ("eligible"), which is still only a candidate.
    case candidate(Candidate)
    /// The service wants more views. This build can't add photos to a capture.
    case needsMorePhotos(NeedsMorePhotos)
    /// The service sends the scan to an installer.
    case installerReview(message: String)
    /// The service found no spot.
    case noCandidate(message: String)
    /// An outcome this build doesn't know. Its message is shown as written.
    case unrecognized(kind: String, message: String)

    public struct Candidate: Sendable, Equatable {
        /// The service's words for the homeowner, verbatim.
        public var message: String
        /// Whether the service made a preview image. This build shows none either way.
        public var previewMade: Bool
        public var ar: ARAvailability

        public init(message: String, previewMade: Bool, ar: ARAvailability) {
            self.message = message
            self.previewMade = previewMade
            self.ar = ar
        }
    }

    public struct NeedsMorePhotos: Sendable, Equatable {
        /// Each requested view's prompt, verbatim.
        public var prompts: [CaptureResult.Prompt]
        /// The service's words for the homeowner, when it sent an outcome.
        public var message: String?
        /// What a follow-up capture would add to; nil when the upload had no capture or run yet.
        public var parent: PhotoAmendmentParent?

        public init(prompts: [CaptureResult.Prompt], message: String?, parent: PhotoAmendmentParent?) {
            self.prompts = prompts
            self.message = message
            self.parent = parent
        }
    }

    /// Whether the spot can be shown on the wall in AR.
    public enum ARAvailability: Sendable, Equatable {
        /// The result failed a check AR needs (`CaptureResult.Record.placement`), such as analysis
        /// nobody verified, or another session, run or epoch than the recording's.
        case withheld(CaptureResult.Unavailable)
        /// Every check passed, but this build has no AR view for photo-processing results.
        case notShownInThisBuild
    }
}

/// How photo processing ended without an answer to show.
public enum PhotoProcessingEnd: Sendable, Equatable {
    /// This build can't send the scan for photo processing, for the reason given. Nothing was sent.
    case notSetUp(String)
    /// The homeowner didn't agree to send the photos, so none went.
    case consentNotGiven
    /// The homeowner stopped sending part way. `recorded`: whether the phone saved that.
    case withdrawn(recorded: Bool)
    /// The phone couldn't build a capture the service can take: the packet failed its checks on
    /// the phone, or no photo was kept.
    case notPrepared
    /// The service's run failed.
    case processingFailed
    /// The capture expired before it finished.
    case expired
    /// The service refused a request at `step`, which sending the same thing again won't change.
    case refused(step: String)
    /// The service answered with something this build can't read as a result.
    case answerUnreadable
    /// The run finished, but its answer wasn't readable before the upload stopped asking.
    case answerNotReady
    /// The answer belongs to another capture than this scan's, so it isn't shown.
    case answerMismatch(PhotoBindingMismatch)
}

/// What a follow-up capture would carry from the capture it adds views to: plain values only.
/// Nothing in this build sends a follow-up, so a request for more photos ends saying so; an
/// amendment client starts from this. The follow-up keeps its own session and epoch: nothing here
/// joins its poses to the parent's.
public struct PhotoAmendmentParent: Sendable, Equatable {
    /// The server's id for the parent capture.
    public var captureID: String
    /// Where the parent went: the scan's profile, which a follow-up must keep.
    public var profile: ProcessingProfile
    /// The finished run that asked for more.
    public var runID: String
    /// The ids of the views the answer asked for (`CaptureResult.ViewNeeded.id`).
    public var viewIDs: [String]
    /// The parent's capture folder. One uploader at a time owns a folder (`CaptureUploader`'s
    /// registry), so a follow-up has to take it through that rather than around it.
    public var folder: URL

    public init(captureID: String, profile: ProcessingProfile, runID: String, viewIDs: [String], folder: URL) {
        self.captureID = captureID
        self.profile = profile
        self.runID = runID
        self.viewIDs = viewIDs
        self.folder = folder
    }
}

extension PhotoProcessingStatus.Stage {
    /// The stage for a decoded answer already bound to the scan. `world` and `meterAnchor` are the
    /// scan on screen now, for the AR check; `parent` is what a follow-up would add to.
    public static func answer(
        _ record: CaptureResult.Record, world: CaptureResult.Association, meterAnchor: simd_float4x4?,
        parent: PhotoAmendmentParent?
    ) -> Self {
        let summary = CaptureResultSummary(record, world: world, meterAnchor: meterAnchor)
        let message = summary.message ?? ""
        switch summary.state {
        case .decided(.eligible):
            let ar: PhotoProcessingAnswer.ARAvailability = summary.arUnavailable.map { .withheld($0) } ?? .notShownInThisBuild
            return .answered(.candidate(.init(message: message, previewMade: record.response.previewUrl != nil, ar: ar)))
        case .decided(.needsMorePhotos), .working(.needsViews):
            return .answered(.needsMorePhotos(.init(prompts: summary.prompts, message: summary.message, parent: parent)))
        case .decided(.manualReview), .working(.manualReview):
            return .answered(.installerReview(message: message))
        case .decided(.notEligible):
            return .answered(.noCandidate(message: message))
        case .decided(.unknown(let kind)):
            return .answered(.unrecognized(kind: kind, message: message))
        case .ended(.expired):
            return .ended(.expired)
        case .ended:
            return .ended(.processingFailed)
        case .working:
            // The uploader only keeps an answer once it has an outcome or the capture failed or
            // expired, so this is an answer without one: unreadable as a result.
            return .ended(.answerUnreadable)
        }
    }

    /// The stage for an upload that stopped on a refusal (`CaptureUploadState.End.failed`).
    public static func refusal(step: String, codes: [String]) -> Self {
        guard step == "result" else { return .ended(.refused(step: step)) }
        if codes.contains("result_not_ready") { return .ended(.answerNotReady) }
        if codes.contains("result_for_another_run") { return .ended(.answerMismatch(.run)) }
        if codes.contains("result_unreadable") { return .ended(.answerUnreadable) }
        return .ended(.refused(step: step))
    }

    /// Where an upload in progress stands. Before the capture ends, only how much was sent.
    public static func progress(_ upload: CaptureUploadStatus, captureEnded: Bool) -> Self {
        guard captureEnded else { return .capturing(sent: upload.committed) }
        if upload.phase == .creating { return .queued }
        if upload.committed < upload.retained { return .uploading(received: upload.committed, kept: upload.retained) }
        return upload.phase == .processing ? .processing : .uploading(received: upload.committed, kept: upload.retained)
    }
}
