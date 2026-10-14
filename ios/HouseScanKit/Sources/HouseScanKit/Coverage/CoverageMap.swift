import Foundation
import simd

/// The two strips of surface the scan must see: the wall face and the ground at its foot.
public enum SurfaceBand: String, Sendable, CaseIterable {
    case wall
    case ground
}

/// How well a cell has been observed. Only `.covered` counts as evidence. Without depth, "in
/// view" below means inside a kept photo's view with nothing but the wall modelled in front of
/// it, which is not the same as seen (`CoverageMap`, "Bounded exceptions").
public enum CoverageLevel: UInt8, Sendable, Equatable {
    case unseen
    /// Some row in view of a kept keyframe (with depth: confirmed by it), but not every row from
    /// two separate positions yet.
    case seen
    /// Every sample row in view from two camera positions at least
    /// `CoverageConfig.coveringBaseline` apart (the rows may be in view of different frames).
    case covered
    /// The homeowner said they cannot get there, or said something stands there when asked about
    /// the chosen spot (`CoverageMap.withdrawClaims`). Never evidence.
    case skipped
    /// LiDAR only: not covered, and a row still short of two positions was hidden in some kept
    /// frame by a nearer depth reading (a bush, a bin). Views that clear the obstruction can
    /// still cover it. A reading of where the space in front of the wall ends (a corridor's far
    /// wall, `CoverageMap.farSurface`) hides nothing: there is no way round it (#160).
    case hidden
}

/// Thresholds of the coverage map. Every value is a starting hypothesis, not a measured optimum;
/// the reasons for each are next to it.
public struct CoverageConfig: Sendable, Equatable {
    /// Cell width along the wall. 6 in is the research note's display guess
    /// (docs/research/t3-first-try-capture.md on t3/research): fine enough that one missed
    /// stride shows as a gap, coarse enough that the strip reads at a glance.
    public var cellWidth: Float = 0.1524
    /// Capture setting, not a clearance: how high up the wall rows are sampled, meters, and so the
    /// most a seen height can report and the most a wall request the phone can meet may ask
    /// (`GapPlanner.isBeyondCapture`). What the scene reports is how high each stretch was
    /// actually seen (`wallSeenSpans()`); the server holds each check to its own height from its
    /// rules (server/README.md, "What settles each check", t3/server 930e8e5), and nothing on the
    /// phone compares against those. The walk itself asks only for `wallWalkHeight`.
    ///
    /// 7.5 ft (2.286 m), because it has to get past the highest height the public rules ask the
    /// wall seen above, 6.5 ft (headroom height, for gas meters, openings and equipment above
    /// the battery): the server credits a stretch only where the reported height exceeds a
    /// check's, so a phone that stops at 6.5 ft settles none of those. On 6 in rows the first row
    /// above 6.5 ft is 7 ft; 7.5 ft keeps one row more, so that with a guessed ground, whose
    /// 0.3 m (0.98 ft) error comes off every reported height (`heightError`), the top row still
    /// reports 6.52 ft. No calibration run exists: whether homeowners' views reach it is untested.
    /// A phone 2.6 m out at chest height pitched 20 degrees down (the synthetic replay's walk)
    /// sees about 2.0 m up; the rest comes from views the gap loop asks for.
    public var wallCaptureHeight: Float = 2.286
    /// Capture setting, not a clearance: how high up the wall the walk asks the camera to see,
    /// meters. The wall band the strip draws runs from the ground up to here, a wall cell is
    /// covered once every wall row up to here is, and the walk's aim tasks and the phone's own gap
    /// check go by that. Anything seen higher is still reported (`wallSeenSpans()`), and a server
    /// that needs more asks for it with a wall request (`GapPlan.Need.wallUp`).
    ///
    /// 4.5 ft (1.3716 m, 9 rows): the height a view that also shows the ground band in front can
    /// reach from the walk's stand-off. From `GuidanceConfig.standOff` (2 m) at chest height
    /// (1.4 m), the ground band's outer row (`groundBandDepth`, 1.2 m out) is 60.3 degrees below
    /// level; a portrait view pitched to put it at the bottom edge shows the wall up to
    /// 1.4 + 2 tan(2a - 60.3) m, where a is the image's long-side half angle inside the 3 % margin.
    /// That is 1.46 m for a = 31 degrees (the synthetic replay's camera, and ARKit's 1920 x 1440
    /// wide-camera frame at fx near 1450 px) and 1.39 m at 30 degrees; 4.5 ft is the highest whole
    /// row under both. It clears the battery's height (3.29 ft under the public rules) for its
    /// backing and the cable's run, also after a guessed ground's 0.98 ft. No calibration run
    /// exists: the phones' intrinsics and how homeowners hold them were not measured.
    public var wallWalkHeight: Float = 1.3716
    /// Spacing of the wall rows from the foot up: 6 in, the cell width, as for the ground depth
    /// rows. A seen height is a whole number of rows, so this is its resolution. A hypothesis,
    /// not tuned.
    public var wallRowSpacing: Float = 0.1524
    /// Ground band depth out from the wall. 1.2 m covers a unit's footprint plus its front clearance.
    /// This near band is what the coverage strip draws and what the walk's guidance asks for; how
    /// far out the ground was actually seen is `groundDepthReach` and `groundDepth(at:)`.
    public var groundBandDepth: Float = 1.2
    /// How far out from the wall ground depth is sampled: 17 ft (34 rows of 6 in), the deepest
    /// ground request the public rules make within cable reach, rounded up to a whole row.
    ///
    /// The server asks for ground out to D + r + e (server/README.md "What settles each check" on
    /// origin/t3/server), with D = 1.8333 ft, r = 10 ft for a pool (the largest clearance in
    /// rules.yaml) and e = 0.3 + 0.16 x ft at the battery's far edge, x ft along the walls from
    /// the meter. The server tries spots whose near edge is within (20 + 0.3 + 0.3 + 0.16 W) /
    /// (1 - 0.16) = 25.02 ft of the meter (solver.py `reach_limit`: the 20 ft cable maximum plus
    /// the default meter and wall errors, W = 2.5833 ft); past that the route fails whatever the
    /// ground shows. The far edge is then 27.60 ft out, e = 4.72 ft, and the pool request
    /// 16.55 ft. A larger meter or wall error, a detour round a wall object, or private rules can
    /// ask for more; `GapPlanner.isBeyondCapture` sends such a request to review.
    public var groundDepthReach: Float = 5.1816
    /// Spacing of the ground depth rows: 6 in, the cell width, so depth is sampled as densely out
    /// from the wall as the cells are along it. Depth is exported as a whole number of rows, so this
    /// is also the resolution of the reported depth; 6 in is under the position error the server
    /// assumes a few feet from the meter (0.3 ft plus 0.16 ft per foot). A hypothesis, not tuned.
    public var groundDepthSpacing: Float = 0.1524
    /// Farther than this a phone camera resolves too little of a wall to measure against it.
    public var maxDistance: Float = 6
    /// Views more oblique than this foreshorten the surface too much to measure on it.
    public var maxAngleFromNormal: Float = 65 * .pi / 180
    /// Samples within this fraction of the image edge don't count, since edges blur and distort most.
    public var imageMargin: Float = 0.03
    /// Sample rows across the ground band, wall foot to outer edge. A cell is covered only when
    /// every row is, so the first and last rows sit on the band's edges: the band a covered cell
    /// claims is the band its rows saw. Three rows leave 0.6 m between rows unsampled; a
    /// hypothesis, not tuned. The wall band's rows are `wallRows`.
    public var rowsPerBand = 3
    /// Two views count as separate positions only this far apart, so parallax exists between them.
    public var coveringBaseline: Float = 0.25
    /// Two kept positions at most this far apart are one stretch of walked path: the homeowner
    /// went from one to the other, and a straight line between them is assumed. Keyframes are kept
    /// about every 0.5 m (`AutoCaptureConfig.spacingMeters`); 1 m allows one dropped frame
    /// between them while staying under two strides, too short to have walked around something.
    /// A hypothesis, not measured.
    public var walkStep: Float = 1.0
    /// Two kept positions at most this many seconds apart are one stretch of walked path; further
    /// apart, the homeowner may have gone anywhere between them. A guess, not measured: 2 s is a
    /// `walkStep` of 1 m at 0.5 m/s, a slow scanning pace. It bounds a detour on which no frame
    /// was kept (auto-capture keeps one every 0.5 m moved unless it skips them as blurry or
    /// hurried) to what 2 s of walking allows; it does not rule one out.
    public var walkGap: Double = 2
    /// Overhead heights are rounded down to 0.1 ft, so a view's cells merge into a few entries
    /// instead of differing in the last millimetre. It costs at most 0.1 ft of the height.
    public var overheadQuantum: Float = 0.03048
    /// Fog drawn ahead of what has been seen, so the homeowner sees where to go next.
    public var fogAhead: Float = 2.5
    /// LiDAR only. A sample counts as seen when the depth reading at its pixel is within
    /// `depthTolerance + depthTolerancePerMeter * d` of its own z-depth d; a reading nearer than
    /// that hides it. A guess, not measured: 10 cm allows for the wall and ground planes being a
    /// few centimetres off (the meter tap, the ground plane) and for siding relief, and 2 cm per
    /// meter for LiDAR noise growing with range. Too small and true wall reads as hidden or
    /// never matches; too large and a bush close to the wall does not hide it (at 2.6 m the
    /// tolerance is 15 cm, so anything standing 15 cm or more proud of the wall hides it).
    public var depthTolerance: Float = 0.10
    public var depthTolerancePerMeter: Float = 0.02
    /// LiDAR only. Readings below this ARConfidenceLevel are no reading: 1, medium. A guess:
    /// ARKit marks object edges and far or dark surfaces low, which is where readings stray most.
    public var minimumDepthConfidence: UInt8 = 1
    /// LiDAR only (#160). A nearer reading is the surface where the space ends
    /// (`CoverageMap.farSurface`), not something in front of the wall, when what it met stands no
    /// more than this nearer the wall than that surface; a sample lies past the space when it is
    /// no more than this short of it. A guess: a detected plane lies within a few centimetres of
    /// its surface, and at 2.5 m a reading is matched within 15 cm (`depthTolerance`).
    public var farSurfaceTolerance: Float = 0.15
    /// How near the surface where the space ends (`CoverageMap.farSurface`) a homeowner holding
    /// the phone toward the wall can bring it: they stand behind the phone. A walk-out line nearer
    /// that surface than this can't be walked (`GapPlanner.walkOutBlock`, #164). 0.3 m is a
    /// guess, not measured.
    public var walkerDepth: Float = 0.3

    public init() {}
}

