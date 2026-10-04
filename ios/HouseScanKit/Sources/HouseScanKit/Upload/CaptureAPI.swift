import Foundation

/// The capture API's request and response bodies that House Scan sends and reads (`/v1/captures`
/// and below), written from the documented wire contract. Unknown response fields are ignored.
public enum CaptureAPI {
    public struct Device: Codable, Sendable, Equatable {
        public var model: String
        public var systemVersion: String
        public var appVersion: String

        public init(model: String, systemVersion: String, appVersion: String) {
            self.model = model
            self.systemVersion = systemVersion
            self.appVersion = appVersion
        }
    }

    /// The create body. Expected counts are left out: they are optional, and a count that grows as
    /// photos arrive would change the body the idempotency key is bound to.
    public struct CreateRequest: Codable, Sendable, Equatable {
        public var packetId: String
        public var formatVersion = Packet04.formatVersion
        public var tier: Packet04.Tier
        public var flow = "guided"
        public var device: Device

        public init(packetId: String, tier: Packet04.Tier, device: Device) {
            self.packetId = packetId
            self.tier = tier
            self.device = device
        }
    }

    public struct Limits: Codable, Sendable, Equatable {
        public var maxSinglePutBytes: Int?
        public var urlTtlS: Int?
        public var maxFiles: Int?
        public var maxBatch: Int?
    }

    public struct CreateResponse: Codable, Sendable, Equatable {
        public var captureId: String
        public var status: String
        public var upload: Limits?
        public var finalizeBy: String?
    }

    public struct RegisterFile: Codable, Sendable, Equatable {
        public var path: String
        public var bytes: Int
        public var sha256: String
        public var md5: String
        public var role: Packet04.Role
        public var contentType: String
        public var meta: Packet04.ImageRecord?

        public init(_ file: SealedFile) {
            path = file.path
            bytes = file.bytes
            sha256 = file.sha256
            md5 = file.md5
            role = file.role
            contentType = file.contentType
            meta = file.meta
        }
    }

    public struct RegisterRequest: Codable, Sendable, Equatable {
        public var files: [RegisterFile]
    }

    public struct UploadTarget: Codable, Sendable, Equatable {
        public var method: String
        public var url: String?
        public var headers: [String: String]?
        public var expiresAt: String?
    }

    public struct RegisteredFile: Codable, Sendable, Equatable {
        public var path: String
        public var state: String
        public var upload: UploadTarget?
    }

    public struct RegisterResponse: Codable, Sendable, Equatable {
        public var files: [RegisteredFile]
    }

    public struct CommitFile: Codable, Sendable, Equatable {
        public var path: String
        public var sha256: String
    }

    public struct CommitRequest: Codable, Sendable, Equatable {
        public var files: [CommitFile]
    }

    public struct CommitResponse: Codable, Sendable, Equatable {
        public var committed: [String]
        public var notFound: [String]
        public var mismatch: [String]
    }

    public struct FinalizeResponse: Codable, Sendable, Equatable {
        public var status: String
        public var missing: [String]
        public var runId: String
        public var etaS: Int?
    }

    public struct Event: Codable, Sendable, Equatable {
        public var seq: Int
        public var type: String
        public var at: String
        /// The fields the app reads. Others, which may carry text meant for the homeowner, are
        /// not kept.
        public var data: EventData?
    }

    public struct EventData: Codable, Sendable, Equatable {
        public var code: String?
        public var kind: String?
        public var stage: String?
        public var status: String?
        public var runId: String?
        public var next: String?
    }

    public struct EventsResponse: Codable, Sendable, Equatable {
        public var status: String
        public var next: Int
        public var events: [Event]
    }

    public struct ErrorItem: Codable, Sendable, Equatable {
        public var code: String
        public var pointer: String?
        public var message: String?
    }

    public struct ErrorBody: Codable, Sendable, Equatable {
        public var errors: [ErrorItem]
    }

    public static func errorCodes(_ body: Data) -> [String] {
        (try? JSONDecoder().decode(ErrorBody.self, from: body))?.errors.map(\.code) ?? []
    }

    /// Statuses after which the capture changes no more.
    public static let terminalStatuses: Set<String> = ["complete", "manual_review", "failed", "expired", "needs_views"]

    /// Terminal statuses that can end a capture with no answer, so a result read carrying one and
    /// a null outcome is final. The others come with the run's outcome.
    public static let statusesWithoutOutcome: Set<String> = ["failed", "expired"]
}

/// An HTTP answer: status, body and the headers the uploader reads.
public struct HTTPReply: Sendable, Equatable {
    public var status: Int
    public var body: Data
    public var retryAfter: Double?

    public init(status: Int, body: Data, retryAfter: Double? = nil) {
        self.status = status
        self.body = body
        self.retryAfter = retryAfter
    }
}

/// How the uploader reaches the network. HTTP error statuses come back as replies; only transport
/// failures throw. An implementation must not follow redirects: the uploader checks each URL
/// before it sends a photo there, and a redirect would send it somewhere unchecked. A redirect
/// comes back as its own 3xx reply.
public protocol CaptureHTTP: Sendable {
    func send(_ request: URLRequest) async throws -> HTTPReply
    /// `file` as the whole body of `request`, streamed from disk.
    func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply
}

/// `CaptureHTTP` on one ordinary `URLSession`.
public struct URLSessionCaptureHTTP: CaptureHTTP {
    public let session: URLSession

    public init(session: URLSession) {
        self.session = session
    }

    /// An ephemeral session: nothing cached or stored in cookies, and no credentials offered to
    /// the signed storage URLs.
    public static func ephemeral(timeout: Double = 60) -> URLSessionCaptureHTTP {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.httpMaximumConnectionsPerHost = 4
        return URLSessionCaptureHTTP(session: URLSession(configuration: configuration))
    }

    public func send(_ request: URLRequest) async throws -> HTTPReply {
        let (data, response) = try await session.data(for: request, delegate: NoRedirects.shared)
        return Self.reply(data, response)
    }

    public func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        let (data, response) = try await session.upload(for: request, fromFile: file, delegate: NoRedirects.shared)
        return Self.reply(data, response)
    }

    /// Refuses every redirect, so the task returns the 3xx response itself. URLSession follows
    /// redirects by default, and App Transport Security alone would still allow one to another
    /// https host, or to plain http on the local network, which the app's Info.plist permits.
    final class NoRedirects: NSObject, URLSessionTaskDelegate, Sendable {
        static let shared = NoRedirects()

        func urlSession(
            _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest
        ) async -> URLRequest? {
            nil
        }
    }

    static func reply(_ data: Data, _ response: URLResponse) -> HTTPReply {
        let http = response as? HTTPURLResponse
        return HTTPReply(
            status: http?.statusCode ?? 0, body: data, retryAfter: http?.value(forHTTPHeaderField: "Retry-After").flatMap(Double.init))
    }
}
