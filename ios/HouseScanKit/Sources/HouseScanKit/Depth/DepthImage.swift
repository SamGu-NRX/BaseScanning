import Foundation
import simd

/// One LiDAR depth frame, taken with the same camera pose as the photo it came with.
///
/// Values are z-depth: the distance along the camera's view axis (-z in camera space), the way
/// ARKit's `sceneDepth` is unprojected (pixel ray with z = -1, times the depth). Pixel
/// coordinates follow `CameraFrame`: (0, 0) is the image's top-left corner and pixel (i, j)
/// covers [i, i + 1) x [j, j + 1), so scaling an image scales its intrinsics exactly.
public struct DepthImage: Sendable, Equatable {
    /// The image's width, pixels; `millimeters` runs row-major at this width.
    public let width: Int
    /// The image's height, pixels.
    public let height: Int
    /// Row-major, `width` x `height`, in millimeters; 0 means no reading.
    public let millimeters: [UInt16]
    /// ARConfidenceLevel per pixel, same layout: 0 low, 1 medium, 2 high. Nil when none was given.
    public let confidence: [UInt8]?
    /// fx, fy, cx, cy in pixels of this image, in the landscape sensor orientation of the photo.
    public let intrinsics: SIMD4<Float>

    /// Traps on a buffer whose size does not match `width` x `height`, or on a non-positive focal
    /// length: both are programming errors in the adapter that built the image.
    public init(width: Int, height: Int, millimeters: [UInt16], confidence: [UInt8]?, intrinsics: SIMD4<Float>) {
        precondition(width > 0 && height > 0, "depth image size \(width) x \(height) is empty")
        precondition(millimeters.count == width * height, "depth has \(millimeters.count) values for \(width) x \(height)")
        if let confidence {
            precondition(confidence.count == width * height, "confidence has \(confidence.count) values for \(width) x \(height)")
        }
        precondition(intrinsics.x > 0 && intrinsics.y > 0, "depth intrinsics \(intrinsics) need positive focal lengths")
        self.width = width
        self.height = height
        self.millimeters = millimeters
        self.confidence = confidence
        self.intrinsics = intrinsics
    }

    /// An image from meters, as ARKit's `sceneDepth.depthMap` and Measure Lab's depth files hold
    /// them: each value becomes whole millimeters, rounded. A value that is not finite, not
    /// positive, or over 65.535 m (the largest UInt16 millimeter count; LiDAR reads a few
    /// meters) becomes 0, no reading.
    public init(meters: [Float], width: Int, height: Int, confidence: [UInt8]?, intrinsics: SIMD4<Float>) {
        let millimeters = meters.map { value -> UInt16 in
            guard value.isFinite, value > 0 else { return 0 }
            let rounded = (value * 1000).rounded()
            return rounded <= Float(UInt16.max) ? UInt16(rounded) : 0
        }
        self.init(width: width, height: height, millimeters: millimeters, confidence: confidence, intrinsics: intrinsics)
    }

    /// Intrinsics of an image covering the same field of view as a photo, at another size: each
    /// axis scales by its own ratio. ARKit's depth map (256 x 192) covers the captured image
    /// (1920 x 1440), so a photo's intrinsics become the depth map's this way.
    public static func intrinsics(scaling photo: SIMD4<Float>, from photoSize: SIMD2<Float>, toWidth width: Int, height: Int) -> SIMD4<Float> {
        let sx = Float(width) / photoSize.x
        let sy = Float(height) / photoSize.y
        return SIMD4(photo.x * sx, photo.y * sy, photo.z * sx, photo.w * sy)
    }

    /// The reading at a pixel in meters, or nil when the pixel is off the image, has no reading,
    /// or its confidence is below `minimumConfidence` (an image without confidence passes).
    public func meters(atPixel pixel: SIMD2<Float>, minimumConfidence: UInt8) -> Float? {
        guard pixel.x >= 0, pixel.y >= 0, pixel.x < Float(width), pixel.y < Float(height) else { return nil }
        let index = Int(pixel.y) * width + Int(pixel.x)
        if let confidence, confidence[index] < minimumConfidence { return nil }
        let value = millimeters[index]
        return value == 0 ? nil : Float(value) / 1000
    }

    /// A smaller copy: each `factor` x `factor` block (partial at the right and bottom edges)
    /// becomes one pixel holding the block's nearest reading and the lowest confidence among the
    /// block's readings. The nearest reading keeps an occluder's edge: a block half bush and half
    /// wall reads as bush, so downsampling can hide a sample but never show one that was hidden.
    public func downsampled(by factor: Int) -> DepthImage {
        precondition(factor >= 1, "downsampling factor \(factor) is below 1")
        guard factor > 1 else { return self }
        let w = (width + factor - 1) / factor
        let h = (height + factor - 1) / factor
        var values = [UInt16](repeating: 0, count: w * h)
        var levels: [UInt8]? = confidence.map { _ in [UInt8](repeating: 0, count: w * h) }
        for y in 0..<h {
            for x in 0..<w {
                var nearest: UInt16 = 0
                var lowest: UInt8 = .max
                for sy in (y * factor)..<min(height, (y + 1) * factor) {
                    for sx in (x * factor)..<min(width, (x + 1) * factor) {
                        let index = sy * width + sx
                        let value = millimeters[index]
                        guard value != 0 else { continue }
                        if nearest == 0 || value < nearest { nearest = value }
                        if let confidence { lowest = min(lowest, confidence[index]) }
                    }
                }
                values[y * w + x] = nearest
                if nearest != 0 { levels?[y * w + x] = lowest }
            }
        }
        let f = Float(factor)
        return DepthImage(
            width: w, height: h, millimeters: values, confidence: levels,
            intrinsics: SIMD4(intrinsics.x / f, intrinsics.y / f, intrinsics.z / f, intrinsics.w / f))
    }

    /// Where a world point lands in this image, taken with `camera`'s pose, and its z-depth from
    /// the camera in meters. Nil when the point is not in front of the camera.
    func projection(of world: SIMD3<Float>, pose camera: CameraFrame) -> (pixel: SIMD2<Float>, depth: Float)? {
        let p = camera.cameraSpace(world)
        let depth = -p.z
        guard depth > 0 else { return nil }
        let i = intrinsics
        return (SIMD2(i.z + i.x * p.x / depth, i.w - i.y * p.y / depth), depth)
    }
}
