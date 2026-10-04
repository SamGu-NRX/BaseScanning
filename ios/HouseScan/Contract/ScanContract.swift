import CoreGraphics
import Foundation
import Observation
import simd
import SwiftUI

// The boundary between the capture engine (Runtime/) and the screens (UI/).
//
// The engine owns every decision: what phase the scan is in, what the camera has covered, when a
// keyframe is kept, what to ask for next. It writes the results into `ScanViewState`. The screens
// read that state, turn the semantic values into words, pictures and motion, and send the
// homeowner's intents back through `ScanActions`. Nothing in UI/ computes geometry or coverage,
// and nothing in Runtime/ holds user-facing copy.
//
// World coordinates are ARKit's `.gravity` frame in meters: +y is up. Wall coordinates are
// `s` (meters along the wall from the meter, negative to the left when facing the wall),
// `height` (meters above the ground at the wall) and `out` (meters from the wall toward the
// homeowner). Feet appear only in copy and in the exported scene.json.

// MARK: - Phase

/// Screens of the flow. The raw value is the state name the engine logs as `STATE=<name>`
/// (OSLog subsystem `dev.housescanning.housescan`, category `state`; contract C4).
enum ScanPhase: String, Sendable, CaseIterable {
    case onboarding
    case findMeter
    case meterCloseUp
    case wallWalk
    case markFeatures
    case gapRequest
    case uploading
    /// Before the result: is anything standing where the answer's spot would go (`SpotCheck`)?
    case spotConfirm
    case result
    case resultAR
    case unsupported
}

// MARK: - Camera

enum TrackingQuality: Equatable, Sendable {
    case notAvailable
    case normal
    case limited(LimitedReason)

    enum LimitedReason: Equatable, Sendable {
        case initializing
        case excessiveMotion
        case insufficientFeatures
        case relocalizing
        case unknown
    }

    /// The phone doesn't know where it is in the world frame the scan was measured in: ARKit is
    /// relocalizing, or not tracking at all. Anything tapped into the scene has to wait. The
    /// other limited states (moving fast, a plain surface) still keep the frame.
    var hasLostItsPlace: Bool {
        switch self {
        case .notAvailable, .limited(.relocalizing): true
        case .normal, .limited: false
        }
    }
}

/// What the screen shows behind the overlays.
enum CameraFeed: Equatable {
    /// Nothing yet, for example before camera permission.
    case none
    /// The live AR camera. The view comes from `ScanActions.liveCameraView()`.
    case live
    /// A replayed keyframe: the unrotated landscape sensor image. Draw it rotated 90° clockwise
    /// with aspect fill, exactly the way `CameraProjection.viewPoint` assumes.
    case still(CGImage)
}

/// Geometry of the frame currently on screen, enough to draw world points over the feed.
///
/// The image is the landscape sensor image; the screen shows it rotated 90° clockwise, scaled to
/// fill the portrait view, centered and cropped. That is how iOS shows the back camera held
/// upright, and how the replay draws a still.
struct CameraProjection: Equatable, Sendable {
    /// Camera-to-world transform. Camera space: +x right and +y up in the sensor image, looking along -z.
    var cameraToWorld: simd_float4x4
    /// fx, fy, cx, cy in pixels of the sensor image.
    var intrinsics: SIMD4<Float>
    /// Sensor image width and height in pixels (landscape, width > height).
    var imageSize: SIMD2<Float>

    var cameraPosition: SIMD3<Float> {
        SIMD3(cameraToWorld.columns.3.x, cameraToWorld.columns.3.y, cameraToWorld.columns.3.z)
    }

    /// Where the camera looks, in world coordinates.
    var forward: SIMD3<Float> {
        -SIMD3(cameraToWorld.columns.2.x, cameraToWorld.columns.2.y, cameraToWorld.columns.2.z)
    }

    /// The point in camera space: +x right, +y up in the sensor image, -z ahead.
    func cameraSpace(_ world: SIMD3<Float>) -> SIMD3<Float> {
        let local = cameraToWorld.inverse * SIMD4(world, 1)
        return SIMD3(local.x, local.y, local.z)
    }

    /// The sensor-image pixel of a world point, or nil when it is behind the camera (or within 5 cm
    /// of its plane). The pixel can fall outside the image.
    func imagePixel(for world: SIMD3<Float>) -> SIMD2<Float>? {
        let p = cameraSpace(world)
        guard p.z < -0.05 else { return nil }
        let depth = -p.z
        return SIMD2(intrinsics.z + intrinsics.x * p.x / depth, intrinsics.w - intrinsics.y * p.y / depth)
    }

    /// View points per sensor pixel for a portrait view of `size`.
    func scale(in size: CGSize) -> CGFloat {
        max(size.width / CGFloat(imageSize.y), size.height / CGFloat(imageSize.x))
    }

    /// The view point of a world point in a portrait view of `size`, or nil when it is behind the
    /// camera. The point can fall outside the view.
    func viewPoint(for world: SIMD3<Float>, in size: CGSize) -> CGPoint? {
        guard let pixel = imagePixel(for: world) else { return nil }
        return viewPoint(forImagePixel: pixel, in: size)
    }

    func viewPoint(forImagePixel pixel: SIMD2<Float>, in size: CGSize) -> CGPoint {
        let s = scale(in: size)
        let offsetX = (size.width - CGFloat(imageSize.y) * s) / 2
        let offsetY = (size.height - CGFloat(imageSize.x) * s) / 2
        return CGPoint(x: offsetX + (CGFloat(imageSize.y) - CGFloat(pixel.y)) * s, y: offsetY + CGFloat(pixel.x) * s)
    }

