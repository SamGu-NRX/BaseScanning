import SwiftUI

/// Every screen driven by a scripted fake engine, launched with `-uiDemo`.
///
/// Launch arguments (all optional), for screenshots and for trying one screen at a time:
/// - `-uiDemoPhase <phase>`: start at a phase with plausible state (`ScanPhase` raw value).
/// - `-uiDemoFreeze`: don't run the timed scripts, so the screen holds still.
/// - `-uiDemoMarking <FeatureKind raw value>`: open the walk in marking mode.
/// - `-uiDemoRefusal`: the marking shows a refusal.
/// - `-uiDemoCoaching <slowDown|needsTexture|tooDark|tooDarkToMeasure|holdSteady|turnSlowly|relocalizing|trackingLost|pastWallEnd>`.
/// - `-uiDemoCloseUpFailed`: the close-up has failed twice, so the way out shows.
/// - `-uiDemoMeterChoose`: the close-up asks which of three made-up readings is the meter number.
/// - `-uiDemoGroundQuestion`: open the feature review with the ground question unanswered.
/// - `-uiDemoGroundAnswer <GroundType raw value|notSure>`: open the feature review with the ground
///   question answered, so it shows as the folded row with Change.
/// - `-uiDemoOffline`: uploads fail offline.
/// - `-uiDemoRejected`: the server refuses the first upload; "Back to review" then sends it again.
/// - `-uiDemoUnusableAnswer <n>`: the first `n` answers can't be used (1 if `n` is missing);
///   "Try again" sends the scan again, and the answer after them is the result.
/// - `-uiDemoFailure <cameraDenied|arUnsupported|sessionFailed|replayUnreadable>`: open on the
///   unsupported screen.
/// - `-uiDemoPass`: the sample result is a pass with approved rules.
/// - `-uiDemoOverlap`: the sample result's spot overlaps the meter's working space.
/// - `-uiDemoResultFile <path>`: debug builds only. The result is the server answer in this JSON
///   file, mapped as the engine maps one; the UI tests keep such files in `Fixtures/results/`.
/// - `-uiDemoNoFeed`: no camera picture, to look at the chrome alone.
/// - `-uiDemoEndQuestion`: the walk asks what is at the left end of the wall.
/// - `-uiDemoMarkEnd`: the walk has reached the right end and asks whether the wall ends there;
///   with `-uiDemoEndMarkRefusal`, "Wall ends here" was just refused (circle off the wall).
/// - `-uiDemoCloseUpSkipped`: the meter close-up was skipped, so no saved photo shows.
/// - `-uiDemoEndPreview`: the homeowner walked back 1.5 m, so the wall map says ending the wall
///   where they stand leaves part of the walk out.
/// - `-uiDemoNextWall`: the right end turns a corner and the walk asks for the next wall; with
///   `-uiDemoRefusal` the last mark was refused, and with `-uiDemoNextWallConfirm` a wall was
///   marked and "Is this the next wall?" is up.
/// - `-uiDemoTiltUp`: both ends are marked and the walk asks to tilt up by the meter.
/// - `-uiDemoAim`: with `-uiDemoPhase wallWalk`, the walk asks to tilt down to the ground
///   about 2 ft right of the meter.
/// - `-uiDemoOverheadQuestion`: the tilt-up view is in and the walk asks what is overhead.
/// - `-uiDemoGap <groundOut|walkOut|overhead>`: the gap screen shows that server request.
/// - `-uiDemoSample`: no server is configured, so the upload screen says the result is a sample.
/// - `-uiDemoDepth`: the phone has depth, so the wall map says it is depth-checked.
/// - `-uiDemoHidden`: on a phone with depth, the walk has two stretches hidden behind something.
/// - `-uiDemoSeeBehind`: as `-uiDemoHidden`, and the walk asks to look past the one on the right.
/// - `-uiDemoAim`: with `-uiDemoPhase wallWalk`, the walk asks to tilt down at the ground right of
///   the meter, and the ring is half full with its legend beside it. Unfrozen, it fills, the walk
///   goes on in the same update as on a phone, and the ring holds green with a tick for a moment.
/// - `-uiDemoAimOffScreen`: as `-uiDemoAim`, for ground left of the view: the edge arrow shows.
/// - `-uiDemoCorner`: the wall turns an outside corner 1.8 m right of the meter and the walk
///   followed it, so the window and part of its clearance zone are round the corner. For the
///   result model: `-uiDemoPhase result -uiDemoCorner`.
/// - `-uiDemoPhase spotConfirm`: the spot check before the result, on the made-up sample spot.
/// - `-uiDemoSpotAnswered <clear|somethingThere|cannotCheck>`: with `-uiDemoPhase spotConfirm`,
///   the check is answered and says what happens next. With `-uiDemoPhase result`, the result
///   follows that answer, so it shows the answer's notice.
/// - `-uiDemoUncheckedElsewhere`: with `-uiDemoPhase result`, an earlier area of the wall was
///   answered "I can't check this area", so the result says so beside its spot's notice.
/// - `-uiDemoFollowUp`: with `-uiDemoPhase uploading` or `gapRequest`, the check has answered
///   and asked for one more view: the upload screen as it hands over, or the view itself.
/// - `-uiDemoPhase processing -uiDemoPhotoState <id>`: a photo-processing scan after it was sent,
///   in one of its states (`ProcessingCopy.Screen.id`, such as `uploading`, `candidate` or
///   `withdrawalNotRecorded`; `processing` by default), with the capture fixture's words.
/// - `-uiDemoPhotoConsent`: on a camera phase, the question whether to send the scan's photos.
///
/// Unfrozen, the demo goes back to the camera once after the first answer, as the engine does
/// when the answer lists a view the camera can take.
enum UIDemo {
    @MainActor
    static func makeRoot() -> some View {
        DemoHost()
    }
}

private struct DemoHost: View {
    @State private var engine = DemoEngine(arguments: ProcessInfo.processInfo.arguments)

    var body: some View {
        ScanRootView(state: engine.state, actions: engine)
    }
}
