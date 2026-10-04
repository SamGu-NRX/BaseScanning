import Foundation
import simd

/// Photo processing for the scan under way. The app's capture events go to the process's one
/// `CaptureSessionCoordinator`, and its status and result come back as one
/// `PhotoProcessingStatus`, checked against the scan they belong to.
///
/// Each photo-processing scan starts a new coordinator session (`beginScan`), which asks for
/// consent again. Start over, or a Legacy scan starting, ends it (`endScan`); a world reset starts
/// the scan's next packet with the same answer (`worldReset`). The coordinator drops status and
/// answers from a session that ended, and `PhotoCaptureBinding` refuses an answer whose run, or
/// whose local association (the recording's world, the coordinator session, packet and capture
/// the phone sent), isn't this scan's. The service declares no input digest, so this is the
/// phone's association, not proof of what the service read.
///
/// A capture saved on disk is never resumed from here. Folder ownership lasts only this process,
/// so a relaunch asks for consent again rather than act on a yes it can't prove.
@MainActor
public final class PhotoProcessingController {
    public enum Setup: Sendable {
        /// Nothing can be sent, for the reason given.
        case notSetUp(String)
        /// Captures go to `environment`, which must allow sending. `standIn`: a stand-in on the
        /// phone answers, such as the DEBUG capture fixture.
        case ready(CaptureSessionCoordinator.Environment, standIn: Bool)
    }

    private let setup: Setup
    private let coordinator: CaptureSessionCoordinator?
    private var begun = false
    /// The photo-processing scan under way; nil when there is none.
    public private(set) var context: ScanContext?
    public private(set) var status: PhotoProcessingStatus? {
        didSet { if status != oldValue { onChange?(status) } }
    }
    public var onChange: (@MainActor (PhotoProcessingStatus?) -> Void)?
    /// The meter anchor's pose now, for the AR check on an answer. Nil while there is none.
    public var meterAnchor: @MainActor () -> simd_float4x4? = { nil }
    private var binding: PhotoCaptureBinding?
    private var upload: CaptureUploadStatus?
    private var captureEnded = false

    public init(setup: Setup) {
        if case .ready(let environment, _) = setup, !environment.sends {
            self.setup = .notSetUp("the capture endpoint may not receive this scan")
        } else {
            self.setup = setup
        }
        if case .ready(let environment, _) = self.setup {
            coordinator = CaptureSessionCoordinator(environment: environment)
        } else {
            coordinator = nil
        }
        coordinator?.onStatus = { [weak self] in self?.uploadChanged($0) }
        coordinator?.onResult = { [weak self] in self?.answered($0) }
    }

    /// The profile a photo-processing scan starting now gets.
    public var profile: ProcessingProfile {
        switch setup {
        case .notSetUp(let reason):
            ProcessingProfile(backend: .photoProcessing, answers: .notSetUp(reason), origin: nil)
        case .ready(let environment, let standIn):
            ProcessingProfile(backend: .photoProcessing, answers: standIn ? .standIn : .service, origin: environment.endpoint)
        }
    }

    /// The current capture's folder on the phone, while a session is open.
    public var captureFolder: URL? { coordinator?.session?.folder }

    // MARK: Scan events

    /// A scan that uses photo processing starts, recording into `recording`'s world.
    public func beginScan(_ context: ScanContext, recording: RecordingSource) {
        precondition(context.profile.backend == .photoProcessing, "a Legacy scan has no photo processing")
        // Whatever the last scan left open ends here, whether or not this one can send.
        startSession("new scan", recording: recording, newScan: true)
        self.context = context
        resetCapture()
        guard coordinator != nil, context.profile == profile, case .ready(_, let standIn) = setup else {
            let reason: String = switch context.profile.answers {
            case .notSetUp(let why): why
            case .service, .standIn: "the scan's processing setup is not this build's"
            }
            status = PhotoProcessingStatus(stage: .ended(.notSetUp(reason)), consent: .notNeeded, standIn: false)
            return
        }
        status = PhotoProcessingStatus(stage: .capturing(sent: 0), consent: .asking, standIn: standIn)
    }

    /// Start over, or a Legacy scan starting: the photo-processing scan's session ends, and
    /// nothing it sent or receives later reaches the screen. `recording` is the next world's.
    public func endScan(recording: RecordingSource) {
        if begun { coordinator?.newWorld("start over", recording: recording, newScan: true) }
        context = nil
        resetCapture()
        status = nil
    }

    /// The scan's ARKit world was thrown away: its packet can't be finished, and the next one is
    /// recorded in `next`'s world under the same consent. A scan whose processing already ended
    /// (not set up, consent withdrawn) stays ended.
    public func worldReset(_ next: ScanContext, recording: RecordingSource) {
        guard let current = context, next.scanID == current.scanID, next.profile == current.profile else { return }
        context = next
        resetCapture()
        guard var state = status, !state.isFinal else { return }
        startSession("world reset", recording: recording, newScan: false)
        state.stage = .capturing(sent: 0)
        state.retrying = false
        status = state
    }

    public func answerConsent(_ yes: Bool) {
        guard context != nil, let coordinator, var state = status, state.consent == .asking else { return }
        // Before any yes there is no upload to withdraw, so a no can't fail here.
        coordinator.answerConsent(yes)
        state.consent = yes ? .granted : .declined
        status = state
    }