    func imagePixel(forViewPoint point: CGPoint, in size: CGSize) -> SIMD2<Float> {
        let s = scale(in: size)
        let offsetX = (size.width - CGFloat(imageSize.y) * s) / 2
        let offsetY = (size.height - CGFloat(imageSize.x) * s) / 2
        let rotatedX = (point.x - offsetX) / s
        let rotatedY = (point.y - offsetY) / s
        return SIMD2(Float(rotatedY), imageSize.y - Float(rotatedX))
    }
}

// MARK: - Wall and coverage

/// The wall line the scan measures, anchored on the meter. The engine refreshes `meter` from the
/// meter's ARAnchor every frame, so overlays drawn from it follow ARKit's corrections.
///
/// Where the walk followed a corner the wall goes on as another straight piece
/// (`cornerSegments`), and s runs on continuously round the corner.
struct WallGeometry: Equatable, Sendable {
    var meter: SIMD3<Float>
    /// Unit, horizontal. +s runs to the right of the meter for someone facing the wall. This and
    /// `outward` are the meter's piece of wall.
    var along: SIMD3<Float>
    /// Unit, horizontal, from the wall toward the homeowner.
    var outward: SIMD3<Float>
    /// World y of the ground at the foot of the wall.
    var groundY: Float
    /// Marked wall ends in meters of s, once the homeowner has marked them.
    var leftEnd: Float?
    var rightEnd: Float?
    /// The pieces of wall past each corner the walk followed; empty for a straight wall.
    var cornerSegments: [Segment] = []

    /// A straight piece of wall past a corner.
    struct Segment: Equatable, Sendable {
        /// The stretch of s it covers: infinite toward the open end, the corner's s toward the meter.
        var span: ClosedRange<Float>
        var along: SIMD3<Float>
        var outward: SIMD3<Float>
        /// Where its line has s = `anchorS` on the ground, as an offset from the meter's foot.
        var anchor: SIMD3<Float>
        var anchorS: Float
    }

    func world(s: Float, height: Float, out: Float = 0) -> SIMD3<Float> {
        let foot = SIMD3(meter.x, groundY, meter.z)
        guard let piece = cornerSegments.first(where: { $0.span.contains(s) }) else {
            return foot + along * s + outward * out + SIMD3(0, height, 0)
        }
        return foot + piece.anchor + piece.along * (s - piece.anchorS) + piece.outward * out + SIMD3(0, height, 0)
    }

    /// The outward direction of the piece of wall at `s`.
    func outward(atS s: Float) -> SIMD3<Float> {
        cornerSegments.first { $0.span.contains(s) }?.outward ?? outward
    }

    /// The along direction of the piece of wall at `s`.
    func along(atS s: Float) -> SIMD3<Float> {
        cornerSegments.first { $0.span.contains(s) }?.along ?? along
    }

    /// The s of the wall point nearest a world point in plan.
    func s(nearest world: SIMD3<Float>) -> Float {
        let d = world - SIMD3(meter.x, groundY, meter.z)
        func planDistance(_ offset: SIMD3<Float>) -> Float { simd_length(SIMD2(d.x - offset.x, d.z - offset.z)) }
        let meterLow = cornerSegments.map(\.span.upperBound).filter { $0 <= 0 }.max() ?? -.infinity
        let meterSpan = meterLow...(cornerSegments.map(\.span.lowerBound).filter { $0 >= 0 }.min() ?? .infinity)
        var best = min(max(simd_dot(d, along), meterSpan.lowerBound), meterSpan.upperBound)
        var bestDistance = planDistance(along * best)
        for piece in cornerSegments {
            let s = min(max(piece.anchorS + simd_dot(d - piece.anchor, piece.along), piece.span.lowerBound), piece.span.upperBound)
            let distance = planDistance(piece.anchor + piece.along * (s - piece.anchorS))
            if distance < bestDistance {
                best = s
                bestDistance = distance
            }
        }
        return best
    }

    /// Height of the meter above the ground.
    var meterHeight: Float { meter.y - groundY }
}

enum CoverageBand: String, Sendable, CaseIterable {
    /// The wall face from the ground up to `CoverageStrip.wallBandHeight`.
    case wall
    /// The ground from the foot of the wall out to `CoverageStrip.groundBandDepth`.
    case ground
}

enum CellState: UInt8, Equatable, Sendable {
    /// No kept keyframe has seen this cell.
    case unseen
    /// Seen, but not yet from two positions.
    case seen
    /// Seen from at least two positions with normal tracking. Evidence exists; this is not a pass.
    case covered
    /// The homeowner said they can't get there. Recorded for installer review; never evidence,
    /// and drawn distinctly from both fog and covered.
    case skipped
    /// LiDAR phones only: the camera looked here, but depth showed something nearer in the way
    /// (a bush, a bin), so the wall or ground behind it is unseen. Not evidence; drawn distinctly.
    case hidden
}

/// Coverage of the unrolled wall, in fixed-width cells along s.
struct CoverageStrip: Equatable, Sendable {
    var cellWidth: Float
    /// s of the left edge of cell 0.
    var firstCellS: Float
    var wall: [CellState]
    var ground: [CellState]
    var wallBandHeight: Float
    var groundBandDepth: Float
    /// The s range worth drawing: what has been seen plus a margin of fog ahead, clipped to the
    /// marked wall ends. Cells outside it are neither fog nor evidence; don't draw them.
    var visibleRange: ClosedRange<Float>
    /// Increments on every change, so views can diff and animate cells that just cleared.
    var revision: Int

    static let empty = CoverageStrip(
        cellWidth: 0.1524, firstCellS: 0, wall: [], ground: [],
        wallBandHeight: 2.4, groundBandDepth: 1.2, visibleRange: 0...0, revision: 0
    )

    func cellRange(_ index: Int) -> ClosedRange<Float> {
        let start = firstCellS + Float(index) * cellWidth
        return start...(start + cellWidth)
    }

    func cells(_ band: CoverageBand) -> [CellState] {
        band == .wall ? wall : ground
    }

