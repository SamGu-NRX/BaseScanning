import Foundation
import HouseScanKit
import Testing
import simd

@Suite struct ResultReadingTests {
    /// The app's offline sample as a JSON object, to edit before decoding.
    private static func sampleObject() throws -> [String: Any] {
        try #require(try JSONSerialization.jsonObject(with: PlacementResultTests.sampleData()) as? [String: Any])
    }

    /// A real answer from the hosted server under the demo policy (see PlacementResultTests).
    private static func serverAnswer() throws -> PlacementResult {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Schemas/server-answer-synthetic-wall.json")
        return try PlacementResult.decode(Data(contentsOf: url))
    }

    /// Encodes `object`, checks it against the result schema and decodes it.
    private static func decode(_ object: [String: Any]) throws -> PlacementResult {
        let data = try JSONSerialization.data(withJSONObject: object)
        #expect(try SceneSchemas.result().validate(data) == [])
        return try PlacementResult.decode(data)
    }

    private static func check(_ id: String, _ outcome: String, measured: Double? = nil, threshold: Double? = nil) -> [String: Any] {
        [
            "id": id, "label": "Made-up check \(id)", "outcome": outcome, "reason": "Made up for a test.",
            "measured_ft": measured ?? NSNull(), "plus_minus_ft": measured == nil ? NSNull() : 0.3,
            "threshold_ft": threshold ?? NSNull(), "comparison": threshold == nil ? NSNull() : "at_least",
            "rule": ["key": "made_up_\(id)_ft", "source": "Made up for a test", "placeholder": true],
        ] as [String: Any]
    }

