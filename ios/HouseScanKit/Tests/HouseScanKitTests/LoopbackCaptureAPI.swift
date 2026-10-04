import CryptoKit
import Foundation
import Network
import Synchronization
import HouseScanKit

/// A local stand-in for the capture API over real HTTP on 127.0.0.1, so the tests drive the
/// actual `URLSession` transport. It implements the documented behaviour the uploader relies on:
/// idempotent create and finalize bound to the exact body, register with signed PUT URLs,
/// Content-MD5 checked on the PUT, commit as the only acknowledgement, early finalize with
/// `awaiting_files`, and events leading to a result. Faults are injected per test.
///
/// Written for these tests from the public description of the API; it is not the server's code.
final class LoopbackCaptureAPI: Sendable {
    struct Request: Sendable {
        var method: String
        var path: String
        var query: [String: String]
        var headers: [String: String]
        var body: Data
    }

    struct Registration {
        var sha256: String
        var bytes: Int
        var md5: String
        var contentType: String
    }

    struct Capture {
        var id: String
        var packetID: String
        var createBody: Data
        var registered: [String: Registration] = [:]
        var stored: [String: Data] = [:]
        var committed: Set<String> = []
        var packetSHA: String?
        var listed: [String] = []
        var runID: String?
        var events: [(type: String, data: [String: String])] = []
        var status = "uploading"
        /// Accepted a finalize, then lost it to a storage failure: no run until it is sent again.
        var finalizeLost = false
    }

    struct State {
        var captures: [String: Capture] = [:]
        var byPacket: [String: String] = [:]
        var tokens: [String: (capture: String, path: String)] = [:]
        var log: [Request] = []
        var nextID = 1
        /// Close the connection without answering the next request whose route matches.
        var dropNext: Set<String> = []
        /// Answer the next N storage PUTs 403, as an expired signature.
        var expireNextPuts = 0
        /// Answer the next N storage PUTs 400 BadDigest.
        var badDigestPuts = 0
        /// Accept the next finalize, then report it lost with `failed` / `retry_finalize`.
        var loseNextFinalize = false
        /// The homeowner message the result carries.
        var resultMessage = "The loopback receiver's test result."
        /// Answer the next N result reads 404 `result_not_ready`: the run is done but its answer
        /// is not readable yet.
        var notReadyResults = 0
        /// Answer the next N result reads 200 with the capture's status and run but a null
        /// outcome, the body the API serves before the run has written its answer.
        var outcomelessResults = 0
        /// Answer the next N result reads 200 with a body that is not JSON.
        var unreadableResults = 0
        /// Answer the next N result reads 200 with an outcome that has a kind but none of the
        /// other fields an outcome must carry.
        var malformedOutcomeResults = 0
        /// The status a capture ends in once every listed file is committed. Only `manual_review`
        /// ends with an outcome; `failed` ends with a `failed` event and none.
        var endStatus = "manual_review"
        /// Every register answer gives this URL for its PUTs instead of the loopback's own.
        var uploadURLOverride: String?
        /// Register answers leave out every file, as if the server never saw the request's list.
        var registerOmitsFiles = false
        /// Commits never acknowledge a file: every path comes back in `notFound`.
        var commitNeverAcknowledges = false
        /// Once every listed file is committed, the next N event polls answer at once with status
        /// `processing` and no events, as a server that doesn't hold the poll would; then the run ends.
        var processingPolls = 0
        /// Routes ("PUT upload", "POST captures", ...) answered `307` with the given `Location`, as
        /// a server sending the request somewhere else would.
        var redirects: [String: String] = [:]
        /// Answer the next commit with this split instead of the truth.
        var commitOverride: ((committed: [String], notFound: [String], mismatch: [String]))?
        /// Routes whose answers wait until `release` is called.
        var held: Set<String> = []
        var parked: [String: [@Sendable () -> Void]] = [:]
    }

    let state = Mutex(State())
    private let listener: NWListener
    private let queue = DispatchQueue(label: "loopback-capture-api")
    var port: UInt16 { listener.port?.rawValue ?? 0 }

    init() throws {
        let parameters = NWParameters.tcp
        parameters.acceptLocalOnly = true
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        let ready = DispatchSemaphore(value: 0)
        listener.stateUpdateHandler = { if case .ready = $0 { ready.signal() } }
        listener.newConnectionHandler = { [queue] connection in
            connection.start(queue: queue)
            Self.receive(connection, buffer: Data()) { request in self.handle(request, connection) }
        }
        listener.start(queue: queue)
        guard ready.wait(timeout: .now() + 5) == .success, listener.port != nil else { throw URLError(.cannotConnectToHost) }
    }

    deinit { listener.cancel() }

