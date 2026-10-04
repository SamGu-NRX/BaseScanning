import HouseScanKit
import RealityKit
import SwiftUI
import UIKit

// The result as a small 3D model of the homeowner's own wall: the meter, the battery standing
// where the server placed it, the cable run between them and the clearance zones tinted by
// outcome. Seeing the battery on your own wall is the moment the scan pays off, so the view opens
// with a short camera sweep that settles on the battery, then lets the homeowner turn it.
//
// A spot that isn't a clean fit gets no battery, only a dashed outline of its footprint on the
// ground: amber or red for a spot that might stand in the meter's working space
// (`ResultPresentation.spotIsClean`), red for the closest spot of a result without one.
//
// Everything is built in the frame of the meter's piece of wall, not in ARKit world coordinates:
// x along that piece (+ to the right), y = height above the ground, z = meters out from the wall
// toward the viewer. The meter sits at (0, meterHeight, 0). Each straight piece of the wall
// (`WallGeometry.cornerSegments`) is a container entity placed and turned where that piece is,
// and what stands on a piece is built flat inside it: x = s, y = height, z = out. So on a straight
// wall x is s, and a walk round a corner shows the corner, with the marks, the battery and the
// clearance zones on the piece they belong to, as the AR view places them.

struct ResultScene3D: View {
    let wall: WallGeometry
    let result: ResultPresentation
    let features: [MarkedFeature]
    let wallHeight: Float

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    // Camera orbit, degrees. Starts at the resting pose so a Reduce Motion user never sees a jump.
    @State private var yaw = CameraPose.rest.yaw
    @State private var pitch = CameraPose.rest.pitch
    @State private var zoom: Float = 1
    @State private var dragOrigin: SIMD2<Float>?
    @State private var zoomOrigin: Float?
    @State private var introPlayed = false
    @State private var introInterrupted = false

    init(wall: WallGeometry, result: ResultPresentation, features: [MarkedFeature], wallHeight: Float) {
        self.wall = wall
        self.result = result
        self.features = features
        self.wallHeight = wallHeight
    }

