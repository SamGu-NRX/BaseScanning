import Foundation

// The placement server's answer (contract C2, server/schemas/result.schema.json). A missing
// required key, a wrong type, an unknown enum value, a wrong array length or a schema_version
// other than "1.0" throws PlacementDecodingError naming the JSON path. Keys marked
// required-but-nullable in the schema must be present.
//
// Unknown keys are ignored. The server adds optional fields within schema 1.0 without changing
// schema_version: `checks[].review_threshold_ft` and `sweep[].segment` arrived that way on
// 2026-09-26, and rejecting them stopped every real upload from reaching the result screen
// (`review_threshold_ft` is now read, and stays optional). An
// unknown value of an enum the app presents (decision, outcome, unsure_cause, and the others
// below) still fails, because the app can't show a decision or outcome it doesn't know.
// `reasons[].code`, `policy.sources` and `route.crossings[].effect` are plain strings: the app
// never reads them, so a value the server adds to them must not break every decode.
// `missing_evidence[].kind` and `.band` decode an unknown value as `.unknown`: one request the
// app can't act on goes to installer review instead of hiding the whole answer. The keys and
// their types stay required.

public enum PlacementDecodingError: Error, Equatable, CustomStringConvertible {
    case unsupportedSchemaVersion(String)
    case unknownEnumValue(path: String, value: String)
    case wrongArrayLength(path: String, expected: Int, actual: Int)
    case missingKey(path: String)
    case malformed(path: String, detail: String)

    public var description: String {
        switch self {
        case .unsupportedSchemaVersion(let v): "result schema_version \"\(v)\" is not supported; expected \"1.0\""
        case .unknownEnumValue(let path, let value): "unknown value \"\(value)\" at \(path)"
        case .wrongArrayLength(let path, let e, let a): "\(path) has \(a) items, expected \(e)"
        case .missingKey(let path): "missing required key \(path)"
        case .malformed(let path, let detail): "malformed result at \(path): \(detail)"
        }
    }
}

public enum PlacementDecision: String, Codable, Sendable, Equatable {
    case pass
    case manualReview = "manual_review"
    case reject
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

public enum PlacementOutcome: String, Codable, Sendable, Equatable {
    case pass
    case fail
    case unsure
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

public enum PlacementUnsureCause: String, Codable, Sendable, Equatable {
    case margin
    case unobserved
    case unknownAttribute = "unknown_attribute"
    case ruleRequiresReview = "rule_requires_review"
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

public enum PlacementComparison: String, Codable, Sendable, Equatable {
    case atLeast = "at_least"
    case atMost = "at_most"
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// A missing-evidence item's kind. A kind the server adds after this app is `.unknown`, with no
/// capture request (`GapPlanner.plan(for:leftEnd:rightEnd:)`), so the result shows it for
/// installer review.
public enum PlacementEvidenceKind: Codable, Sendable, Equatable {
    case band
    case pastEnd
    case unknown(String)

    public init(rawValue: String) {
        self = switch rawValue {
        case "band": .band
        case "past_end": .pastEnd
        default: .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .band: "band"
        case .pastEnd: "past_end"
        case .unknown(let raw): raw
        }
    }

