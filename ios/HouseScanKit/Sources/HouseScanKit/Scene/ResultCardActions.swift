import Foundation

// Which buttons the result screen offers, from its answer and whether the camera is still up.
// Once the camera fails after the scan was sent, the answer stays but nothing that needs the
// camera is offered: not the AR view, not "Show me" on the card or on a check line, and not
// "Capture it now" in Details (#110). Tested in ResultCardActionsTests.

public enum ResultCardActions {
    /// The card's one filled button.
    public enum Primary: Equatable, Sendable {
        /// Opens the AR view. `clean` is false for a spot that might stand in the meter's working
        /// space, which the button calls the closest spot rather than the battery's spot.
        case showAR(clean: Bool)
        /// Opens the camera for the first view that settles an unsure check.
        case takeView
        /// Scans another wall.
        case startOver
    }

    /// The card's filled button, or nil for none. `hasViewToTake` is true when a view the gap
    /// planner can plan settles an unsure check; `sourceAvailable` is false once the camera failed
    /// after the scan was sent (`ScanViewState.spatialResultAvailable`).
    public static func primary(answer: ResultReading.Answer, hasSpot: Bool, spotIsClean: Bool,
                               hasViewToTake: Bool, sourceAvailable: Bool) -> Primary? {
        switch answer {
        case .candidate:
            return hasSpot && sourceAvailable ? .showAR(clean: true) : nil
        case .oneMoreLook:
            return offersView(capturable: hasViewToTake, sourceAvailable: sourceAvailable) ? .takeView : nil
        case .installer:
            return hasSpot && sourceAvailable ? .showAR(clean: spotIsClean) : nil
        case .notHere:
            // Starting over starts a new camera session, so it stays on offer after a failure.
            return .startOver
        }
    }

    /// True when a view the camera can take (`capturable`) is offered as a button: on a check
    /// line, as the card's "Show me", or as "Capture it now" in Details. Never once the camera
    /// failed: the button would open a capture with no frames coming.
    public static func offersView(capturable: Bool, sourceAvailable: Bool) -> Bool {
        capturable && sourceAvailable
    }

    /// True when a check's line offers the camera for the view that settles it: an UNSURE check
    /// no person has to judge, whose settling view the server named (`settlingViewCapturable` is
    /// nil when it named none) and the app can take now (`offersView`). The line's "Show me" and
    /// its words both read this, so a line says one more photo would settle it only when it lets
    /// the homeowner take that photo. A view the homeowner couldn't get to, or a request the app
    /// can't plan, is not capturable.
    public static func lineOffersView(
        outcome: PlacementOutcome, needsPerson: Bool, settlingViewCapturable: Bool?, sourceAvailable: Bool
    ) -> Bool {
        outcome == .unsure && !needsPerson
            && offersView(capturable: settlingViewCapturable == true, sourceAvailable: sourceAvailable)
    }
}
