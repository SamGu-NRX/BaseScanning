import Foundation

/// The remote service that decides where a scan's battery could go. Both run off the phone:
/// Legacy sends scene.json to the placement checker, and photo processing sends the scan's photos
/// and camera path to the capture API. Neither decides on the phone.
public enum ProcessingBackend: String, Sendable, Equatable, Hashable, CaseIterable, Codable {
    case legacy
    case photoProcessing

    /// What a scan uses when nobody chose. Legacy stays the default until photo processing passes
    /// a real end-to-end acceptance; until then the beta offers it as a choice.
    public static let operationalDefault: ProcessingBackend = .legacy
}

/// The developer setting that picks the next scan's backend, in UserDefaults. A scan reads it once,
/// when it starts (`ScanContext`), so a change applies to the next scan.
public enum ProcessingBackendPreference {
    public static let key = "processingBackend"

    /// The stored choice. Nothing stored, or a value this build doesn't know, reads as the
    /// operational default rather than guessing.
    public static func read(_ defaults: UserDefaults) -> ProcessingBackend {
        defaults.string(forKey: key).flatMap(ProcessingBackend.init(rawValue:)) ?? .operationalDefault
    }

    public static func write(_ backend: ProcessingBackend, to defaults: UserDefaults) {
        defaults.set(backend.rawValue, forKey: key)
    }
}

/// The backend a scan uses, as it stood when the scan started: which one, who answers, where the
/// scan's data may go, and whose credential it may present. The scan keeps it to its end, so a
/// later change to the setting or the endpoint can't send an existing capture somewhere else.
public struct ProcessingProfile: Sendable, Equatable, Hashable {
    public enum Answers: Sendable, Equatable, Hashable {
        /// A service off the phone.
        case service
        /// A stand-in on this phone: the bundled sample result, or the DEBUG capture fixture.
        /// Nothing leaves the phone.
        case standIn
        /// Nobody: this build has no usable setup for the backend, for the reason given. Nothing
        /// is sent.
        case notSetUp(String)
    }

    public var backend: ProcessingBackend
    public var answers: Answers
    /// The API base this scan's data may go to. Nil when it goes nowhere.
    public var origin: URL?
    /// Who issues the scoped credential this scan may present, never the credential itself. Nil
    /// when the scan presents none. A token refreshed from the same issuer leaves the profile as
    /// it is.
    public var credentialIssuer: String?

    public init(backend: ProcessingBackend, answers: Answers, origin: URL?, credentialIssuer: String? = nil) {
        self.backend = backend
        self.answers = answers
        self.origin = origin
        self.credentialIssuer = credentialIssuer
    }
}
