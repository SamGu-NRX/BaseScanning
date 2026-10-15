import SwiftUI

/// Measurements from one layout pass. Separate geometry callbacks can combine a new note's
/// height with the previous camera frame, making a note decided from that frame oscillate.
struct CameraChromeMeasurements: PreferenceKey {
    struct Value: Equatable {
        var window: CGRect?
        var content: CGRect?
        var messageHeight: CGFloat?
        var noteFreeHeight: CGFloat?

        func layout(viewport: CGRect, minimumHeight: CGFloat) -> CameraWindow.Layout? {
            guard let window, let content, let messageHeight, let noteFreeHeight else { return nil }
            return CameraWindow.Layout(window: window, content: content, viewport: viewport,
                                       noteRoom: max(messageHeight - noteFreeHeight, 0), minimumHeight: minimumHeight)
        }
    }

    static var defaultValue: Value { Value() }

    static func reduce(value: inout Value, nextValue: () -> Value) {
        let next = nextValue()
        value.window = next.window ?? value.window
        value.content = next.content ?? value.content
        value.messageHeight = next.messageHeight ?? value.messageHeight
        value.noteFreeHeight = next.noteFreeHeight ?? value.noteFreeHeight
    }
}
