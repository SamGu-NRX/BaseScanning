/// Why a point or measurement should count as an abstention rather than an accepted value.
///
/// A result inherits the warnings of every observation it depends on, so a flaw in one ground
/// contact follows the wall it defines into every point on that wall and every measurement that
/// uses them. The observations themselves keep only their own warnings, so analysis can still
/// tell where a warning started.
public enum MeasurementWarning: String, Sendable, Codable {
    /// A ground hit on ARKit's estimated surface rather than a found plane.
    case estimatedPlane
    /// A ground hit on a found plane's extension, past its detected edge.
    case extendedPlane
    /// A ground ray looking down less than the minimum angle.
    case shallowLookDown
    /// A wall point beyond the wall's two contacts, where the plane is extrapolated. On a
    /// measurement, also a height, facing gap or along-wall distance read at a point beyond the
    /// contacts of the wall it uses.
    case outsideWallContacts
    /// One of the wall's ground contacts has a warning of its own.
    case wallContactWarning
    /// No validation contact without warnings of its own has passed yet.
    case wallNotValidated
    /// A validation contact lay farther from the wall plane than the tolerance.
    case wallValidationFailed
    /// A height above ground came out negative: the point is below the wall's ground line.
    case belowGround
}

/// One validation contact checked against a wall plane.
public struct WallCheck: Sendable, Equatable {
    public enum Outcome: Sendable, Equatable {
        /// Within tolerance, from a contact without warnings of its own.
        case confirmed
        /// Within tolerance, but the contact has a warning of its own (an estimated or extended
        /// plane, or a shallow look-down), so its agreement with the plane proves nothing.
        case unconfirmed
        /// Beyond tolerance. A flagged contact that misses can't be told apart from a wrong
        /// wall, so the failure stands whatever the contact's own warnings.
        case failed
    }

    /// Whether the contact lay within the tolerance of the plane.
    public var passes: Bool
    /// The check contact's own warnings.
    public var contactWarnings: [MeasurementWarning]

    public init(passes: Bool, contactWarnings: [MeasurementWarning]) {
        self.passes = passes
        self.contactWarnings = contactWarnings
    }

    public var outcome: Outcome {
        if !passes { return .failed }
        return contactWarnings.isEmpty ? .confirmed : .unconfirmed
    }
}

/// The qualification of a wall, from its contacts' warnings and its validation contacts.
public struct WallStatus: Sendable, Equatable {
    /// Own warnings of each ground contact that defines the wall.
    public var contactWarnings: [[MeasurementWarning]]
    /// Each validation contact, in order.
    public var checks: [WallCheck]

    public init(contactWarnings: [[MeasurementWarning]], checks: [WallCheck]) {
        self.contactWarnings = contactWarnings
        self.checks = checks
    }

    /// A single failed check disqualifies the wall even if another one passed. Only a confirmed
    /// check clears `wallNotValidated`.
    public var warnings: [MeasurementWarning] {
        var result: [MeasurementWarning] = []
        if contactWarnings.contains(where: { !$0.isEmpty }) { result.append(.wallContactWarning) }
        let outcomes = checks.map(\.outcome)
        if outcomes.contains(.failed) {
            result.append(.wallValidationFailed)
        } else if !outcomes.contains(.confirmed) {
            result.append(.wallNotValidated)
        }
        return result
    }
}

/// A point's own warnings plus, for a point located on a wall plane, that wall's status.
public struct PointEvidence: Sendable, Equatable {
    public var own: [MeasurementWarning]
    /// Set only when the point's position came from intersecting a wall plane.
    public var wall: WallStatus?

    public init(own: [MeasurementWarning], wall: WallStatus? = nil) {
        self.own = own
        self.wall = wall
    }

    /// Everything that should stop the point counting as accepted.
    public var warnings: [MeasurementWarning] {
        normalized(own + (wall?.warnings ?? []))
    }
}

/// Warnings for one measurement value.
///
/// It inherits both points' warnings (including the walls that located them), the target wall's
/// status for point-to-wall quantities, and the reference wall's status only when the compared
/// quantity is along-wall distance. `beyondWallContacts` (see `readsWallBeyondContacts`) adds
/// `outsideWallContacts`, and a negative height above ground adds `belowGround`.
public func measurementWarnings(
    from: PointEvidence,
    to: PointEvidence?,
    targetWall: WallStatus?,
    referenceWall: WallStatus?,
    compared: MeasuredQuantity,
    value: Double,
    beyondWallContacts: Bool
) -> [MeasurementWarning] {
    var result = from.warnings + (to?.warnings ?? []) + (targetWall?.warnings ?? [])
    if compared == .alongWall, let referenceWall {
        result += referenceWall.warnings
    }
    if beyondWallContacts {
        result.append(.outsideWallContacts)
    }
    if compared == .heightAboveGround, value < 0 {
        result.append(.belowGround)
    }
    return normalized(result)
}

/// Sorted by name and without repeats, so equal sets compare and encode the same way.
func normalized(_ warnings: [MeasurementWarning]) -> [MeasurementWarning] {
    Set(warnings).sorted { $0.rawValue < $1.rawValue }
}
