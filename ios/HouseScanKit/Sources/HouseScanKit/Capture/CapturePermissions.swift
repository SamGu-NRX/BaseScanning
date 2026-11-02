import Foundation

/// The order of the camera and Motion & Fitness prompts, kept apart from AVFoundation and Core
/// Motion so it can be tested without the system alerts. Both are asked for while the onboarding
/// that explains them is on screen; left to the AR session and the barometer, they would come up
/// over "Find your electric meter".
public enum CapturePermissions {
    /// The camera's authorization. Restricted counts as refused: the homeowner can't allow it.
    public enum Camera: Sendable, Equatable {
        case undecided, allowed, refused
    }

    /// The answer to the onboarding's Motion & Fitness request.
    public enum Motion: Sendable, Equatable {
        case allowed, denied
        /// Core Motion called back with the permission still undecided (an error, or before the
        /// alert was answered). Nothing says the alert was dealt with.
        case unanswered
        /// Nothing to ask: already decided, or this phone has no barometer.
        case notNeeded
    }

    /// What "Allow camera" on the onboarding does.
    public enum Exit: Sendable, Equatable {
        case findMeter
        /// Without the camera there is no scan: the camera failure screen, and no motion prompt.
        case cameraFailure
        /// Ask first, still on the onboarding: the camera if `camera`, then Motion & Fitness if
        /// `motion`. A camera refusal then ends at `cameraFailure`; any motion answer goes on.
        case ask(camera: Bool, motion: Bool)
    }

    /// A replay asks for nothing. Otherwise an undecided permission is asked for before the meter
    /// search, and a refused camera stops there.
    public static func exit(replay: Bool, camera: Camera, motionUndecided: Bool) -> Exit {
        if replay { return .findMeter }
        switch camera {
        case .refused: return .cameraFailure
        case .undecided: return .ask(camera: true, motion: motionUndecided)
        case .allowed: return motionUndecided ? .ask(camera: false, motion: true) : .findMeter
        }
    }

}

/// When the barometer runs during a recording. CMAltimeter raises the Motion & Fitness prompt
/// itself while the permission is undecided, so after an onboarding request that came back
/// unanswered the barometer is held rather than prompt over the meter search. While held, the app
/// keeps checking the permission; once it is decided the barometer starts in the same recording,
/// so a late answer loses only the rows before it (denied, it records nothing). With no onboarding
/// request (`answer == nil`, as on a phone without activity support) nothing is held.
public struct BarometerGate: Sendable, Equatable {
    /// The onboarding's answer.
    public var answer: CapturePermissions.Motion?
    public private(set) var recording = false
    public private(set) var running = false

    public init(answer: CapturePermissions.Motion? = nil) {
        self.answer = answer
    }

    /// Recording, with the barometer waiting for the permission: the app checks it until decided.
    public var held: Bool { recording && !running }

    /// Recording starts. Returns whether the barometer starts with it.
    public mutating func recordingStarted(undecided: Bool) -> Bool {
        recording = true
        running = !(undecided && answer == .unanswered)
        return running
    }

    /// A check of the permission while held. Returns true when the barometer starts now.
    public mutating func permissionChecked(undecided: Bool) -> Bool {
        guard held, !undecided else { return false }
        running = true
        return true
    }

    public mutating func recordingStopped() {
        recording = false
        running = false
    }
}
