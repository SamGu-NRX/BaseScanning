import XCTest

/// The circle "Wall ends here" marks at (`endAimCircle`, identifier `aim.circle`), read from the
/// production layout: the app's own view, as VoiceOver sees it, not a test-only overlay.
extension XCTestCase {
    /// Checks the circle is the 56 pt reticle drawn in the middle of the window, where the ray
    /// "Wall ends here" uses goes through (`ScanEngine.circleEnd`: the middle of the sensor image,
    /// which the camera view fills and centres), is wholly on screen, and is under neither the
    /// card nor any of `covers`.
    ///
    /// The card ends at its last part plus the scrim's padding under it (`InstructionCard`):
    /// unfolded, the reply (`reply`), 12 pt above the scrim's edge; folded around the circle,
    /// Details, 8 pt above it, since the reply has moved to the actions.
    @MainActor
    func assertEndCircleOpen(
        _ app: XCUIApplication, folded: Bool, reply: String, covers: [String], _ context: String,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        func element(_ identifier: String) -> XCUIElement { app.descendants(matching: .any)[identifier].firstMatch }
        let window = app.windows.firstMatch.frame
        let circle = element("aim.circle")
        XCTAssertTrue(circle.waitForExistence(timeout: 5), "\(context): no aiming circle", file: file, line: line)
        let frame = circle.frame
        XCTAssertEqual(frame.width, 56, accuracy: 1, "\(context): the aiming circle is \(frame.size), not the 56 pt reticle", file: file, line: line)
        XCTAssertEqual(frame.height, 56, accuracy: 1, "\(context): the aiming circle is \(frame.size), not the 56 pt reticle", file: file, line: line)
        XCTAssertEqual(frame.midX, window.midX, accuracy: 1, "\(context): the circle \(frame) must be the middle of the camera", file: file, line: line)
        XCTAssertEqual(frame.midY, window.midY, accuracy: 1, "\(context): the circle \(frame) must be the middle of the camera", file: file, line: line)
        XCTAssertTrue(window.contains(frame), "\(context): the circle \(frame) is not wholly on screen", file: file, line: line)

        let parts = (folded ? ["instruction", "instruction.details"] : ["instruction", reply]).map(element).filter(\.exists)
        let padding: CGFloat = folded ? 8 : 12
        if let last = parts.map(\.frame.maxY).max() {
            XCTAssertGreaterThanOrEqual(frame.minY, last + padding, "\(context): the circle \(frame) is under the card, which ends at \(last + padding)", file: file, line: line)
        }
        for identifier in covers {
            let cover = element(identifier)
            guard cover.exists else { continue }
            XCTAssertFalse(frame.intersects(cover.frame), "\(context): the circle \(frame) is under \(identifier) \(cover.frame)", file: file, line: line)
        }
    }
}
