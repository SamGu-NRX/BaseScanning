import Foundation

/// The capture API's own requests, and nothing else, carry the credential the API issued.
///
/// `CaptureUploader` sends two kinds of request through `CaptureHTTP`: `send` for the capture
/// API (create, register, commit, finalize, events, result) and `upload` for the storage URLs
/// the API signs. This decorator adds `Authorization: Bearer <token>` to an API request only
/// when the request's URL is inside the scope fixed at construction, and asks the credential
/// provider for the token only then. A storage upload never gets the credential, and the
/// provider isn't consulted for one, even when storage shares the API's origin.
///
/// It refuses rather than falls back: a request outside the scope, a credential the provider
/// can't supply or one that isn't a well-formed token throws `ScopedCaptureHTTPError` and nothing
/// is sent. It adds no retry; the uploader's existing policy handles thrown errors. Redirects
/// stay refused by the wrapped transport (`URLSessionCaptureHTTP` refuses every one), so the
/// credential never reaches a redirect target.
///
/// This is the client's side of the boundary only. It doesn't establish what the server
/// authorizes, attest result bytes or obtain the credential.
public struct ScopedCaptureHTTP: CaptureHTTP {
    /// Supplies the API credential, refreshing it when the issuer requires. Called once per API
    /// request that passes the scope check, never for a storage upload.
    public typealias Credential = @Sendable () async throws -> String

    public let scope: CaptureAPIScope
    private let credential: Credential
    private let inner: any CaptureHTTP

    public init(scope: CaptureAPIScope, credential: @escaping Credential, inner: any CaptureHTTP) {
        self.scope = scope
        self.credential = credential
        self.inner = inner
    }

    public func send(_ request: URLRequest) async throws -> HTTPReply {
        // The destination is checked before the credential is asked for.
        guard let url = request.url else { throw ScopedCaptureHTTPError.destinationNotAllowed("no_url") }
        if let reason = scope.problem(with: url) { throw ScopedCaptureHTTPError.destinationNotAllowed(reason) }
        let token: String
        do {
            token = try await credential()
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            // Only the error's type: a provider's message may quote what it tried.
            throw ScopedCaptureHTTPError.credentialUnavailable(String(describing: type(of: error)))
        }
        guard Self.isBearerToken(token) else { throw ScopedCaptureHTTPError.credentialMalformed }
        var authorized = request
        authorized.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await inner.send(authorized)
    }

    /// A storage PUT goes out with exactly the headers the server signed. The uploader copies
    /// those in and sets nothing else, and the storage URLs it receives carry their signature in
    /// the query, so a signed set has no reason to hold `Authorization`. One that does is refused
    /// instead of sent: forwarding it could hand a credential to storage, and stripping it could
    /// break a signature in a way the server should hear about, not the bucket.
    public func upload(_ request: URLRequest, file: URL) async throws -> HTTPReply {
        if request.value(forHTTPHeaderField: "Authorization") != nil {
            throw ScopedCaptureHTTPError.storageRequestHasAuthorization
        }
        return try await inner.upload(request, file: file)
    }

    /// RFC 6750's `b64token`: letters, digits and `-._~+/`, then optional `=` padding. Anything
    /// else, a space, a quote or a line break included, could change the header or add another.
    static func isBearerToken(_ token: String) -> Bool {
        let body = token.utf8.reversed().drop { $0 == UInt8(ascii: "=") }
        guard !body.isEmpty else { return false }
        return body.allSatisfy { byte in
            switch byte {
            case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"), UInt8(ascii: "0")...UInt8(ascii: "9"): true
            case UInt8(ascii: "-"), UInt8(ascii: "."), UInt8(ascii: "_"), UInt8(ascii: "~"), UInt8(ascii: "+"), UInt8(ascii: "/"): true
            default: false
            }
        }
    }
}

/// Why `ScopedCaptureHTTP` refused a request. No case carries a credential, a URL or a header
/// value, so the description can be logged.
public enum ScopedCaptureHTTPError: Error, Equatable, CustomStringConvertible {
    /// The URL is outside the configured API scope. The reason names the check that failed.
    case destinationNotAllowed(String)
    /// The credential provider threw. The value is the error's type name only.
    case credentialUnavailable(String)
    /// The provider returned an empty token, or one with characters a bearer token can't have.
    case credentialMalformed
    /// A storage upload already carries an `Authorization` header.
    case storageRequestHasAuthorization

