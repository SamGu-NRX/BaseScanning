import Foundation
import simd

/// What the walk asks for next.
public enum GuidanceTask: Sendable, Equatable {
    /// Walk along the wall toward a side.
    case walk(WalkSide)
    /// The walk has gone `GuidanceConfig.reach` along this side; ask the homeowner to mark where
    /// the wall ends.
    case markEnd(WalkSide)
    /// The ground around `s` is missing: tilt down. The first request of the walk is this at
    /// s = 0, the ground in front of the meter, while the phone is near the meter.
    case aimAtGround(s: Float)
    /// The wall band lags around `s`: tilt up or step back.
    case aimAtWall(s: Float)
    /// The camera is too close to the wall to see the band.
    case stepBack
    /// LiDAR found something standing in front of the wall or ground around `s` (hidden cells):
    /// look at that part from another angle or step around the obstruction.
    case seeBehind(s: Float)
    /// Both ends marked and nothing lags between them.
    case complete
}

public struct GuidanceConfig: Sendable, Equatable {
    /// Keep a task at least this long unless it is satisfied, so instructions never flip A, B, A
    /// within 3 s except when a task was completed, which checklist item I6 allows (for example
    /// step back, done, walk on).
    public var minDwell: Double = 3
    /// Ask for the end once the walk has gone this far from the meter on a side: about 20 ft, past
    /// which Base's public 20 ft cable limit (docs/04, Public rule values) rules out for a
    /// placement anyway. It is the walked distance (`CoverageMap.walkedFarthest`), not unbroken
    /// coverage: a hole near the meter held the prompt back for the whole walk on device run 1,
    /// and holes are the aim tasks' and the gap loop's job. "Wall ends here" is on offer before
    /// this for a shorter wall.
    public var reach: Float = 6.1
    /// Closer to the wall than this, a portrait phone camera sees less than about 1.4 m of it.
    public var tooClose: Float = 1.2
    /// A band lagging over at least this much wall near the camera triggers an aim task
    /// (3 cells of 6 in: one missed stride).
    public var lagRun: Float = 0.45
    /// Where the homeowner should stand: this far out from the wall. The walked path is also the
    /// scan's evidence that the space in front of the wall is clear (`CoverageMap.facingSpans()`),
    /// and the server's facing check needs that clearance to exceed D + r = 1.83 + 3 = 4.83 ft
    /// under the public rules, after the position error of 0.3 ft plus 0.16 ft per foot from the
    /// meter is taken off. At the old 1.5 m (4.92 ft) that never happens: 4.92 - 0.3 = 4.62 ft.
    /// At 2 m (6.56 ft), 6.56 - 0.3 - 0.16 |s| > 4.83 holds within 8.9 ft of the meter; for a
    /// battery whose far edge is 5 ft out that leaves 0.63 ft (19 cm) for the walk to drift
    /// closer. Private rules may differ; the server's request names the depth it needs.
    public var standOff: Float = 2.0
    /// The ground path is drawn at most this long, so it never leads far into unseen ground.
    public var maxPathLength: Float = 3
    /// An aim task whose stretch has gained no covered cell for this long stalls: its stretch is
    /// deferred, the walk goes on, and it is asked for again once both ends are marked
    /// (`GuidancePlanner.firstHole`), where "Can't get there" settles it. 20 s is a guess, not
    /// measured: on build 4.1's field runs the first ground task held the start for 70 to 151 s
    /// (#77), and 20 s leaves time for a few tilts and a step to the side.
    public var stallTimeout: Double = 20

    public init() {}
}

/// Why the walk's task changed on an update (`GuidanceOutput.switched`), for the log.
public enum GuidanceSwitch: String, Sendable, Equatable {
    /// The task was met.
    case satisfied
    /// A task other than an aim was held for `minDwell`, or something hidden near the camera
    /// took over from an aim task.
    case dwell
    /// An aim task's stretch left the camera's window (`GuidancePlanner.lagWindow`) or lies past
    /// a marked end.
    case leftWindow
    /// An aim task gained nothing for `GuidanceConfig.stallTimeout`; its stretch is deferred.
    case stalled
}

