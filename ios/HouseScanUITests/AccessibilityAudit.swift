import XCTest

/// Which accessibility audit issue counts as the same one in two audit passes. Both audit loops
/// (FullFlowUITests and ScreenStatesUITests) fail only on issues found in more than one pass, so
/// the key decides whether a persistent failure is kept or discarded.
///
/// An element with an identifier is keyed by it alone, not by its label: live labels change
/// between passes ("12 photos taken" becomes "13 photos taken" on the photo count), and a label
/// in the key made one failure on one element look like two different issues, so it was dropped.
/// Elements that share an identifier (every feature row's delete button) share a key; an issue on
/// one row in the first pass and another row in the second then counts as repeated, which can
/// only fail the test, never hide a failure.
///
/// An element without an identifier has nothing stable but its label, so that is the fallback.
/// Its frame is not: ScreenStatesUITests scrolls between the passes. An unidentified element whose
/// label changes between passes can still escape the repeat check, so anything on screen whose
/// text changes live should carry an identifier. An issue with no element is keyed by its type.
enum AuditIssueKey {
    static func key(auditType: UInt64, identifier: String?, label: String?) -> String {
        if let identifier, !identifier.isEmpty { return "\(auditType)|id:\(identifier)" }
        if let label, !label.isEmpty { return "\(auditType)|label:\(label)" }
        return "\(auditType)|no element"
    }

    /// Every key read in any pass, for report-only audits of moving screens.
    static func observed<Value>(in passes: [[String: Value]]) -> [String] {
        Set(passes.flatMap { $0.keys }).sorted()
    }

    static func transient<Value>(in passes: [[String: Value]]) -> [String] {
        let persistent = Set(repeated(in: passes))
        return observed(in: passes).filter { !persistent.contains($0) }
    }

    /// The keys found in at least two of the passes, sorted.
    static func repeated<Value>(in passes: [[String: Value]]) -> [String] {
        var counts: [String: Int] = [:]
        for pass in passes {
            for key in pass.keys { counts[key, default: 0] += 1 }
        }
        return counts.filter { $0.value >= 2 }.keys.sorted()
    }
}

/// Reads an element once, from a snapshot. Every property of an `XCUIElement` (`identifier`,
/// `label`, `value`, `frame`) resolves it live, and one that has gone by then records "Failed to
/// get matching snapshot" as a test failure that no `do`/`catch` or retry can catch (PR run
/// 36288432201, an audit issue's element on the walk). `snapshot()` throws instead, and the
/// snapshot's properties are the values it read, so they can't fail afterwards.
@MainActor
enum ElementRead {
    static func snapshot(_ element: XCUIElement) -> (any XCUIElementSnapshot)? {
        try? element.snapshot()
    }

    /// Polls `element` until its value satisfies `accept`, and returns that value; nil on timeout.
    /// A missing element counts as not yet, where a predicate expectation on `value` would read
    /// it live.
    static func waitForValue(of element: XCUIElement, timeout: TimeInterval, _ accept: (String) -> Bool) -> String? {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            if let value = snapshot(element)?.value as? String, accept(value) { return value }
            Thread.sleep(forTimeInterval: 0.25)
        } while Date() < deadline
        return nil
    }
}

/// The accessibility audit both test classes run.
@MainActor
enum AccessibilityAudit {
    struct Finding {
        var message: String
        /// Where the element was when the audit read it.
        var frame: CGRect?
    }

    /// One audit pass: issues keyed by `AuditIssueKey`, and the issues whose element had gone
    /// before it could be read, which have no identity to key.
    struct Pass {
        var findings: [String: Finding] = [:]
        var unread: [String] = []
    }

    struct Outcome {
        /// Found and read in at least two passes: these fail the test.
        var persistent: [(key: String, finding: Finding)]
        /// Read in one pass only. Moving-screen reports retain these transition findings.
        var transient: [(key: String, finding: Finding)] = []
        /// Issues whose element had gone before it could be read, from every pass: attached to
        /// the result, never failed on, since nothing says which element they were.
        var unread: [String]
    }

