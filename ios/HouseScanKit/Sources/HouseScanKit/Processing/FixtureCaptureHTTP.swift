import Foundation
import Synchronization

/// The capture API, answered inside this process, for synthetic runs: the DEBUG app's
/// `-photoProcessingFixture` and the package tests. No request leaves the process, so nothing can
/// be uploaded anywhere. It takes what the uploader sends as the documented API does (create,
/// register with PUT targets, PUT, commit as the only acknowledgement, finalize, events, result)
/// and ends every capture with the answer it was made with. Each answer's words say they come
/// from a fixture.
///
/// Written from the public API types in `CaptureAPI` and `CaptureResult`; it is not the service.
public final class FixtureCaptureHTTP: CaptureHTTP, Sendable {
    public enum Answer: String, Sendable, CaseIterable {
        case candidate, needsViews, manualReview, notEligible, failed, expired
        /// The run finishes, but its answer never becomes readable.
        case notReady
        /// The result read gets a body that isn't a result.
        case unreadable
        /// The service refuses to open the capture.
        case refuseCreate
        /// The run never finishes, so the capture stays in processing.
        case hold

        /// The homeowner message the answer carries, when it has an outcome.
        public var message: String? {
            switch self {
            case .candidate: "Fixture answer: a possible spot to the left of the meter."
            case .needsViews: "Fixture answer: one more view of the wall, please."
            case .manualReview: "Fixture answer: an installer needs to look at this wall."
            case .notEligible: "Fixture answer: no spot on this wall."
            case .failed, .expired, .notReady, .unreadable, .refuseCreate, .hold: nil
            }
        }

        /// The capture's status once every listed file is committed.
        var endStatus: String {
            switch self {
            case .candidate, .notEligible, .notReady, .unreadable: "complete"
            case .needsViews: "needs_views"
            case .manualReview: "manual_review"
            case .failed: "failed"
            case .expired: "expired"
            case .refuseCreate, .hold: "processing"
            }
        }
    }

    /// The API base the fixture answers for. A `.invalid` host can't resolve, so a request that
    /// somehow reached a real transport would fail rather than land somewhere.
    public static let base = URL(string: "https://capture-fixture.invalid/v1")!

    /// The ARKit epoch a candidate's box is in: the name the coordinator gives every packet's
    /// world (`CaptureSessionCoordinator.epoch`), which is main-actor isolated.
    public static let arkitEpoch = "e1"

    private struct Capture {
        var id: String
        var packetID: String
        var uploaded: Set<String> = []
        var committed: Set<String> = []
        var listed: [String]?
        var runID: String?

        var allCommitted: Bool { listed.map { $0.allSatisfy(committed.contains) } ?? false }
    }

    private struct State {
        var captures: [String: Capture] = [:]
        var byPacket: [String: String] = [:]
        var tokens: [String: (capture: String, path: String)] = [:]
        var routes: [String] = []
        var next = 1
    }

    public let answer: Answer
    private let state = Mutex(State())
    private let onRequest: @Sendable ([String]) -> Void

    /// `onRequest` hears every route so far each time a request arrives, with the fixture's lock
    /// held, so calls never overlap. It must not call back into the fixture.
    public init(answer: Answer, onRequest: @escaping @Sendable ([String]) -> Void = { _ in }) {
        self.answer = answer
        self.onRequest = onRequest
    }

    /// Every request so far as "METHOD route", ids left out: "POST captures", "PUT upload",
    /// "POST captures/files:commit", "GET captures/result".
    public var routes: [String] { state.withLock { $0.routes } }

    public func send(_ request: URLRequest) async throws -> HTTPReply {
        reply(to: request)
    }

