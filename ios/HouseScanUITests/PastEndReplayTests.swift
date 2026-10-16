import XCTest

final class PastEndReplayTests: XCTestCase {
    @MainActor func testPastEndReplaysOldCoverageButStillNeedsAFreshPhoto() {
        let app = XCUIApplication()
        app.launchArguments = ["-enginePastEndReplayCheck"]
        app.launch()
        let result = app.staticTexts["engine.check"]
        XCTAssertTrue(result.waitForExistence(timeout: 5))
        XCTAssertEqual(result.label, "passed")
        app.terminate()
    }
}