/// Which cells of the wall and ground strips kept keyframes have observed, and what the scan
/// claims about them.
///
/// With a frame's LiDAR depth (`observe(_:trackingNormal:time:depth:)`), a sample counts only
/// when the depth at its pixel matches its own distance (`CoverageConfig.depthTolerance`). A
/// nearer reading hides it; a farther reading, no reading or a low-confidence one is no evidence
/// either way (the surface is not where the wall model puts it, or nothing was measured). This
/// holds for the bands, the ground depth rows and the ground past a limit end alike, and a later
/// view without depth adds nothing to a row a depth frame found hidden: it can't see past what
/// stands there either.
///
/// Bounded exceptions. Two claims the map makes are inferred, not seen, and would break "unseen
/// stays unsure" on their own:
///
/// - Without depth, a sample counts when it is in a photo's view: in front of the camera, inside
///   the image and not behind the wall. Nothing else is modelled, so wall and ground behind a bush
///   or a bin are claimed as if the photo showed them. On ETH3D the evals lane measured 1.1 ft of
///   a 19.3 ft wall claimed covered that no photo saw, all of it occluded at the bottom.
/// - The walked path (`walkedClearance(at:)`) is claimed as clear space between the wall and the
///   phone, although something lower than the phone (a bush under chest height) can stand under
///   it.
///
/// The server takes both claims as given and reads no images. They are allowed only because the
/// homeowner confirms the chosen spot before the result is shown: the spot check (`SpotPhoto`,
/// `SpotConfirmations`; the app's `ScanEngine+Confirm.swift`) shows a kept photo of the spot and
/// its clearance area and asks whether anything stands in front of the wall or on the ground
/// there. "It's clear" backs the claims over that area. "Something's there" and "I can't check
/// this area" withdraw them (`withdrawClaims(over:)`): the area's wall, ground and walked-path
/// claims export as unseen and the scan is checked again. Claims away from the chosen spot are not confirmed; they decide only
/// where the server looks for a spot, and any spot it chooses is checked in turn.
public struct CoverageMap: Sendable {
    public private(set) var wall: WallFrame
    public let config: CoverageConfig
    /// Marked wall ends in meters of s. Nothing outside them is observed once they are set, except
    /// ground past a limit end (`limitEnds`).
    public private(set) var leftEnd: Float?
    public private(set) var rightEnd: Float?
    /// Marked ends the homeowner said are real limits: no usable wall past them (a fence, a
    /// corner the walk does not follow). Ground past one still counts for clearances, so it is
    /// sampled and reported (`groundDepthSpans`); past any other end nothing is.
    public private(set) var limitEnds: Set<WalkSide> = []
    /// Increments on every change.
    public private(set) var revision = 0
    /// How far the ground under the wall may lie from `wall.groundY` either way, meters, while it
    /// is a guess: 0 once the ground is measured; the engine sets its chest-height guess's error
    /// until then. Guessed too low, a row sampled at height h stands only h less the error above
    /// the real ground, so the error comes off every height the map reports
    /// (`wallSeenHeight(at:)`, `overheadHeight(at:)`). Guessed too high, the rows from the guessed
    /// ground up miss the real foot of the wall, so a seen height also needs the foot rows below
    /// the guessed ground, down to the error (`wallRows`). Which cells the strip counts as
    /// covered doesn't change with it. Changing it rebuilds the cells from the kept cameras.
    public var heightError: Float = 0 {
        didSet { if heightError != oldValue { replayObservedCameras(shiftingSkippedBy: 0) } }
    }

    private var cells: [SurfaceBand: [Int: Cell]] = [.wall: [:], .ground: [:]]
    /// Per cell, per ground depth row (0 at the wall foot, then every `groundDepthSpacing` out to
    /// `groundDepthReach`), the sightings of it, as in a cell's rows. Separate from `cells` so the
    /// near ground band the strip draws keeps its meaning.
    private var depthCells: [Int: [[Sight]]] = [:]
    /// Per limit end, per cell past it (0 touching the end, counting away from the meter), the
    /// camera positions that saw each ground depth row on both sides of the wall's continued
    /// line: the rows in front (out = +row), then the rows behind (out = -row). Kept like
    /// `depthCells`. The cells start at the end itself, so what they report meets the ground
    /// clipped to the end exactly.
    private var pastLimitCells: [WalkSide: [Int: [[Sight]]]] = [:]
    /// s shift from meter moves not yet applied to skipped cells, meters (see `updateWall`).
    private var pendingShift: Float = 0
    /// Cameras of the frames `observe` recorded, oldest first, so a new wall frame can be replayed
    /// against them. Sightings recorded directly through `record` are not kept.
    public private(set) var observedCameras: [CameraFrame] = []
    /// Per entry of `observedCameras`, the depth it was observed with (`storedDepth`), so a
    /// replay decides visibility exactly as the live frame did.
    private var observedDepths: [DepthImage?] = []
    /// Tilt-up views the homeowner confirmed have nothing overhead (`recordOverhead`), oldest
    /// first. What each showed is worked out against the current wall when read.
    public private(set) var overheadCameras: [CameraFrame] = []
    /// Per entry of `observedCameras`, the frame clock time it was kept at; nil when the caller
    /// gave none, and such a pose is never joined into walked path.
    private var observedTimes: [Double?] = []
    /// Per entry of `observedCameras`, the walked-path segment it was captured in (`pathSegment`).
    private var observedSegments: [Int] = []
    /// The stretch of continuous walking a frame captured now belongs to. `breakWalkedPath`
    /// starts a new one. A caller that stores a frame before observing it reads this when the
    /// frame is captured and passes it to `observe`, so a store that finishes after a break
    /// can't join the frame to ones captured on the other side of it.
    public private(set) var pathSegment = 0
    /// Per ground depth cell, the rows a kept frame's depth showed hidden behind something nearer
    /// while short of two positions: a later view without depth can't add to them.
    private var depthHidden: [Int: Set<Int>] = [:]
    /// Per ground depth cell, the rows a kept frame's depth showed past where the space ends
    /// (`farSurface`), while short of two positions. Not hidden: nothing stands in front of the
    /// wall there to look past (#160). A later view without depth can't add to them either: it
    /// can't see through that surface.
    private var depthPastSpace: [Int: Set<Int>] = [:]
    /// As `depthHidden`, for the rows past each limit end (`pastLimitCells`).
    private var pastLimitHidden: [WalkSide: [Int: Set<Int>]] = [:]
    /// Per cell, how far out from the wall the space in front of it visibly ends, meters
    /// (`setFarSurface(_:)`).
    private var farSurfaceByCell: [Int: Float] = [:]
    /// Cells whose wall, ground and walked-path claims the homeowner withdrew
    /// (`withdrawClaims(over:)`). Kept through rebuilds and moved with skipped cells.
    public private(set) var withdrawnCells: Set<Int> = []

    /// One camera position that saw a sample row, and whether that frame's depth confirmed it.
    private struct Sight: Sendable, Equatable {
        var position: SIMD3<Float>
        var depthVerified: Bool
    }

    /// Adds a sighting from `position` unless the row holds two already or one nearer than
    /// `coveringBaseline` (no parallax); true when added.
    private func add(_ row: inout [Sight], _ position: SIMD3<Float>, verified: Bool) -> Bool {
        guard row.count < 2, row.allSatisfy({ simd_distance($0.position, position) >= config.coveringBaseline }) else { return false }
        row.append(Sight(position: position, depthVerified: verified))
        return true
    }

    /// A depth frame found the row hidden behind something nearer: the sightings no depth
    /// confirmed may have been of that obstruction, so they go. Sightings depth confirmed stay.
    /// True when any went.
    private static func dropUnverified(_ row: inout [Sight]) -> Bool {
        let before = row.count
        row.removeAll { !$0.depthVerified }
        return row.count != before
    }

    private struct Cell: Sendable, Equatable {
        /// Per sample row, its sightings, pairwise at least `coveringBaseline` apart. Two are
        /// enough, so a row stops collecting at two.
        var rows: [[Sight]]
        /// Rows below this are the wall's foot rows under a guessed ground (`wallRows`): they
        /// count toward a seen height, not toward the band the strip draws.
        let firstBandRow: Int
        /// The top row of the band the walk asks for (`wallWalkHeight`; every row for the ground).
        /// Rows above it count toward a seen height only.
        let lastWalkRow: Int
        /// Rows a kept frame's depth showed hidden behind something nearer.
        var hiddenRows: Set<Int> = []
        /// Rows a kept frame's depth showed past where the space ends (`farSurface`), while short
        /// of two positions. Not hidden: nothing stands in front of the wall there to look past
        /// (#160). A later view without depth adds nothing to them, as for hidden rows: it can't
        /// see through that surface.
        var pastSpaceRows: Set<Int> = []
        var skipped = false
        var covered = false

        init(rowCount: Int, firstBandRow: Int, lastWalkRow: Int) {
            rows = Array(repeating: [], count: rowCount)
            self.firstBandRow = firstBandRow
            self.lastWalkRow = lastWalkRow
        }

        var isSeen: Bool { rows[firstBandRow...].contains { !$0.isEmpty } }

        /// Every row of the band the walk asks for seen from two positions.
        var bandCovered: Bool { rows[firstBandRow...lastWalkRow].allSatisfy { $0.count >= 2 } }

        /// A hidden row of the walk's band blocks until it has its two positions. Rows above it
        /// don't: the walk doesn't ask for them, so it mustn't send the homeowner round an
        /// obstruction for them either.
        var isHidden: Bool { hiddenRows.contains { (firstBandRow...lastWalkRow).contains($0) && rows[$0].count < 2 } }

        var level: CoverageLevel {
            if covered { return .covered }
            if skipped { return .skipped }
            if isHidden { return .hidden }
            return isSeen ? .seen : .unseen
        }
    }

    public struct Delta: Sendable, Equatable {
        public var newlySeen = 0
        public var newlyCovered = 0
        /// Cells that became `.hidden`.
        public var newlyHidden = 0
        public var changed: Bool { newlySeen + newlyCovered + newlyHidden > 0 }
    }

    /// Depth is kept at most this wide (`storedDepth`): ARKit's 256 x 192 becomes 128 x 96, 24 KB
    /// of millimeters and 12 KB of confidence per keyframe, 18 MB for 500 keyframes instead of 74.
    /// A cell is 15 cm wide; at 3 m a pixel of the halved map spans about 3 cm.
    public static let storedDepthMaxWidth = 128

    /// The copy of `depth` a kept frame is observed and stored with: downsampled by the smallest
    /// whole factor that brings it to `storedDepthMaxWidth` or narrower (`DepthImage.downsampled`).
    /// The live frame uses the same copy, so a rebuild decides exactly as the live frame did.
    public static func storedDepth(_ depth: DepthImage) -> DepthImage {
        depth.downsampled(by: (depth.width + storedDepthMaxWidth - 1) / storedDepthMaxWidth)
    }

    public init(wall: WallFrame, config: CoverageConfig = CoverageConfig()) {
        self.wall = wall
        self.config = config
    }

    // MARK: Cells

    public func cellIndex(forS s: Float) -> Int { Int((s / config.cellWidth).rounded(.down)) }

    /// A cell's s range. Each edge is computed from its own index, so neighbouring cells share a
    /// bit-identical edge: exported spans of neighbouring cells then meet exactly, where
    /// `start + width` could leave an ulp between them that the server (which joins spans only
    /// within 1e-9 ft) would read as an unobserved sliver.
    public func cellRange(_ index: Int) -> ClosedRange<Float> {
        (Float(index) * config.cellWidth)...(Float(index + 1) * config.cellWidth)
    }

    public func level(_ band: SurfaceBand, _ index: Int) -> CoverageLevel {
        if withdrawnCells.contains(index) { return .skipped }
        return cells[band]?[index]?.level ?? .unseen
    }

    /// Indices of every cell overlapping `range` by more than a thousandth of a cell.
    ///
    /// Ranges built from cell edges (gap spans, seen extents) sit within Float rounding of a
    /// boundary: `Float(i) * width / width` can floor to i - 1, and the next cell's start can land
    /// an ulp below this cell's end. Without the tolerance, 9 of the 121 cells within 9 m of the
    /// meter picked up a neighbour, which could read a gap's 3 of 4 cells covered as 4 of 5 (80 %).
    public func indices(overlapping range: ClosedRange<Float>) -> ClosedRange<Int> {
        let tolerance = config.cellWidth * 1e-3
        let first = cellIndex(forS: range.lowerBound + tolerance)
        let last = cellIndex(forS: range.upperBound - tolerance)
        return first...max(first, last)
    }

