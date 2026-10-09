import Foundation

/// Which layer draws the result on "See it on your wall": the AR scene (RealityKit) or the
/// screen's own drawing over the camera (the Canvas).
///
/// The Canvas is the default. The AR scene takes over only once the app has seen it drawing the
/// result for `confirmation` seconds without a break, and gives it back at once when it stops
/// holding the result (its anchor or the model lost), not when the battery merely leaves the view.
/// Build 4.1 hid the Canvas as soon as the model was handed to the AR scene; on a phone where the
/// scene then drew nothing, the homeowner saw no battery at all.
public struct ResultOverlayPolicy: Sendable, Equatable {
    /// How long the AR scene has to be seen drawing before the Canvas steps aside: long enough
    /// to ride out a frame or two of an anchor settling, short enough that both layers are not
    /// on screen together for long. A guess, not measured on a phone.
    public static let confirmation: Double = 0.5

    /// True while the AR scene draws the result and the Canvas should not.
    public private(set) var usesRealityKit = false
    /// The AR scene has been seen drawing the result on this phone, so it can draw it.
    private var confirmed = false
    /// When the current unbroken run of "drawn" began; nil while the AR scene isn't drawing.
    private var drawnSince: Double?

    /// Starts on the Canvas: the AR scene takes over only once `update` has seen it draw the
    /// result for `confirmation` seconds.
    public init() {}

    /// Takes one look at the AR scene: `drawn` says whether it draws the result now, at `time`
    /// in seconds (any clock that only goes forward). Returns `usesRealityKit`.
    ///
    /// `held` says whether the AR scene holds the result, drawn or not: anchored and enabled,
    /// with the battery perhaps out of view. Seeing it drawn is only needed the first time: once
    /// the AR scene has taken over, it keeps the result while `held`, and takes it back as soon
    /// as it holds it again, after a model rebuilt for a moved wall or a moment of lost tracking.
    /// Otherwise, with the phone on the meter and the battery out of view, the Canvas came back
    /// over RealityKit's copy: a second, unoccluded cable and tint a few centimetres off (review
    /// of #100).
    @discardableResult
    public mutating func update(drawn: Bool, held: Bool = false, time: Double) -> Bool {
        if held, usesRealityKit || confirmed {
            usesRealityKit = true
            return true
        }
        guard drawn else {
            drawnSince = nil
            usesRealityKit = false
            return false
        }
        // A clock that ran back restarts the run rather than confirming it early.
        if let since = drawnSince, since <= time {
            if time - since >= Self.confirmation {
                usesRealityKit = true
                confirmed = true
            }
        } else {
            drawnSince = time
        }
        return usesRealityKit
    }
}
