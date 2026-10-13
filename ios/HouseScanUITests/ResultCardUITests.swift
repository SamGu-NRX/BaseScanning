import XCTest

/// The result card on server answers in Fixtures/results, read by the demo in debug builds
/// (`-uiDemoResultFile`), at the default text size and the largest accessibility size. The tests
/// run at both sizes keep a screenshot of the card.
final class ResultCardUITests: XCTestCase {
    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]

    /// What the review-band line says aloud. The visible line is the same sentence with "ft" and
    /// "in" (`ScanCopy.cardLine`), so the two can't disagree.
    private static let reviewBandSpoken = "Measured 16 feet. The rule is at most 20 feet, and anything 15 feet or more "
        + "needs an installer's review. The measurement can be off by about 6 inches."

    /// Words that would say a person was contacted or a review arranged. The app contacts nobody,
    /// and the server's own reason ("the policy sends it to a person") stays under Details.
    private static let handoffWords = ["sent", "sends", "send ", "notified", "scheduled", "will review", "is reviewing", "contact"]

    override func setUp() {
        continueAfterFailure = false
    }

    // MARK: A run past the review line

    /// review-band.json: a 16 ft cable run, under the 20 ft maximum and past the 15 ft review line,
    /// which the server marks unsure. The card says why, and says only that review is needed.
    @MainActor
    func testACableRunPastTheReviewLineSaysWhy() throws {
        try checkReviewBand(textSize: [], name: "result-reviewBand")
    }

    @MainActor
    func testACableRunPastTheReviewLineSaysWhyAtLargestTextSize() throws {
        try checkReviewBand(textSize: Self.largestText, name: "result-reviewBand-AX5")
    }

    @MainActor
    private func checkReviewBand(textSize: [String], name: String) throws {
        let app = launch("review-band", textSize: textSize)
        defer { app.terminate() }
        XCTAssertEqual(element(app, "result.headline").label, "Needs an installer's review")
        let line = element(app, "check.route_length")
        XCTAssertTrue(line.waitForExistence(timeout: 10), "the cable run is not on the card")
        scrollIntoView(line, in: app)
        XCTAssertEqual(line.label, "Cable run length: Not sure yet")
        XCTAssertEqual(value(line), Self.reviewBandSpoken)
        assertNoHandoff(line.label + " " + value(line))
        attach(app, name: name)
    }

    // MARK: A line without a review line

    /// unsure-view.json's window check has no review line: its sentence is the plain measurement
    /// against the rule, with nothing about review added.
    @MainActor
    func testACheckWithoutAReviewLineKeepsItsSentence() throws {
        let app = launch("unsure-view", textSize: [])
        defer { app.terminate() }
        let line = element(app, "check.window_clearance")
        XCTAssertTrue(line.waitForExistence(timeout: 10), "the window check is not on the card")
        XCTAssertEqual(value(line), "Measured 3 feet 1 inch. The rule is at least 3 feet, and the measurement can be off by about 4 inches.")
    }

    // MARK: A photo only when the line offers one

    /// view-not-offered.json: two unsure checks that views would settle. The ground's view can be
    /// taken now, so its line is a "Show me" button that says a photo would settle it. The space
    /// in front's view can't be planned, as after a withdrawn request, so its line promises no
    /// photo and offers no button. The spot-unknown run showed "One more photo would settle this"
    /// on such a line.
    @MainActor
    func testOnlyALineThatOffersThePhotoPromisesIt() throws {
        try checkPhotoOffer(textSize: [], name: "result-viewNotOffered")
    }

    @MainActor
    func testOnlyALineThatOffersThePhotoPromisesItAtLargestTextSize() throws {
        try checkPhotoOffer(textSize: Self.largestText, name: "result-viewNotOffered-AX5")
    }

    @MainActor
    private func checkPhotoOffer(textSize: [String], name: String) throws {
        let app = launch("view-not-offered", textSize: textSize)
        defer { app.terminate() }
        let offered = element(app, "check.ground_surface")
        XCTAssertTrue(offered.waitForExistence(timeout: 10), "the ground check is not on the card")
        XCTAssertEqual(offered.elementType, .button, "the ground's line should open the camera")
        XCTAssertEqual(offered.label, "Ground under the spot: Not sure yet")
        XCTAssertEqual(value(offered), "One more photo would settle this")

        let notOffered = element(app, "check.front_clearance")
        XCTAssertTrue(notOffered.exists, "the space in front is not on the card")
        XCTAssertNotEqual(notOffered.elementType, .button, "a line with no view to take must not be a button")
        XCTAssertEqual(notOffered.label, "Clear space in front: Not sure yet")
        XCTAssertEqual(value(notOffered), "Needs an installer to check")
        assertNoHandoff(notOffered.label + " " + value(notOffered))
        scrollIntoView(notOffered, in: app)
        attach(app, name: name)
    }

    // MARK: No spot and no closest spot

    /// no-spot-no-nearest.json: a reject with sweep runs but neither a spot nor a closest spot,
    /// so nothing has a battery's size. The card answers without a spot, a closest spot or check
    /// lines, the model of the wall shows no spot, and the summary stays under Details.
    @MainActor
    func testAnAnswerWithNoSpotShowsTheWallAndSummaryOnly() throws {
        try checkNoSpot(textSize: [], name: "result-noSpotNoNearest")
    }

    @MainActor
    func testAnAnswerWithNoSpotShowsTheWallAndSummaryOnlyAtLargestTextSize() throws {
        try checkNoSpot(textSize: Self.largestText, name: "result-noSpotNoNearest-AX5")
    }

    @MainActor
    private func checkNoSpot(textSize: [String], name: String) throws {
        let app = launch("no-spot-no-nearest", textSize: textSize)
        defer { app.terminate() }
        XCTAssertEqual(element(app, "result.headline").label, "Not on this wall")
        XCTAssertFalse(element(app, "result.placement").exists)
        XCTAssertFalse(element(app, "result.nearest").exists)
        let checkLines = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH 'check.'"))
        XCTAssertEqual(checkLines.count, 0, "an answer without checks put check lines on the card")
        let model = app.descendants(matching: .any).matching(NSPredicate(format: "label == '3D view of your wall'")).firstMatch
        XCTAssertTrue(model.waitForExistence(timeout: 10), "the model of the wall is missing")
        XCTAssertEqual(value(model), "No battery spot shown")
        attach(app, name: name)

        let details = element(app, "result.details")
        scrollIntoView(details, in: app)
        details.tap()
        let summary = element(app, "result.summary")
        XCTAssertTrue(summary.waitForExistence(timeout: 10), "the summary is missing under Details")
        XCTAssertTrue(summary.label.contains("every spot fails route length, wall backing"), summary.label)
        scrollIntoView(summary, in: app)
        attach(app, name: "\(name)-details")
    }

    /// Review of #53 (#42): a mark the server set aside past an unexplored end is listed under
    /// Details as not counted, in the server's words.
    @MainActor
    func testAMarkTheServerSetAsideIsListedAsNotCounted() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "result", "-uiDemoMarkPastEnd"]
        app.launch()
        XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 15))
        let details = element(app, "result.details")
        scrollIntoView(details, in: app)
        details.tap()
        let mark = element(app, "result.markNotUsed")
        XCTAssertTrue(mark.waitForExistence(timeout: 10), "the set-aside mark is missing under Details")
        XCTAssertTrue(mark.label.contains("so it was not used"), mark.label)
        scrollIntoView(mark, in: app)
        attach(app, name: "result-marksNotUsed")
    }

    // MARK: Helpers

    @MainActor
    private func launch(_ resultFile: String, textSize: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "result",
                               "-uiDemoResultFile", Self.resultFile(resultFile)] + textSize
        app.launch()
        XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 15))
        return app
    }

    /// A server answer in Fixtures/results.
    private static func resultFile(_ name: String, file: String = #filePath) -> String {
        URL(fileURLWithPath: file).deletingLastPathComponent().appending(path: "Fixtures/results/\(name).json").path
    }

    /// Scrolls until the whole element is inside the window, so the screenshot shows it. Each
    /// drag moves the content by what is missing, at most a third of the window, toward the
    /// target: a fixed swipe could carry a tall line from below the window to above it, and
    /// swiping on in one direction would never bring it back.
    @MainActor
    private func scrollIntoView(_ target: XCUIElement, in app: XCUIApplication) {
        let window = app.windows.firstMatch.frame
        let margin: CGFloat = 24
        let start = app.scrollViews.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
        for _ in 0..<12 where !window.contains(target.frame) {
            let frame = target.frame
            let shift: CGFloat = frame.maxY > window.maxY
                ? -min(frame.maxY - window.maxY + margin, window.height / 3)
                : min(window.minY - frame.minY + margin, window.height / 3)
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: shift)))
        }
        XCTAssertTrue(window.contains(target.frame), "\(target.identifier) can't be scrolled fully into view")
    }

    private func assertNoHandoff(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        let lowered = text.lowercased()
        for word in Self.handoffWords {
            XCTAssertFalse(lowered.contains(word), "the card says \"\(word)\": \(text)", file: file, line: line)
        }
    }

    @MainActor
    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    @MainActor
    private func value(_ element: XCUIElement) -> String {
        element.value as? String ?? ""
    }

    @MainActor
    private func attach(_ app: XCUIApplication, name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