    /// One pass. A failed snapshot of the whole tree (it changed while the audit read it) throws
    /// from `performAccessibilityAudit`; that is retried once, and a second failure throws.
    static func pass(_ app: XCUIApplication) throws -> Pass {
        func run() throws -> Pass {
            var pass = Pass()
            try app.performAccessibilityAudit { issue in
                let description = "\(issue.compactDescription) - \(issue.detailedDescription)"
                let type = issue.auditType.rawValue
                guard let element = issue.element else {
                    pass.findings[AuditIssueKey.key(auditType: type, identifier: nil, label: nil)] = Finding(message: "\(description) (no element)")
                    return true
                }
                guard let read = ElementRead.snapshot(element) else {
                    pass.unread.append(description)
                    return true
                }
                pass.findings[AuditIssueKey.key(auditType: type, identifier: read.identifier, label: read.label)] = Finding(
                    message: "\(description) (id '\(read.identifier)' label '\(read.label)')", frame: read.frame)
                return true
            }
            return pass
        }
        do {
            return try run()
        } catch {
            Thread.sleep(forTimeInterval: 1)
            return try run()
        }
    }

    /// Audits the screen and returns the issues that persist. A clean first pass ends it.
    /// Otherwise a second pass follows `between` (a wait long enough for a system banner to
    /// leave, a scroll), and a third only when an element had gone before it could be read in
    /// either: that issue may have been a lasting one on an element the screen was rebuilding, and
    /// the third pass gives it the second sighting it needs. Never more than three passes.
    static func run(_ app: XCUIApplication, between: (Pass) -> Void) throws -> Outcome {
        let first = try pass(app)
        guard !first.findings.isEmpty || !first.unread.isEmpty else { return Outcome(persistent: [], unread: []) }
        between(first)
        var passes = [first, try pass(app)]
        if passes.contains(where: { !$0.unread.isEmpty }) {
            Thread.sleep(forTimeInterval: 1)
            passes.append(try pass(app))
        }
        return outcome(from: passes)
    }

    static func outcome(from passes: [Pass]) -> Outcome {
        let newestFirst = passes.reversed()
        func findings(for keys: [String]) -> [(key: String, finding: Finding)] {
            keys.compactMap { key in
                newestFirst.lazy.compactMap { $0.findings[key] }.first.map { (key: key, finding: $0) }
            }
        }
        let keyedPasses = passes.map(\.findings)
        return Outcome(
            persistent: findings(for: AuditIssueKey.repeated(in: keyedPasses)),
            transient: findings(for: AuditIssueKey.transient(in: keyedPasses)),
            unread: passes.flatMap(\.unread))
    }
}

/// Runs in the UI test bundle without launching the app, except where a test says so.
final class AuditIssueKeyTests: XCTestCase {
    private let contrast = XCUIAccessibilityAuditType.contrast.rawValue

    func testALabelChangeCannotClearARepeatedIssue() {
        let first = [AuditIssueKey.key(auditType: contrast, identifier: "photoCount", label: "12 photos taken"): "pass 1"]
        let second = [AuditIssueKey.key(auditType: contrast, identifier: "photoCount", label: "13 photos taken"): "pass 2"]
        XCTAssertEqual(AuditIssueKey.repeated(in: [first, second]).count, 1, "the same element failing twice must count as repeated")
    }

    func testDifferentChecksOnOneElementStayApart() {
        let contrastKey = AuditIssueKey.key(auditType: contrast, identifier: "photoCount", label: "12 photos taken")
        let clipped = AuditIssueKey.key(
            auditType: XCUIAccessibilityAuditType.textClipped.rawValue, identifier: "photoCount", label: "12 photos taken")
        XCTAssertNotEqual(contrastKey, clipped)
    }

    func testElementsWithoutAnIdentifierFallBackToTheirLabel() {
        XCTAssertEqual(
            AuditIssueKey.key(auditType: contrast, identifier: "", label: "Done"),
            AuditIssueKey.key(auditType: contrast, identifier: nil, label: "Done"))
        XCTAssertNotEqual(
            AuditIssueKey.key(auditType: contrast, identifier: "", label: "Done"),
            AuditIssueKey.key(auditType: contrast, identifier: "", label: "Next"))
        // An identifier never collides with a label that happens to read the same.
        XCTAssertNotEqual(
            AuditIssueKey.key(auditType: contrast, identifier: "Done", label: nil),
            AuditIssueKey.key(auditType: contrast, identifier: nil, label: "Done"))
        XCTAssertEqual(AuditIssueKey.key(auditType: contrast, identifier: nil, label: nil), "\(contrast)|no element")
    }