    var body: some View {
        RealityView { content in
            content.camera = .virtual
            content.add(buildDiorama())
            let camera = PerspectiveCamera()
            camera.name = Self.cameraName
            camera.camera.fieldOfViewInDegrees = 40
            camera.camera.near = 0.1
            content.add(camera)
            placeCamera(camera)
        } update: { content in
            guard let camera = content.entities.first(where: { $0.name == Self.cameraName }) else { return }
            placeCamera(camera)
        }
        .background(
            LinearGradient(
                colors: [Color(red: 0xDC / 255, green: 0xE6 / 255, blue: 0xF2 / 255),
                         Color(red: 0xF2 / 255, green: 0xF1 / 255, blue: 0xEC / 255)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
        .gesture(orbitGesture)
        .simultaneousGesture(zoomGesture)
        .task { await playIntro() }
        .accessibilityElement()
        .accessibilityLabel("3D view of your wall")
        .accessibilityValue(accessibilitySummary)
        .accessibilityHint("Drag to turn the view")
    }

    // MARK: - Camera

    private static let cameraName = "result-orbit-camera"

    private enum CameraPose {
        static let intro = (yaw: Float(-35), pitch: Float(30))
        static let rest = (yaw: Float(18), pitch: Float(18))
        static let yawRange: ClosedRange<Float> = -70...70
        static let pitchRange: ClosedRange<Float> = 8...45
        static let zoomRange: ClosedRange<Float> = 0.6...1.6
        static let introSeconds = 1.6
    }

    /// The point the camera circles: the battery's (or the closest spot's) center when there is
    /// one, else the meter.
    private var orbitTarget: SIMD3<Float> {
        if let spot = result.spot ?? result.nearestSpot {
            let mid = (spot.span.lowerBound + spot.span.upperBound) / 2
            return piece(atS: mid).point(s: mid, height: spot.height / 2, out: spot.offsetFromWall + spot.depth / 2)
        }
        return SIMD3(0, wall.meterHeight / 2, 0)
    }

    /// The camera's yaw 0 faces the piece of wall the orbit target stands on, degrees, so a battery
    /// round a corner is seen from its front. The same spot as `orbitTarget`, the closest one
    /// tried when there is no spot, so a rejected candidate round a corner is seen from its front too.
    private var orbitHeading: Float {
        let s = ResultMarkLayout.focusS(spot: result.spot?.span, nearest: result.nearestSpot?.span)
        let outward = piece(atS: s).outward
        return atan2(outward.x, outward.z) * 180 / .pi
    }

    private var orbitDistance: Float {
        max(5, (wallExtent.upperBound - wallExtent.lowerBound) * 0.9) / zoom
    }

    private func placeCamera(_ camera: Entity) {
        let yawRad = (yaw + orbitHeading) * .pi / 180
        let pitchRad = pitch * .pi / 180
        let direction = SIMD3(sin(yawRad) * cos(pitchRad), sin(pitchRad), cos(yawRad) * cos(pitchRad))
        camera.look(at: orbitTarget, from: orbitTarget + direction * orbitDistance, relativeTo: nil)
    }

    /// Sweeps from a high three-quarter view down to the resting pose, easing out so it lands
    /// softly on the battery. Plays once; a drag stops it where it is and takes over from there.
    private func playIntro() async {
        guard !introPlayed else { return }
        introPlayed = true
        guard !reduceMotion else { return }
        yaw = CameraPose.intro.yaw
        pitch = CameraPose.intro.pitch
        let clock = ContinuousClock()
        let start = clock.now
        while !introInterrupted {
            let t = Float(min((clock.now - start) / .seconds(CameraPose.introSeconds), 1))
            let eased = 1 - pow(1 - t, 3)
            yaw = CameraPose.intro.yaw + (CameraPose.rest.yaw - CameraPose.intro.yaw) * eased
            pitch = CameraPose.intro.pitch + (CameraPose.rest.pitch - CameraPose.intro.pitch) * eased
            if t >= 1 { return }
            do { try await Task.sleep(for: .milliseconds(16)) } catch { return }
        }
    }

    private var orbitGesture: some Gesture {
        DragGesture(minimumDistance: 4)
            .onChanged { value in
                introInterrupted = true
                let origin = dragOrigin ?? SIMD2(yaw, pitch)
                dragOrigin = origin
                yaw = (origin.x - Float(value.translation.width) * 0.3).clamped(to: CameraPose.yawRange)
                pitch = (origin.y + Float(value.translation.height) * 0.2).clamped(to: CameraPose.pitchRange)
            }
            .onEnded { _ in dragOrigin = nil }
    }

    private var zoomGesture: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                let origin = zoomOrigin ?? zoom
                zoomOrigin = origin
                zoom = (origin * Float(value.magnification)).clamped(to: CameraPose.zoomRange)
            }
            .onEnded { _ in zoomOrigin = nil }
    }

    // MARK: - Scene

    /// The wall's s range: the marked ends, or everything the result mentions plus 1.5 m of wall
    /// on each side for an end the homeowner didn't mark.
    private var wallExtent: ClosedRange<Float> {
        var spans: [ClosedRange<Float>] = [0...0]
        if let spot = result.spot { spans.append(spot.span) }
        if let spot = result.nearestSpot { spans.append(spot.span) }
        spans += features.map(\.span)
        spans += result.clearances.map(\.span)
        spans += result.cableRoute.map { $0.x...$0.x }
        let left = wall.leftEnd ?? (spans.map(\.lowerBound).min() ?? 0) - 1.5
        let right = wall.rightEnd ?? (spans.map(\.upperBound).max() ?? 0) + 1.5
        return min(left, right)...max(left, right)
    }

    private func buildDiorama() -> Entity {
        let root = Entity()
        let pieces = self.pieces
        let holders = pieces.map { piece in
            let holder = Entity()
            holder.position = piece.origin
            holder.orientation = piece.orientation
            root.addChild(holder)
            return holder
        }
        func holder(atS s: Float) -> Entity { holders[pieceIndex(atS: s, in: pieces)] }

        for (piece, holder) in zip(pieces, holders) { addWallAndGround(piece, pieces: pieces, to: holder) }

        root.addChild(box(width: 0.3, height: 0.4, depth: 0.15,
                          center: SIMD3(0, wall.meterHeight, 0.075), material: matte(SceneColor.meter)))
        let dial = ModelEntity(mesh: .generateCylinder(height: 0.01, radius: 0.07), materials: [matte(SceneColor.signal)])
        dial.orientation = simd_quatf(angle: .pi / 2, axis: SIMD3(1, 0, 0))
        dial.position = SIMD3(0, wall.meterHeight + 0.04, 0.155)
        root.addChild(dial)

        // A mark goes on the piece its middle is on. A fence or driveway is a run along the wall,
        // and one that crosses a corner is drawn in parts, one on each piece it covers, as the
        // clearance zones are: drawn whole on its middle's piece it ran straight on past the
        // corner (#129). The end pieces reach on past the drawn wall, so no part is lost there.
        for feature in features {
            guard feature.kind == .fence || feature.kind == .driveway else {
                addFeature(feature, to: holder(atS: (feature.span.lowerBound + feature.span.upperBound) / 2))
                continue
            }
            var parts = 0
            for (index, (piece, holder)) in zip(pieces, holders).enumerated() {
                let low = max(feature.span.lowerBound, index == 0 ? -.infinity : piece.span.lowerBound)
                let high = min(feature.span.upperBound, index == pieces.count - 1 ? .infinity : piece.span.upperBound)
                guard high > low else { continue }
                var part = feature
                part.span = low...high
                addFeature(part, to: holder)
                parts += 1
            }
            if parts == 0 { addFeature(feature, to: holder(atS: (feature.span.lowerBound + feature.span.upperBound) / 2)) }
        }

        for (index, zone) in result.clearances.enumerated() {
            var material = UnlitMaterial(color: SceneColor.zone(zone.outcome))
            material.blending = .transparent(opacity: .init(floatLiteral: 0.35))
            // Stacked zones sit a few millimeters apart so overlapping ones don't flicker.
            let lift = ResultMarkLayout.zoneLift(index: index, base: Self.zoneBase)
            // A zone that runs past a corner is drawn in parts, one on each piece it covers.
            for (piece, holder) in zip(pieces, holders) {
                let low = max(zone.span.lowerBound, piece.span.lowerBound)
                let high = min(zone.span.upperBound, piece.span.upperBound)
                guard high > low else { continue }
                holder.addChild(plane(width: high - low, depth: zone.depth,
                                      center: SIMD3((low + high) / 2, lift, zone.depth / 2), material: material))
            }
        }

        addCable(to: root, pieces: pieces)
        // Each on the piece its middle is on, as the battery is.
        func middle(_ spot: BatterySpot) -> Float { (spot.span.lowerBound + spot.span.upperBound) / 2 }
        // Above every clearance zone, however many there are, so none covers it.
        let outlineLift = ResultMarkLayout.outlineLift(zoneCount: result.clearances.count, base: Self.zoneBase,
                                                       thickness: FootprintOutline.thickness, minimum: 0.02)
        if let spot = result.spot {
            switch ResultMarkLayout.spotMark(spotIsClean: result.spotIsClean) {
            case .battery:
                addBattery(spot, to: holder(atS: middle(spot)))
            case .outline:
                holder(atS: middle(spot)).addChild(
                    FootprintOutline.build(spot, color: SceneColor.ink(result.workingSpaceOutcome), lift: outlineLift))
            }
        }
        if let nearest = result.nearestSpot {
            holder(atS: middle(nearest)).addChild(FootprintOutline.build(nearest, color: SceneColor.ink(.fail), lift: outlineLift))
        }
        addLights(to: root)
        return root
    }

    // MARK: - Wall pieces

    /// A straight piece of the wall in the model frame. Flat coordinates (x = s, y = height,
    /// z = out) map to the model as `origin + orientation.act(flat)`; the meter's piece is the
    /// identity.
    private struct WallPiece {
        /// The stretch of s it covers, clipped to the drawn extent.
        var span: ClosedRange<Float>
        var origin: SIMD3<Float>
        var orientation: simd_quatf
        /// A corner the walk followed is at this end, not the end of the drawn wall.
        var cornerLow: Bool
        var cornerHigh: Bool

        func point(s: Float, height: Float, out: Float) -> SIMD3<Float> {
            origin + orientation.act(SIMD3(s, height, out))
        }

        var outward: SIMD3<Float> { orientation.act(SIMD3(0, 0, 1)) }
    }

    /// A world direction in the model frame: the meter's piece's along, up and outward.
    private func modelDirection(_ v: SIMD3<Float>) -> SIMD3<Float> {
        SIMD3(simd_dot(v, wall.along), v.y, simd_dot(v, wall.outward))
    }

    /// The meter's piece and each piece past a corner, left to right, clipped to `wallExtent`.
    private var pieces: [WallPiece] {
        let extent = wallExtent
        let low = wall.cornerSegments.map(\.span.upperBound).filter { $0 <= 0 }.max() ?? -.infinity
        let high = wall.cornerSegments.map(\.span.lowerBound).filter { $0 >= 0 }.min() ?? .infinity
        var all: [(span: ClosedRange<Float>, origin: SIMD3<Float>, orientation: simd_quatf)] = [
            (low...high, .zero, simd_quatf(ix: 0, iy: 0, iz: 0, r: 1)),
        ]
        for segment in wall.cornerSegments {
            let along = simd_normalize(modelDirection(segment.along))
            let outward = simd_normalize(modelDirection(segment.outward))
            let orientation = simd_quatf(simd_float3x3(columns: (along, SIMD3(0, 1, 0), outward)))
            all.append((segment.span, modelDirection(segment.anchor) - along * segment.anchorS, orientation))
        }
        return all.compactMap { piece in
            let lower = max(piece.span.lowerBound, extent.lowerBound)
            let upper = min(piece.span.upperBound, extent.upperBound)
            guard upper > lower else { return nil }
            return WallPiece(span: lower...upper, origin: piece.origin, orientation: piece.orientation,
                             cornerLow: piece.span.lowerBound > extent.lowerBound,
                             cornerHigh: piece.span.upperBound < extent.upperBound)
        }
        .sorted { $0.span.lowerBound < $1.span.lowerBound }
    }

    private func pieceIndex(atS s: Float, in pieces: [WallPiece]) -> Int {
        if let index = pieces.firstIndex(where: { $0.span.contains(s) }) { return index }
        let distances = pieces.map { max($0.span.lowerBound - s, s - $0.span.upperBound, 0) }
        return distances.indices.min { distances[$0] < distances[$1] } ?? 0
    }

    private func piece(atS s: Float) -> WallPiece {
        let pieces = self.pieces
        return pieces.isEmpty
            ? WallPiece(span: 0...0, origin: .zero, orientation: simd_quatf(ix: 0, iy: 0, iz: 0, r: 1), cornerLow: false, cornerHigh: false)
            : pieces[pieceIndex(atS: s, in: pieces)]
    }

    private static let wallThickness: Float = 0.12

    /// Whether the neighbouring piece across the corner at `s` runs behind this piece's face (an
    /// outside corner, the usual one walking round a house) rather than out in front of it.
    private func turnsAway(_ piece: WallPiece, at s: Float, towardLower: Bool, pieces: [WallPiece]) -> Bool {
        let step: Float = towardLower ? -0.5 : 0.5
        let neighbour = pieces[pieceIndex(atS: s + step, in: pieces)]
        let beyond = neighbour.point(s: s + step, height: 0, out: 0) - piece.point(s: s, height: 0, out: 0)
        return simd_dot(beyond, piece.outward) < 0
    }

    /// The piece's wall and the ground strip in front of it. At an outside corner the ground runs
    /// on past the corner to fill the wedge in front of both faces; at an inside corner the wall
    /// runs on by its thickness to close the notch behind the join. Neither pokes out elsewhere.
    private func addWallAndGround(_ piece: WallPiece, pieces: [WallPiece], to holder: Entity) {
        func extensions(corner: Bool, at s: Float, towardLower: Bool) -> (wall: Float, ground: Float) {
            guard corner else { return (0, 0.5) }
            return turnsAway(piece, at: s, towardLower: towardLower, pieces: pieces) ? (0, Self.groundDepth) : (Self.wallThickness, 0)
        }
        let low = extensions(corner: piece.cornerLow, at: piece.span.lowerBound, towardLower: true)
        let high = extensions(corner: piece.cornerHigh, at: piece.span.upperBound, towardLower: false)
        let wallLow = piece.span.lowerBound - low.wall, wallHigh = piece.span.upperBound + high.wall
        holder.addChild(box(width: wallHigh - wallLow, height: wallHeight, depth: Self.wallThickness,
                            center: SIMD3((wallLow + wallHigh) / 2, wallHeight / 2, -Self.wallThickness / 2), material: matte(SceneColor.wall)))
        let groundLow = piece.span.lowerBound - low.ground, groundHigh = piece.span.upperBound + high.ground
        holder.addChild(plane(width: groundHigh - groundLow, depth: Self.groundDepth,
                              center: SIMD3((groundLow + groundHigh) / 2, 0, Self.groundDepth / 2), material: matte(SceneColor.ground)))
    }

    private static let groundDepth: Float = 2.5

    private func addFeature(_ feature: MarkedFeature, to root: Entity) {
        let width = max(feature.span.upperBound - feature.span.lowerBound, 0.05)
        let centerX = (feature.span.lowerBound + feature.span.upperBound) / 2
        switch feature.kind {
        case .window, .door:
            let bottom = feature.bottom ?? (feature.kind == .door ? 0 : 0.9)
            let top = feature.top ?? (feature.kind == .door ? 2.03 : 2.1)
            let height = max(top - bottom, 0.05)
            root.addChild(box(width: width, height: height, depth: 0.02,
                              center: SIMD3(centerX, bottom + height / 2, 0), material: matte(SceneColor.opening)))
        case .gasMeter:
            let bottom = feature.bottom ?? 0.15
            root.addChild(box(width: 0.3, height: 0.35, depth: 0.2,
                              center: SIMD3(centerX, bottom + 0.175, 0.1), material: matte(SceneColor.gas)))
        case .acUnit:
            let back = feature.out ?? 0.3
            root.addChild(box(width: 0.8, height: 0.8, depth: 0.8,
                              center: SIMD3(centerX, 0.4, back + 0.4), material: matte(SceneColor.meter)))
        case .driveway:
            root.addChild(plane(width: width, depth: Self.groundDepth,
                                center: SIMD3(centerX, 0.003, Self.groundDepth / 2), material: matte(SceneColor.driveway)))
        case .fence:
            root.addChild(box(width: width, height: 1.2, depth: 0.04,
                              center: SIMD3(centerX, 0.6, feature.out ?? 2.0), material: matte(SceneColor.fence)))
        }
    }

    private func addBattery(_ spot: BatterySpot, to root: Entity) {
        let width = max(spot.span.upperBound - spot.span.lowerBound, 0.1)
        let centerX = (spot.span.lowerBound + spot.span.upperBound) / 2
        let front = spot.offsetFromWall + spot.depth
        var body = PhysicallyBasedMaterial()
        body.baseColor = .init(tint: SceneColor.battery)
        body.roughness = 0.35
        let corner = min(0.03, min(width, spot.height, spot.depth) / 4)
        let unit = ModelEntity(
            mesh: .generateBox(width: width, height: spot.height, depth: spot.depth, cornerRadius: corner),
            materials: [body]
        )
        unit.position = SIMD3(centerX, spot.height / 2, spot.offsetFromWall + spot.depth / 2)
        root.addChild(unit)
        // A vertical light bar on the front face so the unit reads as "the battery", not a box.
        root.addChild(box(width: 0.04, height: spot.height * 0.7, depth: 0.006,
                          center: SIMD3(centerX, spot.height / 2, front + 0.003), material: UnlitMaterial(color: SceneColor.signal)))
    }

    /// The height of the lowest clearance zone; the rest stack a step apart above it
    /// (`ResultMarkLayout.zoneLift`).
    private static let zoneBase: Float = 0.006

    /// Point by point on the piece each point's s is on: the route has a vertex at every corner it
    /// passes (`SceneWall.chainS`), so each stretch lies on one piece and it bends with the wall.
    private func addCable(to root: Entity, pieces: [WallPiece]) {
        let points = result.cableRoute.map { pieces.isEmpty ? SIMD3($0.x, $0.y, 0.03) : pieces[pieceIndex(atS: $0.x, in: pieces)].point(s: $0.x, height: $0.y, out: 0.03) }
        let material = matte(SceneColor.signal)
        let radius: Float = 0.015
        for (from, to) in zip(points, points.dropFirst()) {
            let length = simd_distance(from, to)
            guard length > 0.001 else { continue }
            let segment = ModelEntity(mesh: .generateCylinder(height: length, radius: radius), materials: [material])
            segment.position = (from + to) / 2
            segment.orientation = simd_quatf(from: SIMD3(0, 1, 0), to: (to - from) / length)
            root.addChild(segment)
        }
        // Round joints so bends don't show a notch.
        for point in points.dropFirst().dropLast() {
            let joint = ModelEntity(mesh: .generateSphere(radius: radius), materials: [material])
            joint.position = point
            root.addChild(joint)
        }
    }

    private func addLights(to root: Entity) {
        // Key light from upper left front, casting the battery's shadow onto the ground.
        let key = DirectionalLight()
        key.light.intensity = 2500
        key.shadow = DirectionalLightComponent.Shadow(maximumDistance: orbitDistance * 1.6 + 6, depthBias: 1)
        key.look(at: .zero, from: SIMD3(-3, 6, 4), relativeTo: nil)
        root.addChild(key)
        // Weak fill from the right so faces turned away from the key light don't go black.
        let fill = DirectionalLight()
        fill.light.intensity = 600
        fill.look(at: .zero, from: SIMD3(4, 2, 3), relativeTo: nil)
        root.addChild(fill)
    }

    private func box(width: Float, height: Float, depth: Float, center: SIMD3<Float>, material: any RealityKit.Material) -> ModelEntity {
        let entity = ModelEntity(mesh: .generateBox(width: width, height: height, depth: depth), materials: [material])
        entity.position = center
        return entity
    }

    /// A horizontal rectangle facing up.
    private func plane(width: Float, depth: Float, center: SIMD3<Float>, material: any RealityKit.Material) -> ModelEntity {
        let entity = ModelEntity(mesh: .generatePlane(width: width, depth: depth), materials: [material])
        entity.position = center
        return entity
    }

    private func matte(_ color: UIColor) -> SimpleMaterial {
        SimpleMaterial(color: color, roughness: 0.85, isMetallic: false)
    }

    // MARK: - Accessibility

    /// Lengths are spelled out: VoiceOver reads "ft" and "in" as letters (B-16).
    private var accessibilitySummary: String {
        guard let spot = result.spot else {
            guard let nearest = result.nearestSpot else { return "No battery spot shown" }
            return "No battery spot. The closest spot tried is outlined \(Self.spokenPlace(nearest.span))"
        }
        guard result.spotIsClean else {
            return "Possible battery spot outlined \(Self.spokenPlace(spot.span)), for an installer to confirm"
        }
        var parts: [String]
        if spot.span.lowerBound > 0 {
            parts = ["Proposed battery spot \(Distance.spoken(spot.span.lowerBound)) right of your meter"]
        } else if spot.span.upperBound < 0 {
            parts = ["Proposed battery spot \(Distance.spoken(-spot.span.upperBound)) left of your meter"]
        } else {
            parts = ["Proposed battery spot below your meter"]
        }
        if let cable = result.cableLength {
            parts.append("cable \(Distance.spoken(cable))")
        }
        return parts.joined(separator: ", ")
    }

    private static func spokenPlace(_ span: ClosedRange<Float>) -> String {
        if span.lowerBound > 0 { return "\(Distance.spoken(span.lowerBound)) right of your meter" }
        if span.upperBound < 0 { return "\(Distance.spoken(-span.upperBound)) left of your meter" }
        return "below your meter"
    }
}

/// A dashed outline of a footprint on the ground: where a battery would stand, drawn without
/// one. Dashes start at each corner and the gaps stretch to fit, so every corner reads
/// (`ResultMarkLayout.dashes`). Built flat on a piece of wall: x = s, y = height, z = out.
@MainActor
enum FootprintOutline {
    /// How tall each dash is, for `ResultMarkLayout.outlineLift`.
    static let thickness: Float = 0.004
    private static let dash: Float = 0.1
    private static let dashGap: Float = 0.06
    private static let lineWidth: Float = 0.03

