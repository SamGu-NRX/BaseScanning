import CryptoKit
import Foundation
import XCTest

/// B-12: a past_end request (the walk stopped at an end, and the check asks to walk on past it)
/// offers the walk's own "Wall ends here" for that end, with the circle, the refusal when the
/// circle isn't on the wall, and the end question; "I can't get there" stays the honest decline.
///
/// Two kinds of test, which prove different things. The frozen demo (`-uiDemoGap pastEnd`) shows
/// the screen's states for the captures and checks what is offered, at the default and the
/// largest text size. The replay runs the real engine with the test answering its uploads
/// (`-answersFromGate`), and checks what marking again or declining does to the scene the next
/// upload sends and to the packet's marks and guidance. There the autopilot marks the end with a
/// tap where the replay shows the place (`-autopilotMarkPastEnd`), through the same `EndAim` check
/// as the circle, but no camera is aimed: aiming a phone at a real wall is not tested here.
final class PastEndRecoveryUITests: XCTestCase {
    private static let largestText = ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]

    override func setUp() {
        continueAfterFailure = false
    }

    // MARK: The screen (demo engine)

    @MainActor
    func testAPastEndRequestOffersWallEndsHere() throws {
        try offersWallEndsHere(textSize: [], name: "gapRequest-pastEnd")
    }

    @MainActor
    func testAPastEndRequestOffersWallEndsHereAtLargestTextSize() throws {
        try offersWallEndsHere(textSize: Self.largestText, name: "gapRequest-pastEnd-AX5")
    }

    @MainActor
    func testAPastEndRequestOnTheLeftKeepsTheCircleOpenAtLargestTextSize() throws {
        let app = launch(["-uiDemoGap", "pastEndLeft"] + Self.largestText)
        let card = label(app, "instruction")
        XCTAssertTrue(card.contains("Keep walking left"), "the folded lead names the side: \(card)")
        let mark = app.buttons["action.markEnd"]
        XCTAssertTrue(mark.waitForExistence(timeout: 5))
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable, "Wall ends here can't be reached")
        assertEndCircleOpen(app, folded: true, reply: "action.skipGap", covers: Self.endCovers, "left past-end, AX5")
        snap(app, "gapRequest-pastEndLeft-AX5")
    }

    @MainActor
    private func offersWallEndsHere(textSize: [String], name: String) throws {
        let folded = !textSize.isEmpty
        let app = launch(["-uiDemoGap", "pastEnd"] + textSize)
        let card = label(app, "instruction")
        if folded {
            // Folded around the circle: walking on leads; the request's words, the eyebrow and
            // the way to mark the end are under Details.
            XCTAssertTrue(card.contains("Keep walking right"), "card reads: \(card)")
            XCTAssertFalse(card.contains("If the wall stops sooner"), "the how-to words must fold under Details: \(card)")
            XCTAssertFalse(card.contains("One more view to finish"), "the eyebrow must fold under Details: \(card)")
        } else {
            XCTAssertTrue(card.contains("One more view to finish"), "card reads: \(card)")
            XCTAssertTrue(card.contains("Keep walking past the right end"), "the request's own words: \(card)")
            XCTAssertTrue(card.contains("If the wall stops sooner, aim where it stops and tap Wall ends here."), "card reads: \(card)")
        }
        XCTAssertTrue(element(app, "action.skipGap").exists, "\"I can't get there\" stays the decline")
        let mark = app.buttons["action.markEnd"]
        XCTAssertTrue(mark.waitForExistence(timeout: 5), "a past-end request must offer Wall ends here")
        XCTAssertEqual(mark.label, "Wall ends here")
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable, "Wall ends here can't be reached")
        // Where "Wall ends here" can be pressed, the circle it marks at is in view.
        assertEndCircleOpen(app, folded: folded, reply: "action.skipGap", covers: Self.endCovers, name)
        snap(app, name)
        guard folded else { return }

        // Details opens every word of the unfolded card, and closes back to the open circle.
        scrollToTop(app)
        let details = app.buttons["instruction.details"]
        XCTAssertTrue(details.waitForExistence(timeout: 5), "the folded card needs Details")
        tapWhenReady(details)
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5), "Details must open the folded words")
        for words in ["One more view to finish.", "Show the ground", "Keep walking past the right end", "If the wall stops sooner, aim where it stops and tap Wall ends here."] {
            XCTAssertTrue(detail.label.contains(words), "Details must hold \"\(words)\": \(detail.label)")
        }
        snap(app, "\(name)-details")
        tapWhenReady(details)
        XCTAssertTrue(waitForAbsence(detail), "Details must close again")
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable)
        assertEndCircleOpen(app, folded: true, reply: "action.skipGap", covers: Self.endCovers, "\(name), Details closed")
        // The decline moved from the card to the actions, and stays within reach.
        let decline = app.buttons["action.skipGap"]
        scrollIntoView(decline, in: app)
        XCTAssertTrue(decline.isHittable, "I can't get there can't be reached")
        XCTAssertEqual(decline.label, "I can't get there")
        XCTAssertGreaterThan(decline.frame.minY, mark.frame.maxY - 0.5, "the decline sits under Wall ends here")
    }

    /// Coaching riding along leads the folded card in a few words, and its whole note opens
    /// Details: under the lead, the dark note's lines reached the circle.
    @MainActor
    func testCoachingOnAFoldedPastEndCardKeepsTheCircleOpen() throws {
        let app = launch(["-uiDemoGap", "pastEnd", "-uiDemoCoaching", "tooDark"] + Self.largestText)
        let card = label(app, "instruction")
        XCTAssertTrue(card.contains("Try your flashlight"), "card reads: \(card)")
        XCTAssertFalse(card.contains("Try your phone's flashlight"), "the coaching's note must fold under Details: \(card)")
        let mark = app.buttons["action.markEnd"]
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable)
        assertEndCircleOpen(app, folded: true, reply: "action.skipGap", covers: Self.endCovers, "coached past-end, AX5")
        snap(app, "gapRequest-pastEnd-tooDark-AX5")
        scrollToTop(app)
        tapWhenReady(app.buttons["instruction.details"])
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5))
        for words in ["It's dark here. Try your phone's flashlight", "Keep walking past the right end", "If the wall stops sooner"] {
            XCTAssertTrue(detail.label.contains(words), "Details must hold \"\(words)\": \(detail.label)")
        }
    }

    /// The circle on the other side of the meter, on either side: the folded card leads with
    /// which end to face, and the circle stays open.
    @MainActor
    func testTheOtherSideRefusalFoldsOnBothSides() throws {
        for (gap, asked, landed) in [("pastEnd", "right", "left"), ("pastEndLeft", "left", "right")] {
            let app = launch(["-uiDemoGap", gap, "-uiDemoEndMarkOtherSide"] + Self.largestText)
            let card = label(app, "instruction")
            XCTAssertTrue(card.contains("Face the \(asked) end"), "\(gap): card reads: \(card)")
            let mark = app.buttons["action.markEnd"]
            scrollIntoView(mark, in: app)
            XCTAssertTrue(mark.isHittable)
            assertEndCircleOpen(app, folded: true, reply: "action.skipGap", covers: Self.endCovers, "\(gap) other side, AX5")
            snap(app, "gapRequest-\(gap)-otherSide-AX5")
            scrollToTop(app)
            tapWhenReady(app.buttons["instruction.details"])
            let detail = element(app, "instruction.detail")
            XCTAssertTrue(detail.waitForExistence(timeout: 5))
            XCTAssertTrue(detail.label.contains("That's the \(landed) side of your meter. Turn to the \(asked) end"), "\(gap): Details reads: \(detail.label)")
            app.terminate()
        }
    }

    /// What may cover the circle: the actions and the card's reply. The card itself is checked by
    /// its bottom edge (`assertEndCircleOpen`).
    private static let endCovers = ["action.markEnd", "action.skipGap", "action.showResult", "action.endCorner", "action.endBlocked", "action.endEnds"]

    /// The circle off the wall: the card says why and what to do, the decline stays, and pressing
    /// again with the circle still off the wall refuses again rather than marking anything.
    @MainActor
    func testARefusedEndOnARequestSaysWhatToDo() throws {
        try refusal(textSize: [], name: "gapRequest-pastEnd-refused")
    }

    @MainActor
    func testARefusedEndOnARequestSaysWhatToDoAtLargestTextSize() throws {
        try refusal(textSize: Self.largestText, name: "gapRequest-pastEnd-refused-AX5")
    }

    @MainActor
    private func refusal(textSize: [String], name: String) throws {
        let folded = !textSize.isEmpty
        let app = launch(["-uiDemoGap", "pastEnd", "-uiDemoEndMarkRefusal"] + textSize)
        // Folded, the correction leads and the reason goes under Details.
        let says = folded ? "Aim at the wall" : "The circle isn't on the wall"
        var card = label(app, "instruction")
        XCTAssertTrue(card.contains(says), "card reads: \(card)")
        if folded {
            XCTAssertFalse(card.contains("The circle isn't on the wall"), "the reason must fold under Details: \(card)")
        } else {
            XCTAssertTrue(card.contains("Aim it at the wall where it stops or turns"), "card reads: \(card)")
        }
        XCTAssertTrue(element(app, "action.skipGap").exists)
        let mark = app.buttons["action.markEnd"]
        scrollIntoView(mark, in: app)
        XCTAssertTrue(mark.isHittable)
        assertEndCircleOpen(app, folded: folded, reply: "action.skipGap", covers: Self.endCovers, name)
        snap(app, name)
        tapWhenReady(mark)
        card = label(app, "instruction")
        XCTAssertTrue(card.contains(says), "a second press marked something: \(card)")
        XCTAssertFalse(element(app, "action.endBlocked").exists, "no end was marked, so nothing to ask")
        guard folded else { return }
        scrollToTop(app)
        tapWhenReady(app.buttons["instruction.details"])
        let detail = element(app, "instruction.detail")
        XCTAssertTrue(detail.waitForExistence(timeout: 5))
        XCTAssertTrue(detail.label.contains("The circle isn't on the wall. Aim it at the wall where it stops or turns, then tap Wall ends here."), "Details reads: \(detail.label)")
    }

    /// "Wall ends here" asks the walk's end question; it replaces the request and its decline
    /// until answered, and "Something blocks it" settles the request, which goes on to the check.
    @MainActor
    func testMarkingTheEndAsksWhatIsThereAndSettlesTheRequest() throws {
        try question(textSize: [], name: "gapRequest-pastEnd-question")
    }

    @MainActor
    func testMarkingTheEndAsksWhatIsThereAtLargestTextSize() throws {
        try question(textSize: Self.largestText, name: "gapRequest-pastEnd-question-AX5")
    }

    @MainActor
    private func question(textSize: [String], name: String) throws {
        let app = launch(["-uiDemoGap", "pastEnd"] + textSize)
        let mark = app.buttons["action.markEnd"]
        scrollIntoView(mark, in: app)
        tapWhenReady(mark)
        let asked = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == 'instruction' AND label CONTAINS %@", "What's at the right end?")).firstMatch
        XCTAssertTrue(asked.waitForExistence(timeout: 5), "marking the end must ask what is there")
        XCTAssertFalse(element(app, "action.skipGap").exists, "the question replaces the decline until answered")
        XCTAssertFalse(element(app, "action.markEnd").exists)
        for answer in ["action.endCorner", "action.endBlocked", "action.endEnds"] {
            XCTAssertTrue(app.buttons[answer].exists, "\(answer) missing")
        }
        // In their order, and the question needs no circle.
        let tops = ["action.endCorner", "action.endBlocked", "action.endEnds"].map { app.buttons[$0].frame.minY }
        XCTAssertEqual(tops, tops.sorted(), "the answers moved out of order: \(tops)")
        XCTAssertFalse(element(app, "aim.circle").exists, "the question shows no circle")
        snap(app, name)
        let blocked = app.buttons["action.endBlocked"]
        scrollIntoView(blocked, in: app)
        tapWhenReady(blocked)
        XCTAssertTrue(element(app, "screen.uploading").waitForExistence(timeout: 10), "the answer must settle the request")
    }

    /// "It turns a corner" can't settle a past-end request, which can't follow the corner: no
    /// check mark, no "Got it, thanks", straight on to the check, as "I can't get there" goes.
    @MainActor
    func testACornerAtThePastEndGoesOnWithoutSettlingIt() throws {
        let app = launch(["-uiDemoGap", "pastEnd"])
        let mark = app.buttons["action.markEnd"]
        scrollIntoView(mark, in: app)
        tapWhenReady(mark)
        let corner = app.buttons["action.endCorner"]
        XCTAssertTrue(corner.waitForExistence(timeout: 5))
        scrollIntoView(corner, in: app)
        tapWhenReady(corner)
        XCTAssertFalse(label(app, "instruction").contains("Got it, thanks"), "a corner settled the request")
        XCTAssertTrue(element(app, "screen.uploading").waitForExistence(timeout: 10), "the corner must move on to the check")
    }

    /// Only a past-end request offers the end: the phone's own request and the server's other
    /// kinds keep their own controls.
    @MainActor
    func testOtherRequestsDoNotOfferTheEnd() throws {
        for gap in [[], ["-uiDemoGap", "groundOut"], ["-uiDemoGap", "walkOut"], ["-uiDemoGap", "overhead"]] {
            let app = launch(gap)
            XCTAssertFalse(app.buttons["action.markEnd"].exists, "\(gap) offers Wall ends here")
            XCTAssertFalse(label(app, "instruction").contains("Wall ends here"), "\(gap) mentions Wall ends here")
            app.terminate()
        }
    }

    // MARK: The real engine (replay, answer gate)

    /// The check asks to walk past the left end. The autopilot marks it again nearer than before
    /// and answers "Something blocks it". The next upload's scene has the shorter left bound as a
    /// limit; the request's guidance is met and the walk's own "mark the end" stays as it was; the
    /// packet's left end is the homeowner's mark, with its time and no inferred flag.
    @MainActor
    func testMarkingThePastEndAgainShortensTheWallOnTheRealEngine() throws {
        let run = try runReplay(markPastEnd: true)
        let mark = try XCTUnwrap(run.pastEndMark, "the autopilot left no past-end-mark.json")
        let marked = try XCTUnwrap(mark["s"] as? Double), cleared = try XCTUnwrap(mark["cleared_s"] as? Double)
        XCTAssertEqual(mark["side"] as? String, "left")
        XCTAssertGreaterThan(marked, cleared, "the end was meant to come nearer than the cleared one")

        let first = try XCTUnwrap(run.scenes.first), last = try XCTUnwrap(run.scenes.last)
        XCTAssertGreaterThanOrEqual(run.scenes.count, 2)
        XCTAssertEqual(try Self.endKind(last, "left"), "limit")
        XCTAssertEqual(try Self.leftEndFeet(last), marked * Self.feetPerMeter, accuracy: 0.2, "the scene's left bound is the mark")
        XCTAssertGreaterThan(try Self.leftEndFeet(last), try Self.leftEndFeet(first) + 1, "the left bound came nearer")
        XCTAssertEqual(try Self.endKind(last, "right"), try Self.endKind(first, "right"), "the right side changed")
        // Wall seen past the nearer end is not reported. Ground past it is, now that it is a limit
        // (server README, "Ends and corners"): that is ground seen, not wall claimed.
        XCTAssertTrue(try Self.observedLeftEdges(last, band: "wall").allSatisfy { $0 >= marked * Self.feetPerMeter - 0.05 }, "wall reported past the nearer end")

        let packet = try XCTUnwrap(run.packet, "no packet-marks-guidance.json")
        let left = try XCTUnwrap(Self.wallEnd(packet, "left"))
        XCTAssertNotNil(left["t"], "a homeowner's mark keeps its time")
        XCTAssertNil((left["attrs"] as? [String: Any])?["inferred"], "a homeowner's mark is not inferred")
        XCTAssertEqual(left["end_kind"] as? String, "limit")
        let pastEnd = Self.guidance(packet, kind: "gap_past_end")
        XCTAssertEqual(pastEnd.last?["outcome"] as? String, "met", "\(pastEnd)")
        attach(run)
    }

    /// The check asks to walk past the left end; the replay can't show it, and the autopilot says
    /// "I can't get there". The next upload's scene has the left end back where it was, with its
    /// kind, and claims nothing past it; the request is cannot_reach, and the packet's left end
    /// keeps the source and time it had.
    @MainActor
    func testDecliningThePastEndRestoresTheEndOnTheRealEngine() throws {
        let run = try runReplay(markPastEnd: false)
        XCTAssertNil(run.pastEndMark)
        XCTAssertGreaterThanOrEqual(run.scenes.count, 2)
        let first = try XCTUnwrap(run.scenes.first), last = try XCTUnwrap(run.scenes.last)
        XCTAssertEqual(try Self.leftEndFeet(last), try Self.leftEndFeet(first), accuracy: 0.01, "the left end moved")
        XCTAssertEqual(try Self.endKind(last, "left"), try Self.endKind(first, "left"))
        let bound = try Self.leftEndFeet(last)
        // Past an unexplored end nothing is reported; past a limit only ground is (the autopilot's
        // fallback marks a limit when no frame shows the end).
        let pastBand: String? = try Self.endKind(last, "left") == "limit" ? "wall" : nil
        XCTAssertTrue(try Self.observedLeftEdges(last, band: pastBand).allSatisfy { $0 >= bound - 0.05 }, "coverage claimed past the restored end")

        let packet = try XCTUnwrap(run.packet, "no packet-marks-guidance.json")
        let left = try XCTUnwrap(Self.wallEnd(packet, "left"))
        let firstPacketLeft = try XCTUnwrap(run.firstPacketLeftEnd, "the first upload's packet had no left end")
        XCTAssertEqual(left["t"] as? Double, firstPacketLeft["t"] as? Double, "the restored end lost or changed its time")
        XCTAssertEqual((left["attrs"] as? [String: Any])?["inferred"] as? Bool, (firstPacketLeft["attrs"] as? [String: Any])?["inferred"] as? Bool)
        let pastEnd = Self.guidance(packet, kind: "gap_past_end")
        XCTAssertEqual(pastEnd.last?["outcome"] as? String, "cannot_reach", "\(pastEnd)")
        attach(run)
    }

    /// The check asks to walk past the left end. The autopilot marks it again nearer and answers
    /// "It turns a corner", which the request can't follow. The next answer asks again to walk
    /// past the left end, now from the corner, and also for ground on the right. The repeat is not
    /// raised; the ground request is. The corner stays the homeowner's unexplored end, nothing past
    /// it is reported, and the request closed cannot_reach, not met.
    @MainActor
    func testACornerAtThePastEndIsNotAskedForAgainOnTheRealEngine() throws {
        let run = try runReplay(markPastEnd: true, corner: true)
        let mark = try XCTUnwrap(run.pastEndMark, "the autopilot left no past-end-mark.json")
        let marked = try XCTUnwrap(mark["s"] as? Double), cleared = try XCTUnwrap(mark["cleared_s"] as? Double)
        XCTAssertEqual(mark["side"] as? String, "left")
        XCTAssertGreaterThan(marked, cleared, "the end was meant to come nearer than the cleared one")

        // The first upload, one after the corner, one after the ground request.
        XCTAssertGreaterThanOrEqual(run.scenes.count, 3, run.log)
        let last = try XCTUnwrap(run.scenes.last)
        XCTAssertEqual(try Self.endKind(last, "left"), "unexplored")
        XCTAssertEqual(try Self.leftEndFeet(last), marked * Self.feetPerMeter, accuracy: 0.2, "the scene's left bound is the corner")
        XCTAssertTrue(try Self.observedLeftEdges(last, band: nil).allSatisfy { $0 >= marked * Self.feetPerMeter - 0.05 }, "coverage claimed past the corner")

        let packet = try XCTUnwrap(run.packet, "no packet-marks-guidance.json")
        let left = try XCTUnwrap(Self.wallEnd(packet, "left"))
        XCTAssertNotNil(left["t"], "the corner is the homeowner's mark, with its time")
        XCTAssertNil((left["attrs"] as? [String: Any])?["inferred"], "the corner is not inferred")
        XCTAssertEqual(left["end_kind"] as? String, "unexplored")
        let pastEnd = Self.guidance(packet, kind: "gap_past_end")
        XCTAssertEqual(pastEnd.count, 1, "the repeated past_end was raised again: \(pastEnd)")
        XCTAssertEqual(pastEnd.first?["outcome"] as? String, "cannot_reach", "\(pastEnd)")
        // The phone's own requests are gap_band too; the server's are the ones in its answers.
        let serverBand = Self.guidance(packet, kind: "gap_band").filter { $0["origin"] as? String == "server" }
        XCTAssertEqual(serverBand.count, 1, "the ground request in the same answer was not raised: \(serverBand)")
        attach(run)
    }

    private struct ReplayRun {
        var scenes: [[String: Any]]
        var packet: [String: Any]?
        var firstPacketLeftEnd: [String: Any]?
        var pastEndMark: [String: Any]?
        var log: String
    }

    /// Plays the synthetic wall with the test as the placement server. The first answer is the
    /// bundled sample, whose missing evidence asks to walk past the left end; later answers drop
    /// that item, so the request is raised once. Returns every scene uploaded, in order, and the
    /// packet projection and mark record the autopilot leaves at the result.
    ///
    /// With `corner`, the autopilot answers "It turns a corner", and the second answer asks again
    /// to walk past the left end together with the sample's ground request on the right.
    @MainActor
    private func runReplay(markPastEnd: Bool, corner: Bool = false) throws -> ReplayRun {
        let sample = String(decoding: try Data(contentsOf: UploadRecoveryUITests.sampleResult), as: UTF8.self)
        let zeros = String(repeating: "0", count: 64)
        XCTAssertTrue(sample.contains("\"input_sha256\": \"\(zeros)\""))
        // The first answer asks only to walk past the left end, so that request is the one raised;
        // the sample's ground request ahead of it would take the first upload. Later answers ask
        // for nothing, so the request is raised once.
        var answer = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(sample.utf8)) as? [String: Any])
        let missing = (answer["missing_evidence"] as? [[String: Any]]) ?? []
        let pastEnd = missing.filter { $0["kind"] as? String == "past_end" && $0["side"] as? String == "left" }
        XCTAssertEqual(pastEnd.count, 1, "the sample no longer asks to walk past the left end")
        answer["missing_evidence"] = pastEnd
        let onlyPastEnd = String(decoding: try JSONSerialization.data(withJSONObject: answer, options: [.sortedKeys]), as: UTF8.self)
        let ground = missing.filter { $0["kind"] as? String == "band" && $0["band"] as? String == "ground" }
        XCTAssertEqual(ground.count, 1, "the sample no longer asks for ground")
        answer["missing_evidence"] = pastEnd + ground
        let pastEndAgain = String(decoding: try JSONSerialization.data(withJSONObject: answer, options: [.sortedKeys]), as: UTF8.self)
        answer["missing_evidence"] = [[String: Any]]()
        let nothingMissing = String(decoding: try JSONSerialization.data(withJSONObject: answer, options: [.sortedKeys]), as: UTF8.self)
        XCTAssertTrue(onlyPastEnd.contains("\"input_sha256\":\"\(zeros)\""), "the binding placeholder moved")

        let files = FileManager.default
        let gate = URL(fileURLWithPath: NSTemporaryDirectory()).appending(path: "housescan-gate-\(UUID().uuidString)", directoryHint: .isDirectory)
        try files.createDirectory(at: gate, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: gate) }
        // Every screen up to the result is let through; the result's stays shut, so the autopilot
        // stops there with its files written.
        for phase in ["onboarding", "findMeter", "meterCloseUp", "wallWalk", "markFeatures", "gapRequest", "uploading", "spotConfirm"] {
            try Data().write(to: gate.appending(path: phase))
        }
        let server = GateServer(gate: gate) { body, index in
            let template = switch index {
            case 0: onlyPastEnd
            case 1 where corner: pastEndAgain
            default: nothingMissing
            }
            return Data(template.replacingOccurrences(of: zeros, with: UploadRecoveryUITests.sha256(body)).utf8)
        }
        defer { server.stop() }

        let app = XCUIApplication()
        app.launchArguments = [
            "-replay", FullFlowUITests.fixture, "-autopilot", "-autopilotHold", "1.0", "-autopilotGate", gate.path,
            "-practiceMeter", "NO", "-serverURL", "http://placement.invalid", "-answersFromGate",
        ] + (markPastEnd ? ["-autopilotMarkPastEnd"] : []) + (corner ? ["-autopilotPastEndCorner"] : [])
        app.launch()
        defer { app.terminate() }
        let any = app.descendants(matching: .any)
        XCTAssertTrue(any["screen.result"].waitForExistence(timeout: 600), "the flow never reached the result")
        let sceneFile = gate.appending(path: "scene.json")
        let deadline = Date().addingTimeInterval(30)
        while !files.fileExists(atPath: sceneFile.path), Date() < deadline { Thread.sleep(forTimeInterval: 0.5) }
        let requests = server.requests.filter { $0.target == "POST /v1/placements" }
        let scenes = try requests.map { try XCTUnwrap(JSONSerialization.jsonObject(with: $0.body) as? [String: Any]) }
        func json(_ name: String) -> [String: Any]? {
            (try? Data(contentsOf: gate.appending(path: name))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        }
        // The packet of the upload that raised the past-end request, which the autopilot leaves as
        // `packet-first.json` when the request shows: its left end is the walk's own.
        let first = json("packet-first.json").flatMap { Self.wallEnd($0, "left") }
        return ReplayRun(
            scenes: scenes, packet: json("packet-marks-guidance.json"), firstPacketLeftEnd: first,
            pastEndMark: json("past-end-mark.json"), log: requests.map(\.target).joined(separator: "\n"))
    }

    // MARK: Reading scenes and packets

    private static let feetPerMeter = 3.280839895

    /// The left end of the first wall's baseline, in feet along it from the meter (negative left).
    private static func leftEndFeet(_ scene: [String: Any]) throws -> Double {
        let walls = try XCTUnwrap(scene["walls"] as? [[String: Any]])
        let baseline = try XCTUnwrap(walls.first?["baseline"] as? [[Double]])
        let meter = try XCTUnwrap((scene["meter"] as? [String: Any])?["pos"] as? [Double])
        let first = try XCTUnwrap(baseline.first), last = try XCTUnwrap(baseline.last)
        let length = hypot(last[0] - first[0], last[1] - first[1])
        return ((first[0] - meter[0]) * (last[0] - first[0]) + (first[1] - meter[2]) * (last[1] - first[1])) / length
    }

    private static func endKind(_ scene: [String: Any], _ side: String) throws -> String? {
        let coverage = try XCTUnwrap(scene["coverage"] as? [String: Any])
        return (coverage["ends"] as? [String: [String: String]])?[side]?["kind"]
    }

    /// The lower edge of every observed span of `band` (every band when nil), in feet.
    private static func observedLeftEdges(_ scene: [String: Any], band: String?) throws -> [Double] {
        let coverage = try XCTUnwrap(scene["coverage"] as? [String: Any])
        let observed = ((coverage["observed"] as? [[String: Any]]) ?? []).filter { band == nil || $0["band"] as? String == band }
        return observed.compactMap { ($0["span_ft"] as? [Double]).map { min($0[0], $0[1]) } }
    }

    private static func wallEnd(_ packet: [String: Any], _ side: String) -> [String: Any]? {
        ((packet["marks"] as? [[String: Any]]) ?? []).first { $0["kind"] as? String == "wall_end" && $0["side"] as? String == side }
    }

    private static func guidance(_ packet: [String: Any], kind: String) -> [[String: Any]] {
        ((packet["guidance"] as? [[String: Any]]) ?? []).filter { $0["kind"] as? String == kind }
    }

    // MARK: Helpers

    @MainActor
    private func attach(_ run: ReplayRun) {
        let text = "uploads: \(run.scenes.count)\npast-end mark: \(run.pastEndMark ?? [:])\npacket: \(run.packet ?? [:])"
        let attachment = XCTAttachment(string: text)
        attachment.name = "pastEnd-replay"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor
    private func launch(_ arguments: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-practiceMeter", "NO", "-uiDemo", "-uiDemoFreeze", "-uiDemoPhase", "gapRequest"] + arguments
        app.launch()
        XCTAssertTrue(element(app, "screen.gapRequest").waitForExistence(timeout: 15), "screen.gapRequest never appeared")
        XCTAssertTrue(element(app, "instruction").waitForExistence(timeout: 5))
        return app
    }

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

    @MainActor
    private func waitForAbsence(_ target: XCUIElement) -> Bool {
        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: target)
        return XCTWaiter().wait(for: [gone], timeout: 5) == .completed
    }

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
    private func tapWhenReady(_ target: XCUIElement) {
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true AND isHittable == true"), object: target)
        XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 10), .completed, "\(target) never took taps")
        target.tap()
    }
}
