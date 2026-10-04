import Foundation

/// One capture's upload, saved in its folder after every change so a relaunch resumes the same
/// capture with the same identity. The request bodies that idempotency keys are bound to (create
/// and finalize) are saved as the exact bytes first sent, never re-encoded.
public struct CaptureUploadState: Codable, Sendable, Equatable {
    public static let fileName = "capture-upload.json"
    /// Written into a capture's folder when the homeowner takes back their yes. `CaptureUploader.resume`
    /// never resumes a folder that has it.
    public static let withdrawnFileName = "consent-withdrawn"

    public static func withdrawnURL(in folder: URL) -> URL { folder.appending(path: withdrawnFileName) }

    /// Called with the uploader's folder registry locked so no initial save or reply can restore
    /// a yes between these writes. Neither failing proves anything durable after a process exit.
    static func recordWithdrawal(in folder: URL) -> CaptureUploader.WithdrawalRecord {
        do {
            try Data().write(to: withdrawnURL(in: folder), options: .atomic)
            return .marked
        } catch {
            let markerError = CaptureUploader.describe(error)
            do {
                try FileManager.default.removeItem(at: CaptureUploader.stateURL(in: folder))
                return .savedStateRemoved
            } catch let removal as NSError where removal.domain == NSCocoaErrorDomain && removal.code == NSFileNoSuchFileError {
                return .savedStateRemoved
            } catch {
                return .notRecorded("marker: \(markerError); state removal: \(CaptureUploader.describe(error))")
            }
        }
    }

    public enum FilePhase: Codable, Sendable, Equatable {
        /// Sealed on the phone, not yet registered (or its URL must be fetched again).
        case queued
        case registered(url: String, headers: [String: String], expiresAt: Date?)
        /// The storage PUT answered 2xx. Not yet received: only a commit says that.
        case uploaded
        /// Acknowledged by files:commit.
        case committed
    }

    public struct File: Codable, Sendable, Equatable {
        public var sealed: SealedFile
        public var phase: FilePhase = .queued
        public var attempts = 0
        /// Register answers that left this file out, plus commit answers that didn't acknowledge
        /// it. Nil in state saved before this was counted.
        public var unacknowledged: Int?
        /// Order in which the file was sealed, the tie-break after priority.
        public var sequence: Int

        public init(sealed: SealedFile, phase: FilePhase = .queued, attempts: Int = 0, sequence: Int) {
            self.sealed = sealed
            self.phase = phase
            self.attempts = attempts
            self.sequence = sequence
        }
    }

    public enum End: Codable, Sendable, Equatable {
        /// The ARKit world was reset or the scan started over: this packet will never be finished.
        case abandoned(String)
        /// The server refused something a retry can't change.
        case failed(step: String, codes: [String], status: Int)
        /// The capture reached a status after which it changes no more.
        case finished(status: String)
    }

    public struct Finalized: Codable, Sendable, Equatable {
        public var status: String
        public var missing: [String]
        public var runID: String

        public init(status: String, missing: [String], runID: String) {
            self.status = status
            self.missing = missing
            self.runID = runID
        }
    }

    /// Local identity of this upload. Answers that arrive for another attempt are dropped.
    public var attemptID: String
    public var packetID: String
    /// The API base the capture was created on. A resume goes only there: its capture id and
    /// signed URLs mean nothing to another server.
    public var destination: String?
    /// The homeowner's yes for this capture and this destination. A capture without one is
    /// never resumed.
    public var consent: Consent?

    public struct Consent: Codable, Sendable, Equatable {
        public var grantedAt: Date
        public var destination: String

        public init(grantedAt: Date, destination: String) {
            self.grantedAt = grantedAt
            self.destination = destination
        }
    }
    /// The create body exactly as first sent.
    public var createBody: Data
    public var captureID: String?
    public var maxBatch = 50
    public var files: [String: File] = [:]
    public var nextSequence = 0
    /// packet.json exactly as frozen when the capture stopped.
    public var packet: Data?
    public var finalized: Finalized?
    public var eventCursor = 0
    /// `retry_finalize` answers followed so far.
    public var finalizeRetries = 0
    /// Result reads that found no answer yet. Saved so a relaunch continues the same limit; nil in
    /// state saved before this was counted.
    public var notReadyReads: Int?
    public var backendStatus: String?
    public var lastEvent: String?
    public var result: Data?
    public var end: End?
    /// Wall-clock marks of each stage, for latency: no ids, URLs or photos.
    public var marks: [String: Date] = [:]

    public init(attemptID: String, packetID: String, createBody: Data) {
        self.attemptID = attemptID
        self.packetID = packetID
        self.createBody = createBody
    }

    public var committedCount: Int { files.values.filter { $0.phase == .committed }.count }
    public var committedBytes: Int { files.values.filter { $0.phase == .committed }.reduce(0) { $0 + $1.sealed.bytes } }
    public var totalBytes: Int { files.values.reduce(0) { $0 + $1.sealed.bytes } }

    public func save(to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(self).write(to: url, options: .atomic)
    }

    public static func load(from url: URL) throws -> CaptureUploadState {
        try JSONDecoder().decode(CaptureUploadState.self, from: Data(contentsOf: url))
    }
}

/// What the experience layer shows: separate facts, never one percentage.
public struct CaptureUploadStatus: Sendable, Equatable {
    public enum Phase: Sendable, Equatable {
        case creating, uploading, processing, finished, failed, abandoned
    }

    public var phase: Phase
    /// Files sealed on the phone for this capture.
    public var retained: Int
    /// Files the server acknowledged with a commit.
    public var committed: Int
    public var committedBytes: Int
    public var totalBytes: Int
    /// Whether packet.json is frozen (the capture stopped).
    public var sealed: Bool
    /// The capture's status as the server last reported it.
    public var backendStatus: String?
    public var lastEvent: String?
    public var eventCursor: Int
    public var resultAvailable: Bool
    /// The server's id for the capture, once created: what an operator copies to find this scan.
    public var captureID: String?
    /// The run finalize started, once accepted.
    public var runID: String?
    /// Set while the uploader waits out a retryable failure.
    public var retryingAt: Date?
    public var detail: String?

    public init(_ state: CaptureUploadState, retryingAt: Date? = nil, detail: String? = nil) {
        phase = switch state.end {
        case .abandoned?: .abandoned
        case .failed?: .failed
        case .finished?: .finished
        case nil: state.captureID == nil ? .creating : state.finalized == nil ? .uploading : .processing
        }
        retained = state.files.count
        committed = state.committedCount
        committedBytes = state.committedBytes
        totalBytes = state.totalBytes
        sealed = state.packet != nil
        backendStatus = state.backendStatus
        lastEvent = state.lastEvent
        eventCursor = state.eventCursor
        resultAvailable = state.result != nil
        captureID = state.captureID
        runID = state.finalized?.runID
        self.retryingAt = retryingAt
        if case .failed(let step, let codes, let status) = state.end {
            self.detail = "\(step) \(status) \(codes.joined(separator: ","))"
        } else {
            self.detail = detail
        }
    }
}
