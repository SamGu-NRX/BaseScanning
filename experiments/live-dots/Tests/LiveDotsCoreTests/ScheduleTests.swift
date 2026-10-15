import Testing
@testable import LiveDotsCore

struct ScheduleTests {
    func close(_ a: Float, _ b: Float) -> Bool { abs(a - b) < 1e-5 }

    @Test func `a keyframe is on screen until the next one appears`() {
        #expect(close(Schedule.onScreen(0, count: 31), 0.25))
        // The hold keyframe stays for the ring fill and the bracket flash.
        #expect(close(Schedule.onScreen(Schedule.holdIndex, count: 31), Schedule.holdDuration + Schedule.flashDuration))
        for index in 0..<30 {
            let shown = Schedule.onScreen(index, count: 31)
            #expect(Schedule.keyframe(at: Schedule.start(of: index) + shown - 1e-3, count: 31) == index)
            #expect(Schedule.keyframe(at: Schedule.start(of: index) + shown + 1e-3, count: 31) == index + 1)
        }
    }

    @Test func `the last keyframe is on screen only until playback stops`() {
        // keyframe(at:) holds the last keyframe at any time, so it cannot bound an offset into it.
        #expect(Schedule.keyframe(at: 1e5, count: 31) == 30)
        let shown = Schedule.onScreen(30, count: 31)
        #expect(close(Schedule.start(of: 30) + shown, Schedule.playbackEnd(count: 31)))
        #expect(close(shown, 0.25 + Tuning.birthDuration))
    }
}
