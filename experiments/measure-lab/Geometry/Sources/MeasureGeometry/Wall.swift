/// A straight wall modeled as a vertical plane through two ground-contact points.
///
/// ARKit's vertical plane detection needs wall texture and may never find blank stucco, but the
/// wall's base usually has texture, and gravity supplies vertical. So the plane comes from two
/// taps where the wall meets the ground (docs/research t3-no-lidar-capture, "Build the wall from the
/// ground"):
///
/// - `direction` u is the horizontal part of contact2 − contact1, normalized. The sign follows tap
///   order, so along-wall coordinates grow from the first contact toward the second.
/// - `normal` n = u × g with g = (0, 1, 0), flipped if needed so it points to the side of the wall
///   the camera was on. Positive offsets are in front of the wall, where the user stands.
/// - The ground line is the straight 3D line through both contacts. Heights are measured above it,
///   which follows a ground that slopes along the wall.
public struct Wall: Sendable, Equatable {
    public let start: SIMD3<Double>
    public let end: SIMD3<Double>
    public let direction: SIMD3<Double>
    public let normal: SIMD3<Double>
    /// Horizontal distance from `start` to `end`, in meters.
    public let length: Double

    public init(
        contact1: SIMD3<Double>,
        contact2: SIMD3<Double>,
        cameraPosition: SIMD3<Double>,
        gates: WallGates = WallGates()
    ) throws(WallError) {
        let run = (contact2 - contact1).horizontal
        let separation = run.length
        guard separation >= gates.minimumContactSeparation else {
            throw .contactsTooClose(separation: separation, minimum: gates.minimumContactSeparation)
        }
        let u = run / separation
        var n = u.cross(worldUp)
        let cameraOffset = n.dot(cameraPosition - contact1)
        guard abs(cameraOffset) >= gates.minimumCameraOffset else {
            throw .cameraInWallPlane(offset: abs(cameraOffset), minimum: gates.minimumCameraOffset)
        }
        if cameraOffset < 0 {
            n = -n
        }
        start = contact1
        end = contact2
        direction = u
        normal = n
        length = separation
    }

    /// Signed horizontal distance from the plane; positive on the side the camera was on.
    public func offset(of point: SIMD3<Double>) -> Double {
        normal.dot(point - start)
    }

    /// Perpendicular horizontal distance from a point to the wall line, as seen from above.
    /// This is the facing gap for a fence or wall across from this one.
    public func gap(to point: SIMD3<Double>) -> Double {
        abs(offset(of: point))
    }

    /// Horizontal position along the wall, in meters from the first contact toward the second.
    public func along(_ point: SIMD3<Double>) -> Double {
        direction.dot(point - start)
    }

    /// Signed along-wall distance from `a` to `b`.
    public func alongDistance(from a: SIMD3<Double>, to b: SIMD3<Double>) -> Double {
        along(b) - along(a)
    }

    /// Ground height (world y) of the ground line at an along-wall position. Outside the two
    /// contacts the line is extended, so check `containsAlong` before trusting it there.
    public func groundHeight(atAlong s: Double) -> Double {
        start.y + (end.y - start.y) * s / length
    }

    /// Height of a point above the ground line directly below it.
    public func heightAboveGround(_ point: SIMD3<Double>) -> Double {
        point.y - groundHeight(atAlong: along(point))
    }

    /// Whether an along-wall position lies between the two contacts. A nanometre of slack at
    /// each end absorbs floating-point rounding: `along(end)` is computed as run·run / |run| and
    /// can land an ulp past `length`, which would flag the second contact itself.
    public func containsAlong(_ s: Double) -> Bool {
        (-1e-9...length + 1e-9).contains(s)
    }

    /// Checks a third ground contact against the plane. The residual is its distance from the
    /// plane; the research note's gate is 2 in.
    public func validate(contact: SIMD3<Double>, gates: WallGates = WallGates()) -> WallValidation {
        let residual = gap(to: contact)
        return WallValidation(residual: residual, tolerance: gates.validationTolerance)
    }

    /// Where a ray meets the wall plane: t = n·(p1 − o) / (n·d), point = o + t·d.
    ///
    /// Refuses rays more than `maximumAngleFromNormal` from the normal (|n·d| < cos of that angle),
    /// which also covers rays parallel to the wall, and intersections at t ≤ 0, which lie behind
    /// the camera.
    public func intersect(_ ray: Ray, gates: WallGates = WallGates()) throws(WallHitError) -> WallHit {
        let cosine = normal.dot(ray.direction)
        let angle = acosDegrees(abs(cosine))
        guard angle <= gates.maximumAngleFromNormal else {
            throw .grazing(angleFromNormal: angle, maximum: gates.maximumAngleFromNormal)
        }
        let t = normal.dot(start - ray.origin) / cosine
        guard t > 0 else {
            throw .behindCamera(t: t)
        }
        let point = ray.point(at: t)
        return WallHit(
            point: point,
            range: t,
            angleFromNormal: angle,
            along: along(point),
            heightAboveGround: heightAboveGround(point),
            withinContacts: containsAlong(along(point))
        )
    }
}

/// Thresholds for building and using a wall. Defaults come from the research note's gates; each
/// is a hypothesis the outdoor run tests, not a calibrated value.
public struct WallGates: Sendable, Equatable {
    /// Minimum horizontal distance between the two ground contacts, in meters. The research note
    /// asks for 6.5 ft; 2 m is that rounded to the metric figure the experiment brief uses.
    public var minimumContactSeparation: Double = 2.0
    /// Largest angle, in degrees, between a tap ray and the wall normal. 60° is |n·d| ≥ 0.5.
    public var maximumAngleFromNormal: Double = 60
    /// Largest allowed distance of a validation contact from the plane: 2 in, in meters.
    public var validationTolerance: Double = 2 * Length.metersPerInch
    /// The normal's sign comes from which side of the plane the camera is on. Closer than this
    /// (in meters), tracking noise could flip it, so the wall is refused instead.
    public var minimumCameraOffset: Double = 0.1

    public init() {}
}

public enum WallError: Error, Sendable, Equatable {
    /// The contacts are closer together horizontally than the minimum, including contacts stacked
    /// vertically.
    case contactsTooClose(separation: Double, minimum: Double)
    /// The camera stood on the wall's own vertical plane, so the front side is unknown.
    case cameraInWallPlane(offset: Double, minimum: Double)
}

public enum WallHitError: Error, Sendable, Equatable {
    case grazing(angleFromNormal: Double, maximum: Double)
    case behindCamera(t: Double)
}

public struct WallHit: Sendable, Equatable {
    public let point: SIMD3<Double>
    /// Distance from the ray origin to the hit, in meters.
    public let range: Double
    /// Angle between the ray and the wall normal, in degrees.
    public let angleFromNormal: Double
    public let along: Double
    public let heightAboveGround: Double
    /// False when the hit lies beyond either contact, where the wall and its ground line are
    /// extrapolated.
    public let withinContacts: Bool
}

public struct WallValidation: Sendable, Equatable {
    public let residual: Double
    public let tolerance: Double

    public var passes: Bool {
        residual <= tolerance
    }
}