    /// Whether any of a cell lies between the marked ends. Cells beyond them are never observed;
    /// ground past a limit end is kept apart (`groundDepthPastLimit`).
    public func isWithinEnds(_ index: Int) -> Bool { allows(index) }

    /// The s range allowed by the marked ends; unbounded sides are nil.
    private func allows(_ index: Int) -> Bool {
        let range = cellRange(index)
        if let leftEnd, range.upperBound <= leftEnd { return false }
        if let rightEnd, range.lowerBound >= rightEnd { return false }
        return true
    }

    // MARK: Observing

    /// Records a kept keyframe kept at `time` seconds on the frame clock. Returns nothing new
    /// unless tracking was normal for it: frames with limited tracking have poses that can be off
    /// by more than a cell, and such a frame also breaks the walked path (`breakWalkedPath`).
    /// Without a `time` the pose still counts for the bands, but never for walked path.
    ///
    /// `depth` is the frame's LiDAR depth, taken with the same pose. With it a sample counts only
    /// where the depth confirms it (see the type's comment), and cells whose rows something nearer
    /// hides become `.hidden`. It is used and kept as `storedDepth(_:)`. A frame without depth
    /// can't tell what stands in front of the wall, so it adds nothing to a row an earlier depth
    /// frame found hidden.
    ///
    /// `segment` is the walked-path segment the frame was captured in (`pathSegment` read at
    /// capture); nil means the current one. Poses join into walked path only within a segment.
    @discardableResult
    public mutating func observe(
        _ camera: CameraFrame, trackingNormal: Bool, time: Double? = nil, depth: DepthImage? = nil, segment: Int? = nil
    ) -> Delta {
        guard trackingNormal else {
            breakWalkedPath()
            return Delta()
        }
        let depth = depth.map(Self.storedDepth)
        observedCameras.append(camera)
        observedTimes.append(time)
        observedSegments.append(segment ?? pathSegment)
        observedDepths.append(depth)
        let depthChanged = recordDepth(from: camera, depth: depth)
        let pastChanged = recordPastLimits(from: camera, depth: depth)
        let (delta, levelChanged) = recordSightings(visibleCells(from: camera, depth: depth), from: camera.position, depthChecked: depth != nil)
        if delta.changed || levelChanged || depthChanged || pastChanged { revision += 1 }
        return delta
    }

    /// Every cell a frame sees or finds hidden at least one row of, before the marked ends clip
    /// anything that isn't allowed. Without `depth` nothing is hidden.
    public func visibleCells(from camera: CameraFrame, depth: DepthImage? = nil) -> [Sighting] {
        visibleCells(from: camera, depth: depth, reads: nil)
    }

    /// `visibleCells(from:depth:)`, noting in `reads` where the far-surface lookup was consulted.
    private func visibleCells(from camera: CameraFrame, depth: DepthImage?, reads: FarSurfaceReadLog?) -> [Sighting] {
        var seen: [Sighting] = []
        for band in SurfaceBand.allCases {
            for index in candidateIndices(for: camera) {
                let views = rowViews(band, index, from: camera, depth: depth, among: Array(rowOffsets(band).indices), reads: reads)
                if !views.seen.isEmpty || !views.hidden.isEmpty || !views.pastSpace.isEmpty {
                    seen.append(Sighting(band: band, index: index, rows: views.seen, hiddenRows: views.hidden, pastSpaceRows: views.pastSpace))
                }
            }
        }
        return seen
    }

    public struct Sighting: Sendable, Hashable {
        public var band: SurfaceBand
        public var index: Int
        /// The sample rows of the cell the frame saw, 0 at the band's bottom (or the wall foot).
        public var rows: Set<Int>
        /// The rows the frame's depth showed hidden behind something nearer.
        public var hiddenRows: Set<Int>
        /// The rows the frame's depth showed past where the space ends (`farSurface`).
        public var pastSpaceRows: Set<Int> = []
    }

    /// Records cells a kept keyframe with normal tracking saw from `position`: the part of
    /// `observe` after visibility, for planners that precompute what each frame sees.
    @discardableResult
    public mutating func record(_ sightings: [Sighting], from position: SIMD3<Float>) -> Delta {
        let (delta, levelChanged) = recordSightings(sightings, from: position, depthChecked: false)
        if delta.changed || levelChanged { revision += 1 }
        return delta
    }

    /// `record` without the revision: also whether any cell's level changed, which a hidden
    /// cell's row gaining a position can do without counting in `Delta`. Sightings not checked
    /// against depth add no position to a row found hidden or past where the space ends.
    @discardableResult
    private mutating func recordSightings(
        _ sightings: [Sighting], from position: SIMD3<Float>, depthChecked: Bool
    ) -> (Delta, levelChanged: Bool) {
        var delta = Delta()
        var levelChanged = false
        for sighting in sightings where allows(sighting.index) {
            var cell = cells[sighting.band]?[sighting.index] ?? newCell(sighting.band)
            let wasSeen = cell.isSeen
            let before = cell.level
            var added = false
            for row in sighting.rows where cell.rows.indices.contains(row) && (depthChecked || !(cell.hiddenRows.contains(row) || cell.pastSpaceRows.contains(row))) {
                // A repeated or nearby frame adds no parallax, so it adds nothing.
                if add(&cell.rows[row], position, verified: depthChecked) { added = true }
            }
            // Only depth finds a row hidden. Its unconfirmed sightings go, and a row left short of
            // two positions is hidden; one two confirmed sightings settled is not.
            for row in sighting.hiddenRows where cell.rows.indices.contains(row) {
                if Self.dropUnverified(&cell.rows[row]) { added = true }
                if cell.rows[row].count < 2, cell.hiddenRows.insert(row).inserted { added = true }
            }
            // A sighting no depth confirmed, of a row past where the space ends, was of that
            // surface, as for the ground depth rows (`recordDepth`). Discarding these rows let
            // views without depth taken before a late far surface keep a wall behind it covered.
            for row in sighting.pastSpaceRows where cell.rows.indices.contains(row) {
                if Self.dropUnverified(&cell.rows[row]) { added = true }
                if cell.rows[row].count < 2, cell.pastSpaceRows.insert(row).inserted { added = true }
            }
            guard added else { continue }
            if !wasSeen, cell.isSeen { delta.newlySeen += 1 }
            let covered = cell.bandCovered
            if covered, !cell.covered { delta.newlyCovered += 1 }
            cell.covered = covered
            let after = cell.level
            if after == .hidden, before != .hidden { delta.newlyHidden += 1 }
            if after != before { levelChanged = true }
            cells[sighting.band, default: [:]][sighting.index] = cell
        }
        return (delta, levelChanged)
    }

    /// How many cells this frame would show a row of for the first time, if kept. A cell whose
    /// lower rows are seen still counts when the frame is the first to see its top row, or it
    /// could never become covered.
    public func newlySeenCount(from camera: CameraFrame) -> Int {
        var count = 0
        for band in SurfaceBand.allCases {
            for index in candidateIndices(for: camera) {
                let cell = cells[band]?[index]
                let unseenRows = rowOffsets(band).indices.filter { cell?.rows[$0].isEmpty ?? true }
                guard !unseenRows.isEmpty else { continue }
                if !rowViews(band, index, from: camera, depth: nil, among: unseenRows).seen.isEmpty { count += 1 }
            }
        }
        return count
    }

    private func candidateIndices(for camera: CameraFrame) -> [Int] {
        let s = wall.wallPoint(camera.position).s
        return indices(overlapping: (s - config.maxDistance)...(s + config.maxDistance)).filter(allows)
    }

    /// Height (wall, `wallRows`) or distance out (ground, evenly from edge to edge) of each sample
    /// row.
    public func rowOffsets(_ band: SurfaceBand) -> [Float] {
        guard band == .ground else { return wallRows }
        let count = max(2, config.rowsPerBand)
        return (0..<count).map { config.groundBandDepth * Float($0) / Float(count - 1) }
    }

    /// Height of each wall row above the ground as it stands in `wall.groundY`, lowest first: the
    /// foot rows below it while the ground is a guess (every `wallRowSpacing` down, and one at
    /// minus `heightError`), then 0 at the foot and every `wallRowSpacing` up to
    /// `wallCaptureHeight`.
    public var wallRows: [Float] {
        let count = Int((config.wallCaptureHeight / config.wallRowSpacing + 1e-3).rounded(.down))
        return footRows + (0...max(1, count)).map { Float($0) * config.wallRowSpacing }
    }

    /// The wall rows below the guessed ground, down to `heightError`, lowest first; none once the
    /// ground is measured.
    private var footRows: [Float] {
        guard heightError > 0 else { return [] }
        let steps = Int((heightError / config.wallRowSpacing - 1e-3).rounded(.down))
        return [-heightError] + (0..<steps).reversed().map { -Float($0 + 1) * config.wallRowSpacing }.filter { $0 > -heightError }
    }

    private func newCell(_ band: SurfaceBand) -> Cell {
        let rows = rowOffsets(band)
        guard band == .wall else { return Cell(rowCount: rows.count, firstBandRow: 0, lastWalkRow: rows.count - 1) }
        let walked = rows.lastIndex { $0 <= config.wallWalkHeight + 1e-4 } ?? rows.count - 1
        return Cell(rowCount: rows.count, firstBandRow: footRows.count, lastWalkRow: max(walked, footRows.count + 1))
    }

    /// The rows of a cell a frame sees: a row counts when both of its samples, a quarter and
    /// three quarters along the cell, are in view. Geometry only; depth is not consulted.
    public func visibleRows(_ band: SurfaceBand, _ index: Int, from camera: CameraFrame) -> Set<Int> {
        rowViews(band, index, from: camera, depth: nil, among: Array(rowOffsets(band).indices)).seen
    }

    private func rowViews(
        _ band: SurfaceBand, _ index: Int, from camera: CameraFrame, depth: DepthImage?, among rows: [Int], reads: FarSurfaceReadLog? = nil
    ) -> RowViews {
        let offsets = rowOffsets(band)
        return sampledRows(index, from: camera, depth: depth, rows: rows, onWallFace: band == .wall, reads: reads) { s, row in
            band == .wall ? wall.world(s: s, height: offsets[row]) : wall.world(s: s, height: 0, out: offsets[row])
        }
    }

    /// What one frame showed of a cell's rows.
    private struct RowViews: Sendable {
        var seen: Set<Int> = []
        var hidden: Set<Int> = []
        /// Rows past where the space ends (`DepthEvidence.pastSpace`): neither seen nor hidden.
        var pastSpace: Set<Int> = []
    }

    /// What depth says about a sample in view.
    private enum DepthEvidence {
        /// The reading matches the sample's distance: the sample was seen.
        case matches
        /// Something nearer stands in front of the sample.
        case nearer
        /// A nearer reading, but of where the space in front of the wall ends (`farSurface`): the
        /// sample lies at or past that surface, or what the reading met is that surface. The
        /// sample is not seen, and there is nothing in front of the wall to look past (#160).
        case pastSpace
        /// No reading, a low-confidence one, or one farther than the sample.
        case none
    }

    private func depthEvidence(_ point: SIMD3<Float>, camera: CameraFrame, depth: DepthImage, reads: FarSurfaceReadLog? = nil) -> DepthEvidence {
        guard let projected = depth.projection(of: point, pose: camera),
              let reading = depth.meters(atPixel: projected.pixel, minimumConfidence: config.minimumDepthConfidence) else { return .none }
        let tolerance = config.depthTolerance + config.depthTolerancePerMeter * projected.depth
        if reading < projected.depth - tolerance {
            return endsSpace(point, reading: reading, depth: projected.depth, camera: camera, reads: reads) ? .pastSpace : .nearer
        }
        return reading <= projected.depth + tolerance ? .matches : .none
    }

