import CoreGraphics

/// Where the open camera between the instruction card and the actions would be if the card had
/// no note, worked out from the layout that has one.
///
/// A line the card shows because of what the open camera holds must not be judged against the
/// camera it shrinks. In "See it on your wall" at AX5, a five-line off-screen note made the card
/// cover the spot, so the spot stayed off screen and the note stayed with it, pushing Done out of
/// reach (CI run of #241 at d5d137ed, ProvisionalPlacementUITests). Judged here, the note's own
/// height can't keep it up.
///
/// The camera chrome is a column, as tall as the screen or scrolling when taller, whose open
/// camera stretches to fill the screen but keeps at least `minimumHeight`. The note sits in the
/// card above it. Without the note the camera starts `noteRoom` higher; it is as tall as the
/// screen leaves it, and a column that gets shorter can't stay scrolled past its new end. The
/// answer is the settled layout: a pull past the top while dragging is left out.
public enum OpenCameraWithoutNote {
    /// - Parameters:
    ///   - window: The open camera as laid out with the note, in global coordinates.
    ///   - content: The whole column as laid out with the note, in global coordinates. Its top
    ///     moves up as the column scrolls.
    ///   - viewport: The safe area the column fills and scrolls in, in global coordinates: at
    ///     rest the column's top is the viewport's top. Not a scroll view's frame that runs under
    ///     the status bar and the home indicator.
    ///   - noteRoom: How much taller the note makes the card. Zero returns `window`.
    ///   - minimumHeight: The least open camera the column keeps without the note.
    public static func frame(window: CGRect, content: CGRect, viewport: CGRect, noteRoom: CGFloat, minimumHeight: CGFloat) -> CGRect {
        guard noteRoom > 0 else { return window }
        // Everything in the column but the open camera, without the note.
        let rest = content.height - window.height - noteRoom
        let height = max(minimumHeight, viewport.height - rest)
        let endOffset = max(rest + height - viewport.height, 0)
        // Settled: a pull past the top springs back, and a column that fits doesn't scroll.
        let offset = min(max(viewport.minY - content.minY, 0), endOffset)
        let top = viewport.minY - offset + (window.minY - content.minY) - noteRoom
        return CGRect(x: window.minX, y: top, width: window.width, height: height)
    }
}
