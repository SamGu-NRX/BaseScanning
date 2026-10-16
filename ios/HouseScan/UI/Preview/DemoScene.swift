import CoreGraphics
import Foundation
import simd

/// A made-up house wall for the UI demo: its geometry, a camera looking at it, and a picture of
/// it rendered through that same camera, so every overlay drawn with `CameraProjection` lands on
/// the right pixels. Synthetic on purpose: no real home appears in the repository.
///
/// World frame as in the contract (`.gravity`, meters, +y up). The wall runs along +x at z = 0,
/// the homeowner stands at +z, and the meter is at the origin of the wall frame.
@MainActor
enum DemoScene {
    static let wall = WallGeometry(
        meter: SIMD3(0, 1.45, 0),
        along: SIMD3(1, 0, 0),
        outward: SIMD3(0, 0, 1),
        groundY: 0,
        leftEnd: nil,
        rightEnd: nil
    )

    static let imageSize = SIMD2<Float>(1920, 1440)

    /// A phone held upright 5 m from the wall, a little right of the meter, tilted 18° down:
    /// the walk.
    static let projection = camera(at: SIMD3(0.6, 1.5, 5.0), pitchDegrees: 18, focal: 1100)
    /// The walk's phone tilted 10° up instead of 18° down, for "See it on your wall" with the
    /// sample spot low in the open camera (`-uiDemoSpotLow`). Worked out from this projection,
    /// not measured: the spot's middle lands at about y = 634 pt on a 402 × 874 pt iPhone 17
    /// screen, where the default pose puts it at about 380 pt, under the folded AX5 card.
    static let lowSpotProjection = camera(at: SIMD3(0.6, 1.5, 5.0), pitchDegrees: -10, focal: 1100)
    /// Standing 2.4 m back and aiming at the meter: finding it.
    static let meterProjection = camera(at: SIMD3(0.05, 1.5, 2.4), pitchDegrees: 0, focal: 1400)
    /// Close to the meter for its photo.
    static let closeUpProjection = camera(at: SIMD3(0, 1.5, 0.9), pitchDegrees: 0, focal: 1400)

    /// An upright phone at `position` looking at the wall (-z), tilted down by `pitchDegrees`.
    /// Camera space: +x is down in the world (the sensor image is rotated for portrait), +y is
    /// right, +z points back toward the homeowner.
    private static func camera(at position: SIMD3<Float>, pitchDegrees: Float, focal: Float) -> CameraProjection {
        let pitch = pitchDegrees * .pi / 180
        let x = SIMD4<Float>(0, -cos(pitch), sin(pitch), 0)
        let y = SIMD4<Float>(1, 0, 0, 0)
        let z = SIMD4<Float>(0, sin(pitch), cos(pitch), 0)
        return CameraProjection(
            cameraToWorld: simd_float4x4(columns: (x, y, z, SIMD4(position, 1))),
            intrinsics: SIMD4(focal, focal, 960, 720),
            imageSize: imageSize
        )
    }

    /// Where the homeowner stands, on the ground.
    static let standingPoint = SIMD3<Float>(0.6, 0, 5.0)

    /// A walking path on the ground from in front of the homeowner to a spot `out` meters from
    /// the wall at `s`, bending gently like a person would walk it.
    static func path(toward s: Float, out: Float = 1.3) -> [SIMD3<Float>] {
        let start = SIMD3<Float>(standingPoint.x, 0, standingPoint.z - 0.9)
        let end = SIMD3<Float>(s, 0, out)
        let bend = SIMD3<Float>(start.x + (end.x - start.x) * 0.25, 0, start.z + (end.z - start.z) * 0.6)
        return stride(from: Float(0), through: 1, by: 0.125).map { t in
            let a = start + (bend - start) * t
            let b = bend + (end - bend) * t
            return a + (b - a) * t
        }
    }

    // Layout of the made-up wall, in meters of s along the wall.
    static let gasMeterSpan: ClosedRange<Float> = -1.55 ... -1.25
    static let windowSpan: ClosedRange<Float> = 2.2...3.0
    static let windowHeights: ClosedRange<Float> = 0.95...2.15
    static let acSpan: ClosedRange<Float> = 3.4...4.1
    static let wallRange: ClosedRange<Float> = -3.2...4.4

    // MARK: Picture

