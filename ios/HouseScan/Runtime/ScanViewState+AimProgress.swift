import HouseScanKit

extension ScanViewState {
    func recordAimSpan(for step: GuidanceStep, range: ClosedRange<Float>) {
        if aimSpan?.step != step { previousAimSpan = aimSpan }
        aimSpan = AimSpan(step: step, range: range)
    }

    func resetAimSpans() {
        aimSpan = nil
        previousAimSpan = nil
    }

    /// How far the walk's aim task is toward done, 0...1, for the ring that fills (#81). Nil for
    /// every step but an aim task.
    var aimProgress: Double? {
        aimProgress(for: guidance)
    }

    /// How far `step` is toward done, 0...1: the covered share of its band over the stretch the
    /// planner checks, trimmed beside deferred stretches, over the share that completes it
    /// (`aimSatisfied`). The planner's numbers are read here, on the engine's side of the
    /// contract, so the screens don't import them.
    ///
    /// The overlay also scores the step that just ended with it. The planner moves on in the
    /// same update that completes a step, so by the time a screen sees the full strip,
    /// `guidance` is already the next step.
    ///
    /// It reads the published strip, which holds the cells in view on the wall map
    /// (`CoverageMap.visibleRange`); the planner reads the whole map. Near an end of that range
    /// the two can differ by a cell. The window is narrowed by the map's own tolerance
    /// (`CoverageMap.indices(overlapping:)`), so a cell that only touches its edge counts for
    /// neither, and the ring can't show done while the task stays up. Nil for every step but an
    /// aim task.
    func aimProgress(for step: GuidanceStep) -> Double? {
        let band: CoverageBand
        let s: Float
        switch step {
        case .aimAtGround(let at):
            band = .ground
            s = at
        case .aimAtWall(let at):
            band = .wall
            s = at
        default:
            return nil
        }
        let recorded = [aimSpan, previousAimSpan].compactMap { $0 }.first { $0.step == step }?.range
        // Demo states have no planner; their aims use the usual window.
        let range = recorded ?? ((s - GuidancePlanner.aimHalfWidth)...(s + GuidancePlanner.aimHalfWidth))
        // Cap the edge inset so a short trimmed window cannot invert its bounds.
        let tolerance = min(coverage.cellWidth * 1e-3, (range.upperBound - range.lowerBound) / 4)
        let covered = coverage.coveredFraction(band, in: (range.lowerBound + tolerance)...(range.upperBound - tolerance))
        return min(1, covered / GuidancePlanner.aimSatisfied)
    }
}
