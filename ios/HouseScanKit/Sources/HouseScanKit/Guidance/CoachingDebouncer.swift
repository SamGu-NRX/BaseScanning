import Foundation

/// Turns the capture gate's per-frame verdicts into coaching that stays up long enough to read.
///
/// The gate judges about ten frames a second and gives one reason per frame: the first check
/// that fails (`AutoCapture.evaluate`). Shown as it comes, that reason flickers. In the 4.1 field
/// test (#80) a night scene's mean luma sat right on the gate's 40, so "too dark" came and went
/// every few seconds, and a tilt in the dark swapped it for "Slow down", because motion is
/// checked before exposure. Tilting and turning in place also read as walking too fast (#26).
///
/// So each problem keeps its own clock, and a frame that reports another problem never restarts
/// a problem that is showing:
/// - Darkness is judged from the frames' own mean luma, not from the gate's reason, with
///   hysteresis: it shows once most recent frames are dark, and clears only once nearly none
///   are and the light has stayed up for a while. It comes first: while it is dark, the motion
///   problems don't take its place.
/// - Moving fast, turning fast and blur each show after their own spell of frames that have
///   them, and clear a moment after the last such frame. A spell that ends before it shows says
///   nothing.
/// - Blur is also judged from the frames' own sharpness against the sharpness from just before
///   the blur began. The gate compares each frame with the median of its last 15, so it stops
///   calling steady blur blurry within a second; held against the earlier frames, steady blur
///   stays a spell until the photos are sharp again.
/// - Walking too fast is not raised, or counted toward a spell, while the homeowner is asked to
///   stand and aim (`aiming`): the motion there is turning the phone, and "walk slower" is the
///   wrong advice.
///
/// Every threshold is a guess to try on a phone at night, not measured.
public struct CoachingDebouncer: Sendable {
    /// What the capture gate's coaching can say.
    public enum GateProblem: Sendable, Equatable, Hashable {
        /// Most recent frames are too dark to count.
        case tooDark
        /// Frames have been mostly dark for a long time. Said in place of `tooDark` until the
        /// light comes back, and once per debouncer (one scan).
        case persistentlyDark
        /// Walking faster than the gate keeps photos at.
        case movingFast
        /// Turning or tilting the phone faster than the gate keeps photos at.
        case turningFast
        /// Frames much less sharp than the recent ones, for a while.
        case blurry
    }

    public struct Config: Sendable, Equatable {
        /// A frame is dark below this mean luma: the gate's own `AutoCaptureConfig.minMeanLuma`.
        public var darkLuma: Double = AutoCaptureConfig().minMeanLuma
        /// Darkness is judged over the measured frames of the last 2 s.
        public var darkWindow: Double = 2
        /// Fewer measured frames than this in the window (about half a second of them) say
        /// nothing about darkness, so the first frames of a walk can't raise it alone.
        public var minDarkSamples = 5
        /// Shows once more than half the measured frames in the window are dark.
        public var showDarkShare: Double = 0.5
        /// Clears once fewer than a fifth of them are dark...
        public var clearDarkShare: Double = 0.2
        /// ...and each measured frame is above 48, well clear of `darkLuma`...
        public var clearLuma: Double = 48
        /// ...for 1.5 s in a row.
        public var clearDarkAfter: Double = 1.5
        /// Dark shown for 20 s, with more than half the measured frames of those 20 s dark,
        /// becomes `persistentlyDark`: waiting for the light won't help.
        public var persistentDarkAfter: Double = 20
        /// Moving or turning fast shows after 0.7 s of it: one fast frame is a jolt, not a habit.
        public var motionShowAfter: Double = 0.7
        /// Blur shows only after 2 s of it. The gate's blur test compares a frame with the recent
        /// median, and a tilt changes the view enough to trip it with no real blur.
        ///
        /// The gate alone can't make a spell that long: its median is over the last 15 measured
        /// frames (`AutoCaptureConfig.sharpnessWindow`), so after about 8 blurry frames in a row
        /// the median is itself blurry and the next frame passes. So a frame also counts as
        /// blurry when it is under `blurRatio` of `blurReference`, the sharpness from before the
        /// spell began, and steady blur (shaky hands at night, a long exposure) keeps its spell.
        public var blurShowAfter: Double = 2
        /// A frame under this share of the sharpness from before the blur counts as blurry: the
        /// gate's own `AutoCaptureConfig.sharpnessRatio`.
        public var blurRatio: Double = AutoCaptureConfig().sharpnessRatio
        /// The sharpness from before the blur is the median of the measured frames of the 1.5 s
        /// before its spell began, about the gate's 15 frames, and needs `minDarkSamples` of them.
        /// It stays fixed while the spell lasts, so it doesn't catch up with the blur as the
        /// gate's median does.
        public var blurReferenceWindow: Double = 1.5
        /// A motion or blur problem that shows clears 0.5 s after the last frame that had it.
        public var clearAfter: Double = 0.5

