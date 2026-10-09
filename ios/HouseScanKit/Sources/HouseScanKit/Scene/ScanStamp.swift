import Foundation

/// What produced a scan, written beside it as `scan-stamp.json` so a scan pulled off a phone can
/// be matched to the app build, the server and the rules that judged it.
public struct ScanStamp: Codable, Sendable, Equatable {
    /// The app build that made the scan: its version and build numbers and the commit it was
    /// built from.
    public struct App: Codable, Sendable, Equatable {
        /// CFBundleShortVersionString and CFBundleVersion.
        public var version: String
        public var build: String
        /// The git commit the app was built from, or "unknown" for builds outside the TestFlight job.
        public var commit: String

        public init(version: String, build: String, commit: String) {
            self.version = version
            self.build = build
            self.commit = commit
        }
    }

    /// Where the scan's answers come from: the placement server, or nothing when the bundled
    /// sample answers instead.
    public struct Server: Codable, Sendable, Equatable {
        /// The placement server the scan is sent to; nil when the bundled sample answers.
        public var url: String?

        public init(url: String?) {
            self.url = url
        }
    }

    /// The server's answer, from its `schema_version`, `policy` and `stats`.
    public struct Answer: Codable, Sendable, Equatable {
        public var schemaVersion: String
        /// The rules' id and revision, when the server names them.
        public var policyID: String?
        public var policyVersion: String?
        public var rulesSHA256: String
        /// Hash of the scene.json the server judged.
        public var inputSHA256: String
        /// True for the bundled sample, not a server's answer.
        public var sample: Bool

        enum CodingKeys: String, CodingKey {
            case sample
            case schemaVersion = "schema_version"
            case policyID = "policy_id"
            case policyVersion = "policy_version"
            case rulesSHA256 = "rules_sha256"
            case inputSHA256 = "input_sha256"
        }

        public init(_ result: PlacementResult, sample: Bool) {
            schemaVersion = result.schemaVersion
            policyID = result.policy.id
            policyVersion = result.policy.version
            rulesSHA256 = result.policy.rulesSHA256
            inputSHA256 = result.stats.inputSHA256
            self.sample = sample
        }
    }

    /// The build that made the scan (`App`).
    public var app: App
    /// The server the scan's answers come from (`Server`).
    public var server: Server
    /// The latest answer for this scan; nil before one arrives.
    public var answer: Answer?
    /// True for a practice scan (`PracticeMeter`): a drawn sample stood in for the electric
    /// meter and its close-up photo, so the meter position and number describe no real meter.
    /// The packet and scene.json don't say so; this file does.
    public var practice: Bool

    public static let fileName = "scan-stamp.json"

    /// Creates the stamp written beside a scan: which build made it and where its answers come
    /// from, with the answer and the practice flag left to be filled in.
    public init(app: App, server: Server, answer: Answer? = nil, practice: Bool = false) {
        self.app = app
        self.server = server
        self.answer = answer
        self.practice = practice
    }

    /// Sorted keys, and a missing answer, URL or version written as null, so the file always has
    /// the same shape.
    public func jsonData() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(app, forKey: .app)
        try c.encode(server, forKey: .server)
        try c.encode(answer, forKey: .answer)
        try c.encode(practice, forKey: .practice)
    }

    enum CodingKeys: String, CodingKey {
        case app, server, answer, practice
    }
}

extension ScanStamp.Answer {
    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(policyID, forKey: .policyID)
        try c.encode(policyVersion, forKey: .policyVersion)
        try c.encode(rulesSHA256, forKey: .rulesSHA256)
        try c.encode(inputSHA256, forKey: .inputSHA256)
        try c.encode(sample, forKey: .sample)
    }
}

extension ScanStamp.Server {
    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(url, forKey: .url)
    }

    enum CodingKeys: String, CodingKey {
        case url
    }
}