    public init(from decoder: any Decoder) throws {
        self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

/// A missing-evidence item's band. A band the server adds after this app is `.unknown`, with no
/// capture request, as an unknown kind is.
public enum PlacementBand: Codable, Sendable, Equatable {
    case wall
    case ground
    case overhead
    case facing
    case unknown(String)

    public init(rawValue: String) {
        self = switch rawValue {
        case "wall": .wall
        case "ground": .ground
        case "overhead": .overhead
        case "facing": .facing
        default: .unknown(rawValue)
        }
    }

    public var rawValue: String {
        switch self {
        case .wall: "wall"
        case .ground: "ground"
        case .overhead: "overhead"
        case .facing: "facing"
        case .unknown(let raw): raw
        }
    }

    public init(from decoder: any Decoder) throws {
        self.init(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

public enum PlacementSide: String, Codable, Sendable, Equatable {
    case left
    case right
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

public enum PlacementEndKind: String, Codable, Sendable, Equatable {
    case limit
    case unexplored
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

public struct PlacementReason: Codable, Sendable, Equatable {
    /// A server enum the app doesn't read, kept as the raw string (see the top of this file).
    public var code: String
    public var message: String
    public var checks: [String]?

    private enum CodingKeys: String, CodingKey, CaseIterable { case code, message, checks }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decode(String.self, forKey: .code)
        message = try c.decode(String.self, forKey: .message)
        checks = try c.decodeIfPresent([String].self, forKey: .checks)
    }
}

public struct PlacementPolicy: Codable, Sendable, Equatable {
    public var id: String?
    public var version: String?
    public var autoApprove: Bool
    /// A server enum the app doesn't read, kept as raw strings (see the top of this file).
    public var sources: [String]
    public var rulesSHA256: String
    /// Whose rules decided, to show with the answer ("Demo rules: ... not Base's."). The solver
    /// also appends it to `summary`. Optional in the schema and absent from older answers.
    public var notice: String?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case id, version, sources, notice
        case autoApprove = "auto_approve"
        case rulesSHA256 = "rules_sha256"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String?.self, forKey: .id)
        version = try c.decode(String?.self, forKey: .version)
        autoApprove = try c.decode(Bool.self, forKey: .autoApprove)
        sources = try c.decode([String].self, forKey: .sources)
        rulesSHA256 = try c.decode(String.self, forKey: .rulesSHA256)
        notice = try c.decodeIfPresent(String.self, forKey: .notice)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(version, forKey: .version)
        try c.encode(autoApprove, forKey: .autoApprove)
        try c.encode(sources, forKey: .sources)
        try c.encode(rulesSHA256, forKey: .rulesSHA256)
        try c.encodeIfPresent(notice, forKey: .notice)
    }
}

public struct PlacementSpot: Codable, Sendable, Equatable {
    public var outcome: PlacementOutcome
    public var wallID: String
    public var segment: Int
    /// Battery's stretch of wall in s feet, left edge first.
    public var spanFt: SIMD2<Double>
    public var widthFt: Double
    public var depthFt: Double
    public var heightFt: Double
    /// Plan corners [x, z] feet: back-left, back-right, front-right, front-left.
    public var footprint: [SIMD2<Double>]
    public var center: SIMD2<Double>
    public var along: SIMD2<Double>
    public var outward: SIMD2<Double>
    /// Footprint centre minus the meter's plan position, [dx, dz] feet.
    public var meterOffsetFt: SIMD2<Double>
    public var routeLengthFt: Double?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case outcome, segment, footprint, center, along, outward
        case wallID = "wall_id"
        case spanFt = "span_ft"
        case widthFt = "width_ft"
        case depthFt = "depth_ft"
        case heightFt = "height_ft"
        case meterOffsetFt = "meter_offset_ft"
        case routeLengthFt = "route_length_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        outcome = try c.decode(PlacementOutcome.self, forKey: .outcome)
        wallID = try c.decode(String.self, forKey: .wallID)
        segment = try c.decode(Int.self, forKey: .segment)
        spanFt = try c.placementPair(.spanFt)
        widthFt = try c.decode(Double.self, forKey: .widthFt)
        depthFt = try c.decode(Double.self, forKey: .depthFt)
        heightFt = try c.decode(Double.self, forKey: .heightFt)
        footprint = try c.placementPairs(.footprint, count: 4)
        center = try c.placementPair(.center)
        along = try c.placementPair(.along)
        outward = try c.placementPair(.outward)
        meterOffsetFt = try c.placementPair(.meterOffsetFt)
        routeLengthFt = try c.decode(Double?.self, forKey: .routeLengthFt)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(outcome, forKey: .outcome)
        try c.encode(wallID, forKey: .wallID)
        try c.encode(segment, forKey: .segment)
        try c.placementEncode(spanFt, forKey: .spanFt)
        try c.encode(widthFt, forKey: .widthFt)
        try c.encode(depthFt, forKey: .depthFt)
        try c.encode(heightFt, forKey: .heightFt)
        try c.placementEncode(footprint, forKey: .footprint)
        try c.placementEncode(center, forKey: .center)
        try c.placementEncode(along, forKey: .along)
        try c.placementEncode(outward, forKey: .outward)
        try c.placementEncode(meterOffsetFt, forKey: .meterOffsetFt)
        try c.encode(routeLengthFt, forKey: .routeLengthFt)
    }
}

public struct PlacementDetour: Codable, Sendable, Equatable {
    public var subject: String
    public var extraFt: Double

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case subject
        case extraFt = "extra_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        subject = try c.decode(String.self, forKey: .subject)
        extraFt = try c.decode(Double.self, forKey: .extraFt)
    }
}

public struct PlacementCrossing: Codable, Sendable, Equatable {
    public var subject: String
    public var spanFt: SIMD2<Double>
    /// A server enum the app doesn't read, kept as the raw string (see the top of this file).
    public var effect: String

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case subject, effect
        case spanFt = "span_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        subject = try c.decode(String.self, forKey: .subject)
        spanFt = try c.placementPair(.spanFt)
        effect = try c.decode(String.self, forKey: .effect)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(subject, forKey: .subject)
        try c.placementEncode(spanFt, forKey: .spanFt)
        try c.encode(effect, forKey: .effect)
    }
}

public struct PlacementRoute: Codable, Sendable, Equatable {
    public var outcome: PlacementOutcome
    public var lengthFt: Double
    public var plusMinusFt: Double
    public var heightFt: Double
    /// Plan points [x, z] feet from the meter along the wall to the battery. Map them onto the wall
    /// with `SceneWall.wallCoordinates(ofPlanPointFeet:)`.
    public var polyline: [SIMD2<Double>]
    public var detours: [PlacementDetour]
    public var crossings: [PlacementCrossing]

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case outcome, polyline, detours, crossings
        case lengthFt = "length_ft"
        case plusMinusFt = "plus_minus_ft"
        case heightFt = "height_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        outcome = try c.decode(PlacementOutcome.self, forKey: .outcome)
        lengthFt = try c.decode(Double.self, forKey: .lengthFt)
        plusMinusFt = try c.decode(Double.self, forKey: .plusMinusFt)
        heightFt = try c.decode(Double.self, forKey: .heightFt)
        polyline = try c.placementPairs(.polyline, count: nil)
        detours = try c.decode([PlacementDetour].self, forKey: .detours)
        crossings = try c.decode([PlacementCrossing].self, forKey: .crossings)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(outcome, forKey: .outcome)
        try c.encode(lengthFt, forKey: .lengthFt)
        try c.encode(plusMinusFt, forKey: .plusMinusFt)
        try c.encode(heightFt, forKey: .heightFt)
        try c.placementEncode(polyline, forKey: .polyline)
        try c.encode(detours, forKey: .detours)
        try c.encode(crossings, forKey: .crossings)
    }
}

public struct PlacementRule: Codable, Sendable, Equatable {
    public var key: String
    public var source: String
    public var placeholder: Bool

