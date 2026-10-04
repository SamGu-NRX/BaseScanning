import XCTest

/// Every step of the walk that asks something has a true answer, and a card that refuses says
/// what to do. Held still on the demo engine (`-uiDemoFreeze`), which mirrors the real engine's
/// choices; the real engine's own handling of each answer is covered by review and the replay
/// flows.
final class WalkRecoveryUITests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    /// B-06: "Is this the right end of the wall?" had one answer, "Wall ends here", so a wall that
    /// goes on had none. "The wall keeps going" ends the side where the walk reached without
    /// asking what is there, and the walk goes on to the other side.
    @MainActor
    func testTheEndCardAnswersThatTheWallKeepsGoing() throws {
        let app = launch(["-uiDemoPhase", "wallWalk", "-uiDemoMarkEnd"])
        XCTAssertTrue(label(app, "instruction").contains("Is this the right end of the wall?"))
        assertEndCircleOpen(app, folded: false, reply: "action.cannotAccess", covers: Self.endCovers, "walk mark-end")
        snap(app, "wallWalk-markEnd-keepsGoing")
        XCTAssertTrue(element(app, "action.markEnd").exists, "Wall ends here must stay on offer")
        let keepsGoing = reply(app, "The wall keeps going")
        XCTAssertTrue(keepsGoing.waitForExistence(timeout: 5), "the end card has no way to say the wall goes on")
        tapWhenReady(keepsGoing)
        let walkLeft = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == 'instruction' AND label CONTAINS 'to your left'")).firstMatch
        XCTAssertTrue(walkLeft.waitForExistence(timeout: 5), "the walk must go on to the left")
        XCTAssertFalse(element(app, "action.endCorner").exists, "a wall that goes on has no end to ask about")
        XCTAssertFalse(element(app, "action.markEnd").exists)
    }

    /// B-06: "Wall ends here" with the circle off the wall did nothing. The card now says why and
    /// what to do, and both answers stay.
    @MainActor
    func testARefusedWallEndSaysWhatToDo() throws {
        let app = launch(["-uiDemoPhase", "wallWalk", "-uiDemoMarkEnd", "-uiDemoEndMarkRefusal"])
        let card = label(app, "instruction")
        snap(app, "wallWalk-markEnd-refused")
        XCTAssertTrue(card.contains("The circle isn't on the wall"), "card reads: \(card)")
        XCTAssertTrue(card.contains("Aim it at the wall where it stops or turns"), "card reads: \(card)")
        XCTAssertTrue(element(app, "action.markEnd").exists)
        XCTAssertTrue(reply(app, "The wall keeps going").waitForExistence(timeout: 5))
        assertEndCircleOpen(app, folded: false, reply: "action.cannotAccess", covers: Self.endCovers, "walk mark-end refused")
    }

    /// At AX5 the card covered the circle "Wall ends here" marks at (CI run 37165788075,
    /// `wallWalk-markEnd-AX5-stacked`). Folded, it leads with where to aim, every word is under
    /// Details, and "The wall keeps going" moves to the actions under "Wall ends here", so with
    /// "Wall ends here" in reach the circle is in view.
    @MainActor
    func testTheEndCircleStaysOpenAtLargestTextSize() throws {
        let app = launch(["-uiDemoPhase", "wallWalk", "-uiDemoMarkEnd"] + Self.largestText)
        let card = label(app, "instruction")
        XCTAssertTrue(card.contains("Aim at the right end"), "card reads: \(card)")
        XCTAssertFalse(card.contains("Is this the right end of the wall?"), "the question folds under Details: \(card)")
        let mark = element(app, "action.markEnd")
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable, "Wall ends here can't be reached")
        assertEndCircleOpen(app, folded: true, reply: "action.cannotAccess", covers: Self.endCovers, "walk mark-end, AX5")
        snap(app, "wallWalk-markEnd-AX5")

        scrollToTop(app)
        let details = element(app, "instruction.details")
        tapWhenReady(details)
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5), "Details must open the folded words")
        XCTAssertTrue(
            detail.label.contains("Is this the right end of the wall? Aim where it stops or turns a corner and tap Wall ends here. If it goes on, tap The wall keeps going."),
            "Details reads: \(detail.label)")
        snap(app, "wallWalk-markEnd-AX5-details")
        tapWhenReady(details)
        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: detail)
        XCTAssertEqual(XCTWaiter().wait(for: [gone], timeout: 5), .completed, "Details must close again")
        scrollIntoView(mark, in: app)
        assertEndCircleOpen(app, folded: true, reply: "action.cannotAccess", covers: Self.endCovers, "walk mark-end, AX5, Details closed")

        let keepsGoing = reply(app, "The wall keeps going")
        XCTAssertTrue(keepsGoing.waitForExistence(timeout: 5), "the wall that goes on still needs its answer")
        scrollIntoView(keepsGoing, in: app)
        XCTAssertTrue(keepsGoing.isHittable, "The wall keeps going can't be reached")
        XCTAssertGreaterThan(keepsGoing.frame.minY, mark.frame.maxY - 0.5, "the answer sits under Wall ends here")
    }

    /// Folded, a refusal leads with what to do, and keeps the circle in view.
    @MainActor
    func testARefusedWallEndKeepsTheCircleOpenAtLargestTextSize() throws {
        let app = launch(["-uiDemoPhase", "wallWalk", "-uiDemoMarkEnd", "-uiDemoEndMarkRefusal"] + Self.largestText)
        let card = label(app, "instruction")
        XCTAssertTrue(card.contains("Aim at the wall"), "card reads: \(card)")
        let mark = element(app, "action.markEnd")
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable)
        assertEndCircleOpen(app, folded: true, reply: "action.cannotAccess", covers: Self.endCovers, "walk mark-end refused, AX5")
        snap(app, "wallWalk-markEnd-refused-AX5")
    }

    /// The walk's end with coaching riding along: the coaching leads in a few words, its note
    /// opens Details, and the circle stays open.
    @MainActor
    func testCoachingOnTheFoldedEndCardKeepsTheCircleOpen() throws {
        let app = launch(["-uiDemoPhase", "wallWalk", "-uiDemoMarkEnd", "-uiDemoCoaching", "tooDark"] + Self.largestText)
        let card = label(app, "instruction")
        XCTAssertTrue(card.contains("Try your flashlight"), "card reads: \(card)")
        XCTAssertFalse(card.contains("Try your phone's flashlight"), "the coaching's note must fold under Details: \(card)")
        let mark = element(app, "action.markEnd")
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable)
        assertEndCircleOpen(app, folded: true, reply: "action.cannotAccess", covers: Self.endCovers, "coached walk mark-end, AX5")
        snap(app, "wallWalk-markEnd-tooDark-AX5")
    }

    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
    /// What may cover the circle on the walk's end: its actions and the card's reply.
    private static let endCovers = ["action.markEnd", "action.markSomething", "action.cannotAccess"]

    /// Scrolls in measured steps until the whole element is in the window: at the largest text
    /// sizes the actions sit below the card, reached by scrolling.
    @MainActor
    private func scrollIntoView(_ target: XCUIElement, in app: XCUIApplication) {
        let window = app.windows.firstMatch.frame
        let scroll = app.scrollViews.firstMatch
        guard scroll.exists else { return }
        let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
        for _ in 0..<12 where !window.contains(target.frame) {
            let frame = target.frame
            let shift: CGFloat = frame.maxY > window.maxY
                ? -min(frame.maxY - window.maxY + 24, window.height / 3)
                : min(window.minY - frame.minY + 24, window.height / 3)
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: shift)))
        }
    }

    /// Drags the screen down until its top shows.
    @MainActor
    private func scrollToTop(_ app: XCUIApplication) {
        let scroll = app.scrollViews.firstMatch
        guard scroll.exists else { return }
        let start = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
        for _ in 0..<4 {
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: app.windows.firstMatch.frame.height * 0.4)))
        }
    }

    /// B-23: "Point at the meter like this." needs the photo it points to. The follow-up view
    /// shows the close-up as the walk does; without a close-up the card says it plainly.
    @MainActor
    func testLostPlaceSaysLikeThisOnlyWithThePhoto() throws {
        let gap = launch(["-uiDemoPhase", "gapRequest", "-uiDemoCoaching", "relocalizing"], screen: "screen.gapRequest")
        XCTAssertTrue(element(gap, "relocalize.meterPhoto").waitForExistence(timeout: 5), "the follow-up view must show the close-up")
        XCTAssertTrue(label(gap, "instruction").contains("Point at the meter like this."))
        snap(gap, "gapRequest-relocalizing")
        gap.terminate()

        let walk = launch(["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "relocalizing", "-uiDemoCloseUpSkipped"])
        let card = label(walk, "instruction")
        XCTAssertFalse(element(walk, "relocalize.meterPhoto").exists)
        snap(walk, "wallWalk-relocalizing-noCloseUp")
        XCTAssertTrue(card.contains("Point back at your meter"), "card reads: \(card)")
        XCTAssertFalse(card.contains("like this"), "no photo, so no \"like this\": \(card)")
    }

    @MainActor
    private func launch(_ arguments: [String], screen: String = "screen.wallWalk") -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze"] + arguments
        app.launch()
        XCTAssertTrue(element(app, screen).waitForExistence(timeout: 15), "\(screen) never appeared")
        XCTAssertTrue(element(app, "instruction").waitForExistence(timeout: 5))
        return app
    }

    /// Kept in the result bundle for the PR's captures.
    @MainActor
    private func snap(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    @MainActor
    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    @MainActor
    private func label(_ app: XCUIApplication, _ identifier: String) -> String {
        ElementRead.snapshot(element(app, identifier))?.label ?? ""
    }

    @MainActor
    private func reply(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier == 'action.cannotAccess' AND label == %@", title)).firstMatch
    }

    /// A new card's reply ignores taps for a moment (`InstructionCard.replyLock`).
    @MainActor
    private func tapWhenReady(_ target: XCUIElement) {
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true AND isHittable == true"), object: target)
        XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 10), .completed, "\(target) never took taps")
        target.tap()
    }
}
