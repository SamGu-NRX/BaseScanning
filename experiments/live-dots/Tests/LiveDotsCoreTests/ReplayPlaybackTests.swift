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
        #expect(continuity.consume(request(playback)))
        playback.tick(now: 0.2)
        let before = request(playback)
        #expect(!continuity.consume(before))
        playback.scrub(to: 0)
        let after = request(playback)
        #expect(after.keyframe == before.keyframe)
        #expect(after.time == 0 && !after.isSettled)
        #expect(after.seekGeneration == before.seekGeneration + 1)
        #expect(continuity.consume(after))
        playback.tick(now: 0.3)
        #expect(!continuity.consume(request(playback)))
    }

    @Test func `natural playback eases across same and adjacent frames but explicit seeks reset`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        #expect(continuity.consume(request(playback)))
        playback.tick(now: 0.1)
        #expect(!continuity.consume(request(playback)))
        playback.tick(now: 0.3)
        #expect(playback.keyframe == 1)
        #expect(!continuity.consume(request(playback)))
        playback.scrub(to: 2)
        #expect(continuity.consume(request(playback)))
        playback.scrub(to: 1)
        #expect(continuity.consume(request(playback)))
    }

    @Test(arguments: [false, true]) func `resuming a settled scrub resets at the birth time`(reduceMotion: Bool) {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.scrub(to: 2)
        let preview = request(playback, reduceMotion: reduceMotion)
        #expect(preview.isSettled)
        #expect(continuity.consume(preview))
        playback.togglePlayback()
        let resumed = request(playback, reduceMotion: reduceMotion)
        #expect(resumed.time == Schedule.start(of: 2) && !resumed.isSettled)
        #expect(resumed.seekGeneration == preview.seekGeneration + 1)
        #expect(resumed.reduceMotion == reduceMotion)
        #expect(continuity.consume(resumed))
        #expect(!continuity.consume(resumed))
    }

    @Test func `scrub and resume between rendered frames still invalidate the old mask`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        #expect(continuity.consume(request(playback)))
        playback.tick(now: 0.2)
        #expect(!continuity.consume(request(playback)))
        playback.togglePlayback()
        playback.scrub(to: 0)
        playback.togglePlayback()
        #expect(continuity.consume(request(playback)))
    }

    @Test func `pause and resume without a scrub preserve the playhead and fog easing`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        playback.tick(now: 0)
        #expect(continuity.consume(request(playback)))
        playback.tick(now: 0.2)
        let before = request(playback)
        #expect(!continuity.consume(before))
        playback.togglePlayback()
        playback.tick(now: 5)
        playback.togglePlayback()
        playback.tick(now: 10)
        let resumed = request(playback)
        #expect(resumed.time == before.time)
        #expect(resumed.seekGeneration == before.seekGeneration)
        #expect(!continuity.consume(resumed))
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
        #expect(continuity.consume(final))
        playback.togglePlayback()
        let restarted = request(playback)
        #expect(restarted.keyframe == 0 && restarted.time == 0)
        #expect(restarted.seekGeneration == final.seekGeneration + 1)
        #expect(continuity.consume(restarted))
    }

    @Test func `mode changes fog reentry and settled previews invalidate continuity`() {
        var playback = ReplayPlayback(keyframeCount: 10)
        var continuity = FogContinuity()
        playback.togglePlayback()
        var frame = request(playback)
        #expect(continuity.consume(frame))
        #expect(!continuity.consume(frame))
        frame.mode = .noLidar
        #expect(continuity.consume(frame))
        #expect(!continuity.consume(frame))
        frame.fog = .off
        #expect(!continuity.consume(frame))
        frame.fog = .on
        #expect(continuity.consume(frame))
        frame.time = 1e5
        #expect(continuity.consume(frame))
        frame.time = 0
        #expect(continuity.consume(frame))
        #expect(!continuity.consume(frame))
    }

    @Test func `offscreen clock rewinds and nonadjacent jumps reset without a player`() {
        var continuity = FogContinuity()
        var frame = FrameRequest(mode: .lidar, keyframe: 0, time: 0.2, reduceMotion: false, fog: .on)
        #expect(continuity.consume(frame))
        frame.time = 0.1
        #expect(continuity.consume(frame))
        frame.keyframe = 3
        frame.time = Schedule.start(of: 3)
        #expect(continuity.consume(frame))
        #expect(!continuity.consume(frame))
    }
}
