import XCTest

/// Executes ScanEngine inside the app, using the existing journey test host.
final class EngineGuidanceTests: XCTestCase {
    @MainActor private func check(_ scenario: String) {
        let app = XCUIApplication()
        app.launchArguments = ["-engineGuidanceCheck", scenario]
        app.launch()
        let result = app.staticTexts["engine.check"]
        XCTAssertTrue(result.waitForExistence(timeout: 5))
        XCTAssertEqual(result.label, "passed")
        app.terminate()
    }

    @MainActor func testAHalfMeterWallKeepsItsEndsAndEntersUpload() { check("narrowWallUpload") }
    @MainActor func testAZeroLengthWallCannotFinish() { check("degenerateWallRefused") }
    @MainActor func testAnOldFrameCannotUndoAnInterruption() { check("staleBeforeEnd") }
    @MainActor func testAnOldFrameCannotEndRecoveryAfterTheInterruptionEnds() { check("staleAfterEnd") }
    @MainActor func testAnInterruptionPausesWithoutAnotherGuidanceUpdate() { check("interruptionRecord") }
    @MainActor func testACurrentGenerationFrameDoesNotNeedAUptimeTimestamp() { check("generationIgnoresTimestamp") }
    @MainActor func testCameraAccessRecoveryStartsWithANewInterruptionGeneration() { check("captureRecreatedAfterEnd") }
    @MainActor func testCameraAccessRecoveryDropsAnUnfinishedInterruption() { check("captureRecreatedWhileInterrupted") }
    @MainActor func testReleasedCaptureCallbacksCannotChangeTheNewCapture() { check("releasedCaptureCallbacks") }
    @MainActor func testMarkingPausesWithoutWalkFrames() { check("marking") }
    @MainActor func testCancellingAMarkResumesBeforeTheNextFrame() { check("markingResume") }
    @MainActor func testCompletingAMarkResumesBeforeTheNextFrame() { check("markingFinish") }
    @MainActor func testTiltUpPausesAcrossTheEarlyReturn() { check("tiltUp") }
    @MainActor func testAnEndQuestionPausesTheAim() { check("question") }
    @MainActor func testTheNextWallScreenPausesTheAim() { check("nextWall") }
    @MainActor func testTwoHiddenCellsPauseWithoutCoaching() { check("hidden") }
    @MainActor func testRefusingAnAimDoesNotImmediatelyAskForItsNeighbours() { check("skipLaggingStretch") }
    @MainActor func testTheRingUsesTheTrimmedCurrentAndPreviousAimSpans() { check("trimmedProgress") }
    @MainActor func testTheEndScanQuestionFreezesReviewCues() { check("endScanReviewFreeze") }
    @MainActor func testTheEndScanQuestionFreezesGuidanceTargetAndPath() { check("endScanFreeze") }
}
