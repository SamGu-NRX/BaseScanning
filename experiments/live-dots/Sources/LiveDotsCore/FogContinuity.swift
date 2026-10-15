/// Keeps fog easing within one continuous playback, never across a seek or a settled preview.
public struct FogContinuity {
    private var previous: FrameRequest?

    public init() {}

    /// Returns whether the renderer must replace its lagged mask with the current frame's mask.
    public mutating func consume(_ request: FrameRequest) -> Bool {
        guard request.fog == .on else {
            previous = nil
            return false
        }
        defer { previous = request }
        guard let previous else { return true }
        return request.isSettled || previous.isSettled
            || request.seekGeneration != previous.seekGeneration
            || request.mode != previous.mode
            || !(0...1).contains(request.keyframe - previous.keyframe)
            || request.time < previous.time
    }
}
