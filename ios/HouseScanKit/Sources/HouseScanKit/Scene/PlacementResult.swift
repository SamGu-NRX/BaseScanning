import Foundation

// The placement server's answer (contract C2, server/schemas/result.schema.json). A missing
// required key, a wrong type, an unknown enum value, a wrong array length or a schema_version
// other than "1.0" throws PlacementDecodingError naming the JSON path. Keys marked
// required-but-nullable in the schema must be present.
//
// Unknown keys are ignored. The server adds optional fields within schema 1.0 without changing
// schema_version: `checks[].review_threshold_ft` and `sweep[].segment` arrived that way on
// 2026-09-26, and rejecting them stopped every real upload from reaching the result screen. An
// unknown value of an enum the app presents (decision, outcome, unsure_cause, and the others
// below) still fails, because the app can't show a decision or outcome it doesn't know.
// `reasons[].code`, `policy.sources` and `route.crossings[].effect` are plain strings: the app
// never reads them, so a value the server adds to them must not break every decode.
// `missing_evidence[].kind` and `.band` decode an unknown value as `.unknown`: one request the
// app can't act on goes to installer review instead of hiding the whole answer. The keys and
// their types stay required.

/// Why a server answer failed to decode, naming the JSON path it failed at.
///
/// `PlacementResult.decode(_:)` throws one for anything outside the result schema the app
/// still cares about: a missing required key, a wrong type or array length, an unsupported
/// `schema_version`, or an unknown value of an enum the app presents. What a real answer may
/// add without failing is the subject of the note at the top of this file.
public enum PlacementDecodingError: Error, Equatable, CustomStringConvertible {
    case unsupportedSchemaVersion(String)
    case unknownEnumValue(path: String, value: String)
    case wrongArrayLength(path: String, expected: Int, actual: Int)
    case missingKey(path: String)
    case malformed(path: String, detail: String)

    /// The failure as one sentence naming the path, for logs and review screens.
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

/// The server's overall call: install the battery, hand the decision to a person, or don't.
///
/// `pass` is a fully observed spot passing every check under an approved policy. `manualReview`
/// means a person must decide: the best spot has an unsure check, the policy is not approved for
/// automatic decisions (`PlacementPolicy.autoApprove`), or an area that could hold a valid spot
/// was not seen. `reject` means every spot within reach fails by a clear margin and both ends of
/// the walk are known. The screen reads the checks alongside the decision
/// (`ResultReading.answer(decision:policyApproved:hasSpot:checks:)`), so a manual_review held
/// back only by the rules' approval still shows as a fit.
public enum PlacementDecision: String, Codable, Sendable, Equatable {
    case pass
    case manualReview = "manual_review"
    case reject
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// How one thing fared — a check, the spot, the cable route, a stretch of the sweep.
///
/// `unsure` is not a soft fail: the deciding evidence is missing, and `PlacementUnsureCause`
/// says what would settle it. A spot the server chose carries no failing check (it is the best
/// passing spot, or the best with none failing), so it arrives `pass` or `unsure`.
public enum PlacementOutcome: String, Codable, Sendable, Equatable {
    case pass
    case fail
    case unsure
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// Why a check came out unsure. The cause decides who can settle it.
///
/// `margin` is a measurement inside its error band, so only a person can judge it;
/// `unobserved` is a deciding area nobody saw, and more photos fix it
/// (`PlacementMissingEvidence` asks for them); `unknownAttribute` is a fact the camera did not
/// establish, such as whether a window opens; `ruleRequiresReview` is a situation the policy
/// sends to a person regardless, such as a cable routed over a door.
/// `PlacementCheck.needsPerson` sorts the first, third and fourth to a person and the second
/// to the camera.
public enum PlacementUnsureCause: String, Codable, Sendable, Equatable {
    case margin
    case unobserved
    case unknownAttribute = "unknown_attribute"
    case ruleRequiresReview = "rule_requires_review"
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// Which side of the threshold passes: `atLeast` wants the measurement above it, `atMost` below.
///
/// The measurement's error is on the passing side's tally: `atLeast` passes when measured less
/// its error clears the threshold, `atMost` when measured plus its error stays under it.
/// `ResultReading.margin(measured:threshold:plusMinus:comparison:)` turns this into a signed
/// clearance for sorting checks.
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

    /// The server's string for a kind, kept as `.unknown` when this app doesn't know it.
    public init(rawValue: String) {
        self = switch rawValue {
        case "band": .band
        case "past_end": .pastEnd
        default: .unknown(rawValue)
        }
    }

