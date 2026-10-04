import XCTest

/// Photo processing (beta) in the running app.
///
/// The real-engine tests run the synthetic replay with the autopilot and the DEBUG capture fixture
/// (`-photoProcessingFixture`), which answers the capture API inside the app; its routes land in
/// `photo-transport.log` in the gate folder. A replay records no motion and only about 2 poses a
/// second, so its 0.4 packet fails the phone's own checks: these runs exercise the choice, consent,
/// sending during the walk and the end the phone reaches, not a service answer. Every answer the
/// service can give is driven end to end in HouseScanKit's `PhotoProcessingControllerTests`, and
/// rendered here from demo mode.
///
/// `-processingBackend` keeps the choice in memory for the run, so nothing here changes the stored
/// setting the Legacy tests run with.
final class PhotoProcessingUITests: XCTestCase {
    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]

    override func setUp() {
        continueAfterFailure = false
    }

    // MARK: The real engine

    /// Photo processing chosen in Developer options before the scan, a yes, photos sent during the
    /// walk, and the scan ends on the processing screen, never on the Legacy upload. A change of
    /// choice on that screen leaves this scan on photo processing; after Start over the next scan
    /// is Legacy, asks nothing and sends nothing.
    @MainActor
    func testTheChoiceHoldsForItsScanAndTheNextScanUsesTheNewOne() throws {
        let run = try Run.launch(backend: "legacy", fixture: "candidate", holding: ["onboarding"])
        defer { run.finish() }
        run.waitFor("onboarding")
        run.choose("photoProcessing", footer: "Applies to the next scan you start.", shot: "selection")
        try run.open("onboarding")

        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20), "no photo question before the meter was marked")
        run.app.buttons["photoConsent.send"].tap()
        XCTAssertTrue(run.send.waitForNonExistence(timeout: 10))
        run.waitFor("processing", timeout: 300)
        XCTAssertFalse(run.app.descendants(matching: .any)["screen.uploading"].exists)
        let ended = run.state("notPrepared", timeout: 60)
        XCTAssertTrue(ended.exists, "the replay's packet didn't end as not prepared")
        run.attach("processing-notPrepared")
        let routes = run.routes()
        XCTAssertEqual(routes.filter { $0 == "POST captures" }.count, 1, "\(routes)")
        XCTAssertTrue(routes.contains("PUT upload"), "no photo went up during the walk: \(routes)")
        XCTAssertFalse(routes.contains("POST captures/finalize"), "a packet the phone refused was finalized: \(routes)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: run.gate.appending(path: "uploading.held").path), "the Legacy upload ran")

        // Changing the choice mid-scan: this scan stays on photo processing.
        run.choose("legacy", footer: "This scan uses Photo processing (beta). A change applies to the next scan.", shot: "selection-midScan")
        XCTAssertTrue(run.state("notPrepared", timeout: 5).exists, "the scan left photo processing when the choice changed")
        try run.open("processing")

        run.tap("action.startOver")
        run.waitFor("onboarding")
        XCTAssertEqual(run.app.buttons["action.developerOptions"].label, "Developer options")
        if run.app.buttons["action.onboardingSkip"].waitForExistence(timeout: 5) { run.app.buttons["action.onboardingSkip"].tap() }
        run.tap("action.finishOnboarding")
        run.waitFor("findMeter")
        XCTAssertFalse(run.send.waitForExistence(timeout: 5), "a Legacy scan asked to send photos")
        Thread.sleep(forTimeInterval: 2)
        XCTAssertEqual(run.routes(), routes, "the next scan sent something to photo processing")
    }

    /// A no sends nothing for the whole scan, and the scan ends saying so.
    @MainActor
    func testANoSendsNothingAndEndsSayingSo() throws {
        let run = try Run.launch(backend: "photoProcessing", fixture: "candidate")
        defer { run.finish() }
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20))
        run.app.buttons["photoConsent.decline"].tap()
        run.waitFor("processing", timeout: 300)
        XCTAssertTrue(run.state("consentNotGiven", timeout: 30).exists)
        run.attach("processing-consentNotGiven")
        XCTAssertEqual(run.routes(), [])
    }

    /// Start over after a sent scan asks the next scan again, shows nothing of the old one and
    /// sends nothing more for it.
    @MainActor
    func testStartingOverNeverRevivesTheOldCapture() throws {
        let run = try Run.launch(backend: "photoProcessing", fixture: "hold")
        defer { run.finish() }
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20))
        run.app.buttons["photoConsent.send"].tap()
        run.waitFor("processing", timeout: 300)
        // The replay's packet can't be finished, so the upload ends on the phone.
        XCTAssertTrue(run.state("notPrepared", timeout: 60).exists)
        run.attach("processing-ended")
        let sent = run.routes()
        Thread.sleep(forTimeInterval: 3)
        XCTAssertEqual(run.routes(), sent, "requests went on after the scan ended")
        try run.open("processing")

        run.tap("action.startOver")
        run.waitFor("onboarding")
        if run.app.buttons["action.onboardingSkip"].waitForExistence(timeout: 5) { run.app.buttons["action.onboardingSkip"].tap() }
        run.tap("action.finishOnboarding")
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20), "the next scan didn't ask again")
        run.app.buttons["photoConsent.decline"].tap()
        XCTAssertTrue(run.send.waitForNonExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 2)
        XCTAssertEqual(run.routes(), sent, "the next scan revived the old capture or sent without a yes")
    }

    /// Photo processing chosen in a build without its setup: Developer options say so, and the
    /// scan stops before the camera starts, says why, and offers a Legacy scan, which starts as a
    /// new scan. No photo question, no capture and nothing sent.
    @MainActor
    func testWithoutSetupTheScanStopsBeforeCaptureAndOffersLegacy() throws {
        let run = try Run.launch(backend: "photoProcessing", fixture: nil, holding: ["onboarding"])
        defer { run.finish() }
        run.waitFor("onboarding")
        run.openOptions()
        let row = run.reveal("developer.processing.photoProcessing")
        XCTAssertTrue(row.label.contains("Not set up in this build"), "the option doesn't say it isn't set up: \(row.label)")
        run.attach("selection-notSetUp")
        run.closeOptions()
        try run.open("onboarding")
        run.waitFor("processing", timeout: 30)
        XCTAssertTrue(run.state("notSetUp", timeout: 5).exists)
        let any = run.app.descendants(matching: .any)
        XCTAssertFalse(any["screen.findMeter"].exists, "the camera started for a scan that can't be sent")
        XCTAssertFalse(run.send.exists, "asked to send photos with nothing to send them to")
        XCTAssertEqual(run.routes(), [], "a capture request was made")
        run.attach("processing-notSetUp-beforeCapture")

        run.tap("action.scanWithLegacy")
        run.waitFor("findMeter", timeout: 20)
        XCTAssertFalse(run.send.waitForExistence(timeout: 3), "the Legacy scan asked to send photos")
        XCTAssertFalse(FileManager.default.fileExists(atPath: run.gate.appending(path: "uploading.held").path))
    }

    // MARK: The real engine with the synthetic capture

    /// The real coordinator and uploader carry `SyntheticCapture` (labelled synthetic in its
    /// packet) to the fixture's answer, which the processing screen shows as the scan's main
    /// result: provisional, quoted, and marked as a test answer about a synthetic capture.
    @MainActor
    func testTheSyntheticCaptureReachesATypedAnswerAsTheMainResult() throws {
        let run = try Run.launch(backend: "photoProcessing", fixture: "candidate", extra: ["-photoProcessingSyntheticCapture"])
        defer { run.finish() }
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20))
        run.app.buttons["photoConsent.send"].tap()
        run.waitFor("processing", timeout: 300)
        XCTAssertTrue(run.state("candidate", timeout: 60).exists, "no answer reached the screen")
        let any = run.app.descendants(matching: .any)
        let message = any["photo.serviceMessage"].firstMatch
        XCTAssertTrue(message.label.contains("Fixture answer: a possible spot to the left of the meter."), "the service's words aren't shown as written: \(message.label)")
        XCTAssertTrue(any["photo.standIn"].firstMatch.exists, "the fixture's answer isn't marked as a test one")
        XCTAssertTrue(run.app.staticTexts.containing(NSPredicate(format: "label CONTAINS 'synthetic capture'")).firstMatch.exists, "no word that the capture was synthetic")
        run.attach("processing-candidate-engine")
        let routes = run.routes()
        XCTAssertTrue(routes.contains("POST captures/finalize"), "\(routes)")
        XCTAssertTrue(routes.contains("GET captures/result"), "\(routes)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: run.gate.appending(path: "uploading.held").path), "the Legacy upload ran")
    }

    /// An answer naming another run than the capture started is refused, and the scan says so
    /// instead of showing it.
    @MainActor
    func testAnAnswerForAnotherRunIsRefused() throws {
        let run = try Run.launch(backend: "photoProcessing", fixture: "wrongRun", extra: ["-photoProcessingSyntheticCapture"])
        defer { run.finish() }
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20))
        run.app.buttons["photoConsent.send"].tap()
        run.waitFor("processing", timeout: 300)
        XCTAssertTrue(run.state("answerMismatch", timeout: 60).exists)
        XCTAssertFalse(run.app.descendants(matching: .any)["photo.serviceMessage"].firstMatch.exists, "the refused answer's words were shown")
        run.attach("processing-answerMismatch-engine")
    }

    /// "Stop sending photos" while the service works, with a capture folder the phone can't write
    /// (`-photoProcessingFixtureReadOnlyCapture`): sending stops and the screen says the phone
    /// couldn't save the choice.
    @MainActor
    func testStoppingWhenThePhoneCantSaveItSaysSo() throws {
        let run = try Run.launch(
            backend: "photoProcessing", fixture: "hold", extra: ["-photoProcessingSyntheticCapture", "-photoProcessingFixtureReadOnlyCapture"])
        defer { run.finish() }
        run.waitFor("findMeter")
        XCTAssertTrue(run.send.waitForExistence(timeout: 20))
        run.app.buttons["photoConsent.send"].tap()
        run.waitFor("processing", timeout: 300)
        XCTAssertTrue(run.state("processing", timeout: 60).exists, "the capture never reached processing")
        run.attach("processing-engine")
        run.tap("action.stopSendingPhotos")
        let confirm = run.app.buttons["Stop sending"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5), "stopping didn't ask first")
        confirm.tap()
        XCTAssertTrue(run.state("withdrawalNotRecorded", timeout: 10).exists)
        run.attach("processing-withdrawalNotRecorded-engine")
        let sent = run.routes()
        // Longer than the uploader waits between polls of a capture still processing.
        Thread.sleep(forTimeInterval: 3)
        XCTAssertEqual(run.routes(), sent, "requests went on after the homeowner stopped sending")
    }

    // MARK: Every state, from demo mode

    /// The consent question and each processing state, at the default text size and the largest,
    /// in demo mode with the capture fixture's words. States a replay can't reach (every service
    /// answer, the withdrawal the phone couldn't save) are covered here as they render, and in the
    /// package tests as they are reached.
    @MainActor
    func testEveryStateAtDefaultAndLargestText() throws {
        let consent = Self.demo(["-uiDemoPhase", "findMeter", "-uiDemoPhotoConsent"])
        XCTAssertTrue(consent.buttons["photoConsent.send"].waitForExistence(timeout: 15))
        XCTAssertGreaterThanOrEqual(consent.buttons["photoConsent.decline"].frame.height, 44)
        attach(consent, "consent")
        try audit(consent, "consent")
        consent.terminate()
        let consentLarge = Self.demo(["-uiDemoPhase", "findMeter", "-uiDemoPhotoConsent"] + Self.largestText)
        XCTAssertTrue(consentLarge.buttons["photoConsent.send"].waitForExistence(timeout: 15))
        attach(consentLarge, "consent-AX5")
        consentLarge.terminate()

        let large: Set<String> = ["uploading", "processing", "candidate", "needsMorePhotos", "installerReview", "withdrawalNotRecorded", "notSetUp", "setupRefused"]
        for state in [
            "queued", "uploading", "processing", "candidate", "needsMorePhotos", "installerReview", "noCandidate", "unrecognized",
            "notSetUp", "consentNotGiven", "withdrawn", "withdrawalNotRecorded", "notPrepared", "processingFailed", "expired", "refused", "setupRefused",
            "answerUnreadable", "answerNotReady", "answerMismatch",
        ] {
            for big in large.contains(state) ? [false, true] : [false] {
                let app = Self.demo(["-uiDemoPhase", "processing", "-uiDemoPhotoState", state] + (big ? Self.largestText : []))
                XCTAssertTrue(
                    app.descendants(matching: .any)["photo.state.\(state)"].waitForExistence(timeout: 15), "photo.state.\(state) never appeared")
                XCTAssertTrue(app.descendants(matching: .any)["photo.standIn"].exists || state == "notSetUp", "\(state) doesn't say its answer is a test one")
                if state == "setupRefused", !big {
                    // Shown here at the result step, after the service may have processed the scan:
                    // the words claim neither way, and don't rule out trying again once it's fixed.
                    let words = app.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: " ")
                    XCTAssertTrue(words.contains("This is a setup problem, not a problem with your photos or marks."), words)
                    XCTAssertTrue(words.contains("The connection needs to be fixed before you try again."), words)
                    XCTAssertFalse(words.contains("wasn't processed"), words)
                    XCTAssertFalse(words.contains("won't help"), words)
                }
                attach(app, "processing-\(state)\(big ? "-AX5" : "")")
                if !big, ["uploading", "candidate", "needsMorePhotos", "withdrawalNotRecorded", "setupRefused"].contains(state) { try audit(app, "processing-\(state)") }
                app.terminate()
            }
        }
    }

    /// "Stop sending photos" asks first, then ends the scan's processing. The demo stands in for the
    /// engine, whose replay can't stay in processing long enough to stop; the engine's withdrawal,
    /// saved or not, is driven in HouseScanKit's `PhotoProcessingControllerTests`.
    @MainActor
    func testStopSendingAsksFirstThenEnds() throws {
        let app = Self.demo(["-uiDemoPhase", "processing", "-uiDemoPhotoState", "processing"])
        defer { app.terminate() }
        let stop = app.buttons["action.stopSendingPhotos"]
        XCTAssertTrue(stop.waitForExistence(timeout: 15), "no way to stop sending while processing")
        stop.tap()
        let confirm = app.buttons["Stop sending"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5), "stopping didn't ask first")
        attach(app, "processing-stopQuestion")
        confirm.tap()
        XCTAssertTrue(app.descendants(matching: .any)["photo.state.withdrawn"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["action.stopSendingPhotos"].exists)
    }

    /// The selection sheet at the largest text size.
    @MainActor
    func testTheSelectionAtLargestText() throws {
        let app = Self.demo(Self.largestText)
        let run = Run(app: app, gate: URL(fileURLWithPath: NSTemporaryDirectory()))
        run.openOptions()
        _ = run.reveal("developer.processing.photoProcessing")
        attach(app, "selection-AX5")
        app.terminate()
    }

    // MARK: Helpers

    @MainActor
    private static func demo(_ extra: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-uiDemo", "-uiDemoFreeze", "-practiceMeter", "NO", "-processingBackend", "photoProcessing"] + extra
        app.launch()
        return app
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    @MainActor
    private func audit(_ app: XCUIApplication, _ screen: String) throws {
        let outcome = try AccessibilityAudit.run(app) { _ in Thread.sleep(forTimeInterval: 2.0) }
        for (_, finding) in outcome.persistent {
            let text = "\(screen): \(finding.message)"
            if ProcessInfo.processInfo.environment["HOUSESCAN_AUDIT_REPORT_ONLY"] == "1" {
                let note = XCTAttachment(string: text)
                note.name = "audit-\(screen)"
                note.lifetime = .keepAlways
                add(note)
            } else {
                XCTFail(text)
            }
        }
    }

    /// One app run on the synthetic replay with the autopilot. Every screen's gate is open from
    /// the start except those in `holding` and the processing screen, which the test opens.
    @MainActor
    struct Run {
        let app: XCUIApplication
        let gate: URL

        @MainActor
        static func launch(backend: String, fixture: String?, holding: Set<String> = [], extra: [String] = []) throws -> Run {
            let gate = URL(fileURLWithPath: NSTemporaryDirectory()).appending(path: "housescan-gate-\(UUID().uuidString)", directoryHint: .isDirectory)
            try FileManager.default.createDirectory(at: gate, withIntermediateDirectories: true)
            for phase in ["onboarding", "findMeter", "meterCloseUp", "wallWalk", "markFeatures", "gapRequest"] where !holding.contains(phase) {
                try Data().write(to: gate.appending(path: phase))
            }
            var arguments = [
                "-replay", FullFlowUITests.fixture, "-autopilot", "-autopilotHold", "1.0", "-autopilotGate", gate.path, "-sampleResult",
                "-practiceMeter", "NO", "-processingBackend", backend,
            ]
            if let fixture { arguments += ["-photoProcessingFixture", fixture] }
            arguments += extra
            let app = XCUIApplication()
            app.launchArguments = arguments
            app.launch()
            return Run(app: app, gate: gate)
        }

        var send: XCUIElement { app.buttons["photoConsent.send"] }

        @MainActor
        func waitFor(_ phase: String, timeout: TimeInterval = 60) {
            XCTAssertTrue(app.descendants(matching: .any)["screen.\(phase)"].waitForExistence(timeout: timeout), "screen.\(phase) never appeared")
        }

        /// Lets the autopilot leave `phase`.
        func open(_ phase: String) throws {
            try Data().write(to: gate.appending(path: phase))
        }

        @MainActor
        func state(_ id: String, timeout: TimeInterval) -> XCUIElement {
            let element = app.descendants(matching: .any)["photo.state.\(id)"]
            _ = element.waitForExistence(timeout: timeout)
            return element
        }

        /// Every capture-API route the fixture answered, in order.
        func routes() -> [String] {
            guard let text = try? String(contentsOf: gate.appending(path: "photo-transport.log"), encoding: .utf8) else { return [] }
            return text.split(separator: "\n").map(String.init)
        }

        @MainActor
        func tap(_ identifier: String) {
            let target = app.descendants(matching: .any)[identifier].firstMatch
            XCTAssertTrue(target.waitForExistence(timeout: 10), "missing \(identifier)")
            for _ in 0..<6 where !target.isHittable { app.swipeUp() }
            target.tap()
        }

        @MainActor
        func openOptions() {
            let open = app.buttons["action.developerOptions"]
            XCTAssertTrue(open.waitForExistence(timeout: 10), "no developer options here")
            open.tap()
            XCTAssertTrue(app.buttons["action.closeDeveloperOptions"].waitForExistence(timeout: 10), "the developer options didn't open")
            _ = reveal("developer.processing.legacy")
        }

        /// Scrolls the options until the row is on screen: at the largest text sizes the sheet
        /// opens with the processing choice below its fold.
        func reveal(_ identifier: String) -> XCUIElement {
            let row = app.buttons[identifier]
            let form = app.collectionViews.firstMatch
            for _ in 0..<8 where !(row.exists && row.isHittable) {
                if form.exists { form.swipeUp() } else { app.swipeUp() }
            }
            XCTAssertTrue(row.exists && row.isHittable, "\(identifier) never came on screen")
            return row
        }

        @MainActor
        func closeOptions() {
            app.buttons["action.closeDeveloperOptions"].tap()
            XCTAssertTrue(app.buttons["developer.processing.legacy"].waitForNonExistence(timeout: 10))
        }

        /// Picks `backend` in Developer options and checks the footer, then closes them.
        @MainActor
        func choose(_ backend: String, footer: String, shot: String) {
            openOptions()
            let row = reveal("developer.processing.\(backend)")
            // The sheet can still be presenting when the row is first on screen (see
            // `FullFlowUITests.setPracticeMeter`), so a tap that changed nothing is repeated.
            for _ in 0..<3 where !row.isSelected { row.tap() }
            XCTAssertTrue(row.isSelected, "\(backend) wasn't selected")
            let footnote = app.staticTexts["developer.processing.footer"]
            for _ in 0..<4 where !footnote.exists { app.collectionViews.firstMatch.swipeUp() }
            XCTAssertEqual(footnote.label, footer)
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = shot
            attachment.lifetime = .keepAlways
            XCTContext.runActivity(named: shot) { $0.add(attachment) }
            closeOptions()
        }

        @MainActor
        func attach(_ name: String) {
            let shot = XCTAttachment(screenshot: app.screenshot())
            shot.name = name
            shot.lifetime = .keepAlways
            XCTContext.runActivity(named: name) { $0.add(shot) }
        }

        @MainActor
        func finish() {
            try? open("processing")
            app.terminate()
            try? FileManager.default.removeItem(at: gate)
        }
    }
}
