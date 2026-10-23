import Testing
@testable import MeasureGeometry

/// Times are seconds of device uptime; tracking must stay normal for 1 s.
struct TrackingStabilityTests {
    var stability = TrackingStability(requiredSeconds: 1)

    @Test mutating func `older normal report cannot overwrite a newer limited report`() {
        stability.trackingChanged(isNormal: false, at: 11)
        let accepted = stability.trackingChanged(isNormal: true, at: 10)
        #expect(!accepted)
        #expect(!stability.isStable(at: 12))
        stability.trackingChanged(isNormal: true, at: 13)
        #expect(!stability.isStable(at: 13.999))
        #expect(stability.isStable(at: 14))
    }

    @Test mutating func `interruption ending before its queued start still restarts stability`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.interruptionChanged(isInterrupted: false, at: 12)
        let accepted = stability.interruptionChanged(isInterrupted: true, at: 11)
        #expect(!accepted)
        #expect(!stability.isStable(at: 12.999))
        #expect(stability.isStable(at: 13))
    }

    @Test mutating func `late tracking uses the end of the interruption as its earliest start`() {
        stability.interruptionChanged(isInterrupted: false, at: 12)
        stability.trackingChanged(isNormal: true, at: 10)
        #expect(stability.stableAt == 13)
        // A newer interruption report cannot hide a limited tracking report from the other stream.
        stability.trackingChanged(isNormal: false, at: 11)
        #expect(!stability.isStable(at: 20))
    }

    @Test mutating func `late interruption end cannot move a newer normal start backward`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.trackingChanged(isNormal: true, at: 15)
        stability.interruptionChanged(isInterrupted: false, at: 12)
        #expect(stability.stableAt == 16)
    }

    @Test mutating func `normal tracking becomes stable after the full interval`() {
        stability.trackingChanged(isNormal: true, at: 10)
        #expect(stability.stableAt == 11)
        #expect(!stability.isStable(at: 10.999))
        #expect(stability.isStable(at: 11))
    }

    @Test mutating func `an interruption before the interval ends needs a fresh interval after it`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.interruptionChanged(isInterrupted: true, at: 10.5)
        // The original deadline passes while the camera is interrupted.
        #expect(!stability.isStable(at: 11))
        #expect(stability.stableAt == nil)
        // Tracking still reads normal when the interruption ends, with no tracking callback.
        stability.interruptionChanged(isInterrupted: false, at: 20)
        #expect(!stability.isStable(at: 20))
        #expect(!stability.isStable(at: 20.999))
        #expect(stability.isStable(at: 21))
    }

    @Test mutating func `an interruption after stability revokes it until a fresh interval`() {
        stability.trackingChanged(isNormal: true, at: 10)
        #expect(stability.isStable(at: 12))
        stability.interruptionChanged(isInterrupted: true, at: 12)
        #expect(!stability.isStable(at: 12))
        stability.interruptionChanged(isInterrupted: false, at: 13)
        #expect(!stability.isStable(at: 13.5))
        #expect(stability.isStable(at: 14))
    }

    @Test mutating func `a normal report during an interruption does not start the interval`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.trackingChanged(isNormal: true, at: 10.2)
        #expect(stability.stableAt == nil)
        stability.interruptionChanged(isInterrupted: false, at: 15)
        #expect(stability.stableAt == 16)
    }

    @Test mutating func `ending an interruption while tracking is limited waits for normal`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.interruptionChanged(isInterrupted: true, at: 11)
        stability.trackingChanged(isNormal: false, at: 11.5)
        stability.interruptionChanged(isInterrupted: false, at: 12)
        #expect(stability.stableAt == nil)
        stability.trackingChanged(isNormal: true, at: 14)
        #expect(!stability.isStable(at: 14.5))
        #expect(stability.isStable(at: 15))
    }

    @Test mutating func `each normal callback starts a fresh conservative interval`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.trackingChanged(isNormal: true, at: 10.8)
        #expect(!stability.isStable(at: 11))
        #expect(stability.isStable(at: 11.8))
    }

    @Test mutating func `a late limited callback cannot hide a break between normal callbacks`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.trackingChanged(isNormal: true, at: 12)
        let accepted = stability.trackingChanged(isNormal: false, at: 11)
        #expect(!accepted)
        #expect(!stability.isStable(at: 12))
        #expect(stability.isStable(at: 13))
    }

    @Test mutating func `leaving normal tracking revokes stability`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.trackingChanged(isNormal: false, at: 12)
        #expect(!stability.isStable(at: 12))
        stability.trackingChanged(isNormal: true, at: 13)
        #expect(!stability.isStable(at: 13.9))
        #expect(stability.isStable(at: 14))
    }

    @Test mutating func `queued tracking reports cannot cross a reset`() {
        stability.reset(at: 20)
        let oldLimited = stability.trackingChanged(isNormal: false, at: 19)
        let oldNormal = stability.trackingChanged(isNormal: true, at: 20)
        let prematureNormal = stability.trackingChanged(isNormal: true, at: 20.1)
        #expect(!oldLimited && !oldNormal && !prematureNormal)
        #expect(stability.stableAt == nil)
        let freshLimited = stability.trackingChanged(isNormal: false, at: 20.2)
        let freshNormal = stability.trackingChanged(isNormal: true, at: 20.3)
        let lateLimited = stability.trackingChanged(isNormal: false, at: 19.9)
        #expect(freshLimited && freshNormal)
        #expect(!lateLimited)
        #expect(!stability.isStable(at: 21.299))
        #expect(stability.isStable(at: 21.3))
    }

    @Test mutating func `interruption reports cross a reset in their own order`() {
        stability.reset(at: 20)
        let queuedStart = stability.interruptionChanged(isInterrupted: true, at: 19.5)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 20.3)
        #expect(queuedStart)
        #expect(!stability.isStable(at: 25))
        let queuedEnd = stability.interruptionChanged(isInterrupted: false, at: 19.8)
        let olderStart = stability.interruptionChanged(isInterrupted: true, at: 19.6)
        #expect(queuedEnd && !olderStart)
        // The run starts no earlier than the fresh normal report after the reset.
        #expect(!stability.isStable(at: 21.299))
        #expect(stability.isStable(at: 21.3))
    }

    @Test mutating func `an older end cannot clear a newer start queued before a reset`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.reset(at: 20)
        stability.interruptionChanged(isInterrupted: false, at: 15)
        stability.interruptionChanged(isInterrupted: true, at: 18)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 40))
    }

    @Test mutating func `an older end delivered after a newer start queued before a reset is refused`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.reset(at: 20)
        stability.interruptionChanged(isInterrupted: true, at: 18)
        let olderEnd = stability.interruptionChanged(isInterrupted: false, at: 15)
        #expect(!olderEnd)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 40))
    }

    @Test mutating func `an interruption end stamped before a reset still ends the interruption`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.reset(at: 20)
        let lateEnd = stability.interruptionChanged(isInterrupted: false, at: 19)
        #expect(lateEnd)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 21.999))
        #expect(stability.isStable(at: 22))
    }

    @Test mutating func `a pre-reset interruption end after fresh tracking starts the run no earlier than that tracking`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.reset(at: 20)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 25))
        stability.interruptionChanged(isInterrupted: false, at: 19)
        #expect(!stability.isStable(at: 21.999))
        #expect(stability.isStable(at: 22))
    }

    @Test mutating func `an interruption still running across a reset keeps taps waiting`() {
        stability.interruptionChanged(isInterrupted: true, at: 10)
        stability.reset(at: 20)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 30))
        stability.interruptionChanged(isInterrupted: false, at: 30)
        #expect(!stability.isStable(at: 30.999))
        #expect(stability.isStable(at: 31))
    }

    @Test mutating func `a pre-reset end cannot end an interruption that began after the reset`() {
        stability.reset(at: 20)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        stability.interruptionChanged(isInterrupted: true, at: 25)
        let staleEnd = stability.interruptionChanged(isInterrupted: false, at: 19)
        #expect(!staleEnd)
        #expect(!stability.isStable(at: 40))
    }

    @Test mutating func `a new AR run waits for normal tracking again`() {
        stability.trackingChanged(isNormal: true, at: 10)
        stability.reset(at: 20)
        #expect(!stability.isStable(at: 20))
        let prematureNormal = stability.trackingChanged(isNormal: true, at: 20.1)
        #expect(!prematureNormal)
        stability.trackingChanged(isNormal: false, at: 20.2)
        stability.trackingChanged(isNormal: true, at: 21)
        #expect(!stability.isStable(at: 21.999))
        #expect(stability.isStable(at: 22))
    }
}
