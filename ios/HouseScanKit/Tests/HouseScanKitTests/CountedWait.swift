import Testing

/// Waits until `condition` holds, as a hang safeguard and not a timing requirement: the photo
/// processing suites' `until` helpers call this so a capture that never reaches a state fails its
/// test instead of hanging the run.
///
/// The budget is this waiter's own turns, `seconds * 50` waits of 20 ms or more, not wall time, the
/// semantics `CaptureUploaderTests.settles` took in 6c1f68ea. A stalled test process holds the code
/// under test and this waiter up together, and a wall-clock deadline can expire before either gets
/// to run: hosted run 37219668935 stalled every task for about 42 s, and in run 37229126189 a 10 s
/// wall-clock wait here gave up on an upload that takes about 4 s locally. `condition` is read once
/// more after the last wait, so a state reached during that wait still counts. It runs in the
/// caller's isolation (`#isolation`), so a main-actor suite's condition is read on the main actor
/// and never sent across.
func countedWait(
    _ seconds: Double, isolation: isolated (any Actor)? = #isolation, _ condition: () -> Bool
) async throws -> Bool {
    for _ in 0..<Int(seconds * 50) {
        if condition() { return true }
        try await Task.sleep(for: .milliseconds(20))
    }
    return condition()
}

/// The safeguard still ends, and still reads the answer after its last wait.
@Suite struct CountedWaitTests {
    @Test func aConditionThatNeverHoldsFailsAfterAtLeastItsBudget() async throws {
        var reads = 0
        let started = ContinuousClock.now
        let held = try await countedWait(0.2) { reads += 1; return false }
        #expect(!held)
        // 10 turns of at least 20 ms each, and one read per turn plus the final read.
        #expect(ContinuousClock.now - started >= .milliseconds(200))
        #expect(reads == 11)
    }

    @Test func aConditionThatHoldsOnlyAtTheFinalReadCounts() async throws {
        var reads = 0
        #expect(try await countedWait(0.2) { reads += 1; return reads == 11 })
    }

    @Test func aConditionThatAlreadyHoldsReturnsWithoutWaiting() async throws {
        var reads = 0
        #expect(try await countedWait(30) { reads += 1; return true })
        #expect(reads == 1)
    }
}