    /// Fraction of cells in `range` that are covered, per band.
    func coveredFraction(_ band: CoverageBand, in range: ClosedRange<Float>) -> Double {
        let states = cells(band).indices.filter { range.overlaps(cellRange($0)) }.map { cells(band)[$0] }
        guard !states.isEmpty else { return 0 }
        return Double(states.filter { $0 == .covered }.count) / Double(states.count)
    }
}

/// LiDAR phones: dots on the surfaces depth has measured, drawn over the camera by the live fog.
/// Guidance, never evidence: `coverage` alone decides what the scan has seen, and the fog lifts
/// only where it says so. Built from the same kept keyframes as coverage (`LiveDotsFeed`).
struct LiveDots: Sendable {
    struct Dot: Sendable, Equatable {
        /// Stable while the dot exists, so the renderer can tell a birth from a survivor.
        var id: UInt64
        var position: SIMD3<Float>
        /// On a crease or a silhouette; drawn larger, with a stronger glow.
        var isEdge: Bool
        /// On something standing in front of the wall (a bin, a bush): drawn violet, the hidden
        /// state's colour.
        var onOccluder: Bool
        /// 0.45 seen from one direction, up to 0.9 from four.
        var opacity: Float
    }

    /// At most `SurfaceDotConfig.maxDots` (5,000), those within 6 m of the latest keyframe.
    var dots: [Dot] = []
    /// Increments on every change, so the renderer rebuilds its buffer only then.
    var revision = 0

    static let empty = LiveDots()
}

// MARK: - Guidance

enum WallSide: String, Equatable, Sendable {
    case left
    case right
}

/// What the homeowner should do next, as a meaning. UI/ owns the words.
enum GuidanceStep: Equatable, Sendable {
    /// Point the camera at the electric meter and mark it.
    case findMeter
    /// Marking needs a vertical surface under the reticle; step closer or aim at the wall.
    case aimAtWallForMeter
    /// Hold the meter in the ring until the close-up is taken.
    case holdOnMeter
    /// Walk along the wall toward `side`. `remaining` is meters of s still unseen on that side,
    /// nil while the end is unknown. "Can't get there" and "Wall ends here" end the wall on that
    /// side where `ScanViewState.endPreview` shows.
    case walk(side: WallSide, remaining: Float?)
    /// The walk has gone about 20 ft on this side (`GuidanceConfig.reach`); ask where the wall
    /// ends, marked at the reticle (`ScanActions.markWallEnd`).
    case markEnd(side: WallSide)
    /// Tilt down: the ground at the foot of the wall around `s` is missing. The walk asks for the
    /// ground in front of the meter (s = 0) first.
    case aimAtGround(s: Float)
    /// Tilt up or step back: the wall face around `s` is missing.
    case aimAtWall(s: Float)
    /// Too close to the wall to see enough of it.
    case stepBack
    /// Both ends marked and coverage is complete enough; review marked features.
    case walkComplete
    /// Point the phone up at the wall above this stretch (meters of s), to show what is overhead
    /// where the battery would stand.
    case tiltUp(span: ClosedRange<Float>)
    /// The wall turns a corner on `side`: walk round it, aim at the next wall and mark it
    /// (`ScanActions.markNextWall`). `refusal` is set when the last mark was refused.
    case markNextWall(side: WallSide, refusal: NextWallRefusal?)
    /// Show a specific gap (see `ScanViewState.gap`).
    case gap
    /// Something stands in front of the wall or ground around `s` (LiDAR saw it): look at that
    /// part from another angle or step around the obstruction.
    case seeBehind(s: Float)
}

/// Where the aim target of the step on screen lies from the view, in the terms of a portrait
/// screen, so the card can agree with the ring and the edge chevron (#81).
enum AimDirection: Equatable, Sendable {
    case onScreen
    case above
    case below
    case left
    case right
    case behind
}

/// What the card of an aim step (`GuidanceStep.aimAtGround`, `.aimAtWall`) should say beside the
/// step itself. UI/ owns the words.
struct GuidanceHint: Equatable, Sendable {
    /// Where the target lies from the view now; nil when unknown.
    var aim: AimDirection?
    /// Every part of the stretch still open has been seen once, from about here: looking again
    /// adds nothing, a step to the side does (#77).
    var needsSecondPosition = false
    /// Too close to the wall to see the band: step back as well, without leaving the step (#77).
    var stepBack = false
}

/// A problem to fix while it lasts. Tracking problems and `pastWallEnd` override guidance; the
/// capture gate's (`slowDown`, `turnSlowly`, `holdSteady`, `tooDark`, `tooDarkToMeasure`) and
/// `needsTexture` ride along with it on the walk. UI/ owns the words.
enum Coaching: Equatable, Sendable {
    case initializing
    case slowDown
    case needsTexture
    case tooDark
    /// Frames have been mostly too dark for a long while: waiting won't help, daylight will
    /// (`CoachingDebouncer.GateProblem.persistentlyDark`).
    case tooDarkToMeasure
    case holdSteady
    /// Turning or tilting the phone faster than the capture gate keeps photos at, as opposed to
    /// walking too fast (#26).
    case turnSlowly
    case relocalizing
    case trackingLost
    /// The phone stands past an end it can't see back from, so the walk keeps no photos: nothing
    /// in them would count (`CoverageMap.unexploredEndPassed`).
    case pastWallEnd
}

