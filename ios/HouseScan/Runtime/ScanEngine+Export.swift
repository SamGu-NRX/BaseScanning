import Foundation
import HouseScanKit
import OSLog
import simd

extension ScanEngine {
    /// scene.json for the current scan (contract C1), without mesh measurements: the autopilot's
    /// gate copy. The upload captures the scene once and measures the mesh against that capture
    /// (`captureUpload`, `UploadPackaging`).
    func sceneJSON() throws -> Data {
        try SceneExport.jsonData(sceneInput())
    }

    /// The scan's scene as it stands now, without mesh measurements, read in this one turn.
    ///
    /// The scene frame is the AR world frame moved down so the ground at the wall is y = 0: the
    /// server reads heights (meter.pos y, pose translations) as height above that ground, and
    /// scene.json has no field for the ground's height otherwise.
    func sceneInput() throws -> SceneInput {
        guard let map = coverage else { throw ExportError.noWall }
        let wall = map.wall
        let drop = SIMD3<Float>(0, wall.groundY, 0)
        // The corners carry their pieces' sources (`markNextWall`); the meter's piece, its own
        // (`meterLineSource`, set on the map in `markMeter`).
        let sceneWall = SceneWall(
            meter: wall.meter - drop, outward: wall.outward, groundY: 0, leftCorners: wall.leftCorners, rightCorners: wall.rightCorners,
            source: wall.source)

        // SceneExport rejects negative heights, a top below a bottom, ground points behind the
        // wall and a zero-length driveway edge, and taps can produce each of them once the ground
        // or the meter anchor moves after the tap (a window tapped wholly below a guessed ground,
        // a fence foot that ends up behind the refined wall line). Such marks are clamped to the
        // nearest valid shape here and logged. A driveway or fence with nothing valid left fails
        // the export instead (`ExportError.markCollapsed`): dropping it would send the ground
        // near it as seen and clear, which can pass its clearance check with the hazard unsent.
        let features: [SceneFeature] = try state.features.map { feature in
            let points = feature.points.map { $0 - drop }
            switch feature.kind {
            case .door, .window, .battery, .elecBox:
                let heights = [feature.bottom ?? 0, feature.top ?? 0]
                let bottom = max(0, heights.min() ?? 0)
                let top = max(bottom, heights.max() ?? 0)
                if bottom != feature.bottom || top != feature.top {
                    RuntimeLog.engine.info("export: \(feature.kind.rawValue, privacy: .public) heights \(feature.bottom ?? .nan)...\(feature.top ?? .nan) clamped to \(bottom)...\(top)")
                }
                if let box = feature.kind.sceneBox {
                    return .box(kind: box, span: feature.span, bottom: bottom, top: top)
                }
                return .opening(kind: feature.kind == .door ? .door : .window, span: feature.span, bottom: bottom, top: top,
                                operable: feature.kind == .window ? feature.opens : nil)
            case .gasMeter:
                return .pointObject(kind: .gasMeter, tap: points.first ?? wall.meter - drop, bottom: nil, top: nil)
            case .acUnit:
                return .pointObject(kind: .ac, tap: points.first ?? wall.meter - drop, bottom: nil, top: nil)
            case .fence:
                return .fence(foot: try Self.groundLine(feature, points, wall: sceneWall))
            case .driveway:
                return .driveway(edge: try Self.groundLine(feature, points, wall: sceneWall))
            }
        }

        let keyframes = store.keyframes.map { stored -> SceneKeyframe in
            var pose = stored.camera.cameraToWorld
            pose.columns.3.y -= wall.groundY
            return SceneKeyframe(
                id: stored.id, cameraToWorld: pose, intrinsics: stored.camera.intrinsics,
                w: Int(stored.camera.imageSize.x), h: Int(stored.camera.imageSize.y), img: stored.fileName
            )
        }

        // A guessed ground puts the same error into every height in the scene. scene.json has no
        // field for "the ground was estimated", so the error bars say it instead.
        let groundError: Float? = groundMeasured ? nil : Self.estimatedGroundError
        let input = SceneInput(
            wall: sceneWall,
            baselineS: Self.exportSpan(map),
            meterPlusMinus: groundError,
            meterPlane: meterPlaneSource,
            objectPlusMinus: groundError,
            features: features,
            coverage: SceneCoverage(
                map, leftEndMarked: wallEndKinds[.left] == .limit, rightEndMarked: wallEndKinds[.right] == .limit
            ),
            keyframes: keyframes,
            stills: store.stills,
            // The mesh's measurements are filled in once measured (`UploadPackaging`). Written
            // without plus_minus_ft: the server takes its mesh error for both.
            // Unanswered exports like "Not sure": no patch, and the server reports the surface unknown.
            groundType: state.groundAnswer.flatMap(Self.sceneGroundType)
        )
        return input
    }

