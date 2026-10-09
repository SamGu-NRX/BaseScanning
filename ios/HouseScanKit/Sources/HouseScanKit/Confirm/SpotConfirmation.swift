import Foundation
import simd

// The homeowner's check of the spot a placement answer names.
//
// Camera-only coverage records what was in a photo's view, not what the photo saw: a bush or a
// bin in front of the wall is claimed as wall and ground behind it. The walked path is taken as
// clear space in front of the wall even where something low stands under it (`CoverageMap`,
// "Bounded exceptions"). Before the answer is shown, the app shows the homeowner one kept photo
// of the spot and the area around it and asks whether anything stands there. "It's clear" keeps
// the claims; "Something's there" takes them back over that area (`CoverageMap.withdrawClaims`)
// and the scan is checked again. This file holds the parts with one correct answer: which area
// is asked about, which photo shows it, and when an answer already given settles a new answer.

/// The area a spot check asks about, meters: the spot's footprint and the clearance zone the
/// answer drew around it, as a stretch of wall and the ground in front of it.
public struct SpotArea: Sendable, Equatable {
    /// The footprint along the wall (s) and out from it.
    public var spot: ClosedRange<Float>
    /// How far out from the wall the footprint itself reaches.
    public var spotOut: ClosedRange<Float>
    /// The whole area along the wall: the footprint and every zone that contains it.
    public var span: ClosedRange<Float>
    /// How far out from the wall the area reaches.
    public var depth: Float

    /// The area as its parts. `init(spot:spotOut:zones:)` is the usual path: it widens the
    /// footprint over the clearance zones that hold it.
    public init(spot: ClosedRange<Float>, spotOut: ClosedRange<Float>, span: ClosedRange<Float>, depth: Float) {
        self.spot = spot
        self.spotOut = spotOut
        self.span = span
        self.depth = depth
    }

    /// Two answers' spans that differ by less than this are the same, meters: 0.01 ft, the
    /// server's COVERAGE_TOLERANCE_FT, so a spot the server placed again in the same place counts
    /// as the same spot after its trip through feet and Float meters.
    public static let tolerance: Float = 0.003048

    /// The area around a spot: the footprint, widened along the wall to every clearance zone that
    /// contains the whole footprint (the run of candidate positions the spot belongs to) and out
    /// to the deepest of them. Zones beside the spot, which only overlap it, are other runs of the
    /// answer's sweep and are left out. Zones are (span along the wall, depth out), meters.
    public init(spot: ClosedRange<Float>, spotOut: ClosedRange<Float>, zones: [(span: ClosedRange<Float>, depth: Float)]) {
        let t = Self.tolerance
        let holding = zones.filter { $0.span.lowerBound <= spot.lowerBound + t && $0.span.upperBound >= spot.upperBound - t }
        let low = holding.map(\.span.lowerBound).reduce(spot.lowerBound, min)
        let high = holding.map(\.span.upperBound).reduce(spot.upperBound, max)
        self.init(spot: spot, spotOut: spotOut, span: low...high, depth: holding.map(\.depth).reduce(spotOut.upperBound, max))
    }

    /// Whether this area holds `other`: the same footprint, and an area at least as large. An
    /// answer about this area is an answer about `other`.
    public func holds(_ other: SpotArea) -> Bool {
        let t = Self.tolerance
        func same(_ a: ClosedRange<Float>, _ b: ClosedRange<Float>) -> Bool {
            abs(a.lowerBound - b.lowerBound) <= t && abs(a.upperBound - b.upperBound) <= t
        }
        return same(spot, other.spot) && same(spotOut, other.spotOut)
            && span.lowerBound <= other.span.lowerBound + t && span.upperBound >= other.span.upperBound - t
            && depth >= other.depth - t
    }
}

// MARK: - Photo choice

/// A kept photo the spot check may show.
public struct SpotPhotoCandidate: Sendable, Equatable {
    /// The kept keyframe's id.
    public var id: String
    /// The camera the photo was taken with (`CameraFrame`).
    public var camera: CameraFrame
    /// Whether tracking was normal when it was taken: photos taken while tracking was limited
    /// never show the spot (`SpotPhoto.best`).
    public var trackingNormal: Bool

