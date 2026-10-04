import Foundation

/// Whether this build and this homeowner allow a capture upload. No endpoint means nothing is
/// offered and nothing is sent; an endpoint without the homeowner's yes sends nothing either.
public enum CaptureUploadGate {
    public enum Decision: Sendable, Equatable {
        case off(String)
        case on(URL)
    }

    /// `endpoint` is the API base including `/v1`, from the build's Info.plist. An empty build
    /// setting, an unexpanded `$(...)`, or anything but https (http only to this machine, for
    /// tests) is off.
    public static func decide(endpoint: String?, consented: Bool) -> Decision {
        let text = endpoint?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty, !text.hasPrefix("$(") else { return .off("no capture endpoint in this build") }
        guard let url = URL(string: text), let scheme = url.scheme?.lowercased(), let host = url.host(), url.user == nil, url.password == nil else {
            return .off("capture endpoint is not a URL")
        }
        let local = host == "127.0.0.1" || host == "localhost"
        guard scheme == "https" || (scheme == "http" && local) else { return .off("capture endpoint must be https") }
        guard consented else { return .off("the homeowner has not agreed to send the capture") }
        return .on(url)
    }
}

/// What the app does with its captures, from three build settings read once at launch. Only the
/// integration build records a 0.4 capture at all; only a build that also names an endpoint and
/// says device data may go there sends one, and then only after the homeowner's yes.
public enum CaptureIntegrationMode: Sendable, Equatable {
    /// The client build, or no usable endpoint: nothing is recorded for the capture API or sent.
    case off(String)
    /// The capture is recorded on the phone and never sent: the endpoint may not receive device
    /// data (an unauthenticated test server, unless the build says otherwise).
    case recordOnly(URL)
    /// Recorded, and sent after the homeowner's yes.
    case send(URL)

    public var endpoint: URL? {
        switch self {
        case .off: nil
        case .recordOnly(let url), .send(let url): url
        }
    }

    /// Where the scan's pictures come from.
    public enum Source: Sendable, Equatable {
        /// The phone's camera: sending needs the build's device-data switch.
        case device
        /// A recorded session played back (`-replay`). Its pictures can be anyone's, so it is sent
        /// only to a receiver on this machine, and only when the run asks for that
        /// (`sendToLocalReceiver`, the UI tests' switch); never to a remote endpoint.
        case replay(sendToLocalReceiver: Bool)
    }

    /// `integrationBuild` and `sendDeviceData` are Info.plist values that must read exactly "YES".
    /// A launch argument can name `endpoint`, but can't turn a client build into an integration
    /// build or allow device data.
    public static func resolve(integrationBuild: String?, endpoint: String?, sendDeviceData: String?, source: Source = .device) -> CaptureIntegrationMode {
        guard integrationBuild == "YES" else { return .off("not the integration build") }
        switch CaptureUploadGate.decide(endpoint: endpoint, consented: true) {
        case .off(let reason): return .off(reason)
        case .on(let url):
            switch source {
            case .device: return sendDeviceData == "YES" ? .send(url) : .recordOnly(url)
            case .replay(let local):
                let loopback = url.host() == "127.0.0.1" || url.host() == "localhost"
                return local && loopback ? .send(url) : .recordOnly(url)
            }
        }
    }
}