    /// Whether a reading nearer than `point` (z-depth `reading` where the point's is `depth`) is
    /// the surface where the space in front of the wall ends rather than something standing in
    /// front of the wall: the point lies no more than `farSurfaceTolerance` short of that surface
    /// or past it, or what the reading met does. On build 5.1 a corridor's far wall, 5 ft out,
    /// hid everything behind it and was asked to be looked past (#160). False wherever no far
    /// surface is known.
    ///
    /// A large flat obstacle standing parallel in front of the wall, such as an AC unit's screen
    /// or a parked car's side, can qualify as the far surface (`FarSurface.spans`). The wall and
    /// ground behind it then read unseen rather than hidden: the walk stops asking to look past
    /// it, but nothing behind it is claimed seen, so a check there stays unsure.
    ///
    /// With `reads`, both cells this could consult are noted with what the lookup holds there,
    /// before the empty-lookup return and the short circuit: under another lookup the answer can
    /// turn on either of them (`RebuildViewCache`).
    private func endsSpace(_ point: SIMD3<Float>, reading: Float, depth: Float, camera: CameraFrame, reads: FarSurfaceReadLog? = nil) -> Bool {
        // z-depth grows in proportion along a ray from the camera, so what the reading met lies
        // that fraction of the way to the point.
        let met = camera.position + (point - camera.position) * (reading / depth)
        if let reads {
            for world in [point, met] {
                let cell = cellIndex(forS: wall.wallPoint(world).s)
                reads.note(cell, farSurfaceByCell[cell])
            }
        }
        guard !farSurfaceByCell.isEmpty else { return false }
        func pastSpace(_ world: SIMD3<Float>) -> Bool {
            let at = wall.wallPoint(world)
            guard let far = farSurface(atS: at.s) else { return false }
            return at.out >= far - config.farSurfaceTolerance
        }
        return pastSpace(point) || pastSpace(met)
    }

    /// The rows of a cell whose samples, a quarter and three quarters along it, are all in view:
    /// in front of the camera, inside the image margin, within `maxDistance`, and seen within
    /// `maxAngleFromNormal` of the surface's normal: the outward of the cell's piece of wall for
    /// the wall face, up for the ground. With `depth`, a row in view is seen only when depth
    /// matches both samples, and hidden when it finds either behind something nearer.
    private func sampledRows(
        _ index: Int, from camera: CameraFrame, depth: DepthImage?, rows: [Int], onWallFace: Bool, reads: FarSurfaceReadLog? = nil,
        point: (_ s: Float, _ row: Int) -> SIMD3<Float>
    ) -> RowViews {
        let range = cellRange(index)
        let width = range.upperBound - range.lowerBound
        let middle = range.lowerBound + width / 2
        // Behind the plane of the cell's piece of wall the wall itself hides both bands, and the
        // ground row at the wall's foot would pass from there.
        guard wall.out(of: camera.position, pieceAtS: middle) > 0 else { return RowViews() }
        let normal = onWallFace ? wall.segment(atS: middle).outward : WallFrame.up
        let alongs = [range.lowerBound + width * 0.25, range.lowerBound + width * 0.75]
        let cosLimit = cos(config.maxAngleFromNormal)
        func sees(_ point: SIMD3<Float>) -> Bool {
            let toCamera = camera.position - point
            let distance = simd_length(toCamera)
            guard distance <= config.maxDistance, distance > 0 else { return false }
            guard simd_dot(toCamera / distance, normal) >= cosLimit else { return false }
            guard let pixel = camera.pixel(of: point) else { return false }
            return camera.contains(pixel: pixel, margin: config.imageMargin)
        }
        var views = RowViews()
        for row in rows {
            let points = alongs.map { point($0, row) }
            guard points.allSatisfy(sees) else { continue }
            guard let depth else {
                views.seen.insert(row)
                continue
            }
            let evidence = points.map { depthEvidence($0, camera: camera, depth: depth, reads: reads) }
            if evidence.allSatisfy({ $0 == .matches }) {
                views.seen.insert(row)
            } else if evidence.contains(.nearer) {
                views.hidden.insert(row)
            } else if evidence.contains(.pastSpace) {
                views.pastSpace.insert(row)
            }
        }
        return views
    }

    // MARK: Wall height

    /// How high up the wall a cell was seen, meters above the ground: the highest wall row such
    /// that every row from the lowest foot row up to it was seen from two positions at least
    /// `coveringBaseline` apart (with depth, and never through something nearer), less
    /// `heightError`. It is the height the covered samples reach, never the edge of a row nobody
    /// sampled. Nil when not even the first row above the foot is covered, nothing is left after
    /// the error, the cell lies beyond a marked end, or its claims were withdrawn.
    public func wallSeenHeight(at index: Int) -> Float? {
        guard !withdrawnCells.contains(index), let rows = seenWallRowHeight(at: index) else { return nil }
        let height = rows - heightError
        return height > 0 ? height : nil
    }

    /// `wallSeenHeight(at:)` before `heightError`: the height of the rows themselves.
    private func seenWallRowHeight(at index: Int) -> Float? {
        guard allows(index), let cell = cells[.wall]?[index] else { return nil }
        let covered = cell.rows.prefix { $0.count >= 2 }.count
        guard covered >= cell.firstBandRow + 2 else { return nil }
        return wallRows[covered - 1]
    }

    /// Stretches of wall face seen and how high each was seen (`wallSeenHeight(at:)`), merged
    /// where neighbouring cells reached the same height, for scene.json's wall band. Every
    /// height is a whole number of rows less the same error, so equal heights merge exactly.
    public func wallSeenSpans() -> [ObservedSpan] {
        let items = (cells[.wall] ?? [:]).keys.sorted().compactMap { index in
            wallSeenHeight(at: index).map { ObservedSpan(span: cellRange(index), out: $0) }
        }
        return ObservedSpan.merge(items, touching: config.cellWidth * 0.01).map(clippedToEnds)
    }

    // MARK: Ground depth

    /// Distance out from the wall of each ground depth row: 0, then every `groundDepthSpacing`
    /// up to `groundDepthReach`.
    public var groundDepthRows: [Float] {
        let count = Int((config.groundDepthReach / config.groundDepthSpacing + 1e-3).rounded(.down))
        return (0...max(1, count)).map { Float($0) * config.groundDepthSpacing }
    }

    /// The ground depth rows of a cell a frame sees, by the same rules as the bands' rows.
    public func visibleDepthRows(_ index: Int, from camera: CameraFrame) -> Set<Int> {
        visibleDepthRows(index, from: camera, depth: nil)
    }

    private func visibleDepthRows(_ index: Int, from camera: CameraFrame, depth: DepthImage?) -> Set<Int> {
        depthRowViews(index, from: camera, depth: depth).seen
    }

    private func depthRowViews(_ index: Int, from camera: CameraFrame, depth: DepthImage?, reads: FarSurfaceReadLog? = nil) -> RowViews {
        let rows = groundDepthRows
        return sampledRows(index, from: camera, depth: depth, rows: Array(rows.indices), onWallFace: false, reads: reads) { s, row in
            wall.world(s: s, height: 0, out: rows[row])
        }
    }

    /// What one frame showed of one cell's ground depth rows.
    private struct DepthRowView: Sendable {
        var index: Int
        var views: RowViews
    }

    /// A frame's view of the ground depth rows of every cell it shows any of: the part of
    /// `recordDepth` that reads no recorded state.
    private func depthRowViews(from camera: CameraFrame, depth: DepthImage?, reads: FarSurfaceReadLog? = nil) -> [DepthRowView] {
        candidateIndices(for: camera).compactMap { index in
            let views = depthRowViews(index, from: camera, depth: depth, reads: reads)
            guard !views.seen.isEmpty || !views.hidden.isEmpty || !views.pastSpace.isEmpty else { return nil }
            return DepthRowView(index: index, views: views)
        }
    }

    /// Adds a kept frame's view of the ground depth rows; true when any row gained a position.
    /// Hidden rows gain nothing, so the reach stops at the first row something stood in front of,
    /// and a frame without depth adds nothing to a row an earlier depth frame found hidden. Rows
    /// the depth showed past where the space ends are kept apart from hidden ones
    /// (`depthPastSpace`), and a frame without depth adds nothing to them either.
    @discardableResult
    private mutating func recordDepth(from camera: CameraFrame, depth: DepthImage?) -> Bool {
        recordDepth(depthRowViews(from: camera, depth: depth), from: camera.position, depthChecked: depth != nil)
    }

    /// `recordDepth(from:depth:)` given the frame's views, from `position`; `depthChecked` when the
    /// frame had depth.
    @discardableResult
    private mutating func recordDepth(_ found: [DepthRowView], from position: SIMD3<Float>, depthChecked: Bool) -> Bool {
        let rowCount = groundDepthRows.count
        var changed = false
        for item in found {
            let index = item.index, views = item.views
            var rows = depthCells[index] ?? Array(repeating: [], count: rowCount)
            for row in views.hidden {
                if Self.dropUnverified(&rows[row]) { changed = true }
                if rows[row].count < 2 { depthHidden[index, default: []].insert(row) }
            }
            // A sighting no depth confirmed, of ground behind the surface where the space ends,
            // was of that surface.
            for row in views.pastSpace {
                if Self.dropUnverified(&rows[row]) { changed = true }
                if rows[row].count < 2 { depthPastSpace[index, default: []].insert(row) }
            }
            let hidden = depthChecked ? [] : (depthHidden[index] ?? []).union(depthPastSpace[index] ?? [])
            for row in views.seen where !hidden.contains(row) {
                if add(&rows[row], position, verified: depthChecked) { changed = true }
            }
            depthCells[index] = rows
        }
        return changed
    }

    /// How far out from the wall the ground of a cell was seen, meters: the farthest depth row
    /// such that every row from the wall foot out to it was seen from two positions at least
    /// `coveringBaseline` apart (occlusion only with depth; see the type's comment). Nil when
    /// not even the first row past the foot is, the cell lies beyond a marked end, or its claims
    /// were withdrawn.
    public func groundDepth(at index: Int) -> Float? {
        guard allows(index), !withdrawnCells.contains(index), let rows = depthCells[index] else { return nil }
        let covered = rows.prefix { $0.count >= 2 }.count
        guard covered >= 2 else { return nil }
        return groundDepthRows[covered - 1]
    }

    /// Stretches of ground and how far out each was seen (`groundDepth(at:)`, and past a limit
    /// end `groundDepthPastLimit`), merged where neighbouring cells reached the same depth.
    /// Every depth is a whole number of rows, so equal depths merge exactly and none is rounded up.
    public func groundDepthSpans() -> [ObservedSpan] {
        let inside = ObservedSpan.merge(depthCells.keys.sorted().compactMap { index in
            groundDepth(at: index).map { ObservedSpan(span: cellRange(index), out: $0) }
        }, touching: config.cellWidth * 0.01).map(clippedToEnds)
        let past = pastLimitCells.flatMap { side, cells in
            cells.keys.compactMap { cell -> ObservedSpan? in
                guard let end = end(side), let depth = groundDepthPastLimit(side, cell) else { return nil }
                return ObservedSpan(span: pastCellRange(side, cell, end: end), out: depth)
            }
        }
        let all = (inside + past).sorted { $0.span.lowerBound < $1.span.lowerBound }
        return ObservedSpan.merge(all, touching: config.cellWidth * 0.01)
    }

    // MARK: Ground past a limit end

