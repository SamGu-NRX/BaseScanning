import XCTest

/// A request the finished check sent back ("One more view to finish"), read from the production
/// layout in demo mode. At the largest text size the card folds (`GapRequestScreen.followUpFolds`):
/// the side to aim at leads, the follow-up, the stretch and the check's words are under Details,
/// "I can't get there" sits among the actions, wholly on screen and usable once its lock passes,
/// and the camera between them stays open. At the default size nothing changes.
final class GapFollowUpFoldUITests: XCTestCase {
    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
    private static let followUp = ["-uiDemo", "-uiDemoFreeze", "-practiceMeter", "NO", "-uiDemoPhase", "gapRequest", "-uiDemoFollowUp"]
    /// `CameraChromeLayout.minCameraWindow`: room for the largest aim ring (64 pt radius, 108
    /// percent at its pulse) with a margin, the minimum every folded aiming card keeps.
    private static let minCameraWindow: CGFloat = 180
    /// `InstructionCard`'s `replyLock` (600 ms), with room for a slow runner.
    private static let lockWait: TimeInterval = 3

    override func setUp() {
        continueAfterFailure = false
    }

    @MainActor
    private func launch(_ extra: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = Self.followUp + extra
        app.launch()
        XCTAssertTrue(app.descendants(matching: .any)["screen.gapRequest"].waitForExistence(timeout: 15))
        return app
    }

    @MainActor
    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    @MainActor
    private func waitUntilEnabled(_ element: XCUIElement) -> Bool {
        let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true"), object: element)
        return XCTWaiter().wait(for: [enabled], timeout: Self.lockWait) == .completed
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// At the default size the card keeps every word and its reply, as before: the follow-up over
    /// the stretch's two ends, no Details, and "I can't get there" as the card's trailing pill,
    /// under the words, enabled once its lock passes.
    @MainActor
    func testAtTheDefaultSizeTheCardKeepsItsWordsAndReply() throws {
        let app = launch([])
        defer { app.terminate() }
        let window = app.windows.firstMatch.frame
        let card = element(app, "instruction")
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        XCTAssertTrue(card.label.contains("One more view to finish"), card.label)
        XCTAssertTrue(card.label.contains("4 ft to 7 ft right of your meter"), card.label)
        XCTAssertFalse(element(app, "instruction.details").exists, "the default size folded the card")
        let reply = app.buttons["action.skipGap"]
        XCTAssertTrue(reply.waitForExistence(timeout: 5))
        XCTAssertTrue(waitUntilEnabled(reply), "I can't get there stayed locked")
        XCTAssertTrue(reply.isHittable)
        XCTAssertTrue(window.contains(reply.frame), "I can't get there \(reply.frame) isn't wholly on screen")
        XCTAssertGreaterThanOrEqual(reply.frame.minY, card.frame.maxY, "the reply must sit under the card's words")
        // The card's pill hugs its words at the card's trailing edge; the moved reply is full width.
        XCTAssertLessThan(reply.frame.width, window.width * 0.7, "the reply \(reply.frame) left the card")
        XCTAssertGreaterThan(reply.frame.midX, window.midX, "the reply \(reply.frame) isn't the card's trailing pill")
        XCTAssertTrue(element(app, "action.showResult").exists)
        attach(app, "gapRequest-followUp-default")
    }

    /// At the largest text size the card folds: a short lead and Details, "I can't get there"
    /// wholly on screen below the card and enabled once its lock passes, and at least
    /// `minCameraWindow` of camera between them.
    @MainActor
    func testAtTheLargestSizeTheCardFoldsAndTheReplyStaysOnScreen() throws {
        let app = launch(Self.largestText)
        defer { app.terminate() }
        let window = app.windows.firstMatch.frame
        let card = element(app, "instruction")
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        XCTAssertTrue(card.label.contains("Show the ground to the right"), card.label)
        XCTAssertFalse(card.label.contains("One more view to finish"), "the follow-up should be under Details: \(card.label)")
        XCTAssertFalse(card.label.contains("4 ft to 7 ft"), "the stretch should be under Details: \(card.label)")
        let details = element(app, "instruction.details")
        XCTAssertTrue(details.exists, "the folded card has no Details")
        // The card's scrim ends 8 pt under Details (`assertEndCircleOpen`).
        let cardBottom = max(card.frame.maxY, details.frame.maxY) + 8
        let reply = app.buttons["action.skipGap"]
        XCTAssertTrue(reply.waitForExistence(timeout: 5))
        XCTAssertTrue(waitUntilEnabled(reply), "I can't get there stayed locked")
        XCTAssertTrue(reply.isHittable, "I can't get there can't be tapped where it is")
        XCTAssertTrue(window.contains(reply.frame), "I can't get there \(reply.frame) isn't wholly on screen in \(window)")
        XCTAssertGreaterThanOrEqual(reply.frame.minY, cardBottom, "I can't get there \(reply.frame) is under the card, which ends at \(cardBottom)")
        XCTAssertGreaterThanOrEqual(
            reply.frame.minY - cardBottom, Self.minCameraWindow, "only \(reply.frame.minY - cardBottom) pt of camera between the card and the actions")
        XCTAssertEqual(reply.label, "I can't get there")
        attach(app, "gapRequest-followUp-AX5-folded")
    }

    /// Details holds every word the unfolded card had: the follow-up, the stretch's two ends and
    /// the check's own words. "I can't get there" stays reachable below the open card.
    @MainActor
    func testAtTheLargestSizeDetailsHoldsEveryWord() throws {
        let app = launch(Self.largestText)
        defer { app.terminate() }
        let details = element(app, "instruction.details")
        XCTAssertTrue(details.waitForExistence(timeout: 5))
        details.tap()
        let words = element(app, "instruction.detail")
        XCTAssertTrue(words.waitForExistence(timeout: 5), "Details didn't open")
        for text in ["One more view to finish", "Show the ground from 4 ft to 7 ft right of your meter", "A second look at the ground"] {
            XCTAssertTrue(words.label.contains(text), "Details lacks \"\(text)\": \(words.label)")
        }
        attach(app, "gapRequest-followUp-AX5-details")
        let reply = app.buttons["action.skipGap"]
        XCTAssertTrue(reply.exists)
        for _ in 0..<6 where !reply.isHittable { app.swipeUp(velocity: .slow) }
        XCTAssertTrue(reply.isHittable, "I can't get there can't be reached with Details open")
        XCTAssertTrue(app.windows.firstMatch.frame.contains(reply.frame))
        XCTAssertGreaterThanOrEqual(reply.frame.minY, words.frame.maxY, "I can't get there is under the open card")
        attach(app, "gapRequest-followUp-AX5-details-reply")
    }
}
