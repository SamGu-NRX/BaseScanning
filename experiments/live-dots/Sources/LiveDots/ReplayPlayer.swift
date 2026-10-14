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
    /// Playback seconds. Keyframe k is on screen from k / 4 to (k + 1) / 4.
    @ObservationIgnored private(set) var playhead: Float = 0
    /// After a scrub the frame shows its settled state instead of replaying births.
    @ObservationIgnored private var settled = false
    @ObservationIgnored private var lastTick: CFTimeInterval?
    @ObservationIgnored private var lastFrame: CFTimeInterval?
    @ObservationIgnored private var ambient: Float = 0
    @ObservationIgnored private var frameInterval: Float = 1 / 60

    /// Playback runs this long past the last keyframe so its births finish.
    private var end: Float { Schedule.playbackEnd(count: keyframeCount) }

    init(keyframeCount: Int, reduceMotion: Bool) {
        self.keyframeCount = keyframeCount
        self.reduceMotion = reduceMotion
    }

    var request: FrameRequest {
        FrameRequest(
            mode: mode, keyframe: keyframe, time: settled ? 1e5 : playhead,
            reduceMotion: reduceMotion, fog: fog, scheme: scheme, ambientTime: ambient, frameInterval: frameInterval)
    }

    func togglePlayback() {
        if isPlaying {
            isPlaying = false
            return
        }
        if playhead >= end - 1e-3 || (keyframe == keyframeCount - 1 && settled) { seek(to: 0, settle: false) }
        settled = false
        lastTick = nil
        isPlaying = true
    }

    /// Jumps to a keyframe and shows it settled. Scrubbing while playing keeps playing from there.
    func scrub(to index: Int) {
        seek(to: index, settle: !isPlaying)
    }

    private func seek(to index: Int, settle: Bool) {
        keyframe = min(max(index, 0), keyframeCount - 1)
        playhead = Schedule.start(of: keyframe)
        settled = settle
        lastTick = nil
    }

    /// Advances the clock; the Metal view calls this once per display frame.
    func tick(now: CFTimeInterval) {
        if let lastFrame { frameInterval = min(Float(now - lastFrame), 0.1) }
        lastFrame = now
        ambient += frameInterval
        guard isPlaying else { return }
        if let lastTick { playhead += Float(now - lastTick) }
        lastTick = now
        let index = Schedule.keyframe(at: playhead, count: keyframeCount)
        if index != keyframe { keyframe = index }
        if playhead >= end {
            playhead = end
            isPlaying = false
        }
    }
}
