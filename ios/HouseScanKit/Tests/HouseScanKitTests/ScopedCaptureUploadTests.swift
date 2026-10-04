import Foundation
import HouseScanKit
import Synchronization
import Testing

/// The whole uploader through `ScopedCaptureHTTP`, over real HTTP to the loopback capture API
/// with an expected bearer (`LoopbackCaptureAPI.State.expectedBearer`). The credential reaches
/// every API request and no signed PUT. A refusal at the credential boundary ends the upload at
/// once with its code, instead of being retried as a network failure would be.
@Suite struct ScopedCaptureUploadTests {
    typealias Rig = CaptureUploaderTests.Rig
    typealias Provider = ScopedCaptureHTTPTests.Provider
    static let token = "loopback-capture-token"

    /// Every backoff the uploader asked for. Empty means it never waited to retry.
    final class Sleeps: Sendable {
        private let values = Mutex<[Double]>([])
        var isEmpty: Bool { values.withLock { $0.isEmpty } }
        var record: @Sendable (Double) async throws -> Void { { [self] seconds in values.withLock { $0.append(seconds) } } }
    }

    /// A loopback API expecting `token`, and an uploader on it whose transport is scoped to
    /// `scopeBase` (the loopback's own base unless a test points it elsewhere).
    static func rig(
        provider: Provider, scopeBase: ((LoopbackCaptureAPI) -> URL)? = nil, sleeps: Sleeps,
        configure: (inout LoopbackCaptureAPI.State) -> Void = { _ in }
    ) throws -> Rig {
        let server = try LoopbackCaptureAPI()
        server.state.withLock {
            $0.expectedBearer = token
            configure(&$0)
        }
        let scope = try CaptureAPIScope(base: scopeBase?(server) ?? server.base, transport: .loopbackHTTP)
        let http = ScopedCaptureHTTP(scope: scope, credential: provider.credential, inner: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        return try Rig(server: server, http: http, sleep: sleeps.record)
    }

    @Test func aWholeCaptureUploadsWithTheCredentialOnEveryAPIRequestAndNoPut() async throws {
        let sleeps = Sleeps()
        let provider = Provider([Self.token])
        let rig = try Self.rig(provider: provider, sleeps: sleeps)
        defer { rig.cleanUp() }
        let images = try await rig.capture.sealImages()
        await rig.uploader.add(images)
        try #require(await CaptureUploaderTests.settles(rig.uploader))
        try await rig.finishAndSeal()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        let done = await rig.uploader.snapshot
        #expect(done.end == .finished(status: "manual_review"))
        #expect(done.result != nil)
        let log = rig.server.state.withLock { $0.log }
        let puts = log.filter { LoopbackCaptureAPI.route($0) == "PUT upload" }
        let api = log.filter { LoopbackCaptureAPI.route($0) != "PUT upload" }
        #expect(puts.count >= images.count + 4)
        #expect(!api.isEmpty)
        #expect(api.allSatisfy { $0.headers["authorization"] == "Bearer \(Self.token)" })
        #expect(puts.allSatisfy { $0.headers["authorization"] == nil })
        // At least one fetch per API request the uploader made. Not equality: URLSession may
        // resend a request on a reused connection, which the server logs twice.
        #expect(provider.calls.withLock { $0 } > 0)
    }

    /// Each refusal ends the upload at its step with the boundary's code and status 0, sends the
    /// server nothing, and sleeps for no retry.
    @Test func aDestinationOutsideTheScopeEndsTheUploadWithoutARetry() async throws {
        let sleeps = Sleeps()
        let provider = Provider([Self.token])
        let rig = try Self.rig(provider: provider, scopeBase: { URL(string: "http://127.0.0.1:\(Int($0.port) + 1)/v1")! }, sleeps: sleeps)
        defer { rig.cleanUp() }
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "create", codes: ["auth_destination_refused"], status: 0))
        #expect(rig.server.state.withLock { $0.log.isEmpty })
        #expect(provider.calls.withLock { $0 } == 0)
        #expect(sleeps.isEmpty)
    }

    @Test(arguments: [("", "auth_credential_malformed"), ("has space", "auth_credential_malformed")])
    func aMalformedCredentialEndsTheUploadWithoutARetry(token: String, code: String) async throws {
        let sleeps = Sleeps()
        let provider = Provider([token])
        let rig = try Self.rig(provider: provider, sleeps: sleeps)
        defer { rig.cleanUp() }
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "create", codes: [code], status: 0))
        #expect(rig.server.state.withLock { $0.log.isEmpty })
        #expect(provider.calls.withLock { $0 } == 1)
        #expect(sleeps.isEmpty)
    }

    /// A provider that can't supply a credential ends the upload; nothing is sent without one.
    /// Retrying a credential refresh belongs in the provider, by its own policy.
    @Test func anUnavailableCredentialEndsTheUploadWithoutARetry() async throws {
        struct Offline: Error {}
        let sleeps = Sleeps()
        let calls = Mutex(0)
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.expectedBearer = Self.token }
        let http = ScopedCaptureHTTP(
            scope: try CaptureAPIScope(base: server.base, transport: .loopbackHTTP),
            credential: {
                calls.withLock { $0 += 1 }
                throw Offline()
            },
            inner: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        let rig = try Rig(server: server, http: http, sleep: sleeps.record)
        defer { rig.cleanUp() }
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "create", codes: ["auth_credential_unavailable"], status: 0))
        #expect(server.state.withLock { $0.log.isEmpty })
        #expect(calls.withLock { $0 } == 1)
        #expect(sleeps.isEmpty)
    }

    /// Signed headers that include Authorization end the upload at the PUT, with no PUT sent
    /// and no retry. The API requests before it carried the credential as usual.
    @Test func signedHeadersCarryingAuthorizationEndTheUploadAtThePut() async throws {
        let sleeps = Sleeps()
        let rig = try Self.rig(provider: Provider([Self.token]), sleeps: sleeps) {
            $0.extraSignedHeaders = ["Authorization": "Bearer storage-credential"]
        }
        defer { rig.cleanUp() }
        await rig.uploader.add(try await rig.capture.sealImages(count: 2))
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .failed(step: "put", codes: ["auth_storage_authorization"], status: 0))
        #expect(rig.server.requests("PUT upload").isEmpty)
        #expect(rig.server.requests("POST captures").first?.headers["authorization"] == "Bearer \(Self.token)")
        #expect(sleeps.isEmpty)
    }

    /// A withdrawal that lands while the credential is being fetched outranks the refusal that
    /// follows: the upload stops as withdrawn, not as a credential failure.
    @Test func aWithdrawalOutranksACredentialRefusal() async throws {
        struct Offline: Error {}
        let sleeps = Sleeps()
        let uploader = Mutex<CaptureUploader?>(nil)
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.expectedBearer = Self.token }
        let http = ScopedCaptureHTTP(
            scope: try CaptureAPIScope(base: server.base, transport: .loopbackHTTP),
            credential: {
                _ = uploader.withLock { $0 }?.withdrawConsent()
                throw Offline()
            },
            inner: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        let rig = try Rig(server: server, http: http, sleep: sleeps.record)
        defer { rig.cleanUp() }
        uploader.withLock { $0 = rig.uploader }
        await rig.uploader.kick()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        let end = await rig.uploader.snapshot.end
        #expect(end != .failed(step: "create", codes: ["auth_credential_unavailable"], status: 0))
        #expect(server.state.withLock { $0.log.isEmpty })
        #expect(sleeps.isEmpty)
    }

    /// A withdrawal that lands while a provider is waiting, one that ignores cancellation and
    /// then returns a good token, ends the upload as withdrawn: nothing is sent, no retry is
    /// scheduled, and the end is saved. The wrapper throws CancellationError there, which the
    /// uploader must not read as a network failure to retry.
    @Test func aWithdrawalWhileTheProviderWaitsEndsTheUploadAsWithdrawn() async throws {
        let sleeps = Sleeps()
        let gate = ScopedCaptureHTTPTests.Gate()
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.expectedBearer = Self.token }
        let http = ScopedCaptureHTTP(
            scope: try CaptureAPIScope(base: server.base, transport: .loopbackHTTP),
            credential: {
                await gate.wait()
                return Self.token
            },
            inner: URLSessionCaptureHTTP.ephemeral(timeout: 10))
        let rig = try Rig(server: server, http: http, sleep: sleeps.record)
        defer { rig.cleanUp() }
        await rig.uploader.kick()
        await gate.entered()
        #expect(rig.uploader.withdrawConsent() == .marked)
        gate.release()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        let snapshot = await rig.uploader.snapshot
        #expect(snapshot.end == .abandoned(CaptureUploader.withdrawnReason))
        #expect(await rig.uploader.status.retryingAt == nil)
        #expect(server.state.withLock { $0.log.isEmpty })
        #expect(sleeps.isEmpty)
        let saved = try CaptureUploadState.load(from: CaptureUploader.stateURL(in: rig.capture.folder))
        #expect(saved.end == .abandoned(CaptureUploader.withdrawnReason))
    }

    /// With the default loopback (no expected bearer), the old assertion stands: an API request
    /// that carries Authorization is refused.
    @Test func theDefaultLoopbackStillRefusesAnyAPIAuthorization() async throws {
        let rig = try Rig()
        defer { rig.cleanUp() }
        var request = URLRequest(url: rig.server.base.appending(path: "captures"))
        request.httpMethod = "POST"
        request.setValue("Bearer x", forHTTPHeaderField: "Authorization")
        request.httpBody = Data("{}".utf8)
        let reply = try await URLSessionCaptureHTTP.ephemeral(timeout: 10).send(request)
        #expect(reply.status == 400)
        #expect(CaptureAPI.errorCodes(reply.body) == ["unexpected_authorization"])
    }
}
