import UIKit
import XCTest

/// Every screen state, including ones a replay never reaches (camera denied, offline upload,
/// tracking coaching, a refused mark), rendered by the scripted demo engine (`-uiDemo`, see
/// UI/Preview/UIDemo.swift) and held still with `-uiDemoFreeze`.
///
/// Each state must pass `performAccessibilityAudit()` at the default text size, and the screens
/// with the most text again at the largest accessibility size (AX5). A screenshot of each state
/// is attached to the result bundle.
final class ScreenStatesUITests: XCTestCase {
    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]

    /// Name, extra launch arguments, and the screen identifier that must appear.
    private static let states: [(name: String, arguments: [String], screen: String)] = [
        ("onboarding", [], "onboarding"),
        // Keep the Practice-on accessibility check even when ordinary fixtures force it off.
        ("onboarding-practice", ["-practiceMeter", "YES"], "onboarding"),
        ("onboarding-moves", [], "onboarding"),
        ("onboarding-permissions", [], "onboarding"),
        ("findMeter", ["-uiDemoPhase", "findMeter"], "findMeter"),
        ("meterCloseUp-cantGetClearShot", ["-uiDemoPhase", "meterCloseUp", "-uiDemoCloseUpFailed"], "meterCloseUp"),
        ("meterCloseUp-chooseNumber", ["-uiDemoPhase", "meterCloseUp", "-uiDemoMeterChoose"], "meterCloseUp"),
        ("wallWalk", ["-uiDemoPhase", "wallWalk"], "wallWalk"),
        ("wallWalk-slowDown", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "slowDown"], "wallWalk"),
        ("wallWalk-tooDark", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "tooDark"], "wallWalk"),
        ("wallWalk-turnSlowly", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "turnSlowly"], "wallWalk"),
        ("wallWalk-needsTexture", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "needsTexture"], "wallWalk"),
        ("wallWalk-relocalizing", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "relocalizing"], "wallWalk"),
        ("wallWalk-markingRefused", ["-uiDemoPhase", "wallWalk", "-uiDemoMarking", "window", "-uiDemoRefusal"], "wallWalk"),
        ("wallWalk-endQuestion", ["-uiDemoPhase", "wallWalk", "-uiDemoEndQuestion"], "wallWalk"),
        ("wallWalk-endPreview", ["-uiDemoPhase", "wallWalk", "-uiDemoEndPreview"], "wallWalk"),
        ("wallWalk-endScanQuestion", ["-uiDemoPhase", "wallWalk", "-uiDemoEndScanQuestion"], "wallWalk"),
        ("wallWalk-endScanTooShort", ["-uiDemoPhase", "wallWalk", "-uiDemoEndScanQuestion", "-uiDemoEndScanTooShort"], "wallWalk"),
        ("wallWalk-endQuestionLeavesOut", ["-uiDemoPhase", "wallWalk", "-uiDemoEndPreview", "-uiDemoEndQuestion"], "wallWalk"),
        ("wallWalk-pastWallEnd", ["-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "pastWallEnd"], "wallWalk"),
        ("wallWalk-nextWall", ["-uiDemoPhase", "wallWalk", "-uiDemoNextWall"], "wallWalk"),
        ("wallWalk-nextWallRefused", ["-uiDemoPhase", "wallWalk", "-uiDemoNextWall", "-uiDemoRefusal"], "wallWalk"),
        ("wallWalk-nextWallConfirm", ["-uiDemoPhase", "wallWalk", "-uiDemoNextWall", "-uiDemoNextWallConfirm"], "wallWalk"),
        ("wallWalk-tiltUp", ["-uiDemoPhase", "wallWalk", "-uiDemoTiltUp"], "wallWalk"),
        ("wallWalk-overheadQuestion", ["-uiDemoPhase", "wallWalk", "-uiDemoOverheadQuestion"], "wallWalk"),
        ("wallWalk-hidden", ["-uiDemoPhase", "wallWalk", "-uiDemoHidden"], "wallWalk"),
        ("wallWalk-seeBehind", ["-uiDemoPhase", "wallWalk", "-uiDemoSeeBehind"], "wallWalk"),
        ("wallWalk-aim", ["-uiDemoPhase", "wallWalk", "-uiDemoAim"], "wallWalk"),
        ("wallWalk-aimOffScreen", ["-uiDemoPhase", "wallWalk", "-uiDemoAimOffScreen"], "wallWalk"),
        // All three legend entries under the map at once (end preview, hidden, depth): they
        // overlapped in one row on CI's LiDAR walk (run 36307476187).
        ("wallWalk-fullLegend", ["-uiDemoPhase", "wallWalk", "-uiDemoEndPreview", "-uiDemoHidden"], "wallWalk"),
        ("markFeatures", ["-uiDemoPhase", "markFeatures"], "markFeatures"),
        ("markFeatures-marking", ["-uiDemoPhase", "markFeatures", "-uiDemoMarking", "door"], "markFeatures"),
        ("markFeatures-lostPlace", ["-uiDemoPhase", "markFeatures", "-uiDemoCoaching", "relocalizing"], "markFeatures"),
        ("markFeatures-groundQuestion", ["-uiDemoGroundQuestion"], "markFeatures"),
        ("markFeatures-groundAnswered", ["-uiDemoGroundAnswer", "mulch"], "markFeatures"),
        ("gapRequest", ["-uiDemoPhase", "gapRequest"], "gapRequest"),
        ("gapRequest-groundOut", ["-uiDemoPhase", "gapRequest", "-uiDemoGap", "groundOut"], "gapRequest"),
        ("gapRequest-walkOut", ["-uiDemoPhase", "gapRequest", "-uiDemoGap", "walkOut"], "gapRequest"),
        ("gapRequest-tooDark", ["-uiDemoPhase", "gapRequest", "-uiDemoCoaching", "tooDark"], "gapRequest"),
        ("gapRequest-overhead", ["-uiDemoPhase", "gapRequest", "-uiDemoGap", "overhead"], "gapRequest"),
        ("gapRequest-overheadQuestion", ["-uiDemoPhase", "gapRequest", "-uiDemoGap", "overhead", "-uiDemoOverheadQuestion"], "gapRequest"),
        ("gapRequest-followUp", ["-uiDemoPhase", "gapRequest", "-uiDemoFollowUp"], "gapRequest"),
        ("uploading", ["-uiDemoPhase", "uploading"], "uploading"),
        ("uploading-offline", ["-uiDemoPhase", "uploading", "-uiDemoOffline"], "uploading"),
        ("uploading-sample", ["-uiDemoPhase", "uploading", "-uiDemoSample"], "uploading"),
        ("uploading-rejected", ["-uiDemoPhase", "uploading", "-uiDemoRejected"], "uploading"),
        ("uploading-followUp", ["-uiDemoPhase", "uploading", "-uiDemoFollowUp"], "uploading"),
        ("spotConfirm", ["-uiDemoPhase", "spotConfirm"], "spotConfirm"),
        ("spotConfirm-answered", ["-uiDemoPhase", "spotConfirm", "-uiDemoSpotAnswered", "clear"], "spotConfirm"),
        ("result-review", ["-uiDemoPhase", "result"], "result"),
        ("result-pass", ["-uiDemoPhase", "result", "-uiDemoPass"], "result"),
        ("result-corner", ["-uiDemoPhase", "result", "-uiDemoCorner"], "result"),
        ("result-overlap", ["-uiDemoPhase", "result", "-uiDemoOverlap"], "result"),
        ("result-reject", ["-uiDemoPhase", "result", "-uiDemoResultFile", resultFile("reject-nearest")], "result"),
        ("result-wallNotMeasured", ["-uiDemoPhase", "result", "-uiDemoWallNotMeasured"], "result"),
        ("resultAR", ["-uiDemoPhase", "resultAR"], "resultAR"),
        ("cameraDenied", ["-uiDemoFailure", "cameraDenied"], "unsupported"),
        ("arUnsupported", ["-uiDemoFailure", "arUnsupported"], "unsupported"),
        ("sessionFailed", ["-uiDemoFailure", "sessionFailed"], "unsupported"),
        ("replayUnreadable", ["-uiDemoFailure", "replayUnreadable"], "unsupported"),
    ]

    /// A server answer in Fixtures/results, which the demo reads in debug builds.
    private static func resultFile(_ name: String, file: String = #filePath) -> String {
        URL(fileURLWithPath: file).deletingLastPathComponent().appending(path: "Fixtures/results/\(name).json").path
    }

    /// The screens with the most text, also checked at AX5.
    private static let largestTextStates: Set<String> = [
        "onboarding", "onboarding-practice", "onboarding-moves", "onboarding-permissions", "findMeter", "wallWalk", "wallWalk-endQuestion", "wallWalk-endPreview", "wallWalk-endQuestionLeavesOut", "wallWalk-nextWallRefused", "wallWalk-overheadQuestion", "gapRequest-walkOut", "gapRequest-overheadQuestion", "meterCloseUp-cantGetClearShot", "meterCloseUp-chooseNumber",
        "markFeatures", "gapRequest", "uploading-offline", "uploading-rejected", "result-review", "cameraDenied",
        "wallWalk-hidden", "wallWalk-seeBehind", "wallWalk-fullLegend", "gapRequest-followUp", "uploading-followUp",
        "markFeatures-groundQuestion", "markFeatures-groundAnswered", "markFeatures-lostPlace",
        // The card's reply under the aim step's words and under coaching.
        "wallWalk-aim", "wallWalk-slowDown",
        "spotConfirm", "spotConfirm-answered",
    ]

    /// Words a state must show: in the named element's label or value, or with no identifier,
    /// in any text on screen.
    private static let expectations: [String: [(identifier: String?, text: String)]] = [
        "onboarding-practice": [("action.developerOptions", "Practice meter is on.")],
        // #55: an answered permission is not asked again, so the phone only may ask.
        "onboarding-permissions": [("onboarding.permissions", "Your phone may ask to use the camera")],
        "wallWalk-hidden": [("wallTape", "2 sections hidden behind something")],
        "wallWalk-fullLegend": [("wallTape", "2 sections hidden behind something")],
        "wallWalk-seeBehind": [("instruction", "Something is in front of the wall here")],
        // At AX5 looking past it leads, and the situation folds under Details.
        "wallWalk-seeBehind-AX5": [("instruction", "Look around it"), ("instruction.details", "Details")],
        // At AX5 the meter's description folds under Details, and the camera stays open.
        "findMeter-AX5": [("instruction", "Find your electric meter"), ("instruction.details", "Details")],
        "gapRequest-followUp": [("instruction", "One more view to finish")],
        // At AX5 the side to aim at leads, and the follow-up and the stretch fold under Details.
        "gapRequest-followUp-AX5": [("instruction", "Show the ground right of your meter"), ("instruction.details", "Details")],
        // #75: a server request's stretch by its two ends, not its middle.
        "gapRequest-groundOut": [("instruction", "From 4 ft to 7 ft right of your meter.")],
        "uploading-followUp": [(nil, "One more view to finish")],
        "markFeatures-groundQuestion": [(nil, "What's on the ground along this wall?")],
        "markFeatures-groundAnswered": [("ground.answered", "Mulch")],
        "markFeatures-lostPlace": [("review.lostPlace", "Your phone lost its place")],
        // #80, #26: the gate's coaching rides on the task card.
        "wallWalk-slowDown": [("instruction", "Walk slowly to your right")],
        "wallWalk-tooDark": [("instruction", "It's dark here")],
        "wallWalk-turnSlowly": [("instruction", "Turn more slowly")],
        "gapRequest-tooDark": [("instruction", "Show the ground")],
        "spotConfirm": [("spot.question", "Is anything standing in the marked area?")],
        "spotConfirm-answered": [("spot.answered", "Thanks, it's clear")],
        // #40: an overlap reads as one, not as clearance.
        "result-overlap": [("check.meter_working_space", "Overlaps by 1 foot. The rule is no overlap")],
        // The answer comes from the checks: an unsure ground check a view settles.
        "result-review": [
            ("result.headline", "One more look"),
            // #83: keep the exact stopped end beside the redesigned answer.
            ("result.unseenSide", "The scan stopped 1 ft 4 in left of your meter. A closer spot may be past there."),
        ],
        // #66: the end question names the captured stretch it leaves out.
        "wallWalk-endQuestionLeavesOut": [("instruction", "This leaves out 5 ft you walked")],
        // A reject names the closest spot and the check it fails.
        "result-reject": [("result.nearest", "The closest spot")],
        // #67: the demo has no AR scene, so the screen must draw the result itself.
        "resultAR": [("ar.overlay", "drawn on your wall")],
        // #81: the aim ring fills as its stretch is captured.
        "wallWalk-aim": [("aim.ring", "50 percent captured")],
        // At AX5 the aim card folds its how-to words and the ring's legend under Details
        // (testAimCameraStaysOpenAtLargestTextSize).
        "wallWalk-aim-AX5": [("instruction", "Tilt down to show the ground"), ("instruction.details", "Details")],
        // #82: a second "Can't get there" soon after the first asks before ending the scan.
        "wallWalk-endScanQuestion": [("instruction", "End the scan here?")],
        "wallWalk-endScanTooShort": [("instruction", "You haven't walked enough of the wall")],
        // #76: a wall never walked shows no spot.
        "result-wallNotMeasured": [("result.headline", "We couldn't measure your wall")],
        // #85: each move in the cards' own words, one element per move.
        "onboarding-moves": [
            ("onboarding.move.1", "Aim at your meter"),
            ("onboarding.move.2", "Tilt down to show the ground"),
            ("onboarding.move.3", "Take a step back"),
            ("onboarding.move.4", "An arrow at the screen edge means the spot is off screen"),
            ("onboarding.move.5", "Wall ends here"),
        ],
    ]

    /// States past the screen's first view: the controls tapped to get there, and an element whose
    /// page must then be the one across the screen.
    private static let navigation: [String: (taps: [String], shows: String)] = [
        // #85: the page after the walk page previews the moves the walk asks for.
        "onboarding-moves": (["action.onboardingNext"], "onboarding.move.1"),
        // The last page: at AX5 its permission note scrolls with the page instead of the footer.
        "onboarding-permissions": (["action.onboardingSkip"], "onboarding.permissions"),
    ]

    /// Controls a state must offer, by identifier.
    private static let controls: [String: [String]] = [
        // #39: stopping is available even when just one requested view remains.
        "gapRequest-followUp": ["action.skipGap", "action.showResult"],
        "wallWalk-endScanQuestion": ["action.endScan", "action.keepWalking"],
        // Too little walked to finish: a new scan, not a loop back to the walk.
        "wallWalk-endScanTooShort": ["action.endScanStartOver", "action.keepWalking"],
        "result-wallNotMeasured": ["action.startOver"],
    ]

    /// States where the scan is packaged, so "Share scan" must show.
    private static let shareStates: Set<String> = ["uploading-offline", "uploading-rejected", "result-review", "result-pass"]

    override func setUp() {
        continueAfterFailure = true
    }

    @MainActor
    func testEveryStatePassesTheAccessibilityAudit() throws {
        for state in Self.states {
            try check(state.name, arguments: state.arguments, screen: state.screen)
            if Self.largestTextStates.contains(state.name) {
                try check("\(state.name)-AX5", arguments: state.arguments + Self.largestText, screen: state.screen)
            }
        }
    }

    /// The every-state audit starts on page one. Exercise the last page too: at AX5 its
    /// permission note used to consume the footer while the camera label was truncated.
    @MainActor
    func testOnboardingFinishesAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "YES", "-uiDemo", "-uiDemoFreeze"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.onboarding").waitForExistence(timeout: 15))
        attach(app, name: "onboarding-practice-AX5-polish")
        tap(app, "action.onboardingSkip")
        let allow = app.buttons["action.finishOnboarding"]
        XCTAssertTrue(allow.waitForExistence(timeout: 5))
        XCTAssertTrue(allow.isHittable)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(allow.frame))
        let note = element(app, "onboarding.permissions")
        for _ in 0..<8 where !note.isHittable { app.swipeUp() }
        XCTAssertTrue(note.isHittable, "permission explanation cannot be reached by scrolling")
        attach(app, name: "onboarding-permissions-AX5-polish")
        allow.tap()
        XCTAssertTrue(element(app, "screen.findMeter").waitForExistence(timeout: 5))
    }

    /// A failed upload says to keep the scan open to try again, and with the scan packaged that
    /// Saved scans can still share its file after the app closes. The note, Try again and Share
    /// scan are all reachable.
    @MainActor
    func testOfflineRecoveryActions() throws {
        try checkOfflineRecovery(textSize: [], name: "offline-recovery-polish")
    }

    @MainActor
    func testOfflineRecoveryActionsAtLargestTextSize() throws {
        try checkOfflineRecovery(textSize: Self.largestText, name: "offline-recovery-AX5-polish")
    }

    @MainActor
    private func checkOfflineRecovery(textSize: [String], name: String) throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze",
                               "-uiDemoPhase", "uploading", "-uiDemoOffline"] + textSize
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.uploading").waitForExistence(timeout: 15))
        let scroll = app.scrollViews.firstMatch
        let note = element(app, "upload.recoveryLimit")
        for _ in 0..<8 where !note.isHittable { scroll.swipeUp(velocity: .slow) }
        XCTAssertTrue(note.isHittable)
        // The demo scan is packaged, so the note points to Saved scans for after the app closes.
        XCTAssertTrue(note.label.contains("If you close the app, you can still share its saved file from Saved scans on the first screen."), note.label)
        let retry = app.buttons["action.retryUpload"]
        for _ in 0..<8 where !retry.isHittable { scroll.swipeUp(velocity: .slow) }
        XCTAssertTrue(retry.isHittable)
        let share = app.buttons["action.shareScan"]
        for _ in 0..<8 where !share.isHittable { scroll.swipeUp(velocity: .slow) }
        XCTAssertTrue(share.isHittable)
        attach(app, name: name)
        share.tap()
        XCTAssertTrue(app.otherElements["ActivityListView"].waitForExistence(timeout: 10))
    }

    @MainActor
    private func attach(_ app: XCUIApplication, name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// The brand read on the close-up is only offered: "Not <brand>" removes it and leaves the
    /// number candidates to answer.
    @MainActor
    func testRejectingTheMeterBrandKeepsTheNumbers() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "meterCloseUp", "-uiDemoMeterChoose"]
        app.launch()
        XCTAssertTrue(element(app, "meter.brand").waitForExistence(timeout: 15))
        tap(app, "action.rejectMeterBrand")
        XCTAssertTrue(element(app, "meter.brand").waitForNonExistence(timeout: 5))
        XCTAssertTrue(element(app, "meter.candidate.0").exists)
    }

    /// The homeowner's path through the real buttons and camera taps, not the autopilot.
    @MainActor
    func testWholeFlowThroughTheButtons() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo"]
        app.launch()
        XCTAssertTrue(element(app, "screen.onboarding").waitForExistence(timeout: 15))
        // The walk, the moves it asks for (#85), the haze, then safety with the camera prompt.
        tap(app, "action.onboardingNext")
        tap(app, "action.onboardingNext")
        tap(app, "action.onboardingNext")
        // Both prompts come from "Allow camera", so the page says why before either shows.
        let permissions = element(app, "onboarding.permissions")
        XCTAssertTrue(permissions.waitForExistence(timeout: 5), "missing onboarding.permissions")
        XCTAssertTrue(permissions.label.contains("Motion & Fitness, which lets it record air pressure"), "the motion prompt must be explained: \(permissions.label)")
        tap(app, "action.finishOnboarding")
        XCTAssertTrue(element(app, "screen.findMeter").waitForExistence(timeout: 10))
        // A tap on the camera marks the meter, like the button.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(element(app, "screen.meterCloseUp").waitForExistence(timeout: 10))
        // Nothing is filled in: the homeowner picks the reading that matches the meter.
        // The demo's close-up shows the picker 4.5 s after it opens. 30 s, not 15: on CI run
        // 36307476187 the Simulator's push daemon spun in a reconnect loop and the app's main
        // thread got no time for 14.6 s (09:42:19.6 to 09:42:34.2 in its log), so the picker
        // would have come at about 17.3 s, just after the old limit.
        tap(app, "meter.candidate.0", timeout: 30)
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        // A window takes two taps on the camera: bottom-left corner, then top-right.
        tap(app, "action.markSomething")
        tap(app, "feature.window")
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.45)).tap()
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.7, dy: 0.35)).tap()
        // The right end turns a corner: mark the next wall, walk on along it, and end it there.
        tap(app, "action.markEnd", timeout: 30)
        tap(app, "action.endCorner")
        tap(app, "action.markNextWall")
        // "Is this the next wall?" before the walk follows it (#70).
        tap(app, "action.nextWallYes")
        tap(app, "action.markEnd", timeout: 30)
        tap(app, "action.endBlocked")
        tap(app, "action.markEnd", timeout: 30)
        tap(app, "action.endBlocked")
        // Both ends answered: the walk asks to tilt up, then what is overhead.
        tap(app, "action.overheadClear", timeout: 15)
        tap(app, "action.finishWalk")
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 10))
        tap(app, "ground.answer.gravel")
        tap(app, "window.opens.no")
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 10))
        XCTAssertTrue(element(app, "screen.uploading").waitForExistence(timeout: 20))
        // The answer lists a view the camera can take: the scan goes back to the camera for it
        // on its own, and the result follows that view.
        let followUp = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == 'instruction' AND label CONTAINS 'One more view to finish'")).firstMatch
        XCTAssertTrue(followUp.waitForExistence(timeout: 20), "the answer's view must be asked for on the camera")
        // Before the result, the spot is checked on a photo.
        XCTAssertTrue(element(app, "screen.spotConfirm").waitForExistence(timeout: 30))
        XCTAssertEqual(element(app, "spot.photo").label, "Photo of your wall")
        tap(app, "action.spotClear")
        XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 30))
        XCTAssertTrue(element(app, "result.sampleBadge").exists, "a sample result must say so")
        XCTAssertTrue(element(app, "result.rulesNotFinal").exists, "placeholder rules must be disclosed")
        // B-14: a limit says whether it is a minimum or a maximum. The unit is left off: VoiceOver
        // text spells lengths out ("3 feet") once B-16 lands, the screen text says "3 ft".
        // One read (`ElementRead`): the result's content is still settling in.
        let window = ElementRead.snapshot(element(app, "check.window"))?.value as? String
        XCTAssertTrue(window?.contains("The rule is at least 3") == true,
                      "the window rule must read as a minimum, got \(String(describing: window))")
        // The result reveal slides its content in; a tap while it moves can miss (one failure in
        // three local runs), so wait until the button takes taps.
        let showAR = element(app, "action.showAR")
        XCTAssertTrue(showAR.waitForExistence(timeout: 20), "missing action.showAR")
        let hittable = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isHittable == true"), object: showAR)
        XCTAssertEqual(XCTWaiter().wait(for: [hittable], timeout: 10), .completed, "action.showAR never took taps")
        showAR.tap()
        XCTAssertTrue(element(app, "screen.resultAR").waitForExistence(timeout: 10))
        tap(app, "action.closeAR")
        // Start over sits under Details, last.
        tap(app, "result.details", timeout: 10)
        let startOver = element(app, "action.startOver")
        XCTAssertTrue(startOver.waitForExistence(timeout: 10))
        app.swipeUp()
        app.swipeUp()
        startOver.tap()
        XCTAssertTrue(element(app, "screen.onboarding").waitForExistence(timeout: 10))
    }

    /// The card's reply says what it does on each step (#63): "Skip this spot" where the phone is
    /// already at the spot, "Can't get there" where the walk asks to go somewhere. It stays under
    /// the capture gate's coaching (#80) and goes while the phone has lost its place or is past
    /// the end of the wall.
    @MainActor
    func testCardReplyFollowsTheStep() throws {
        let steps: [(name: String, arguments: [String], reply: String?)] = [
            ("walk", [], "Can't get there"),
            ("aim", ["-uiDemoAim"], "Skip this spot"),
            ("tiltUp", ["-uiDemoTiltUp"], "Skip this"),
            ("seeBehind", ["-uiDemoSeeBehind"], "Can't see past it"),
            ("slowDown", ["-uiDemoCoaching", "slowDown"], "Can't get there"),
            ("tooDark", ["-uiDemoCoaching", "tooDark"], "Can't get there"),
            ("relocalizing", ["-uiDemoCoaching", "relocalizing"], nil),
            ("pastWallEnd", ["-uiDemoCoaching", "pastWallEnd"], nil),
        ]
        for step in steps {
            let app = XCUIApplication()
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk"] + step.arguments
            app.launch()
            defer { app.terminate() }
            guard element(app, "screen.wallWalk").waitForExistence(timeout: 15) else {
                XCTFail("\(step.name): screen.wallWalk never appeared")
                continue
            }
            let shown = element(app, "action.cannotAccess")
            if let expected = step.reply {
                XCTAssertTrue(shown.waitForExistence(timeout: 5), "\(step.name): the card has no reply")
                // One read (`ElementRead`), as elsewhere in this file.
                XCTAssertEqual(ElementRead.snapshot(shown)?.label, expected, "\(step.name): wrong reply")
            } else {
                XCTAssertFalse(shown.waitForExistence(timeout: 2), "\(step.name): the reply must not show")
            }
        }
    }

    /// Each reply answers its own card: "Skip this spot" on the aim card leads to the walk, whose
    /// "Can't get there" ends the wall there. A new card's reply takes taps only after a moment
    /// (#82), so each tap waits for it. The end question's third answer ends the wall (#70), and
    /// the walk goes on to the other side.
    @MainActor
    func testRepliesAnswerTheirOwnCardAndTheWallCanJustEnd() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAim"]
        app.launch()
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        tapReply(app, "Skip this spot")
        tapReply(app, "Can't get there")
        tap(app, "action.markEnd", timeout: 10)
        XCTAssertTrue(element(app, "action.endCorner").waitForExistence(timeout: 5), "the end question must ask")
        XCTAssertTrue(element(app, "action.endBlocked").exists)
        tap(app, "action.endEnds", timeout: 5)
        XCTAssertTrue(element(app, "action.endEnds").waitForNonExistence(timeout: 5), "the answer must close the question")
        let walkLeft = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == 'instruction' AND label CONTAINS 'to your left'")).firstMatch
        XCTAssertTrue(walkLeft.waitForExistence(timeout: 5), "the walk must go on to the left once the right end is answered")
        XCTAssertTrue(reply(app, "Can't get there").waitForExistence(timeout: 5), "the walk's reply must come back")
    }

    /// #82: "End the scan here?" holds the card's reply back until it is answered. "Keep walking"
    /// goes back to the walk; "Yes, end here" finishes it for the feature review.
    @MainActor
    func testEndScanQuestionKeepsWalkingOrEnds() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoEndScanQuestion"]
        app.launch()
        XCTAssertTrue(element(app, "action.keepWalking").waitForExistence(timeout: 15))
        XCTAssertFalse(element(app, "action.cannotAccess").exists, "the card's reply must wait for the answer")
        // Run 36841096811: on a slow runner each hittability check took 1-2 s and 5 s ran out.
        tap(app, "action.keepWalking", timeout: 15)
        XCTAssertTrue(element(app, "action.keepWalking").waitForNonExistence(timeout: 5), "the answer must close the question")
        XCTAssertTrue(element(app, "screen.wallWalk").exists, "Keep walking must stay on the walk")
        app.terminate()

        app.launch()
        tap(app, "action.endScan", timeout: 15)
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 5), "Yes, end here must finish the walk")
    }

    /// #76: a wall neither side of which was walked offers a new scan, not the spot on the wall.
    @MainActor
    func testWallNotMeasuredShowsNoSpot() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "result", "-uiDemoWallNotMeasured"]
        app.launch()
        XCTAssertTrue(element(app, "result.wallNotMeasured").waitForExistence(timeout: 15))
        XCTAssertFalse(element(app, "action.showAR").exists, "no spot, so no See it on your wall")
        XCTAssertFalse(element(app, "result.placement").exists)
        XCTAssertFalse(element(app, "result.nearest").exists)
    }

    /// #80: the capture gate's coaching keeps the walk's task on the card and adds its own line,
    /// instead of replacing the card.
    @MainActor
    func testGateCoachingKeepsTheTaskOnTheCard() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoCoaching", "tooDark"]
        app.launch()
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        let card = element(app, "instruction")
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        // One snapshot read, never live (the #24 flake fix): a live read of a gone element
        // records a failure that can't be caught.
        let label = ElementRead.snapshot(card)?.label ?? ""
        XCTAssertTrue(label.contains("Walk slowly to your right"), "the task must stay on the card, got \(label)")
        XCTAssertTrue(label.contains("Keep the wall and the ground in view"), "the task's second line must stay on the card, got \(label)")
        XCTAssertTrue(label.contains("It's dark here"), "the coaching must show on the card, got \(label)")
        XCTAssertTrue(label.contains("flashlight"), "the dark coaching must say what would help, got \(label)")
    }

    /// #80, as on the walk: the capture gate's coaching keeps a gap request on the card. The dark
    /// coaching can stay up for a whole night request (field test 4.1, run 3).
    @MainActor
    func testGateCoachingKeepsTheGapRequestOnTheCard() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "gapRequest", "-uiDemoCoaching", "tooDark"]
        app.launch()
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 15))
        let card = element(app, "instruction")
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        let label = ElementRead.snapshot(card)?.label ?? ""
        XCTAssertTrue(label.contains("Show the ground"), "the request must stay on the card, got \(label)")
        XCTAssertTrue(label.contains("a clear look from two places"), "the request's second line must stay on the card, got \(label)")
        XCTAssertTrue(label.contains("It's dark here"), "the coaching must show on the card, got \(label)")
        XCTAssertTrue(label.contains("flashlight"), "the dark coaching must say what would help, got \(label)")
    }

    /// B-09: "Add something" on the review opens the camera with the marking prompt, and the
    /// review comes back with the new item once it is marked, or unchanged after Cancel.
    @MainActor
    func testAddSomethingFromTheReviewMarksOnTheCamera() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoPhase", "markFeatures"]
        app.launch()
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        let rowsBefore = app.buttons.matching(identifier: "action.deleteFeature").count
        tap(app, "feature.ac")
        XCTAssertTrue(element(app, "action.markPoint").waitForExistence(timeout: 5), "the marking view must appear")
        XCTAssertTrue(element(app, "screen.markFeatures").exists, "marking from the review stays in the review phase")
        tap(app, "action.cancelMarking")
        XCTAssertTrue(element(app, "action.confirmFeatures").waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons.matching(identifier: "action.deleteFeature").count, rowsBefore)
        tap(app, "feature.ac")
        tap(app, "action.markPoint", timeout: 5)
        XCTAssertTrue(element(app, "action.confirmFeatures").waitForExistence(timeout: 5), "the review must come back after the mark")
        XCTAssertEqual(app.buttons.matching(identifier: "action.deleteFeature").count, rowsBefore + 1)
    }

    /// AX5 puts the review chips below the window. The helper must let the real tap scroll
    /// before XCTest computes a hit point, then Cancel must return to the unchanged review.
    @MainActor
    func testTapHelperReachesAnOffscreenReviewChip() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "markFeatures"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        let chip = app.buttons.matching(identifier: "feature.ac").firstMatch
        let frame = try chip.snapshot().frame
        let window = try app.windows.firstMatch.snapshot().frame
        XCTAssertGreaterThan(frame.maxY, window.maxY, "the regression needs a chip below the window")
        let rowsBefore = app.buttons.matching(identifier: "action.deleteFeature").count
        tap(app, "feature.ac")
        XCTAssertTrue(element(app, "action.markPoint").waitForExistence(timeout: 5), "the tap must open the marking camera")
        tap(app, "action.cancelMarking")
        XCTAssertTrue(element(app, "action.confirmFeatures").waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons.matching(identifier: "action.deleteFeature").count, rowsBefore)
    }

    @MainActor
    func testTapHelperClosesARThroughTheDoneButton() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "resultAR"]
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.resultAR").waitForExistence(timeout: 15))
        tap(app, "action.closeAR")
        XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 10), "Done must return to the result")
        XCTAssertTrue(element(app, "screen.resultAR").waitForNonExistence(timeout: 5))
    }

    /// While the phone has lost its place the review can't start a mark, which taps into the
    /// scene: the chips give way to a line saying so, and "Looks complete" still sends the scan.
    @MainActor
    func testReviewWhileLostOffersFinishingNotMarking() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "markFeatures", "-uiDemoCoaching", "relocalizing"]
        app.launch()
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        XCTAssertTrue(element(app, "review.lostPlace").exists, "the review must say the phone lost its place")
        for kind in ["gas_meter", "door", "window", "ac", "drive", "fence"] {
            XCTAssertFalse(element(app, "feature.\(kind)").exists, "feature.\(kind) must not be offered while the phone is lost")
        }
        XCTAssertTrue(element(app, "action.confirmFeatures").isHittable, "Looks complete must stay available")
    }

    /// The ground question asks until it is answered, then folds into one row with the answer;
    /// Change opens the answers again with the current one selected, and a new pick folds it back.
    @MainActor
    func testGroundQuestionFoldsIntoARowAndChangeReopensIt() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoGroundQuestion"]
        app.launch()
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        for id in ["lawn", "mulch", "gravel", "concrete", "drive", "deck", "notSure"] {
            XCTAssertTrue(element(app, "ground.answer.\(id)").exists, "missing ground.answer.\(id)")
        }
        XCTAssertFalse(element(app, "ground.change").exists, "an unanswered question must not show Change")

        tap(app, "ground.answer.gravel")
        XCTAssertTrue(element(app, "ground.change").waitForExistence(timeout: 5), "the answer must fold into a row")
        XCTAssertTrue(element(app, "ground.answer.lawn").waitForNonExistence(timeout: 5), "the answers must go once answered")
        // Read once each (`ElementRead`): the row and the answers are swapping in and out.
        XCTAssertTrue(ElementRead.snapshot(element(app, "ground.answered"))?.label.contains("Gravel") == true, "the row must show the answer")

        tap(app, "ground.change")
        let gravel = element(app, "ground.answer.gravel")
        XCTAssertTrue(gravel.waitForExistence(timeout: 5), "Change must bring the answers back")
        XCTAssertTrue(ElementRead.snapshot(gravel)?.isSelected == true, "the current answer must show as selected")
        XCTAssertFalse(element(app, "ground.change").exists)

        tap(app, "ground.answer.notSure")
        XCTAssertTrue(element(app, "ground.change").waitForExistence(timeout: 5))
        XCTAssertTrue(ElementRead.snapshot(element(app, "ground.answered"))?.label.contains("Not sure") == true)
    }

    /// A refused upload offers the review, not "Try again"; from the review the scan is sent
    /// again and reaches the result.
    ///
    /// The pass sample (`-uiDemoPass`) lists no view to take, so the resend goes straight to the
    /// result. The review sample asked for one more, and one wait covered a second capture, two
    /// uploads and the server's follow-up; on run 36736877861 it ran out with the app still
    /// uploading at 60% (#191).
    ///
    /// The review's own view is left to the demo, which covers it and sends by itself: 1.2 s, then
    /// six 0.45 s steps to covered, then 1.6 s (`DemoEngine.gapScript`). This test used to tap
    /// "I can't get there" first, which raced that script. Counted from the start of the wait for
    /// the view, the tap's event came 3.2 to 3.5 s in when it passed (runs 37001843593 and
    /// 37001782118). On run 37013846307 it came at 4.3 s, and on 37013940503 the tap began at
    /// 4.5 s; both times the button had gone. Skipping is checked frozen in
    /// `testSkippingTheGapRequestSends`.
    @MainActor
    func testRejectedUploadGoesBackToReview() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoPhase", "gapRequest", "-uiDemoRejected", "-uiDemoPass"]
        app.launch()
        XCTAssertTrue(element(app, "action.backToReview").waitForExistence(timeout: 30))
        XCTAssertFalse(element(app, "action.retryUpload").exists, "a refused scan must not offer Try again")
        XCTAssertTrue(element(app, "action.startOver").exists)
        tap(app, "action.backToReview")
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 10))
        // #65: the ground is unanswered, so the first tap points to it and the second sends.
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "review.unanswered").waitForExistence(timeout: 5))
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 10), "the review must open its own view")
        tap(app, "action.spotClear", timeout: 30)
        XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 20))
    }

    /// "I can't get there" on a gap request sends the scan. Frozen, the demo's gap script never
    /// runs, so the request can't finish itself before the tap.
    @MainActor
    func testSkippingTheGapRequestSends() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "gapRequest"]
        app.launch()
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 15))
        // The card's reply ignores taps for a moment after it appears (`InstructionCard.replyLock`).
        let skip = element(app, "action.skipGap")
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true AND isHittable == true"), object: skip)
        XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 10), .completed, "I can't get there never took taps")
        skip.tap()
        XCTAssertTrue(element(app, "screen.uploading").waitForExistence(timeout: 10), "skipping must send the scan")
        XCTAssertFalse(element(app, "screen.gapRequest").exists)
    }

    /// #65 soft gate: with the ground or a window's question unanswered, the first "Looks
    /// complete" stays on the review and says so; answering ("Not sure" counts) clears the line,
    /// and the next tap sends. Unanswered, the second tap sends anyway.
    @MainActor
    func testLooksCompleteFirstPointsToAnUnansweredQuestion() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoPhase", "markFeatures"]
        app.launch()
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        XCTAssertTrue(element(app, "window.opens.notSure").exists, "the window question must offer Not sure")
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "review.unanswered").waitForExistence(timeout: 5), "the first tap must say a question is unanswered")
        XCTAssertFalse(element(app, "screen.gapRequest").exists, "the first tap must not send")
        tap(app, "ground.answer.gravel")
        tap(app, "window.opens.notSure")
        XCTAssertTrue(element(app, "review.unanswered").waitForNonExistence(timeout: 5), "the line must go once all are answered")
        XCTAssertTrue(ElementRead.snapshot(element(app, "window.opens.notSure"))?.isSelected == true, "Not sure must show as the answer")
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 10), "answered, the tap must send")
        app.terminate()

        app.launch()
        XCTAssertTrue(element(app, "screen.markFeatures").waitForExistence(timeout: 15))
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "review.unanswered").waitForExistence(timeout: 5))
        tap(app, "action.confirmFeatures")
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 10), "the second tap must send anyway")
    }

    /// #81: the first aim ring comes with a line under the card saying what it is for, clear of
    /// the card at every text size, and reads its progress to VoiceOver. Off screen, the edge
    /// arrow stands in for the ring, and neither shows.
    @MainActor
    func testAimRingShowsProgressAndItsLegend() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAim"]
        app.launch()
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        let ring = element(app, "aim.ring")
        XCTAssertTrue(ring.waitForExistence(timeout: 5), "the aim ring must show its progress")
        XCTAssertEqual(ring.value as? String, "50 percent captured")
        let legend = element(app, "aim.legend")
        XCTAssertTrue(legend.waitForExistence(timeout: 5), "the first aim ring must come with its legend")
        XCTAssertTrue(legend.label.contains("It fills as your phone captures this spot"), "legend reads \(legend.label)")
        let card = element(app, "instruction")
        XCTAssertFalse(legend.frame.intersects(card.frame), "the legend must keep clear of the card: \(legend.frame) vs \(card.frame)")
        XCTAssertFalse(element(app, "instruction.details").exists, "at the default size every word stays on the card")
        app.terminate()

        // At the largest text size the legend is folded under Details with the card's how-to
        // words, and still reads in full once opened.
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAim"] + Self.largestText
        app.launch()
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        tap(app, "instruction.details")
        let largeLegend = element(app, "aim.legend")
        XCTAssertTrue(largeLegend.waitForExistence(timeout: 5), "the legend must show under Details at the largest text size")
        XCTAssertTrue(largeLegend.label.contains("It fills as your phone captures this spot"), "legend reads \(largeLegend.label)")
        let largeCard = element(app, "instruction")
        XCTAssertFalse(largeLegend.frame.intersects(largeCard.frame), "the legend must keep clear of the card: \(largeLegend.frame) vs \(largeCard.frame)")
        app.terminate()

        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAimOffScreen"]
        app.launch()
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        XCTAssertFalse(element(app, "aim.ring").exists, "off screen, the arrow stands in for the ring")
        XCTAssertFalse(element(app, "aim.legend").exists, "the legend goes with the ring")
    }

    // MARK: Largest text layout

    /// The content width inside a camera screen's side margins (`Metrics.edge`, 16 pt), less a
    /// point for rounding.
    private static func contentWidth(_ window: CGRect) -> CGFloat { window.width - 2 * 16 - 1 }

    /// B-28: side by side at AX5, "Mark something" and "Wall ends here" broke into one or two
    /// letters a line. Every pair of wall-walk actions must stack there, each spanning the
    /// content width. A button taller than a quarter of the screen is a label broken a letter a
    /// line; a stacked two-line label is about 130 pt on an iPhone 17. At the default size the
    /// walk's pair stays on one row.
    @MainActor
    func testWallWalkActionsStackAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        func launch(_ extra: [String], textSize: [String] = Self.largestText) {
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk"] + extra + textSize
            app.launch()
            XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        }

        launch([], textSize: [])
        let mark = element(app, "action.markSomething"), end = element(app, "action.endHere")
        XCTAssertTrue(mark.waitForExistence(timeout: 5) && end.waitForExistence(timeout: 5))
        XCTAssertEqual(mark.frame.midY, end.frame.midY, accuracy: 1, "at the default size the walk's two actions share a row")
        app.terminate()

        launch([])
        assertStacked(app, "action.markSomething", "action.endHere", name: "wallWalk-walking-AX5-stacked")
        // The walk's reply ends the side here, which asks for the wall's end.
        scrollToTop(app)
        tapReply(app, "Can't get there")
        assertStacked(app, "action.markSomething", "action.markEnd", name: "wallWalk-markEnd-AX5-stacked")
        app.terminate()

        launch(["-uiDemoTiltUp"])
        assertStacked(app, "action.markSomething", "action.finishWalk", name: "wallWalk-finish-AX5-stacked")
        app.terminate()

        launch(["-uiDemoMarking", "window"])
        assertStacked(app, "action.cancelMarking", "action.markPoint", name: "wallWalk-marking-AX5-stacked")
        app.terminate()
    }

    @MainActor
    private func assertStacked(_ app: XCUIApplication, _ upper: String, _ lower: String, name: String) {
        let window = app.windows.firstMatch.frame
        let first = element(app, upper), second = element(app, lower)
        XCTAssertTrue(first.waitForExistence(timeout: 5), "\(name): missing \(upper)")
        XCTAssertTrue(second.waitForExistence(timeout: 5), "\(name): missing \(lower)")
        for (identifier, target) in [(upper, first), (lower, second)] {
            let frame = target.frame
            XCTAssertGreaterThanOrEqual(frame.width, Self.contentWidth(window), "\(name): \(identifier) is \(frame.width) pt wide in a \(window.width) pt window; beside another action its words break apart")
            XCTAssertLessThanOrEqual(frame.height, window.height / 4, "\(name): \(identifier) is \(frame.height) pt tall; its label breaks a letter or two a line")
        }
        XCTAssertLessThanOrEqual(first.frame.maxY, second.frame.minY + 0.5, "\(name): \(upper) \(first.frame) must sit above \(lower) \(second.frame)")
        XCTAssertTrue(scrollUntilHittable(second, in: app), "\(name): \(lower) can't be reached")
        attach(app, name: name)
    }

    /// Drags the screen down until its top shows, for a control that scrolled out of view.
    @MainActor
    private func scrollToTop(_ app: XCUIApplication) {
        let step = app.windows.firstMatch.frame.height * 0.4
        for _ in 0..<3 {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
            start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: step)), withVelocity: .slow, thenHoldForDuration: 0.3)
        }
    }

    /// B-36: at AX5 the aim card filled the screen and hid the aim ring under it. On a step that
    /// has the homeowner aim, the card now keeps the task and its reply in view, folds the how-to
    /// words and the ring's legend under Details, and the chrome leaves the camera open between
    /// the card and the actions. The spot's marker is in that open camera: the ring, or, while
    /// the spot is under the card, an arrow toward it that reads the ring's progress. At no
    /// scroll position is either drawn under the words, Details, the reply or an action.
    @MainActor
    func testAimCameraStaysOpenAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAim"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        let window = app.windows.firstMatch.frame
        let card = element(app, "instruction")
        let details = element(app, "instruction.details")
        let reply = element(app, "action.cannotAccess")
        let mark = element(app, "action.markSomething")
        for (identifier, target) in [("instruction", card), ("instruction.details", details), ("action.cannotAccess", reply), ("action.markSomething", mark)] {
            XCTAssertTrue(target.waitForExistence(timeout: 5), "missing \(identifier)")
        }
        let label = ElementRead.snapshot(card)?.label ?? ""
        XCTAssertTrue(label.contains("Tilt down to show the ground"), "the task must stay on the card, got \(label)")
        XCTAssertFalse(label.contains("1 ft 4 in right of your meter"), "the how-to words must fold under Details, got \(label)")
        XCTAssertFalse(element(app, "aim.legend").exists, "the legend must fold under Details")
        XCTAssertTrue(details.isHittable, "Details must be reachable without scrolling")
        XCTAssertTrue(reply.isHittable, "the card's reply must be reachable without scrolling")
        let cardBottom = max(card.frame.maxY, details.frame.maxY, reply.frame.maxY)
        let openCamera = CGRect(x: window.minX, y: cardBottom, width: window.width, height: mark.frame.minY - cardBottom)
        XCTAssertGreaterThanOrEqual(openCamera.height, 150, "the camera must stay open between the card and the actions: \(openCamera)")

        let ring = element(app, "aim.ring")
        let arrow = element(app, "aim.arrow")
        let marker = ring.exists ? ring : arrow
        XCTAssertTrue(marker.waitForExistence(timeout: 5), "the spot needs its ring or an arrow toward it")
        XCTAssertTrue(openCamera.contains(marker.frame), "the spot's marker \(marker.frame) must be in the open camera \(openCamera)")
        XCTAssertEqual(ElementRead.snapshot(marker)?.value as? String, "50 percent captured", "the marker must read the spot's progress")
        attach(app, name: "wallWalk-aim-AX5-folded")

        let covers = ["instruction", "instruction.details", "action.cannotAccess", "action.markSomething"].map { ($0, element(app, $0)) }
        // Short drags through every scroll position; where the screen fits, nothing moves.
        for step in 0..<12 {
            for (name, shown) in [("ring", ring), ("arrow", arrow)] where shown.exists {
                let frame = shown.frame
                XCTAssertTrue(window.contains(frame), "step \(step): \(name) \(frame) is not wholly on screen")
                for (identifier, cover) in covers where cover.exists {
                    XCTAssertFalse(frame.intersects(cover.frame), "step \(step): \(name) \(frame) is under \(identifier) \(cover.frame)")
                }
            }
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.75))
            start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -60)), withVelocity: .slow, thenHoldForDuration: 0.2)
        }
        XCTAssertTrue(scrollUntilHittable(mark, in: app), "Mark something must be reachable by scrolling")
    }

    /// At AX5, Details opens the folded how-to words and the ring's legend, and closes them again.
    @MainActor
    func testFoldedDetailsOpenAndCloseAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoAim"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
        tap(app, "instruction.details")
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5), "Details must open the how-to words")
        XCTAssertTrue(detail.label.contains("1 ft 4 in right of your meter"), "detail reads \(detail.label)")
        let legend = element(app, "aim.legend")
        XCTAssertTrue(legend.waitForExistence(timeout: 5), "Details must open the ring's legend")
        XCTAssertTrue(legend.label.contains("It fills as your phone captures this spot"), "legend reads \(legend.label)")
        attach(app, name: "wallWalk-aim-AX5-details")
        tap(app, "instruction.details")
        XCTAssertTrue(detail.waitForNonExistence(timeout: 5), "Details must close again")
        XCTAssertFalse(legend.exists)
    }

    /// Where a card's second line is itself what to do now, it stays on the card at AX5: the walk
    /// out with its live reading, stepping back for ground further out, and tilting up to the roof
    /// or the sky. Looking past an obstruction leads with its action instead
    /// (`testSeeBehindLeadsWithItsActionAtLargestTextSize`).
    @MainActor
    func testActionWordsStayOnTheCardAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        for (state, words) in [
            ("gapRequest-walkOut", "Follow the dotted line"),
            ("gapRequest-groundOut", "Step back and tilt down"), ("gapRequest-overhead", "up to the roof or the sky"),
        ] {
            guard let fixture = Self.states.first(where: { $0.name == state }) else {
                XCTFail("no state named \(state)")
                continue
            }
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze"] + fixture.arguments + Self.largestText
            app.launch()
            XCTAssertTrue(element(app, "screen.\(fixture.screen)").waitForExistence(timeout: 15))
            let card = element(app, "instruction")
            XCTAssertTrue(card.waitForExistence(timeout: 5))
            let label = ElementRead.snapshot(card)?.label ?? ""
            XCTAssertTrue(label.contains(words), "\(state): \"\(words)\" must stay on the card, got \(label)")
            XCTAssertFalse(element(app, "instruction.details").exists, "\(state): nothing to fold")
            app.terminate()
        }
    }

    /// Root's review of the AX5 frame at 35cc8521: unfolded, the see-behind card covered the camera,
    /// and the spot it asks the homeowner to look past, down to its reply. Folded, what to do leads,
    /// ride-along coaching and the reply stay, the camera opens between the card and the controls
    /// below it, and Details holds the situation and where it is. The step's ring has no progress,
    /// so it is hidden from VoiceOver and its place is checked in the attached frames.
    @MainActor
    func testSeeBehindLeadsWithItsActionAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        for (coaching, name) in [(nil, "wallWalk-seeBehind-AX5-folded"), ("slowDown", "wallWalk-seeBehind-slowDown-AX5-folded")] {
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoSeeBehind"]
                + (coaching.map { ["-uiDemoCoaching", $0] } ?? []) + Self.largestText
            app.launch()
            XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
            let window = app.windows.firstMatch.frame
            let card = element(app, "instruction")
            let details = element(app, "instruction.details")
            let cantSee = reply(app, "Can't see past it")
            XCTAssertTrue(card.waitForExistence(timeout: 5), "missing instruction")
            XCTAssertTrue(details.waitForExistence(timeout: 5), "\(name): the situation must fold under Details")
            XCTAssertTrue(cantSee.waitForExistence(timeout: 5), "\(name): missing the reply Can't see past it")
            let label = ElementRead.snapshot(card)?.label ?? ""
            XCTAssertTrue(label.contains("Look around it"), "\(name): what to do must lead, got \(label)")
            XCTAssertFalse(label.contains("Something is in front of the wall here"), "\(name): the situation must fold under Details, got \(label)")
            if coaching != nil {
                XCTAssertTrue(label.contains("Slow down"), "\(name): the coaching must stay in view, got \(label)")
            }
            XCTAssertTrue(details.isHittable, "\(name): Details must be reachable without scrolling")
            XCTAssertTrue(cantSee.isHittable, "\(name): Can't see past it must be reachable without scrolling")
            // "Mark something" steps aside on this step, so the camera ends at the first control
            // shown below the card, or at the bottom of the screen.
            let cardBottom = max(card.frame.maxY, details.frame.maxY, cantSee.frame.maxY)
            let below = ["action.endHere", "action.markEnd", "action.finishWalk", "wallTape"]
                .map { element(app, $0) }
                .filter { $0.exists && $0.frame.minY > cardBottom }
                .map(\.frame.minY)
            let openCamera = CGRect(x: window.minX, y: cardBottom, width: window.width, height: min(below.min() ?? window.maxY, window.maxY) - cardBottom)
            attach(app, name: name)
            XCTAssertGreaterThanOrEqual(openCamera.height, 150, "\(name): the camera must stay open between the card and the controls: \(openCamera)")

            if coaching == nil {
                tap(app, "instruction.details")
                let detail = element(app, "instruction.detail")
                XCTAssertTrue(detail.waitForExistence(timeout: 5), "Details must open the situation")
                XCTAssertTrue(detail.label.contains("Something is in front of the wall here, about 5 ft right of your meter. Look at it from the side or step around it."), "detail reads \(detail.label)")
                XCTAssertTrue(scrollUntilHittable(cantSee, in: app), "Can't see past it must stay reachable with Details open")
                attach(app, name: "wallWalk-seeBehind-AX5-details")
            }
            app.terminate()
        }
    }

    /// Root's manual pass at b6ee153b: after "Allow camera" at AX5, the find-meter card filled the
    /// camera with its description, hid the reticle at the middle of the screen, and pushed "This
    /// is my meter" below the screen. Folded, the task stays, the description sits under Details,
    /// the camera opens between the card and the button, and the button marks the meter.
    @MainActor
    func testFindMeterKeepsTheCameraOpenAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "findMeter"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.findMeter").waitForExistence(timeout: 15))
        assertFindMeterFolded(app, task: "Find your electric meter", description: "A gray box with a round glass dial", name: "findMeter-AX5-folded")

        let details = element(app, "instruction.details")
        tap(app, "instruction.details")
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5), "Details must open the meter's description")
        XCTAssertTrue(detail.label.contains("A gray box with a round glass dial or a small screen, usually on an outside wall."), "detail reads \(detail.label)")
        attach(app, name: "findMeter-AX5-details")
        tap(app, "instruction.details")
        XCTAssertTrue(detail.waitForNonExistence(timeout: 5), "Details must close again")
        XCTAssertTrue(details.isHittable)

        tap(app, "action.markMeter")
        XCTAssertTrue(element(app, "screen.meterCloseUp").waitForExistence(timeout: 10), "This is my meter must mark the meter")
    }

    /// The practice scan's find-meter step folds the same way: "Tap a spot on a wall" stays, the
    /// sample's explanation sits under Details, and "Put the sample meter here" is on screen. The
    /// real engine on a replay, since only it starts a practice scan.
    @MainActor
    func testPracticeFindMeterKeepsTheCameraOpenAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-replay", FullFlowUITests.fixture, "-sampleResult", "-practiceMeter", "YES"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.onboarding").waitForExistence(timeout: 30))
        if element(app, "action.onboardingSkip").waitForExistence(timeout: 5) { tap(app, "action.onboardingSkip") }
        tap(app, "action.finishOnboarding", timeout: 15)
        XCTAssertTrue(element(app, "screen.findMeter").waitForExistence(timeout: 15))
        XCTAssertEqual(element(app, "action.markMeter").label, "Put the sample meter here")
        assertFindMeterFolded(app, task: "Tap a spot on a wall", description: "A sample meter goes there", name: "findMeter-practice-AX5-folded")
        tap(app, "action.markMeter")
        XCTAssertTrue(element(app, "screen.meterCloseUp").waitForExistence(timeout: 15), "Put the sample meter here must mark the meter")
    }

    /// The folded find-meter card: the task in view, its description under Details, the button
    /// whole on screen without scrolling, and open camera between them for the reticle (76 pt).
    @MainActor
    private func assertFindMeterFolded(_ app: XCUIApplication, task: String, description: String, name: String) {
        let window = app.windows.firstMatch.frame
        let card = element(app, "instruction")
        let details = element(app, "instruction.details")
        let mark = element(app, "action.markMeter")
        XCTAssertTrue(card.waitForExistence(timeout: 5), "\(name): missing instruction")
        XCTAssertTrue(details.waitForExistence(timeout: 5), "\(name): the description must fold under Details")
        XCTAssertTrue(mark.waitForExistence(timeout: 5), "\(name): missing action.markMeter")
        let label = ElementRead.snapshot(card)?.label ?? ""
        XCTAssertTrue(label.contains(task), "\(name): the task must stay on the card, got \(label)")
        XCTAssertFalse(label.contains(description), "\(name): the description must fold under Details, got \(label)")
        let cardBottom = max(card.frame.maxY, details.frame.maxY)
        let openCamera = CGRect(x: window.minX, y: cardBottom, width: window.width, height: mark.frame.minY - cardBottom)
        attach(app, name: name)
        XCTAssertTrue(details.isHittable, "\(name): Details must be reachable without scrolling")
        XCTAssertTrue(window.contains(mark.frame) && mark.isHittable, "\(name): the mark button must be whole on screen without scrolling: \(mark.frame) in \(window)")
        XCTAssertGreaterThanOrEqual(openCamera.height, 150, "\(name): the camera must stay open between the card and the button: \(openCamera)")
    }

    /// Coaching that rides along with the task says what to do now, so the folded card at AX5
    /// keeps it in view with the task; only the how-to words fold.
    @MainActor
    func testCoachingStaysOnTheFoldedCardAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        for (coaching, words) in [("slowDown", "Slow down"), ("tooDark", "It's dark here")] {
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "wallWalk", "-uiDemoCoaching", coaching] + Self.largestText
            app.launch()
            XCTAssertTrue(element(app, "screen.wallWalk").waitForExistence(timeout: 15))
            let card = element(app, "instruction")
            XCTAssertTrue(card.waitForExistence(timeout: 5))
            let label = ElementRead.snapshot(card)?.label ?? ""
            XCTAssertTrue(label.contains("Walk slowly to your right"), "\(coaching): the task must stay, got \(label)")
            XCTAssertTrue(label.contains(words), "\(coaching): the coaching must stay in view, got \(label)")
            XCTAssertFalse(label.contains("Keep the wall and the ground in view"), "\(coaching): the how-to words must fold, got \(label)")
            XCTAssertTrue(card.isHittable || card.frame.maxY <= app.windows.firstMatch.frame.maxY, "\(coaching): the card must be on screen")
            XCTAssertTrue(element(app, "instruction.details").exists, "\(coaching): Details must hold the folded words")
            attach(app, name: "wallWalk-\(coaching)-AX5-folded")
            app.terminate()
        }
    }

    /// At AX5 a scrolled onboarding page ran on under the page dots. On every page, before and
    /// after scrolling it to its end, the row the dots sit in must show only the background
    /// beside the dots. Read from the screenshot's pixels: the dots are hidden from
    /// accessibility, and a page's frame alone can't show what it paints past its edge.
    @MainActor
    func testOnboardingTextStaysClearOfThePageDotsAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze"] + Self.largestText
        app.launch()
        defer { app.terminate() }
        XCTAssertTrue(element(app, "screen.onboarding").waitForExistence(timeout: 15))
        let footer = element(app, "onboarding.footer")
        XCTAssertTrue(footer.waitForExistence(timeout: 5))
        // Each page's last element: the page is at its end once its bottom shows above the footer.
        let ends: [XCUIElement] = [
            app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Walk along the wall by your electric meter")).firstMatch,
            element(app, "onboarding.move.5"),
            element(app, "onboarding.privacy"),
            element(app, "onboarding.permissions"),
        ]
        for (index, last) in ends.enumerated() {
            let page = index + 1
            // Let the page slide settle.
            Thread.sleep(forTimeInterval: 0.8)
            assertDotsRowClear(app, name: "onboarding-page\(page)-AX5-dots")
            XCTAssertTrue(last.waitForExistence(timeout: 5), "page \(page): missing its last element")
            XCTAssertTrue(scrollPageToEnd(app, last: last, footer: footer), "page \(page): the bottom of its last element \(last.frame) never came above the footer \(footer.frame)")
            assertDotsRowClear(app, name: "onboarding-page\(page)-AX5-dots-end")
            if page < ends.count { tap(app, "action.onboardingNext") }
        }
    }

    /// Swipes the page on screen up, at most twelve times, until the bottom of its last element
    /// shows above the footer. At AX5 a single move can be taller than the page's viewport, so
    /// only its bottom edge marks the end. The swipe goes to the page's own scroll view: a drag
    /// at a screen point left the second page where it was (run 36743027718).
    @MainActor
    private func scrollPageToEnd(_ app: XCUIApplication, last: XCUIElement, footer: XCUIElement) -> Bool {
        let window = app.windows.firstMatch.frame
        func atEnd() -> Bool {
            let frame = last.frame
            return frame.maxY <= footer.frame.minY + 1 && frame.maxY > window.minY
        }
        // The page on screen, not its neighbours in the pager.
        guard let page = app.scrollViews.matching(identifier: "onboarding.page").allElementsBoundByIndex
            .first(where: { abs($0.frame.minX - window.minX) < 1 && $0.frame.width > 0 }) else {
            return false
        }
        for _ in 0..<12 where !atEnd() {
            page.swipeUp(velocity: .slow)
        }
        return atEnd()
    }

    /// The band from the footer's top edge to the button's, where the dots sit, must be the
    /// background colour except for the middle 120 pt, which holds the dots.
    @MainActor
    private func assertDotsRowClear(_ app: XCUIApplication, name: String) {
        let footer = element(app, "onboarding.footer").frame
        let button = app.buttons.matching(NSPredicate(format: "identifier IN %@", ["action.onboardingNext", "action.finishOnboarding"])).firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 5), "\(name): missing the footer's button")
        let band = CGRect(x: footer.minX, y: footer.minY, width: footer.width, height: button.frame.minY - footer.minY)
        XCTAssertGreaterThan(band.height, 8, "\(name): no room for the dots above the button: \(footer) vs \(button.frame)")
        let screenshot = app.screenshot()
        attach(app, name: name)
        guard let pixels = ScreenPixels(screenshot.image) else {
            XCTFail("\(name): cannot read the screenshot's pixels")
            return
        }
        if let stray = pixels.firstMismatch(in: band, excludingMiddle: 120) {
            XCTFail("\(name): something other than the background is drawn beside the page dots at \(stray) in \(band)")
        }
    }

    /// At AX5 the Replay and sample-result badges covered most of the result's 3D view. There
    /// they sit clear of it, in full on screen; at the default size they stay on the view.
    @MainActor
    func testResultBadgesKeepOffTheModelAtLargestTextSize() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        for (textSize, name) in [([String](), "result-badges"), (Self.largestText, "result-badges-AX5")] {
            app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "result"] + textSize
            app.launch()
            XCTAssertTrue(element(app, "screen.result").waitForExistence(timeout: 15))
            let window = app.windows.firstMatch.frame
            let model = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "3D view of your wall")).firstMatch
            XCTAssertTrue(model.waitForExistence(timeout: 5), "\(name): missing the 3D view")
            for identifier in ["modeBadge", "result.sampleBadge"] {
                let badge = element(app, identifier)
                XCTAssertTrue(badge.waitForExistence(timeout: 5), "\(name): missing \(identifier)")
                XCTAssertTrue(window.contains(badge.frame), "\(name): \(identifier) \(badge.frame) runs off screen")
                if textSize.isEmpty {
                    XCTAssertTrue(model.frame.contains(badge.frame), "\(name): \(identifier) \(badge.frame) must stay on the 3D view \(model.frame)")
                } else {
                    XCTAssertFalse(model.frame.intersects(badge.frame), "\(name): \(identifier) \(badge.frame) covers the 3D view \(model.frame)")
                }
            }
            attach(app, name: name)
            app.terminate()
        }
    }

    /// "Share scan" opens the system share sheet with the scan file.
    @MainActor
    func testShareScanOpensTheShareSheet() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "uploading", "-uiDemoRejected"]
        app.launch()
        // The sheet must not outlive this test: left open, it stayed over the next test's launch
        // for about 16 s and that test never found its button (run 37121918073). Terminating waits
        // until the app, and with it the sheet, is gone.
        defer { app.terminate() }
        tap(app, "action.shareScan")
        let sheet = app.otherElements["ActivityListView"]
        let found = sheet.waitForExistence(timeout: 10)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "shareSheet"
        shot.lifetime = .keepAlways
        add(shot)
        if !found {
            add(XCTAttachment(string: app.debugDescription))
        }
        XCTAssertTrue(found, "the share sheet never appeared")
        // Close it the way a person would before the app goes.
        sheet.swipeDown()
        _ = sheet.waitForNonExistence(timeout: 5)
    }

    @MainActor
    private func check(_ name: String, arguments: [String], screen: String) throws {
        let app = XCUIApplication()
        // Ordinary fixtures must not inherit a Practice switch saved by another test.
        let practice = arguments.contains("-practiceMeter") ? [] : ["-practiceMeter", "NO"]
        app.launchArguments = practice + ["-uiDemo", "-uiDemoFreeze"] + arguments
        app.launch()
        defer { app.terminate() }
        guard element(app, "screen.\(screen)").waitForExistence(timeout: 15) else {
            XCTFail("\(name): screen.\(screen) never appeared")
            return
        }
        if let route = Self.navigation[name.hasSuffix("-AX5") ? String(name.dropLast(4)) : name] {
            for identifier in route.taps { tap(app, identifier) }
            // Across, not down: at AX5 the element can sit below the fold of its page.
            let window = app.windows.firstMatch.frame
            let deadline = Date().addingTimeInterval(10)
            var arrived = false
            repeat {
                if let frame = ElementRead.snapshot(element(app, route.shows))?.frame,
                   frame.minX >= window.minX, frame.maxX <= window.maxX {
                    arrived = true
                } else {
                    Thread.sleep(forTimeInterval: 0.25)
                }
            } while !arrived && Date() < deadline
            guard arrived else {
                XCTFail("\(name): \(route.shows) never slid into view")
                return
            }
            // The page slides in; let it settle before the screenshot and the audit.
            Thread.sleep(forTimeInterval: 0.5)
        }
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
        if Self.shareStates.contains(where: { name == $0 || name == "\($0)-AX5" }) {
            // The result keeps Share scan under Details.
            if screen == "result" { tap(app, "result.details") }
            XCTAssertTrue(element(app, "action.shareScan").waitForExistence(timeout: 5), "\(name): Share scan is missing")
        }
        // An AX5 state takes its own entry where it has one, else the default size's.
        for expected in Self.expectations[name] ?? Self.expectations[name.hasSuffix("-AX5") ? String(name.dropLast(4)) : name] ?? [] {
            let found: Bool
            if let identifier = expected.identifier {
                let target = ElementRead.snapshot(element(app, identifier))
                found = target.map { $0.label.contains(expected.text) || ($0.value as? String)?.contains(expected.text) == true } ?? false
            } else {
                found = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", expected.text)).firstMatch.exists
            }
            XCTAssertTrue(found, "\(name): \"\(expected.text)\" is missing")
        }
        for identifier in Self.controls[name.hasSuffix("-AX5") ? String(name.dropLast(4)) : name] ?? [] {
            XCTAssertTrue(element(app, identifier).exists, "\(name): \(identifier) is missing")
        }
        // A system banner can slide over the app mid-audit (CI's Simulator showed "Ready for Apple
        // Intelligence" over the photo count), so an issue fails the test only when a second
        // audit, after the banner's few seconds on screen, finds it again.
        let outcome = try AccessibilityAudit.run(app) { first in
            Thread.sleep(forTimeInterval: 6)
            revealCutOff(first.findings.values.compactMap(\.frame), in: app)
        }
        if !outcome.unread.isEmpty {
            let note = XCTAttachment(string: outcome.unread.joined(separator: "\n"))
            note.name = "\(name)-element-gone"
            note.lifetime = .keepAlways
            add(note)
        }
        for (_, finding) in outcome.persistent {
            XCTFail("\(name): \(finding.message)")
        }
        // After the audit, so its scrolling can't change what the audit saw: a control that
        // exists can still sit past the bottom edge, out of the homeowner's reach. At the default
        // size it must be tappable where it is; at AX5 the screen scrolls, so after scrolling to it
        // (#39: "Show my result" sits below the tape there).
        for identifier in Self.controls[name.hasSuffix("-AX5") ? String(name.dropLast(4)) : name] ?? [] {
            let target = element(app, identifier)
            guard target.exists else { continue }
            let reached = name.hasSuffix("-AX5") ? scrollUntilHittable(target, in: app) : target.isHittable
            XCTAssertTrue(reached, "\(name): \(identifier) can't be tapped")
        }
    }

    /// Drags the screen up, at most four times, until the control can be tapped. The same slow
    /// drag as `revealCutOff`, so it scrolls without momentum.
    @MainActor
    private func scrollUntilHittable(_ target: XCUIElement, in app: XCUIApplication) -> Bool {
        let step = app.windows.firstMatch.frame.height * 0.4
        for _ in 0..<4 {
            if target.isHittable { return true }
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
            start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -step)), withVelocity: .slow, thenHoldForDuration: 0.3)
        }
        return target.isHittable
    }

    /// At the largest text sizes a camera screen scrolls, and a control cut off by the bottom edge
    /// is audited on the sliver that shows, which fails contrast however it is drawn (a few
    /// points of a button's top edge over the camera). A homeowner would scroll to it, so before
    /// the second audit the screen scrolls until every flagged element that crosses the bottom
    /// edge is in full view; one that still fails there fails the test.
    @MainActor
    private func revealCutOff(_ frames: [CGRect], in app: XCUIApplication) {
        let screen = app.windows.firstMatch.frame
        guard let lowest = frames.filter({ $0.minY < screen.maxY && $0.maxY > screen.maxY }).map(\.maxY).max() else { return }
        let distance = min(lowest - screen.maxY + 60, screen.height * 0.5)
        // A slow drag from mid-screen: it scrolls by about the distance dragged, without the
        // momentum a swipe adds.
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
        start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -distance)), withVelocity: .slow, thenHoldForDuration: 0.5)
    }

    @MainActor
    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    /// Taps once the element can take the tap. Existing isn't enough: a control that has just
    /// appeared can still be moving into place (the walk's controls settle after the close-up),
    /// and a tap there misses without an error (the button flow at 577acc4 never opened the mark
    /// tray).
    ///
    /// The wait reads each button's frame and enabled state from one fresh snapshot. The old
    /// separate existence/layout/hittability waits repeatedly resolved the AR Done button,
    /// consuming its 20 s budget in PR run 37022177313 attempt 1. One wait keeps that budget.
    /// Bottom-edge controls use XCTest's ordinary scroll-to-visible tap, before hit-point queries.
    @MainActor
    private func tap(_ app: XCUIApplication, _ identifier: String, timeout: TimeInterval = 20) {
        let target = app.buttons.matching(identifier: identifier).firstMatch
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        var state = TapReadiness.State.missing
        var lastObservation = state
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            state = TapReadiness.evaluate(deadline: deadline, now: { ProcessInfo.processInfo.systemUptime }, read: {
                ElementRead.snapshot(target).map { .init(frame: $0.frame, isEnabled: $0.isEnabled) }
            }, window: { ElementRead.snapshot(app.windows.firstMatch)?.frame }, hittable: { target.isHittable })
            if state != .expired { lastObservation = state }
            return state == .ready
        }, object: nil)
        guard XCTWaiter().wait(for: [ready], timeout: timeout) == .completed else {
            XCTFail("\(identifier) not ready for tap: \(state.rawValue); last observation: \(lastObservation.rawValue)")
            return
        }
        target.tap()
    }

    /// The card's reply with these words.
    @MainActor
    private func reply(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier == 'action.cannotAccess' AND label == %@", title)).firstMatch
    }

    /// Taps the card's reply once it takes taps: a new card's reply ignores them for a moment
    /// (`InstructionCard.replyLock`), and a tap then does nothing.
    @MainActor
    private func tapReply(_ app: XCUIApplication, _ title: String) {
        let target = reply(app, title)
        XCTAssertTrue(target.waitForExistence(timeout: 10), "missing the reply \"\(title)\"")
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true AND isHittable == true"), object: target)
        XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 5), .completed, "the reply \"\(title)\" never took taps")
        target.tap()
    }
}