    private enum CodingKeys: String, CodingKey, CaseIterable { case key, source, placeholder }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        source = try c.decode(String.self, forKey: .source)
        placeholder = try c.decode(Bool.self, forKey: .placeholder)
    }
}

public struct PlacementCheck: Codable, Sendable, Equatable {
    public var id: String
    public var label: String
    public var outcome: PlacementOutcome
    public var unsureCause: PlacementUnsureCause?
    public var reason: String
    public var measuredFt: Double?
    public var plusMinusFt: Double?
    public var thresholdFt: Double?
    /// A second, stricter line on the passing side of `thresholdFt`, on checks with a review band
    /// (route_length): a measurement that doesn't clear it is at best UNSURE even when it clears
    /// `thresholdFt` (result.schema.json). Optional in the schema; nil when the answer has none.
    public var reviewThresholdFt: Double?
    public var comparison: PlacementComparison?
    public var subject: String?
    public var rule: PlacementRule

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case id, label, outcome, reason, comparison, subject, rule
        case unsureCause = "unsure_cause"
        case measuredFt = "measured_ft"
        case plusMinusFt = "plus_minus_ft"
        case thresholdFt = "threshold_ft"
        case reviewThresholdFt = "review_threshold_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        label = try c.decode(String.self, forKey: .label)
        outcome = try c.decode(PlacementOutcome.self, forKey: .outcome)
        unsureCause = try c.decodeIfPresent(PlacementUnsureCause.self, forKey: .unsureCause)
        reason = try c.decode(String.self, forKey: .reason)
        measuredFt = try c.decode(Double?.self, forKey: .measuredFt)
        plusMinusFt = try c.decode(Double?.self, forKey: .plusMinusFt)
        thresholdFt = try c.decode(Double?.self, forKey: .thresholdFt)
        reviewThresholdFt = try c.decodeIfPresent(Double.self, forKey: .reviewThresholdFt)
        comparison = try c.decode(PlacementComparison?.self, forKey: .comparison)
        subject = try c.decodeIfPresent(String.self, forKey: .subject)
        rule = try c.decode(PlacementRule.self, forKey: .rule)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(label, forKey: .label)
        try c.encode(outcome, forKey: .outcome)
        try c.encodeIfPresent(unsureCause, forKey: .unsureCause)
        try c.encode(reason, forKey: .reason)
        try c.encode(measuredFt, forKey: .measuredFt)
        try c.encode(plusMinusFt, forKey: .plusMinusFt)
        try c.encode(thresholdFt, forKey: .thresholdFt)
        try c.encodeIfPresent(reviewThresholdFt, forKey: .reviewThresholdFt)
        try c.encode(comparison, forKey: .comparison)
        try c.encodeIfPresent(subject, forKey: .subject)
        try c.encode(rule, forKey: .rule)
    }
}

public struct PlacementMissingEvidence: Codable, Sendable, Equatable {
    public var kind: PlacementEvidenceKind
    public var band: PlacementBand?
    public var spanFt: SIMD2<Double>?
    /// Ground, facing and overhead requests: how far the view must reach, feet, out from the wall
    /// (ground, facing) or up from the ground (overhead). An observed entry settles the request
    /// when its `out_ft` is at least this.
    public var outFt: Double?
    public var side: PlacementSide?
    public var checks: [String]?
    public var message: String

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case kind, band, side, checks, message
        case spanFt = "span_ft"
        case outFt = "out_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(PlacementEvidenceKind.self, forKey: .kind)
        band = try c.decodeIfPresent(PlacementBand.self, forKey: .band)
        spanFt = c.contains(.spanFt) ? try c.placementPair(.spanFt) : nil
        outFt = try c.decodeIfPresent(Double.self, forKey: .outFt)
        side = try c.decodeIfPresent(PlacementSide.self, forKey: .side)
        checks = try c.decodeIfPresent([String].self, forKey: .checks)
        message = try c.decode(String.self, forKey: .message)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(kind, forKey: .kind)
        try c.encodeIfPresent(band, forKey: .band)
        if let spanFt { try c.placementEncode(spanFt, forKey: .spanFt) }
        try c.encodeIfPresent(outFt, forKey: .outFt)
        try c.encodeIfPresent(side, forKey: .side)
        try c.encodeIfPresent(checks, forKey: .checks)
        try c.encode(message, forKey: .message)
    }
}

/// A marked object the server did not use (result.schema.json `objects_not_used`).
public struct PlacementObjectNotUsed: Codable, Sendable, Equatable {
    /// "objects[<index>] <type>", as in a check's `subject`.
    public var object: String
    /// "left" or "right". Kept as a string: the app doesn't read it, so a value the server adds
    /// later mustn't stop the result screen.
    public var side: String
    /// The server's sentence for the homeowner.
    public var message: String