    /// The still frames: rendered once each, landscape, unrotated, like sensor images.
    static let image: CGImage? = render(through: projection)
    static let meterImage: CGImage? = render(through: meterProjection)
    static let closeUpImage: CGImage? = render(through: closeUpProjection)

    private static func render(through projection: CameraProjection) -> CGImage? {
        let width = Int(imageSize.x), height = Int(imageSize.y)
        guard let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return nil }
        // Draw in sensor pixel coordinates: origin top-left, y down.
        context.translateBy(x: 0, y: CGFloat(height))
        context.scaleBy(x: 1, y: -1)

        let painter = Painter(context: context, projection: projection)
        painter.fillAll(CGColor(srgbRed: 0.72, green: 0.83, blue: 0.93, alpha: 1))

        // Wall, foundation, siding and eave.
        painter.quad(s: -6...7, y: 0...3.3, z: 0, color: rgb(0.88, 0.83, 0.74))
        var y: Float = 0.42
        while y < 3.25 {
            painter.line(from: SIMD3(-6, y, 0.002), to: SIMD3(7, y, 0.002), color: rgb(0.74, 0.69, 0.60), width: 3)
            painter.line(from: SIMD3(-6, y - 0.012, 0.002), to: SIMD3(7, y - 0.012, 0.002), color: rgb(0.95, 0.91, 0.84), width: 2)
            y += 0.19
        }
        painter.quad(s: -6...7, y: 0...0.28, z: 0.004, color: rgb(0.62, 0.61, 0.58))
        painter.polygon([SIMD3(-6, 3.3, 0), SIMD3(7, 3.3, 0), SIMD3(7, 3.55, 0.45), SIMD3(-6, 3.55, 0.45)], color: rgb(0.46, 0.43, 0.40))

        // Ground: lawn, then a gravel bed along the wall.
        painter.polygon([SIMD3(-9, 0, 0), SIMD3(10, 0, 0), SIMD3(10, 0, 4.85), SIMD3(-9, 0, 4.85)], color: rgb(0.42, 0.53, 0.33))
        painter.polygon([SIMD3(-9, 0.001, 0), SIMD3(10, 0.001, 0), SIMD3(10, 0.001, 0.5), SIMD3(-9, 0.001, 0.5)], color: rgb(0.66, 0.62, 0.55))
        for i in 0..<220 {
            // Pebbles: a fixed pseudo-random scatter so the frame is identical on every run.
            let fx = Float((i * 7919) % 1000) / 1000, fz = Float((i * 104729) % 1000) / 1000
            let p = SIMD3<Float>(-4 + fx * 9, 0.002, 0.03 + fz * 0.44)
            painter.dot(p, radius: 0.018, color: i % 3 == 0 ? rgb(0.52, 0.49, 0.44) : rgb(0.78, 0.75, 0.70))
        }

        // Downspout.
        painter.quad(s: -3.02 ... -2.94, y: 0...3.3, z: 0.07, color: rgb(0.96, 0.96, 0.95))

        // Window with frame and glass.
        painter.quad(s: windowSpan, y: windowHeights, z: 0.01, color: rgb(0.97, 0.97, 0.96))
        painter.quad(s: (windowSpan.lowerBound + 0.07)...(windowSpan.upperBound - 0.07),
                     y: (windowHeights.lowerBound + 0.07)...(windowHeights.upperBound - 0.07), z: 0.012, color: rgb(0.45, 0.56, 0.66))
        painter.line(from: SIMD3(2.6, 1.02, 0.014), to: SIMD3(2.6, 2.08, 0.014), color: rgb(0.97, 0.97, 0.96), width: 8)

        // Gas meter with its riser pipe.
        painter.quad(s: -1.43 ... -1.37, y: 0...0.4, z: 0.1, color: rgb(0.78, 0.66, 0.20))
        painter.quad(s: gasMeterSpan, y: 0.35...0.72, z: 0.15, color: rgb(0.86, 0.80, 0.52))

        // Electric meter: conduit, can, glass dial.
        painter.quad(s: -0.035...0.035, y: 0.28...1.25, z: 0.05, color: rgb(0.55, 0.56, 0.57))
        painter.quad(s: -0.16...0.16, y: 1.22...1.72, z: 0.12, color: rgb(0.66, 0.68, 0.70))
        painter.disc(center: SIMD3(0, 1.5, 0.125), radius: 0.1, color: rgb(0.86, 0.90, 0.93))
        painter.disc(center: SIMD3(0, 1.5, 0.126), radius: 0.07, color: rgb(0.30, 0.33, 0.36))

