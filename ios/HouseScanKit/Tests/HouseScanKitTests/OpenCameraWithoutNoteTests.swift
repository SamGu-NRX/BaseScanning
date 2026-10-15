import CoreGraphics
import HouseScanKit
import Testing

@Suite struct OpenCameraWithoutNoteTests {
    /// The iPhone 17's safe area under the status bar, about where the chrome scrolls.
    private static let viewport = CGRect(x: 0, y: 62, width: 402, height: 778)

    /// The camera chrome's column as SwiftUI lays it out: `above` and `below` the open camera,
    /// which fills the screen down to `minimum`, scrolled by `scroll` at most to the column's end.
    private struct Column {
        var above: CGFloat
        var below: CGFloat
        var minimum: CGFloat
        var note: CGFloat = 0
        var scroll: CGFloat = 0

        var window: CGRect { laidOut.window }
        var content: CGRect { laidOut.content }
        /// How far the column is scrolled once `scroll` is held to its end.
        var appliedScroll: CGFloat { OpenCameraWithoutNoteTests.viewport.minY - content.minY }

        private var laidOut: (window: CGRect, content: CGRect) {
            let viewport = OpenCameraWithoutNoteTests.viewport
            let rest = above + note + below
            let height = max(minimum, viewport.height - rest)
            let offset = min(scroll, rest + height - viewport.height)
            let content = CGRect(x: 0, y: viewport.minY - offset, width: viewport.width, height: rest + height)
            return (CGRect(x: 16, y: content.minY + above + note, width: 370, height: height), content)
        }
    }

    private static func withoutNote(_ column: Column, minimum: CGFloat) -> CGRect {
        OpenCameraWithoutNote.frame(window: column.window, content: column.content, viewport: viewport,
                                    noteRoom: column.note, minimumHeight: minimum)
    }

    @Test func withoutANoteTheWindowIsUnchanged() {
        let column = Column(above: 300, below: 100, minimum: 180)
        #expect(Self.withoutNote(column, minimum: 180) == column.window)
    }

    /// Removing the note and restoring the spacer take separate SwiftUI updates. The AX5
    /// trace had no note while the spacer still had the note's zero-height minimum.
    @Test func removingTheNoteRestoresTheCameraBeforeTheSpacerCatchesUp() {
        let transient = Column(above: 760, below: 81, minimum: 0)
        let settled = Column(above: 760, below: 81, minimum: 180)
        #expect(transient.window.height == 0)
        #expect(Self.withoutNote(transient, minimum: 180) == settled.window)
    }

    /// Closing Details can leave the now screen-height column at its previous scroll offset
    /// for one update. It must not report the spot below the camera and reinsert the note.
    @Test(arguments: [-40, 63, 243, 496] as [CGFloat])
    func removingTheNoteClampsTheOldScrollOffset(offset: CGFloat) {
        let settled = Column(above: 364, below: 81, minimum: 180)
        let result = OpenCameraWithoutNote.frame(
            window: settled.window.offsetBy(dx: 0, dy: -offset),
            content: settled.content.offsetBy(dx: 0, dy: -offset), viewport: Self.viewport,
            noteRoom: 0, minimumHeight: 180)
        #expect(result == settled.window)
    }

    @Test func removingTheNoteWhileScrollingRestoresBothMinimumAndScrollLimit() {
        let transient = Column(above: 760, below: 81, minimum: 0)
        let settled = Column(above: 760, below: 81, minimum: 180, scroll: 496)
        let result = OpenCameraWithoutNote.frame(
            window: transient.window.offsetBy(dx: 0, dy: -496),
            content: transient.content.offsetBy(dx: 0, dy: -496), viewport: Self.viewport,
            noteRoom: 0, minimumHeight: 180)
        #expect(result == settled.window)
        #expect(result.height == 180)
    }

