import ARKit
import MeasureGeometry
import Observation
import UIKit

/// One measuring session: the tool state machine, everything recorded so far, and the
/// session.json writer.
///
/// `CaptureController` resolves each tap to a saved frame, a pixel and a ray (plus a ground
/// raycast when the tool needs one) and hands it to `handle(_:)`. All geometry decisions happen
/// here through the MeasureGeometry package, so every accept and refuse is logged in one place.
@MainActor
@Observable
final class LabSession {
    /// Tracking must stay `.normal` this long before a tap counts (research note, acceptance gates).
    static let trackingStableSeconds = 1.0
    /// Ground taps flatter than this are kept but flagged (research note, acceptance gates).
    static let minimumGroundLookDown = 30.0
    /// Live taps compare ARKit's display transform with the app's portrait mapping. The two should
    /// agree to rounding; 2 px is an arbitrary alarm level, not a measured one.
    static let displayMappingTolerance = 2.0

    private let wallGates = WallGates()
    private let triangulationGates = TriangulationGates()
    let recorder = KeyframeRecorder()
    let lidarAvailable = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
    let meshReconstructionSupported = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)

    private(set) var manifest: SessionManifest
    private(set) var folder: URL?
    private var storageErrors = StorageErrorState<String>()
    var storageError: String? { storageErrors.message }

    private(set) var tool: Tool = .ground
    private(set) var wallStep: WallStep = .firstContact
    private(set) var twoViewFirst: TwoViewFirst?
    private(set) var lastEvent: LabEvent?

    private(set) var trackingState: TrackingState = .notAvailable
    private(set) var isTrackingStable = false
    private(set) var isInterrupted = false
    private(set) var failure: SessionFailure?
    private(set) var horizontalPlaneCount = 0
    private(set) var verticalPlaneCount = 0

    private var horizontalPlanes: Set<UUID> = []
    private var verticalPlanes: Set<UUID> = []
    /// Points, walls, checks and saved measurements, for every accept-or-abstain decision.
    private var ledger = MeasurementLedger()
    /// Sends each finished keyframe write to the session that reserved it.
    private var router = KeyframeRouter<String>()
    /// Closed sessions with keyframe writes still in flight or a manifest not yet on disk, by
    /// session id. Each stays until both are done, however many sessions opened since.
    private var closedManifests = ClosedManifests<String, ClosedSession>()
    private var stability = TrackingStability(requiredSeconds: LabSession.trackingStableSeconds)
    /// Wakes the UI when `stability` should turn stable; `stability` decides.
    private var stabilityTask: Task<Void, Never>?
    private var keyframesSinceSave = 0
    private var eventCount = 0

    enum WallStep: Equatable {
        case firstContact
        case secondContact(pointID: String, position: SIMD3<Double>)
        /// The wall exists; further taps are optional validation contacts.
        case validate(wallID: String)
    }

    struct TwoViewFirst {
        let tapID: String
        let keyframeID: String
        let ray: Ray
    }

    private struct ClosedSession: Sendable {
        var manifest: SessionManifest
        let folder: URL
    }

    /// Pure: nothing touches disk until `start()`, so SwiftUI can re-create the owning view freely.
    init() {
        manifest = Self.makeManifest(sceneDepth: false, recorder: recorder)
    }

    /// Opens the first session folder. Later calls do nothing.
    func start() {
        guard folder == nil, storageError == nil else { return }
        manifest = Self.makeManifest(sceneDepth: false, recorder: recorder)
        openFolder(startRecorder: true)
    }

    // MARK: - Session lifecycle

    var sceneDepthEnabled: Bool {
        manifest.session.sceneDepthEnabled
    }

    /// Scene depth is fixed per session, so it can change only before the first tap.
    var canChangeSceneDepth: Bool {
        lidarAvailable && manifest.taps.isEmpty
    }

    /// Saves and closes the current session and opens an empty one in a new folder. The caller
    /// stops the recorder first, passes how many writes the closing session reserved, and starts
    /// the recorder on the returned destination once the AR map resets.
    func startNewSession(sceneDepth: Bool, closingReserved: Int) -> KeyframeRecorder.Destination? {
        let saved = save()
        let awaitingWrites = router.closeCurrent(reserved: closingReserved)
        if let folder {
            closedManifests.close(
                manifest.session.id,
                ClosedSession(manifest: manifest, folder: folder),
                awaitingWrites: awaitingWrites,
                saved: saved
            )
        }
        manifest = Self.makeManifest(sceneDepth: sceneDepth && lidarAvailable, recorder: recorder)
        ledger = MeasurementLedger()
        wallStep = .firstContact
        twoViewFirst = nil
        lastEvent = nil
        keyframesSinceSave = 0
        return openFolder(startRecorder: false)
    }

    private static func makeManifest(sceneDepth: Bool, recorder: KeyframeRecorder) -> SessionManifest {
        let now = Date.now
        let idFormatter = DateFormatter()
        idFormatter.locale = Locale(identifier: "en_US_POSIX")
        idFormatter.timeZone = .gmt
        idFormatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        let spacing = recorder.spacing
        let wallGates = WallGates()
        let triangulationGates = TriangulationGates()
        let info = SessionInfo(
            // The random suffix keeps two sessions started in the same second in separate folders.
            id: idFormatter.string(from: now) + "-" + String(format: "%04x", UInt16.random(in: .min ... .max)),
            startedAt: now.ISO8601Format(),
            startedAtUptime: ProcessInfo.processInfo.systemUptime,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            deviceModel: deviceModelIdentifier(),
            systemVersion: UIDevice.current.systemVersion,
            lidarAvailable: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth),
            meshReconstructionSupported: ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh),
            sceneDepthEnabled: sceneDepth
        )
        let gates = GateValues(
            trackingStableSeconds: trackingStableSeconds,
            minimumGroundLookDown: minimumGroundLookDown,
            minimumContactSeparation: wallGates.minimumContactSeparation,
            maximumAngleFromWallNormal: wallGates.maximumAngleFromNormal,
            wallValidationTolerance: wallGates.validationTolerance,
            minimumCameraOffsetFromWall: wallGates.minimumCameraOffset,
            minimumRayAngle: triangulationGates.minimumRayAngle,
            maximumRayGap: triangulationGates.maximumGap,
            keyframeSpacingMeters: spacing.meters,
            keyframeSpacingDegrees: spacing.degrees
        )
        return SessionManifest(session: info, gates: gates)
    }

    @discardableResult
    private func openFolder(startRecorder: Bool) -> KeyframeRecorder.Destination? {
        do {
            let folder = try SessionStore.makeFolder(id: manifest.session.id)
            self.folder = folder
            storageErrors.clear()
            let destination = KeyframeRecorder.Destination(sessionID: manifest.session.id, folder: folder)
            router.open(manifest.session.id)
            if startRecorder { recorder.start(destination) }
            save()
            return destination
        } catch {
            folder = nil
            storageErrors.report("Couldn't create a folder for this session: \(error.localizedDescription)")
            return nil
        }
    }

    /// Writes session.json and returns the folder to archive. Called right before zipping so the
    /// manifest lists every keyframe saved so far. Returns nil when that write fails: the file on
    /// disk is older than memory, and could still list a measurement as accepted after a later
    /// wall check made it an abstention. `storageError` says why.
    func prepareExport() -> URL? {
        save() ? folder : nil
    }

    /// Writes the current session.json, and retries any closed session's manifest that is not on
    /// disk yet. Returns whether the current one was written.
    @discardableResult
    func save() -> Bool {
        defer { flushClosedManifests() }
        guard let folder else { return false }
        do {
            try SessionStore.write(manifest, to: folder)
            storageErrors.currentManifestSaved()
            keyframesSinceSave = 0
            return true
        } catch {
            storageErrors.currentManifestFailed("Couldn't save session.json: \(error.localizedDescription)")
            return false
        }
    }

    private func flushClosedManifests() {
        let result = closedManifests.flush { closed in try SessionStore.write(closed.manifest, to: closed.folder) }
        for session in result.saved {
            storageErrors.closedSessionSaved(session)
        }
        if let failure = result.failures.first {
            storageErrors.report(
                "Couldn't save session.json of closed session \(failure.session): "
                    + "\(failure.error.localizedDescription). It is kept and saved again on the next write.",
                closedSession: failure.session
            )
        }
    }

    // MARK: - ARKit state

    func trackingChanged(to state: TrackingState, at time: Double) {
        // Refused: a report from the AR run before the last reset, delivered late.
        guard stability.trackingChanged(isNormal: state == .normal, at: time) else { return }
        if state != trackingState {
            trackingState = state
            manifest.tracking.append(TrackingRecord(time: time, state: state.manifestName))
        }
        refreshStability()
    }

    /// Sets `isTrackingStable` from `stability` now, and wakes again when it should turn stable.
    private func refreshStability() {
        stabilityTask?.cancel()
        stabilityTask = nil
        let now = ProcessInfo.processInfo.systemUptime
        isTrackingStable = stability.isStable(at: now)
        guard !isTrackingStable, let stableAt = stability.stableAt else { return }
        stabilityTask = Task {
            try? await Task.sleep(for: .seconds(max(0, stableAt - now)))
            guard !Task.isCancelled else { return }
            refreshStability()
        }
    }

    func planesAdded(_ planes: [DetectedPlane]) {
        for plane in planes {
            switch plane.orientation {
            case .horizontal: horizontalPlanes.insert(plane.id)
            case .vertical: verticalPlanes.insert(plane.id)
            }
        }
        horizontalPlaneCount = horizontalPlanes.count
        verticalPlaneCount = verticalPlanes.count
    }

    func planesRemoved(_ planes: [DetectedPlane]) {
        for plane in planes {
            switch plane.orientation {
            case .horizontal: horizontalPlanes.remove(plane.id)
            case .vertical: verticalPlanes.remove(plane.id)
            }
        }
        horizontalPlaneCount = horizontalPlanes.count
        verticalPlaneCount = verticalPlanes.count
    }

    func interruptionChanged(isInterrupted: Bool, at time: Double) {
        guard stability.interruptionChanged(isInterrupted: isInterrupted, at: time) else { return }
        self.isInterrupted = isInterrupted
        refreshStability()
        if isInterrupted { save() }
    }

    func sessionFailed(_ failure: SessionFailure) {
        self.failure = failure
        save()
    }

    /// Called when a new ARSession run resets tracking at `resetUptime`: plane counts start from
    /// zero, tracking and interruption reports stamped before the reset are ignored, and taps wait
    /// until the new map reports normal tracking for the full stability time.
    func arSessionRestarted(at resetUptime: Double) {
        trackingState = .notAvailable
        stability.reset(at: resetUptime)
        refreshStability()
        horizontalPlanes = []
        verticalPlanes = []
        horizontalPlaneCount = 0
        verticalPlaneCount = 0
    }

    /// Files one finished keyframe write under the session that reserved it.
    func keyframeDelivered(_ delivery: KeyframeRecorder.Delivery) {
        let (route, drained) = router.report(for: delivery.sessionID)
        switch (route, delivery.result) {
        case (.current, .success(let saved)):
            manifest.keyframes.append(saved.record)
            keyframesSinceSave += 1
            // Keep session.json close to the images on disk without re-encoding it every frame.
            if keyframesSinceSave >= 20 { save() }
        case (.current, .failure(let error)):
            storageErrors.report(error.message)
        case (.closed, .success(let saved)):
            closedManifests.update(delivery.sessionID) { $0.manifest.keyframes.append(saved.record) }
        case (.closed, .failure(let error)):
            // The keyframe itself is lost, so no manifest retry clears this.
            storageErrors.report("Closed session \(delivery.sessionID): \(error.message)")
        case (.unknown, _):
            break
        }
        guard route == .closed else { return }
        if drained { closedManifests.markDrained(delivery.sessionID) }
        // Written now so a late keyframe reaches disk promptly; a failure stays kept for the
        // next save to retry.
        flushClosedManifests()
    }

    /// A save that reserved nothing, so no delivery will follow.
    func recorderFailed(_ error: RecorderError) {
        if error == .frameFromPreviousMap {
            refuseUnresolvedTap(reason: "frameFromPreviousMap", message: error.message)
        } else {
            storageErrors.report(error.message)
        }
    }

    // MARK: - Tools

    func select(_ tool: Tool) {
        self.tool = tool
    }

    /// Why a tap would be refused right now, or nil when it would be accepted.
    var markBlocker: String? {
        if isInterrupted { return "Camera paused" }
        if trackingState != .normal { return trackingState.instruction }
        if !isTrackingStable { return "Hold steady for a moment" }
        if tool == .wallPoint, activeWall == nil { return "Make a wall first" }
        return nil
    }

    private var activeWall: (id: String, wall: Wall)? {
        guard let id = manifest.walls.last?.id, let wall = ledger.wall(id) else { return nil }
        return (id, wall)
    }

    var instruction: String {
        switch tool {
        case .ground:
            return "Aim the ring at the ground, then Mark."
        case .wall:
            switch wallStep {
            case .firstContact:
                return "Aim where the wall meets the ground at one end, then Mark."
            case .secondContact:
                return "Walk at least 2 m (6 ft 7 in) along the wall. Mark its base again."
            case .validate(let id):
                return "\(id) is set. To check it, mark a third point on its base."
            }
        case .wallPoint:
            guard let wall = activeWall else { return "Make a wall with the Wall tool first." }
            return "Aim at a point on \(wall.id), then Mark."
        case .twoView:
            if twoViewFirst == nil {
                return "Freeze a frame and tap a feature, or aim the ring at it and Mark."
            }
            return "Step about 1 m sideways, then mark the same feature again."
        }
    }

    /// The label for the tool's secondary action, or nil when there is none.
    var restartTitle: String? {
        switch tool {
        case .wall where wallStep != .firstContact: "New wall"
        case .twoView where twoViewFirst != nil: "Start over"
        default: nil
        }
    }

    func restartTool() {
        switch tool {
        case .wall: wallStep = .firstContact
        case .twoView: twoViewFirst = nil
        case .ground, .wallPoint: break
        }
    }

    private var stepName: String {
        switch tool {
        case .ground, .wallPoint: "single"
        case .wall:
            switch wallStep {
            case .firstContact: "firstContact"
            case .secondContact: "secondContact"
            case .validate: "validationContact"
            }
        case .twoView: twoViewFirst == nil ? "firstView" : "secondView"
        }
    }

    /// Logs a tap that never reached a frame, for example while tracking is limited.
    func refuseUnresolvedTap(reason: String, message: String) {
        logRefusal(tap: nil, Refusal(reason: reason, message: message))
        announce(.refused, "Not measured", [message])
        save()
    }

    func handle(_ input: TapInput) {
        guard input.snapshot.sessionID == manifest.session.id else { return }
        var tap = TapRecord(
            id: "T\(manifest.taps.count + 1)",
            time: input.snapshot.timestamp,
            tool: tool.rawValue,
            step: stepName,
            keyframe: input.snapshot.keyframeID,
            frozen: input.frozen,
            pixel: [input.pixel.u, input.pixel.v],
            rayOrigin: input.ray.origin,
            rayDirection: input.ray.direction,
            displayMappingCheck: input.displayMappingCheck
        )
        switch tool {
        case .ground: handleGround(input, tap: &tap)
        case .wall: handleWall(input, tap: &tap)
        case .wallPoint: handleWallPoint(input, tap: &tap)
        case .twoView: handleTwoView(input, tap: &tap)
        }
        manifest.taps.append(tap)
        if let check = input.displayMappingCheck, check > Self.displayMappingTolerance, let event = lastEvent {
            // Frozen-frame taps rely on this mapping; say so on the spot rather than only in the log.
            lastEvent = LabEvent(id: event.id, tone: .warning, title: event.title, lines: event.lines + [
                "Screen mapping is off by \(check.formatted(.number.precision(.fractionLength(1)))) px; frozen taps may miss",
            ])
        }
        save()
    }

    private func handleGround(_ input: TapInput, tap: inout TapRecord) {
        guard let point = makeGroundPoint(input, tap: &tap) else { return }
        announce(point.flags.isEmpty ? .accepted : .warning, "\(point.id) on the ground", describe(point))
    }

    private func handleWall(_ input: TapInput, tap: inout TapRecord) {
        switch wallStep {
        case .firstContact:
            guard let point = makeGroundPoint(input, tap: &tap) else { return }
            wallStep = .secondContact(pointID: point.id, position: point.position)
            announce(point.flags.isEmpty ? .accepted : .warning, "First contact \(point.id)", describe(point))

        case .secondContact(let firstID, let firstPosition):
            guard let point = makeGroundPoint(input, tap: &tap) else { return }
            let wall: Wall
            do {
                wall = try Wall(
                    contact1: firstPosition, contact2: point.position, cameraPosition: input.ray.origin, gates: wallGates
                )
            } catch {
                refuse(&tap, "No wall from \(firstID) and \(point.id)", Self.explain(error))
                return
            }
            let id = "W\(manifest.walls.count + 1)"
            ledger.addWall(id, wall, contacts: [firstID, point.id])
            manifest.walls.append(WallRecord(
                id: id,
                contacts: [firstID, point.id],
                start: wall.start,
                end: wall.end,
                direction: wall.direction,
                normal: wall.normal,
                length: wall.length,
                cameraPosition: input.ray.origin,
                validations: [],
                warnings: []
            ))
            refreshWarnings(ofWall: id)
            wallStep = .validate(wallID: id)
            let contactFlagged = ledger.wallStatus(id)?.warnings.contains(.wallContactWarning) ?? false
            var lines = ["\(Format.length(wall.length)) between \(firstID) and \(point.id)"]
            if contactFlagged { lines.append("A contact has a warning, so everything on \(id) is flagged") }
            lines.append("Mark a third point on its base to check it")
            announce(contactFlagged ? .warning : .accepted, "\(id) set", lines)

        case .validate(let wallID):
            guard let wall = ledger.wall(wallID), let index = manifest.walls.firstIndex(where: { $0.id == wallID }) else { return }
            guard let point = makeGroundPoint(input, tap: &tap) else { return }
            let check = wall.validate(contact: point.position, gates: wallGates)
            manifest.walls[index].validations.append(WallRecord.Validation(
                point: point.id, residual: check.residual, tolerance: check.tolerance, passes: check.passes
            ))
            let result = ledger.addCheck(check, contact: point.id, toWall: wallID)
            refreshWarnings(ofWall: wallID)
            let nowAbstentions = applySavedWarnings(result.changedMeasurements)
            var lines = ["\(point.id) is \(Format.inches(check.residual)) off \(wallID) (limit \(Format.inches(check.tolerance)))"]
            let title: String
            switch result.outcome {
            case .confirmed:
                title = "\(wallID) checks out"
            case .unconfirmed:
                title = "\(wallID) not confirmed"
                lines += point.flags.map(\.message)
                lines.append("A check point with a warning can't confirm the wall. Mark another on its base.")
            case .failed:
                title = "\(wallID) failed its check"
            }
            let wallWarnings = manifest.walls[index].warnings
            if result.outcome == .confirmed {
                lines += wallWarnings.map(\.message)
            }
            if !nowAbstentions.isEmpty {
                lines.append("Now abstentions: \(nowAbstentions.joined(separator: ", "))")
            }
            announce(result.outcome == .confirmed && wallWarnings.isEmpty ? .accepted : .warning, title, lines)
        }
    }

    private func handleWallPoint(_ input: TapInput, tap: inout TapRecord) {
        guard let (wallID, wall) = activeWall else {
            refuse(&tap, "Not measured", Refusal(reason: "noWall", message: "There is no wall yet. Make one with the Wall tool."))
            return
        }
        let hit: WallHit
        do {
            hit = try wall.intersect(input.ray, gates: wallGates)
        } catch {
            refuse(&tap, "Not on \(wallID)", Self.explain(error))
            return
        }
        let point = addPoint(
            kind: .wall,
            position: hit.point,
            taps: [tap.id],
            onWall: PointRecord.OnWall(wall: wallID, range: hit.range, angleFromNormal: hit.angleFromNormal),
            flags: hit.withinContacts ? [] : [.outsideWallContacts],
            wallWarnings: ledger.wallStatus(wallID)?.warnings ?? []
        )
        tap.point = point.id
        announce(evidence(for: point).warnings.isEmpty ? .accepted : .warning, "\(point.id) on \(wallID)", describe(point))
    }

    private func handleTwoView(_ input: TapInput, tap: inout TapRecord) {
        guard let first = twoViewFirst else {
            twoViewFirst = TwoViewFirst(tapID: tap.id, keyframeID: input.snapshot.keyframeID, ray: input.ray)
            announce(.accepted, "First view saved", ["Step about 1 m sideways and mark the same feature."])
            return
        }
        guard first.keyframeID != input.snapshot.keyframeID else {
            refuse(&tap, "Need a second view", Refusal(
                reason: "sameFrame",
                message: "Both taps are on the same frame. Step sideways, freeze a new frame, then tap the feature."
            ))
            return
        }
        let result: Triangulation
        do {
            result = try Triangulation(first.ray, input.ray, gates: triangulationGates)
        } catch {
            // A small angle can be fixed from the first view; a miss means the taps disagree.
            if case .rayAngleTooSmall = error {} else { twoViewFirst = nil }
            refuse(&tap, "Not triangulated", Self.explain(error))
            return
        }
        twoViewFirst = nil
        let point = addPoint(
            kind: .twoView,
            position: result.point,
            taps: [first.tapID, tap.id],
            twoView: PointRecord.TwoView(
                firstTap: first.tapID,
                secondTap: tap.id,
                rayAngle: result.rayAngle,
                gap: result.gap,
                baseline: result.baseline,
                t1: result.t1,
                t2: result.t2
            ),
            flags: []
        )
        tap.point = point.id
        announce(.accepted, "\(point.id) from two views", describe(point))
    }

    /// Adds a ground point from the tap's raycast, or logs why there is none.
    private func makeGroundPoint(_ input: TapInput, tap: inout TapRecord) -> PointRecord? {
        guard let hit = input.ground else {
            refuse(&tap, "No ground there", Refusal(
                reason: "noGround",
                message: "ARKit found no ground along that ray. Aim at ground it has mapped, or move closer."
            ))
            return nil
        }
        let lookDown = input.ray.lookDownDegrees
        var flags: [MeasurementWarning] = []
        switch hit.surface {
        case .detectedPlane: break
        case .extendedPlane: flags.append(.extendedPlane)
        case .estimatedPlane: flags.append(.estimatedPlane)
        }
        if lookDown < Self.minimumGroundLookDown { flags.append(.shallowLookDown) }
        let point = addPoint(
            kind: .ground,
            position: hit.point,
            taps: [tap.id],
            ground: PointRecord.Ground(surface: hit.surface.rawValue, planeAnchor: hit.planeAnchor, lookDown: lookDown),
            flags: flags
        )
        tap.point = point.id
        return point
    }

    private func addPoint(
        kind: PointRecord.Kind,
        position: SIMD3<Double>,
        taps: [String],
        ground: PointRecord.Ground? = nil,
        onWall: PointRecord.OnWall? = nil,
        twoView: PointRecord.TwoView? = nil,
        flags: [MeasurementWarning],
        wallWarnings: [MeasurementWarning] = []
    ) -> PointRecord {
        let coordinates = activeWall.map { active in
            PointRecord.WallCoordinates(
                wall: active.id,
                along: active.wall.along(position),
                heightAboveGround: active.wall.heightAboveGround(position),
                offset: active.wall.offset(of: position),
                withinContacts: active.wall.containsAlong(active.wall.along(position))
            )
        }
        let point = PointRecord(
            id: "P\(manifest.points.count + 1)",
            kind: kind,
            position: position,
            taps: taps,
            ground: ground,
            onWall: onWall,
            twoView: twoView,
            wallCoordinates: coordinates,
            flags: flags,
            wallWarnings: wallWarnings
        )
        manifest.points.append(point)
        ledger.addPoint(point.id, at: position, flags: flags, onWall: onWall?.wall)
        return point
    }

    // MARK: - Measurements

    typealias MeasureTarget = MeasurementLedger.Target

    func values(from pointID: String, to target: MeasureTarget, referenceWall: String?) -> [MeasuredQuantity: Double] {
        ledger.values(from: pointID, to: target, referenceWall: referenceWall)
    }

    func addMeasurement(
        from pointID: String,
        to target: MeasureTarget,
        referenceWall: String?,
        compared quantity: MeasuredQuantity,
        tape: TapeReading?
    ) {
        let values = values(from: pointID, to: target, referenceWall: referenceWall)
        let id = "M\(manifest.measurements.count + 1)"
        guard let measured = values[quantity],
              let warnings = ledger.saveMeasurement(id, from: pointID, to: target, referenceWall: referenceWall, compared: quantity)
        else { return }
        let comparison = tape.map { TapeComparison(measured: measured, tape: $0.meters) }
        manifest.measurements.append(MeasurementRecord(
            id: id,
            time: ProcessInfo.processInfo.systemUptime,
            from: pointID,
            to: target.id,
            referenceWall: values[.alongWall] == nil ? nil : referenceWall,
            values: Dictionary(uniqueKeysWithValues: values.map { ($0.key.rawValue, $0.value) }),
            compared: quantity.rawValue,
            tape: tape.map { MeasurementRecord.Tape(feet: $0.feet, inches: $0.inches, meters: $0.meters) },
            errorMeters: comparison?.error,
            errorInches: comparison?.errorInches,
            warnings: warnings,
            accepted: warnings.isEmpty
        ))
        save()
        var lines = ["\(quantity.title): \(Format.length(measured))"]
        if let comparison {
            lines.append("Tape \(Format.length(comparison.tape)) · error \(Format.signedInches(comparison.error))")
        }
        lines += warnings.map(\.message)
        announce(warnings.isEmpty ? .accepted : .warning, warnings.isEmpty ? "\(id) saved" : "\(id) saved as an abstention", lines)
    }

    // MARK: - Warnings

    private func refreshWarnings(ofWall id: String) {
        guard let index = manifest.walls.firstIndex(where: { $0.id == id }), let status = ledger.wallStatus(id) else { return }
        manifest.walls[index].warnings = status.warnings
    }

    /// Copies the ledger's warnings onto saved measurement records after a wall check. Returns the
    /// ids that were accepted and are now abstentions.
    private func applySavedWarnings(_ ids: [String]) -> [String] {
        var nowAbstentions: [String] = []
        for id in ids {
            guard let index = manifest.measurements.firstIndex(where: { $0.id == id }), let warnings = ledger.savedWarnings(id) else {
                preconditionFailure("Measurement \(id) is in the ledger but not the manifest")
            }
            if manifest.measurements[index].accepted, !warnings.isEmpty { nowAbstentions.append(id) }
            manifest.measurements[index].warnings = warnings
            manifest.measurements[index].accepted = warnings.isEmpty
        }
        return nowAbstentions
    }

    /// A point's own warnings plus, for a point on a wall, that wall's current status.
    func evidence(for point: PointRecord) -> PointEvidence {
        guard let evidence = ledger.evidence(forPoint: point.id) else {
            preconditionFailure("Point \(point.id) is in the manifest but not the ledger")
        }
        return evidence
    }

    /// Everything that would make this measurement an abstention if saved now.
    func warnings(
        from pointID: String,
        to target: MeasureTarget,
        referenceWall: String?,
        compared quantity: MeasuredQuantity
    ) -> [MeasurementWarning] {
        ledger.warnings(from: pointID, to: target, referenceWall: referenceWall, compared: quantity) ?? []
    }

    // MARK: - Log helpers

    /// Why a tap was refused: a stable code for analysis, the text shown, and the numbers behind it.
    private struct Refusal {
        let reason: String
        let message: String
        var values: [String: Double] = [:]
    }

    /// Logs the refusal against the tap and shows it.
    private func refuse(_ tap: inout TapRecord, _ title: String, _ refusal: Refusal) {
        tap.refusal = logRefusal(tap: tap.id, refusal)
        announce(.refused, title, [refusal.message])
    }

    @discardableResult
    private func logRefusal(tap: String?, _ refusal: Refusal) -> String {
        let id = "R\(manifest.refusals.count + 1)"
        manifest.refusals.append(RefusalRecord(
            id: id,
            time: ProcessInfo.processInfo.systemUptime,
            tool: tool.rawValue,
            tap: tap,
            reason: refusal.reason,
            message: refusal.message,
            values: refusal.values
        ))
        return id
    }

    private func announce(_ tone: LabEvent.Tone, _ title: String, _ lines: [String]) {
        eventCount += 1
        lastEvent = LabEvent(id: eventCount, tone: tone, title: title, lines: lines)
    }

    private func describe(_ point: PointRecord) -> [String] {
        var lines: [String] = []
        if let ground = point.ground {
            lines.append("\(Format.degrees(ground.lookDown)) look-down · \(GroundHit.Surface(rawValue: ground.surface)?.label ?? ground.surface)")
        }
        if let onWall = point.onWall {
            lines.append("\(Format.length(onWall.range)) away · \(Format.degrees(onWall.angleFromNormal)) from straight on")
        }
        if let twoView = point.twoView {
            lines.append("Rays \(Format.degrees(twoView.rayAngle)) apart, missing by \(Format.inches(twoView.gap)) · \(Format.length(twoView.baseline)) step")
        }
        if let coordinates = point.wallCoordinates {
            lines.append("\(Format.length(coordinates.along)) along \(coordinates.wall) · \(Format.length(coordinates.heightAboveGround)) up · \(Format.length(coordinates.offset)) out")
        }
        lines += evidence(for: point).warnings.map(\.message)
        return lines
    }

    private static func explain(_ error: WallError) -> Refusal {
        switch error {
        case .contactsTooClose(let separation, let minimum):
            Refusal(
                reason: "contactsTooClose",
                message: "The contacts are \(Format.length(separation)) apart; at least \(Format.length(minimum)) is needed. Mark the second one farther along.",
                values: ["separation": separation, "minimum": minimum]
            )
        case .cameraInWallPlane(let offset, let minimum):
            Refusal(
                reason: "cameraInWallPlane",
                message: "You're standing in line with the wall, so its front side is unclear. Step out in front of it and mark the second contact again.",
                values: ["offset": offset, "minimum": minimum]
            )
        }
    }

    private static func explain(_ error: WallHitError) -> Refusal {
        switch error {
        case .grazing(let angle, let maximum):
            Refusal(
                reason: "grazingRay",
                message: "That ray meets the wall \(Format.degrees(angle)) from straight on; the limit is \(Format.degrees(maximum)). Stand more in front of the point.",
                values: ["angleFromNormal": angle, "maximum": maximum]
            )
        case .behindCamera(let t):
            Refusal(
                reason: "wallBehindCamera",
                message: "The wall plane is behind the camera along that ray. Face the wall and try again.",
                values: ["t": t]
            )
        }
    }

    private static func explain(_ error: TriangulationError) -> Refusal {
        switch error {
        case .rayAngleTooSmall(let angle, let minimum):
            Refusal(
                reason: "rayAngleTooSmall",
                message: "The two views are \(Format.degrees(angle)) apart; at least \(Format.degrees(minimum)) is needed. Step farther sideways and mark it again.",
                values: ["rayAngle": angle, "minimum": minimum]
            )
        case .behindCamera(let t1, let t2):
            Refusal(
                reason: "raysMeetBehindCamera",
                message: "The rays meet behind the camera, so the taps aren't on the same feature. Start over.",
                values: ["t1": t1, "t2": t2]
            )
        case .raysMiss(let gap, let maximum):
            Refusal(
                reason: "raysMiss",
                message: "The rays pass \(Format.inches(gap)) apart (limit \(Format.inches(maximum))), so the taps may be on different features. Start over.",
                values: ["gap": gap, "maximum": maximum]
            )
        }
    }
}