    /// The stable code an upload that hit this refusal ends with (`CaptureUploadState.End.failed`).
    /// It names the kind of setup or credential problem, never a value.
    public var code: String {
        switch self {
        case .destinationNotAllowed: "auth_destination_refused"
        case .credentialUnavailable: "auth_credential_unavailable"
        case .credentialMalformed: "auth_credential_malformed"
        case .storageRequestHasAuthorization: "auth_storage_authorization"
        }
    }

    public var description: String {
        switch self {
        case .destinationNotAllowed(let reason): "the request is outside the capture API's scope (\(reason))"
        case .credentialUnavailable(let type): "the capture API credential couldn't be obtained (\(type))"
        case .credentialMalformed: "the capture API credential isn't a well-formed bearer token"
        case .storageRequestHasAuthorization: "a storage upload carries an Authorization header"
        }
    }
}

/// The capture API a credential is for: one scheme, host and port, and the paths under one
/// base path. Fixed when the uploader is configured, so a later settings change can't redirect
/// an in-flight capture's credential.
public struct CaptureAPIScope: Sendable, Equatable {
    /// Plain http is allowed only by name, and only to this machine.
    public enum Transport: Sendable, Equatable {
        case httpsOnly
        /// For tests and local development: http to `127.0.0.1`, `::1` or `localhost`. Never a
        /// public host.
        case loopbackHTTP
    }

    public let scheme: String
    public let host: String
    public let port: Int
    /// The base path without a trailing slash; empty for the origin's root.
    public let basePath: String
    public let transport: Transport

    /// The scope of `base`, the URL the uploader is given. Throws when `base` itself couldn't
    /// receive a credential: not https (or loopback http under `.loopbackHTTP`), credentials in
    /// the URL, a query or fragment, or a path with dot segments or encoded separators.
    public init(base: URL, transport: Transport = .httpsOnly) throws {
        guard let parts = URLComponents(url: base, resolvingAgainstBaseURL: false),
              let scheme = parts.scheme?.lowercased(), let host = parts.host?.lowercased(), !host.isEmpty
        else { throw ScopedCaptureHTTPError.destinationNotAllowed("base_invalid") }
        guard parts.user == nil, parts.password == nil else { throw ScopedCaptureHTTPError.destinationNotAllowed("base_userinfo") }
        guard parts.query == nil, parts.fragment == nil else { throw ScopedCaptureHTTPError.destinationNotAllowed("base_query") }
        guard Self.allows(scheme: scheme, host: host, transport: transport) else {
            throw ScopedCaptureHTTPError.destinationNotAllowed("base_insecure")
        }
        let path = parts.percentEncodedPath
        guard Self.isPlainPath(path) else { throw ScopedCaptureHTTPError.destinationNotAllowed("base_path") }
        self.scheme = scheme
        self.host = host
        self.port = parts.port ?? (scheme == "https" ? 443 : 80)
        basePath = path.hasSuffix("/") ? String(path.dropLast()) : path
        self.transport = transport
    }

    /// Nil when `url` may receive the credential, else the check it fails.
    public func problem(with url: URL) -> String? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let scheme = parts.scheme?.lowercased(), let host = parts.host?.lowercased()
        else { return "url_invalid" }
        if parts.user != nil || parts.password != nil { return "userinfo" }
        if scheme != self.scheme { return "scheme" }
        // Exact host: a suffix, a prefix or a trailing dot is another name.
        if host != self.host { return "host" }
        if (parts.port ?? (scheme == "https" ? 443 : 80)) != port { return "port" }
        let path = parts.percentEncodedPath
        if !Self.isPlainPath(path) { return "path_segments" }
        guard path == basePath || path.hasPrefix(basePath + "/") || (basePath.isEmpty && path.isEmpty) else { return "path" }
        return nil
    }

    static func allows(scheme: String, host: String, transport: Transport) -> Bool {
        if scheme == "https" { return true }
        guard scheme == "http", transport == .loopbackHTTP else { return false }
        return ["127.0.0.1", "::1", "[::1]", "localhost"].contains(host)
    }

    /// No `.` or `..` segment, and no percent-encoded dot, slash or backslash that a server
    /// might decode into one.
    static func isPlainPath(_ path: String) -> Bool {
        let lowered = path.lowercased()
        if lowered.contains("%2e") || lowered.contains("%2f") || lowered.contains("%5c") || path.contains("\\") { return false }
        return !path.split(separator: "/", omittingEmptySubsequences: false).contains { $0 == "." || $0 == ".." }
    }
}
