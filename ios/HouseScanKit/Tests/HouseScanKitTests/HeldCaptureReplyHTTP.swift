import Foundation
import HouseScanKit
import Synchronization
import Testing

/// Holds an already received reply, so test-controlled waiting does not spend a network timeout.
/// Cancellation leaves the reply waiting, as a background transfer can do after a world ends.
final class HeldCaptureReplyHTTP: CaptureHTTP {
    enum HoldError: Error, Equatable {
        case secondReplyWhileWaiting(path: String)
    }

    private enum HoldDecision {
        case parked, released, occupied
    }

    private struct State {
        var released = false
        var waiting: CheckedContinuation<Void, any Error>?
    }

    private let inner: any CaptureHTTP
    private let pathSuffix: String
    private let state = Mutex(State())

    init(_ inner: any CaptureHTTP, pathSuffix: String) {
        self.inner = inner
        self.pathSuffix = pathSuffix
    }

    var parked: Bool { state.withLock { $0.waiting != nil } }

    func release() {
        let waiting = state.withLock { s in
            s.released = true
            let waiting = s.waiting
            s.waiting = nil
            return waiting
        }
        waiting?.resume()
    }

    func send(_ request: URLRequest) async throws -> HTTPReply {
        let reply = try await inner.send(request)
        if request.url?.path.hasSuffix(pathSuffix) == true {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                let decision = state.withLock { s -> HoldDecision in
                    if s.released { return .released }
                    guard s.waiting == nil else { return .occupied }
                    s.waiting = continuation
                    return .parked
                }
                switch decision {
                case .parked: break
                case .released: continuation.resume()
                case .occupied:
                    Issue.record("HeldCaptureReplyHTTP received a second reply for \(pathSuffix) while the first is waiting")
                    continuation.resume(throwing: HoldError.secondReplyWhileWaiting(path: pathSuffix))
                }
            }
        }
        return reply
    }

    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        try await inner.upload(request, file: file)
    }
}

@Suite struct HeldCaptureReplyHTTPTests {
    private struct ImmediateReplyHTTP: CaptureHTTP {
        func send(_ request: URLRequest) async throws -> HTTPReply {
            HTTPReply(status: 200, body: Data())
        }

        func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
            try await send(request)
        }
    }

    @Test func aSecondHeldReplyFailsWithoutLosingTheFirst() async throws {
        let http = HeldCaptureReplyHTTP(ImmediateReplyHTTP(), pathSuffix: "/result")
        defer { http.release() }
        let request = URLRequest(url: try #require(URL(string: "http://127.0.0.1/result")))
        let first = Task { try await http.send(request) }
        let deadline = ContinuousClock.now + .seconds(5)
        while !http.parked, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        try #require(http.parked)

        var receivedError: (any Error)?
        await withKnownIssue("The helper reports overlapping held replies as a test failure") {
            do { _ = try await http.send(request) }
            catch { receivedError = error }
        }
        #expect(receivedError as? HeldCaptureReplyHTTP.HoldError == .secondReplyWhileWaiting(path: "/result"))
        #expect(http.parked)
        http.release()
        #expect(try await first.value == HTTPReply(status: 200, body: Data()))
        #expect(!http.parked)
    }
}
