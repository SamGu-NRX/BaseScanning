import HouseScanKit
import Testing

/// #110: the result card offers only what the camera can still do.
@Suite struct ResultCardActionsTests {
    private typealias Primary = ResultCardActions.Primary

    private static func primary(_ answer: ResultReading.Answer, hasSpot: Bool = true, spotIsClean: Bool = true,
                                hasViewToTake: Bool = false, sourceAvailable: Bool = true) -> Primary? {
        ResultCardActions.primary(answer: answer, hasSpot: hasSpot, spotIsClean: spotIsClean,
                                  hasViewToTake: hasViewToTake, sourceAvailable: sourceAvailable)
    }

    @Test func withTheCameraUpEachAnswerKeepsItsButton() {
        #expect(Self.primary(.candidate) == .showAR(clean: true))
        #expect(Self.primary(.oneMoreLook, hasViewToTake: true) == .takeView)
        #expect(Self.primary(.installer, spotIsClean: false) == .showAR(clean: false))
        #expect(Self.primary(.installer) == .showAR(clean: true))
        #expect(Self.primary(.notHere, hasSpot: false) == .startOver)
    }

    /// No spot, nothing to show in AR; no view to take, no "Show me".
    @Test func nothingToShowMeansNoButton() {
        #expect(Self.primary(.candidate, hasSpot: false) == nil)
        #expect(Self.primary(.installer, hasSpot: false) == nil)
        #expect(Self.primary(.oneMoreLook, hasViewToTake: false) == nil)
    }

    /// The camera failed after upload: a manual-review result with a capturable unsure check
    /// showed a filled "Show me" that opened a capture with no frames. Now it is gone, as the AR
    /// button already was. Starting over starts a new camera, so it stays.
    @Test func aFailedCameraOffersNothingThatNeedsIt() {
        #expect(Self.primary(.oneMoreLook, hasViewToTake: true, sourceAvailable: false) == nil)
        #expect(Self.primary(.candidate, sourceAvailable: false) == nil)
        #expect(Self.primary(.installer, spotIsClean: false, sourceAvailable: false) == nil)
        #expect(Self.primary(.notHere, hasSpot: false, sourceAvailable: false) == .startOver)
    }

    /// The check lines' "Show me" and Details' "Capture it now" follow the same rule.
    @Test func aViewIsOfferedOnlyWhileTheCameraCanTakeIt() {
        #expect(ResultCardActions.offersView(capturable: true, sourceAvailable: true))
        #expect(!ResultCardActions.offersView(capturable: true, sourceAvailable: false))
        #expect(!ResultCardActions.offersView(capturable: false, sourceAvailable: true))
        #expect(!ResultCardActions.offersView(capturable: false, sourceAvailable: false))
    }

    /// A check line offers its view, and may say one more photo would settle it, only for an
    /// unsure check no person has to judge, whose settling view was named and can be taken now
    /// with the camera up. On the spot-unknown run a withdrawn "Clear space in front" request
    /// (not capturable) still read "One more photo would settle this". Every combination.
    @Test(arguments: [PlacementOutcome.pass, .fail, .unsure], [false, true])
    func aLineOffersItsViewOnlyWhenTheCameraCanTakeIt(outcome: PlacementOutcome, needsPerson: Bool) {
        let capturable: [Bool?] = [nil, false, true]
        for settling in capturable {
            for sourceAvailable in [false, true] {
                let offered = ResultCardActions.lineOffersView(
                    outcome: outcome, needsPerson: needsPerson, settlingViewCapturable: settling,
                    sourceAvailable: sourceAvailable)
                let expected = outcome == .unsure && !needsPerson && settling == true && sourceAvailable
                #expect(offered == expected, "settling view \(String(describing: settling)), camera up \(sourceAvailable)")
            }
        }
    }

    /// The cases by name: the one line that offers a photo, and each reason another doesn't.
    @Test func theLinesThatOfferAPhoto() {
        let offers = { (outcome: PlacementOutcome, person: Bool, capturable: Bool?, camera: Bool) in
            ResultCardActions.lineOffersView(outcome: outcome, needsPerson: person, settlingViewCapturable: capturable,
                                             sourceAvailable: camera)
        }
        #expect(offers(.unsure, false, true, true))
        // A view the homeowner couldn't get to, or one the app can't plan.
        #expect(!offers(.unsure, false, false, true))
        // No view named for it.
        #expect(!offers(.unsure, false, nil, true))
        // The camera failed after the scan was sent.
        #expect(!offers(.unsure, false, true, false))
        // A person judges it, whatever view is named.
        #expect(!offers(.unsure, true, true, true))
        // Settled checks offer nothing.
        #expect(!offers(.pass, false, true, true))
        #expect(!offers(.fail, false, true, true))
    }
}
