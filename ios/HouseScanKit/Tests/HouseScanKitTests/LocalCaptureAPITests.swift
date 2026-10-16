import Foundation
import HouseScanKit
import Testing

/// The DEBUG app's developer capture API (`-photoProcessingAPI`, `-photoProcessingAPIToken`):
/// which launches get a real HTTP transport, and which are refused.
@Suite struct LocalCaptureAPITests {
    static let token = "local-capture-api-test-token"

    static func refusal(_ launch: LocalCaptureAPI.Launch) -> String? {
        if case .refused(let reason) = launch { return reason }
        return nil
    }

    /// A debug replay with a loopback address and a credential gets the transport that a whole
    /// capture goes through over real HTTP: the credential on every API request, none on a PUT.
    @Test func aDebugReplayWithALoopbackAddressSendsOverRealHTTPWithTheCredential() async throws {
        let server = try LoopbackCaptureAPI()
        server.state.withLock { $0.expectedBearer = Self.token }
        let launch = LocalCaptureAPI.resolve(debugBuild: true, onReplay: true, address: server.base.absoluteString, token: Self.token)
        guard case .on(let api) = launch else {
            Issue.record("expected the loopback address to be accepted, got \(launch)")
            return
        }
        #expect(api.endpoint == server.base)
        #expect(api.scope.host == "127.0.0.1" && api.scope.transport == .loopbackHTTP)

        let rig = try CaptureUploaderTests.Rig(server: server, http: api.http(inner: URLSessionCaptureHTTP.ephemeral(timeout: 10)))
        defer { rig.cleanUp() }
        await rig.uploader.add(try await rig.capture.sealImages())
        try await rig.finishAndSeal()
        try #require(await CaptureUploaderTests.settles(rig.uploader))

        #expect(await rig.uploader.snapshot.end == .finished(status: "manual_review"))
        let log = server.state.withLock { $0.log }
        let puts = log.filter { LoopbackCaptureAPI.route($0) == "PUT upload" }
        let calls = log.filter { LoopbackCaptureAPI.route($0) != "PUT upload" }
        #expect(!puts.isEmpty && !calls.isEmpty)
        #expect(calls.allSatisfy { $0.headers["authorization"] == "Bearer \(Self.token)" })
        #expect(puts.allSatisfy { $0.headers["authorization"] == nil })
    }

    @Test func aReleaseBuildIsNotSetUpWhateverItIsGiven() {
        for onReplay in [true, false] {
            let launch = LocalCaptureAPI.resolve(debugBuild: false, onReplay: onReplay, address: "http://127.0.0.1:8000/v1", token: Self.token)
            #expect(Self.refusal(launch) == "a release build sends to no developer capture API")
        }
    }

    @Test(arguments: [nil, ""] as [String?])
    func aMissingCredentialIsRefused(token: String?) {
        let launch = LocalCaptureAPI.resolve(debugBuild: true, onReplay: true, address: "http://127.0.0.1:8000/v1", token: token)
        #expect(Self.refusal(launch) == "the developer capture API needs its test credential (-photoProcessingAPIToken)")
    }

    @Test(arguments: ["http://192.168.1.20:8000/v1", "https://capture.example.com/v1", "http://127.0.0.1.example.com/v1", "not a url"])
    func anAddressOffThisMachineIsRefused(address: String) {
        let launch = LocalCaptureAPI.resolve(debugBuild: true, onReplay: true, address: address, token: Self.token)
        #expect(Self.refusal(launch) == "the developer capture API must be on this machine: 127.0.0.1, ::1 or localhost")
    }

    @Test func aLoopbackAddressTheScopeRejectsIsRefused() {
        let launch = LocalCaptureAPI.resolve(debugBuild: true, onReplay: true, address: "http://127.0.0.1:8000/v1?x=1", token: Self.token)
        #expect(Self.refusal(launch)?.hasPrefix("the developer capture API address can't receive a credential") == true)
    }

    @Test func noAddressLeavesItToTheFixtureAndTheLiveCameraIsRefused() {
        guard case .notRequested = LocalCaptureAPI.resolve(debugBuild: true, onReplay: true, address: nil, token: Self.token) else {
            Issue.record("no address should leave photo processing to the fixture or not set up")
            return
        }
        let live = LocalCaptureAPI.resolve(debugBuild: true, onReplay: false, address: "http://127.0.0.1:8000/v1", token: Self.token)
        #expect(Self.refusal(live) == "the developer capture API runs only on a replay, never the live camera")
    }
}
