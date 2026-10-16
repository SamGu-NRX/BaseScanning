import Testing
@testable import HouseScanKit

struct SlowReleaseTests {
    @Test func showsTheFirstReadingAndHoldsItWhileFound() {
        var latch = SlowRelease<Int>(release: 2)
        #expect(latch.update(nil, time: 0) == nil)
        #expect(latch.update(5, time: 1) == 5)
        #expect(latch.update(7, time: 1.1) == 5, "a later reading must not move the line")
    }

    /// Review of #168: a plane ARKit moves or removes must not leave the line up for good.
    @Test func clearsOnlyAfterNothingIsFoundForTheReleaseTime() {
        var latch = SlowRelease<Int>(release: 2)
        latch.update(5, time: 0)
        #expect(latch.update(nil, time: 1) == 5)
        #expect(latch.update(nil, time: 2.9) == 5)
        #expect(latch.update(nil, time: 3) == nil)
        #expect(latch.update(8, time: 3.1) == 8, "found again, the new reading shows")
    }

    @Test func aReadingInsideTheReleaseTimeRestartsIt() {
        var latch = SlowRelease<Int>(release: 2)
        latch.update(5, time: 0)
        latch.update(nil, time: 1)
        #expect(latch.update(6, time: 2.5) == 5)
        #expect(latch.update(nil, time: 3) == 5)
        #expect(latch.update(nil, time: 4.9) == 5, "the absence counts from 3 s, not 1 s")
        #expect(latch.update(nil, time: 5) == nil)
    }
}