    /// `spot`'s footprint, its dashes centered `lift` above the ground.
    static func build(_ spot: BatterySpot, color: UIColor, lift: Float) -> Entity {
        build(s: spot.span, out: spot.offsetFromWall...(spot.offsetFromWall + spot.depth), color: color, lift: lift)
    }

    static func build(s: ClosedRange<Float>, out: ClosedRange<Float>, color: UIColor, lift: Float) -> Entity {
        let root = Entity()
        let material = UnlitMaterial(color: color)
        let left = s.lowerBound, right = s.upperBound, back = out.lowerBound, front = out.upperBound
        let edges: [(SIMD2<Float>, SIMD2<Float>)] = [
            (SIMD2(left, back), SIMD2(right, back)), (SIMD2(right, back), SIMD2(right, front)),
            (SIMD2(right, front), SIMD2(left, front)), (SIMD2(left, front), SIMD2(left, back)),
        ]
        for (from, to) in edges {
            let length = simd_distance(from, to)
            guard length > 0.01 else { continue }
            let direction = (to - from) / length
            let dashes = ResultMarkLayout.dashes(along: length, dash: dash, gap: dashGap)
            let horizontal = abs(direction.x) > abs(direction.y)
            for start in dashes.starts {
                let middle = from + direction * (start + dashes.length / 2)
                let entity = ModelEntity(
                    mesh: .generateBox(width: horizontal ? dashes.length : lineWidth, height: thickness,
                                       depth: horizontal ? lineWidth : dashes.length),
                    materials: [material]
                )
                entity.position = SIMD3(middle.x, lift, middle.y)
                root.addChild(entity)
            }
        }
        return root
    }
}

/// The result for "See it on your wall" (`LiveCapture.showResult`): the battery, the cable run and
/// the clearance zones, the same pieces as `BatteryOverlay` draws over a replay. A spot that isn't
/// a clean fit gets the outline of its footprint instead of a battery, as on the result card. Built from
/// `WallGeometry` in world axes with the meter at the origin, so it turns a corner where the wall does.
@MainActor
enum ResultARModel {
    /// The LiDAR mesh sits a centimeter or two off the real wall and ground, and hides whatever is
    /// behind it: anything flush with either would be cut into.
    static let meshClearance: Float = 0.03

