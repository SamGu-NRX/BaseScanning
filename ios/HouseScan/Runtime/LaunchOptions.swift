import Foundation
import HouseScanKit
import OSLog

/// Verification hooks from the launch arguments (contract C4).
///
/// - `-replay <folder>`: play a recorded measure-lab-session v2 folder instead of the camera.
/// - `-autopilot`: drive the intents automatically (UI tests, demos).
/// - `-serverURL <url>`: the placement server to upload to. Without it the app uses the build's
///   default, Info.plist `HouseScanServerURL` (set from `HOUSESCAN_SERVER_URL` in
///   Config/Shared.xcconfig); with neither, uploads answer with the bundled sample.
/// - `-sampleResult`: answer uploads with the bundled sample result, flagged as a sample, even
///   when a server is configured. UI tests pass it to stay offline and deterministic.
/// - `-autopilotHold <seconds>`: how long the autopilot leaves each screen up (default 1.2 s).
///   UI tests raise it so each screen stays long enough to screenshot and audit.
/// - `-autopilotGate <folder>`: before the flow leaves a screen, wait until a file named after
///   that phase exists in the folder. UI tests write it once they have screenshotted and audited
///   the screen, so a slow audit can never miss a screen. While it waits, the app leaves
///   `<phase>.held` there, so a test can tell when the autopilot has finished with a screen and
///   holds it still. After the result shows, the autopilot also writes the scan's scene.json
///   there, for the test to check.
/// - `-autopilotCantGetThere`: the autopilot ends the walk with "Can't get there" instead of
///   marking the ends (`Autopilot.endWalkByCantGetThere`).
/// - `-autopilotSomethingThere`: the autopilot answers the first spot check "Something's there"
///   instead of "It's clear", so the scan is checked again without that area.
/// - `-autopilotMarkPastEnd`: on a server past_end request the autopilot marks that end again,
///   nearer than the end the request cleared, with a tap where the replay shows that place, and
///   answers "Something blocks it" (`Autopilot.markPastEnd`). Without it the autopilot plays the
///   request's frames and says "I can't get there" when they don't settle it.
/// - `-autopilotPastEndCorner`: with `-autopilotMarkPastEnd`, the autopilot answers "It turns a
///   corner" instead, which a request can't follow (`ScanEngine.answerWallEnd`).
/// - `-autopilotCannotCheck`: the autopilot answers the first spot check "I can't check this
///   area" instead of "It's clear". It can't be combined with `-autopilotSomethingThere`.
/// - `-sampleResultAfterSpotAnswer <path>` (debug builds only): with the bundled sample, the
///   upload sent after a spot check answered "Something's there" or "I can't check this area",
///   and every later upload of that scan, is answered with the server answer in this JSON file,
///   such as one without a spot. Start over or a new wall goes back to the bundled sample.
/// - `-injectGroundRise <meters>`: with `-replay`, `-autopilot` and `-autopilotGate`, once the
///   first upload starts, each time a file named `inject-ground` appears in the gate folder the
///   app deletes it and hands the engine a detected floor that many meters above its current
///   ground, as ARKit refining the ground would (`ScanEngine.injectGroundForTest`). Replays carry
///   no plane evidence, so this is the only way a UI test reaches that path. Meters must be over
///   the engine's 1 cm refine threshold and at most `GroundPlaneChoice.maximumRaise`.
/// - `-answersFromGate`: with `-autopilotGate` and a server URL, uploads go through the gate
///   folder instead of the network: the UI test reads each request there and writes the server's
///   answer (`GateAnswerProtocol`).
/// - `-failCloseUpSave`: with `-replay`, every meter close-up's photo fails to save, as on a phone
///   with no space left, so a UI test reaches the save-failure retake and the skip after it. The
///   photo is dropped before `KeyframeStore.saveStill`, which then takes its own failure path.
///   The retake reason stays up 10 s instead of 2, so the test's query can't miss it.
/// - `-simulateAppStore`: run as an App Store install would, so the developer options and practice
///   meter are unavailable whatever the stored switch says (`DeveloperSettings`). It can only take
///   the switch away, never offer it.
/// - `-processingBackend <legacy|photoProcessing>` (debug builds only): the backend choice in
///   Developer options starts at this value and stays in memory for the run, so a UI test's choice
///   never reaches the stored setting the other tests run with (`ProcessingBackendSetting`).
/// - `-photoProcessingFixture <answer>` (debug builds only, with `-replay`): photo processing sends
///   to a capture API answered inside the app (`FixtureCaptureHTTP`), which ends every capture
///   with this answer (`FixtureCaptureHTTP.Answer`). Nothing leaves the phone. Without it, photo
///   processing isn't set up in any build. A replay's own packet always fails the phone's checks,
///   since a replay has no motion.
/// - `-photoProcessingSyntheticCapture` (debug builds only, with `-photoProcessingFixture`): the
///   capture sent is HouseScanKit's `SyntheticCapture`, labelled synthetic in its packet, in place
///   of the replay's photos, so the fixture's answer comes back through the real upload. It is a
///   test of the app's path against the fixture, never a result about the wall on screen.
/// - `-photoProcessingFixtureReadOnlyCapture` (debug builds only): just before "Stop sending
///   photos" takes effect, the capture's folder is made read-only, so the phone can't save the
///   withdrawal and says so (`PhotoProcessingEnd.withdrawn(recorded: false)`).
struct LaunchOptions: Equatable {
    var replayFolder: URL?
    var autopilot = false
    var serverURL: URL?
    var sampleResult = false
    var autopilotHold: Double = 1.2
    var autopilotGate: URL?
    var autopilotCantGetThere = false
    var autopilotSomethingThere = false
    var autopilotCannotCheck = false
    var autopilotMarkPastEnd = false
    var autopilotPastEndCorner = false
    var sampleResultAfterSpotAnswer: URL?
    var simulateAppStore = false
    var injectGroundRise: Float?
    var answersFromGate = false
    var failCloseUpSave = false
    var processingBackend: ProcessingBackend?
    var photoProcessingFixture: FixtureCaptureHTTP.Answer?
    var photoProcessingSyntheticCapture = false
    var photoProcessingFixtureReadOnlyCapture = false