    public init(object: String, side: String, message: String) {
        self.object = object
        self.side = side
        self.message = message
    }
}

public struct PlacementEnd: Codable, Sendable, Equatable {
    public var kind: PlacementEndKind
    /// Where the wall chain ends, in s feet.
    public var sFt: Double
    public var point: SIMD2<Double>
    public var beyondReach: Bool?

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case kind, point
        case sFt = "s_ft"
        case beyondReach = "beyond_reach"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(PlacementEndKind.self, forKey: .kind)
        sFt = try c.decode(Double.self, forKey: .sFt)
        point = try c.placementPair(.point)
        beyondReach = try c.decodeIfPresent(Bool.self, forKey: .beyondReach)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(kind, forKey: .kind)
        try c.encode(sFt, forKey: .sFt)
        try c.placementEncode(point, forKey: .point)
        try c.encodeIfPresent(beyondReach, forKey: .beyondReach)
    }
}

public struct PlacementEnds: Codable, Sendable, Equatable {
    public var left: PlacementEnd
    public var right: PlacementEnd

    private enum CodingKeys: String, CodingKey, CaseIterable { case left, right }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        left = try c.decode(PlacementEnd.self, forKey: .left)
        right = try c.decode(PlacementEnd.self, forKey: .right)
    }
}