/// A screenshot's pixels, read in the screen's points.
private struct ScreenPixels {
    private let data: [UInt8]
    private let width: Int
    private let height: Int
    private let scale: CGFloat

    init?(_ image: UIImage) {
        guard let cgImage = image.cgImage, image.size.width > 0 else { return nil }
        let w = cgImage.width, h = cgImage.height
        var bytes = [UInt8](repeating: 0, count: w * h * 4)
        // Drawn upright into a bitmap context, the first row in memory is the image's top row.
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard drawn else { return nil }
        data = bytes
        width = w
        height = h
        scale = CGFloat(w) / image.size.width
    }

    private func pixel(_ x: Int, _ y: Int) -> (Int, Int, Int) {
        let i = (y * width + x) * 4
        return (Int(data[i]), Int(data[i + 1]), Int(data[i + 2]))
    }

    /// The first pixel in `rect`, outside `middle` points around its centre line, that differs
    /// from the pixel 2 pt inside the rect's left edge on the same row by more than a rendering
    /// tolerance. Nil when every pixel matches.
    func firstMismatch(in rect: CGRect, excludingMiddle middle: CGFloat) -> CGPoint? {
        let tolerance = 24
        let top = max(Int((rect.minY * scale).rounded(.up)), 0), bottom = min(Int((rect.maxY * scale).rounded(.down)), height)
        let left = max(Int((rect.minX * scale).rounded(.up)), 0), right = min(Int((rect.maxX * scale).rounded(.down)), width)
        guard top < bottom, left < right else { return nil }
        let rows = top..<bottom, columns = left..<right
        let skip = (rect.midX - middle / 2) * scale...(rect.midX + middle / 2) * scale
        let referenceX = min(max(Int(((rect.minX + 2) * scale).rounded()), 0), width - 1)
        for y in rows {
            let background = pixel(referenceX, y)
            for x in columns where !skip.contains(CGFloat(x)) {
                let (r, g, b) = pixel(x, y)
                if abs(r - background.0) > tolerance || abs(g - background.1) > tolerance || abs(b - background.2) > tolerance {
                    return CGPoint(x: CGFloat(x) / scale, y: CGFloat(y) / scale)
                }
            }
        }
        return nil
    }
}
