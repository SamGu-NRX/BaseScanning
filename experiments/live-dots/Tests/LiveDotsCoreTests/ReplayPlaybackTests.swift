import Testing
@testable import LiveDotsCore

struct ReplayPlaybackTests {
    private func request(_ playback: ReplayPlayback, reduceMotion: Bool = false) -> FrameRequest {
        playback.frameRequest(mode: .lidar, reduceMotion: reduceMotion, fog: .on)
    }

    @Test func `a seek within the playing keyframe resets the fog once`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        var resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
        playback.tick(now: 0.2)
        let before = request(playback)
        resetsFog = continuity.consume(before)
        #expect(!resetsFog)
        playback.scrub(to: 0)
        let after = request(playback)
        #expect(after.keyframe == before.keyframe)
        #expect(after.time == 0 && !after.isSettled)
        #expect(after.seekGeneration == before.seekGeneration + 1)
        resetsFog = continuity.consume(after)
        #expect(resetsFog)
        playback.tick(now: 0.3)
        resetsFog = continuity.consume(request(playback))
        #expect(!resetsFog)
    }

    @Test func `natural playback eases across same and adjacent frames but explicit seeks reset`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        var resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
        playback.tick(now: 0.1)
        resetsFog = continuity.consume(request(playback))
        #expect(!resetsFog)
        playback.tick(now: 0.3)
        #expect(playback.keyframe == 1)
        resetsFog = continuity.consume(request(playback))
        #expect(!resetsFog)
        playback.scrub(to: 2)
        resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
        playback.scrub(to: 1)
        resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
    }

    @Test(arguments: [false, true]) func `resuming a settled scrub resets at the birth time`(reduceMotion: Bool) {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.scrub(to: 2)
        let preview = request(playback, reduceMotion: reduceMotion)
        #expect(preview.isSettled)
        var resetsFog = continuity.consume(preview)
        #expect(resetsFog)
        playback.togglePlayback()
        let resumed = request(playback, reduceMotion: reduceMotion)
        #expect(resumed.time == Schedule.start(of: 2) && !resumed.isSettled)
        #expect(resumed.seekGeneration == preview.seekGeneration + 1)
        #expect(resumed.reduceMotion == reduceMotion)
        resetsFog = continuity.consume(resumed)
        #expect(resetsFog)
        resetsFog = continuity.consume(resumed)
        #expect(!resetsFog)
    }

    @Test func `scrub and resume between rendered frames still invalidate the old mask`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        var resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
        playback.tick(now: 0.2)
        resetsFog = continuity.consume(request(playback))
        #expect(!resetsFog)
        playback.togglePlayback()
        playback.scrub(to: 0)
        playback.togglePlayback()
        resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
    }

    @Test func `pause and resume without a scrub preserve the playhead and fog easing`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        var resetsFog = continuity.consume(request(playback))
        #expect(resetsFog)
        playback.tick(now: 0.2)
        let before = request(playback)
        resetsFog = continuity.consume(before)
        #expect(!resetsFog)
        playback.togglePlayback()
        playback.tick(now: 5)
        playback.togglePlayback()
        playback.tick(now: 10)
        let resumed = request(playback)
        #expect(resumed.time == before.time)
        #expect(resumed.seekGeneration == before.seekGeneration)
        resetsFog = continuity.consume(resumed)
        #expect(!resetsFog)
        playback.tick(now: 10.02)
        #expect(abs(playback.playhead - before.time - 0.02) < 1e-5)
    }

    @Test func `restarting completed playback seeks to the first frame`() {
        var playback = ReplayPlayback(keyframeCount: 3)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        playback.tick(now: 10)
        #expect(!playback.isPlaying)
        let final = request(playback)
        var resetsFog = continuity.consume(final)
        #expect(resetsFog)
        playback.togglePlayback()
        let restarted = request(playback)
        #expect(restarted.keyframe == 0 && restarted.time == 0)
        #expect(restarted.seekGeneration == final.seekGeneration + 1)
        resetsFog = continuity.consume(restarted)
        #expect(resetsFog)
    }

    @Test func `mode changes fog reentry and settled previews invalidate continuity`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        var frame = request(playback)
        var resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        resetsFog = continuity.consume(frame)
        #expect(!resetsFog)
        frame.mode = .noLidar
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        resetsFog = continuity.consume(frame)
        #expect(!resetsFog)
        frame.fog = .off
        resetsFog = continuity.consume(frame)
        #expect(!resetsFog)
        frame.fog = .on
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        frame.time = 1e5
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        frame.time = 0
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        resetsFog = continuity.consume(frame)
        #expect(!resetsFog)
    }

    @Test func `offscreen clock rewinds and nonadjacent jumps reset without a player`() {
        var continuity = FogContinuity()
        var frame = FrameRequest(mode: .lidar, keyframe: 0, time: 0.2, reduceMotion: false, fog: .on)
        var resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        frame.time = 0.1
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        frame.keyframe = 3
        frame.time = Schedule.start(of: 3)
        resetsFog = continuity.consume(frame)
        #expect(resetsFog)
        resetsFog = continuity.consume(frame)
        #expect(!resetsFog)
    }
}
