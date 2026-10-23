import Foundation
import HouseScanKit
import Testing

/// "Can't get there" on the walk's card: a second one soon after asks to end the scan (#82), and
/// a wall neither side of which was walked shows no spot (#76).
@Suite struct WalkRefusalsTests {
    @Test func firstRefusalNeverAsks() {
        let refusals = WalkRefusals()
        #expect(!refusals.asksToEndScan(at: 0))
        #expect(!refusals.asksToEndScan(at: 100))
    }

    @Test func secondRefusalWithinTheWindowAsks() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -0.2, walked: 0, time: 10)
        #expect(refusals.asksToEndScan(at: 10.6))
        #expect(refusals.asksToEndScan(at: 10 + WalkRefusals.repeatWindow))
        #expect(!refusals.asksToEndScan(at: 10 + WalkRefusals.repeatWindow + 0.1))
        // A clock that went backwards (a new scan's clock) doesn't count as soon.
        #expect(!refusals.asksToEndScan(at: 9))
    }

    @Test func keepWalkingForgetsTheLastRefusal() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -0.2, walked: 0, time: 10)
        refusals.keepWalking()
        #expect(!refusals.asksToEndScan(at: 11))
        // The side it ended is still refused.
        #expect(refusals.wasRefused(.left, end: -0.2))
    }

    /// Synthetic: both sides ended by "Can't get there" with the walk and the end within a
    /// battery's width of the meter on each side. This is the only case the rule catches.
    @Test func bothSidesEndedNearTheMeterMeansNeitherWasWalked() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -0.3, walked: 0.2, time: 1)
        refusals.ended(.right, at: 0.6, walked: 0, time: 2)
        #expect(refusals.neitherSideWalked(leftEnd: -0.3, rightEnd: 0.6))
    }

    /// The rule's known limit (#76): field test run 3 is NOT caught. Its kept views reached about
    /// 2.0 m left and 1.8 m right of the meter before both walk cards were refused, and its ends
    /// were recorded at -4.0 m and +0.5 m, so both sides count as walked and the spot still shows.
    /// Run 3's spot inside the house came from a bad meter tap (#69).
    @Test func run3IsNotCaughtBecauseItsViewsReachedPastTheMinimum() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -4.0, walked: 2.04, time: 1)
        refusals.ended(.right, at: 0.525, walked: 1.83, time: 2)
        #expect(!refusals.neitherSideWalked(leftEnd: -4.0, rightEnd: 0.525))
    }

    /// Only coincident/reversed or missing ends prevent finishing; a 0.5 m wall is valid.
    @Test func endsTooCloseToFinish() {
        #expect(!WalkRefusals.endsTooClose(left: -0.2, right: 0.3))
        #expect(WalkRefusals.endsTooClose(left: 0, right: 0))
        #expect(WalkRefusals.endsTooClose(left: 0.2, right: -0.2))
        #expect(WalkRefusals.endsTooClose(left: nil, right: 2))
        #expect(!WalkRefusals.endsTooClose(left: -0.4, right: 0.4))
        #expect(!WalkRefusals.endsTooClose(left: -3, right: 0))
    }

    @Test func oneSideRefusedIsNotEnough() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -0.3, walked: 0, time: 1)
        #expect(!refusals.neitherSideWalked(leftEnd: -0.3, rightEnd: 4))
        #expect(!refusals.neitherSideWalked(leftEnd: -0.3, rightEnd: nil))
    }

    /// Device run 1's shape: each side walked 5 m, then ended with "Can't get there".
    @Test func aSideWalkedBeforeTheRefusalCountsAsWalked() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -5, walked: 5, time: 1)
        refusals.ended(.right, at: 5, walked: 5, time: 2)
        #expect(!refusals.wasRefused(.left, end: -5))
        #expect(!refusals.neitherSideWalked(leftEnd: -5, rightEnd: 5))
        // Just short of the minimum still counts as refused.
        refusals.ended(.left, at: -0.7, walked: WalkRefusals.walkedMinimum - 0.01, time: 3)
        #expect(refusals.wasRefused(.left, end: -0.7))
        refusals.ended(.left, at: -0.8, walked: WalkRefusals.walkedMinimum, time: 4)
        #expect(!refusals.wasRefused(.left, end: -0.8))
    }

    /// Run 2's shape once ends land at the phone (#71): the right end at the wall's physical end,
    /// 5.2 m out, even if few views were kept on the way, and the left end at the meter. The
    /// right side was walked, so the result keeps its spot.
    @Test func anEndAtThePhoneOutAlongTheWallCountsAsWalked() {
        var refusals = WalkRefusals()
        refusals.ended(.right, at: 5.2, walked: 0.3, time: 1)
        refusals.ended(.left, at: 0, walked: 0, time: 20)
        #expect(!refusals.wasRefused(.right, end: 5.2))
        #expect(refusals.wasRefused(.left, end: 0))
        #expect(!refusals.neitherSideWalked(leftEnd: 0, rightEnd: 5.2))
        // The same on the left: the end's distance from the meter counts, not its sign.
        refusals.ended(.left, at: -1.5, walked: 0, time: 30)
        #expect(!refusals.wasRefused(.left, end: -1.5))
    }

    /// An end moved or set again by something else (a past-end request that was met, a corner)
    /// is no longer the one the refusal set.
    @Test func anEndThatMovedIsNoLongerRefused() {
        var refusals = WalkRefusals()
        refusals.ended(.left, at: -0.3, walked: 0, time: 1)
        refusals.ended(.right, at: 0.6, walked: 0, time: 2)
        #expect(!refusals.neitherSideWalked(leftEnd: -2.4, rightEnd: 0.6))
        #expect(!refusals.neitherSideWalked(leftEnd: nil, rightEnd: 0.6))
    }
}