    /// The server's string: a known kind's wire name, or an unknown kind's raw value, so a
    /// round trip preserves what the server wrote.
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

    /// The server's string for a band, kept as `.unknown` when this app doesn't know it.
    public init(rawValue: String) {
        self = switch rawValue {
        case "wall": .wall
        case "ground": .ground
        case "overhead": .overhead
        case "facing": .facing
        default: .unknown(rawValue)
        }
    }

    /// The server's string: a known band's wire name, or an unknown band's raw value, so a
    /// round trip preserves what the server wrote.
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

/// Which side of the meter an end or a request is on: left is the negative side of s, right the
/// positive.
public enum PlacementSide: String, Codable, Sendable, Equatable {
    case left
    case right
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// Whether a wall end is a real limit or just where the walk stopped.
///
/// `limit` is an end with no usable wall past it (a fence, a corner the walk does not follow);
/// `unexplored` is where the scan stopped, and the wall may continue — a spot just past it could
/// still be the better answer (`PlacementResult.closerUnseenEnd()`).
public enum PlacementEndKind: String, Codable, Sendable, Equatable {
    case limit
    case unexplored
    public init(from decoder: any Decoder) throws { self = try placementEnum(decoder) }
}

/// One reason for the decision, in the server's own words.
///
/// A reason speaks for the whole scan — the policy, an unobserved area, an unexplored end — not
/// for one spot; when no spot was chosen, the failing check at the spot nearest to passing
/// explains it instead (`PlacementResult.nearestFailure`). The app shows `message` and never
/// reads `code` (see the top of this file).
public struct PlacementReason: Codable, Sendable, Equatable {
    /// A server enum the app doesn't read, kept as the raw string (see the top of this file).
    public var code: String
    /// The sentence to show for this reason.
    public var message: String
    /// Ids of the checks this reason is about, when it is about specific ones.
    public var checks: [String]?