public struct GuidanceOutput: Sendable, Equatable {
    public var task: GuidanceTask
    /// A world point to aim at.
    public var target: SIMD3<Float>?
    /// A ground path from the homeowner toward where to stand next, world points.
    public var path: [SIMD3<Float>]
    /// The aim task that stalled on this update and was deferred (`GuidanceConfig.stallTimeout`).
    public var stalled: GuidanceTask? = nil
    /// Why the task changed on this update; nil when it didn't, and for the first task.
    public var switched: GuidanceSwitch? = nil
    /// An aim task while the camera is closer to the wall than `GuidanceConfig.tooClose`: the
    /// task stays, and the card asks to step back as well, instead of a separate "Take a step
    /// back" card that read as a different request (#77).
    public var stepBack = false
    /// An aim task whose open rows have each been seen once, from about where the camera is now
    /// (`CoverageMap.needsSecondPosition`): looking again from here adds nothing, a step to the
    /// side does.
    public var needsSecondPosition = false
}

/// Chooses the next walk task from coverage and the camera, with hysteresis.
public struct GuidancePlanner: Sendable {
    public let config: GuidanceConfig
    public private(set) var current: GuidanceTask?
    private var since: Double = 0
    /// The current aim task's covered share when it last grew, and when: the stall clock.
    private var lastFraction: Double = 0
    private var lastProgressAt: Double = 0
    private var pausedAt: Double?
    /// Aim stretches that stalled. The requests chosen from the camera (the ground by the meter
    /// and a lagging band) leave them out for the rest of the scan, until `reset()`; `firstHole`
    /// still asks for them once both ends are marked.
    private var deferred: [DeferredStretch] = []

    private struct DeferredStretch: Sendable {
        var band: SurfaceBand
        var range: ClosedRange<Float>
    }

    public init(config: GuidanceConfig = GuidanceConfig()) {
        self.config = config
    }

    /// Drops the current task, so the next update chooses afresh, and keeps the stretches already
    /// deferred. The engine calls it when an action settled the task ("Can't get there", an
    /// answered end or overhead question, a followed corner, a refused finish). Clearing the
    /// deferred stretches there too asked again, with a fresh stall clock, for a request that had
    /// just stalled, instead of going on with the walk (#56).
    public mutating func settleCurrentTask() {
        current = nil
    }

    /// Forgets the current task and the deferred stretches. For a new scan, and for a spatial
    /// reset, whose new world frame makes the old stretches meaningless.
    public mutating func reset() {
        current = nil
        deferred = []
        pausedAt = nil
    }

    /// Records whether the aim can be acted on, without choosing or settling a task. The engine
    /// calls this even when a question or another instruction bypasses `update` (review of #237).
    public mutating func recordAimActionability(_ actionable: Bool, time: Double) {
        if let pausedAt {
            lastProgressAt += max(0, time - pausedAt)
            self.pausedAt = actionable ? nil : time
        } else if !actionable {
            pausedAt = time
        }
    }

