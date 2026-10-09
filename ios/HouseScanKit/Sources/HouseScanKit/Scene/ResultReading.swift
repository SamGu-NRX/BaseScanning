import Foundation

// What the result screen leads with, read from the server's answer. The app's screen turns these
// into words; the rules that pick them live here so each one has a test against a decoded answer
// (ResultReadingTests). They read the checks as well as `decision`: a manual_review held back
// only by rules that aren't approved yet still fits, and a spot that might stand in the meter's
// working space goes to an installer whatever the decision says.

/// What the result screen leads with, read from the server's answer: the headline (`Answer`),
/// the checks the card shows (`cardLines`), and how far each passed its check (`Check.margin`).
/// The rules that pick them live here, so each has a test against a decoded answer; the screen
/// only turns them into words.
public enum ResultReading {
    /// The server's check for the NEC 110.26 working space in front of the meter
    /// (server/README.md, "Reading a result"). A spot that might stand in that space is never a
    /// clean fit.
    public static let meterWorkingSpaceCheckID = "meter_working_space"

    /// What the screen says first.
    public enum Answer: String, Equatable, Sendable {
        /// The spot passes every check; at most the rules' approval holds it back.
        case fits
        /// An unsure check that a view the camera can take now would settle.
        case oneMoreLook
        /// A person has to decide: a borderline measurement, an unknown attribute, a spot that
        /// might stand in the meter's working space, or nothing a view could settle.
        case installer
        /// Every spot within reach fails.
        case notHere
    }

    /// One check as the reading needs it, in whatever units the caller uses.
    public struct Check: Equatable, Sendable {
        public var id: String
        public var outcome: PlacementOutcome
        /// For an UNSURE check, true when a person has to judge it (`PlacementCheck.needsPerson`).
        public var needsPerson: Bool
        /// True when a view the camera can take now would settle it.
        public var viewCapturable: Bool
        /// How far the measurement clears its limit (`margin(measured:threshold:plusMinus:comparison:)`);
        /// nil without both a measurement and a limit.
        public var margin: Double?

        public init(id: String, outcome: PlacementOutcome, needsPerson: Bool = false, viewCapturable: Bool = false, margin: Double? = nil) {
            self.id = id
            self.outcome = outcome
            self.needsPerson = needsPerson
            self.viewCapturable = viewCapturable
            self.margin = margin
        }
    }

    /// True when there is a spot, the server checked it at all, and no meter working-space check
    /// at it has an outcome other than PASS. Other checks, including ids this app doesn't know,
    /// don't count here. A spot with no checks is never clean: the schema allows `checks: []`,
    /// and "none failed" over no checks is no evidence.
    public static func spotIsClean(hasSpot: Bool, checks: [Check]) -> Bool {
        hasSpot && !checks.isEmpty && checks.allSatisfy { $0.id != meterWorkingSpaceCheckID || $0.outcome == .pass }
    }

    public static func answer(decision: PlacementDecision, policyApproved: Bool, hasSpot: Bool, checks: [Check]) -> Answer {
        if hasSpot, !spotIsClean(hasSpot: hasSpot, checks: checks) { return .installer }
        switch decision {
        case .pass:
            return .fits
        case .reject:
            return .notHere
        case .manualReview:
            // Needs at least one check: over none, "every check passes" is vacuously true.
            if hasSpot, !policyApproved, !checks.isEmpty, checks.allSatisfy({ $0.outcome == .pass }) { return .fits }
            if checks.contains(where: { $0.outcome == .unsure && !$0.needsPerson && $0.viewCapturable }) { return .oneMoreLook }
            return .installer
        }
    }

    /// Indices of the checks the result card shows, in order: every FAIL, then every UNSURE, then
    /// the two PASSes closest to their limits (smallest `margin`; a pass without one sorts last).
    /// At most `limit`.
    public static func cardLines(_ checks: [Check], limit: Int = 3) -> [Int] {
        let indices = checks.indices
        let fails = indices.filter { checks[$0].outcome == .fail }
        let unsure = indices.filter { checks[$0].outcome == .unsure }
        // Ties keep the server's order.
        let passes = indices.filter { checks[$0].outcome == .pass }
            .sorted { (checks[$0].margin ?? .infinity, $0) < (checks[$1].margin ?? .infinity, $1) }
            .prefix(2)
        return Array((fails + unsure + passes).prefix(limit))
    }