    /// Everything one upload sends, read in one main-actor turn: the scene, the wall in world
    /// meters, the LiDAR mesh and the capture packet's inputs. Nothing after this rereads the
    /// engine's geometry for this upload, so a ground or anchor correction while the mesh is
    /// measured can't mix two walls into its scene or packet.
    func captureUpload() throws -> UploadPackaging<PacketInputs> {
        let scene = try sceneInput()
        guard let map = coverage else { throw ExportError.noWall }
        let meshSnapshot = liveCapture?.meshSnapshot()
        return UploadPackaging(scene: scene, worldWall: map.wall, mesh: meshSnapshot?.mesh, packet: packetInputs(mesh: meshSnapshot))
    }

    static func sceneGroundType(_ answer: GroundAnswer) -> SceneGroundType? {
        switch answer {
        case .notSure: nil
        case .type(.lawn): .lawn
        case .type(.mulch): .mulch
        case .type(.gravel): .gravel
        case .type(.concrete): .concrete
        case .type(.drive): .drive
        case .type(.deck): .deck
        }
    }

    /// How the line of the meter's piece of wall was found, for scene.json's `walls[].source`.
    ///
    /// Live, `markMeter` puts the wall through the raycast's hit point, facing the normal of the
    /// plane it hit. On a detected plane (an ARPlaneAnchor, `.existingPlaneGeometry`) the normal
    /// is the anchor's and the hit point lies on the anchor's geometry, so both the line's
    /// direction and its distance from the camera are the plane's: `plane`. The tap only picks
    /// where along that line the meter is. On an estimated plane there is no anchor: ARKit fits a
    /// plane to the feature points around the tapped pixel for that one raycast, which is what an
    /// AR tap is, and the schema's `plane` means detected planes, so it is `tap`. Nothing in the
    /// capture takes a wall line from the LiDAR mesh (`MeshProbe` only measures in front of and
    /// above a line already set), so no piece is `mesh`.
    ///
    /// A replay's wall is `tap`: a recorded measure-lab wall runs through two tapped ground
    /// contacts (its `contacts`), and a wall assumed from the trajectory was never measured at
    /// all, which no source describes; replays run only in tests and demos.
    var meterLineSource: WallLineSource {
        replay == nil ? Self.lineSource(of: meterPlaneSource) : .tap
    }

    /// The source of a wall line put through a live vertical-plane raycast's hit, facing the hit
    /// plane's normal (`meterLineSource` has the reasons).
    static func lineSource(of plane: MeterPlaneSource) -> WallLineSource {
        switch plane {
        case .detectedPlane: .plane
        case .estimatedPlane: .tap
        }
    }

    /// The stretch of wall the scene describes, meters of s: between the marked ends, or out to
    /// what was seen (at least 1 m) on a side without one, always containing the meter.
    nonisolated static func exportSpan(_ map: CoverageMap) -> ClosedRange<Float> {
        let seen = map.seenExtent
        let low = min(map.leftEnd ?? min(seen?.lowerBound ?? -1, -1), -0.1)
        let high = max(map.rightEnd ?? max(seen?.upperBound ?? 1, 1), 0.1)
        return low...high
    }