    /// The column fits: the note pushes the camera's top down and Done stays at the bottom.
    @Test func aNoteThatFitsGivesBackItsRoomAboveTheCamera() {
        let with = Column(above: 300, below: 100, minimum: 180, note: 150)
        #expect(with.content.height == Self.viewport.height)
        let without = Column(above: 300, below: 100, minimum: 180)
        #expect(Self.withoutNote(with, minimum: 180) == without.window)
        #expect(without.window.maxY == with.window.maxY)
    }

    /// The failure in #241's CI run at d5d137ed: at AX5 the five-line note made the column
    /// overflow, so Done moved below the screen. Without the note the camera ends above Done
    /// again, not where the pushed-down camera ended.
    @Test func aNoteThatPushesDoneOffScreenIsUndoneAtBothEdges() {
        let with = Column(above: 306, below: 106, minimum: 180, note: 292)
        #expect(with.content.height > Self.viewport.height)
        let without = Column(above: 306, below: 106, minimum: 180)
        let estimate = Self.withoutNote(with, minimum: 180)
        #expect(estimate == without.window)
        #expect(estimate.maxY < with.window.maxY)
    }

    /// Scrolled to Done with the note up: without it the column is shorter than that scroll,
    /// so it settles back to its end and the camera sits lower than a plain shift would put it.
    @Test func aScrollPastTheShorterColumnsEndSettlesAtItsEnd() {
        let with = Column(above: 306, below: 106, minimum: 180, note: 292, scroll: 200)
        #expect(with.content.minY < Self.viewport.minY)
        let without = Column(above: 306, below: 106, minimum: 180, scroll: 200)
        #expect(without.content.minY == Self.viewport.minY)
        #expect(Self.withoutNote(with, minimum: 180) == without.window)
    }

    /// Pulled down past the top while the note made the column scroll: without the note the
    /// column fits and can't be pulled, so the camera is where it settles.
    @Test func aPullPastTheTopIsLeftOut() {
        var with = Column(above: 306, below: 106, minimum: 180, note: 292)
        let settled = with.window
        let pulled = 40 as CGFloat
        let estimate = OpenCameraWithoutNote.frame(
            window: settled.offsetBy(dx: 0, dy: pulled), content: with.content.offsetBy(dx: 0, dy: pulled),
            viewport: Self.viewport, noteRoom: with.note, minimumHeight: 180)
        with.note = 0
        #expect(estimate == with.window)
    }

    /// A column that overflows even without the note keeps the least camera and its scroll.
    @Test func aColumnThatStillOverflowsKeepsTheLeastCameraAndTheScroll() {
        let with = Column(above: 560, below: 106, minimum: 180, note: 120, scroll: 60)
        let without = Column(above: 560, below: 106, minimum: 180, scroll: 60)
        #expect(without.content.minY == Self.viewport.minY - 60)
        let estimate = Self.withoutNote(with, minimum: 180)
        #expect(estimate == without.window)
        #expect(estimate.height == 180)
    }

    /// A screen that lets the note take its room from the least camera (`CameraChrome`) lays the
    /// note out over a shorter minimum. The answer is still the layout with the usual minimum.
    @Test(arguments: [0, 60, 180] as [CGFloat])
    func theNoteLayoutsOwnMinimumDoesNotMatter(minimumWithNote: CGFloat) {
        let with = Column(above: 420, below: 106, minimum: minimumWithNote, note: 292)
        let without = Column(above: 420, below: 106, minimum: 180)
        #expect(Self.withoutNote(with, minimum: 180) == without.window)
    }

    /// Whatever the note, the scroll and the column, the answer is the layout without the note:
    /// the note's own height never decides what the camera holds.
    @Test(arguments: [0, 150, 360, 520] as [CGFloat], [0, 40, 180, 400] as [CGFloat])
    func theAnswerIsAlwaysTheLayoutWithoutTheNote(above: CGFloat, scroll: CGFloat) {
        for note: CGFloat in [1, 52, 180, 292, 600] {
            let with = Column(above: above, below: 106, minimum: 180, note: note, scroll: scroll)
            let without = Column(above: above, below: 106, minimum: 180, scroll: with.appliedScroll)
            #expect(Self.withoutNote(with, minimum: 180) == without.window, "note \(note)")
        }
    }
}