    private enum CodingKeys: String, CodingKey, CaseIterable { case code, message, checks }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decode(String.self, forKey: .code)
        message = try c.decode(String.self, forKey: .message)
        checks = try c.decodeIfPresent([String].self, forKey: .checks)
    }
}

/// Which rules judged the scan, and whether they allow the server to decide on its own.
///
/// The app stamps the rule set's identity beside the scan (`ScanStamp.Answer` keeps `id`,
/// `version` and `rulesSHA256`) and shows it with the answer, so a result can be traced to the
/// rules that produced it.
public struct PlacementPolicy: Codable, Sendable, Equatable {
    /// The rule set's id, when the server names one; nil for no named policy (the bundled
    /// sample has none).
    public var id: String?
    /// The rule set's revision, when the server names one.
    public var version: String?
    /// Whether the rules allow automatic decisions. False when no policy is selected or the
    /// rules set it false — the public strict policy does, because some of its values are
    /// placeholders — and then every would-be pass or reject arrives as `manual_review`. Which
    /// of a check's values were placeholders is `PlacementRule.placeholder`.
    public var autoApprove: Bool
    /// A server enum the app doesn't read, kept as raw strings (see the top of this file).
    public var sources: [String]
    /// Hash of the merged rules the decision used; `PlacementPolicy.rulesShortHash` shortens
    /// it to the part the screen shows.
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

/// One battery position the server evaluated, with its stretch of wall, footprint and cable run.
///
/// `PlacementResult.spot` is the chosen one, `PlacementResult.nearestConsidered` the closest to
/// passing when nothing was chosen. Positions are plan coordinates [x, z] feet in the scene
/// frame; map them onto the wall with `SceneWall.wallCoordinates(ofPlanPointFeet:)`. The schema
/// carries no ground height, so the app rests the drawn box on its detected ground plane.
public struct PlacementSpot: Codable, Sendable, Equatable {
    /// How the spot fared (`pass`, `unsure` or `fail`). The chosen spot carries no failing
    /// check, so it arrives `pass` or `unsure`.
    public var outcome: PlacementOutcome
    /// The wall the battery backs onto; `segment` is the straight piece of it.
    public var wallID: String
    /// Index of the straight baseline segment of `wallID` the battery backs onto.
    public var segment: Int
    /// Battery's stretch of wall in s feet, left edge first.
    public var spanFt: SIMD2<Double>
    /// The battery's width along the wall, feet.
    public var widthFt: Double
    /// The battery's depth out from the wall, feet.
    public var depthFt: Double
    /// The battery's height above the ground, feet.
    public var heightFt: Double
    /// Plan corners [x, z] feet: back-left, back-right, front-right, front-left.
    public var footprint: [SIMD2<Double>]
    /// Plan centre of the footprint, [x, z] feet.
    public var center: SIMD2<Double>
    /// Unit vector along the wall toward +s, [x, z].
    public var along: SIMD2<Double>
    /// Unit vector out from the wall toward the battery's front, [x, z].
    public var outward: SIMD2<Double>
    /// Footprint centre minus the meter's plan position, [dx, dz] feet.
    public var meterOffsetFt: SIMD2<Double>
    /// The cable run from the meter to this spot, feet, when the server reports one.
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

/// A place the cable route goes over or under something on the wall, and what the round trip
/// costs.
///
/// An object whose crossing effect is `detour` stays where it is and the cable bends: up and
/// over or down and under, whichever is shorter. The bend's `extraFt` is added to
/// `PlacementRoute.lengthFt`.
public struct PlacementDetour: Codable, Sendable, Equatable {
    /// What the route bends around, with the same naming `PlacementCheck.subject` uses.
    public var subject: String
    /// The extra feet the bend adds to the route.
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

/// Something on the stretch of wall the cable route passes, and what the rules make of it.
///
/// Two things appear here: a stretch with no wall at all (effect `fail` when the gap is longer
/// than the walls' errors, `review` when it may not be a gap) and an object whose rule names an
/// effect for its type. `effect` is kept as the raw string the server wrote — `fail`, `review`,
/// `detour` or `allow` — because the app never reads it (see the top of this file).
public struct PlacementCrossing: Codable, Sendable, Equatable {
    /// What is crossed: an object's name, or "stretch with no wall".
    public var subject: String
    /// The stretch of wall, in s feet, over which the route meets it.
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

/// The cable route from the meter to the chosen battery spot; nil whenever the spot is.
///
/// The route runs along the supported wall, bending around the objects the rules let it go over
/// or under (`detours`), and everything it passes is reported in `crossings`. Its `outcome` is
/// the route's own verdict, separate from the spot's.
public struct PlacementRoute: Codable, Sendable, Equatable {
    /// How the cable run fared: `pass`, `unsure` or `fail`.
    public var outcome: PlacementOutcome
    /// Length along the wall from the meter to the battery's near edge, feet: the wall stretch
    /// itself, an allowance per corner it rounds, and every detour's `PlacementDetour.extraFt`.
    public var lengthFt: Double
    /// How far `lengthFt` may be off, feet: the meter's and wall's own errors, plus the round
    /// trip past anything the route detours around on uncertain heights.
    public var plusMinusFt: Double
    /// Height above the ground the cable runs at, feet, from the rules.
    public var heightFt: Double
    /// Plan points [x, z] feet from the meter along the wall to the battery. Map them onto the wall
    /// with `SceneWall.wallCoordinates(ofPlanPointFeet:)`.
    public var polyline: [SIMD2<Double>]
    /// The bends the route makes around wall objects, each with the feet it costs.
    public var detours: [PlacementDetour]
    /// Everything on the wall the route passes: gaps with no wall, and objects the rules name
    /// an effect for.
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

/// The rule value a check was judged by, with where the value came from.
///
/// Whether a value is a `placeholder` — a demo value with no public source — matters beyond
/// this one check: the public strict policy refuses automatic decisions while any of its values
/// is one, which is one way `PlacementPolicy.autoApprove` comes out false. Each check reports
/// its own value's standing here.
public struct PlacementRule: Codable, Sendable, Equatable {
    /// The parameter's name in the server's rules.yaml.
    public var key: String
    /// Citation for the value, as the rules file gave it.
    public var source: String
    /// True for a demo value with no public source.
    public var placeholder: Bool