    /// The sample with one more check at its spot.
    private static func sample(adding extra: [String: Any]) throws -> PlacementResult {
        var object = try Self.sampleObject()
        object["checks"] = [extra] + (try #require(object["checks"] as? [[String: Any]]))
        return try decode(object)
    }

    // MARK: A. The nearest rejected spot

    @Test func rejectNamesTheNearestSpotAndTheCheckItFails() throws {
        var object = try Self.sampleObject()
        var nearest = try #require(object["spot"] as? [String: Any])
        nearest["outcome"] = "fail"
        nearest["span_ft"] = [-5.3, -2.7]
        object["decision"] = "reject"
        object["spot"] = NSNull()
        object["route"] = NSNull()
        object["nearest_considered"] = nearest
        object["missing_evidence"] = [Any]()
        object["checks"] = [
            Self.check("wall_backing", "pass"),
            Self.check("gas_clearance", "fail", measured: 2.33, threshold: 3),
            Self.check("route_length", "fail", measured: 24, threshold: 20),
        ]
        let result = try Self.decode(object)

        #expect(result.nearestConsidered?.spanFt == SIMD2(-5.3, -2.7))
        let failure = try #require(result.nearestFailure)
        #expect(failure.id == "gas_clearance" && failure.measuredFt == 2.33 && failure.thresholdFt == 3)
        #expect(result.answer { _ in true } == .notHere)
    }

    /// The outline uses the server's size for the nearest spot, not a battery size of the app's.
    @Test func theNearestSpotCarriesItsOwnSize() throws {
        var object = try Self.sampleObject()
        var nearest = try #require(object["spot"] as? [String: Any])
        nearest["width_ft"] = 2.6
        nearest["depth_ft"] = 1.8
        nearest["height_ft"] = 3.3
        object["spot"] = NSNull()
        object["route"] = NSNull()
        object["nearest_considered"] = nearest
        let spot = try #require(try Self.decode(object).nearestConsidered)
        #expect(spot.widthFt == 2.6 && spot.depthFt == 1.8 && spot.heightFt == 3.3)
    }

    /// No default size stands in for a missing one: the answer is refused, naming the key.
    @Test(arguments: ["width_ft", "depth_ft", "height_ft", "span_ft"])
    func aNearestSpotWithoutADimensionIsRefused(key: String) throws {
        var object = try Self.sampleObject()
        var nearest = try #require(object["spot"] as? [String: Any])
        nearest[key] = nil
        object["spot"] = NSNull()
        object["route"] = NSNull()
        object["nearest_considered"] = nearest
        let data = try JSONSerialization.data(withJSONObject: object)
        #expect(throws: PlacementDecodingError.missingKey(path: "nearest_considered.\(key)")) { try PlacementResult.decode(data) }
    }

    @Test func aSpotHasNoNearestFailure() throws {
        let result = try PlacementResult.decode(PlacementResultTests.sampleData())
        #expect(result.spot != nil && result.nearestFailure == nil)
    }

    // MARK: B. No clean spot

    @Test(arguments: ["unsure", "fail"])
    func aSpotThatMightStandInTheWorkingSpaceGoesToAnInstaller(outcome: String) throws {
        let result = try Self.sample(adding: Self.check(ResultReading.meterWorkingSpaceCheckID, outcome, measured: -0.5, threshold: 0))
        #expect(!result.spotIsClean)
        // The sample also has an unsure check a capturable view settles; the spot still goes to a person.
        #expect(result.answer { _ in true } == .installer)
    }

    @Test func aSpotClearOfTheWorkingSpaceIsClean() throws {
        let result = try Self.sample(adding: Self.check(ResultReading.meterWorkingSpaceCheckID, "pass", measured: 1.2, threshold: 0))
        #expect(result.spotIsClean)
        #expect(result.answer { _ in true } == .oneMoreLook)
    }

    /// A manual_review held back only by unapproved rules, with every check passing, is a
    /// candidate like a pass: the rules' approval was never what made a spot a confirmed fit.
    @Test func allPassUnderUnapprovedRulesIsACandidate() throws {
        var object = try Self.sampleObject()
        object["checks"] = [
            Self.check("wall_backing", "pass"),
            Self.check(ResultReading.meterWorkingSpaceCheckID, "pass", measured: 1.2, threshold: 0),
        ]
        object["missing_evidence"] = [Any]()
        let result = try Self.decode(object)
        #expect(result.decision == .manualReview && !result.policy.autoApprove && result.spot != nil)
        #expect(result.spotIsClean)
        #expect(result.answer { _ in true } == .candidate)
    }

    /// The same answer with `checks: []`, which the schema allows: no evidence is not a fit.
    @Test func aSpotWithNoChecksGoesToAnInstaller() throws {
        var object = try Self.sampleObject()
        object["checks"] = [Any]()
        object["missing_evidence"] = [Any]()
        let result = try Self.decode(object)
        #expect(result.decision == .manualReview && !result.policy.autoApprove && result.spot != nil && result.checks.isEmpty)
        #expect(!result.spotIsClean)
        #expect(result.answer { _ in true } == .installer)
    }

    @Test func checksThisAppDoesNotKnowNeverCountAgainstTheSpot() throws {
        let result = try Self.sample(adding: Self.check("some_future_check", "fail", measured: 1, threshold: 3))
        #expect(result.spotIsClean)
    }

    @Test func noSpotIsNeverClean() throws {
        #expect(!ResultReading.spotIsClean(hasSpot: false, checks: []))
    }

    /// The hosted server's answer to the synthetic wall: the working-space check is unsure.
    @Test func theServerAnswerSpotIsNotClean() throws {
        let result = try Self.serverAnswer()
        #expect(result.decision == .manualReview && result.spot != nil)
        #expect(!result.spotIsClean)
        #expect(result.answer { _ in true } == .installer)
    }

    // MARK: C. Answer first

    @Test func theNoticeComesOffTheSummary() throws {
        let result = try Self.serverAnswer()
        let notice = try #require(result.policy.notice)
        #expect(notice.hasPrefix("Demo rules:"))
        #expect(result.summary.hasSuffix(notice))
        #expect(result.summaryWithoutNotice == "More views are needed around the best spot, 1 ft 6 in right of the meter: 7 checks depend on areas the scan did not see.")
    }

    @Test func aSummaryWithoutTheNoticeStaysAsItIs() throws {
        let result = try PlacementResult.decode(PlacementResultTests.sampleData())
        #expect(result.policy.notice == nil)
        #expect(result.summaryWithoutNotice == result.summary)
    }

    @Test func theNoticeSurvivesEncoding() throws {
        let result = try Self.serverAnswer()
        let encoded = try JSONEncoder().encode(result)
        #expect(try SceneSchemas.result().validate(encoded) == [])
        #expect(try PlacementResult.decode(encoded).policy.notice == result.policy.notice)
    }

    @Test func theRulesHashIsItsFirstEightCharacters() throws {
        #expect(try Self.serverAnswer().policy.rulesShortHash == "2f52ec35")
    }

    // MARK: D. Each unsure check linked to its view

    @Test func aViewLinksEveryCheckItSettles() throws {
        var object = try Self.sampleObject()
        var missing = try #require(object["missing_evidence"] as? [[String: Any]])
        missing[0]["checks"] = ["front_clearance", "window_clearance"]
        object["missing_evidence"] = missing
        let result = try Self.decode(object)

        #expect(result.evidenceIndex(settling: "front_clearance") == 0)
        #expect(result.evidenceIndex(settling: "window_clearance") == 0)
        #expect(result.evidenceIndex(settling: "gas_clearance") == nil)
    }

    @Test func theServerAnswerLinksItsFiveChecksToTheFirstView() throws {
        let result = try Self.serverAnswer()
        for id in ["ac_clearance", "drive_clearance", "gas_clearance", "opening_clearance", "pool_clearance"] {
            #expect(result.evidenceIndex(settling: id) == 0, "\(id)")
        }
        #expect(result.evidenceIndex(settling: "wall_backing") == nil)
    }

    // MARK: The UI tests' result files

    /// ios/HouseScanUITests/Fixtures/results, the answers the screenshots and UI tests show
    /// through `-uiDemoResultFile`. Each must stay in the schema and read as its name says.
    @Test(arguments: [
        ("pass", ResultReading.Answer.candidate), ("reject-nearest", .notHere),
        ("unsure-view", .oneMoreLook), ("no-clean-spot", .installer),
        ("no-spot-no-nearest", .notHere), ("review-band", .installer), ("view-not-offered", .oneMoreLook),
    ])
    func uiResultFilesReadAsNamed(file: String, answer: ResultReading.Answer) throws {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // HouseScanKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // HouseScanKit
            .deletingLastPathComponent()  // ios
            .appendingPathComponent("HouseScanUITests/Fixtures/results/\(file).json")
        let data = try Data(contentsOf: url)
        #expect(try SceneSchemas.result().validate(data) == [])
        #expect(try PlacementResult.decode(data).answer { _ in true } == answer)
    }

    // MARK: The answer

    @Test func theAnswerReadsTheChecks() {
        typealias Check = ResultReading.Check
        let pass = Check(id: "a", outcome: .pass)
        let byView = Check(id: "b", outcome: .unsure, needsPerson: false, viewCapturable: true)
        let skippedView = Check(id: "c", outcome: .unsure, needsPerson: false, viewCapturable: false)
        let byPerson = Check(id: "d", outcome: .unsure, needsPerson: true)
        let answer = { (decision: PlacementDecision, approved: Bool, spot: Bool, checks: [Check]) in
            ResultReading.answer(decision: decision, policyApproved: approved, hasSpot: spot, checks: checks)
        }

        #expect(answer(.pass, true, true, [pass]) == .candidate)
        // Only the rules' approval holds it back.
        #expect(answer(.manualReview, false, true, [pass]) == .candidate)
        #expect(answer(.manualReview, false, false, [pass]) == .installer)
        #expect(answer(.manualReview, true, true, [pass, byView, byPerson]) == .oneMoreLook)
        #expect(answer(.manualReview, true, true, [pass, byPerson]) == .installer)
        // A view the homeowner already couldn't take settles nothing.
        #expect(answer(.manualReview, true, true, [pass, skippedView]) == .installer)
        #expect(answer(.reject, true, false, [Check(id: "e", outcome: .fail)]) == .notHere)
    }

    @Test func needsPersonFollowsTheUnsureCause() throws {
        let result = try PlacementResult.decode(PlacementResultTests.sampleData())
        #expect(result.checks.map(\.needsPerson) == [false, true, false, false])  // pass, margin, unobserved, pass
        var unexplained = result.checks[2]
        unexplained.unsureCause = nil
        #expect(unexplained.needsPerson)
    }

    // MARK: No confirmed fit (B17)

    /// The reading has no answer that says a battery fits: a passing spot rests on space the scan
    /// can't show was confirmed, so the most it can be is a candidate.
    @Test func noAnswerSaysABatteryFits() {
        #expect(ResultReading.Answer(rawValue: "fits") == nil)
        typealias Check = ResultReading.Check
        let outcomes: [PlacementOutcome] = [.pass, .unsure, .fail]
        // Each check set once with a check this app doesn't know, so a new server check can't
        // open a shortcut to a stronger answer.
        var sets: [[Check]] = [[]]
        for id in ["wall_backing", "a_check_this_app_does_not_know", ResultReading.meterWorkingSpaceCheckID] {
            sets = sets.flatMap { set in [set] + outcomes.map { set + [Check(id: id, outcome: $0, viewCapturable: true)] } }
        }
        for decision in [PlacementDecision.pass, .manualReview, .reject] {
            for approved in [true, false] {
                for hasSpot in [true, false] {
                    for checks in sets {
                        let answer = ResultReading.answer(decision: decision, policyApproved: approved, hasSpot: hasSpot, checks: checks)
                        let described = "\(decision) approved=\(approved) spot=\(hasSpot) checks=\(checks.map { "\($0.id)=\($0.outcome)" })"
                        // A reject without a spot stays negative whatever its checks say.
                        if decision == .reject, !hasSpot { #expect(answer == .notHere, "\(described)") }
                        guard answer == .candidate else { continue }
                        // A candidate has a spot and at least one check, every one passing.
                        #expect(hasSpot && !checks.isEmpty && checks.allSatisfy { $0.outcome == .pass } && decision != .reject,
                                "\(described)")
                    }
                }
            }
        }
    }

    /// A pass that contradicts itself (no spot, no checks, or a check that didn't pass) goes to a
    /// person rather than reading as a candidate, and its checks stay as sent.
    @Test func anInconsistentPassGoesToAnInstaller() {
        typealias Check = ResultReading.Check
        let answer = { (spot: Bool, checks: [Check]) in
            ResultReading.answer(decision: .pass, policyApproved: true, hasSpot: spot, checks: checks)
        }
        #expect(answer(false, []) == .installer)
        #expect(answer(true, []) == .installer)
        #expect(answer(false, [Check(id: "a", outcome: .pass)]) == .installer)
        #expect(answer(true, [Check(id: "a", outcome: .pass), Check(id: "b", outcome: .unsure, viewCapturable: true)]) == .installer)
        #expect(answer(true, [Check(id: "a", outcome: .pass), Check(id: "b", outcome: .fail)]) == .installer)
        #expect(answer(true, [Check(id: "a", outcome: .pass)]) == .candidate)
    }

    /// An installer's review can still carry a spot, and the reading keeps it: only the words
    /// over it change.
    @Test func anInstallerReviewKeepsItsSpot() throws {
        let result = try Self.sample(adding: Self.check(ResultReading.meterWorkingSpaceCheckID, "unsure", measured: -0.5, threshold: 0))
        #expect(result.answer { _ in true } == .installer)
        #expect(result.spot != nil)
    }

    /// Reading an answer changes nothing in it. A passing answer bound to the scene sent stays
    /// bound, decodes to the same result before and after it is read, and keeps the server's
    /// decision and every check's outcome: the candidate is how the app words it, not a new
    /// answer.
    @Test func readingAPassLeavesTheAnswerAsTheServerSentIt() throws {
        let scene = Data(#"{"note":"the bytes this phone sent"}"#.utf8)
        var object = try #require(try JSONSerialization.jsonObject(with: ResultBindingTests.answer(declaring: PacketFiles.sha256(scene))) as? [String: Any])
        object["decision"] = "pass"
        object["checks"] = try #require(object["checks"] as? [[String: Any]]).map { check in
            var check = check
            check["outcome"] = "pass"
            check.removeValue(forKey: "unsure_cause")
            return check
        }
        object["missing_evidence"] = [Any]()
        let data = try JSONSerialization.data(withJSONObject: object)
        #expect(try SceneSchemas.result().validate(data) == [])
        try ResultBinding.check(answer: data, submittedScene: scene)

        let result = try PlacementResult.decode(data)
        let sent = data
        #expect(result.answer { _ in true } == .candidate)
        #expect(ResultReading.cardLines(result.readingChecks { _ in true }).isEmpty)
        #expect(data == sent)
        #expect(try PlacementResult.decode(data) == result)
        #expect(result.decision == .pass)
        #expect(result.checks.allSatisfy { $0.outcome == .pass })
        try ResultBinding.check(answer: data, submittedScene: scene)
    }

    // MARK: The card's check lines

    /// The card leads with what failed and what is unsure. A passing check is never on it: the
    /// card read a pass as space confirmed clear, which a candidate can't claim (B17). Every check
    /// stays in Details.
    @Test func cardLinesPutFailuresFirstThenUnsureAndNoPasses() {
        typealias Check = ResultReading.Check
        let checks = [
            Check(id: "pass", outcome: .pass),
            Check(id: "unsure", outcome: .unsure),
            Check(id: "fail", outcome: .fail),
            Check(id: "unsure2", outcome: .unsure),
            Check(id: "fail2", outcome: .fail),
        ]
        #expect(ResultReading.cardLines(checks) == [2, 4, 1])
        #expect(ResultReading.cardLines(checks, limit: 5) == [2, 4, 1, 3])
        #expect(ResultReading.cardLines(checks.filter { $0.outcome == .pass }).isEmpty)
    }
}