public struct PlacementSweepRun: Codable, Sendable, Equatable {
    public var wallID: String
    /// Range of battery left-edge positions in s feet covered by this run.
    public var startFt: SIMD2<Double>
    public var outcome: PlacementOutcome
    public var failing: [String]
    public var unsure: [String]

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case outcome, failing, unsure
        case wallID = "wall_id"
        case startFt = "start_ft"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        wallID = try c.decode(String.self, forKey: .wallID)
        startFt = try c.placementPair(.startFt)
        outcome = try c.decode(PlacementOutcome.self, forKey: .outcome)
        failing = try c.decode([String].self, forKey: .failing)
        unsure = try c.decode([String].self, forKey: .unsure)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(wallID, forKey: .wallID)
        try c.placementEncode(startFt, forKey: .startFt)
        try c.encode(outcome, forKey: .outcome)
        try c.encode(failing, forKey: .failing)
        try c.encode(unsure, forKey: .unsure)
    }
}

public struct PlacementStats: Codable, Sendable, Equatable {
    public var candidates: Int
    public var pass: Int
    public var unsure: Int
    public var fail: Int
    public var elapsedMs: Double
    public var inputSHA256: String

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case candidates, pass, unsure, fail
        case elapsedMs = "elapsed_ms"
        case inputSHA256 = "input_sha256"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        candidates = try c.decode(Int.self, forKey: .candidates)
        pass = try c.decode(Int.self, forKey: .pass)
        unsure = try c.decode(Int.self, forKey: .unsure)
        fail = try c.decode(Int.self, forKey: .fail)
        elapsedMs = try c.decode(Double.self, forKey: .elapsedMs)
        inputSHA256 = try c.decode(String.self, forKey: .inputSHA256)
    }
}