    private enum CodingKeys: String, CodingKey, CaseIterable { case key, source, placeholder }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        source = try c.decode(String.self, forKey: .source)
        placeholder = try c.decode(Bool.self, forKey: .placeholder)
    }
}

/// One rule check at a spot: what was measured, against what, and how it came out.
///
/// `PlacementResult.checks` holds the checks at the chosen spot — or, when no spot was chosen,
/// at `PlacementResult.nearestConsidered`, where they explain why that spot fails. An `unsure`
/// outcome names its cause, and `PlacementCheck.needsPerson` separates the unsure checks a
/// person must judge from the ones another view settles.
public struct PlacementCheck: Codable, Sendable, Equatable {
    /// Stable identifier, for example `gas_clearance` or `route_length`; requests for more
    /// evidence name the checks they settle by it.
    public var id: String
    /// The check's name to show, for example "Distance from the gas meter".
    public var label: String
    /// How the check came out; an `unsure` one says why in `unsureCause`.
    public var outcome: PlacementOutcome
    /// Why the check is unsure, present only for an `unsure` outcome. Nil on an unsure check
    /// is itself unexplained, so a person looks at it (`PlacementCheck.needsPerson`).
    public var unsureCause: PlacementUnsureCause?
    /// The server's sentence for the outcome, shown under the label.
    public var reason: String
    /// The deciding measurement, feet — the gap to the nearest gas meter, for one. Nil when
    /// nothing relevant was found or the area was not observed.
    public var measuredFt: Double?
    /// The measurement's error, feet.
    public var plusMinusFt: Double?
    /// The limit the measurement is judged against, feet.
    public var thresholdFt: Double?
    /// Which side of `thresholdFt` passes (`PlacementComparison`). Nil when the server gives
    /// no direction, and the distance to the limit counts either way
    /// (`ResultReading.margin(measured:threshold:plusMinus:comparison:)`).
    public var comparison: PlacementComparison?
    /// What the measurement is to, for example "objects[2] gas_meter"; nil when the check has
    /// no such counterpart (the cable-run check measures the route, not a distance to
    /// something).
    public var subject: String?
    /// The rule value this check was judged by (`PlacementRule`).
    public var rule: PlacementRule

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case id, label, outcome, reason, comparison, subject, rule
        case unsureCause = "unsure_cause"
        case measuredFt = "measured_ft"
        case plusMinusFt = "plus_minus_ft"
        case thresholdFt = "threshold_ft"
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
        try c.encode(comparison, forKey: .comparison)
        try c.encodeIfPresent(subject, forKey: .subject)
        try c.encode(rule, forKey: .rule)
    }
}

/// A view the scan still needs: one that would settle an `unsure` check or an unobserved area.
///
/// Only areas nobody observed are asked for — an unsure measurement inside its error band needs
/// a person, not more photos — and the list is empty for `pass` and `reject` decisions. Filming
/// exactly what a request names settles it: a coverage entry with the same band and span, seen
/// out at least to `outFt`. `GapPlanner.plan(for:leftEnd:rightEnd:)` turns the band requests
/// into the walk's next asks; a kind or band this app doesn't know decodes as `.unknown` and
/// can only go to installer review.
public struct PlacementMissingEvidence: Codable, Sendable, Equatable {
    /// What view is wanted: a band of coverage (`band`), a look past a wall end (`pastEnd`),
    /// or a kind this app doesn't know (`PlacementEvidenceKind.unknown`).
    public var kind: PlacementEvidenceKind
    /// Which band the request asks about, nil when it names none (a `pastEnd` request names a
    /// side instead).
    public var band: PlacementBand?
    /// The stretch of wall the view must cover, in s feet, when the request names one.
    public var spanFt: SIMD2<Double>?
    /// Ground, facing and overhead requests: how far the view must reach, feet, out from the wall
    /// (ground, facing) or up from the ground (overhead). An observed entry settles the request
    /// when its `out_ft` is at least this.
    public var outFt: Double?
    /// Which end to look past, for a `pastEnd` request.
    public var side: PlacementSide?
    /// Ids of the checks answering this request settles, when it settles any;
    /// `PlacementResult.evidenceIndex(settling:)` finds the request for a check id.
    public var checks: [String]?
    /// The ask in words, for the screen: "Sample: film the ground in front of the spot."
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

/// Where the wall chain ends on one side of the meter.
///
/// A `limit` end is a real end of usable wall; an `unexplored` one is only where the walk
/// stopped, and `beyondReach` says whether anything past it could matter anyway.
public struct PlacementEnd: Codable, Sendable, Equatable {
    /// Whether this end is a real limit or unexplored (`PlacementEndKind`).
    public var kind: PlacementEndKind
    /// Where the wall chain ends, in s feet.
    public var sFt: Double
    /// The end's plan position, [x, z] feet.
    public var point: SIMD2<Double>
    /// True when the end is so far along the wall that no spot past it could pass the
    /// route-length check: an unexplored end there does not block a `reject`, and
    /// `PlacementResult.closerUnseenEnd()` never names it.
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

/// The two ends of the wall chain the server worked from, one each side of the meter.
public struct PlacementEnds: Codable, Sendable, Equatable {
    /// The end on the left, at negative s.
    public var left: PlacementEnd
    /// The end on the right, at positive s.
    public var right: PlacementEnd

