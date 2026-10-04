import HouseScanKit

// What the result screen leads with, read from the presentation by HouseScanKit's
// `ResultReading`, where the rules are tested. Reading the presentation rather than the server's
// answer means the engine's results and the UI demo's samples go through the same rules.

extension ResultPresentation {
    /// This answer for a wall neither side of which was walked (`wallNotMeasured`, #76): the
    /// server still placed a spot on the tapped wall line, but nothing confirmed that line, so
    /// the spot, the nearest spot, the cable route, the clearances and the checks and requests
    /// made at them are dropped. The rules' notice and hash stay.
    func withWallNotMeasured() -> ResultPresentation {
        var shown = self
        shown.wallNotMeasured = true
        shown.summary = ""
        shown.spot = nil
        shown.nearestSpot = nil
        shown.nearestFailingCheck = nil
        shown.cableRoute = []
        shown.cableLength = nil
        shown.checks = []
        shown.clearances = []
        shown.missing = []
        shown.unseenEnd = nil
        return shown
    }

    /// True when there is a spot and the meter working-space check at it, if the server ran one,
    /// passed.
    var spotIsClean: Bool {
        ResultReading.spotIsClean(hasSpot: spot != nil, checks: readingChecks)
    }

    /// How the meter working-space check came out at the spot, for the outline of a spot that
    /// isn't clean: `.unsure` when it isn't clean for any other reason, so the outline never reads
    /// as a clear fail without one.
    var workingSpaceOutcome: CheckOutcome {
        checks.contains { $0.id == ResultReading.meterWorkingSpaceCheckID && $0.outcome == .fail } ? .fail : .unsure
    }

    var answer: ResultReading.Answer {
        ResultReading.answer(decision: placementDecision, policyApproved: policyApproved, hasSpot: spot != nil, checks: readingChecks)
    }

    /// The check lines on the result card, in the order `ResultReading.cardLines` gives.
    var cardChecks: [CheckRow] {
        ResultReading.cardLines(readingChecks).map { checks[$0] }
    }

    /// The view `row`'s line on the card offers to take, or nil: the view the server named to
    /// settle it (`settledBy`), when `ResultCardActions.lineOffersView` says the line offers it.
    /// The line's "Show me" and its words (`ScanCopy.cardLine`, `ScanCopy.unsureNote`) both read
    /// this, so the words never promise a photo the line doesn't let the homeowner take.
    func offeredView(for row: CheckRow, sourceAvailable: Bool) -> MissingEvidence? {
        let view = row.settledBy.flatMap { id in missing.first { $0.id == id } }
        guard ResultCardActions.lineOffersView(
            outcome: row.outcome.placementOutcome, needsPerson: row.needsPerson,
            settlingViewCapturable: view?.capturable, sourceAvailable: sourceAvailable)
        else { return nil }
        return view
    }

    /// The view that would settle `row` and that the camera can take now: only for an unsure
    /// check a person needn't judge. `offeredView` with the camera up.
    func viewToTake(for row: CheckRow) -> MissingEvidence? {
        offeredView(for: row, sourceAvailable: true)
    }

    /// The first view in `missing` that settles an unsure check and can be taken now.
    var firstViewToTake: MissingEvidence? {
        let wanted = Set(checks.compactMap { viewToTake(for: $0)?.id })
        return missing.first { wanted.contains($0.id) }
    }

    private var readingChecks: [ResultReading.Check] {
        checks.map { row in
            ResultReading.Check(
                id: row.id, outcome: row.outcome.placementOutcome, needsPerson: row.needsPerson,
                viewCapturable: viewToTake(for: row) != nil
            )
        }
    }

    private var placementDecision: PlacementDecision {
        switch decision {
        case .pass: .pass
        case .manualReview: .manualReview
        case .reject: .reject
        }
    }
}

private extension CheckOutcome {
    var placementOutcome: PlacementOutcome {
        switch self {
        case .pass: .pass
        case .fail: .fail
        case .unsure: .unsure
        }
    }
}