/// Where a wall end would land if the homeowner ended the wall now, while ending it is on offer.
/// The strip draws it before the tap, so an end never lands somewhere the screen didn't show.
struct EndPreview: Equatable, Sendable {
    var side: WallSide
    /// Meters of s. During the walk, where "Wall ends here" puts it, and "Can't get there" too
    /// while the walk asks to walk that way: the phone's place along the wall, kept to the stretch
    /// walked on that side (`WalkedEnd`). While the walk asks for the end (`GuidanceStep.markEnd`),
    /// where the reticle meets the wall.
    var s: Float
    /// True when `s` comes from the reticle (`ScanActions.markWallEnd`), false when from the
    /// phone's place (`ScanActions.endWallHere`).
    var atReticle: Bool
    /// Meters of walked path past `s` on this side, set when the homeowner walked back toward the
    /// meter by at least a keyframe's spacing: what the walk saw from there is left out if the wall
    /// ends at `s`. Only while the walk asks to walk this side or mark its end, and not with the
    /// phone far out from the wall (`WalkedEnd.leavesOut`, issue #66); "Wall ends here" pressed at
    /// other times says it on the end question (`ScanViewState.endQuestionLeavesOut`).
    var leavesOutWalked: Float?
    /// True when `leavesOutWalked` counts cells the strip showed past the end rather than the
    /// walk (`WalkedEnd.leftOutIsSeen`), so the words say "you saw".
    var leavesOutSeen = false
}

// MARK: - Captures

struct CaptureEvent: Identifiable, Equatable {
    enum Kind: Equatable, Sendable {
        case walk
        case closeUp
        case gap
    }

    let id: Int
    let kind: Kind
    /// A small, upright thumbnail for the acknowledgment, when cheap to produce.
    let thumbnail: CGImage?
}

enum CloseUpState: Equatable {
    /// Aiming at the meter. `hold` runs 0...1 while every gate passes; the shutter fires at 1.
    case aiming(hold: Double, problem: CloseUpProblem?)
    case captured(CGImage?)
    /// Two tries failed, or the homeowner chose "Can't get a clear shot". Goes to review.
    case skipped
}

enum CloseUpProblem: Equatable, Sendable {
    case blurry
    case tooDark
    case tooBright
    case meterNotCentered
    case tooFar
    case tracking
    /// The best reading's characters are too small in the photo: move closer.
    case numberTooSmall
    /// No number could be read at all: retake.
    case noNumber
    /// The photo couldn't be written to the phone, or read back once written. Nothing about the
    /// shot was wrong, so the words don't blame the homeowner's hands (B-20).
    case photoNotSaved
}

/// One reading of the meter number from the close-up, for the homeowner to confirm.
struct MeterNumberCandidate: Identifiable, Equatable, Sendable {
    let id: Int
    var text: String
    /// A barcode on the meter carries the same number.
    var barcodeConfirmed: Bool
}

/// The meter number after the close-up. The homeowner confirms it by tapping; nothing is
/// filled in automatically.
enum MeterNumberState: Equatable, Sendable {
    case reading
    /// The best candidates, barcode-confirmed first, at most three.
    case choose([MeterNumberCandidate])
    case confirmed(String)
    /// The homeowner skipped the close-up; the number goes to review.
    case skipped
}

// MARK: - Features

enum FeatureKind: String, Equatable, Sendable, CaseIterable, Identifiable {
    case gasMeter = "gas_meter"
    case door
    case window
    case acUnit = "ac"
    case driveway = "drive"
    case fence

    var id: String { rawValue }

    /// How many taps a mark takes: two diagonal corners for a door or window, two points along
    /// the near edge of a driveway or the foot of a fence, one point for a gas meter or AC unit.
    /// scene.json (server/schemas/scene.schema.json) stores a driveway as a ground polygon and a
    /// fence as a facing gap over a span, so both need a line, not a point.
    var tapCount: Int {
        switch self {
        case .door, .window, .driveway, .fence: 2
        case .gasMeter, .acUnit: 1
        }
    }
}

struct MarkedFeature: Identifiable, Equatable, Sendable {
    let id: UUID
    var kind: FeatureKind
    /// Along-wall extent in meters of s.
    var span: ClosedRange<Float>
    var bottom: Float?
    var top: Float?
    /// Distance out from the wall, for ground items and fences.
    var out: Float?
    /// World points of the taps, for drawing pins.
    var points: [SIMD3<Float>]
    /// Windows only: the homeowner's answer, nil until asked and after "Not sure".
    var opens: Bool?
    /// Windows only: the answer was "Not sure". `opens` stays nil, so the window is sent with
    /// `operable` left out, which scene.schema.json reads as unknown; no new value is sent.
    var opensNotSure = false

    /// A window whose open/shut question has no answer yet ("Not sure" is an answer).
    var awaitsOpensAnswer: Bool { kind == .window && opens == nil && !opensNotSure }
}

/// Marking in progress: which kind, and which tap of `kind.tapCount` comes next (0-based).
struct MarkingState: Equatable, Sendable {
    var kind: FeatureKind
    var step: Int
    /// Set when the last tap was refused, for example no surface under it.
    var refusal: MarkRefusal?
}

/// Why a mark of the next wall round a corner was refused.
/// The ground types a scene can record (scene.schema.json `ground[].type`), in the order the
/// question lists them.
enum GroundType: String, CaseIterable, Identifiable, Sendable {
    case lawn, mulch, gravel, concrete, drive, deck
    var id: String { rawValue }
}

/// The homeowner's answer to what the ground along the wall is, asked once after the walk. The
/// camera can't tell mulch from soil, so this is the only source of the ground's type.
enum GroundAnswer: Equatable, Sendable {
    /// Exported as patches of this type over the ground the coverage saw, and nowhere else.
    case type(GroundType)
    /// "Not sure": no patch is sent and the server reports the surface as unknown.
    case notSure
}

/// The next wall round a corner, marked and waiting for the homeowner to confirm it
/// (`ScanViewState.nextWallConfirm`).
struct NextWallConfirm: Equatable, Sendable {
    var side: WallSide
    /// Meters from the end marked on that side to where the marked wall meets this one
    /// (`CoverageMap.CornerProposal.fromEnd`).
    var fromEnd: Float
}

enum NextWallRefusal: Equatable, Sendable {
    /// No wall under the circle.
    case noSurface
    case trackingNotReady
    /// The marked wall runs nearly the same way as this one: probably the same wall.
    case sameWall
    /// The marked wall doesn't meet this one anywhere near where it was marked as ending.
    case notAtCorner
}