    /// The task for this moment.
    ///
    /// - A satisfied task gives way at once.
    /// - An unsatisfied aim task is held while its stretch is still in the camera's window and
    ///   between the ends (`holds`), however long that takes, unless it stalls: no covered cell
    ///   gained for `stallTimeout`. A stalled stretch is deferred and the walk goes on. On build
    ///   4.1 the 3 s dwell was also the most any card was held, so the card changed 22 times in
    ///   86 s (#84). Only a hidden stretch near the camera takes over from a held aim task; when
    ///   the camera comes closer than `tooClose`, the aim task stays and `stepBack` is set.
    /// - Any other task gives way to the preferred one once held for `minDwell`.
    ///
    /// Deterministic in `time`.
    public mutating func update(coverage: CoverageMap, camera: CameraFrame?, time: Double, aimActionable: Bool = true) -> GuidanceOutput {
        recordAimActionability(aimActionable, time: time)
        var preferred = preferredTask(coverage: coverage, camera: camera)
        var switched: GuidanceSwitch?
        var stalled: GuidanceTask?
        if let current {
            let satisfied = isSatisfied(current, coverage: coverage, camera: camera)
            let aim = Self.aim(current)
            if let aim, !satisfied {
                let fraction = aimWindow(for: current, coverage: coverage).map { coverage.coveredFraction(aim.band, in: $0) } ?? 0
                // A clock that went back (a replay restarted) restarts the stall clock too.
                if fraction > lastFraction || time < lastProgressAt {
                    lastFraction = fraction
                    lastProgressAt = time
                }
            }
            if let aim, aimActionable, !satisfied, time - lastProgressAt >= config.stallTimeout,
               let range = aimWindow(for: current, coverage: coverage) {
                if !deferred.contains(where: { $0.band == aim.band && $0.range == range }) {
                    deferred.append(DeferredStretch(band: aim.band, range: range))
                }
                stalled = current
                preferred = preferredTask(coverage: coverage, camera: camera)
                // `firstHole` asks for a deferred stretch again once both ends are marked, so the
                // same task can come back here; the log then shows the stall but no switch.
                switched = preferred == current ? nil : .stalled
                begin(preferred, coverage: coverage, time: time)
            } else if current != preferred {
                if satisfied {
                    switched = .satisfied
                    begin(preferred, coverage: coverage, time: time)
                } else if aim != nil, Self.holds(current, preferred, coverage: coverage, camera: camera) {
                    // Held: see above.
                } else if time - since >= config.minDwell {
                    switched = aim != nil && !Self.stillInView(current, coverage: coverage, camera: camera) ? GuidanceSwitch.leftWindow : .dwell
                    begin(preferred, coverage: coverage, time: time)
                }
            }
        } else {
            begin(preferred, coverage: coverage, time: time)
        }
        let task = current ?? preferred
        var output = cues(for: task, coverage: coverage, camera: camera)
        output.switched = switched
        output.stalled = stalled
        if let aim = Self.aim(task), let camera, let window = aimWindow(for: task, coverage: coverage) {
            output.stepBack = coverage.wall.wallPoint(camera.position).out < config.tooClose
            output.needsSecondPosition = coverage.needsSecondPosition(band: aim.band, range: window, from: camera.position)
        }
        return output
    }

    /// Makes `task` the current one from `time`, with a fresh stall clock.
    private mutating func begin(_ task: GuidanceTask, coverage: CoverageMap, time: Double) {
        current = task
        since = time
        if let aim = Self.aim(task), let window = aimWindow(for: task, coverage: coverage) {
            lastFraction = coverage.coveredFraction(aim.band, in: window)
        } else {
            lastFraction = 0
        }
        lastProgressAt = time
    }

