/// The evidence behind one session's results: each point's position, own warnings and the wall
/// that located it, each wall's contacts and checks, and each saved measurement's warnings.
///
/// `LabSession` records every point, wall, check and measurement here and reads qualification
/// back from here, so these rules are the ones the app runs:
///
/// - A check contact with a warning of its own can't confirm a wall (`WallCheck.Outcome`).
/// - Every check re-evaluates every saved measurement, so a wall that fails after a value was
///   saved turns that value into an abstention.
/// - Saved warnings only grow. A later passing check never clears a warning a measurement
///   already has, so an abstention stays one.
///
/// Ids are the session's own ("P3", "W1", "M2"). Adding something that refers to an id the
/// ledger has never seen is a bookkeeping bug in the caller and stops the app.
public struct MeasurementLedger: Sendable {
    /// What a measurement runs to, by id.
    public enum Target: Hashable, Sendable {
        case point(String)
        case wall(String)

        public var id: String {
            switch self {
            case .point(let id), .wall(let id): id
            }
        }
    }

    private struct Point: Sendable {
        let position: SIMD3<Double>
        let flags: [MeasurementWarning]
        /// Set only for a point located by intersecting this wall's plane.
        let wall: String?
    }

    private struct WallEntry: Sendable {
        let wall: Wall
        let contacts: [String]
        var checks: [WallCheck] = []
    }

    private struct Saved: Sendable {
        let id: String
        let from: String
        let target: Target
        let referenceWall: String?
        let compared: MeasuredQuantity
        var warnings: [MeasurementWarning]
    }

    private var points: [String: Point] = [:]
    private var walls: [String: WallEntry] = [:]
    /// In save order.
    private var saved: [Saved] = []

    public init() {}

    // MARK: - Recording

    /// `wall` names the wall whose plane located the point, so the point carries that wall's
    /// status from then on.
    public mutating func addPoint(_ id: String, at position: SIMD3<Double>, flags: [MeasurementWarning], onWall wall: String? = nil) {
        precondition(points[id] == nil, "Point \(id) is already in the ledger")
        if let wall {
            precondition(walls[wall] != nil, "Point \(id) is on \(wall), which is not in the ledger")
        }
        points[id] = Point(position: position, flags: flags, wall: wall)
    }

    public mutating func addWall(_ id: String, _ wall: Wall, contacts: [String]) {
        precondition(walls[id] == nil, "Wall \(id) is already in the ledger")
        for contact in contacts {
            precondition(points[contact] != nil, "Wall \(id) uses contact \(contact), which is not in the ledger")
        }
        walls[id] = WallEntry(wall: wall, contacts: contacts)
    }

    /// Records a validation contact against a wall, then re-evaluates every saved measurement.
    /// Returns the check's outcome and the ids of saved measurements that gained a warning, in
    /// save order.
    public mutating func addCheck(
        _ validation: WallValidation,
        contact pointID: String,
        toWall wallID: String
    ) -> (outcome: WallCheck.Outcome, changedMeasurements: [String]) {
        precondition(walls[wallID] != nil, "Check on \(wallID), which is not in the ledger")
        let check = WallCheck(passes: validation.passes, contactWarnings: point(pointID).flags)
        walls[wallID]?.checks.append(check)
        var changed: [String] = []
        for index in saved.indices {
            let measurement = saved[index]
            let current = currentWarnings(
                from: measurement.from,
                to: measurement.target,
                referenceWall: measurement.referenceWall,
                compared: measurement.compared
            ) ?? []
            let merged = normalized(measurement.warnings + current)
            if merged != measurement.warnings {
                saved[index].warnings = merged
                changed.append(measurement.id)
            }
        }
        return (check.outcome, changed)
    }

    /// Saves a measurement and returns its warnings, or nil when the compared quantity doesn't
    /// apply to this pair (for example along-wall distance without a reference wall).
    public mutating func saveMeasurement(
        _ id: String,
        from pointID: String,
        to target: Target,
        referenceWall: String?,
        compared: MeasuredQuantity
    ) -> [MeasurementWarning]? {
        precondition(!saved.contains { $0.id == id }, "Measurement \(id) is already in the ledger")
        guard let warnings = currentWarnings(from: pointID, to: target, referenceWall: referenceWall, compared: compared) else {
            return nil
        }
        saved.append(Saved(
            id: id, from: pointID, target: target, referenceWall: referenceWall, compared: compared, warnings: warnings
        ))
        return warnings
    }

    // MARK: - Reading

    public func wall(_ id: String) -> Wall? {
        walls[id]?.wall
    }

    public func wallStatus(_ id: String) -> WallStatus? {
        guard let entry = walls[id] else { return nil }
        return WallStatus(contactWarnings: entry.contacts.map { point($0).flags }, checks: entry.checks)
    }

    /// A point's own warnings plus, for a point on a wall, that wall's current status.
    public func evidence(forPoint id: String) -> PointEvidence? {
        guard let point = points[id] else { return nil }
        return PointEvidence(own: point.flags, wall: point.wall.flatMap(wallStatus))
    }

    /// Every quantity that applies from a point to a target. Empty when an id is unknown.
    public func values(from pointID: String, to target: Target, referenceWall: String?) -> [MeasuredQuantity: Double] {
        guard let from = points[pointID], let end = geometry(of: target) else { return [:] }
        return measuredValues(from: from.position, to: end, referenceWall: referenceWall.flatMap(wall))
    }

    /// The warnings a measurement would be saved with now, or nil when the quantity doesn't
    /// apply.
    public func warnings(
        from pointID: String,
        to target: Target,
        referenceWall: String?,
        compared: MeasuredQuantity
    ) -> [MeasurementWarning]? {
        currentWarnings(from: pointID, to: target, referenceWall: referenceWall, compared: compared)
    }

    /// A saved measurement's warnings, including any added by later wall checks.
    public func savedWarnings(_ id: String) -> [MeasurementWarning]? {
        saved.first { $0.id == id }?.warnings
    }

    // MARK: - Private

    private func point(_ id: String) -> Point {
        guard let point = points[id] else { preconditionFailure("Point \(id) is not in the ledger") }
        return point
    }

    private func geometry(of target: Target) -> MeasurementTarget? {
        switch target {
        case .point(let id): points[id].map { .point($0.position) }
        case .wall(let id): wall(id).map { .wall($0) }
        }
    }

    private func currentWarnings(
        from pointID: String,
        to target: Target,
        referenceWall referenceID: String?,
        compared: MeasuredQuantity
    ) -> [MeasurementWarning]? {
        guard let from = points[pointID], let fromEvidence = evidence(forPoint: pointID), let end = geometry(of: target) else {
            return nil
        }
        let reference = referenceID.flatMap(wall)
        guard let value = measuredValues(from: from.position, to: end, referenceWall: reference)[compared] else { return nil }
        let toEvidence: PointEvidence?
        let targetWall: WallStatus?
        switch target {
        case .point(let id):
            toEvidence = evidence(forPoint: id)
            targetWall = nil
        case .wall(let id):
            toEvidence = nil
            targetWall = wallStatus(id)
        }
        return measurementWarnings(
            from: fromEvidence,
            to: toEvidence,
            targetWall: targetWall,
            referenceWall: referenceID.flatMap(wallStatus),
            compared: compared,
            value: value,
            beyondWallContacts: readsWallBeyondContacts(from: from.position, to: end, referenceWall: reference, compared: compared)
        )
    }
}