/// Why "Wall ends here" at the circle marked no end (`ScanViewState.endMarkRefusal`). Before,
/// the button did nothing at all in each case, and nothing said why (B-06).
enum EndMarkRefusal: Error, Equatable, Sendable {
    /// The circle isn't on the wall: aimed at the ground, so the ray meets the wall's plane under
    /// the floor, or past the distance a camera's view counts for (`CoverageConfig.maxDistance`)
    /// far down a long wall. Aimed above the wall is kept (`EndAim`).
    case noWall
    /// The circle is on the wall on this side of the meter, not the side the card asks about.
    case otherSide(WallSide)
    /// Tracking isn't normal, as for a feature mark (`MarkRefusal.trackingNotReady`).
    case trackingNotReady
    /// During a past_end request: an end there would leave less wall than the walk's minimum
    /// (`CoverageMap.endWouldLeaveTooLittle`, `WallFrame.minWallLength`), which the app can't use.
    /// Decided in `aimedEnd`, so the preview and the button agree.
    case tooLittleWall
}

enum MarkRefusal: Equatable, Sendable {
    case noSurface
    case wrongSide
    case tooFarFromWall
    case trackingNotReady
}

// MARK: - Gap loop

/// One targeted request for missing evidence, from the phone's planner or the server's result.
struct GapRequest: Identifiable, Equatable, Sendable {
    enum Origin: Equatable, Sendable {
        case phone
        case server
    }

    enum Reason: Equatable, Sendable {
        /// The ground under a possible battery spot has not been seen from two positions.
        case groundNearCandidate
        /// The wall face above a possible spot is missing.
        case wallAboveCandidate
        /// The server listed evidence it needs; `detail` is its plain-language text.
        case server(detail: String)
        /// Show the ground out to `out` meters from the wall.
        case groundOut(out: Float)
        /// Walk this stretch at least `out` meters from the wall: a walked path shows the space
        /// in front of the wall is clear out to it.
        case walkOut(out: Float)
        /// Tilt up here to show what is overhead.
        case overhead
    }

    /// A walk-out request's reading where the phone is, meters: how far out from the wall it is,
    /// and how far out the walk must pass there to count (the request's clearance plus the wall's
    /// position error). On build 7.1 the card named only the clearance (#164).
    struct WalkOutReading: Equatable, Sendable {
        var out: Float
        var needed: Float
    }

    /// Where the space in front of the wall visibly ends short of a walk-out request's line,
    /// meters out from the wall, and how far out that line lies there (#164).
    struct SpaceEnds: Equatable, Sendable {
        var at: Float
        var needed: Float
    }

    let id: Int
    var origin: Origin
    var reason: Reason
    var band: CoverageBand
    var span: ClosedRange<Float>
    /// 0...1 of the requested cells covered so far.
    var progress: Double
    var isSatisfied: Bool
    /// A walk-out request's reading from the phone now; nil for other requests and without a
    /// camera. It changes as the phone moves, so it is not part of the request the card's reply
    /// answers (`ScanCopy.gapTask`).
    var walkOut: WalkOutReading? = nil
    /// Set while a walk-out request's line lies past where the space ends
    /// (`GapPlanner.walkOutBlock`): no walk can meet it, and the card says so.
    var spaceEnds: SpaceEnds? = nil
    /// A server past_end request's side: the walk stopped there, and the request asks to walk on
    /// past it. The screen also offers "Wall ends here" for that end, with its question, as the
    /// walk does (B-12). Nil for every other request.
    var pastEndSide: WallSide? = nil
}

// MARK: - Upload and result

enum UploadState: Equatable, Sendable {
    case idle
    case packaging
    case uploading(fraction: Double)
    case analyzing
    /// A network failure or a server error (5xx): sending again can work. `offline` means no
    /// connection at all.
    case failed(message: String, offline: Bool)
    /// The server refused the scan (4xx) or the scan couldn't be packaged. Sending again would send
    /// the same thing, so the way on is back to the review or start over, never "Try again".
    case rejected(message: String)
    /// The server answered, but House Scan couldn't use the answer: it didn't decode, or it didn't
    /// name the scene sent. It isn't shown. The scan isn't at fault, so the way on is "Try again",
    /// with sharing the scan or starting over for when asking again doesn't help. `attempts` counts
    /// such answers since the scan was sent from the review or a gap, at least 1.
    case unusableAnswer(attempts: Int)
    case done
}

enum CheckOutcome: String, Equatable, Sendable {
    case pass
    case fail
    case unsure
}

/// Which side of a rule's limit passes: a clearance the spot must keep (at least) or a length it
/// must stay within (at most).
enum RuleComparison: Equatable, Sendable {
    case atLeast
    case atMost
}

struct CheckRow: Identifiable, Equatable, Sendable {
    let id: String
    var title: String
    var outcome: CheckOutcome
    /// Plain-language reason from the server.
    var reason: String
    /// For an unsure check: true when a person has to judge it (a measurement inside its error
    /// band, a fact the camera can't establish, or a policy that sends it to review), false when
    /// more photos would settle it.
    var needsPerson: Bool = false
    /// The deciding measurement, the rule's limit and the measurement's error, in meters, when the
    /// server gave them. A borderline result shows all three ("3 ft 2 in from the gas meter; the
    /// rule is at least 3 ft and our measurement can be off by about 4 in").
    var measured: Float? = nil
    var threshold: Float? = nil
    var plusMinus: Float? = nil
    /// Whether `threshold` is a minimum or a maximum, when the server said which.
    var comparison: RuleComparison? = nil
    /// A stricter line inside `threshold`, meters, when the server sent one (`review_threshold_ft`,
    /// on the cable run): a measurement that doesn't clear it needs review even within the limit.
    /// Nil when the answer has none.
    var reviewThreshold: Float? = nil
    /// True when `reviewThreshold` explains this unsure check, so the card names it
    /// (`PlacementCheck.reviewBandApplies`, decided in the server's feet before conversion).
    var reviewBandApplies = false
    /// The `MissingEvidence.id` of the first view that would settle this check, when the server
    /// named one.
    var settledBy: String? = nil
}