    /// The target and path of `task` seen from `camera`, without choosing a task. The engine calls
    /// this for frames that must not move the task (a replay frame shown for a tap) so the cues
    /// never lag behind the camera the screen shows.
    public func cues(for task: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> GuidanceOutput {
        GuidanceOutput(task: task, target: target(for: task, coverage: coverage, camera: camera), path: path(for: task, coverage: coverage, camera: camera))
    }

    // MARK: Choosing

    /// How far from the meter coverage runs unbroken on a side, over both bands (skipped cells
    /// count as done). Where the walk heads and where it stalls behind something hidden; the end
    /// prompt and a walked end go by the walked path instead (`walkedFarthest`, `WalkedEnd`).
    public func reach(_ side: WalkSide, coverage: CoverageMap) -> Float {
        let width = coverage.config.cellWidth
        var index = side == .right ? 0 : -1
        var reached: Float = 0
        let limit = Int((config.reach * 2) / width)
        for _ in 0..<limit {
            let done = SurfaceBand.allCases.allSatisfy {
                let level = coverage.level($0, index)
                return level == .covered || level == .skipped
            }
            if !done { break }
            let range = coverage.cellRange(index)
            reached = side == .right ? range.upperBound : -range.lowerBound
            index += side == .right ? 1 : -1
        }
        return reached
    }

    func preferredTask(coverage: CoverageMap, camera: CameraFrame?) -> GuidanceTask {
        if let camera, coverage.wall.wallPoint(camera.position).out < config.tooClose {
            return .stepBack
        }
        if let camera, let blocked = hiddenNearCamera(coverage: coverage, camera: camera) {
            return blocked
        }
        // Only while the camera is near the meter (or its place is unknown). On build 4.1 this
        // request came before the walk wherever the phone was, and blocked the start of every run
        // for 70 to 151 s; 20 ft into a walk the card still said "at your meter" (#77). Away from
        // the meter the walk goes on, and `firstHole` asks for the stretch once both ends are
        // marked.
        let nearMeter = camera.map { abs(coverage.wall.wallPoint($0.position).s) <= Self.lagWindow } ?? true
        if nearMeter, !groundByMeterDone(coverage), !isDeferred(.ground, at: 0) {
            return .aimAtGround(s: 0)
        }
        if let camera, let lag = laggingBand(coverage: coverage, camera: camera) {
            return lag
        }
        for side in [WalkSide.left, .right] {
            let end = side == .left ? coverage.leftEnd : coverage.rightEnd
            guard end == nil else { continue }
            return coverage.walkedFarthest(side) >= config.reach ? .markEnd(side) : .walk(side)
        }
        if let hole = firstHole(coverage: coverage) {
            return hole
        }
        return .complete
    }

    /// Half the usual stretch an aim asks for, meters either side of its s. `aimWindow` trims
    /// it beside deferred spans during the walk.
    public static let aimHalfWidth: Float = 0.3
    /// An aim task is satisfied once this share of its stretch's cells is covered.
    public static let aimSatisfied = 0.8

    /// Whether the ground in front of the meter is done: the stretch an aim task at s = 0 asks
    /// for, with skipped cells counting, so "Can't get there" on it moves the walk on. Every check
    /// the server runs starts beside the meter, and on device run 1 nothing asked for this stretch:
    /// the tilt prompts all asked for ground from 2 ft 9 in right of the meter outward. So it is
    /// the walk's first request while the phone is near the meter (`preferredTask`), before either
    /// side. Done when no cell of it is between the ends.
    func groundByMeterDone(_ coverage: CoverageMap) -> Bool {
        let window = -Self.aimHalfWidth...Self.aimHalfWidth
        let cells = coverage.indices(overlapping: window).filter(coverage.isWithinEnds)
        guard !cells.isEmpty else { return true }
        let done = cells.filter { index in
            let level = coverage.level(.ground, index)
            return level == .covered || level == .skipped
        }
        return Double(done.count) >= Self.aimSatisfied * Double(cells.count)
    }

    /// Two aim tasks that ask for the same stretch. For the same band, stretches overlapping by
    /// more than half: the lag window slides with the camera, so its middle drifts by a cell or
    /// two between updates. Switching on that drift made the card go from "3 ft 3 in" to
    /// "2 ft 9 in right of your meter" on device run 1 while it still asked for the same ground.
    /// For the ground and the wall, stretches that overlap at all: on build 4.1 "tilt up" and
    /// "tilt down" alternated over the same few feet of wall (#84). Until one card asks for both,
    /// the one on screen stays.
    static func sameStretch(_ a: GuidanceTask, _ b: GuidanceTask) -> Bool {
        switch (a, b) {
        case (.aimAtGround(let x), .aimAtGround(let y)), (.aimAtWall(let x), .aimAtWall(let y)):
            abs(x - y) < aimHalfWidth
        case (.aimAtGround(let x), .aimAtWall(let y)), (.aimAtWall(let x), .aimAtGround(let y)):
            abs(x - y) < 2 * aimHalfWidth
        default:
            false
        }
    }

    /// Meters either side of the camera within which an aim task is held (`stillInView`), and
    /// that `hiddenNearCamera` looks at; ahead of the camera, how far `laggingBand` looks.
    static let lagWindow: Float = 1

    /// How far behind the camera, against the walk, `laggingBand` still looks: 0.3 m, two cells,
    /// so ground under the phone counts. It looked 1 m back, so on build 4.1 aim targets landed up
    /// to about 3 ft behind the phone (#84).
    static let lagBehind: Float = 0.3

    /// The band and s of an aim task; nil for any other task.
    static func aim(_ task: GuidanceTask) -> (band: SurfaceBand, s: Float)? {
        switch task {
        case .aimAtGround(let s): return (band: .ground, s: s)
        case .aimAtWall(let s): return (band: .wall, s: s)
        case .walk, .markEnd, .stepBack, .seeBehind, .complete: return nil
        }
    }

    /// The stretch an aim task at `s` asks for.
    static func stretch(around s: Float) -> ClosedRange<Float> {
        (s - aimHalfWidth)...(s + aimHalfWidth)
    }

    /// The span requested by an aim. During the walk, a neighbouring deferred span trims the
    /// usual 0.6 m window without discarding the remaining lagging run. Coverage counts a cell
    /// when its non-deferred part overlaps this window, as `CoverageMap.indices` does elsewhere.
    /// Once both ends are marked, the full window returns so `firstHole` can revisit deferred
    /// spans. Nil for other tasks, or an aim centred inside a still-deferred span.
    public func aimWindow(for task: GuidanceTask, coverage: CoverageMap) -> ClosedRange<Float>? {
        guard let aim = Self.aim(task) else { return nil }
        var window = Self.stretch(around: aim.s)
        guard Self.walkingSide(coverage) != nil else { return window }
        for stretch in deferred where stretch.band == aim.band && stretch.range.overlaps(window) {
            if stretch.range.contains(aim.s) { return nil }
            if stretch.range.upperBound < aim.s {
                window = max(window.lowerBound, stretch.range.upperBound.nextUp)...window.upperBound
            } else {
                window = window.lowerBound...min(window.upperBound, stretch.range.lowerBound.nextDown)
            }
        }
        return window
    }

    /// An aim task whose stretch is still within `lagWindow` of the camera and between the marked
    /// ends. The preferred task is compared at each update, so small drifts added up: on device
    /// run 2 the card went 5 ft 3 in, 4 ft 9 in, 4 ft 6 in, 4 ft, each step under half the
    /// stretch but 0.38 m in all, while the same ground was still in front of the homeowner. Past
    /// a marked end nothing is recorded, so the request could never be met.
    static func stillInView(_ task: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> Bool {
        guard let aim = Self.aim(task), let camera, isWithinEnds(aim.s, coverage) else { return false }
        return abs(aim.s - coverage.wall.wallPoint(camera.position).s) <= lagWindow
    }

    /// Whether an unsatisfied aim task stays on screen although `preferred` differs: never past a
    /// marked end (by either rule below: review of #56), never over a hidden stretch near the
    /// camera, and otherwise while `preferred` asks for the same stretch (`sameStretch`) or the
    /// task's own stretch is still in view (`stillInView`). A preferred "step back" doesn't
    /// replace it: `update` sets `GuidanceOutput.stepBack` instead.
    static func holds(_ current: GuidanceTask, _ preferred: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> Bool {
        guard let aim = Self.aim(current), isWithinEnds(aim.s, coverage) else { return false }
        if case .seeBehind = preferred { return false }
        return sameStretch(current, preferred) || stillInView(current, coverage: coverage, camera: camera)
    }

    static func isWithinEnds(_ s: Float, _ coverage: CoverageMap) -> Bool {
        s >= (coverage.leftEnd ?? -.infinity) && s <= (coverage.rightEnd ?? .infinity)
    }

    /// The side the walk is asked to go: the first without a marked end, left first, as
    /// `preferredTask` picks it; nil once both ends are marked.
    static func walkingSide(_ coverage: CoverageMap) -> WalkSide? {
        [WalkSide.left, .right].first { ($0 == .left ? coverage.leftEnd : coverage.rightEnd) == nil }
    }

    /// The stretch `laggingBand` looks at from a camera at `s`: `lagWindow` ahead in the walk's
    /// direction and `lagBehind` back. Both ways once both ends are marked, when the walk has no
    /// direction.
    static func lagSpan(at s: Float, walking side: WalkSide?) -> ClosedRange<Float> {
        switch side {
        case .right?: (s - lagBehind)...(s + lagWindow)
        case .left?: (s - lagWindow)...(s + lagBehind)
        case nil: (s - lagWindow)...(s + lagWindow)
        }
    }

    /// Whether a stalled stretch of `band` holds `s`.
    private func isDeferred(_ band: SurfaceBand, at s: Float) -> Bool {
        deferred.contains { $0.band == band && $0.range.contains(s) }
    }

    /// A band that lags the other ahead of the camera (`lagSpan`): the other band is covered there
    /// but this one isn't, over at least `lagRun`. Only cells between the ends count: cells seen
    /// before an end was set stay in the map, but past it nothing is observed or skipped, so a
    /// task there could never be met or refused (issue #38).
    ///
    /// Cells centred in deferred stretches don't count, and they split the lagging cells into runs:
    /// request is for the run nearest the camera that is long enough, with its aim window trimmed
    /// at a neighbouring deferred span. A three-cell run still gets a request even when its full
    /// 0.6 m window would reach back into that span. Taking the middle of all the cells left
    /// asked for a stalled stretch again whenever the band lagged on both sides of
    /// it, since the first and last cell, and so the middle, were unchanged (review of #120).
    /// Cells where the band is already done split the runs the same way: the middle of two runs
    /// either side of covered ground aimed the homeowner at that covered ground (#129).
    private func laggingBand(coverage: CoverageMap, camera: CameraFrame) -> GuidanceTask? {
        let s = coverage.wall.wallPoint(camera.position).s
        let window = Self.lagSpan(at: s, walking: Self.walkingSide(coverage))
        let indices = coverage.indices(overlapping: window).filter(coverage.isWithinEnds)
        let needed = Int((config.lagRun / coverage.config.cellWidth).rounded(.up))
        func done(_ level: CoverageLevel) -> Bool { level == .covered || level == .skipped }
        func middleOf(_ index: Int) -> Float {
            let range = coverage.cellRange(index)
            return (range.lowerBound + range.upperBound) / 2
        }
        /// The middle of the run of `band`'s lagging cells nearest the camera, among those of at
        /// least `needed` cells between deferred stretches and cells where `band` is done, whose
        /// aim task can be met (`canSatisfy`).
        func nearestRun(_ band: SurfaceBand, lags: (Int) -> Bool) -> Float? {
            var runs: [[Int]] = [[]]
            for index in indices {
                if isDeferred(band, at: middleOf(index)) || done(coverage.level(band, index)) {
                    if !runs[runs.count - 1].isEmpty { runs.append([]) }
                } else if lags(index) {
                    runs[runs.count - 1].append(index)
                }
            }
            return runs.filter { $0.count >= needed }
                .compactMap { middle($0, coverage) }
                .filter { canSatisfy(band, at: $0, coverage) }
                .min { abs($0 - s) < abs($1 - s) }
        }
        if let mid = nearestRun(.ground, lags: { done(coverage.level(.wall, $0)) && !done(coverage.level(.ground, $0)) }) {
            return .aimAtGround(s: mid)
        }
        if let mid = nearestRun(.wall, lags: { done(coverage.level(.ground, $0)) && !done(coverage.level(.wall, $0)) }) {
            return .aimAtWall(s: mid)
        }
        return nil
    }

    /// Cells hidden in either band around the camera, over at least `lagRun`, between the ends as
    /// for `laggingBand`. It comes before a lagging band: aiming at a band something stands in
    /// front of adds nothing.
    private func hiddenNearCamera(coverage: CoverageMap, camera: CameraFrame) -> GuidanceTask? {
        let s = coverage.wall.wallPoint(camera.position).s
        let needed = Int((config.lagRun / coverage.config.cellWidth).rounded(.up))
        let hidden = coverage.indices(overlapping: (s - Self.lagWindow)...(s + Self.lagWindow)).filter { index in
            coverage.isWithinEnds(index) && SurfaceBand.allCases.contains { coverage.level($0, index) == .hidden }
        }
        guard hidden.count >= needed, let mid = middle(hidden, coverage) else { return nil }
        return .seeBehind(s: mid)
    }

    /// After both ends are marked: the first stretch between them where a band is not done.
    /// Deferred stretches count: this is where a stalled request, or the ground by the meter left
    /// while the walk went on, comes back.
    private func firstHole(coverage: CoverageMap) -> GuidanceTask? {
        guard let left = coverage.leftEnd, let right = coverage.rightEnd, left < right else { return nil }
        let needed = Int((config.lagRun / coverage.config.cellWidth).rounded(.up))
        for band in [SurfaceBand.ground, .wall] {
            var run: [Int] = []
            for index in coverage.indices(overlapping: left...right) {
                let level = coverage.level(band, index)
                if level == .covered || level == .skipped {
                    if run.count >= needed { break }
                    run = []
                } else {
                    run.append(index)
                }
            }
            if run.count >= needed, let mid = middle(run, coverage) {
                return band == .ground ? .aimAtGround(s: mid) : .aimAtWall(s: mid)
            }
        }
        return nil
    }

    /// Whether an aim task for `band` at `s` can be met: skipped cells are never covered, so the
    /// rest of its stretch must reach `aimSatisfied`. A run of three missing cells between two
    /// skipped ones is centred on a stretch of five, of which only 3/5 can be covered: the
    /// request could only end by stalling (review of #154).
    private func canSatisfy(_ band: SurfaceBand, at s: Float, _ coverage: CoverageMap) -> Bool {
        let task: GuidanceTask = band == .ground ? .aimAtGround(s: s) : .aimAtWall(s: s)
        guard let window = aimWindow(for: task, coverage: coverage) else { return false }
        let cells = coverage.indices(overlapping: window).filter(coverage.isWithinEnds)
        let open = cells.filter { coverage.level(band, $0) != .skipped }
        return !cells.isEmpty && Double(open.count) >= Self.aimSatisfied * Double(cells.count)
    }

    private func middle(_ indices: [Int], _ coverage: CoverageMap) -> Float? {
        guard let first = indices.first, let last = indices.last else { return nil }
        return (coverage.cellRange(first).lowerBound + coverage.cellRange(last).upperBound) / 2
    }

    func isSatisfied(_ task: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> Bool {
        switch task {
        case .walk(let side), .markEnd(let side):
            return (side == .left ? coverage.leftEnd : coverage.rightEnd) != nil
        case .aimAtGround, .aimAtWall:
            guard let aim = Self.aim(task), let window = aimWindow(for: task, coverage: coverage) else { return false }
            return coverage.coveredFraction(aim.band, in: window) >= Self.aimSatisfied
        case .stepBack:
            guard let camera else { return false }
            return coverage.wall.wallPoint(camera.position).out >= config.tooClose + 0.2
        case .seeBehind(let s):
            return !coverage.indices(overlapping: (s - 0.3)...(s + 0.3)).contains { index in
                coverage.isWithinEnds(index) && SurfaceBand.allCases.contains { coverage.level($0, index) == .hidden }
            }
        case .complete:
            return false
        }
    }

    // MARK: Cues

    /// Where a walk toward `side` heads: 1 m past whichever is farther on that side, the unbroken
    /// coverage or the camera. Reach alone stops at the first hole, so a homeowner already past a
    /// hole would get a target behind them, and the arrow would point against "walk right". Holes
    /// are the job of the aim tasks, which ask for them once they are near or both ends are marked.
    func walkGoal(_ side: WalkSide, coverage: CoverageMap, camera: CameraFrame?) -> Float {
        var ahead = reach(side, coverage: coverage)
        if let camera { ahead = max(ahead, side.sign * coverage.wall.wallPoint(camera.position).s) }
        return side.sign * (ahead + 1)
    }

    func target(for task: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> SIMD3<Float>? {
        let wall = coverage.wall
        switch task {
        case .walk(let side):
            return wall.world(s: walkGoal(side, coverage: coverage, camera: camera), height: 1)
        case .aimAtGround(let s):
            return wall.world(s: s, height: 0, out: coverage.config.groundBandDepth / 2)
        case .aimAtWall(let s):
            return wall.world(s: s, height: 1.2)
        case .seeBehind(let s):
            // The wall's foot, where the two bands meet: either may be the hidden one.
            return wall.world(s: s, height: 0)
        // "Wall ends here" lands where the reticle meets the wall, so the reticle is the only aim;
        // the ring sat at the covered reach, which can be at the meter however far the walk went.
        case .markEnd, .stepBack, .complete:
            return nil
        }
    }

    func path(for task: GuidanceTask, coverage: CoverageMap, camera: CameraFrame?) -> [SIMD3<Float>] {
        guard let camera else { return [] }
        let wall = coverage.wall
        let from = wall.wallPoint(camera.position).s
        let goal: Float
        switch task {
        case .walk(let side): goal = walkGoal(side, coverage: coverage, camera: camera)
        case .aimAtGround(let s), .aimAtWall(let s): goal = s
        // No path for seeBehind: which way round the obstruction is clear is not known, and a path
        // to straight in front of it could lead to the same blocked view.
        case .markEnd, .stepBack, .seeBehind, .complete: return []
        }
        let clipped = from + max(-config.maxPathLength, min(config.maxPathLength, goal - from))
        let steps = max(1, Int((abs(clipped - from) / 0.5).rounded(.up)))
        return (0...steps).map { i in
            wall.world(s: from + (clipped - from) * Float(i) / Float(steps), height: 0, out: config.standOff)
        }
    }
}
