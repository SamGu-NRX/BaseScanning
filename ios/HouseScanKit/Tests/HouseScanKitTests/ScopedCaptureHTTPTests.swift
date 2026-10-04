import Foundation
import HouseScanKit
import Synchronization
import Testing

/// The capture API credential reaches the configured API and nothing else. Two kinds of test:
/// a recording transport shows exactly which request would leave and with what headers (no
/// socket), and `LoopbackPlacementServer` runs the real `URLSessionCaptureHTTP` over HTTP on
/// 127.0.0.1 for what only a socket shows: the header on the wire and a refused redirect.
@Suite struct ScopedCaptureHTTPTests {
    static let base = URL(string: "https://api.example.com/v1")!
    static let token = "eyJhbGciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl"

    /// Records what would be sent. Not a socket.
    final class Recorder: CaptureHTTP, Sendable {
        let sent = Mutex<[(kind: String, request: URLRequest)]>([])

        func send(_ request: URLRequest) async throws -> HTTPReply {
            sent.withLock { $0.append(("send", request)) }
            return HTTPReply(status: 200, body: Data("{}".utf8))
        }

        func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
            sent.withLock { $0.append(("upload", request)) }
            return HTTPReply(status: 200, body: Data())
        }
    }

    /// A provider that counts its calls and returns `tokens` in order, the last repeating.
    final class Provider: Sendable {
        let calls = Mutex(0)
        let tokens: [String]
        init(_ tokens: [String] = [ScopedCaptureHTTPTests.token]) { self.tokens = tokens }

        var credential: ScopedCaptureHTTP.Credential {
            { [self] in
                let index = calls.withLock { count in
                    defer { count += 1 }
                    return count
                }
                return tokens[min(index, tokens.count - 1)]
            }
        }
    }

    static func scoped(_ provider: Provider, inner: any CaptureHTTP, base: URL = base, transport: CaptureAPIScope.Transport = .httpsOnly) throws
        -> ScopedCaptureHTTP
    {
        ScopedCaptureHTTP(scope: try CaptureAPIScope(base: base, transport: transport), credential: provider.credential, inner: inner)
    }

    static func request(_ url: String, method: String = "POST") -> URLRequest {
        var request = URLRequest(url: URL(string: url)!)
        request.httpMethod = method
        request.httpBody = Data(#"{"packetId":"p1"}"#.utf8)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("p1", forHTTPHeaderField: "Idempotency-Key")
        return request
    }

    // MARK: API requests (request inspection, no socket)

    @Test func anAPIRequestCarriesTheCredentialAndIsOtherwiseUnchanged() async throws {
        let recorder = Recorder()
        let provider = Provider()
        let http = try Self.scoped(provider, inner: recorder)
        _ = try await http.send(Self.request("https://api.example.com/v1/captures"))
        let sent = try #require(recorder.sent.withLock { $0.first })
        #expect(sent.kind == "send")
        #expect(sent.request.value(forHTTPHeaderField: "Authorization") == "Bearer \(Self.token)")
        #expect(sent.request.httpMethod == "POST")
        #expect(sent.request.httpBody == Data(#"{"packetId":"p1"}"#.utf8))
        #expect(sent.request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(sent.request.value(forHTTPHeaderField: "Idempotency-Key") == "p1")
        #expect(provider.calls.withLock { $0 } == 1)
    }

    /// The base path itself, its subpaths, a case-different host and an explicit default port
    /// are all the same API.
    @Test(arguments: [
        "https://api.example.com/v1", "https://api.example.com/v1/captures/c1/events?wait=25",
        "https://API.example.com/v1/captures", "https://api.example.com:443/v1/captures",
    ])
    func everyRequestInsideTheScopeIsAllowed(url: String) async throws {
        let recorder = Recorder()
        _ = try await Self.scoped(Provider(), inner: recorder).send(Self.request(url))
        #expect(recorder.sent.withLock { $0.count } == 1)
    }

    /// Each of these is refused before the provider is asked for anything, and nothing is sent.
    @Test(arguments: [
        ("https://api.example.com.evil.test/v1/captures", "host"),
        ("https://evilapi.example.com/v1/captures", "host"),
        ("https://example.com/v1/captures", "host"),
        ("https://api.example.com./v1/captures", "host"),
        ("https://api.example.com:8443/v1/captures", "port"),
        ("http://api.example.com/v1/captures", "scheme"),
        ("https://user:pass@api.example.com/v1/captures", "userinfo"),
        ("https://api.example.com/v10/captures", "path"),
        ("https://api.example.com/other", "path"),
        ("https://api.example.com/", "path"),
        ("https://api.example.com/v1/../admin", "path_segments"),
        ("https://api.example.com/v1/%2e%2e/admin", "path_segments"),
        ("https://api.example.com/v1/captures%2F..%2Fadmin", "path_segments"),
    ])
    func aRequestOutsideTheScopeIsRefusedBeforeTheCredentialIsRead(url: String, reason: String) async throws {
        let recorder = Recorder()
        let provider = Provider()
        let http = try Self.scoped(provider, inner: recorder)
        await #expect(throws: ScopedCaptureHTTPError.destinationNotAllowed(reason)) {
            try await http.send(Self.request(url))
        }
        #expect(provider.calls.withLock { $0 } == 0)
        #expect(recorder.sent.withLock { $0.isEmpty })
    }

    /// The provider is asked for every API request, so a token it refreshes reaches the next
    /// request, at the same fixed scope.
    @Test func aRefreshedCredentialIsUsedForTheNextRequest() async throws {
        let recorder = Recorder()
        let provider = Provider(["first-token", "second-token"])
        let http = try Self.scoped(provider, inner: recorder)
        _ = try await http.send(Self.request("https://api.example.com/v1/captures"))
        _ = try await http.send(Self.request("https://api.example.com/v1/captures/c1/files"))
        let headers = recorder.sent.withLock { $0.map { $0.request.value(forHTTPHeaderField: "Authorization") } }
        #expect(headers == ["Bearer first-token", "Bearer second-token"])
        await #expect(throws: ScopedCaptureHTTPError.destinationNotAllowed("host")) {
            try await http.send(Self.request("https://other.example.com/v1/captures"))
        }
        #expect(provider.calls.withLock { $0 } == 2)
    }

    /// An empty token, padding alone, or one with a space, quote or line break is refused, and
    /// the error names no part of it.
    @Test(arguments: ["", "==", "two words", "line\r\nX-Injected: 1", "quote\"d", "tab\tbed", "café"])
    func aMalformedCredentialIsRefusedWithoutItsValue(token: String) async throws {
        let recorder = Recorder()
        let http = try Self.scoped(Provider([token]), inner: recorder)
        let thrown = await #expect(throws: ScopedCaptureHTTPError.self) {
            try await http.send(Self.request("https://api.example.com/v1/captures"))
        }
        #expect(thrown == .credentialMalformed)
        #expect(recorder.sent.withLock { $0.isEmpty })
        if !token.isEmpty { #expect(!String(describing: thrown).contains(token)) }
    }

    @Test(arguments: ["abc", "a.b-c_d~e+f/g", "dGVzdA==", ScopedCaptureHTTPTests.token])
    func wellFormedBearerTokensAreSent(token: String) async throws {
        let recorder = Recorder()
        _ = try await Self.scoped(Provider([token]), inner: recorder).send(Self.request("https://api.example.com/v1/captures"))
        #expect(recorder.sent.withLock { $0.first?.request.value(forHTTPHeaderField: "Authorization") } == "Bearer \(token)")
    }

    /// A provider that can't supply a credential stops the request. Nothing goes out
    /// unauthenticated, and the error carries the type, not the provider's message.
    @Test func anUnavailableCredentialSendsNothing() async throws {
        struct Expired: Error, CustomStringConvertible { var description: String { "token sk-live-secret expired" } }
        let recorder = Recorder()
        let http = ScopedCaptureHTTP(scope: try CaptureAPIScope(base: Self.base), credential: { throw Expired() }, inner: recorder)
        let thrown = await #expect(throws: ScopedCaptureHTTPError.self) {
            try await http.send(Self.request("https://api.example.com/v1/captures"))
        }
        #expect(thrown == .credentialUnavailable("Expired"))
        #expect(!String(describing: thrown).contains("sk-live-secret"))
        #expect(recorder.sent.withLock { $0.isEmpty })
    }

    /// Holds the credential provider until the test releases it, ignoring cancellation while it
    /// waits, as a provider that doesn't watch for it would.
    final class Gate: Sendable {
        private let waiting = Mutex<CheckedContinuation<Void, Never>?>(nil)
        private let enteredStream: AsyncStream<Void>
        private let enteredContinuation: AsyncStream<Void>.Continuation

        init() {
            (enteredStream, enteredContinuation) = AsyncStream<Void>.makeStream()
        }

        /// Waits for `release`; reports to `entered` once it is waiting.
        func wait() async {
            await withCheckedContinuation { continuation in
                waiting.withLock { $0 = continuation }
                enteredContinuation.yield()
            }
        }

        func entered() async {
            for await _ in enteredStream { return }
        }

        func release() {
            waiting.withLock {
                $0?.resume()
                $0 = nil
            }
        }
    }

    /// Cancelled while the provider is waiting, the request isn't sent, even though the
    /// provider then returns a well-formed token.
    @Test func aRequestCancelledDuringTheCredentialFetchIsNeverSent() async throws {
        let recorder = Recorder()
        let gate = Gate()
        let http = ScopedCaptureHTTP(
            scope: try CaptureAPIScope(base: Self.base),
            credential: {
                await gate.wait()
                return ScopedCaptureHTTPTests.token
            },
            inner: recorder)
        let request = Self.request("https://api.example.com/v1/captures")
        let task = Task { try await http.send(request) }
        await gate.entered()
        task.cancel()
        gate.release()
        let result = await task.result
        #expect(throws: CancellationError.self) { try result.get() }
        #expect(recorder.sent.withLock { $0.isEmpty })
    }

    /// A request already cancelled doesn't reach the provider at all.
    @Test func aRequestCancelledBeforeTheCredentialFetchNeverReachesTheProvider() async throws {
        let recorder = Recorder()
        let provider = Provider()
        let http = try Self.scoped(provider, inner: recorder)
        let request = Self.request("https://api.example.com/v1/captures")
        let task = Task { () async throws -> HTTPReply in
            withUnsafeCurrentTask { $0?.cancel() }
            return try await http.send(request)
        }
        let result = await task.result
        #expect(throws: CancellationError.self) { try result.get() }
        #expect(provider.calls.withLock { $0 } == 0)
        #expect(recorder.sent.withLock { $0.isEmpty })
    }

    /// An Authorization the caller already set is replaced, never sent alongside or instead.
    @Test func anAPIRequestCarriesOnlyTheIssuedCredential() async throws {
        let recorder = Recorder()
        var request = Self.request("https://api.example.com/v1/captures")
        request.setValue("Bearer stale", forHTTPHeaderField: "Authorization")
        _ = try await Self.scoped(Provider(), inner: recorder).send(request)
        #expect(recorder.sent.withLock { $0.first?.request.value(forHTTPHeaderField: "Authorization") } == "Bearer \(Self.token)")
    }

    // MARK: Storage uploads (request inspection, no socket)

    /// A signed PUT goes out exactly as built, without asking the provider, even to the API's
    /// own origin.
    @Test(arguments: ["https://storage.example.com/bucket/k0.jpg?X-Goog-Signature=abc", "https://api.example.com/v1/upload/token-1"])
    func aSignedPutIsSentUnchangedAndWithoutTheCredential(url: String) async throws {
        let recorder = Recorder()
        let provider = Provider()
        var request = URLRequest(url: URL(string: url)!)
        request.httpMethod = "PUT"
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue("rL0Y20zC+Fzt72VPzMSk2A==", forHTTPHeaderField: "Content-MD5")
        _ = try await Self.scoped(provider, inner: recorder).upload(request, file: URL(fileURLWithPath: "/dev/null"))
        let sent = try #require(recorder.sent.withLock { $0.first })
        #expect(sent.kind == "upload")
        #expect(sent.request == request)
        #expect(sent.request.value(forHTTPHeaderField: "Authorization") == nil)
        #expect(provider.calls.withLock { $0 } == 0)
    }

    /// The chosen policy: storage requests carry their signature in the URL, so an Authorization
    /// header on one is refused, not forwarded and not stripped.
    @Test func aStorageRequestCarryingAuthorizationIsRefused() async throws {
        let recorder = Recorder()
        let provider = Provider()
        var request = URLRequest(url: URL(string: "https://storage.example.com/bucket/k0.jpg?sig=abc")!)
        request.httpMethod = "PUT"
        request.setValue("Bearer leaked", forHTTPHeaderField: "Authorization")
        let http = try Self.scoped(provider, inner: recorder)
        await #expect(throws: ScopedCaptureHTTPError.storageRequestHasAuthorization) {
            try await http.upload(request, file: URL(fileURLWithPath: "/dev/null"))
        }
        #expect(recorder.sent.withLock { $0.isEmpty })
        #expect(provider.calls.withLock { $0 } == 0)
    }

    // MARK: The scope itself

    @Test(arguments: [
        ("http://api.example.com/v1", "base_insecure"), ("https://u:p@api.example.com/v1", "base_userinfo"),
        ("https://api.example.com/v1?x=1", "base_query"), ("https://api.example.com/v1/../x", "base_path"),
        ("ftp://api.example.com/v1", "base_insecure"),
    ])
    func aBaseThatCouldNotReceiveACredentialIsRefused(base: String, reason: String) {
        #expect(throws: ScopedCaptureHTTPError.destinationNotAllowed(reason)) { try CaptureAPIScope(base: URL(string: base)!) }
    }

    /// Plain http is allowed only by name and only to this machine.
    @Test func loopbackHTTPIsAnExplicitLocalOnlyChoice() throws {
        #expect(throws: ScopedCaptureHTTPError.destinationNotAllowed("base_insecure")) {
            try CaptureAPIScope(base: URL(string: "http://127.0.0.1:8080/v1")!)
        }
        #expect(throws: ScopedCaptureHTTPError.destinationNotAllowed("base_insecure")) {
            try CaptureAPIScope(base: URL(string: "http://api.example.com/v1")!, transport: .loopbackHTTP)
        }
        let scope = try CaptureAPIScope(base: URL(string: "http://127.0.0.1:8080/v1")!, transport: .loopbackHTTP)
        #expect(scope.problem(with: URL(string: "http://127.0.0.1:8080/v1/captures")!) == nil)
        #expect(scope.problem(with: URL(string: "http://127.0.0.1:8081/v1/captures")!) == "port")
        #expect(scope.problem(with: URL(string: "http://localhost:8080/v1/captures")!) == "host")
    }

    // MARK: Over a real socket

    /// The real transport on 127.0.0.1: an API request arrives with the credential, a storage
    /// PUT to the same origin arrives without it, and a redirect from either is not followed,
    /// so the second listener receives nothing.
    @Test func overHTTPTheCredentialReachesOnlyTheAPIAndNoRedirectTarget() async throws {
        let elsewhere = try LoopbackPlacementServer { _ in .init(status: 200, body: Data("{}".utf8)) }
        let target = elsewhere.base.absoluteString
        let api = try LoopbackPlacementServer { request in
            if request.path.hasSuffix("/redirect") || request.path.hasSuffix("/upload/redirect") {
                return .init(status: 307, headers: ["Location": "\(target)\(request.path)"], body: Data())
            }
            return .init(status: 200, body: Data("{}".utf8))
        }
        let provider = Provider()
        let http = try Self.scoped(
            provider, inner: URLSessionCaptureHTTP.ephemeral(timeout: 10),
            base: api.base.appending(path: "v1"), transport: .loopbackHTTP)

        let sent = try await http.send(Self.request("\(api.base.absoluteString)/v1/captures"))
        #expect(sent.status == 200)
        let redirected = try await http.send(Self.request("\(api.base.absoluteString)/v1/redirect"))
        #expect(redirected.status == 307)

        let file = FileManager.default.temporaryDirectory.appending(path: "scoped-\(UUID().uuidString).bin")
        try Data("photo".utf8).write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        for path in ["upload/token-1", "upload/redirect"] {
            var put = URLRequest(url: URL(string: "\(api.base.absoluteString)/v1/\(path)")!)
            put.httpMethod = "PUT"
            put.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
            _ = try await http.upload(put, file: file)
        }

        let seen = api.requests
        #expect(seen.map(\.path) == ["/v1/captures", "/v1/redirect", "/v1/upload/token-1", "/v1/upload/redirect"])
        #expect(seen[0].headers["authorization"] == "Bearer \(Self.token)")
        #expect(seen[1].headers["authorization"] == "Bearer \(Self.token)")
        #expect(seen[2].headers["authorization"] == nil)
        #expect(seen[3].headers["authorization"] == nil)
        #expect(seen[2].body == Data("photo".utf8))
        #expect(elsewhere.requests.isEmpty)
        #expect(provider.calls.withLock { $0 } == 2)
    }
}
