import SwiftUI
import simd

/// What the homeowner has marked, pinned onto the camera image: the meter, the wall ends and
/// the marked features.
///
/// Things that stand on the wall get the prototype's corner brackets (after RoomPlan's
/// outlines) with a short caption: the meter in Signal blue, everything else in the dots'
/// Hologram white. A marked thing is settled, so its bracket is solid; the prototype's dashed,
/// unsettled bracket has no counterpart here. Driveways and fences stay dashed lines on the
/// ground, and the wall ends white posts.
struct WallMarksOverlay: View {
    var projection: CameraProjection
    var wall: WallGeometry
    var features: [MarkedFeature]
    var wallBandHeight: Float

    /// The dots' Hologram, #E6ECF4.
    static let hologram = Color(red: 0xE6 / 255, green: 0xEC / 255, blue: 0xF4 / 255)

    /// Display sizes (width, height) for things marked with one tap, in meters: a bracket needs
    /// a box, and one tap gives only the middle. Typical sizes, not measurements; the export uses
    /// its own nominal width (`ScanEngine.project`).
    static let meterBox = SIMD2<Float>(0.3, 0.4)
    static let gasMeterBox = SIMD2<Float>(0.3, 0.4)
    static let acUnitBox = SIMD2<Float>(0.6, 0.6)

    var body: some View {
        Canvas { context, size in
            let geometry = WallProjection(projection: projection, wall: wall, size: size)
            drawEnds(in: &context, geometry)
            for feature in features {
                drawFeature(feature, in: &context, geometry)
            }
            let up = SIMD3<Float>(0, 1, 0)
            let along = wall.along(atS: 0)
            if let corners = box(center: wall.meter, along: along, up: up, size: Self.meterBox, geometry) {
                drawBracket(corners, color: Palette.signal, caption: "Electric meter", in: &context)
            }
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private func drawEnds(in context: inout GraphicsContext, _ geometry: WallProjection) {
        for end in [wall.leftEnd, wall.rightEnd].compactMap(\.self) {
            guard let bottom = geometry.point(s: end, height: 0), let top = geometry.point(s: end, height: wallBandHeight) else { continue }
            var line = Path()
            line.move(to: bottom)
            line.addLine(to: top)
            context.stroke(line, with: .color(.black.opacity(0.35)), style: StrokeStyle(lineWidth: 7, lineCap: .round))
            context.stroke(line, with: .color(.white), style: StrokeStyle(lineWidth: 4, lineCap: .round))
            let cap = CGRect(x: bottom.x - 7, y: bottom.y - 7, width: 14, height: 14)
            context.fill(Path(ellipseIn: cap), with: .color(.white))
        }
    }

    private func drawFeature(_ feature: MarkedFeature, in context: inout GraphicsContext, _ geometry: WallProjection) {
        let caption = ScanCopy.name(feature.kind)
        let up = SIMD3<Float>(0, 1, 0)
        switch feature.kind {
        // A battery or box is outlined where it meets the wall, the only part of it measured.
        case .door, .window, .battery, .elecBox:
            guard let bottom = feature.bottom, let top = feature.top else { return }
            let corners = [
                wall.world(s: feature.span.lowerBound, height: top, out: 0.01),
                wall.world(s: feature.span.upperBound, height: top, out: 0.01),
                wall.world(s: feature.span.upperBound, height: bottom, out: 0.01),
                wall.world(s: feature.span.lowerBound, height: bottom, out: 0.01),
            ].compactMap(geometry.point)
            if corners.count == 4 { drawBracket(corners, color: Self.hologram, caption: caption, in: &context) }
        case .gasMeter, .acUnit:
            guard let point = feature.points.first else { return }
            let s = (feature.span.lowerBound + feature.span.upperBound) / 2
            let size = feature.kind == .gasMeter ? Self.gasMeterBox : Self.acUnitBox
            // An AC unit stands on the ground, and most taps on one land there (#163): its bracket
            // stands on the ground under the tap rather than centred on it, half under the floor.
            let center = feature.kind == .acUnit ? SIMD3(point.x, wall.groundY + size.y / 2, point.z) : point
            if let corners = box(center: center, along: wall.along(atS: s), up: up, size: size, geometry) {
                drawBracket(corners, color: Self.hologram, caption: caption, in: &context)
            }
        case .driveway, .fence:
            let points = feature.points.compactMap(geometry.point)
            guard points.count >= 2 else { return }
            var line = Path()
            line.addLines(points)
            context.stroke(line, with: .color(.black.opacity(0.3)), style: StrokeStyle(lineWidth: 8, lineCap: .round))
            context.stroke(line, with: .color(.white), style: StrokeStyle(lineWidth: 4, lineCap: .round, dash: [10, 7]))
            for point in points {
                let dot = CGRect(x: point.x - 6, y: point.y - 6, width: 12, height: 12)
                context.fill(Path(ellipseIn: dot.insetBy(dx: -2, dy: -2)), with: .color(.black.opacity(0.25)))
                context.fill(Path(ellipseIn: dot), with: .color(.white))
            }
        }
    }

    /// Four corners on screen, clockwise from top-left, of a box facing out of the wall around
    /// `center`; nil when any corner is behind the camera.
    private func box(center: SIMD3<Float>, along: SIMD3<Float>, up: SIMD3<Float>, size: SIMD2<Float>, _ geometry: WallProjection) -> [CGPoint]? {
        let w = along * (size.x / 2), h = up * (size.y / 2)
        let corners = [center - w + h, center + w + h, center + w - h, center - w - h].compactMap(geometry.point)
        return corners.count == 4 ? corners : nil
    }

    /// The prototype's bracket: four L-shaped corners 6 pt outside the box, each arm 18 pt or 40%
    /// of its edge, with a 4 pt rounded bend, over a soft dark halo so it holds on a bright wall;
    /// the caption sits 13 pt above the top edge.
    private func drawBracket(_ box: [CGPoint], color: Color, caption: String, in context: inout GraphicsContext) {
        let cx = box.map(\.x).reduce(0, +) / 4, cy = box.map(\.y).reduce(0, +) / 4
        let corners = box.map { p -> CGPoint in
            let dx = p.x - cx, dy = p.y - cy
            let length = max((dx * dx + dy * dy).squareRoot(), 0.001)
            return CGPoint(x: p.x + dx / length * 6, y: p.y + dy / length * 6)
        }
        var path = Path()
        for i in corners.indices {
            let corner = corners[i], previous = corners[(i + 3) % 4], next = corners[(i + 1) % 4]
            func toward(_ other: CGPoint) -> CGPoint {
                let dx = other.x - corner.x, dy = other.y - corner.y
                let length = max((dx * dx + dy * dy).squareRoot(), 0.001)
                let arm = min(18, 0.4 * length)
                return CGPoint(x: corner.x + dx / length * arm, y: corner.y + dy / length * arm)
            }
            path.move(to: toward(previous))
            path.addArc(tangent1End: corner, tangent2End: toward(next), radius: 4)
            path.addLine(to: toward(next))
        }
        context.stroke(path, with: .color(.black.opacity(0.28)), style: StrokeStyle(lineWidth: 4.5, lineCap: .round, lineJoin: .round))
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

        let top = CGPoint(x: (corners[0].x + corners[1].x) / 2, y: min(corners[0].y, corners[1].y) - 13)
        var layer = context
        layer.addFilter(.shadow(color: .black.opacity(0.45), radius: 6, y: 1))
        layer.draw(
            Text(caption).font(.system(size: 13, weight: .semibold, design: .rounded)).foregroundStyle(.white),
            at: top, anchor: .center)
    }
}
