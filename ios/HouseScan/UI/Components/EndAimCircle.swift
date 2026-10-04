import SwiftUI

extension View {
    /// The circle a mark lands under (`Reticle`), as one accessibility element: "Aiming circle".
    /// The reticle itself is hidden from VoiceOver as decoration. Here it says where "Wall ends
    /// here" and the other marks land, and UI tests read its real frame to check that nothing
    /// covers it (`PastEndRecoveryUITests`, `WalkRecoveryUITests`). Apply it before the reticle
    /// is stretched to the screen, so the element's frame is the circle's. Show the circle only
    /// while it is offered: `.accessibilityHidden` on this element left it in the tree at zero
    /// opacity (CI run 37171342665), so VoiceOver would stop on a circle that isn't there.
    func endAimCircle() -> some View {
        accessibilityElement(children: .ignore)
            .accessibilityLabel("Aiming circle")
            .accessibilityIdentifier("aim.circle")
    }
}