    /// Error of the chest-height ground guess (camera height minus 1.4 m), meters. Phones held
    /// for scanning sit roughly 1.1 to 1.7 m up, so ±0.3 m. A hypothesis; no measured spread exists.
    static let estimatedGroundError: Float = 0.3

    /// A ground point on or behind the wall line moved to 1 mm in front of it, keeping its s. The
    /// millimetre keeps float round-off from turning an on-the-line point into a negative depth.
    private static func inFront(of wall: SceneWall, _ point: SIMD3<Float>) -> SIMD3<Float> {
        let c = wall.wallCoordinates(of: point)
        return c.out >= 0.001 ? point : wall.world(s: c.s, height: c.height, out: 0.001)
    }

    /// The two ground points of a driveway edge or a fence foot, each moved in front of the wall.
    /// Throws `markCollapsed` when they are not two points 1 cm apart in plan: a wall line refined
    /// past both taps puts them on one point. SceneExport's own degenerate-edge tolerance is
    /// 1e-3 ft; 1 cm keeps well clear of it.
    private static func groundLine(_ feature: MarkedFeature, _ points: [SIMD3<Float>], wall: SceneWall) throws(ExportError) -> [SIMD3<Float>] {
        let line = points.map { inFront(of: wall, $0) }
        guard line.count == 2, simd_distance(SIMD2(line[0].x, line[0].z), SIMD2(line[1].x, line[1].z)) > 0.01 else {
            RuntimeLog.engine.error("export: \(feature.kind.rawValue, privacy: .public) \(feature.id.uuidString, privacy: .public) has \(points.count) taps that don't make a line; asking for it to be marked again")
            throw .markCollapsed(feature.kind)
        }
        return line
    }

    enum ExportError: Error, CustomStringConvertible {
        case noWall
        /// A driveway or fence whose taps no longer make a line. The homeowner has to mark it
        /// again; `UploadFailure.packaging(_:)` says so.
        case markCollapsed(FeatureKind)

        var description: String {
            switch self {
            case .noWall: "There is no wall to export yet."
            case .markCollapsed(let kind): "The \(kind.rawValue) mark's taps don't make a line."
            }
        }
    }

    /// The server's result in the terms the screens use (meters, wall coordinates).
    func presentation(of result: PlacementResult, isSample: Bool) -> ResultPresentation {
        let sceneWall = coverage.map {
            SceneWall(meter: $0.wall.meter, outward: $0.wall.outward, groundY: $0.wall.groundY, leftCorners: $0.wall.leftCorners, rightCorners: $0.wall.rightCorners)
        }
        // A view is capturable only when a gap request can be built from it: a band item needs
        // its span (and a facing item its out_ft, which a walk can reach), a past_end item its
        // side. Otherwise the button would do nothing. A request the homeowner already skipped
        // or answered with something overhead stays with the installer.
        let shown = Self.presentation(of: result, isSample: isSample, wall: sceneWall) { [self, gapPlanner, coverage, skippedGaps] item in
            gapPlanner.plan(for: item, leftEnd: coverage?.leftEnd, rightEnd: coverage?.rightEnd, limitEnds: coverage?.limitEnds ?? [])
                .map { plan in !skippedGaps.contains { plan.asksForSameView(as: $0) } && captureCanSettle(plan) } ?? false
        }
        // "Can't get there" ended both sides before either was walked (#76): the server placed
        // the spot on the meter tap's wall line alone, which nothing confirmed.
        guard walkRefusals.neitherSideWalked(leftEnd: coverage?.leftEnd, rightEnd: coverage?.rightEnd) else { return shown }
        RuntimeLog.engine.info("result: neither side of the meter was walked; showing no spot")
        return shown.withWallNotMeasured()
    }