    func testMovingReportsRetainAnIssueThatDisappearsAfterTheFirstPass() {
        let passes = [["contrast|id:instruction": 1], [:], ["contrast|id:photoCount": 1]]
        XCTAssertEqual(AuditIssueKey.observed(in: passes), ["contrast|id:instruction", "contrast|id:photoCount"])
        XCTAssertTrue(AuditIssueKey.repeated(in: passes).isEmpty)
        XCTAssertEqual(AuditIssueKey.transient(in: passes), ["contrast|id:instruction", "contrast|id:photoCount"])
        let repeated = passes + [["contrast|id:instruction": 1]]
        XCTAssertEqual(AuditIssueKey.transient(in: repeated), ["contrast|id:photoCount"])
    }

    @MainActor
    func testFirstPassOnlyFindingRetainsItsMessage() {
        let key = AuditIssueKey.key(auditType: contrast, identifier: "instruction", label: "Slow down")
        let outcome = AccessibilityAudit.outcome(from: [
            .init(findings: [key: .init(message: "pass 1")]),
            .init()
        ])
        XCTAssertTrue(outcome.persistent.isEmpty)
        XCTAssertEqual(outcome.transient.map(\.key), [key])
        XCTAssertEqual(outcome.transient.first?.finding.message, "pass 1")
    }

    @MainActor
    func testRepeatedFindingKeepsOnlyTheNewestMessage() {
        let key = AuditIssueKey.key(auditType: contrast, identifier: "instruction", label: "Slow down")
        let outcome = AccessibilityAudit.outcome(from: [
            .init(findings: [key: .init(message: "pass 1")]),
            .init(findings: [key: .init(message: "pass 2")])
        ])
        XCTAssertEqual(outcome.persistent.map(\.key), [key])
        XCTAssertEqual(outcome.persistent.first?.finding.message, "pass 2")
        XCTAssertTrue(outcome.transient.isEmpty)
    }

    func testAnIssueFoundOnlyOnceIsNotRepeated() {
        let first = [AuditIssueKey.key(auditType: contrast, identifier: "instruction", label: "Slow down"): 1]
        let second = [AuditIssueKey.key(auditType: contrast, identifier: "photoCount", label: "3 photos taken"): 1]
        XCTAssertTrue(AuditIssueKey.repeated(in: [first, second]).isEmpty)
    }

    /// The third pass: an issue missed in the middle pass (its element was being rebuilt) still
    /// counts once the first and third find it, and one seen once in three still doesn't.
    func testTwoSightingsInThreePassesCount() {
        let key = AuditIssueKey.key(auditType: contrast, identifier: "instruction", label: "Walk slowly")
        let other = AuditIssueKey.key(auditType: contrast, identifier: "photoCount", label: "3 photos taken")
        XCTAssertEqual(AuditIssueKey.repeated(in: [[key: 1], [:], [key: 1, other: 1]]), [key])
    }

    /// PR run 36288432201's failure, made deterministic: the answer the audit would have flagged
    /// is gone once it is tapped. Reading it the old way (a property straight off the element)
    /// records a failure; `ElementRead` reads nothing and records nothing. This test fails if
    /// either stops being true.
    @MainActor
    func testAGoneElementIsReadWithoutRecordingAFailure() {
        let app = XCUIApplication()
        app.launchArguments = ["-uiDemo", "-uiDemoFreeze", "-uiDemoGroundQuestion"]
        app.launch()
        defer { app.terminate() }
        let gravel = app.buttons["ground.answer.gravel"]
        XCTAssertTrue(gravel.waitForExistence(timeout: 15))
        XCTAssertEqual(ElementRead.snapshot(gravel)?.identifier, "ground.answer.gravel", "a present element reads normally")
        gravel.tap()
        XCTAssertTrue(app.buttons["ground.change"].waitForExistence(timeout: 5))
        XCTAssertTrue(gravel.waitForNonExistence(timeout: 5))

        XCTAssertNil(ElementRead.snapshot(gravel), "a gone element has no snapshot")
        XCTAssertNil(ElementRead.waitForValue(of: gravel, timeout: 0.5) { _ in true })
        XCTExpectFailure("reading a property off a gone element records a failure: the old audit's read") {
            _ = gravel.identifier
        }
    }
}
