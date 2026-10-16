import Foundation

/// A capture API a developer runs on this machine, for the DEBUG app's `-photoProcessingAPI`
/// and `-photoProcessingAPIToken`: photo processing sends to it over `URLSessionCaptureHTTP`,
/// wrapped in `ScopedCaptureHTTP` with the static test credential the developer started the
/// server with. It is the transport a deployed service would get, pointed at a local address.
///
/// Only loopback addresses (127.0.0.1, ::1, localhost) are accepted, the hosts
/// `CaptureAPIScope` lets plain http reach, so this never sends to another machine. A phone can't
/// reach a Mac's loopback, so in practice it is the simulator. Like the capture fixture, it runs
/// only on a replay: the replay's own packet fails the phone's checks (a replay has no motion),
/// so a run that should reach the server sends `SyntheticCapture` instead.
///
/// The token never appears in a description or log: the struct keeps only the credential
/// closure `ScopedCaptureHTTP` calls.
public struct LocalCaptureAPI: Sendable {
    /// The API base, such as `http://127.0.0.1:8000/v1`.
    public let endpoint: URL
    public let scope: CaptureAPIScope
    private let credential: ScopedCaptureHTTP.Credential

    /// The transport photo processing sends with. The default inner transport is the app's.
    public func http(inner: any CaptureHTTP = URLSessionCaptureHTTP.ephemeral()) -> ScopedCaptureHTTP {
        ScopedCaptureHTTP(scope: scope, credential: credential, inner: inner)
    }

    public enum Launch: Sendable {
        /// No address was given, so the capture fixture or "not set up" decides.
        case notRequested
        /// An address was given and this run won't send to it, for the reason given. The app
        /// shows photo processing as not set up; it never falls back to the fixture.
        case refused(String)
        case on(LocalCaptureAPI)
    }

    /// The app passes `debugBuild` from `#if DEBUG`, the compile-time half of the guard, as for
    /// `CaptureFixtureLaunch`; this decides the rest and is what the package tests check.
    public static func resolve(debugBuild: Bool, onReplay: Bool, address: String?, token: String?) -> Launch {
        guard let address else { return .notRequested }
        guard debugBuild else { return .refused("a release build sends to no developer capture API") }
        guard onReplay else { return .refused("the developer capture API runs only on a replay, never the live camera") }
        guard let url = URL(string: address), let host = url.host()?.lowercased(),
              CaptureAPIScope.allows(scheme: "http", host: host, transport: .loopbackHTTP)
        else { return .refused("the developer capture API must be on this machine: 127.0.0.1, ::1 or localhost") }
        let scope: CaptureAPIScope
        do {
            scope = try CaptureAPIScope(base: url, transport: .loopbackHTTP)
        } catch {
            return .refused("the developer capture API address can't receive a credential: \(error)")
        }
        guard let token, !token.isEmpty else {
            return .refused("the developer capture API needs its test credential (-photoProcessingAPIToken)")
        }
        return .on(LocalCaptureAPI(endpoint: url, scope: scope, credential: { token }))
    }
}