public struct PlacementResult: Codable, Sendable, Equatable {
    public var schemaVersion: String
    public var decision: PlacementDecision
    public var summary: String
    public var reasons: [PlacementReason]
    public var policy: PlacementPolicy
    public var spot: PlacementSpot?
    public var route: PlacementRoute?
    public var checks: [PlacementCheck]
    public var nearestConsidered: PlacementSpot?
    public var missingEvidence: [PlacementMissingEvidence]
    /// Marks the server set aside: no footprint, past an unexplored end where the wall may turn
    /// (`objects_not_used`). Optional in schema 1.0; empty when the answer has none.
    public var objectsNotUsed: [PlacementObjectNotUsed]
    public var ends: PlacementEnds
    public var sweep: [PlacementSweepRun]
    public var stats: PlacementStats

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case decision, summary, reasons, policy, spot, route, checks, ends, sweep, stats
        case schemaVersion = "schema_version"
        case nearestConsidered = "nearest_considered"
        case missingEvidence = "missing_evidence"
        case objectsNotUsed = "objects_not_used"
    }

    /// Decodes a server result, throwing `PlacementDecodingError` for anything outside the schema.
    public static func decode(_ data: Data) throws -> PlacementResult {
        do {
            return try JSONDecoder().decode(PlacementResult.self, from: data)
        } catch let error as PlacementDecodingError {
            throw error
        } catch let DecodingError.keyNotFound(key, context) {
            throw PlacementDecodingError.missingKey(path: placementPath(context.codingPath + [key]))
        } catch let DecodingError.typeMismatch(_, context) {
            throw PlacementDecodingError.malformed(path: placementPath(context.codingPath), detail: context.debugDescription)
        } catch let DecodingError.valueNotFound(_, context) {
            throw PlacementDecodingError.malformed(path: placementPath(context.codingPath), detail: context.debugDescription)
        } catch let DecodingError.dataCorrupted(context) {
            throw PlacementDecodingError.malformed(path: placementPath(context.codingPath), detail: context.debugDescription)
        }
    }

    public init(from decoder: any Decoder) throws {
        // Version first: a newer server may add keys, and the version is the clearer error.
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decode(String.self, forKey: .schemaVersion)
        guard schemaVersion == "1.0" else { throw PlacementDecodingError.unsupportedSchemaVersion(schemaVersion) }
        decision = try c.decode(PlacementDecision.self, forKey: .decision)
        summary = try c.decode(String.self, forKey: .summary)
        reasons = try c.decode([PlacementReason].self, forKey: .reasons)
        policy = try c.decode(PlacementPolicy.self, forKey: .policy)
        spot = try c.decode(PlacementSpot?.self, forKey: .spot)
        route = try c.decode(PlacementRoute?.self, forKey: .route)
        checks = try c.decode([PlacementCheck].self, forKey: .checks)
        nearestConsidered = try c.decodeIfPresent(PlacementSpot.self, forKey: .nearestConsidered)
        missingEvidence = try c.decode([PlacementMissingEvidence].self, forKey: .missingEvidence)
        objectsNotUsed = try c.decodeIfPresent([PlacementObjectNotUsed].self, forKey: .objectsNotUsed) ?? []
        ends = try c.decode(PlacementEnds.self, forKey: .ends)
        sweep = try c.decode([PlacementSweepRun].self, forKey: .sweep)
        stats = try c.decode(PlacementStats.self, forKey: .stats)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(decision, forKey: .decision)
        try c.encode(summary, forKey: .summary)
        try c.encode(reasons, forKey: .reasons)
        try c.encode(policy, forKey: .policy)
        try c.encode(spot, forKey: .spot)
        try c.encode(route, forKey: .route)
        try c.encode(checks, forKey: .checks)
        try c.encodeIfPresent(nearestConsidered, forKey: .nearestConsidered)
        try c.encode(missingEvidence, forKey: .missingEvidence)
        if !objectsNotUsed.isEmpty { try c.encode(objectsNotUsed, forKey: .objectsNotUsed) }
        try c.encode(ends, forKey: .ends)
        try c.encode(sweep, forKey: .sweep)
        try c.encode(stats, forKey: .stats)
    }
}

