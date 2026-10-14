import CoreGraphics

/// A direction to a projected spot outside the visible camera between the card and actions.
public enum OpenCameraDirection {
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
