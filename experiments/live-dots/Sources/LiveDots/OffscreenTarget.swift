import LiveDotsCore
import CoreGraphics
import Metal

/// A render target the size of an iPhone 15 Pro screen (1170 x 2532 pixels at 3x) whose pixels
/// come back to the CPU, for the export and the benchmark.
final class OffscreenTarget {
    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    static let pointSize = SIMD2<Float>(390, 844)
    static let scale: Float = 3

    let width: Int
    let height: Int
    private let renderer: DotRenderer
    private let texture: MTLTexture
    private let readback: MTLBuffer

    init(renderer: DotRenderer, pointSize: SIMD2<Float> = pointSize, scale: Float = scale) throws {
        self.renderer = renderer
        width = Int(pointSize.x * scale)
        height = Int(pointSize.y * scale)
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: DotRenderer.pixelFormat, width: width, height: height, mipmapped: false)
        descriptor.usage = [.renderTarget]
        descriptor.storageMode = .private
        guard let texture = renderer.device.makeTexture(descriptor: descriptor),
              let readback = renderer.device.makeBuffer(length: width * height * 4, options: .storageModeShared)
        else { throw Failure(description: "Metal would not allocate a \(width) x \(height) target") }
        self.texture = texture
        self.readback = readback
    }

    /// Renders and waits. Returns GPU seconds. With `readBack`, the pixels land in `pixels`.
    @discardableResult
    func render(_ request: FrameRequest, readBack: Bool = true) throws -> Double {
        guard let commandBuffer = renderer.queue.makeCommandBuffer() else { throw Failure(description: "no command buffer") }
        let pass = MTLRenderPassDescriptor()
        pass.colorAttachments[0].texture = texture
        pass.colorAttachments[0].loadAction = .clear
        pass.colorAttachments[0].storeAction = .store
        pass.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 1)
        try renderer.encode(
            request, into: commandBuffer, pass: pass,
            pixelSize: SIMD2(Float(width), Float(height)), pointScale: Float(width) / OffscreenTarget.pointSize.x)
        if readBack, let blit = commandBuffer.makeBlitCommandEncoder() {
            blit.copy(
                from: texture, sourceSlice: 0, sourceLevel: 0, sourceOrigin: MTLOrigin(x: 0, y: 0, z: 0),
                sourceSize: MTLSize(width: width, height: height, depth: 1),
                to: readback, destinationOffset: 0, destinationBytesPerRow: width * 4, destinationBytesPerImage: width * height * 4)
            blit.endEncoding()
        }
        commandBuffer.commit()
        commandBuffer.waitUntilCompleted()
        if let error = commandBuffer.error { throw Failure(description: "GPU error: \(error)") }
        return commandBuffer.gpuEndTime - commandBuffer.gpuStartTime
    }

    /// A bitmap context over the last rendered pixels (BGRA, premultiplied), for compositing.
    func context() throws -> CGContext {
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(
                data: readback.contents(), width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { throw Failure(description: "no bitmap context for the readback") }
        return context
    }

    var bytes: UnsafeRawBufferPointer {
        UnsafeRawBufferPointer(start: readback.contents(), count: width * height * 4)
    }
}