    public func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        // The bytes stay where they are; a file that isn't there fails as a real upload would.
        guard FileManager.default.fileExists(atPath: file.path) else { throw URLError(.fileDoesNotExist) }
        return reply(to: request)
    }

    // MARK: Routing

    private func reply(to request: URLRequest) -> HTTPReply {
        let method = request.httpMethod ?? "GET"
        let url = request.url ?? Self.base
        var parts = url.path.split(separator: "/").map(String.init)
        if parts.first == "v1" { parts.removeFirst() }
        let route = Self.route(method: method, parts: parts)
        state.withLock { s in
            s.routes.append(route)
            onRequest(s.routes)
        }
        let body = request.httpBody ?? Data()
        return state.withLock { s in
            if parts.first == "upload" { return put(token: parts.last ?? "", &s) }
            if method == "POST", parts == ["captures"] { return create(body, &s) }
            guard parts.count >= 3, parts[0] == "captures", var capture = s.captures[parts[1]] else { return error(404, "capture_not_found") }
            defer { s.captures[capture.id] = capture }
            switch (method, parts[2...].joined(separator: "/")) {
            case ("POST", "files"): return register(body, &capture, &s)
            case ("POST", "files:commit"): return commit(body, &capture)
            case ("POST", "finalize"): return finalize(body, &capture, &s)
            case ("GET", "events"): return events(url, capture)
            case ("GET", "result"): return result(capture)
            default: return error(404, "route")
            }
        }
    }

    static func route(method: String, parts: [String]) -> String {
        if parts.first == "upload" { return "\(method) upload" }
        if parts.count <= 2 { return "\(method) captures" }
        return "\(method) captures/\(parts[2...].joined(separator: "/"))"
    }

    // MARK: API

    private func create(_ body: Data, _ s: inout State) -> HTTPReply {
        guard let request = try? JSONDecoder().decode(CaptureAPI.CreateRequest.self, from: body) else { return error(422, "schema") }
        if answer == .refuseCreate { return error(422, "fixture_refused") }
        let id: String
        let status: Int
        if let existing = s.byPacket[request.packetId] {
            (id, status) = (existing, 200)
        } else {
            id = "cap_fixture_\(s.next)"
            s.next += 1
            s.captures[id] = Capture(id: id, packetID: request.packetId)
            s.byPacket[request.packetId] = id
            status = 201
        }
        return json(status, ["captureId": id, "status": "uploading", "upload": ["maxBatch": 50]])
    }

    private func register(_ body: Data, _ c: inout Capture, _ s: inout State) -> HTTPReply {
        guard let request = try? JSONDecoder().decode(CaptureAPI.RegisterRequest.self, from: body) else { return error(422, "schema") }
        var files: [[String: Any]] = []
        for file in request.files {
            if c.committed.contains(file.path) {
                files.append(["path": file.path, "state": "committed"])
                continue
            }
            let token = UUID().uuidString
            s.tokens[token] = (c.id, file.path)
            files.append([
                "path": file.path, "state": "pending",
                "upload": [
                    "method": "PUT", "url": "https://capture-fixture.invalid/upload/\(token)",
                    "headers": ["Content-Type": file.contentType], "expiresAt": "2099-01-01T00:00:00Z",
                ],
            ])
        }
        return json(200, ["files": files])
    }

    private func put(token: String, _ s: inout State) -> HTTPReply {
        guard let target = s.tokens[token], var capture = s.captures[target.capture] else { return HTTPReply(status: 403, body: Data()) }
        capture.uploaded.insert(target.path)
        s.captures[capture.id] = capture
        return HTTPReply(status: 200, body: Data())
    }

    private func commit(_ body: Data, _ c: inout Capture) -> HTTPReply {
        guard let request = try? JSONDecoder().decode(CaptureAPI.CommitRequest.self, from: body) else { return error(422, "schema") }
        let paths = request.files.map(\.path)
        let committed = paths.filter(c.uploaded.contains)
        c.committed.formUnion(committed)
        return json(200, ["committed": committed, "notFound": paths.filter { !c.uploaded.contains($0) }, "mismatch": [String]()])
    }

    private func finalize(_ body: Data, _ c: inout Capture, _ s: inout State) -> HTTPReply {
        guard let packet = try? JSONDecoder().decode(Packet04.Packet.self, from: body) else { return error(422, "schema") }
        guard packet.packetId == c.packetID else { return error(422, "packet_id_mismatch") }
        if c.runID == nil {
            c.listed = packet.files.map(\.path)
            c.runID = "run_fixture_\(s.next)"
            s.next += 1
        }
        let missing = (c.listed ?? []).filter { !c.committed.contains($0) }
        return json(202, ["status": missing.isEmpty ? "processing" : "awaiting_files", "missing": missing, "runId": c.runID ?? ""])
    }

    private func events(_ url: URL, _ c: Capture) -> HTTPReply {
        let after = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "after" })?.value.flatMap(Int.init) ?? 0
        guard let runID = c.runID else { return json(200, ["status": "uploading", "next": after, "events": [Any]()]) }
        guard c.allCommitted, answer != .hold else {
            return json(200, ["status": c.allCommitted ? "processing" : "awaiting_files", "next": after, "events": [Any]()])
        }
        let ended = answer.endStatus == "failed" || answer.endStatus == "expired"
        let event: [String: Any] = [
            "seq": 1, "type": ended ? "failed" : "verdict_ready", "at": "2026-01-01T00:00:00Z", "data": ["runId": runID],
        ]
        let list: [[String: Any]] = after < 1 ? [event] : []
        return json(200, ["status": answer.endStatus, "next": 1, "events": list])
    }

    private func result(_ c: Capture) -> HTTPReply {
        guard let runID = c.runID, c.allCommitted, answer != .hold else {
            return json(200, ["runId": c.runID ?? "", "status": "processing", "viewsNeeded": [Any](), "memberActions": [Any](), "outcome": NSNull()])
        }
        switch answer {
        case .notReady: return error(404, "result_not_ready")
        case .unreadable: return HTTPReply(status: 200, body: Data("<html>not a result</html>".utf8))
        default: break
        }
        let views: [[String: Any]] = answer == .needsViews ? [[
            "id": "view_fixture_1", "kind": "wall_band", "criteria": [String](), "why": "unseen", "promptId": "prompt_fixture_1",
            "prompt": ["title": "Fixture: show the ground below the meter", "body": "Fixture: step back until the ground below the meter is in view."],
        ]] : []
        var outcome: Any = NSNull()
        if let message = answer.message {
            let kind = switch answer {
            case .candidate: "eligible"
            case .needsViews: "needs_more_photos"
            case .manualReview: "manual_review"
            default: "not_eligible"
            }
            var decided: [String: Any] = ["kind": kind, "profile": "fixture", "message": message, "viewsNeeded": [Any](), "reasons": [Any]()]
            if answer == .candidate {
                // A box in the packet's ARKit world, so AR is withheld for the right reasons
                // (analysis nobody verified) rather than for a missing placement.
                let identity: [Double] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -0.9, 0.6, 0.2, 1]
                decided["arkitEpoch"] = Self.arkitEpoch
                decided["recommendedPlacement"] = [
                    "wallId": "wall_fixture", "startSM": -1.2, "confidence": 0.5,
                    "boxArkitWorld": ["pose": identity, "size": [0.7, 1.2, 0.3]],
                ]
            }
            outcome = decided
        }
        return json(200, [
            "runId": runID, "status": answer.endStatus, "viewsNeeded": views, "memberActions": [Any](), "outcome": outcome,
        ])
    }

    // MARK: Plumbing

    private func json(_ status: Int, _ object: [String: Any]) -> HTTPReply {
        let body = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data()
        return HTTPReply(status: status, body: body)
    }

    private func error(_ status: Int, _ code: String) -> HTTPReply {
        json(status, ["errors": [["code": code]]])
    }
}