struct MissingEvidence: Identifiable, Equatable, Sendable {
    let id: String
    var text: String
    /// True when another view can resolve it now; false means it goes to installer review.
    var capturable: Bool
    /// The `CheckRow.id`s this view settles, as the server listed them.
    var checkIDs: [String] = []
}

struct BatterySpot: Equatable, Sendable {
    /// Along-wall extent of the footprint, meters of s.
    var span: ClosedRange<Float>
    /// Footprint depth out from the wall and unit height, meters (from the server's rules).
    var depth: Float
    var height: Float
    /// Gap between the wall and the back of the unit, meters.
    var offsetFromWall: Float
}

struct ClearanceZone: Identifiable, Equatable, Sendable {
    let id: String
    var label: String
    var outcome: CheckOutcome
    /// Along-wall extent and depth out from the wall, meters.
    var span: ClosedRange<Float>
    var depth: Float
}

/// An unexplored end of the walk: its side and where the scan stopped, meters of s.
struct UnseenEnd: Equatable, Sendable {
    var side: WallSide
    var s: Float
}

struct ResultPresentation: Equatable, Sendable {
    enum Decision: String, Equatable, Sendable {
        case pass
        case manualReview = "manual_review"
        case reject
    }

    var decision: Decision
    /// The server's one-sentence summary, without `rulesNotice`.
    var summary: String = ""
    /// False while the server's rules hold placeholder values: every would-be pass or reject is
    /// then manual_review, and the screen should say the rules aren't final.
    var policyApproved: Bool = true
    /// Whose rules decided, from the server ("Demo rules: ... not Base's."), to show with the
    /// answer. Nil when the rules need no such label.
    var rulesNotice: String? = nil
    /// The first eight characters of the rules' SHA-256 (`policy.rules_sha256`), so a reviewer
    /// can tell which rules answered.
    var rulesHash: String? = nil
    var spot: BatterySpot?
    /// When there is no spot: the spot the server found closest to passing.
    var nearestSpot: BatterySpot? = nil
    /// The `CheckRow.id` of the first check `nearestSpot` fails. Without a spot, `checks` are the
    /// checks at `nearestSpot`.
    var nearestFailingCheck: String? = nil
    /// Cable route as (s, height) points along the wall, meters, from the meter to the spot.
    var cableRoute: [SIMD2<Float>]
    var cableLength: Float?
    var checks: [CheckRow]
    var clearances: [ClearanceZone]
    var missing: [MissingEvidence]
    /// Where the scan stopped on a side it didn't finish, nearer the meter than the spot, so a
    /// closer spot may lie past it (`PlacementResult.closerUnseenEnd`).
    var unseenEnd: UnseenEnd? = nil
    /// True when neither side of the meter was walked: "Can't get there" ended both before the
    /// homeowner walked either (`WalkRefusals`, #76). The wall line then comes from the meter tap
    /// alone, so the screen says the wall couldn't be measured and shows no spot, no route and no
    /// "See it on your wall" (`withWallNotMeasured`).
    var wallNotMeasured = false
    /// True when no server answered and the result is the offline sample used by tests and
    /// demos. The UI must say so on screen.
    var isSample: Bool
    /// The answer's `policy.rules_sha256`: which rules judged the scan, for matching a screenshot
    /// to the scan stamp (`ScanStamp`).
    var rulesSHA256: String? = nil
}

// MARK: - Spot check

/// The homeowner's answer to the spot check.
enum SpotCheckAnswer: Equatable, Sendable {
    /// Nothing stands in the area: the result is shown.
    case clear
    /// Something stands there: the scan stops claiming that area and is checked again.
    case somethingThere
    /// The homeowner can't see or reach the area to say: it was not observed, which is neither
    /// an obstruction nor clear. The scan stops claiming that area and is checked again, and the
    /// result says nobody checked it.
    case cannotCheck
}

/// The one question asked before an answer's spot is shown as the result: is anything standing
/// in front of the wall, or on the ground, in the area around the spot? A photo can claim wall
/// and ground behind a bush, and a walked path can pass over something low, so the scan's claims
/// there stand only once the homeowner says the area is clear (HouseScanKit `CoverageMap`,
/// "Bounded exceptions"). Meters of s along the wall and out from it, like `BatterySpot`.
struct SpotCheck: Equatable {
    /// Counts the checks of a scan.
    let id: Int
    /// The spot's footprint along the wall and out from it.
    var spot: ClosedRange<Float>
    var spotOut: ClosedRange<Float>
    var spotHeight: Float
    /// The whole area asked about: the footprint and the clearance zone around it.
    var area: ClosedRange<Float>
    var areaDepth: Float
    /// The kept photo that shows the area best; nil when none does, and the question is asked
    /// about the place itself.
    var photo: Photo?
    /// Nil until answered. The answer stays up a moment before the flow moves on.
    var answer: SpotCheckAnswer?
    /// The spot is the bundled sample's, not a server's (`ResultPresentation.isSample`).
    var isSample: Bool

    struct Photo: Equatable {
        /// The unrotated landscape sensor image. Draw it rotated 90° clockwise, as `CameraFeed.still`.
        var image: CGImage
        /// Where it was taken, for drawing the area over it.
        var projection: CameraProjection
    }
}

// MARK: - State and intents