    init(
        arguments: [String] = ProcessInfo.processInfo.arguments,
        defaultServerURL: String? = Bundle.main.object(forInfoDictionaryKey: "HouseScanServerURL") as? String
    ) {
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        if let path = value(after: "-replay") {
            replayFolder = URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true)
        }
        autopilot = arguments.contains("-autopilot")
        autopilotCantGetThere = arguments.contains("-autopilotCantGetThere")
        autopilotSomethingThere = arguments.contains("-autopilotSomethingThere")
        autopilotCannotCheck = arguments.contains("-autopilotCannotCheck")
        autopilotMarkPastEnd = arguments.contains("-autopilotMarkPastEnd")
        autopilotPastEndCorner = arguments.contains("-autopilotPastEndCorner")
        precondition(!autopilotPastEndCorner || autopilotMarkPastEnd, "-autopilotPastEndCorner answers the end -autopilotMarkPastEnd marks; pass both")
        precondition(!(autopilotSomethingThere && autopilotCannotCheck), "-autopilotSomethingThere and -autopilotCannotCheck each choose the first spot answer; pass one")
        #if DEBUG
        sampleResultAfterSpotAnswer = value(after: "-sampleResultAfterSpotAnswer").map { URL(fileURLWithPath: $0) }
        if let text = value(after: "-processingBackend") {
            guard let backend = ProcessingBackend(rawValue: text) else {
                preconditionFailure("-processingBackend takes legacy or photoProcessing, got \(text)")
            }
            processingBackend = backend
        }
        if let text = value(after: "-photoProcessingFixture") {
            guard let answer = FixtureCaptureHTTP.Answer(rawValue: text) else {
                preconditionFailure("-photoProcessingFixture takes one of \(FixtureCaptureHTTP.Answer.allCases.map(\.rawValue)), got \(text)")
            }
            photoProcessingFixture = answer
        }
        photoProcessingSyntheticCapture = arguments.contains("-photoProcessingSyntheticCapture")
        photoProcessingFixtureReadOnlyCapture = arguments.contains("-photoProcessingFixtureReadOnlyCapture")
        #endif
        simulateAppStore = arguments.contains("-simulateAppStore")
        failCloseUpSave = arguments.contains("-failCloseUpSave")
        serverURL = (value(after: "-serverURL") ?? defaultServerURL).flatMap(Self.serverURL)
        sampleResult = arguments.contains("-sampleResult")
        if let gate = value(after: "-autopilotGate") { autopilotGate = URL(fileURLWithPath: gate, isDirectory: true) }
        if let hold = value(after: "-autopilotHold").flatMap(Double.init), hold > 0 { autopilotHold = hold }
        answersFromGate = arguments.contains("-answersFromGate")
        if answersFromGate, autopilotGate == nil || serverURL == nil {
            preconditionFailure("-answersFromGate needs -autopilotGate and a server URL (-serverURL or the build's)")
        }
        if let text = value(after: "-injectGroundRise") {
            guard let meters = Float(text), meters > 0.01, meters <= 0.1 else {
                preconditionFailure("-injectGroundRise takes meters over 0.01 and at most 0.1, got \(text)")
            }
            injectGroundRise = meters
        }
    }

    /// An http(s) URL with a host, or nil. An empty build setting leaves the plist value empty,
    /// and an unexpanded "$(HOUSESCAN_SERVER_URL)" has no scheme; both mean "no server".
    private static func serverURL(_ text: String) -> URL? {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespaces)),
              let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http",
              url.host() != nil else { return nil }
        return url
    }
}

/// Log channels. STATE lines are parsed by verification/hsverify/statelog.py, which needs them public.
enum RuntimeLog {
    static let state = Logger(subsystem: "dev.housescanning.housescan", category: "state")
    static let guidance = Logger(subsystem: "dev.housescanning.housescan", category: "guidance")
    static let engine = Logger(subsystem: "dev.housescanning.housescan", category: "engine")
    static let autopilot = Logger(subsystem: "dev.housescanning.housescan", category: "autopilot")
    /// Tracking, relocalization and every capture-gate decision, for reading a real session back.
    /// Only enum-valued reasons, frame ids and counts are public; never meter numbers or images.
    static let capture = Logger(subsystem: "dev.housescanning.housescan", category: "capture")
}