    var base: URL { URL(string: "http://127.0.0.1:\(port)/v1")! }

    func requests(_ route: String) -> [Request] { state.withLock { $0.log.filter { Self.route($0) == route } } }

    func release(_ route: String) {
        let waiting = state.withLock { s -> [@Sendable () -> Void] in
            s.held.remove(route)
            return s.parked.removeValue(forKey: route) ?? []
        }
        waiting.forEach { $0() }
    }

    /// "POST /v1/captures/{id}/files" style names, ids replaced.
    static func route(_ r: Request) -> String {
        let parts = r.path.split(separator: "/").map(String.init)
        if parts.first == "upload" { return "\(r.method) upload" }
        guard parts.count >= 2 else { return "\(r.method) \(r.path)" }
        if parts.count == 2 { return "\(r.method) captures" }
        return "\(r.method) captures/\(parts[2...].joined(separator: "/"))"
    }

    // MARK: HTTP over the connection

    private static func receive(_ connection: NWConnection, buffer: Data, handler: @escaping @Sendable (Request) -> Void) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { data, _, done, error in
            var buffer = buffer
            if let data { buffer.append(data) }
            if let request = parse(buffer) { return handler(request) }
            if done || error != nil { return connection.cancel() }
            receive(connection, buffer: buffer, handler: handler)
        }
    }

    private static func parse(_ data: Data) -> Request? {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: data[..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
        let line = head[0].split(separator: " ")
        guard line.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for field in head.dropFirst() {
            guard let colon = field.firstIndex(of: ":") else { continue }
            headers[field[..<colon].lowercased()] = field[field.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        let body = data[end.upperBound...]
        guard body.count >= length else { return nil }
        let target = URLComponents(string: String(line[1]))
        var query: [String: String] = [:]
        for item in target?.queryItems ?? [] { query[item.name] = item.value }
        var path = target?.path ?? ""
        if path.hasPrefix("/v1/") { path.removeFirst(4) } else if path.hasPrefix("/") { path.removeFirst() }
        return Request(method: String(line[0]), path: path, query: query, headers: headers, body: Data(body.prefix(length)))
    }

    private func handle(_ request: Request, _ connection: NWConnection) {
        let route = Self.route(request)
        let (drop, hold, redirect) = state.withLock { s -> (Bool, Bool, String?) in
            s.log.append(request)
            return (s.dropNext.remove(route) != nil, s.held.contains(route), s.redirects[route])
        }
        let (status, body) = redirect == nil ? answer(request) : (307, Data())
        let location = redirect.map { "Location: \($0)\r\n" } ?? ""
        let send: @Sendable () -> Void = {
            if drop { return connection.cancel() }
            let head = "HTTP/1.1 \(status) X\r\n\(location)Content-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n"
            connection.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in connection.cancel() })
        }
        if hold {
            state.withLock { $0.parked[route, default: []].append(send) }
        } else {
            send()
        }
    }

    // MARK: API behaviour

    private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) }
    private func error(_ status: Int, _ code: String) -> (Int, Data) { (status, json(["errors": [["code": code, "pointer": "/", "message": code]]])) }

    private func answer(_ r: Request) -> (Int, Data) {
        state.withLock { s in
            let parts = r.path.split(separator: "/").map(String.init)
            if parts.first == "upload" { return put(r, token: parts.last ?? "", &s) }
            if r.headers["authorization"] != nil { return error(400, "unexpected_authorization") }
            if r.method == "POST", parts == ["captures"] { return create(r, &s) }
            guard parts.count >= 2, var capture = s.captures[parts[1]] else { return error(404, "capture_not_found") }
            defer { s.captures[capture.id] = capture }
            switch (r.method, parts.dropFirst(2).joined(separator: "/")) {
            case ("POST", "files"): return register(r, &capture, &s)
            case ("POST", "files:commit"): return commit(r, &capture, &s)
            case ("POST", "finalize"): return finalize(r, &capture, &s)
            case ("GET", "events"): return events(r, &capture, &s)
            case ("GET", "result"):
                if s.notReadyResults > 0 {
                    s.notReadyResults -= 1
                    return error(404, "result_not_ready")
                }
                if s.unreadableResults > 0 {
                    s.unreadableResults -= 1
                    return (200, Data("<html>not a result</html>".utf8))
                }
                if s.malformedOutcomeResults > 0 {
                    s.malformedOutcomeResults -= 1
                    return (200, json(["runId": capture.runID ?? "", "status": capture.status, "viewsNeeded": [], "memberActions": [],
                                       "outcome": ["kind": "manual_review"]]))
                }
                let withheld = s.outcomelessResults > 0
                if withheld { s.outcomelessResults -= 1 }
                let outcome: Any = capture.status == "manual_review" && !withheld
                    ? ["kind": "manual_review", "profile": "C", "message": s.resultMessage, "viewsNeeded": [], "reasons": []] as [String: Any]
                    : NSNull()
                return (200, json(["runId": capture.runID ?? "", "status": capture.status, "viewsNeeded": [], "memberActions": [], "outcome": outcome]))
            default: return error(404, "route")
            }
        }
    }

    private func create(_ r: Request, _ s: inout State) -> (Int, Data) {
        guard let body = try? JSONSerialization.jsonObject(with: r.body) as? [String: Any], let packetID = body["packetId"] as? String else {
            return error(422, "schema")
        }
        guard let key = r.headers["idempotency-key"] else { return error(422, "idempotency_key_missing") }
        guard key == packetID else { return error(422, "idempotency_key_mismatch") }
        guard body["formatVersion"] as? String == "0.4" else { return error(422, "format_version_unsupported") }
        let status: Int
        let capture: Capture
        if let existing = s.byPacket[packetID].flatMap({ s.captures[$0] }) {
            guard existing.createBody == r.body else { return error(409, "idempotency_conflict") }
            (status, capture) = (200, existing)
        } else {
            capture = Capture(id: "cap_\(s.nextID)", packetID: packetID, createBody: r.body)
            s.nextID += 1
            s.captures[capture.id] = capture
            s.byPacket[packetID] = capture.id
            status = 201
        }
        return (status, json([
            "captureId": capture.id, "status": "uploading", "eventsUrl": "/v1/captures/\(capture.id)/events",
            "finalizeBy": "2099-01-01T00:00:00Z", "upload": ["maxSinglePutBytes": 16_777_216, "maxBatch": 50, "urlTtlS": 3600, "maxFiles": 2000, "partBytes": 8_388_608],
        ]))
    }

    private func register(_ r: Request, _ c: inout Capture, _ s: inout State) -> (Int, Data) {
        guard let body = try? JSONSerialization.jsonObject(with: r.body) as? [String: Any], let files = body["files"] as? [[String: Any]],
              (1...50).contains(files.count) else { return error(422, "batch_too_large") }
        var out: [[String: Any]] = []
        for file in files {
            guard let path = file["path"] as? String, Packet04.isRelPath(path), let sha = file["sha256"] as? String, sha.count == 64,
                  let bytes = file["bytes"] as? Int, let md5 = file["md5"] as? String, Data(base64Encoded: md5)?.count == 16,
                  let role = file["role"] as? String, Packet04.Role(rawValue: role) != nil, let type = file["contentType"] as? String
            else { return error(422, "schema") }
            let meta = file["meta"] as? [String: Any]
            let image = role == "keyframe" || role == "still"
            if image != (meta != nil) || (meta.map { $0.count != 1 } ?? false) { return error(422, "meta_mismatch") }
            if let previous = c.registered[path], previous.sha256 != sha { return error(409, "sha256_conflict") }
            if c.packetSHA != nil, !c.listed.contains(path) { return error(409, "not_in_manifest") }
            c.registered[path] = Registration(sha256: sha, bytes: bytes, md5: md5, contentType: type)
            if c.committed.contains(path) {
                out.append(["path": path, "state": "committed"])
                continue
            }
            if s.registerOmitsFiles { continue }
            let token = UUID().uuidString
            s.tokens[token] = (c.id, path)
            out.append(["path": path, "state": "pending", "upload": [
                "method": "PUT", "url": s.uploadURLOverride ?? "http://127.0.0.1:\(port)/upload/\(token)",
                "headers": ["Content-Type": type, "Content-MD5": md5], "expiresAt": "2099-01-01T00:00:00Z",
            ]])
        }
        return (200, json(["files": out]))
    }

    private func put(_ r: Request, token: String, _ s: inout State) -> (Int, Data) {
        guard r.method == "PUT", let (captureID, path) = s.tokens[token], var c = s.captures[captureID], let reg = c.registered[path] else {
            return (403, Data("token_invalid".utf8))
        }
        if r.headers["authorization"] != nil { return (400, Data("authorization sent to storage".utf8)) }
        if s.badDigestPuts > 0 {
            s.badDigestPuts -= 1
            return (400, Data("<Error><Code>BadDigest</Code></Error>".utf8))
        }
        if s.expireNextPuts > 0 {
            s.expireNextPuts -= 1
            s.tokens[token] = nil
            return (403, Data("<Error><Code>AccessDenied</Code><Message>Request has expired</Message></Error>".utf8))
        }
        guard r.headers["content-md5"] == reg.md5, Data(Insecure.MD5.hash(data: r.body)).base64EncodedString() == reg.md5,
              r.headers["content-type"] == reg.contentType
        else { return (400, Data("<Error><Code>BadDigest</Code></Error>".utf8)) }
        c.stored[path] = r.body
        s.captures[captureID] = c
        return (200, Data())
    }

    private func commit(_ r: Request, _ c: inout Capture, _ s: inout State) -> (Int, Data) {
        guard let body = try? JSONSerialization.jsonObject(with: r.body) as? [String: Any], let files = body["files"] as? [[String: Any]] else {
            return error(422, "schema")
        }
        var committed: [String] = [], notFound: [String] = [], mismatch: [String] = []
        for file in files {
            guard let path = file["path"] as? String, let sha = file["sha256"] as? String else { return error(422, "schema") }
            guard let data = c.stored[path] else { notFound.append(path); continue }
            if PacketFiles.sha256(data) != sha || c.registered[path]?.sha256 != sha { mismatch.append(path); continue }
            committed.append(path)
        }
        if let forced = s.commitOverride {
            s.commitOverride = nil
            (committed, notFound, mismatch) = forced
        }
        if s.commitNeverAcknowledges {
            notFound += committed + mismatch
            committed = []
            mismatch = []
        }
        c.committed.formUnion(committed)
        if !committed.isEmpty { c.events.append(("files_committed", ["committed": String(c.committed.count)])) }
        startIfComplete(&c, endStatus: s.endStatus)
        return (200, json(["committed": committed, "notFound": notFound, "mismatch": mismatch]))
    }

    private func finalize(_ r: Request, _ c: inout Capture, _ s: inout State) -> (Int, Data) {
        guard r.headers["idempotency-key"] == c.packetID else { return error(422, "idempotency_key_mismatch") }
        let sha = PacketFiles.sha256(r.body)
        if let declared = r.headers["x-packet-sha256"], declared != sha { return error(422, "packet_sha_mismatch") }
        if let previous = c.packetSHA, previous != sha { return error(409, "packet_sha_conflict") }
        guard let packet = try? JSONDecoder().decode(Packet04.Packet.self, from: r.body) else { return error(422, "schema") }
        guard packet.packetId == c.packetID else { return error(422, "packet_id_mismatch") }
        let problems = Packet04Check.problems(packet, folder: nil)
        if !problems.isEmpty { return error(422, "schema") }
        for entry in packet.files {
            if let reg = c.registered[entry.path], reg.sha256 != entry.sha256 || reg.bytes != entry.bytes { return error(422, "sha256_mismatch") }
        }
        if c.packetSHA == nil {
            c.packetSHA = sha
            c.listed = packet.files.map(\.path)
            c.runID = "run_" + sha.prefix(16)
            c.status = "awaiting_files"
            if s.loseNextFinalize {
                s.loseNextFinalize = false
                c.finalizeLost = true
                c.events.append(("failed", ["code": "storage_unavailable", "next": "retry_finalize"]))
            }
            startIfComplete(&c, endStatus: s.endStatus)
        } else if c.finalizeLost {
            c.finalizeLost = false
            startIfComplete(&c, endStatus: s.endStatus)
        }
        let missing = c.listed.filter { !c.committed.contains($0) }
        return (202, json(["status": missing.isEmpty ? "processing" : "awaiting_files", "missing": missing, "runId": c.runID!, "etaS": 1]))
    }

    private func startIfComplete(_ c: inout Capture, endStatus: String) {
        guard c.packetSHA != nil, !c.finalizeLost, c.status == "awaiting_files", c.listed.allSatisfy(c.committed.contains) else { return }
        c.status = endStatus
        c.events.append(("stage", ["stage": "ingest", "status": "ok", "runId": c.runID!]))
        if endStatus == "manual_review" {
            c.events.append(("verdict_ready", ["kind": "manual_review", "runId": c.runID!]))
        } else {
            c.events.append(("failed", ["code": "loopback_run_failed", "runId": c.runID!]))
        }
    }

    private func events(_ r: Request, _ c: inout Capture, _ s: inout State) -> (Int, Data) {
        if c.status == "manual_review" || c.status == "failed", s.processingPolls > 0, c.runID != nil {
            s.processingPolls -= 1
            let after = Int(r.query["after"] ?? "0") ?? 0
            return (200, json(["status": "processing", "next": after, "events": [] as [Any]]))
        }
        let after = Int(r.query["after"] ?? "0") ?? 0
        let list = c.events.enumerated().filter { $0.offset + 1 > after }.map { index, event in
            ["seq": index + 1, "type": event.type, "at": "2026-09-27T00:00:00Z", "data": event.data] as [String: Any]
        }
        return (200, json(["status": c.status, "next": c.events.count, "events": list]))
    }
}