/// Everything the screens read. The engine is the only writer.
@MainActor
@Observable
final class ScanViewState {
    var phase: ScanPhase = .onboarding
    var feed: CameraFeed = .none
    var projection: CameraProjection?
    var tracking: TrackingQuality = .notAvailable
    var coaching: Coaching?
    var guidance: GuidanceStep = .findMeter
    /// Set with an aim step (`GuidanceStep.aimAtGround`, `.aimAtWall`); nil with any other.
    var guidanceHint: GuidanceHint?
    /// A world point to aim at (ring when on screen, edge chevron when not).
    var target: SIMD3<Float>?
    /// A walking path on the ground from the homeowner toward the next place to stand, world points.
    var path: [SIMD3<Float>] = []

    var wall: WallGeometry?
    var coverage: CoverageStrip = .empty
    /// LiDAR phones: the live dots. Empty without depth.
    var liveDots: LiveDots = .empty
    var closeUp: CloseUpState = .aiming(hold: 0, problem: nil)
    /// Close-up attempts that ended without a usable photo. "Can't get a clear shot" appears
    /// from the second one on, never earlier.
    var closeUpFailedAttempts = 0
    /// Nil until the close-up photo is taken.
    var meterNumber: MeterNumberState?
    /// The meter's maker as read from the close-up, shown above the number candidates. It exists
    /// only beside those candidates or the number confirmed from them: while `meterNumber` is
    /// nil, reading or skipped it is nil, so a brand from an earlier photo can't outlive its
    /// number through a retake, a skip or a new close-up. The homeowner can reject it
    /// (`rejectMeterBrand`). Like the number, it stays on the phone.
    var meterBrand: String? {
        get {
            switch meterNumber {
            case .choose, .confirmed: offeredMeterBrand
            case .reading, .skipped, nil: nil
            }
        }
        set { offeredMeterBrand = newValue }
    }
    /// Storage for `meterBrand`, set with the candidates it was read with. Read `meterBrand`.
    private var offeredMeterBrand: String?

    var captureCount = 0
    var lastCapture: CaptureEvent?

    var features: [MarkedFeature] = []
    /// Marks lying wholly past a marked end, where the scan doesn't cover them. The review says
    /// so; they are still exported, since a hazard just past an end can be within clearance of a
    /// spot at it.
    var featuresPastEnds: Set<UUID> = []
    var marking: MarkingState?

    var gap: GapRequest?
    /// A wall end was just marked on this side and the engine needs to know what is there:
    /// the wall turns a corner (it continues, unexplored) or something blocks it (a fence, gate
    /// or property line: a real limit). Nil when nothing is being asked.
    var endQuestion: WallSide?
    /// Meters of the walk the end being asked about leaves out, when "Wall ends here" put it at
    /// least a keyframe's spacing short of the farthest kept view on that side
    /// (`WalkedEnd.walkedPast`); the question says so. Nil for an end marked at the reticle. Only
    /// meaningful while `endQuestion` is set: whatever sets `endQuestion` sets this too.
    var endQuestionLeavesOut: Float?
    /// True when `endQuestionLeavesOut` counts cells the strip showed rather than the walk.
    var endQuestionLeavesOutSeen = false
    /// A wall marked during `GuidanceStep.markNextWall` whose corner passed the checks, waiting
    /// for "Is this the next wall?" (`ScanActions.confirmNextWall`, #70). Nil otherwise.
    var nextWallConfirm: NextWallConfirm?
    /// Where the wall end on the side being walked would land now; nil while ending it isn't on
    /// offer (a question or a mark is up, both ends are marked, or the walk is doing something
    /// else). "Wall ends here" shows only while it is set.
    var endPreview: EndPreview?
    /// Why the last "Wall ends here" at the circle marked nothing, while the walk still asks for
    /// that end. Cleared once the circle is on the asked end (`EndPreview` at the reticle on that
    /// side), when an end is set, or when the walk asks for something else.
    var endMarkRefusal: EndMarkRefusal?
    /// "Can't get there" came again on a walk card within `WalkRefusals.repeatWindow` of the one
    /// that last ended a side: the walk asks "End the scan here?" instead of ending this side too
    /// (#82). Answered by `answerEndScan`.
    var endScanQuestion = false
    /// Set with `endScanQuestion` when ending the open sides now would leave ends closer than a
    /// battery is wide (`WalkRefusals.endsTooClose`): "Done with this wall" would refuse them and
    /// the walk would go on, so the question offers "Start over" instead of "Yes, end here".
    var endScanTooShort = false
    /// True after "Done with this wall" was refused because the ends were closer together than
    /// `WallFrame.minWallLength`; the ends were cleared. False again once an end is marked.
    var wallTooShort = false
    /// Set after the tilt-up view: is anything overhead there (roof edge, porch, stairs)? The
    /// camera can't tell open sky from an eave, so the homeowner answers.
    var overheadQuestion = false
    /// The answer to the ground question on the feature review. Nil until the homeowner answers;
    /// an unanswered question exports like `.notSure`.
    var groundAnswer: GroundAnswer?
    var upload: UploadState = .idle
    /// How many views the finished check's answer will still ask for without a tap, counting a
    /// server request on screen: what "One more view to finish" and "2 more views to finish" promise.
    var followUps = 0
    var result: ResultPresentation?
    /// The homeowner's check of the proposed spot, retained beside the result.
    var spotCheck: SpotCheck?
    /// True when the homeowner answered "I can't check this area" about an area other than the
    /// spot the result names, or while it names none. That area stays out of the scan, so the
    /// result says so beside whatever it says about its own spot. Cleared with the scan's spot
    /// checks.
    var uncheckedAreaElsewhere = false
    /// True while the engine sees the AR scene drawing the result in the live camera
    /// (`ResultOverlayPolicy`). The AR screen then draws no overlay of its own; otherwise it
    /// draws `BatteryOverlay`.
    var resultInCamera = false
    /// False once the camera failed after the scan was sent: the answer stays, but "See it on
    /// your wall" is neither shown nor offered until a new scan starts the camera again.
    var spatialResultAvailable = true

