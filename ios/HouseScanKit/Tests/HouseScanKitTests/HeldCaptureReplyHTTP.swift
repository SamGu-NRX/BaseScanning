import Foundation
import HouseScanKit
import Synchronization

/// Holds an already received reply, so test-controlled waiting does not spend a network timeout.
/// Cancellation leaves the reply waiting, as a background transfer can do after a world ends.
final class HeldCaptureReplyHTTP: CaptureHTTP {
    private struct State {
        var released = false
        var waiting: CheckedContinuation<Void, Never>?
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
            await withCheckedContinuation { continuation in
                let released = state.withLock { s in
                    if s.released { return true }
                    precondition(s.waiting == nil, "HeldCaptureReplyHTTP holds only one reply at a time")
                    s.waiting = continuation
                    return false
                }
                if released { continuation.resume() }
            }
        }
        return reply
    }

    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        try await inner.upload(request, file: file)
    }
}
