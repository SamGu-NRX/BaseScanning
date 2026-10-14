import CoreGraphics

/// A direction to a projected spot outside the visible camera between the card and actions.
public enum OpenCameraDirection {
    /// Names the direction drawn toward the open camera window. Keeping the previous axis
    /// prevents diagonal jitter from changing the card's sentence on every frame.
    public static func classify(_ direction: CGVector, previous: AimHint? = nil) -> AimHint {
        let horizontal = abs(direction.dx), vertical = abs(direction.dy)
        let bias = CGFloat(AimHint.axisBias)
        let useVertical: Bool
        switch previous {
        case .above?, .below?: useVertical = vertical * bias >= horizontal
        case .left?, .right?: useVertical = vertical > horizontal * bias
        case .onScreen?, .behind?, nil: useVertical = vertical >= horizontal
        }
        if useVertical { return direction.dy < 0 ? .above : .below }
        return direction.dx < 0 ? .left : .right
    }

    /// Projected spots use the visible window's center, since the full camera's center can be
    /// covered by the card. A spot behind the camera has no projection and uses `fallback`.
    /// Returns nil when the projected spot is already visible.
    public static func toward(_ projected: CGPoint?, in window: CGRect, fallback: CGVector?) -> CGVector? {
        guard let projected else { return fallback }
        guard !window.contains(projected) else { return nil }
        let dx = projected.x - window.midX, dy = projected.y - window.midY
        let length = (dx * dx + dy * dy).squareRoot()
        guard length > 0 else { return nil }
        return CGVector(dx: dx / length, dy: dy / length)
    }
}
