/// How the unmeasured world is shown.
public enum FogStyle: String, CaseIterable, Sendable {
    /// The fog layer: one continuous haze that dissolves where dots arrive.
    case on
    /// The old comparison: a flat 35% veil over 30 cm wall cells no dot has reached.
    case veil
    case off
}

/// What one frame shows: which keyframe, when on the playback clock, and the toggles.
public struct FrameRequest: Equatable, Sendable {
    public var mode: CaptureMode
    public var keyframe: Int
    /// Playback seconds; animations are evaluated at this time.
    public var time: Float
    public var reduceMotion: Bool
    public var fog: FogStyle
    public var scheme: DotScheme = .hologram
    /// Seconds on a clock that keeps running while playback is paused, for the fog's drift.
    public var ambientTime: Float = 0
    /// Seconds since the previous frame, for the fog's lag.
    public var frameInterval: Float = 1 / 60

    /// Changes on explicit seeks and on resuming from a settled scrub.
    public var seekGeneration: UInt64 = 0

    public init(mode: CaptureMode, keyframe: Int, time: Float, reduceMotion: Bool, fog: FogStyle,
                scheme: DotScheme = .hologram, ambientTime: Float = 0, frameInterval: Float = 1 / 60,
                seekGeneration: UInt64 = 0) {
        self.mode = mode
        self.keyframe = keyframe
        self.time = time
        self.reduceMotion = reduceMotion
        self.fog = fog
        self.scheme = scheme
        self.ambientTime = ambientTime
        self.frameInterval = frameInterval
        self.seekGeneration = seekGeneration
    }

    /// A scrub or a still shows its keyframe settled; the renderer passes a far-future time.
    public var isSettled: Bool { time > 1e4 }
}
