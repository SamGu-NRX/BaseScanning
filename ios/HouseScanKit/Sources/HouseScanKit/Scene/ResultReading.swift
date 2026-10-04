import Foundation

// What the result screen leads with, read from the server's answer. The app's screen turns these
// into words; the rules that pick them live here so each one has a test against a decoded answer
// (ResultReadingTests). They read the checks as well as `decision`: a manual_review held back
// only by rules that aren't approved yet is still a candidate, and a spot that might stand in the
// meter's working space goes to an installer whatever the decision says.
//
// No answer says a battery fits. A spot whose checks all pass is a candidate: the server's checks
// read observed space beyond the area the homeowner confirms in the spot check, some of it
// inferred rather than seen (a walked path, a photo's view; `CoverageMap`, "Bounded
// exceptions"), and the answer doesn't say which. Until it does, the app can't tell that all the
// space a battery needs was confirmed (B17). The answer itself is unchanged: this reads it.

public enum ResultReading {
    /// The server's check for the NEC 110.26 working space in front of the meter
    /// (server/README.md, "Reading a result"). A spot that might stand in that space is never a
    /// clean fit.
    public static let meterWorkingSpaceCheckID = "meter_working_space"

    /// What the screen says first.
    public enum Answer: String, Equatable, Sendable {
        /// A proposed spot: every check passes on what the scan recorded, and at most the rules'
        /// approval holds it back. Not a confirmed fit: an installer checks the space on site.
        case candidate
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

        public init(id: String, outcome: PlacementOutcome, needsPerson: Bool = false, viewCapturable: Bool = false) {
            self.id = id
            self.outcome = outcome
            self.needsPerson = needsPerson
            self.viewCapturable = viewCapturable
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
            // A candidate needs what makes one: a spot, and checks that all pass. A pass without a
            // spot, with no checks, or with a check that didn't pass contradicts itself, and a
            // person reads it; its checks stay as the server sent them.
            return hasSpot && allPass(checks) ? .candidate : .installer
        case .reject:
            return .notHere
        case .manualReview:
            if hasSpot, !policyApproved, allPass(checks) { return .candidate }
            if checks.contains(where: { $0.outcome == .unsure && !$0.needsPerson && $0.viewCapturable }) { return .oneMoreLook }
            return .installer
        }
    }

    /// At least one check, and every one passed. Over none, "every check passes" is vacuously
    /// true and says nothing.
    private static func allPass(_ checks: [Check]) -> Bool {
        !checks.isEmpty && checks.allSatisfy { $0.outcome == .pass }
    }

    /// Indices of the checks the result card shows, in order: every FAIL, then every UNSURE, at
    /// most `limit`. No PASS: on the card a passing line read as space confirmed clear, which the
    /// scan can't show (see the top of this file). Every check, passing ones included, stays in
    /// the result's Details.
    public static func cardLines(_ checks: [Check], limit: Int = 3) -> [Int] {
        let indices = checks.indices
        let fails = indices.filter { checks[$0].outcome == .fail }
        let unsure = indices.filter { checks[$0].outcome == .unsure }
        return Array((fails + unsure).prefix(limit))
    }

    /// Whether a check's review line (`PlacementCheck.reviewThresholdFt`) explains its outcome, so
    /// the card can say that past it the check needs review. Reads the server's outcome and never
    /// replaces it. True only when all of these hold:
    /// - the outcome is UNSURE: a PASS cleared the line, and a FAIL is past the limit itself;
    /// - the check has a measurement, a limit, a review line and a direction;
    /// - the review line lies on the passing side of the limit (under a maximum, over a
    ///   minimum), so there is a band between them;
    /// - the measurement doesn't clear the review line by the schema's own test (at_most clears
    ///   when measured + error < review line; at_least when measured - error > review line).
    /// A missing or negative error counts as none.
    public static func reviewBandApplies(
        outcome: PlacementOutcome, measured: Double?, plusMinus: Double?, threshold: Double?,
        reviewThreshold: Double?, comparison: PlacementComparison?
    ) -> Bool {
        guard outcome == .unsure, let measured, let threshold, let reviewThreshold, let comparison else { return false }
        let error = max(plusMinus ?? 0, 0)
        switch comparison {
        case .atMost: return reviewThreshold < threshold && !(measured + error < reviewThreshold)
        case .atLeast: return reviewThreshold > threshold && !(measured - error > reviewThreshold)
        }
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

    /// `ResultReading.reviewBandApplies` on this check's values as the server sent them, in feet.
    /// Read it here, before any conversion: a run of 14.5 ft ± 6 in reaches the 15 ft line
    /// exactly, and the same values narrowed to Float meters fell just short of it.
    public var reviewBandApplies: Bool {
        ResultReading.reviewBandApplies(
            outcome: outcome, measured: measuredFt, plusMinus: plusMinusFt, threshold: thresholdFt,
            reviewThreshold: reviewThresholdFt, comparison: comparison)
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
                viewCapturable: evidenceIndex(settling: check.id).map(capturable) ?? false
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