        public init() {}
    }

    /// One continuous run of frames with a motion or blur problem.
    private struct Spell: Sendable, Equatable {
        var since: Double
        var lastSeen: Double
    }

    private enum PersistentDark: Sendable, Equatable {
        case notYet
        case showing
        /// Said, and the light came back since: not said again.
        case said
    }

    public let config: Config
    private var lastTime: Double?
    private var spells: [GateProblem: Spell] = [:]
    /// Measured frames of the last `persistentDarkAfter` seconds: time and whether it was dark.
    private var luma: [(time: Double, dark: Bool)] = []
    /// Measured frames of the last `blurReferenceWindow` seconds: time and sharpness.
    private var sharpness: [(time: Double, value: Double)] = []
    /// The median sharpness from before the current blur spell (`Config.blurReferenceWindow`),
    /// nil while there is no spell.
    private var blurReference: Double?
    /// When the dark coaching went up, nil while it is down.
    private var darkSince: Double?
    /// Since when the light has been back, while the dark coaching is up.
    private var brightSince: Double?
    private var persistentDark = PersistentDark.notYet

    public init(config: Config = Config()) {
        self.config = config
    }

    /// Whether the dark coaching (`tooDark` or `persistentlyDark`) is up.
    public var isDark: Bool { darkSince != nil }

    /// Takes one judged frame and returns the problem to coach, if any.
    ///
    /// - `time`: the frame's timestamp in seconds. A time before the last one (a replay that
    ///   restarted) starts over.
    /// - `skip`: the gate's reason for skipping the frame, nil when the gate passed it.
    /// - `meanLuma`: the frame's own mean luma, nil when it wasn't measured.
    /// - `sharpness`: the frame's own sharpness (`FrameQuality.sharpness`), nil when it wasn't
    ///   measured.
    /// - `aiming`: the homeowner is asked to stand and aim (an aim, tilt, step-back, see-behind
    ///   or marking step), so walking too fast is not raised.
    public mutating func update(time: Double, skip: CaptureDecision.SkipReason?, meanLuma: Double?, sharpness: Double? = nil, aiming: Bool) -> GateProblem? {
        if let lastTime, time < lastTime { self = CoachingDebouncer(config: config) }
        lastTime = time
        updateDarkness(time: time, meanLuma: meanLuma)
        updateMotion(time: time, skip: skip, sharpness: sharpness)
        // Walking speed isn't coached while aiming, so a spell there mustn't build up either: it
        // would say "Slow down" on the first frames of the next walking step, however calm.
        if aiming { spells[.movingFast] = nil }
        if darkSince != nil { return persistentDark == .showing ? .persistentlyDark : .tooDark }
        if isShown(.turningFast, at: time) { return .turningFast }
        if !aiming, isShown(.movingFast, at: time) { return .movingFast }
        if isShown(.blurry, at: time) { return .blurry }
        return nil
    }

    /// Forgets the motion and blur spells, for example while tracking is lost. Darkness is kept:
    /// the light hasn't changed because the phone lost its place.
    public mutating func forgetMotion() {
        spells = [:]
        sharpness = []
        blurReference = nil
    }

    // MARK: Darkness