        // AC unit on a pad: side, top, front.
        let ac0 = acSpan.lowerBound, ac1 = acSpan.upperBound
        painter.polygon([SIMD3(ac0, 0, 0.25), SIMD3(ac0, 0, 0.95), SIMD3(ac0, 0.8, 0.95), SIMD3(ac0, 0.8, 0.25)], color: rgb(0.62, 0.63, 0.63))
        painter.polygon([SIMD3(ac0, 0.8, 0.25), SIMD3(ac1, 0.8, 0.25), SIMD3(ac1, 0.8, 0.95), SIMD3(ac0, 0.8, 0.95)], color: rgb(0.82, 0.83, 0.83))
        painter.polygon([SIMD3(ac0, 0, 0.95), SIMD3(ac1, 0, 0.95), SIMD3(ac1, 0.8, 0.95), SIMD3(ac0, 0.8, 0.95)], color: rgb(0.74, 0.75, 0.75))
        var grille: Float = 0.1
        while grille < 0.75 {
            painter.line(from: SIMD3(ac0 + 0.05, grille, 0.952), to: SIMD3(ac1 - 0.05, grille, 0.952), color: rgb(0.58, 0.59, 0.59), width: 2)
            grille += 0.05
        }
        return context.makeImage()
    }

    /// A small upright picture of the meter for the close-up acknowledgment.
    static let meterThumbnail: CGImage? = {
        let side = 240
        guard let context = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return nil }
        context.setFillColor(rgb(0.88, 0.83, 0.74))
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        context.setFillColor(rgb(0.66, 0.68, 0.70))
        context.fill(CGRect(x: 55, y: 30, width: 130, height: 180))
        context.setFillColor(rgb(0.86, 0.90, 0.93))
        context.fillEllipse(in: CGRect(x: 75, y: 90, width: 90, height: 90))
        context.setFillColor(rgb(0.30, 0.33, 0.36))
        context.fillEllipse(in: CGRect(x: 90, y: 105, width: 60, height: 60))
        return context.makeImage()
    }()

    private static func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> CGColor {
        CGColor(srgbRed: r, green: g, blue: b, alpha: 1)
    }

    /// Draws world-space shapes through the demo camera.
    @MainActor
    private struct Painter {
        let context: CGContext
        let projection: CameraProjection

        func pixel(_ world: SIMD3<Float>) -> CGPoint? {
            projection.imagePixel(for: world).map { CGPoint(x: CGFloat($0.x), y: CGFloat($0.y)) }
        }

        func fillAll(_ color: CGColor) {
            context.setFillColor(color)
            context.fill(CGRect(x: 0, y: 0, width: context.width, height: context.height))
        }

        func polygon(_ corners: [SIMD3<Float>], color: CGColor) {
            let points = corners.compactMap(pixel)
            guard points.count == corners.count, points.count >= 3 else { return }
            context.beginPath()
            context.addLines(between: points)
            context.closePath()
            context.setFillColor(color)
            context.fillPath()
        }

        func quad(s: ClosedRange<Float>, y: ClosedRange<Float>, z: Float, color: CGColor) {
            polygon([
                SIMD3(s.lowerBound, y.lowerBound, z), SIMD3(s.upperBound, y.lowerBound, z),
                SIMD3(s.upperBound, y.upperBound, z), SIMD3(s.lowerBound, y.upperBound, z),
            ], color: color)
        }

        func line(from a: SIMD3<Float>, to b: SIMD3<Float>, color: CGColor, width: CGFloat) {
            guard let p = pixel(a), let q = pixel(b) else { return }
            context.setStrokeColor(color)
            context.setLineWidth(width)
            context.strokeLineSegments(between: [p, q])
        }

        func disc(center: SIMD3<Float>, radius: Float, color: CGColor) {
            let corners = (0..<32).map { i -> SIMD3<Float> in
                let a = Float(i) / 32 * 2 * .pi
                return center + SIMD3(cos(a) * radius, sin(a) * radius, 0)
            }
            polygon(corners, color: color)
        }

        func dot(_ center: SIMD3<Float>, radius: Float, color: CGColor) {
            let corners = (0..<10).map { i -> SIMD3<Float> in
                let a = Float(i) / 10 * 2 * .pi
                return center + SIMD3(cos(a) * radius, 0, sin(a) * radius)
            }
            polygon(corners, color: color)
        }
    }
}