    /// How far out the ground of a cell past a limit end was seen, meters: the farthest depth row
    /// such that every row out to it, on both sides of the wall's continued line, was seen from
    /// two positions at least `coveringBaseline` apart. The server reads ground past a limit end
    /// as covering both sides of that line (server/README.md "Ends and corners" on
    /// origin/t3/server), so one side seen is not enough. Cell 0 touches the end. Nil when the
    /// end on `side` is not a limit or not even the first row past the line is seen both ways.
    public func groundDepthPastLimit(_ side: WalkSide, _ cell: Int) -> Float? {
        guard limitEnds.contains(side), let rows = pastLimitCells[side]?[cell] else { return nil }
        let count = groundDepthRows.count
        let front = rows[0..<count].prefix { $0.count >= 2 }.count
        let behind = rows[count...].prefix { $0.count >= 2 }.count
        let covered = min(front, behind)
        guard covered >= 2 else { return nil }
        return groundDepthRows[covered - 1]
    }

    private func end(_ side: WalkSide) -> Float? { side == .left ? leftEnd : rightEnd }

    /// The s range of the `cell`th cell past `end`, counting away from the meter.
    private func pastCellRange(_ side: WalkSide, _ cell: Int, end: Float) -> ClosedRange<Float> {
        let near = end + side.sign * Float(cell) * config.cellWidth
        let far = end + side.sign * Float(cell + 1) * config.cellWidth
        return min(near, far)...max(near, far)
    }

    /// The piece of wall a limit end is on. Past the end the server continues this piece's line
    /// straight (the chain the export sends stops at the end), so past cells use it too. A
    /// corner's own s belongs to the piece on its left, which is the chain's last piece for a
    /// right end there; a left end there starts the piece on its right.
    private func endPiece(_ side: WalkSide, end: Float) -> WallSegment {
        wall.segment(atS: side == .left ? end.nextUp : end)
    }

    /// Adds a kept frame's view of the ground past each limit end; true when any row gained a
    /// position.
    ///
    /// Samples are placed and seen like the ground depth rows (`sampledRows`), on the end
    /// piece's line continued. The camera must stand in front of the wall. A sample behind the
    /// continued line counts only when the sight line to it crosses that line past the end: one
    /// crossing short of the end runs through the house. Nothing else hides anything: the
    /// house's other walls are not modelled (at an inside corner, where the next wall comes
    /// forward, ground past the end is counted though it is indoors), and without depth neither
    /// is anything standing on the ground (see the type's comment).
    @discardableResult
    private mutating func recordPastLimits(from camera: CameraFrame, depth: DepthImage?) -> Bool {
        recordPastLimits(pastLimitViews(from: camera, depth: depth), from: camera.position, depthChecked: depth != nil)
    }

    /// What one frame showed of a sample row past a limit end.
    private enum PastLimitRow: UInt8, Sendable {
        /// Not both samples in view, or in view without matching: adds nothing.
        case none
        /// Depth found something nearer, or the surface where the space ends, at either sample.
        case blocked
        /// Both samples in view and, with depth, matched.
        case matches
    }

    /// One frame's view of the rows of one cell past a limit end (`pastLimitCells`).
    private struct PastLimitCellView: Sendable {
        var side: WalkSide
        var cell: Int
        var rows: [PastLimitRow]
    }

    /// A frame's view of the ground past each limit end: the part of `recordPastLimits` that reads
    /// no recorded state. Cells it shows nothing of are left out.
    private func pastLimitViews(from camera: CameraFrame, depth: DepthImage?, reads: FarSurfaceReadLog? = nil) -> [PastLimitCellView] {
        guard wall.wallPoint(camera.position).out > 0 else { return [] }
        var found: [PastLimitCellView] = []
        for side in limitEnds {
            guard let end = end(side) else { continue }
            let piece = endPiece(side, end: end)
            let origin = wall.origin
            let eye = piece.coordinates(ofOffset: camera.position - origin)
            guard eye.out > 0 else { continue }
            // Cells whose s lies within `maxDistance` of the camera's along the end's piece.
            let beyond = (eye.s - end) * side.sign
            let last = Int(((beyond + config.maxDistance) / config.cellWidth).rounded(.down))
            let first = max(0, Int(((beyond - config.maxDistance) / config.cellWidth).rounded(.down)))
            guard last >= first else { continue }
            let depths = groundDepthRows
            let cosLimit = cos(config.maxAngleFromNormal)
            func point(_ s: Float, _ out: Float) -> SIMD3<Float> {
                origin + piece.anchor + piece.along * (s - piece.anchorS) + piece.outward * out
            }
            /// Nil when the sample is out of view; without depth, a sample in view counts as seen.
            func evidence(_ s: Float, _ out: Float) -> DepthEvidence? {
                if out < 0 {
                    // Where the sight line crosses the continued line (out = 0).
                    let crossing = eye.s + (s - eye.s) * eye.out / (eye.out - out)
                    guard (crossing - end) * side.sign >= 0 else { return nil }
                }
                let target = point(s, out)
                let toCamera = camera.position - target
                let distance = simd_length(toCamera)
                guard distance <= config.maxDistance, distance > 0,
                      simd_dot(toCamera / distance, WallFrame.up) >= cosLimit,
                      let pixel = camera.pixel(of: target),
                      camera.contains(pixel: pixel, margin: config.imageMargin) else { return nil }
                guard let depth else { return .matches }
                return depthEvidence(target, camera: camera, depth: depth, reads: reads)
            }
            for cell in first...last {
                let range = pastCellRange(side, cell, end: end)
                let width = range.upperBound - range.lowerBound
                let alongs = [range.lowerBound + width * 0.25, range.lowerBound + width * 0.75]
                var rows = [PastLimitRow](repeating: .none, count: 2 * depths.count)
                for row in rows.indices {
                    let out = row < depths.count ? depths[row] : -depths[row - depths.count]
                    let samples = alongs.map { evidence($0, out) }
                    guard samples.allSatisfy({ $0 != nil }) else { continue }
                    // Past a limit end nothing is asked to be looked past, so the surface where
                    // the space ends blocks a row just as anything nearer does.
                    if depth != nil, samples.contains(where: { $0 == .nearer || $0 == .pastSpace }) {
                        rows[row] = .blocked
                    } else if samples.allSatisfy({ $0 == .matches }) {
                        rows[row] = .matches
                    }
                }
                if rows.contains(where: { $0 != .none }) { found.append(PastLimitCellView(side: side, cell: cell, rows: rows)) }
            }
        }
        return found
    }

    /// `recordPastLimits(from:depth:)` given the frame's views, from `position`; `depthChecked`
    /// when the frame had depth.
    @discardableResult
    private mutating func recordPastLimits(_ found: [PastLimitCellView], from position: SIMD3<Float>, depthChecked: Bool) -> Bool {
        var changed = false
        for view in found {
            var rows = pastLimitCells[view.side]?[view.cell] ?? Array(repeating: [], count: view.rows.count)
            var added = false
            for (row, kind) in view.rows.enumerated() {
                switch kind {
                case .none:
                    continue
                case .blocked:
                    if Self.dropUnverified(&rows[row]) { added = true }
                    if rows[row].count < 2 { pastLimitHidden[view.side, default: [:]][view.cell, default: []].insert(row) }
                case .matches:
                    guard depthChecked || !(pastLimitHidden[view.side]?[view.cell]?.contains(row) ?? false) else { continue }
                    if add(&rows[row], position, verified: depthChecked) { added = true }
                }
            }
            if added {
                pastLimitCells[view.side, default: [:]][view.cell] = rows
                changed = true
            }
        }
        return changed
    }

    /// Indices of `observedCameras` in the order the frames were captured, which a rebuild replays
    /// them in: `observe` sees them in the order their photos finished storing. By walked-path
    /// segment (a replay's gap loop plays recorded frames again in a later one), then frame time,
    /// a frame without one (the close-up) first in its segment, then the order observed.
    private var captureOrder: [Int] {
        observedCameras.indices.sorted { a, b in
            (observedSegments[a], observedTimes[a] ?? -.infinity, a) < (observedSegments[b], observedTimes[b] ?? -.infinity, b)
        }
    }

    /// Rebuilds the ground past the limit ends from `observedCameras`, after an end moved or
    /// became or stopped being a limit.
    private mutating func replayPastLimits() {
        pastLimitCells = [:]
        pastLimitHidden = [:]
        guard !limitEnds.isEmpty else { return }
        prepareRebuildViews()
        for index in captureOrder {
            let views = rebuildPastLimitViews(index)
            recordPastLimits(views, from: observedCameras[index].position, depthChecked: observedDepths[index] != nil)
        }
    }

    // MARK: Where the space ends

    /// Stretches of wall (s, meters) and how far out from the wall the open space in front of
    /// each visibly ends (`FarSurface.spans`): a corridor's far wall, a side yard's fence. Set by
    /// `setFarSurface(_:)`; empty until then.
    public private(set) var farSurface: [ObservedSpan] = []

    /// Records where the space in front of the wall ends (#160, #164). With depth, a reading
    /// nearer than a sample that is that surface, or a sample at or past it, then counts as the
    /// end of the space (neither seen nor hidden) rather than as something in front of the wall to
    /// look past.
    ///
    /// When the surface over any cell changes and a kept frame had depth, every kept frame is
    /// replayed against the new surface, as it is for a new ground or wall
    /// (`replayObservedCameras`). ARKit can find a corridor's far wall only after depth frames
    /// have marked the ground behind it hidden. Before this replay those rows stayed hidden and
    /// the walk kept asking to look past the wall (review of #168). When ARKit stops finding a
    /// surface, the replay marks hidden again whatever that surface ended. Real obstructions
    /// nearer the wall stay hidden, and skipped and withdrawn cells are kept. A row that depth
    /// shows past the surface loses the sightings no depth confirmed, and later views without
    /// depth add nothing to it (`Cell.pastSpaceRows`). Frames without depth never consult the
    /// surface, so a map without depth frames replays nothing and its revision stays the same.
    public mutating func setFarSurface(_ spans: [ObservedSpan]) {
        setFarSurface(spans, replay: true)
    }

    // Wall updates and corner turns already replay all kept frames. They update the lookup
    // without replaying here, so each operation rebuilds once, with its own skipped-cell shift.
    private mutating func setFarSurface(_ spans: [ObservedSpan], replay: Bool) {
        guard spans != farSurface else { return }
        farSurface = spans
        var byCell: [Int: Float] = [:]
        for item in spans {
            for index in indices(overlapping: item.span) {
                byCell[index] = min(byCell[index] ?? item.out, item.out)
            }
        }
        guard byCell != farSurfaceByCell else { return }
        farSurfaceByCell = byCell
        if replay, observedDepths.contains(where: { $0 != nil }) { replayObservedCameras(shiftingSkippedBy: 0) }
    }

    /// How far out from the wall the space ends over the cell holding `s`, meters (the nearest
    /// surface where spans overlap), or nil where no surface is known.
    public func farSurface(atS s: Float) -> Float? {
        farSurfaceByCell[cellIndex(forS: s)]
    }

    /// The ground depth rows of a cell that a kept frame's depth showed hidden behind something
    /// standing in front of the wall, and that are still short of two positions. Rows past where
    /// the space ends are not among them (#160).
    public func groundDepthHiddenRows(at index: Int) -> Set<Int> {
        (depthHidden[index] ?? []).filter { (depthCells[index]?[$0].count ?? 0) < 2 }
    }

    // MARK: Facing space

    /// The server's position error for the wall line at `s`, meters: the default for how the
    /// piece holding `s` was found (tap 0.3 ft, mesh 0.5, plane 0.75) plus its drift of 0.16 ft
    /// per foot from the meter (`ServerErrorDefaults`, mirrored from the server's rules). The
    /// server widens each wall's error by its source, so a walked clearance measured from a
    /// detected-plane wall has to give up more than one from a tapped wall.
    public func positionError(atS s: Float) -> Float {
        ServerErrorDefaults.wall(wall.segment(atS: s).source, atS: s)
    }