    private mutating func updateDarkness(time: Double, meanLuma: Double?) {
        guard let meanLuma else { return }
        luma.append((time: time, dark: meanLuma < config.darkLuma))
        let keepFrom = time - max(config.persistentDarkAfter, config.darkWindow)
        if let firstKept = luma.firstIndex(where: { $0.time > keepFrom }), firstKept > 0 {
            luma.removeFirst(firstKept)
        }
        let recent = luma.filter { $0.time > time - config.darkWindow }
        let share = Double(recent.filter { $0.dark }.count) / Double(max(recent.count, 1))
        guard let since = darkSince else {
            if recent.count >= config.minDarkSamples, share > config.showDarkShare {
                darkSince = time
                brightSince = nil
            }
            return
        }
        if share < config.clearDarkShare, meanLuma > config.clearLuma {
            if brightSince == nil { brightSince = time }
        } else {
            brightSince = nil
        }
        if let bright = brightSince, time - bright >= config.clearDarkAfter {
            darkSince = nil
            brightSince = nil
            if persistentDark == .showing { persistentDark = .said }
            return
        }
        if persistentDark == .notYet, time - since >= config.persistentDarkAfter {
            let longShare = Double(luma.filter { $0.dark }.count) / Double(max(luma.count, 1))
            if longShare > config.showDarkShare { persistentDark = .showing }
        }
    }

    // MARK: Motion and blur

    private mutating func updateMotion(time: Double, skip: CaptureDecision.SkipReason?, sharpness value: Double?) {
        var evidence = Self.evidence(skip)
        // The sharpness from before any blur: fixed while a spell lasts, else the recent median.
        let reference = blurReference ?? Self.median(sharpness.map(\.value), atLeast: config.minDarkSamples)
        if skip != .trackingNotReady, let value, let reference, value < reference * config.blurRatio {
            evidence[.blurry] = true
        }
        for (problem, seen) in evidence {
            if seen {
                spells[problem] = Spell(since: spells[problem]?.since ?? time, lastSeen: time)
            } else if let spell = spells[problem], !reachedShow(problem, spell) {
                // A spell that ends before it shows says nothing.
                spells[problem] = nil
            }
        }
        spells = spells.filter { time - $0.value.lastSeen < config.clearAfter }
        blurReference = spells[.blurry] == nil ? nil : reference
        if let value {
            sharpness.append((time: time, value: value))
            if let firstKept = sharpness.firstIndex(where: { $0.time > time - config.blurReferenceWindow }), firstKept > 0 {
                sharpness.removeFirst(firstKept)
            }
        }
    }

    private static func median(_ values: [Double], atLeast count: Int) -> Double? {
        guard values.count >= count else { return nil }
        return values.sorted()[values.count / 2]
    }

    private func isShown(_ problem: GateProblem, at time: Double) -> Bool {
        guard let spell = spells[problem] else { return false }
        return reachedShow(problem, spell) && time - spell.lastSeen < config.clearAfter
    }

    private func reachedShow(_ problem: GateProblem, _ spell: Spell) -> Bool {
        let after = problem == .blurry ? config.blurShowAfter : config.motionShowAfter
        return spell.lastSeen - spell.since >= after
    }

    /// What one verdict says about each motion and blur problem: true when the frame had it,
    /// false when the gate checked and the frame didn't, and nothing when a check that comes
    /// first failed (`AutoCapture.evaluate` checks tracking, then speed, then turning, then
    /// exposure, then sharpness).
    private static func evidence(_ skip: CaptureDecision.SkipReason?) -> [GateProblem: Bool] {
        switch skip {
        case .trackingNotReady?: [:]
        case .movingFast?: [.movingFast: true]
        case .turningFast?: [.movingFast: false, .turningFast: true]
        case .tooDark?, .tooBright?: [.movingFast: false, .turningFast: false]
        case .blurry?: [.movingFast: false, .turningFast: false, .blurry: true]
        case .tooSoon?, .redundant?, nil: [.movingFast: false, .turningFast: false, .blurry: false]
        }
    }
}

extension GapPlan.Need {
    /// Whether the request asks the homeowner to walk rather than stand and aim. Only a walk past
    /// the stretch far enough out does ("Walk along this stretch... Follow the dotted line"), so
    /// on it the capture gate's coaching says "Slow down" as on the walk; on the others walking
    /// speed isn't coached (`CoachingDebouncer.update`'s `aiming`). No `default`, so a new need
    /// has to pick a side.
    public var asksToWalk: Bool {
        switch self {
        case .walkOut: true
        case .cells, .groundOut, .overhead, .wallUp: false
        }
    }
}
