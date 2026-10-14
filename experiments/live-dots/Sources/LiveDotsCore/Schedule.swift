/// When each keyframe appears on the playback clock. Keyframes play at 4 per second, except the
/// third, which holds for the 1 s "Hold still" ring fill and the 150 ms bracket flash after it.
public enum Schedule {
    public static let holdIndex = 2
    public static let holdDuration: Float = 1
    public static let flashDuration: Float = 0.15

    static var interval: Float { 1 / Tuning.keyframesPerSecond }

    /// Playback seconds at which keyframe `index` appears.
    public static func start(of index: Int) -> Float {
        Float(index) * interval + (index > holdIndex ? holdDuration + flashDuration - interval : 0)
    }

    /// The keyframe on screen at playback time `t`.
    public static func keyframe(at t: Float, count: Int) -> Int {
        var index = 0
        while index + 1 < count, start(of: index + 1) <= t { index += 1 }
        return index
    }

    /// When the last keyframe's slot ends.
    public static func end(count: Int) -> Float {
        start(of: count)
    }

    /// When playback stops: past the last keyframe's slot, long enough for its births to finish.
    public static func playbackEnd(count: Int) -> Float {
        end(count: count) + Tuning.birthDuration
    }

    /// Seconds keyframe `index` stays on screen: until the next keyframe appears, or for the last
    /// one, until playback stops. `keyframe(at:count:)` holds the last keyframe forever, so it
    /// cannot bound an offset into that one.
    public static func onScreen(_ index: Int, count: Int) -> Float {
        (index + 1 < count ? start(of: index + 1) : playbackEnd(count: count)) - start(of: index)
    }
}