    /// How far `measured` clears `threshold` on the passing side, in units of `plusMinus` when it
    /// is positive, else in the measurement's own units. Negative when it misses. Without a
    /// comparison the side is unknown, so the distance to the limit either way.
    public static func margin(measured: Double?, threshold: Double?, plusMinus: Double?, comparison: PlacementComparison?) -> Double? {
        guard let measured, let threshold else { return nil }
        let clearance = switch comparison {
        case .atLeast: measured - threshold
        case .atMost: threshold - measured
        case nil: abs(measured - threshold)
        }
        guard let plusMinus, plusMinus > 0 else { return clearance }
        return clearance / plusMinus
    }
}

extension PlacementPolicy {
    /// The first eight characters of `rulesSHA256`: enough to tell two rule sets apart on screen.
    public var rulesShortHash: String {
        String(rulesSHA256.prefix(8))
    }
}

extension PlacementCheck {
    /// For an UNSURE check, true when a person has to judge it: a measurement inside its error
    /// band, an attribute the camera can't establish, or a rule that always goes to review. False
    /// when a view of an unobserved area would settle it, and for PASS and FAIL. An UNSURE with no
    /// cause is unexplained, so a person looks at it.
    public var needsPerson: Bool {
        guard outcome == .unsure else { return false }
        guard let unsureCause else { return true }
        return unsureCause != .unobserved
    }
}

extension PlacementResult {
    /// When no spot passes, the check that rules out `nearestConsidered`: the first FAIL in
    /// `checks`, which describe that spot whenever `spot` is null (result.schema.json). `reasons`
    /// can't name it, because they speak for the whole scan (the policy, an unexplored end).
    public var nearestFailure: PlacementCheck? {
        guard spot == nil, nearestConsidered != nil else { return nil }
        return checks.first { $0.outcome == .fail }
    }

    /// Index in `missingEvidence` of the first view that lists `checkID` among the checks it settles.
    public func evidenceIndex(settling checkID: String) -> Int? {
        missingEvidence.firstIndex { $0.checks?.contains(checkID) == true }
    }

    /// `summary` without the policy notice the solver appends to it ("<summary> <notice>"), so the
    /// app can show the two apart. Unchanged when the summary doesn't end with the notice.
    public var summaryWithoutNotice: String {
        guard let notice = policy.notice, !notice.isEmpty, summary.hasSuffix(notice) else { return summary }
        return String(summary.dropLast(notice.count)).trimmingCharacters(in: .whitespaces)
    }

    /// `checks` as the reading needs them. `capturable` says whether the view at an index of
    /// `missingEvidence` can be taken now; the app knows that from its gap planner.
    public func readingChecks(capturable: (Int) -> Bool) -> [ResultReading.Check] {
        checks.map { check in
            ResultReading.Check(
                id: check.id, outcome: check.outcome, needsPerson: check.needsPerson,
                viewCapturable: evidenceIndex(settling: check.id).map(capturable) ?? false,
                margin: ResultReading.margin(measured: check.measuredFt, threshold: check.thresholdFt,
                                             plusMinus: check.plusMinusFt, comparison: check.comparison)
            )
        }
    }

    /// The answer the screen leads with; see `ResultReading.answer`.
    public func answer(capturable: (Int) -> Bool) -> ResultReading.Answer {
        ResultReading.answer(decision: decision, policyApproved: policy.autoApprove, hasSpot: spot != nil,
                             checks: readingChecks(capturable: capturable))
    }

    /// See `ResultReading.spotIsClean`.
    public var spotIsClean: Bool {
        ResultReading.spotIsClean(hasSpot: spot != nil, checks: readingChecks { _ in false })
    }
}
