import HouseScanKit
import Testing

/// The onboarding's permission requests and what they allow later.
@Suite struct CapturePermissionsTests {
    @Test func aReplayAsksForNothing() {
        #expect(CapturePermissions.exit(replay: true, camera: .undecided, motionUndecided: true) == .findMeter)
        #expect(CapturePermissions.exit(replay: true, camera: .refused, motionUndecided: true) == .findMeter)
    }

    /// Without the camera there is no scan, so Motion & Fitness is not asked for.
    @Test func aRefusedCameraShowsTheFailureWithoutAskingForMotion() {
        #expect(CapturePermissions.exit(replay: false, camera: .refused, motionUndecided: true) == .cameraFailure)
        #expect(CapturePermissions.exit(replay: false, camera: .refused, motionUndecided: false) == .cameraFailure)
    }

    @Test func undecidedPermissionsAreAskedForBeforeTheMeterSearch() {
        #expect(CapturePermissions.exit(replay: false, camera: .undecided, motionUndecided: true) == .ask(camera: true, motion: true))
        #expect(CapturePermissions.exit(replay: false, camera: .undecided, motionUndecided: false) == .ask(camera: true, motion: false))
        #expect(CapturePermissions.exit(replay: false, camera: .allowed, motionUndecided: true) == .ask(camera: false, motion: true))
        #expect(CapturePermissions.exit(replay: false, camera: .allowed, motionUndecided: false) == .findMeter)
    }

    /// An unanswered request must not lead to the barometer raising the prompt over the meter
    /// search.
    @Test func anUnansweredMotionRequestHoldsTheBarometer() {
        var gate = BarometerGate(answer: .unanswered)
        let started = gate.recordingStarted(undecided: true)
        #expect(!started)
        #expect(gate.held)
        let startsNow = gate.permissionChecked(undecided: true)
        #expect(!startsNow)
        #expect(gate.held)
    }

    /// Decided during the recording, the held barometer starts in that recording, once.
    @Test func aHeldBarometerStartsMidRecordingOnceDecided() {
        var gate = BarometerGate(answer: .unanswered)
        _ = gate.recordingStarted(undecided: true)
        let startsNow = gate.permissionChecked(undecided: false)
        #expect(startsNow)
        #expect(gate.running && !gate.held)
        let startsAgain = gate.permissionChecked(undecided: false)
        #expect(!startsAgain)
    }

    /// Stopped while held, nothing starts; the next recording checks again.
    @Test func aStoppedRecordingStartsNothingAndTheNextOneChecksAgain() {
        var gate = BarometerGate(answer: .unanswered)
        _ = gate.recordingStarted(undecided: true)
        gate.recordingStopped()
        #expect(!gate.held)
        let startsNow = gate.permissionChecked(undecided: false)
        #expect(!startsNow)
        let started = gate.recordingStarted(undecided: false)
        #expect(started)
    }

    @Test func otherAnswersStartTheBarometerWithTheRecording() {
        for answer: CapturePermissions.Motion in [.allowed, .denied, .notNeeded] {
            var gate = BarometerGate(answer: answer)
            let started = gate.recordingStarted(undecided: false)
            #expect(started)
            #expect(!gate.held)
        }
        // Never asked (no activity support): the barometer's own prompt is the only one.
        var gate = BarometerGate()
        let started = gate.recordingStarted(undecided: true)
        #expect(started)
        #expect(!gate.held)
    }
}