    /// `presentation(of:isSample:)` without an engine, for the UI demo: `wall` is the scan's wall
    /// in world meters (nil draws no route off a sample's frame), `capturable` says whether a
    /// requested view can be taken now.
    static func presentation(
        of result: PlacementResult, isSample: Bool, wall sceneWall: SceneWall?,
        capturable: (PlacementMissingEvidence) -> Bool
    ) -> ResultPresentation {
        let meters: (Double) -> Float = { Float($0 * 0.3048) }

        let spot = result.spot.map(Self.batterySpot)
        let nearestSpot = result.nearestConsidered.map(Self.batterySpot)

        var route: [SIMD2<Float>] = []
        if let cable = result.route {
            let height = meters(cable.heightFt)
            // Each point as an offset from the meter: the result's meter is the spot's centre
            // minus its offset from the meter. The answer to this scan is in the scene's plan
            // frame (the AR world's x and z in feet, scene.json's frame; result.schema.json's
            // meter_offset_ft is "in the scene frame's axes"), so the offsets are put on the
            // current wall chain by `chainS`, which follows the route round corners and adds a
            // vertex at each so the drawn line bends there. Offsets rather than the points
            // themselves keep the route on the meter's anchor as it moves, like the spot's s.
            // The bundled sample is in a frame of its own, unrelated to this wall, so its route
            // is measured along the sample spot's own direction, as before.
            if isSample {
                if let placed = result.spot {
                    let meterPlan = placed.center - placed.meterOffsetFt
                    route = cable.polyline.map { point in
                        let d = point - meterPlan
                        return SIMD2(meters(d.x * placed.along.x + d.y * placed.along.y), height)
                    }
                }
            } else if let sceneWall {
                // A route comes with a spot (result.schema.json); without one, the meter as it is now.
                let meterNow = SIMD2(Double(sceneWall.meter.x), Double(sceneWall.meter.z)) * SceneUnits.feetPerMeter
                let meterPlan = result.spot.map { $0.center - $0.meterOffsetFt } ?? meterNow
                route = sceneWall.chainS(ofPlanOffsetsFeet: cable.polyline.map { $0 - meterPlan }).map { SIMD2($0, height) }
            }
        }

        let checks = result.checks.map { check in
            CheckRow(
                id: check.id, title: check.label, outcome: Self.outcome(check.outcome), reason: check.reason,
                needsPerson: check.needsPerson,
                measured: check.measuredFt.map(meters), threshold: check.thresholdFt.map(meters), plusMinus: check.plusMinusFt.map(meters),
                comparison: check.comparison.map(Self.comparison),
                reviewThreshold: check.reviewThresholdFt.map(meters), reviewBandApplies: check.reviewBandApplies,
                settledBy: result.evidenceIndex(settling: check.id).map(Self.missingID)
            )
        }

        // The spot carries the battery's size, or without one the nearest spot, which the server
        // sized the same way. With neither, `sweepZones` gives no zones: the result keeps its
        // wall, summary and reasons, and no view draws floor the server never sized.
        let sized = (spot ?? nearestSpot).map {
            ResultMarkLayout.BatterySize(width: $0.span.upperBound - $0.span.lowerBound, depth: $0.depth)
        }
        let zones = ResultMarkLayout.sweepZones(
            starts: result.sweep.map { SIMD2(meters($0.startFt.x), meters($0.startFt.y)) }, battery: sized)
        // One zone per run, or none.
        let clearances = zip(result.sweep, zones).enumerated().map { index, pair in
            let (run, zone) = pair
            return ClearanceZone(
                id: "sweep-\(index)",
                label: (run.failing + run.unsure).joined(separator: ", "),
                outcome: Self.outcome(run.outcome),
                span: zone.span,
                depth: zone.depth
            )
        }

        let missing = result.missingEvidence.enumerated().map { index, item in
            MissingEvidence(
                id: Self.missingID(index), text: item.message,
                capturable: capturable(item),
                checkIDs: item.checks ?? []
            )
        }

        // The unexplored end nearer the meter than the spot, if any, not the first past_end
        // request: the server lists those left first, whether or not a spot past the end could
        // beat the one it chose (issue #83).
        let unseen = result.closerUnseenEnd().map { end in
            UnseenEnd(side: end.side == .left ? .left : .right, s: meters(end.sFt))
        }

        return ResultPresentation(
            decision: Self.decision(result.decision),
            // The solver ends the summary with the policy notice; the screen shows it apart.
            summary: result.summaryWithoutNotice,
            policyApproved: result.policy.autoApprove,
            rulesNotice: result.policy.notice.flatMap { $0.isEmpty ? nil : $0 },
            rulesHash: result.policy.rulesShortHash,
            spot: spot,
            nearestSpot: nearestSpot,
            nearestFailingCheck: result.nearestFailure?.id,
            cableRoute: route,
            cableLength: result.route.map { meters($0.lengthFt) },
            checks: checks,
            clearances: clearances,
            missing: missing,
            marksNotUsed: result.objectsNotUsed.map(\.message),
            unseenEnd: unseen,
            isSample: isSample,
            rulesSHA256: result.policy.rulesSHA256
        )
    }