    /// A no after a yes: nothing more from this scan is sent, and the scan can't be processed
    /// again. The stage says whether the phone saved the no.
    public func stopSending() {
        guard let coordinator, var state = status, state.consent == .granted, !state.isFinal else { return }
        let recorded = switch coordinator.answerConsent(false) {
        case .success: true
        case .failure: false
        }
        state.consent = .withdrawn(recorded: recorded)
        state.stage = .ended(.withdrawn(recorded: recorded))
        state.retrying = false
        status = state
    }

    public func kept(_ photo: KeptPhoto) {
        guard acceptsCapture else { return }
        coordinator?.kept(photo)
    }

    public func meterTapped(_ tap: TapObservation, hit: Packet04.TapHit?) {
        guard acceptsCapture else { return }
        coordinator?.meterTapped(tap, hit: hit)
    }

    /// The scan was sent: the packet is frozen and processing starts, if the homeowner said yes.
    /// `acceptedCloseUpAt` is the frame time of the close-up the scan accepted, nil after a skip.
    public func captureEnded(acceptedCloseUpAt: Double?) {
        guard let context, var state = status, !captureEnded, case .capturing = state.stage else { return }
        captureEnded = true
        switch state.consent {
        case .granted:
            guard let coordinator, let session = coordinator.session, session.uploader != nil, let origin = profile.origin else {
                state.stage = .ended(.notPrepared)
                status = state
                return
            }
            binding = PhotoCaptureBinding(
                spatial: context.spatial, origin: origin, captureSessionID: session.localID, packetID: session.packetID,
                epoch: CaptureSessionCoordinator.epoch)
            coordinator.captureEnded(acceptedCloseUpAt: acceptedCloseUpAt)
            if let upload {
                state.stage = .progress(upload, captureEnded: true)
            } else {
                state.stage = .queued
            }
        case .asking, .declined:
            state.stage = .ended(.consentNotGiven)
        case .withdrawn(let recorded):
            state.stage = .ended(.withdrawn(recorded: recorded))
        case .notNeeded:
            return
        }
        status = state
    }

    /// Waits until the current session's queued work and uploads are idle. For tests.
    public func settle() async {
        await coordinator?.settle()
    }

    // MARK: Coordinator events

    private var acceptsCapture: Bool {
        guard context != nil, !captureEnded, let status, case .capturing = status.stage else { return false }
        return status.consent == .asking || status.consent == .granted
    }

    private func startSession(_ reason: String, recording: RecordingSource, newScan: Bool) {
        guard let coordinator else { return }
        if begun {
            coordinator.newWorld(reason, recording: recording, newScan: newScan)
        } else {
            coordinator.begin(recording: recording)
            begun = true
        }
    }

    private func resetCapture() {
        binding = nil
        upload = nil
        captureEnded = false
    }

    /// The coordinator reports only its current session; nil means that session ended, and
    /// whoever ended it has already set the stage.
    private func uploadChanged(_ next: CaptureUploadStatus?) {
        guard let next, context != nil else { return }
        upload = next
        guard var state = status, !state.isFinal else { return }
        state.retrying = next.retryingAt != nil
        switch next.phase {
        case .creating, .uploading, .processing:
            state.stage = .progress(next, captureEnded: captureEnded)
        case .finished:
            // The answer follows through `answered`, once the coordinator has decoded it.
            state.stage = .processing
        case .abandoned:
            // Start over and world resets end the session before its uploader stops, so an
            // abandoned upload still current is the packet the phone couldn't finish.
            guard captureEnded else { return }
            state.stage = .ended(.notPrepared)
            state.retrying = false
        case .failed:
            readRefusal()
            return
        }
        status = state
    }

    private func readRefusal() {
        guard let session = coordinator?.session, let uploader = session.uploader else { return }
        Task { [weak self] in
            let end = await uploader.snapshot.end
            guard let self, self.coordinator?.session === session, var state = self.status, !state.isFinal,
                  case .failed(let step, let codes, _)? = end else { return }
            state.stage = .refusal(step: step, codes: codes)
            state.retrying = false
            self.status = state
        }
    }

    private func answered(_ record: CaptureResult.Record) {
        guard let binding, var state = status, !state.isFinal, let session = coordinator?.session else { return }
        let observed = PhotoCaptureObservation(
            spatial: context?.spatial, origin: profile.origin, captureSessionID: session.localID, packetID: session.packetID,
            captureID: upload?.captureID, runID: upload?.runID)
        if let mismatch = binding.mismatch(record, observed: observed) {
            // An answer for a world that is gone was never this scan's to show or to end.
            guard mismatch != .world else { return }
            state.stage = .ended(.answerMismatch(mismatch))
        } else {
            let world = CaptureResult.Association(
                sessionID: session.localID, captureID: upload?.captureID ?? "", runID: upload?.runID ?? "", epoch: binding.epoch)
            state.stage = .answer(record, world: world, meterAnchor: meterAnchor(), parent: amendmentParent(record, session: session))
        }
        state.retrying = false
        status = state
    }

    private func amendmentParent(_ record: CaptureResult.Record, session: CaptureSessionCoordinator.Session) -> PhotoAmendmentParent? {
        guard let context, let captureID = upload?.captureID, let runID = upload?.runID else { return nil }
        let views = record.response.viewsNeeded.isEmpty ? record.response.outcome?.viewsNeeded ?? [] : record.response.viewsNeeded
        return PhotoAmendmentParent(captureID: captureID, profile: context.profile, runID: runID, viewIDs: views.map(\.id), folder: session.folder)
    }
}