    /// Records how the meter piece's line was found (`WallFrame.source`); the export and the
    /// walked clearance read it.
    public mutating func setWallLineSource(_ source: WallLineSource) {
        guard wall.source != source else { return }
        wall.source = source
        revision += 1
    }

    /// How far out from the wall a cell is known clear because the homeowner walked past it,
    /// meters. A step is the straight line between consecutive kept positions with normal
    /// tracking, with no `breakWalkedPath` between them, both in front of the wall, at most
    /// `walkStep` apart and kept at most `walkGap` seconds apart; it shows the space
    /// between the wall and itself clear, out to its end nearer the wall. The cell is clear out
    /// to the largest distance d such that steps reaching at least d cover it along the wall.
    /// That distance less `positionError` at the cell's edges (the larger: farther from the
    /// meter, or on the piece with the larger default) is the clearance, which the contract takes as exact ("for a walked path, the distance from the
    /// wall less your position error"). Nil when no steps cover the cell, nothing is left after
    /// the error, the cell lies beyond a marked end, or its claims were withdrawn.
    ///
    /// Only the phone's path is known, so this is the space between the wall and the phone; the
    /// homeowner's body is behind it, farther out. Nothing observed the space under the path: the
    /// claim that it is clear is inferred, one of the type's bounded exceptions, which the spot
    /// check backs for the chosen spot and `withdrawClaims(over:)` takes back.
    public func walkedClearance(at index: Int) -> Float? {
        walkedClearance(at: index, steps: walkedSteps())
    }

    private func walkedClearance(at index: Int, steps: [WalkedStep]) -> Float? {
        guard allows(index), !withdrawnCells.contains(index) else { return nil }
        let cell = cellRange(index)
        let tolerance: Float = 1e-5
        let over = steps.filter { $0.low < cell.upperBound - tolerance && $0.high > cell.lowerBound + tolerance }
        // Deepest first: the first depth whose steps join up across the cell is the answer.
        for depth in Set(over.map(\.nearest)).sorted(by: >) {
            var reached = cell.lowerBound
            for step in over.filter({ $0.nearest >= depth }).sorted(by: { $0.low < $1.low }) where step.low <= reached + tolerance {
                reached = max(reached, step.high)
            }
            guard reached >= cell.upperBound - tolerance else { continue }
            let clear = depth - max(positionError(atS: cell.lowerBound), positionError(atS: cell.upperBound))
            return clear > 0 ? clear : nil
        }
        return nil
    }

    /// Stretches known clear in front of the wall from the walked path (`walkedClearance(at:)`),
    /// neighbours of equal clearance merged. The error grows by 0.08 ft a cell, so this is
    /// nearly one span per walked cell (80 for a walk 6 m either side of the meter).
    public func facingSpans() -> [ObservedSpan] {
        let steps = walkedSteps()
        guard let low = steps.map(\.low).min(), let high = steps.map(\.high).max(), low < high else { return [] }
        let items = indices(overlapping: low...high).compactMap { index in
            walkedClearance(at: index, steps: steps).map { ObservedSpan(span: cellRange(index), out: $0) }
        }
        return ObservedSpan.merge(items, touching: config.cellWidth * 0.01).map(clippedToEnds)
    }

    private struct WalkedStep {
        var low: Float
        var high: Float
        /// Distance from the wall of the step's end nearer to it.
        var nearest: Float
    }

    /// The homeowner's path is not continuous from the last kept pose to the next one (tracking
    /// was lost, or the capture paused): walked-path facing does not join the poses on either
    /// side. A straight line across an outage or a detour would claim space no one walked past.
    /// Frames captured from now on belong to a new `pathSegment`.
    public mutating func breakWalkedPath() {
        pathSegment += 1
    }

    /// Steps between poses kept in the same segment, in capture order (their times): the order
    /// they were observed in is the order their photos finished storing, which can differ.
    private func walkedSteps() -> [WalkedStep] {
        let timed = observedCameras.indices.compactMap { index in observedTimes[index].map { (index, $0) } }
        let segments = Dictionary(grouping: timed) { observedSegments[$0.0] }.values
        return segments.flatMap { members -> [WalkedStep] in
            let ordered = members.sorted { ($0.1, $0.0) < ($1.1, $1.0) }
            return zip(ordered, ordered.dropFirst()).compactMap { earlier, later in step(from: earlier, to: later) }
        }
    }

    private func step(from earlier: (Int, Double), to later: (Int, Double)) -> WalkedStep? {
        guard later.1 - earlier.1 <= config.walkGap else { return nil }
        let first = observedCameras[earlier.0]
        let second = observedCameras[later.0]
        guard simd_distance(first.position, second.position) <= config.walkStep else { return nil }
        let a = wall.wallPoint(first.position)
        let b = wall.wallPoint(second.position)
        guard a.out > 0, b.out > 0 else { return nil }
        return WalkedStep(low: min(a.s, b.s), high: max(a.s, b.s), nearest: min(a.out, b.out))
    }

    // MARK: Overhead

    /// What a view tilted up at the wall shows: per cell along the wall, the height on the wall's
    /// plane the view reached, rounded down to `overheadQuantum`, neighbours of equal height
    /// merged. A pure function of the camera, the wall and the marked ends; it records nothing,
    /// and it is what decides whether a view is tilted up at all (the tilt-up step, the overhead
    /// question).
    ///
    /// A cell counts when both its samples (a quarter and three quarters along it) are in view at
    /// `wallCaptureHeight`, the top of the band the walk asks for; its height is the highest point
    /// above that still in view at both, where in view means in front of the camera, inside the
    /// image margin and within `maxDistance`. The view of a plane is convex, so everything between
    /// the two heights is in view too. Unlike the bands there is no limit on obliqueness: nothing
    /// is measured on this part of the wall, the homeowner only judges whether anything is
    /// overhead. Empty when the camera is behind the wall or does not show the wall at
    /// `wallCaptureHeight`.
    ///
    /// The camera sees the wall's plane, not what is on it: it can't tell open sky above a
    /// one-storey eave from the wall. So a reach is evidence only once the homeowner has said
    /// nothing is overhead (`recordOverhead`), and then only from where the walk saw the wall up
    /// to (`overheadHeight(at:)`).
    public func overheadReach(from camera: CameraFrame) -> [ObservedSpan] {
        overheadReach(from: camera) { _ in config.wallCaptureHeight }
    }

    /// `overheadReach(from:)` with the height each cell must be in view from given per cell; a
    /// cell with none is left out.
    private func overheadReach(from camera: CameraFrame, startingAt start: (Int) -> Float?) -> [ObservedSpan] {
        guard wall.wallPoint(camera.position).out > 0 else { return [] }
        func inView(_ s: Float, _ height: Float) -> Bool {
            let point = wall.world(s: s, height: height)
            guard simd_distance(point, camera.position) <= config.maxDistance, let pixel = camera.pixel(of: point) else { return false }
            return camera.contains(pixel: pixel, margin: config.imageMargin)
        }
        func reach(_ s: Float, from bottom: Float) -> Float? {
            guard inView(s, bottom) else { return nil }
            var low = bottom
            var high = bottom + config.maxDistance
            // Bisection to under a millimetre; `low` stays in view throughout.
            for _ in 0..<14 {
                let middle = (low + high) / 2
                if inView(s, middle) { low = middle } else { high = middle }
            }
            return low
        }
        let items = candidateIndices(for: camera).compactMap { index -> ObservedSpan? in
            guard let bottom = start(index) else { return nil }
            let range = cellRange(index)
            let width = range.upperBound - range.lowerBound
            guard let a = reach(range.lowerBound + width * 0.25, from: bottom),
                  let b = reach(range.lowerBound + width * 0.75, from: bottom) else { return nil }
            let height = (min(a, b) / config.overheadQuantum).rounded(.down) * config.overheadQuantum
            return ObservedSpan(span: range, out: height)
        }
        return ObservedSpan.merge(items, touching: config.cellWidth * 0.01).map(clippedToEnds)
    }

    /// What a recorded view is evidence of: its reach over each cell counted from the height the
    /// walk saw that cell's wall up to (its rows, before `heightError`), so what the view shows
    /// clear above joins what the walk saw below with no stretch of wall between them that
    /// neither showed. A cell whose wall nobody saw gives nothing. A fixed start, such as the
    /// band's top, would assume every cell had been seen that high.
    private func overheadEvidence(from camera: CameraFrame) -> [ObservedSpan] {
        overheadReach(from: camera) { seenWallRowHeight(at: $0) }
    }

    /// Keeps a tilt-up view as overhead evidence. Call it only after the homeowner answered that
    /// nothing is overhead there (ScanActions.answerOverhead(clear: true)): the camera cannot
    /// tell a clear view from an eave. Returns what the view showed (`overheadReach`); nothing
    /// is kept when tracking was not normal or the view showed no wall at `wallCaptureHeight`.
    @discardableResult
    public mutating func recordOverhead(_ camera: CameraFrame, trackingNormal: Bool) -> [ObservedSpan] {
        guard trackingNormal else { return [] }
        let reach = overheadReach(from: camera)
        guard !reach.isEmpty else { return [] }
        overheadCameras.append(camera)
        revision += 1
        return reach
    }

    /// Height seen clear above a cell, meters: the highest any recorded tilt-up view reached
    /// there (`overheadEvidence`), less `heightError`. Nil when none reached it.
    public func overheadHeight(at index: Int) -> Float? {
        guard allows(index) else { return nil }
        let middle = (cellRange(index).lowerBound + cellRange(index).upperBound) / 2
        let reached = overheadCameras.compactMap { camera in
            overheadEvidence(from: camera).first { $0.span.contains(middle) }?.out
        }.max()
        return reached.map { $0 - heightError }.flatMap { $0 > 0 ? $0 : nil }
    }

    /// Stretches seen clear overhead, each with the height seen (`overheadHeight(at:)`), for
    /// scene.json's overhead band.
    public func overheadSpans() -> [ObservedSpan] {
        var best: [Int: Float] = [:]
        for camera in overheadCameras {
            for item in overheadEvidence(from: camera) {
                for index in indices(overlapping: item.span) where allows(index) {
                    best[index] = max(best[index] ?? item.out, item.out)
                }
            }
        }
        let items = best.keys.sorted().compactMap { index -> ObservedSpan? in
            guard let reached = best[index], reached - heightError > 0 else { return nil }
            return ObservedSpan(span: cellRange(index), out: reached - heightError)
        }
        return ObservedSpan.merge(items, touching: config.cellWidth * 0.01).map(clippedToEnds)
    }

    /// A span trimmed to the marked ends. Cells overlapping an end keep only the allowed part.
    private func clippedToEnds(_ observed: ObservedSpan) -> ObservedSpan {
        var span = observed.span
        if let leftEnd { span = max(span.lowerBound, leftEnd)...max(span.upperBound, leftEnd) }
        if let rightEnd { span = min(span.lowerBound, rightEnd)...min(span.upperBound, rightEnd) }
        return ObservedSpan(span: span, out: observed.out)
    }

    /// Whether a frame sees any row of the cell.
    public func isVisible(_ band: SurfaceBand, _ index: Int, from camera: CameraFrame) -> Bool {
        !visibleRows(band, index, from: camera).isEmpty
    }

    // MARK: Homeowner input