    static func missingID(_ index: Int) -> String {
        "missing-\(index)"
    }

    /// A server spot in wall terms: its stretch of s, size and gap to the wall, meters.
    ///
    /// Placed by its `span_ft`, its stretch in s along the wall chain, which the screens turn into
    /// world points piece by piece (`WallGeometry.world(s:)`), so a spot round a corner lands on
    /// the right piece and still moves with the meter's anchor. The meter offset split along the
    /// spot's own `along` gave s only on the meter's piece: past a corner it measured along the
    /// other piece's direction and drew the spot on the meter's wall. Both s and the depth below
    /// are frame-free, so the bundled sample (in its own frame) still means the same on any wall.
    ///
    /// The size is the server's (`span_ft`, `depth_ft`, `height_ft`), never a battery size of the
    /// app's own, for `nearest_considered` as for `spot`. There is no fallback for a missing one:
    /// decoding requires every dimension (`PlacementSpot.init(from:)`), so an answer without one
    /// fails with its path, such as `nearest_considered.depth_ft`, and the upload screen says the
    /// answer couldn't be read (`ResultReadingTests.aNearestSpotWithoutADimensionIsRefused`).
    static func batterySpot(_ placed: PlacementSpot) -> BatterySpot {
        let meters: (Double) -> Float = { Float($0 * 0.3048) }
        let low = meters(min(placed.spanFt.x, placed.spanFt.y))
        let high = meters(max(placed.spanFt.x, placed.spanFt.y))
        let depth = meters(placed.depthFt)
        // How far the footprint's centre stands out from its back edge (back-left and back-right
        // corners come first), along the spot's own outward: the gap to the wall is what exceeds
        // half the depth. The server puts the back on the wall line. Decoding refuses a footprint
        // without exactly four corners.
        let d = placed.center - (placed.footprint[0] + placed.footprint[1]) / 2
        let centerOut = meters(d.x * placed.outward.x + d.y * placed.outward.y)
        return BatterySpot(
            span: low...high,
            depth: depth, height: meters(placed.heightFt),
            offsetFromWall: max(0, centerOut - depth / 2)
        )
    }

    static func outcome(_ outcome: PlacementOutcome) -> CheckOutcome {
        switch outcome {
        case .pass: .pass
        case .fail: .fail
        case .unsure: .unsure
        }
    }

    static func comparison(_ comparison: PlacementComparison) -> RuleComparison {
        switch comparison {
        case .atLeast: .atLeast
        case .atMost: .atMost
        }
    }

    static func decision(_ decision: PlacementDecision) -> ResultPresentation.Decision {
        switch decision {
        case .pass: .pass
        case .manualReview: .manualReview
        case .reject: .reject
        }
    }
}
