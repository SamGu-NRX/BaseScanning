import LiveDotsCore
import Observation
import QuartzCore

/// The playback state the controls edit and the renderer reads. Keyframes play at 4 per second
/// and cut; the dots animate on the same clock.
@Observable
final class ReplayPlayer {
    let keyframeCount: Int
    var mode: CaptureMode = .lidar
    var scheme: DotScheme = .hologram
    var reduceMotion: Bool
    var fog: FogStyle = .on
    private(set) var isPlaying = false
    private(set) var keyframe = 0
    var playhead: Float { playback.playhead }
    @ObservationIgnored private var playback: ReplayPlayback

    init(keyframeCount: Int, reduceMotion: Bool) {
        self.keyframeCount = keyframeCount
        self.playback = ReplayPlayback(keyframeCount: keyframeCount)
        self.reduceMotion = reduceMotion
    }

    var request: FrameRequest {
        playback.frameRequest(mode: mode, reduceMotion: reduceMotion, fog: fog, scheme: scheme)
    }

    func togglePlayback() {
        playback.togglePlayback()
        updateControls()
    }

    /// Jumps to a keyframe and shows it settled. Scrubbing while playing keeps playing from there.
    func scrub(to index: Int) {
        playback.scrub(to: index)
        updateControls()
    }

    /// Advances the clock; the Metal view calls this once per display frame.
    func tick(now: CFTimeInterval) {
        playback.tick(now: now)
        updateControls()
    }

    private func updateControls() {
        if isPlaying != playback.isPlaying { isPlaying = playback.isPlaying }
        if keyframe != playback.keyframe { keyframe = playback.keyframe }
    }
}