    /// A candidate from the kept keyframe's id and camera.
    public init(id: String, camera: CameraFrame, trackingNormal: Bool) {
        self.id = id
        self.camera = camera
        self.trackingNormal = trackingNormal
    }
}

/// The photo chosen for a spot check and how much of the area it shows.
public struct SpotPhotoChoice: Sendable, Equatable {
    /// The chosen candidate's id: the keyframe to show, and what the spot check records as the
    /// photo shown (`SpotConfirmation.photoID`).
    public var id: String
    /// Fractions of the footprint's and the whole area's ground samples inside the image.
    public var footprintInView: Float
    /// The same fraction over the whole area — its `SpotArea.span` by `SpotArea.depth` — where
    /// `footprintInView` covers the footprint alone.
    public var areaInView: Float
    /// Angle between the view from the area's middle to the camera and the wall's outward, in
    /// plan, radians.
    public var angleFromFront: Float
}

/// Thresholds of the photo choice. Hypotheses, not measured with homeowners.
public struct SpotPhotoConfig: Sendable, Equatable {
    /// A photo more than 60 degrees to the side shows the area's ground foreshortened and the
    /// wall face edge on, where something in front of the wall is hard to make out. Tighter than
    /// coverage's 65 degrees (`CoverageConfig.maxAngleFromNormal`) because a person reads this
    /// photo, not a measurement.
    public var maxAngleFromFront: Float = 60 * .pi / 180
    /// Farther than coverage's limit a phone photo shows a battery-sized area too small to judge.
    public var maxDistance: Float = CoverageConfig().maxDistance
    /// Samples this close to the image's edge don't count, as in coverage.
    public var imageMargin: Float = CoverageConfig().imageMargin
    /// A photo must show at least half the footprint: with less, the homeowner can't see where
    /// the battery would stand.
    public var minFootprintInView: Float = 0.5
    /// Ground samples every this many meters along the wall and out from it.
    public var sampleSpacing: Float = 0.15

    /// The defaults above.
    public init() {}
}

/// Which kept photo a spot check shows: the one that best shows the area asked about
/// (`SpotPhoto.best`).
public enum SpotPhoto {
    /// The kept photo that best shows the area, or nil when none shows enough of it.
    ///
    /// A photo qualifies when it was taken with normal tracking, from in front of the area's
    /// piece of wall, within `maxAngleFromFront` of straight on, and shows at least
    /// `minFootprintInView` of the footprint. Among those the photo with the most of the area in
    /// view wins: the footprint's fraction and the whole area's, weighted equally. Ties go to the
    /// photo nearer straight on, then to the earlier candidate. A sample counts when it lies in
    /// front of the camera, inside the image margin and within `maxDistance`; the ground is
    /// sampled every `sampleSpacing` along the wall and out from it, edges included.
    public static func best(
        _ candidates: [SpotPhotoCandidate], area: SpotArea, wall: WallFrame, config: SpotPhotoConfig = SpotPhotoConfig()
    ) -> SpotPhotoChoice? {
        let footprint = samples(wall, along: area.spot, out: area.spotOut, spacing: config.sampleSpacing)
        let whole = samples(wall, along: area.span, out: 0...area.depth, spacing: config.sampleSpacing)
        let middle = (area.span.lowerBound + area.span.upperBound) / 2
        let piece = wall.segment(atS: middle)
        let centre = wall.world(s: middle, height: 0, out: area.depth / 2)
        var best: (choice: SpotPhotoChoice, score: Float)?
        for candidate in candidates where candidate.trackingNormal {
            let camera = candidate.camera
            guard wall.out(of: camera.position, pieceAtS: middle) > 0 else { continue }
            let toCamera = SIMD2(camera.position.x - centre.x, camera.position.z - centre.z)
            guard simd_length(toCamera) > 0 else { continue }
            let cosine = simd_dot(simd_normalize(toCamera), simd_normalize(SIMD2(piece.outward.x, piece.outward.z)))
            let angle = acos(min(max(cosine, -1), 1))
            guard angle <= config.maxAngleFromFront else { continue }
            func fraction(_ points: [SIMD3<Float>]) -> Float {
                let inView = points.filter { point in
                    guard simd_distance(point, camera.position) <= config.maxDistance, let pixel = camera.pixel(of: point) else { return false }
                    return camera.contains(pixel: pixel, margin: config.imageMargin)
                }
                return Float(inView.count) / Float(max(1, points.count))
            }
            let footprintInView = fraction(footprint)
            guard footprintInView >= config.minFootprintInView else { continue }
            let choice = SpotPhotoChoice(id: candidate.id, footprintInView: footprintInView, areaInView: fraction(whole), angleFromFront: angle)
            let score = (footprintInView + choice.areaInView) / 2
            if let current = best, score < current.score || (score == current.score && angle >= current.choice.angleFromFront) { continue }
            best = (choice, score)
        }
        return best?.choice
    }