    /// True when frames come from a recorded session instead of the camera.
    var isReplay = false
    /// True when the autopilot is driving the intents (UI tests, demos). Show a small badge.
    var isAutopilot = false
    /// True for a practice scan (HouseScanKit `PracticeMeter`): a drawn sample meter stands in
    /// for the electric meter and its close-up photo. Every screen that could pass for a real scan
    /// shows a "Practice meter" badge. Set when the scan starts, from the developer options.
    var isPracticeScan = false
    /// True when no server is configured and the result will be the bundled sample: nothing is
    /// sent, and every screen that talks about the upload or shows the spot must say so.
    var usesSampleResult = false
    /// True when the phone has LiDAR and coverage counts only what depth confirms.
    var depthAvailable = false
    /// The scan's bundle (scene.json, keyframe photos and their poses) once it is packaged, for
    /// "Share scan". Photos leave the phone only if the homeowner shares this.
    var shareableScan: URL?
    /// A camera permission or session failure the homeowner can act on.
    var failure: ScanFailure?

    init() {}
}

enum ScanFailure: Equatable, Sendable {
    case cameraDenied
    case arUnsupported
    case sessionFailed(String)
    case replayUnreadable(String)
}

/// The homeowner's intents. View points are in the coordinate space of the full-screen camera
/// view of `viewSize`; nil means the reticle at the view's center.
@MainActor
protocol ScanActions: AnyObject {
    func finishOnboarding()
    func markMeter(at point: CGPoint?, viewSize: CGSize)
    func skipCloseUp()
    /// The homeowner's pick from `MeterNumberState.choose`; nil means "None of these", which
    /// asks for a retake.
    func chooseMeterNumber(_ candidate: MeterNumberCandidate?)
    /// "Not <brand>" beside the number candidates: drops `ScanViewState.meterBrand`.
    func rejectMeterBrand()
    /// Marks a wall end where `point` meets the wall, on whichever side of the meter that is.
    func markWallEnd(at point: CGPoint?, viewSize: CGSize)
    /// "Wall ends here" during the walk: ends the wall on the side being walked where
    /// `ScanViewState.endPreview` shows it (the phone's place), then asks what is there
    /// (`ScanViewState.endQuestion`). Does nothing while `endPreview` is nil or at the reticle.
    func endWallHere()
    /// The answer to `ScanViewState.endQuestion`. During the walk a corner asks for the next wall
    /// (`GuidanceStep.markNextWall`); a corner the walk doesn't follow exports as an unexplored
    /// end, a blocked wall as a limit, and an end left unanswered stays unexplored.
    func answerWallEnd(turnsCorner: Bool)
    /// Marks the wall under `point` as the next wall round the corner, during
    /// `GuidanceStep.markNextWall`. The walk then goes on along it; "I can't get there"
    /// (`cannotAccessArea`) leaves the end as an unexplored corner instead.
    func markNextWall(at point: CGPoint?, viewSize: CGSize)
    /// "Back" during `GuidanceStep.markNextWall`: stops looking for the next wall and asks
    /// `ScanViewState.endQuestion` about that end again (#70).
    func cancelNextWall()
    /// The answer to `ScanViewState.nextWallConfirm`: true follows the corner to the marked wall,
    /// false drops it and goes on looking for the next wall (#70).
    func confirmNextWall(_ isNextWall: Bool)
    /// The answer to `ScanViewState.overheadQuestion`: true when nothing is overhead.
    func answerOverhead(clear: Bool)
    /// The answer to the ground question during `.markFeatures`; can be changed until upload.
    func answerGround(_ answer: GroundAnswer)
    func beginMarking(_ kind: FeatureKind)
    func markFeaturePoint(at point: CGPoint?, viewSize: CGSize)
    func cancelMarking()
    func deleteFeature(_ id: UUID)
    /// The window question on the review; nil is "Not sure".
    func setWindowOpens(_ id: UUID, opens: Bool?)
    /// Leave the walk for the feature review (allowed once both ends are marked). Ends closer
    /// together than `WallFrame.minWallLength` are cleared instead, and `wallTooShort` is set.
    func finishWalk()
    /// The answer to `ScanViewState.endScanQuestion`. "Yes, end here" (`end`) ends every side
    /// without an end where "Can't get there" would (`ScanViewState.endPreview`, unexplored) and
    /// finishes the walk with what was walked, as `finishWalk` does. "Keep walking" dismisses the
    /// question, and the next "Can't get there" ends its side without asking.
    func answerEndScan(_ end: Bool)
    /// Features confirmed; the engine runs the gap check, then uploads.
    func confirmFeatures()
    /// "I can't get there": the gap is recorded for installer review. On a request the finished
    /// check sent back, the check's next request follows; the result shows once none is left.
    func skipGap()
    /// "Show my result" on a request the finished check sent back: the view is recorded for
    /// installer review like a skipped one, and after one more upload the result shows instead
    /// of the check's next request.
    func showResultNow()
    /// "I can't get to this part of the wall", during the walk: the cells the guidance is asking
    /// for become `.skipped` and the guidance moves on to the next task. While the walk asks to
    /// walk a side, that side's end goes where `ScanViewState.endPreview` shows, as an unexplored
    /// end.
    func cannotAccessArea()
    func retryUpload()
    /// After a rejected upload: back to the feature review, keeping the scan.
    func backToReview()
    /// The answer to `ScanViewState.spotCheck`.
    func answerSpotCheck(_ answer: SpotCheckAnswer)
    /// Start a capture for a server-listed missing item.
    func captureMissing(_ id: String)
    func showAR()
    func closeAR()
    func startOver()
    /// The app came back to the foreground on the camera-access failure: if access is now on,
    /// the scan goes on without Start over. Does nothing otherwise.
    func recheckCameraAccess()
    /// The live AR camera view. Only called while `feed == .live`.
    func liveCameraView() -> AnyView
}