/// An unexplored wall end past which a spot nearer the meter could lie
/// (`PlacementResult.closerUnseenEnd()`).
public struct PlacementUnseenEnd: Sendable, Equatable {
    public var side: PlacementSide
    /// Where the scan stopped, in s feet from the meter.
    public var sFt: Double

    public init(side: PlacementSide, sFt: Double) {
        self.side = side
        self.sFt = sFt
    }
}

extension PlacementResult {
    /// The end to name in "the scan stopped here, a closer spot may be past there", or nil when
    /// no end calls for it (issue #83).
    ///
    /// Only an unexplored end within cable reach counts: a limit end has no wall past it, and
    /// wall past an end beyond reach can't hold the battery. With a spot, the end must be nearer
    /// the meter than the spot's near edge, so a spot past it could be closer; a spot over the
    /// meter leaves none. Of the ends left, the one nearest the meter, whichever side it is on:
    /// the server lists its past_end requests left first, which says nothing about distance.
    public func closerUnseenEnd() -> PlacementUnseenEnd? {
        let nearEdge: Double? = spot.map { spot in
            let low = min(spot.spanFt.x, spot.spanFt.y), high = max(spot.spanFt.x, spot.spanFt.y)
            return low <= 0 && high >= 0 ? 0 : min(abs(low), abs(high))
        }
        var candidates: [PlacementUnseenEnd] = []
        for (side, end) in [(PlacementSide.left, ends.left), (PlacementSide.right, ends.right)]
        where end.kind == .unexplored && end.beyondReach != true {
            if let nearEdge, abs(end.sFt) >= nearEdge { continue }
            candidates.append(PlacementUnseenEnd(side: side, sFt: end.sFt))
        }
        return candidates.min { abs($0.sFt) < abs($1.sFt) }
    }
}

// MARK: - Strict decoding helpers

/// "checks[2].rule.key" style path for error messages.
private func placementPath(_ codingPath: [any CodingKey]) -> String {
    var path = ""
    for key in codingPath {
        if let index = key.intValue {
            path += "[\(index)]"
        } else {
            path += path.isEmpty ? key.stringValue : ".\(key.stringValue)"
        }
    }
    return path.isEmpty ? "$" : path
}

private func placementEnum<E: RawRepresentable>(_ decoder: any Decoder) throws -> E where E.RawValue == String {
    let raw = try decoder.singleValueContainer().decode(String.self)
    guard let value = E(rawValue: raw) else {
        throw PlacementDecodingError.unknownEnumValue(path: placementPath(decoder.codingPath), value: raw)
    }
    return value
}

extension KeyedDecodingContainer {
    fileprivate func placementPair(_ key: Key) throws -> SIMD2<Double> {
        let values = try decode([Double].self, forKey: key)
        guard values.count == 2 else {
            throw PlacementDecodingError.wrongArrayLength(path: placementPath(codingPath + [key]), expected: 2, actual: values.count)
        }
        return SIMD2(values[0], values[1])
    }

    fileprivate func placementPairs(_ key: Key, count: Int?) throws -> [SIMD2<Double>] {
        let rows = try decode([[Double]].self, forKey: key)
        if let count, rows.count != count {
            throw PlacementDecodingError.wrongArrayLength(path: placementPath(codingPath + [key]), expected: count, actual: rows.count)
        }
        return try rows.enumerated().map { index, row in
            guard row.count == 2 else {
                throw PlacementDecodingError.wrongArrayLength(
                    path: placementPath(codingPath + [key]) + "[\(index)]", expected: 2, actual: row.count)
            }
            return SIMD2(row[0], row[1])
        }
    }
}

extension KeyedEncodingContainer {
    fileprivate mutating func placementEncode(_ pair: SIMD2<Double>, forKey key: Key) throws {
        try encode([pair.x, pair.y], forKey: key)
    }

    fileprivate mutating func placementEncode(_ pairs: [SIMD2<Double>], forKey key: Key) throws {
        try encode(pairs.map { [$0.x, $0.y] }, forKey: key)
    }
}
