/// The replay clock shared by the interactive player and its continuity tests.
public struct ReplayPlayback {
    public let keyframeCount: Int
    public private(set) var isPlaying = false
    public private(set) var keyframe = 0
    public private(set) var playhead: Float = 0
    public private(set) var seekGeneration: UInt64 = 0
    private var settled = false
    private var lastTick: Double?
    private var lastFrame: Double?
    private var ambient: Float = 0
    private var frameInterval: Float = 1 / 60

    private var end: Float { Schedule.playbackEnd(count: keyframeCount) }

    public init(keyframeCount: Int) {
        precondition(keyframeCount > 0, "replay playback needs at least one keyframe")
        self.keyframeCount = keyframeCount
    }

    public func frameRequest(mode: CaptureMode, reduceMotion: Bool, fog: FogStyle,
                             scheme: DotScheme = .hologram) -> FrameRequest {
        FrameRequest(
            mode: mode, keyframe: keyframe, time: settled ? 1e5 : playhead,
            reduceMotion: reduceMotion, fog: fog, scheme: scheme, ambientTime: ambient,
            frameInterval: frameInterval, seekGeneration: seekGeneration)
    }

    public mutating func togglePlayback() {
        if isPlaying {
            isPlaying = false
            return
        }
        if playhead >= end - 1e-3 || (keyframe == keyframeCount - 1 && settled) {
            seek(to: 0, settle: false)
        } else if settled {
            // Births restart at the scrubbed keyframe's start, not at its settled preview time.
            seekGeneration &+= 1
        }
        settled = false
        lastTick = nil
        isPlaying = true
    }

    public mutating func scrub(to index: Int) {
        seek(to: index, settle: !isPlaying)
    }

    private mutating func seek(to index: Int, settle: Bool) {
        keyframe = min(max(index, 0), keyframeCount - 1)
        playhead = Schedule.start(of: keyframe)
        settled = settle
        seekGeneration &+= 1
        lastTick = nil
    }

    public mutating func tick(now: Double) {
        if let lastFrame { frameInterval = min(Float(now - lastFrame), 0.1) }
        lastFrame = now
        ambient += frameInterval
        guard isPlaying else { return }
        if let lastTick { playhead += Float(now - lastTick) }
        lastTick = now
        keyframe = Schedule.keyframe(at: playhead, count: keyframeCount)
        if playhead >= end {
            playhead = end
            isPlaying = false
        }
    }
}