    /// Marks the wall's end on `side` at `s`. The end is unexplored until `setEndIsLimit` says
    /// otherwise, also when it was a limit before it moved.
    public mutating func setEnd(_ side: WalkSide, at s: Float) {
        switch side {
        case .left: leftEnd = s
        case .right: rightEnd = s
        }
        // Precomputed `record` maps have no kept frames to replay.
        if observedCameras.isEmpty {
            if limitEnds.remove(side) != nil { replayPastLimits() }
            revision += 1
        } else {
            limitEnds.remove(side)
            // Re-marking after a clear applies the same bounds to the restored sightings.
            replayObservedCameras(shiftingSkippedBy: 0)
        }
    }

    /// Says whether the marked end on `side` is a real limit (scene.json's `limit`) or not
    /// (`unexplored`). Only past a limit is ground reported. Does nothing when no end is marked
    /// on that side: a limit is a property of a marked end.
    public mutating func setEndIsLimit(_ side: WalkSide, _ isLimit: Bool) {
        guard end(side) != nil, limitEnds.contains(side) != isLimit else { return }
        if isLimit { limitEnds.insert(side) } else { limitEnds.remove(side) }
        replayPastLimits()
        revision += 1
    }

    /// Forgets a marked end, for example to walk past it when the server asks what lies beyond.
    public mutating func clearEnd(_ side: WalkSide) {
        switch side {
        case .left: leftEnd = nil
        case .right: rightEnd = nil
        }
        limitEnds.remove(side)
        // Kept frames were clipped to the old end. Rebuild rather than merge so repeated clears
        // restore those sightings without counting the same view twice.
        replayObservedCameras(shiftingSkippedBy: 0)
    }

    /// Marks the not-yet-covered cells of `range` as skipped ("I can't get there").
    public mutating func markSkipped(_ band: SurfaceBand, _ range: ClosedRange<Float>) {
        for index in indices(overlapping: range) where allows(index) {
            var cell = cells[band]?[index] ?? newCell(band)
            guard !cell.covered else { continue }
            cell.skipped = true
            cells[band, default: [:]][index] = cell
        }
        revision += 1
    }

    /// Withdraws what the map claims over every cell overlapping `range`: the wall face and the
    /// ground, near band and depth rows alike, and the walked-path clearance. The cells read
    /// `.skipped` in both bands, and the export reports none of them (`wallSeenSpans`,
    /// `groundDepthSpans`, `facingSpans`), so the server treats the stretch as unseen. For the
    /// homeowner's "Something's there" in the spot check, where the camera-only and walked-path
    /// claims were wrong, and "I can't check this area", where nothing backs them (see the type's
    /// "Bounded exceptions").
    ///
    /// It lasts for the scan, through rebuilds, and later views add nothing there: a photo taken
    /// without depth would claim the same stretch past the same obstruction. Overhead views are
    /// left alone: the question asks what stands in front of the wall and on the ground, not what
    /// is overhead. So is ground past a limit end, which lies outside the ends a spot stands
    /// between.
    public mutating func withdrawClaims(over range: ClosedRange<Float>) {
        withdrawnCells.formUnion(indices(overlapping: range))
        revision += 1
    }

    /// Whether any cell overlapping `range` has its claims withdrawn.
    public func hasWithdrawnClaims(overlapping range: ClosedRange<Float>) -> Bool {
        indices(overlapping: range).contains { withdrawnCells.contains($0) }
    }

    /// Moves the map to a new wall frame (after the meter anchor is refined or the ground is
    /// measured) and recomputes what every observed camera saw against it.
    ///
    /// Shifting cells is not enough: a cell records which heights a camera saw, and a corrected
    /// ground or wall plane moves the sample rows to heights no frame may have looked at. With a
    /// ground 0.3 m too high, shifted cells claimed wall and ground that were only ever sampled in
    /// the air. So seen and covered cells are rebuilt by replaying `observedCameras`; marked ends
    /// move exactly (s is measured from the meter, so moving it by d along the wall moves every
    /// end by -d), and skipped cells move by whole cells, the remainder (under half a cell,
    /// 7.6 cm, below tap error) waiting for later moves. The replay clips to the current ends, so
    /// cells beyond an end seen before it was marked are gone until a new frame sees them.
    /// Corners the walk followed move like the ends, so they keep their place in the world: pass
    /// `frame` with the corners it had (a copy of `wall` with a new meter or ground).
    ///
    /// The wall must run the same way: a turned wall has no single shift of s that keeps the ends
    /// and corners where they were, and the cameras would be replayed against axes they weren't
    /// seen with. A moved or turned meter anchor moves everything captured with it (`apply(_:)`);
    /// a frame with other axes stops here.
    public mutating func updateWall(_ frame: WallFrame) {
        guard frame != wall else { return }
        precondition(frame.hasSameAxes(as: wall), "updateWall got a wall turned from \(wall.outward) to \(frame.outward); a turned anchor is apply(_:)")
        let delta = simd_dot(wall.meter - frame.meter, frame.along)
        var frame = frame
        frame.shiftCorners(by: delta)
        wall = frame
        leftEnd = leftEnd.map { $0 + delta }
        rightEnd = rightEnd.map { $0 + delta }
        // Where the space ends stays where it was in the world, as the ends do.
        setFarSurface(farSurface.map { ObservedSpan(span: ($0.span.lowerBound + delta)...($0.span.upperBound + delta), out: $0.out) }, replay: false)
        pendingShift += delta
        let whole = Int((pendingShift / config.cellWidth).rounded())
        pendingShift -= Float(whole) * config.cellWidth
        replayObservedCameras(shiftingSkippedBy: whole)
    }

    /// Moves everything the map holds with the world, as one rigid body: the wall (its meter,
    /// ground, pieces and corners) and every kept camera. Used when ARKit corrects the meter's
    /// anchor (`MeterAnchorTracking`): what was captured moves with the meter, so the relations
    /// between the wall and the views of it, and every s (cells, ends, corners), stay exactly as
    /// they were, and nothing is rebuilt.
    public mutating func apply(_ correction: YawCorrection) {
        wall.apply(correction)
        observedCameras = observedCameras.map { correction.moved($0) }
        overheadCameras = overheadCameras.map { correction.moved($0) }
        revision += 1
    }

    /// How far from the marked end on its side (or, with none marked, the far edge of what was
    /// seen) a corner may lie: 3 m. A guess, not measured: the homeowner marked the end at the
    /// corner, and AR taps near the walk are off by well under a meter, so a corner farther
    /// away means the marked wall is some other wall.
    public static let maxCornerFromEnd: Float = 3

    /// A corner `turnCorner` would follow, and how far it lies from the marked end on its side
    /// (or the reference `turnCorner` bounds it by), before anything changes. The walk shows it
    /// and asks "Is this the next wall?" before following it (#70, team decision 2026-09-27):
    /// on build 4.1 a surface behind the end post passed the 3 m bound, and the distance is
    /// logged so the bound can be set from real corners.
    public struct CornerProposal: Sendable, Equatable {
        public var corner: WallCorner
        /// Meters from the end (or reference) to the corner, along the chain; never negative.
        public var fromEnd: Float
    }

    /// The corner `turnCorner(side, meeting:outward:source:)` would follow, without following it.
    /// Throws what `turnCorner` would.
    public func proposeCorner(
        _ side: WalkSide, meeting point: SIMD3<Float>, outward: SIMD3<Float>, source: WallLineSource
    ) throws(CornerRefusal) -> CornerProposal {
        var corner = try wall.corner(on: side, meeting: point, outward: outward)
        corner.source = source
        let seenEdge = seenExtent.map { side == .left ? $0.lowerBound : $0.upperBound }
        let reference = (side == .left ? leftEnd : rightEnd) ?? seenEdge ?? 0
        let fromEnd = abs(corner.s - reference)
        guard fromEnd <= Self.maxCornerFromEnd else { throw .implausible(s: corner.s) }
        return CornerProposal(corner: corner, fromEnd: fromEnd)
    }

    /// Follows the wall round a corner on `side`, to the wall the homeowner marked at `point`
    /// facing `outward` (toward the homeowner). The corner is where the two walls' lines meet on
    /// the ground (`WallFrame.corner(on:meeting:outward:)`). The end on that side is cleared, so
    /// the walk goes on along the new wall, and every kept camera is replayed against the new
    /// chain: cells past the corner were measured on the old wall's line. `source` is how the
    /// marked wall's line was found, kept on the corner for the export. Changes nothing when it
    /// throws.
    @discardableResult
    public mutating func turnCorner(
        _ side: WalkSide, meeting point: SIMD3<Float>, outward: SIMD3<Float>, source: WallLineSource
    ) throws(CornerRefusal) -> WallCorner {
        let corner = try proposeCorner(side, meeting: point, outward: outward, source: source).corner
        wall.turn(side, at: corner)
        switch side {
        case .left: leftEnd = nil
        case .right: rightEnd = nil
        }
        limitEnds.remove(side)
        // Past the corner s runs along the new piece, which the surface found in front of the old
        // one says nothing about; the caller measures it again against the new chain.
        setFarSurface([], replay: false)
        replayObservedCameras(shiftingSkippedBy: 0)
        return corner
    }

    /// Rebuilds seen and covered cells by replaying `observedCameras` against the current wall,
    /// keeping skipped and withdrawn cells, moved by `whole` cells.
    private mutating func replayObservedCameras(shiftingSkippedBy whole: Int) {
        var skipped: [SurfaceBand: [Int: Cell]] = [.wall: [:], .ground: [:]]
        for (band, bandCells) in cells {
            for (index, cell) in bandCells where cell.skipped {
                var kept = newCell(band)
                kept.skipped = true
                skipped[band, default: [:]][index + whole] = kept
            }
        }
        cells = skipped
        withdrawnCells = Set(withdrawnCells.map { $0 + whole })
        depthCells = [:]
        depthHidden = [:]
        depthPastSpace = [:]
        prepareRebuildViews()
        for index in captureOrder {
            let position = observedCameras[index].position, depthChecked = observedDepths[index] != nil
            let depthRows = rebuildDepthRowViews(index)
            recordDepth(depthRows, from: position, depthChecked: depthChecked)
            let bands = rebuildBandViews(index)
            recordSightings(bands, from: position, depthChecked: depthChecked)
        }
        replayPastLimits()
        revision += 1
    }

    // MARK: Reading

    /// s extent of cells seen, covered or found hidden, or nil when none are.
    public var seenExtent: ClosedRange<Float>? {
        let seen = cells.values.flatMap { $0.filter { $0.value.isSeen || !$0.value.hiddenRows.isEmpty }.keys }
        guard let low = seen.min(), let high = seen.max() else { return nil }
        return cellRange(low).lowerBound...cellRange(high).upperBound
    }

    /// The s extent kept frames look over: every kept camera's s, `maxDistance` either way, which
    /// covers the cells a frame samples (`candidateIndices`). Nil before any frame is kept.
    /// Unlike `seenExtent`, a rebuild from the same cameras leaves it where it is, so a far
    /// surface measured over it (`FarSurfaceTracker`) can't change with what the rebuild decides.
    public var viewedExtent: ClosedRange<Float>? {
        let along = observedCameras.map { wall.wallPoint($0.position).s }
        guard let low = along.min(), let high = along.max() else { return nil }
        return (low - config.maxDistance)...(high + config.maxDistance)
    }

    /// Range worth drawing: seen cells plus fog ahead, clipped to the marked ends. Before anything
    /// is seen, the fog around the meter.
    public var visibleRange: ClosedRange<Float> {
        let seen = seenExtent ?? 0...0
        var low = seen.lowerBound - config.fogAhead
        var high = seen.upperBound + config.fogAhead
        if let leftEnd { low = max(low, leftEnd) }
        if let rightEnd { high = min(high, rightEnd) }
        return low...max(low, high)
    }