    private static let unitName = "result-ar-battery"

    static func build(wall: WallGeometry, result: ResultPresentation) -> Entity {
        let root = Entity()
        func local(_ s: Float, _ height: Float, _ out: Float) -> SIMD3<Float> {
            wall.world(s: s, height: height, out: out) - wall.meter
        }
        for (index, zone) in result.clearances.enumerated() {
            var material = UnlitMaterial(color: SceneColor.zone(zone.outcome))
            material.blending = .transparent(opacity: .init(floatLiteral: 0.35))
            let width = zone.span.upperBound - zone.span.lowerBound
            let middle = zone.span.lowerBound + width / 2
            // Stacked zones sit a few millimeters apart so overlapping ones don't flicker.
            let entity = ModelEntity(mesh: .generatePlane(width: width, depth: zone.depth), materials: [material])
            entity.position = local(middle, ResultMarkLayout.zoneLift(index: index, base: meshClearance), zone.depth / 2)
            entity.orientation = facing(wall, atS: middle)
            root.addChild(entity)
        }
        let cable = result.cableRoute.map { local($0.x, $0.y, meshClearance) }
        let material = SimpleMaterial(color: SceneColor.signal, roughness: 0.85, isMetallic: false)
        let radius: Float = 0.015
        for (from, to) in zip(cable, cable.dropFirst()) {
            let length = simd_distance(from, to)
            guard length > 0.001 else { continue }
            let segment = ModelEntity(mesh: .generateCylinder(height: length, radius: radius), materials: [material])
            segment.position = (from + to) / 2
            segment.orientation = simd_quatf(from: SIMD3(0, 1, 0), to: (to - from) / length)
            root.addChild(segment)
        }
        for point in cable.dropFirst().dropLast() {
            let joint = ModelEntity(mesh: .generateSphere(radius: radius), materials: [material])
            joint.position = point
            root.addChild(joint)
        }
        if let spot = result.spot, ResultMarkLayout.spotMark(spotIsClean: result.spotIsClean) == .outline {
            let middle = (spot.span.lowerBound + spot.span.upperBound) / 2
            let back = max(spot.offsetFromWall, meshClearance)
            // Flat on the spot's piece of wall, with s measured from the spot's middle.
            let outline = FootprintOutline.build(
                s: (spot.span.lowerBound - middle)...(spot.span.upperBound - middle), out: back...(back + spot.depth),
                color: SceneColor.ink(result.workingSpaceOutcome),
                lift: ResultMarkLayout.outlineLift(zoneCount: result.clearances.count, base: meshClearance,
                                                   thickness: FootprintOutline.thickness)
            )
            outline.position = local(middle, 0, 0)
            outline.orientation = facing(wall, atS: middle)
            root.addChild(outline)
        } else if let spot = result.spot {
            let width = max(spot.span.upperBound - spot.span.lowerBound, 0.1)
            let middle = (spot.span.lowerBound + spot.span.upperBound) / 2
            let back = max(spot.offsetFromWall, meshClearance)
            // Origin at the middle of the footprint on the ground, so `rise` grows it upward.
            let unit = Entity()
            unit.name = unitName
            unit.position = local(middle, 0, back + spot.depth / 2)
            unit.orientation = facing(wall, atS: middle)
            var paint = PhysicallyBasedMaterial()
            paint.baseColor = .init(tint: SceneColor.battery)
            paint.roughness = 0.35
            let corner = min(0.03, min(width, spot.height, spot.depth) / 4)
            let body = ModelEntity(
                mesh: .generateBox(width: width, height: spot.height, depth: spot.depth, cornerRadius: corner),
                materials: [paint]
            )
            body.position = SIMD3(0, spot.height / 2, 0)
            body.components.set(GroundingShadowComponent(castsShadow: true))
            unit.addChild(body)
            let bar = ModelEntity(
                mesh: .generateBox(width: 0.04, height: spot.height * 0.7, depth: 0.006),
                materials: [UnlitMaterial(color: SceneColor.signal)]
            )
            bar.position = SIMD3(0, spot.height / 2, spot.depth / 2 + 0.003)
            unit.addChild(bar)
            root.addChild(unit)
        }
        return root
    }