struct LabEvent: Identifiable, Equatable {
    enum Tone: Equatable {
        case accepted
        /// Accepted, but a flag or failed check applies.
        case warning
        case refused
    }

    let id: Int
    let tone: Tone
    let title: String
    let lines: [String]
}

extension MeasurementWarning {
    var message: String {
        switch self {
        case .estimatedPlane: "Estimated surface, not a found plane"
        case .extendedPlane: "Past the edge of the found plane"
        case .shallowLookDown: "Looking down less than 30°; tap from closer"
        case .outsideWallContacts: "Beyond the wall's two contacts"
        case .wallContactWarning: "One of the wall's contacts has a warning"
        case .wallNotValidated: "The wall has no clean check point yet"
        case .wallValidationFailed: "The wall failed its check"
        case .belowGround: "Below the wall's ground line"
        }
    }
}

extension GroundHit.Surface {
    var label: String {
        switch self {
        case .detectedPlane: "found plane"
        case .extendedPlane: "extended plane"
        case .estimatedPlane: "estimated surface"
        }
    }
}

extension MeasuredQuantity {
    var title: String {
        switch self {
        case .straight: "Straight line"
        case .horizontal: "Horizontal"
        case .vertical: "Height difference"
        case .alongWall: "Along the wall"
        case .gapToWall: "Gap to the wall"
        case .heightAboveGround: "Height above ground"
        }
    }
}

/// The hardware model, for example "iPhone15,4", so results can be grouped by phone.
private func deviceModelIdentifier() -> String {
    var info = utsname()
    uname(&info)
    return withUnsafeBytes(of: &info.machine) { bytes in
        String(decoding: bytes.prefix { $0 != 0 }, as: UTF8.self)
    }
}
