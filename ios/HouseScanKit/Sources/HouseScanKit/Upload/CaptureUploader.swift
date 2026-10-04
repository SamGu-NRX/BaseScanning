import Foundation
import Synchronization

/// Sends one capture to the capture API while it is being taken: create once, then register, PUT
/// and commit each sealed file as it arrives, then finalize with the frozen packet (possibly
/// before the last commits), then follow the events to a result.
///
/// Every answer is applied only while the attempt that asked is still current, so a reply that
/// arrives after a world reset or a new capture changes nothing. The saved state
/// (`CaptureUploadState`) is written after every change, which is what resumes a capture after a
/// relaunch with the same packet id, capture id and frozen request bodies.
public actor CaptureUploader {
    public struct Policy: Sendable {
        public var maxConcurrentPuts = 3
        /// Files per register and commit call: the contract's "about every 10 files".
        public var batch = 10
        public var firstDelay = 1.0
        public var maxDelay = 30.0
        /// Seconds the server may hold an events poll.
        public var eventsWait = 20
        /// A signed URL this close to expiry is fetched again before the PUT.
        public var expiryMargin = 60.0
        /// PUTs of one file the storage refuses (not transient) before the upload stops.
        public var maxRefusedPuts = 6
        /// `retry_finalize` answers followed before the upload stops.
        public var maxFinalizeRetries = 3
        /// Result reads that find the run's answer not readable yet before the upload stops. Each
        /// waits the retry delay first, so the default allows about three minutes. No measurement
        /// of how long a finished run's answer takes to become readable sets this number; it only
        /// keeps an answer that never appears from holding the upload open.
        public var maxNotReadyResults = 10
        /// Retries after a register answer leaves a file out or a commit doesn't acknowledge it,
        /// counted per file per server capture. Each retry waits the retry delay; the upload stops
        /// on the next unacknowledged answer, so a file is sent at most `maxUnacknowledged + 1`
        /// times. No measurement sets this; it keeps a server that never takes a file from getting
        /// the same photo forever.
        public var maxUnacknowledged = 5
        /// Seconds between events polls that come back at once with nothing new and the capture
        /// still running, for a server or proxy that doesn't hold the poll. Not measured.
        public var minimumPollInterval = 2.0

        public init() {}

        public func delay(afterFailure n: Int, retryAfter: Double?) -> Double {
            if let retryAfter, retryAfter > 0 { return min(retryAfter, 120) }
            return min(maxDelay, firstDelay * pow(2, Double(max(n, 1) - 1)))
        }
    }

    /// A failure a later try can fix.
    struct Transient: Error {
        var step: String
        var detail: String
        var retryAfter: Double?
    }

    /// The homeowner took back their yes: nothing more is sent.
    struct Withdrawn: Error {}

    /// The server refused something a retry can't change.
    struct Refused: Error {
        var step: String
        var status: Int
        var codes: [String]
    }

    public nonisolated let folder: URL
    private let base: URL
    private let http: any CaptureHTTP
    private let policy: Policy
    private let now: @Sendable () -> Date
    private let sleep: @Sendable (Double) async throws -> Void
    private var state: CaptureUploadState
    private var loop: Task<Void, Never>?
    private var failures = 0
    private var retryingAt: Date?
    private var digestRetried: Set<String> = []
    /// The registry covers public start/resume as well as coordinator discovery. An idle live
    /// uploader still owns its folder, since it can receive more sealed files. Weak ownership
    /// releases only after the uploader and its running task are gone. Withdrawn entries stay
    /// for this process's lifetime, including when every disk write failed.
    private struct FolderEntry {
        weak var owner: CaptureUploader?
        var withdrawn = false
        var stopped = false
        var running: Task<Void, Never>?
    }
    private static let folders = Mutex<[String: FolderEntry]>([:])
    private nonisolated let folderKey: String

    public enum OwnershipError: Error, Sendable, Equatable {
        case alreadyOwned
        case consentWithdrawn
        /// Use resume for an existing saved upload; start must never overwrite its consent.
        case savedUploadExists
        case folderIdentityUnavailable
    }
    /// Set when a state change could not be saved: the loop stops rather than send a request the
    /// saved state doesn't know about.
    private var saveFailed = false
    private var observer: (@Sendable (CaptureUploadStatus) -> Void)?
    private var log: (@Sendable (String) -> Void)?

    public nonisolated static func stateURL(in folder: URL) -> URL { folder.appending(path: CaptureUploadState.fileName) }

    /// Resolve symbolic links before asking for the filesystem's case-correct path. The
    /// canonical-path resource alone preserves a final-component symlink on macOS.
    /// Refuse an unknown identity instead of allowing a second registry key for one folder.
    private static func canonicalFolder(_ folder: URL) throws -> URL {
        let fresh = URL(fileURLWithPath: folder.path, isDirectory: true).standardizedFileURL.resolvingSymlinksInPath()
        guard let path = try fresh.resourceValues(forKeys: [.canonicalPathKey]).canonicalPath else {
            throw OwnershipError.folderIdentityUnavailable
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    private init(
        folder: URL, base: URL, http: any CaptureHTTP, state: CaptureUploadState, policy: Policy,
        now: @escaping @Sendable () -> Date, sleep: @escaping @Sendable (Double) async throws -> Void
    ) {
        self.folder = folder
        self.folderKey = folder.path
        self.base = base
        self.http = http
        self.state = state
        self.policy = policy
        self.now = now
        self.sleep = sleep
    }

    /// A new capture. The create body is encoded once here and kept as those bytes for every retry.
    /// `consentedAt` is when the homeowner said yes to sending this capture to `base`.
    public static func start(
        folder: URL, base: URL, http: any CaptureHTTP, create: CaptureAPI.CreateRequest, consentedAt: Date, policy: Policy = .init(),
        now: @escaping @Sendable () -> Date = { Date() },
        sleep: @escaping @Sendable (Double) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) throws -> CaptureUploader {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let folder = try canonicalFolder(folder)
        return try folders.withLock { entries in
            entries = entries.filter { $0.value.owner != nil || $0.value.withdrawn }
            guard entries[folder.path]?.withdrawn != true,
                  !FileManager.default.fileExists(atPath: CaptureUploadState.withdrawnURL(in: folder).path)
            else { throw OwnershipError.consentWithdrawn }
            guard entries[folder.path]?.owner == nil else { throw OwnershipError.alreadyOwned }
            guard !FileManager.default.fileExists(atPath: stateURL(in: folder).path) else { throw OwnershipError.savedUploadExists }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            var state = CaptureUploadState(attemptID: UUID().uuidString, packetID: create.packetId, createBody: try encoder.encode(create))
            state.destination = base.absoluteString
            state.consent = .init(grantedAt: consentedAt, destination: base.absoluteString)
            state.marks["started"] = now()
            try state.save(to: stateURL(in: folder))
            let uploader = CaptureUploader(folder: folder, base: base, http: http, state: state, policy: policy, now: now, sleep: sleep)
            entries[folder.path] = FolderEntry(owner: uploader)
            return uploader
        }
    }

    /// Resumes a saved upload with its recorded consent and endpoint. Returns nil when absent,
    /// ended or withdrawn; throws `alreadyOwned` while another live uploader owns the folder.
    /// The new attempt id and ownership are saved together, before any request can start.
    public static func resume(
        folder: URL, base: URL, http: any CaptureHTTP, policy: Policy = .init(),
        now: @escaping @Sendable () -> Date = { Date() },
        sleep: @escaping @Sendable (Double) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }
    ) throws -> CaptureUploader? {
        guard FileManager.default.fileExists(atPath: folder.path) else { return nil }
        let folder = try canonicalFolder(folder)
        return try folders.withLock { entries in
            entries = entries.filter { $0.value.owner != nil || $0.value.withdrawn }
            let url = stateURL(in: folder)
            guard entries[folder.path]?.withdrawn != true,
                  FileManager.default.fileExists(atPath: url.path),
                  !FileManager.default.fileExists(atPath: CaptureUploadState.withdrawnURL(in: folder).path)
            else { return nil }
            guard entries[folder.path]?.owner == nil else { throw OwnershipError.alreadyOwned }
            var state = try CaptureUploadState.load(from: url)
            guard state.end == nil, state.destination == base.absoluteString, state.consent?.destination == base.absoluteString else { return nil }
            state.attemptID = UUID().uuidString
            try state.save(to: url)
            let uploader = CaptureUploader(folder: folder, base: base, http: http, state: state, policy: policy, now: now, sleep: sleep)
            entries[folder.path] = FolderEntry(owner: uploader)
            return uploader
        }
    }

    public func observe(_ observer: @escaping @Sendable (CaptureUploadStatus) -> Void, log: (@Sendable (String) -> Void)? = nil) {
        self.observer = observer
        self.log = log
        observer(status)
    }

    public var status: CaptureUploadStatus {
        CaptureUploadStatus(state, retryingAt: retryingAt, detail: saveFailed ? "upload state could not be saved" : nil)
    }
    public var snapshot: CaptureUploadState { state }

    // MARK: Input

    /// Sealed files, uploaded as soon as the capture exists. A path already known is ignored:
    /// sealed files never change.
    public func add(_ files: [SealedFile]) {
        guard state.end == nil, state.packet == nil else { return }
        var added = false
        for file in files where state.files[file.path] == nil {
            state.files[file.path] = .init(sealed: file, sequence: state.nextSequence)
            state.nextSequence += 1
            added = true
        }
        guard added else { return }
        mark("firstRetained")
        persist()
        kick()
    }

    /// The capture stopped: `packet` is frozen as these bytes, and `files` is every file it lists.
    /// Any the uploader has not seen yet (an `add` still on its way) is queued here, so the
    /// server never waits for a listed file that nobody sends.
    public func seal(packet: Data, files: [SealedFile]) {
        guard state.end == nil, state.packet == nil else { return }
        for file in files where state.files[file.path] == nil {
            state.files[file.path] = .init(sealed: file, sequence: state.nextSequence)
            state.nextSequence += 1
        }
        state.packet = packet
        mark("sealed")
        persist()
        kick()
    }

    public static let withdrawnReason = "consent withdrawn"

    /// How a withdrawal was recorded on disk.
    public enum WithdrawalRecord: Sendable, Equatable {
        /// The marker `resume` refuses was written.
        case marked
        /// The marker couldn't be written, so the saved state was removed instead.
        case savedStateRemoved
        /// Neither the marker nor the removal succeeded: a relaunch could still find the saved
        /// yes until a later save records the upload as abandoned.
        case notRecorded(String)
    }

    /// Revokes the folder before returning and cancels its running task. A request already
    /// admitted to a transport that ignores cancellation may finish; its reply cannot admit
    /// another request or save resumable consent. `.notRecorded` means only the live process
    /// is protected: after a crash the old saved yes may still exist.
    @discardableResult
    public nonisolated func withdrawConsent() -> WithdrawalRecord {
        let (record, task) = Self.folders.withLock { entries in
            entries[folderKey, default: FolderEntry()].withdrawn = true
            let record = CaptureUploadState.recordWithdrawal(in: folder)
            return (record, entries[folderKey]?.running)
        }
        // Cancellation handlers belong to the transport; never call them while holding our lock.
        task?.cancel()
        return record
    }

    /// Closes admission synchronously when a coordinator ends a world, before its queued actor
    /// work can kick the upload. Ownership stays until the old uploader and its requests die.
    nonisolated func stopSending() {
        let task = Self.folders.withLock { entries in
            entries[folderKey]?.stopped = true
            return entries[folderKey]?.running
        }
        task?.cancel()
    }

    /// Ends this upload for good: a world reset or starting over. Replies still in flight are
    /// dropped when they arrive.
    public func abandon(_ reason: String) {
        stopSending()
        guard state.end == nil else { return }
        state.end = .abandoned(reason)
        state.attemptID = UUID().uuidString
        loop?.cancel()
        persist()
    }

    /// Starts the loop if it is idle: after a relaunch, or when the network is back.
    public func kick() {
        guard loop == nil, state.end == nil, !saveFailed else { return }
        Self.folders.withLock { entries in
            guard let entry = entries[folderKey], entry.owner === self, !entry.withdrawn, !entry.stopped else { return }
            let task = Task { await self.drive() }
            loop = task
            entries[folderKey]?.running = task
        }
    }

    /// Waits for the loop to go idle: nothing to send until more input, or ended.
    public func settled() async {
        while let running = loop { await running.value; if loop == running { loop = nil } }
    }

    // MARK: Loop

    private func drive() async {
        while !Task.isCancelled, state.end == nil, !saveFailed {
            // A withdrawal sends nothing more; the save records the upload as abandoned.
            if isWithdrawn { persist(); break }
            let attempt = state.attemptID
            do {
                let progressed = try await step()
                guard attempt == state.attemptID, state.end == nil else { break }
                failures = 0
                if retryingAt != nil { retryingAt = nil; publish() }
                if !progressed { break }
            } catch let error as Transient {
                guard attempt == state.attemptID else { break }
                failures += 1
                let delay = policy.delay(afterFailure: failures, retryAfter: error.retryAfter)
                retryingAt = now().addingTimeInterval(delay)
                log?("capture-upload retry step=\(error.step) in=\(String(format: "%.1f", delay))s detail=\(error.detail)")
                publish()
                do { try await sleep(delay) } catch { break }
            } catch is Withdrawn {
                persist()
                break
            } catch let error as Refused {
                guard attempt == state.attemptID else { break }
                state.end = .failed(step: error.step, codes: error.codes, status: error.status)
                log?("capture-upload failed step=\(error.step) status=\(error.status) codes=\(error.codes.joined(separator: ","))")
                persist()
            } catch {
                break
            }
        }
        loop = nil
        Self.folders.withLock { $0[folderKey]?.running = nil }
    }

    /// One unit of work. False when there is nothing to do until more input arrives.
    private func step() async throws -> Bool {
        if state.captureID == nil { try await create(); return true }
        // The frozen packet goes as soon as the capture stops, before the files still uploading:
        // the server answers awaiting_files and starts the run on the last commit.
        if state.packet != nil, state.finalized == nil { try await finalize(); return true }
        requeueExpiring()
        let queued = ordered(where: { $0.phase == .queued })
        if !queued.isEmpty { try await register(Array(queued.prefix(min(policy.batch, state.maxBatch)))); return true }
        // Each round of PUTs is committed before the next starts, so the first photos count as
        // received after one round trip of uploads rather than a whole register batch.
        let uploaded = ordered(where: { $0.phase == .uploaded })
        if !uploaded.isEmpty { try await commit(Array(uploaded.prefix(min(policy.batch, state.maxBatch)))); return true }
        let registered = ordered(where: { if case .registered = $0.phase { true } else { false } })
        if !registered.isEmpty { try await put(Array(registered.prefix(policy.maxConcurrentPuts))); return true }
        if state.finalized != nil, state.end == nil {
            if state.result != nil || CaptureAPI.terminalStatuses.contains(state.backendStatus ?? "") {
                try await fetchResult()
                return true
            }
            try await pollEvents()
            return true
        }
        return false
    }

    private func ordered(where include: (CaptureUploadState.File) -> Bool) -> [CaptureUploadState.File] {
        state.files.values.filter(include).sorted { ($0.sealed.priority, $0.sequence) < ($1.sealed.priority, $1.sequence) }
    }

    // MARK: Calls

    private func create() async throws {
        var request = jsonRequest("captures", method: "POST", body: state.createBody)
        request.setValue(state.packetID, forHTTPHeaderField: "Idempotency-Key")
        let (reply, attempt) = try await call("create", request)
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status), let body = try? JSONDecoder().decode(CaptureAPI.CreateResponse.self, from: reply.body) else {
            throw Refused(step: "create", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        state.captureID = body.captureId
        state.backendStatus = body.status
        if let batch = body.upload?.maxBatch, batch > 0 { state.maxBatch = batch }
        mark("created")
        persist()
    }

    private func register(_ files: [CaptureUploadState.File]) async throws {
        let body = try JSONEncoder().encode(CaptureAPI.RegisterRequest(files: files.map { .init($0.sealed) }))
        let (reply, attempt) = try await call("register", jsonRequest("captures/\(captureID)/files", method: "POST", body: body))
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status), let answer = try? JSONDecoder().decode(CaptureAPI.RegisterResponse.self, from: reply.body) else {
            if try captureGone(reply) { return }
            throw Refused(step: "register", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        for file in answer.files where state.files[file.path] != nil {
            if file.state == "committed" {
                setPhase(file.path, .committed)
            } else if let target = file.upload, target.method == "PUT", let url = target.url {
                // Photos go only to an https URL, or http to this machine for tests, and only to a
                // URL the PUT can use: a URL that doesn't parse would leave the file registered
                // forever. This checks the URL as given; `CaptureHTTP` refuses redirects, so a
                // storage server can't send the photo on to a URL this never checked.
                if let problem = Self.uploadURLProblem(url) { throw Refused(step: "register", status: reply.status, codes: [problem]) }
                setPhase(file.path, .registered(url: url, headers: target.headers ?? [:], expiresAt: target.expiresAt.flatMap(Self.parseDate)))
            } else {
                throw Refused(step: "register", status: reply.status, codes: ["upload_method_\(file.upload?.method ?? "none")"])
            }
        }
        let answered = Set(answer.files.map(\.path))
        try unacknowledged(files.map(\.sealed.path).filter { !answered.contains($0) }, step: "register", status: reply.status)
    }

    /// Counts files the server didn't take, saves, and backs off (or stops once one has been left
    /// out more than `maxUnacknowledged` times). Saves and returns when there are none.
    private func unacknowledged(_ paths: [String], step: String, status: Int) throws {
        for path in paths {
            let count = (state.files[path]?.unacknowledged ?? 0) + 1
            state.files[path]?.unacknowledged = count
        }
        persist()
        guard !paths.isEmpty else { return }
        if paths.contains(where: { (state.files[$0]?.unacknowledged ?? 0) > policy.maxUnacknowledged }) {
            throw Refused(step: step, status: status, codes: ["not_acknowledged"])
        }
        throw Transient(step: step, detail: "\(paths.count) not acknowledged", retryAfter: nil)
    }

    /// Why `text` can't receive a photo: nil when it is an https URL, or http to this machine.
    static func uploadURLProblem(_ text: String) -> String? {
        guard let url = URL(string: text), let scheme = url.scheme?.lowercased(), let host = url.host(), !host.isEmpty else {
            return "upload_url_invalid"
        }
        let local = host == "127.0.0.1" || host == "localhost"
        return scheme == "https" || (scheme == "http" && local) ? nil : "upload_url_insecure"
    }

    private func put(_ files: [CaptureUploadState.File]) async throws {
        let attempt = state.attemptID
        var transient: Transient?
        var refused: Refused?
        var index = 0
        while index < files.count {
            // Each batch of PUTs is admitted only while the homeowner's yes stands.
            if !canSend || Task.isCancelled { throw Withdrawn() }
            let chunk = files[index..<min(index + policy.maxConcurrentPuts, files.count)]
            index += chunk.count
            // A URL saved before it was checked goes back to register, which checks it.
            for file in chunk {
                if case .registered(let url, _, _) = file.phase, Self.uploadURLProblem(url) != nil { setPhase(file.sealed.path, .queued) }
            }
            let results = await withTaskGroup(of: (String, Result<HTTPReply, any Error>).self) { group in
                for file in chunk {
                    guard case .registered(let url, let headers, _) = file.phase, Self.uploadURLProblem(url) == nil, let target = URL(string: url) else { continue }
                    var request = URLRequest(url: target)
                    request.httpMethod = "PUT"
                    // Exactly the headers the server signed; never the API's own credentials.
                    for (name, value) in headers { request.setValue(value, forHTTPHeaderField: name) }
                    let local = folder.appending(path: file.sealed.path)
                    let http = self.http
                    group.addTask {
                        if !self.canSend || Task.isCancelled { return (file.sealed.path, .failure(Withdrawn())) }
                        do { return (file.sealed.path, .success(try await http.upload(request, file: local))) } catch { return (file.sealed.path, .failure(error)) }
                    }
                }
                var out: [(String, Result<HTTPReply, any Error>)] = []
                for await result in group { out.append(result) }
                return out
            }
            if !canSend || Task.isCancelled { throw Withdrawn() }
            guard attempt == state.attemptID, state.end == nil else { return }
            for (path, result) in results {
                state.files[path]?.attempts += 1
                switch result {
                case .success(let reply) where (200..<300).contains(reply.status):
                    setPhase(path, .uploaded)
                case .success(let reply) where reply.status == 400 && !digestRetried.contains(path):
                    // BadDigest: the bytes on disk are sealed, so sending them once more is the retry.
                    digestRetried.insert(path)
                case .success(let reply) where reply.status == 400:
                    // The same sealed bytes refused twice: the file on disk no longer matches its digest.
                    throw Refused(step: "put", status: 400, codes: ["bad_digest"])
                case .success(let reply) where Self.isTransient(reply.status):
                    transient = Transient(step: "put", detail: "status \(reply.status)", retryAfter: reply.retryAfter)
                case .success(let reply):
                    // 403 expired signature or a spent token: a fresh URL, a bounded number of times.
                    guard (state.files[path]?.attempts ?? 0) < policy.maxRefusedPuts else {
                        throw Refused(step: "put", status: reply.status, codes: ["storage_refused"])
                    }
                    setPhase(path, .queued)
                case .failure(let error):
                    switch failure("put", error) {
                    case let error as Refused: refused = refused ?? error
                    case let error as Transient: transient = error
                    case let error: throw error
                    }
                }
            }
            persist()
            if let refused { throw refused }
        }
        if let transient { throw transient }
    }

    private func commit(_ files: [CaptureUploadState.File]) async throws {
        let body = try JSONEncoder().encode(CaptureAPI.CommitRequest(files: files.map { .init(path: $0.sealed.path, sha256: $0.sealed.sha256) }))
        let (reply, attempt) = try await call("commit", jsonRequest("captures/\(captureID)/files:commit", method: "POST", body: body))
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status), let answer = try? JSONDecoder().decode(CaptureAPI.CommitResponse.self, from: reply.body) else {
            if try captureGone(reply) { return }
            throw Refused(step: "commit", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        let committed = Set(answer.committed)
        for file in files {
            // Only a commit acknowledges a file; notFound, mismatch or no mention sends it again.
            setPhase(file.sealed.path, committed.contains(file.sealed.path) ? .committed : .queued)
        }
        if !committed.isEmpty { mark("firstCommit") }
        if state.packet != nil, state.files.values.allSatisfy({ $0.phase == .committed }) { mark("lastCommit") }
        try unacknowledged(files.map(\.sealed.path).filter { !committed.contains($0) }, step: "commit", status: reply.status)
    }

    private func finalize() async throws {
        guard let packet = state.packet else { return }
        var request = jsonRequest("captures/\(captureID)/finalize", method: "POST", body: packet)
        request.setValue(state.packetID, forHTTPHeaderField: "Idempotency-Key")
        request.setValue(PacketFiles.sha256(packet), forHTTPHeaderField: "X-Packet-Sha256")
        let (reply, attempt) = try await call("finalize", request)
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status), let answer = try? JSONDecoder().decode(CaptureAPI.FinalizeResponse.self, from: reply.body) else {
            if try captureGone(reply) { return }
            throw Refused(step: "finalize", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        state.finalized = .init(status: answer.status, missing: answer.missing, runID: answer.runId)
        state.backendStatus = answer.status
        mark("finalizeAccepted")
        persist()
    }

    private func pollEvents() async throws {
        var components = URLComponents(url: base.appending(path: "captures/\(captureID)/events"), resolvingAgainstBaseURL: false)!
        components.queryItems = [.init(name: "after", value: String(state.eventCursor)), .init(name: "wait", value: String(policy.eventsWait))]
        var request = URLRequest(url: components.url!)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = Double(policy.eventsWait) + 30
        let sent = now()
        let (reply, attempt) = try await call("events", request)
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status), let answer = try? JSONDecoder().decode(CaptureAPI.EventsResponse.self, from: reply.body) else {
            if try captureGone(reply) { return }
            throw Refused(step: "events", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        state.backendStatus = answer.status
        state.eventCursor = max(state.eventCursor, answer.next)
        for event in answer.events {
            mark("firstEvent")
            state.lastEvent = [event.type, event.data?.stage, event.data?.status, event.data?.kind, event.data?.code].compactMap { $0 }.joined(separator: " ")
            // A verdict for another run is not this capture's result.
            if event.type == "verdict_ready", event.data?.runId == nil || event.data?.runId == state.finalized?.runID { mark("verdictReady") }
            if event.type == "failed", event.data?.next == "retry_finalize" {
                // The server lost the finalize to a transient storage failure: send the same frozen
                // packet again.
                guard state.finalizeRetries < policy.maxFinalizeRetries else {
                    throw Refused(step: "finalize", status: 0, codes: [event.data?.code ?? "retry_finalize"])
                }
                state.finalizeRetries += 1
                state.finalized = nil
            }
        }
        if !answer.events.isEmpty { log?("capture-upload events status=\(answer.status) last=\(state.lastEvent ?? "")") }
        persist()
        // An answer that came back at once with nothing new means the server didn't hold the
        // poll; waiting here keeps the loop from asking again immediately.
        let waited = now().timeIntervalSince(sent)
        if answer.events.isEmpty, !CaptureAPI.terminalStatuses.contains(answer.status), waited < policy.minimumPollInterval {
            try await sleep(policy.minimumPollInterval - waited)
        }
    }

    private func fetchResult() async throws {
        var request = URLRequest(url: base.appending(path: "captures/\(captureID)/result"))
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (reply, attempt) = try await call("result", request)
        guard attempt == state.attemptID else { return }
        guard (200..<300).contains(reply.status) else {
            if try captureGone(reply) { return }
            // The run reads as done before its answer is stored.
            if reply.status == 404, CaptureAPI.errorCodes(reply.body).contains("result_not_ready") { try notReady(reply) }
            throw Refused(step: "result", status: reply.status, codes: CaptureAPI.errorCodes(reply.body))
        }
        // The whole body must decode as the result the app reads, or a finished upload could hold
        // an answer nothing can show.
        let decoder = JSONDecoder()
        guard let response = try? decoder.decode(CaptureResult.Response.self, from: reply.body),
              let head = try? decoder.decode(ResultHead.self, from: reply.body)
        else {
            throw Refused(step: "result", status: reply.status, codes: ["result_unreadable"])
        }
        if let runID = head.runId, runID != state.finalized?.runID {
            throw Refused(step: "result", status: reply.status, codes: ["result_for_another_run"])
        }
        let status = head.status ?? state.backendStatus ?? "unknown"
        // Until the run has written its answer the API serves the capture's status with a null
        // outcome, and that status can already be one the capture ends in.
        if response.outcome == nil, !CaptureAPI.statusesWithoutOutcome.contains(status) { try notReady(reply) }
        state.result = reply.body
        state.end = .finished(status: status)
        mark("result")
        persist()
    }

    /// The run and status as the server wrote them; `CaptureResult.Status` keeps no raw text.
    private struct ResultHead: Decodable {
        var runId: String?
        var status: String?
    }

    /// Always throws: a retry after the usual wait, or the end of the upload once
    /// `maxNotReadyResults` reads have found no answer.
    private func notReady(_ reply: HTTPReply) throws -> Never {
        let notReadyReads = (state.notReadyReads ?? 0) + 1
        state.notReadyReads = notReadyReads
        persist()
        guard notReadyReads <= policy.maxNotReadyResults else {
            throw Refused(step: "result", status: reply.status, codes: ["result_not_ready"])
        }
        throw Transient(step: "result", detail: "result not ready (\(notReadyReads))", retryAfter: reply.retryAfter)
    }

    // MARK: Plumbing

    private var captureID: String { state.captureID ?? "" }

    /// The server lost the capture (404: a redeploy with in-memory state). The same create body
    /// opens it again, and every file goes up again under the new id.
    private func captureGone(_ reply: HTTPReply) throws -> Bool {
        guard reply.status == 404, CaptureAPI.errorCodes(reply.body).contains("capture_not_found") else { return false }
        log?("capture-upload the server no longer has the capture; creating it again")
        state.captureID = nil
        state.finalized = nil
        state.eventCursor = 0
        // A new server capture is a new run: the old one's limits don't carry over.
        state.notReadyReads = nil
        for path in state.files.keys {
            state.files[path]?.phase = .queued
            state.files[path]?.unacknowledged = nil
        }
        persist()
        return true
    }

    private func requeueExpiring() {
        let limit = now().addingTimeInterval(policy.expiryMargin)
        for (path, file) in state.files {
            if case .registered(_, _, let expires?) = file.phase, expires <= limit { state.files[path]?.phase = .queued }
        }
    }

    private func setPhase(_ path: String, _ phase: CaptureUploadState.FilePhase) {
        state.files[path]?.phase = phase
    }

    private func call(_ step: String, _ request: URLRequest) async throws -> (HTTPReply, String) {
        // Every API request is admitted only while the homeowner's yes stands. One already sent
        // finishes, but its reply can only save the upload as abandoned.
        if !canSend || Task.isCancelled { throw Withdrawn() }
        let attempt = state.attemptID
        let reply: HTTPReply
        do {
            reply = try await http.send(request)
        } catch {
            throw failure(step, error)
        }
        if Self.isTransient(reply.status), attempt == state.attemptID {
            throw Transient(step: step, detail: "status \(reply.status)", retryAfter: reply.retryAfter)
        }
        return (reply, attempt)
    }

    private func jsonRequest(_ path: String, method: String, body: Data) -> URLRequest {
        var request = URLRequest(url: base.appending(path: path))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func mark(_ name: String) {
        guard state.marks[name] == nil else { return }
        let at = now()
        state.marks[name] = at
        let since = state.marks["started"].map { at.timeIntervalSince($0) } ?? 0
        log?("capture-upload stage=\(name) t=\(String(format: "%.2f", since))s committed=\(state.committedCount)/\(state.files.count)")
    }

    private nonisolated var isWithdrawn: Bool { Self.folders.withLock { $0[folderKey]?.withdrawn == true } }

    private nonisolated var canSend: Bool {
        Self.folders.withLock { entries in
            guard let entry = entries[folderKey] else { return false }
            return entry.owner === self && !entry.withdrawn && !entry.stopped
        }
    }

    private func persist() {
        do {
            try Self.folders.withLock { entries in
                guard entries[folderKey]?.owner === self else { throw OwnershipError.alreadyOwned }
                let withdrawn = entries[folderKey]?.withdrawn == true
                // A withdrawal outranks any end a late reply set (finished, failed).
                if withdrawn, state.end != .abandoned(Self.withdrawnReason) {
                    state.end = .abandoned(Self.withdrawnReason)
                    // Replies still in flight belong to the old attempt and are dropped.
                    state.attemptID = UUID().uuidString
                }
                try state.save(to: Self.stateURL(in: folder))
            }
        } catch {
            saveFailed = true
            log?("capture-upload stopped: the upload state could not be saved (\(Self.describe(error)))")
        }
        publish()
    }

    private func publish() {
        observer?(status)
    }

    /// 501 is the server saying it doesn't do what was asked (multipart), which waiting won't change.
    static func isTransient(_ status: Int) -> Bool { status == 0 || status == 408 || status == 429 || ((500..<600).contains(status) && status != 501) }

    /// The error's domain and code only: a URLError's description can carry the signed URL.
    /// What a transport error means for the upload. A withdrawal or cancellation that happened
    /// meanwhile outranks the error, whatever it is: the request may have been cut short by that
    /// very withdrawal (`ScopedCaptureHTTP` throws CancellationError once a credential wait is
    /// cancelled), and retrying it would leave the upload waiting to retry instead of saved as
    /// withdrawn. A refusal by the credential boundary won't change on retry: a URL outside the
    /// API's scope, a malformed credential, one the provider couldn't supply, or a storage
    /// request carrying Authorization. It ends the upload with the boundary's code and status 0,
    /// since no server answered. Any other error, a network one included, is retried as before.
    private func failure(_ step: String, _ error: any Error) -> any Error {
        if !canSend || Task.isCancelled { return Withdrawn() }
        guard let refusal = error as? ScopedCaptureHTTPError else {
            return Transient(step: step, detail: Self.describe(error), retryAfter: nil)
        }
        return Refused(step: step, status: 0, codes: [refusal.code])
    }

    static func describe(_ error: any Error) -> String {
        let ns = error as NSError
        return "\(ns.domain) \(ns.code)"
    }

    static func parseDate(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }
}