    /// Lifts the battery out of the ground. Call once the model is in the scene. With Reduce Motion
    /// the battery stays where `build` put it, at its final size: a shorter rise is still a rise.
    static func rise(_ model: Entity) {
        guard !UIAccessibility.isReduceMotionEnabled, let unit = model.findEntity(named: unitName) else { return }
        let settled = unit.transform
        unit.scale = SIMD3(1, 0.02, 1)
        unit.move(to: settled, relativeTo: unit.parent, duration: 0.7, timingFunction: .easeOut)
    }

    /// x along the wall, y up, z out from the wall toward the homeowner.
    private static func facing(_ wall: WallGeometry, atS s: Float) -> simd_quatf {
        let up = SIMD3<Float>(0, 1, 0)
        let outward = simd_normalize(wall.outward(atS: s))
        return simd_quatf(simd_float3x3(columns: (simd_normalize(simd_cross(up, outward)), up, outward)))
    }
}

/// Fixed scene colors. They match the asset-catalog palette in light mode; a model of a house
/// in daylight shouldn't change color with the phone's dark mode.
private enum SceneColor {
    static var signal: UIColor { rgb(0x1F66F2) }
    static var wall: UIColor { rgb(0xE3D7C3) }
    static var ground: UIColor { rgb(0x6F7F5E) }
    static var meter: UIColor { rgb(0x8E949C) }
    static var battery: UIColor { rgb(0xF4F5F7) }
    static var opening: UIColor { rgb(0x7F93A8) }
    static var gas: UIColor { rgb(0xD9B84A) }
    static var driveway: UIColor { rgb(0x5A5E63) }
    static var fence: UIColor { rgb(0x9A8466) }

    /// `Palette.outcomeInk`: the darker outcome colors, for lines that must stand out on the
    /// light ground rather than tint it.
    static func ink(_ outcome: CheckOutcome) -> UIColor {
        switch outcome {
        case .pass: UIColor(red: 0.08, green: 0.50, blue: 0.26, alpha: 1)
        case .unsure: UIColor(red: 0.56, green: 0.36, blue: 0.0, alpha: 1)
        case .fail: UIColor(red: 0.76, green: 0.16, blue: 0.12, alpha: 1)
        }
    }

    /// A clearance zone's tint, as `Palette.zone`: where the server's sweep passes, the
    /// proposal's blue, not a green that reads as ground confirmed clear.
    static func zone(_ outcome: CheckOutcome) -> UIColor {
        switch outcome {
        case .pass: signal
        case .unsure: rgb(0xF5B53D)
        case .fail: rgb(0xFF5A4E)
        }
    }

    private static func rgb(_ hex: UInt32) -> UIColor {
        UIColor(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