    /// Covered stretches of a band, merged, in meters of s. Only covered cells count: a cell seen
    /// from one position has no parallax behind it.
    public func coveredIntervals(_ band: SurfaceBand) -> [ClosedRange<Float>] {
        let indices = (cells[band] ?? [:]).filter { $0.value.covered && allows($0.key) && !withdrawnCells.contains($0.key) }.keys.sorted()
        var runs: [ClosedRange<Float>] = []
        for index in indices {
            let range = cellRange(index)
            if let last = runs.last, abs(last.upperBound - range.lowerBound) < config.cellWidth * 0.01 {
                runs[runs.count - 1] = last.lowerBound...range.upperBound
            } else {
                runs.append(range)
            }
        }
        if let leftEnd, let first = runs.first { runs[0] = max(first.lowerBound, leftEnd)...first.upperBound }
        if let rightEnd, let last = runs.last { runs[runs.count - 1] = last.lowerBound...min(last.upperBound, rightEnd) }
        return runs
    }

    /// Fraction of cells over `range` in `band` that are covered.
    public func coveredFraction(_ band: SurfaceBand, in range: ClosedRange<Float>) -> Double {
        let all = indices(overlapping: range).filter(allows)
        guard !all.isEmpty else { return 0 }
        return Double(all.filter { level(band, $0) == .covered }.count) / Double(all.count)
    }

    /// Whether looking again from `position` can't finish `band` over `range`, and a step to the
    /// side can: every open row there (a row of the band the walk asks for, in a cell between the
    /// ends that is neither covered nor skipped) has exactly one sighting, taken less than
    /// `coveringBaseline` from `position`. False when nothing there is open, and when some open row
    /// has no sighting yet: the view has to reach it first. Changes nothing.
    public func needsSecondPosition(band: SurfaceBand, range: ClosedRange<Float>, from position: SIMD3<Float>) -> Bool {
        var open = 0
        for index in indices(overlapping: range) where allows(index) {
            guard let cell = cells[band]?[index] else { return false }
            if cell.covered || cell.skipped { continue }
            for row in cell.rows[cell.firstBandRow...cell.lastWalkRow] where row.count < 2 {
                guard row.count == 1, simd_distance(row[0].position, position) < config.coveringBaseline else { return false }
                open += 1
            }
        }
        return open > 0
    }

    /// Total covered cells over both bands.
    public var coveredCount: Int {
        cells.values.reduce(0) { $0 + $1.filter { $0.value.covered && !withdrawnCells.contains($0.key) }.count }
    }

    // MARK: Rebuild view cache

    /// A rebuild (`replayObservedCameras`, `replayPastLimits`) replays every kept frame, and most
    /// of its time goes to working out what each frame shows. That part reads no recorded
    /// sightings, so it is kept per frame and used again while nothing it was worked out from has
    /// changed: the frame's camera and depth, the wall chain with its ground, `heightError`, the
    /// ends and which of them are limits (`RebuildViewBasis`), and the far-surface lookup at every
    /// cell the frame's depth readings consulted (`FarSurfaceReads`). `config` is fixed. Any
    /// change to the basis (a wall update, a corner, an anchor correction, a measured ground, an
    /// end) drops every kept view. The merge, which reads and writes the recorded sightings in
    /// capture order, runs in full every time, so a rebuild records what it would without the
    /// cache. Only a far-surface rebuild can reuse views: every other rebuild changes the basis.
    private var rebuildViews = RebuildViewCache()

    /// Test and measurement seam: false makes every rebuild work out every view again, so a test
    /// can hold a cached map against a full rebuild. Nothing in the app sets it.
    var reusesRebuildViews = true

    /// How many per-frame views rebuilds took from the cache and worked out, for tests and the
    /// probe. A frame has up to three: the bands, the ground depth rows, the ground past limits.
    private(set) var rebuildViewCounts: (reused: Int, computed: Int) = (0, 0)

    private struct RebuildViewBasis: Equatable, Sendable {
        var wall: WallFrame
        var leftEnd: Float?
        var rightEnd: Float?
        var limitEnds: Set<WalkSide>
        var heightError: Float
    }

    private struct RebuildViewCache: Sendable {
        var basis: RebuildViewBasis?
        /// By index into `observedCameras`.
        var frames: [Int: FrameViews] = [:]
    }

    /// The kept views of one frame, with the camera and depth they were worked out from.
    private struct FrameViews: Sendable {
        var camera: CameraFrame
        var depth: DepthImage?
        var bands: KeptViews<[Sighting]>?
        var depthRows: KeptViews<[DepthRowView]>?
        var pastLimits: KeptViews<[PastLimitCellView]>?
    }

    private struct KeptViews<Value: Sendable>: Sendable {
        var value: Value
        var reads: FarSurfaceReads
    }

    /// The far-surface lookup's cells a frame's views consulted, and what each held there (absent
    /// for no surface). The views hold while the lookup holds the same at every one of them.
    private struct FarSurfaceReads: Sendable {
        var cells: Set<Int>
        var values: [Int: Float]

        func agree(with lookup: [Int: Float]) -> Bool {
            cells.allSatisfy { lookup[$0] == values[$0] }
        }
    }

    /// Collects `FarSurfaceReads` while views are worked out (`endsSpace`).
    private final class FarSurfaceReadLog {
        private(set) var cells: Set<Int> = []
        private(set) var values: [Int: Float] = [:]

        func note(_ cell: Int, _ value: Float?) {
            guard cells.insert(cell).inserted else { return }
            values[cell] = value
        }

        var reads: FarSurfaceReads { FarSurfaceReads(cells: cells, values: values) }
    }

    /// Drops every kept view unless the basis they were worked out on is the current one.
    private mutating func prepareRebuildViews() {
        let basis = RebuildViewBasis(wall: wall, leftEnd: leftEnd, rightEnd: rightEnd, limitEnds: limitEnds, heightError: heightError)
        guard !reusesRebuildViews || rebuildViews.basis != basis else { return }
        rebuildViews = RebuildViewCache(basis: reusesRebuildViews ? basis : nil)
    }

    private mutating func rebuildBandViews(_ index: Int) -> [Sighting] {
        if let kept = keptViews(index, \.bands) { return kept }
        let log = reusesRebuildViews ? FarSurfaceReadLog() : nil
        let views = visibleCells(from: observedCameras[index], depth: observedDepths[index], reads: log)
        keep(views, reads: log, at: index, \.bands)
        return views
    }

    private mutating func rebuildDepthRowViews(_ index: Int) -> [DepthRowView] {
        if let kept = keptViews(index, \.depthRows) { return kept }
        let log = reusesRebuildViews ? FarSurfaceReadLog() : nil
        let views = depthRowViews(from: observedCameras[index], depth: observedDepths[index], reads: log)
        keep(views, reads: log, at: index, \.depthRows)
        return views
    }

    private mutating func rebuildPastLimitViews(_ index: Int) -> [PastLimitCellView] {
        if let kept = keptViews(index, \.pastLimits) { return kept }
        let log = reusesRebuildViews ? FarSurfaceReadLog() : nil
        let views = pastLimitViews(from: observedCameras[index], depth: observedDepths[index], reads: log)
        keep(views, reads: log, at: index, \.pastLimits)
        return views
    }

    /// The kept views of the frame at `index`, when they were worked out from its current camera
    /// and depth and the far-surface lookup agrees at every cell they consulted.
    private mutating func keptViews<Value: Sendable>(_ index: Int, _ part: KeyPath<FrameViews, KeptViews<Value>?>) -> Value? {
        guard reusesRebuildViews, let frame = rebuildViews.frames[index],
              frame.camera == observedCameras[index], frame.depth == observedDepths[index],
              let kept = frame[keyPath: part], kept.reads.agree(with: farSurfaceByCell) else { return nil }
        rebuildViewCounts.reused += 1
        return kept.value
    }

    private mutating func keep<Value: Sendable>(
        _ value: Value, reads log: FarSurfaceReadLog?, at index: Int, _ part: WritableKeyPath<FrameViews, KeptViews<Value>?>
    ) {
        rebuildViewCounts.computed += 1
        guard let log else { return }
        let camera = observedCameras[index], depth = observedDepths[index]
        var frame = rebuildViews.frames[index].flatMap { $0.camera == camera && $0.depth == depth ? $0 : nil }
            ?? FrameViews(camera: camera, depth: depth)
        frame[keyPath: part] = KeptViews(value: value, reads: log.reads)
        rebuildViews.frames[index] = frame
    }

    /// Test seam: the stored fields in which this map's state differs from `other`'s, leaving out
    /// the rebuild view cache, its counts and `reusesRebuildViews`. Empty when every reader of
    /// either map, now or after any later change, gets the same answers.
    func stateDifferences(from other: CoverageMap) -> [String] {
        var differing: [String] = []
        func compare<T: Equatable>(_ name: String, _ a: T, _ b: T) {
            if a != b { differing.append(name) }
        }
        compare("wall", wall, other.wall)
        compare("config", config, other.config)
        compare("leftEnd", leftEnd, other.leftEnd)
        compare("rightEnd", rightEnd, other.rightEnd)
        compare("limitEnds", limitEnds, other.limitEnds)
        compare("revision", revision, other.revision)
        compare("heightError", heightError, other.heightError)
        compare("cells", cells, other.cells)
        compare("depthCells", depthCells, other.depthCells)
        compare("pastLimitCells", pastLimitCells, other.pastLimitCells)
        compare("pendingShift", pendingShift, other.pendingShift)
        compare("observedCameras", observedCameras, other.observedCameras)
        compare("observedDepths", observedDepths, other.observedDepths)
        compare("overheadCameras", overheadCameras, other.overheadCameras)
        compare("observedTimes", observedTimes, other.observedTimes)
        compare("observedSegments", observedSegments, other.observedSegments)
        compare("pathSegment", pathSegment, other.pathSegment)
        compare("depthHidden", depthHidden, other.depthHidden)
        compare("depthPastSpace", depthPastSpace, other.depthPastSpace)
        compare("pastLimitHidden", pastLimitHidden, other.pastLimitHidden)
        compare("farSurfaceByCell", farSurfaceByCell, other.farSurfaceByCell)
        compare("farSurface", farSurface, other.farSurface)
        compare("withdrawnCells", withdrawnCells, other.withdrawnCells)
        return differing
    }
}

/// A stretch of wall (s, meters) and how far the view of it reached, meters: out from the wall
/// for ground and facing, up from the ground for overhead. It is scene.json's `coverage.observed`
/// entry before conversion to feet (`span_ft`, `out_ft`), and like `out_ft` it is a distance the
/// capture is sure of. The export rounds it down (`SceneExport.feetDown`).
public struct ObservedSpan: Sendable, Equatable {
    public var span: ClosedRange<Float>
    public var out: Float

    public init(span: ClosedRange<Float>, out: Float) {
        self.span = span
        self.out = out
    }

    /// Joins neighbours (in s order) that touch within `touching` and reach exactly as far.
    static func merge(_ sorted: [ObservedSpan], touching: Float) -> [ObservedSpan] {
        var runs: [ObservedSpan] = []
        for item in sorted {
            if let last = runs.last, last.out == item.out, abs(last.span.upperBound - item.span.lowerBound) < touching {
                runs[runs.count - 1].span = last.span.lowerBound...item.span.upperBound
            } else {
                runs.append(item)
            }
        }
        return runs
    }
}

public enum WalkSide: String, Sendable, CaseIterable {
    case left
    case right

    /// -1 for left, +1 for right: the sign of s on this side of the meter.
    public var sign: Float { self == .left ? -1 : 1 }
    public var opposite: WalkSide { self == .left ? .right : .left }
}
