import Foundation
import simd

/// What the integration build shows of a capture-API result: where it came from, what the server
/// decided, its own words, the views it asks for, and why there is no AR box. Built from the
/// decoded response only; nothing is inferred, and an absent part stays absent.
public struct CaptureResultSummary: Sendable, Equatable {
    public enum State: Sendable, Equatable {
        /// The run has no outcome yet.
        case working(CaptureResult.Status)
        case decided(CaptureResult.OutcomeKind)
        /// The capture is over without an outcome: it failed or expired.
        case ended(CaptureResult.Status)
    }

    public var state: State
    /// The server's message for the homeowner, verbatim; nil before an outcome.
    public var message: String?
    /// Each requested view's prompt, verbatim, title then body.
    public var prompts: [CaptureResult.Prompt]
    /// The criteria rows the server sent; nil when it sent none. Never filled in by the app.
    public var criteria: [CaptureResult.Criterion]?
    /// False until someone has verified the server ran real analysis for this result.
    public var analysisVerified: Bool
    /// Why AR shows nothing for this result on the scan now on screen.
    public var arUnavailable: CaptureResult.Unavailable?

    /// `world` is the scan on screen now; `meterAnchor` its meter anchor's pose in that world, nil
    /// when the scan has none to place a box on (AR then says so, after the other checks).
    public init(_ record: CaptureResult.Record, world: CaptureResult.Association, meterAnchor: simd_float4x4?) {
        let response = record.response
        state = if let outcome = response.outcome {
            .decided(outcome.kind)
        } else {
            switch response.status {
            case .failed, .expired: .ended(response.status)
            default: .working(response.status)
            }
        }
        message = response.outcome?.message
        let views = response.viewsNeeded.isEmpty ? response.outcome?.viewsNeeded ?? [] : response.viewsNeeded
        prompts = views.map(\.prompt)
        criteria = response.criteria
        analysisVerified = record.analysis == .verified
        // A zero matrix is not a pose, so without an anchor the gate ends on `.invalidMeterAnchor`.
        arUnavailable = switch record.placement(in: world, meterAnchor: meterAnchor ?? simd_float4x4()) {
        case .box: nil
        case .unavailable(let reason): reason
        }
    }
}