    private enum CodingKeys: String, CodingKey, CaseIterable { case left, right }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        left = try c.decode(PlacementEnd.self, forKey: .left)
        right = try c.decode(PlacementEnd.self, forKey: .right)
    }
}

/// One stretch of wall and how battery positions starting on it fared, merged with neighbours
/// of equal outcome.
///
/// Together the runs colour the wall on the site plan: each stretch fails because of the checks
/// in `failing`, is held by those in `unsure`, or passes. A stretch too far along the wall for
/// any cable route is one failing run, not evaluated start by start.
public struct PlacementSweepRun: Codable, Sendable, Equatable {
    /// The wall this run is on.
    public var wallID: String
    /// Range of battery left-edge positions in s feet covered by this run.
    public var startFt: SIMD2<Double>
    /// How battery positions starting in this run fared.
    public var outcome: PlacementOutcome
    /// Ids of the checks that fail in this run.
    public var failing: [String]
    /// Ids of the checks that are unsure in this run.
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

/// The solver's tally for one answer, and the hashes that tie it to what was judged.
public struct PlacementStats: Codable, Sendable, Equatable {
    /// Battery positions the solver evaluated.
    public var candidates: Int
    /// How many evaluated positions passed.
    public var pass: Int
    /// How many evaluated positions came out unsure.
    public var unsure: Int
    /// How many evaluated positions came out failing.
    public var fail: Int
    /// How long the solver took over the whole answer, milliseconds.
    public var elapsedMs: Double
    /// Hash of the scene JSON as uploaded; `ScanStamp.Answer` keeps it beside the scan so an
    /// answer can be matched to the scene it judged.
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

/// The placement server's answer for one scan, decoded strictly against the result schema.
///
/// `PlacementResult.decode(_:)` is the way in. The parts read in the order the screen leads
/// with them: the `decision` and `summary` carry the verdict, `checks` back it at the chosen
/// `spot` (or at `nearestConsidered` when no spot was chosen), `missingEvidence` asks for the
/// views that would settle what is unsure, `ends` and `sweep` carry the whole-wall picture, and
/// `stats` the solver's tally.
public struct PlacementResult: Codable, Sendable, Equatable {
    /// The schema the answer speaks; decode accepts only "1.0" and throws
    /// `PlacementDecodingError.unsupportedSchemaVersion` for anything else, a newer server's
    /// version included.
    public var schemaVersion: String
    /// The server's overall call (`PlacementDecision`).
    public var decision: PlacementDecision
    /// One sentence for the homeowner or reviewer, with the policy's `notice` appended when
    /// there is one (`summaryWithoutNotice` splits them back apart).
    public var summary: String
    /// Why the decision is what it is, in the server's words (`PlacementReason`).
    public var reasons: [PlacementReason]
    /// The rules that judged the scan (`PlacementPolicy`).
    public var policy: PlacementPolicy
    /// The chosen battery position: the best passing spot, or for a `manualReview` the best
    /// spot with no failing check. Nil when no such spot exists, including every `reject`.
    public var spot: PlacementSpot?
    /// The cable route to `spot` (`PlacementRoute`); nil whenever the spot is.
    public var route: PlacementRoute?
    /// Every check at the chosen spot — or, when `spot` is nil, at `nearestConsidered`.
    public var checks: [PlacementCheck]
    /// When `spot` is nil, the evaluated spot closest to passing (fewest failing checks, then
    /// fewest unsure, then shortest route), whose checks explain why it fails; nil otherwise.
    public var nearestConsidered: PlacementSpot?
    /// The views that would settle what is unsure (`PlacementMissingEvidence`); empty for
    /// `pass` and `reject` decisions.
    public var missingEvidence: [PlacementMissingEvidence]
    /// Where the wall chain ends, one each side (`PlacementEnds`).
    public var ends: PlacementEnds
    /// The whole wall as runs of equal outcome (`PlacementSweepRun`).
    public var sweep: [PlacementSweepRun]
    /// The solver's tally (`PlacementStats`).
    public var stats: PlacementStats

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case decision, summary, reasons, policy, spot, route, checks, ends, sweep, stats
        case schemaVersion = "schema_version"
        case nearestConsidered = "nearest_considered"
        case missingEvidence = "missing_evidence"
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
        try c.encode(ends, forKey: .ends)
        try c.encode(sweep, forKey: .sweep)
        try c.encode(stats, forKey: .stats)
    }
}

/// An unexplored wall end past which a spot nearer the meter could lie
/// (`PlacementResult.closerUnseenEnd()`).
public struct PlacementUnseenEnd: Sendable, Equatable {
    /// Which end of the wall is being named.
    public var side: PlacementSide
    /// Where the scan stopped, in s feet from the meter.
    public var sFt: Double

    /// Creates an end for `PlacementResult.closerUnseenEnd()` to name.
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