    /// Ground points over a rectangle in wall coordinates, `spacing` apart or closer, edges included.
    static func samples(_ wall: WallFrame, along: ClosedRange<Float>, out: ClosedRange<Float>, spacing: Float) -> [SIMD3<Float>] {
        func steps(_ range: ClosedRange<Float>) -> [Float] {
            let count = max(1, Int(((range.upperBound - range.lowerBound) / spacing).rounded(.up)))
            return (0...count).map { range.lowerBound + (range.upperBound - range.lowerBound) * Float($0) / Float(count) }
        }
        return steps(along).flatMap { s in steps(out).map { o in wall.world(s: s, height: 0, out: o) } }
    }
}

// MARK: - Binding

/// The homeowner's answer to a spot check.
public enum SpotConfirmationAnswer: String, Sendable, Equatable {
    /// Nothing stands in the area: the photo's claims over it stand, backed by this answer.
    case clear
    /// Something stands there: the claims over the area are withdrawn and the scan is checked
    /// again.
    case somethingThere
}

/// One spot check and its answer, tied to what it was about: the area, the answer that named the
/// spot and the scene that answer was for (each by the sha256 of its bytes), and the photo shown.
public struct SpotConfirmation: Sendable, Equatable {
    /// The area the question was about (`SpotArea`).
    public var area: SpotArea
    /// The sha256 of the placement answer that named this spot.
    public var answerSHA256: String
    /// The sha256 of the scene.json that answer was for.
    public var sceneSHA256: String
    /// The keyframe shown, nil when no kept photo showed the area and the question was asked
    /// without one.
    public var photoID: String?
    /// What the homeowner said (`SpotConfirmationAnswer`).
    public var answer: SpotConfirmationAnswer

    /// One check: the area asked about, the digests tying it to the exchange that named the
    /// spot, the photo shown, and the answer.
    public init(area: SpotArea, answerSHA256: String, sceneSHA256: String, photoID: String?, answer: SpotConfirmationAnswer) {
        self.area = area
        self.answerSHA256 = answerSHA256
        self.sceneSHA256 = sceneSHA256
        self.photoID = photoID
        self.answer = answer
    }
}

/// Every spot check of a scan, oldest first.
///
/// A later answer that names a spot is settled by an earlier check only when that check's area
/// holds the new one (`SpotArea.holds`): the same footprint, and an area at least as large. A
/// spot that moved, or an area that grew, is asked about again. The homeowner's answer is about
/// the place, not the photos, so a new scene of the same place (a re-upload after another view,
/// or after "Something's there") is settled by it; the answer and scene digests record which
/// exchange the homeowner answered.
public struct SpotConfirmations: Sendable, Equatable {
    public private(set) var records: [SpotConfirmation] = []

    /// A scan starts with no checks: the first spot is always asked about.
    public init() {}

    public mutating func record(_ confirmation: SpotConfirmation) {
        records.append(confirmation)
    }

    /// The latest check that settles `area`, or nil when the homeowner has to be asked.
    public func settling(_ area: SpotArea) -> SpotConfirmation? {
        records.last { $0.area.holds(area) }
    }
}
