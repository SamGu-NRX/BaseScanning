import LiveDotsCore
import Metal
import simd

/// How the unmeasured world is shown.
enum FogStyle: String, CaseIterable {
    /// The fog layer: one continuous haze that dissolves where dots arrive.
    case on
    /// The old comparison: a flat 35% veil over 30 cm wall cells no dot has reached.
    case veil
    case off
}

/// The fog layer's GPU state: the 96 x 208 reveal mask, its blur, the lagged copy that carries
/// from frame to frame, and each keyframe's depth for the depth term. The shaders and what they
/// borrow are described at the fog section of Shaders.swift.
final class FogLayer {
    static let maskWidth = 96, maskHeight = 208

    private let splatPipeline: MTLRenderPipelineState
    private let blurPipeline: MTLRenderPipelineState
    private let lagPipeline: MTLRenderPipelineState
    private let compositePipeline: MTLRenderPipelineState
    private let accumulation: MTLTexture
    private let blurred: MTLTexture
    private let reveal: MTLTexture
    private let lag: [MTLTexture]
    private var current = 0
    private var primed = false
    private let depths: [MTLTexture]

    init(device: MTLDevice, library: MTLLibrary, replay: Replay) throws {
        func pipeline(_ vertex: String, _ fragment: String, format: MTLPixelFormat, additive: Bool = false,
                      premultiplied: Bool = false) throws -> MTLRenderPipelineState {
            let descriptor = MTLRenderPipelineDescriptor()
            descriptor.vertexFunction = library.makeFunction(name: vertex)
            descriptor.fragmentFunction = library.makeFunction(name: fragment)
            guard let a = descriptor.colorAttachments[0] else { throw DotRenderer.SetupError.buffer("fog pipeline") }
            a.pixelFormat = format
            if additive || premultiplied {
                a.isBlendingEnabled = true
                a.sourceRGBBlendFactor = .one
                a.destinationRGBBlendFactor = additive ? .one : .oneMinusSourceAlpha
                a.sourceAlphaBlendFactor = .zero
                a.destinationAlphaBlendFactor = .one
            }
            return try device.makeRenderPipelineState(descriptor: descriptor)
        }
        // Half floats, not the 8 bits the design named: splats add up past 1 before the blur,
        // and an 8-bit target would clamp each one as it lands.
        let maskFormat = MTLPixelFormat.r16Float
        splatPipeline = try pipeline("splatVertex", "splatFragment", format: maskFormat, additive: true)
        blurPipeline = try pipeline("cameraVertex", "blurFragment", format: maskFormat)
        lagPipeline = try pipeline("cameraVertex", "lagFragment", format: maskFormat)
        compositePipeline = try pipeline("cameraVertex", "fogFragment", format: DotRenderer.pixelFormat, premultiplied: true)

        func mask() throws -> MTLTexture {
            let d = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: maskFormat, width: Self.maskWidth, height: Self.maskHeight, mipmapped: false)
            d.usage = [.renderTarget, .shaderRead]
            d.storageMode = .private
            guard let texture = device.makeTexture(descriptor: d) else { throw DotRenderer.SetupError.buffer("fog mask") }
            return texture
        }
        accumulation = try mask()
        blurred = try mask()
        reveal = try mask()
        lag = [try mask(), try mask()]

        depths = try replay.keyframes.map { keyframe in
            let map = try replay.depth(for: keyframe)
            let d = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: .r32Float, width: map.width, height: map.height, mipmapped: false)
            d.usage = [.shaderRead]
            d.storageMode = .shared
            guard let texture = device.makeTexture(descriptor: d) else { throw DotRenderer.SetupError.buffer("depth") }
            map.meters.withUnsafeBytes { raw in
                if let base = raw.baseAddress {
                    texture.replace(
                        region: MTLRegionMake2D(0, 0, map.width, map.height), mipmapLevel: 0,
                        withBytes: base, bytesPerRow: map.width * 4)
                }
            }
            return texture
        }
    }

    /// Splats, blurs and eases the reveal mask. `reset` snaps the lag to the new mask (a scrub, a
    /// mode switch, the fog turned back on, a still); the first frame always snaps.
    func encodeMask(
        into commandBuffer: MTLCommandBuffer, splats: MTLBuffer?, splatCount: Int, clip: simd_float4x4,
        pixelsPerMetre: Float, time: Float, dt: Float, reduceMotion: Bool, reset: Bool
    ) {
        func pass(_ target: MTLTexture, clear: Bool, _ body: (MTLRenderCommandEncoder) -> Void) {
            let descriptor = MTLRenderPassDescriptor()
            descriptor.colorAttachments[0].texture = target
            descriptor.colorAttachments[0].loadAction = clear ? .clear : .dontCare
            descriptor.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 0)
            descriptor.colorAttachments[0].storeAction = .store
            guard let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: descriptor) else { return }
            body(encoder)
            encoder.endEncoding()
        }
        pass(accumulation, clear: true) { encoder in
            guard let splats, splatCount > 0 else { return }
            var uniforms = SplatUniforms(clip: clip, pixelsPerMetre: pixelsPerMetre, time: time, pad: .zero)
            encoder.setRenderPipelineState(splatPipeline)
            encoder.setVertexBuffer(splats, offset: 0, index: 0)
            encoder.setVertexBytes(&uniforms, length: MemoryLayout<SplatUniforms>.stride, index: 1)
            encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: splatCount)
        }
        for (source, target, direction, finish) in [
            (accumulation, blurred, SIMD2<Int32>(1, 0), Int32(0)), (blurred, reveal, SIMD2<Int32>(0, 1), Int32(1)),
        ] {
            pass(target, clear: false) { encoder in
                var direction = direction, finish = finish
                encoder.setRenderPipelineState(blurPipeline)
                encoder.setFragmentTexture(source, index: 0)
                encoder.setFragmentBytes(&direction, length: MemoryLayout<SIMD2<Int32>>.stride, index: 0)
                encoder.setFragmentBytes(&finish, length: MemoryLayout<Int32>.stride, index: 1)
                encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            }
        }
        let previous = lag[current]
        current = 1 - current
        var uniforms = LagUniforms(dt: dt, reduceMotion: reduceMotion ? 1 : 0, reset: reset || !primed ? 1 : 0, pad: 0)
        primed = true
        pass(lag[current], clear: false) { encoder in
            encoder.setRenderPipelineState(lagPipeline)
            encoder.setFragmentTexture(reveal, index: 0)
            encoder.setFragmentTexture(previous, index: 1)
            encoder.setFragmentBytes(&uniforms, length: MemoryLayout<LagUniforms>.stride, index: 0)
            encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        }
    }

    /// Draws the fog over the camera image, before the dots.
    func encodeComposite(into encoder: MTLRenderCommandEncoder, uniforms: FogUniforms, keyframe: Int) {
        var uniforms = uniforms
        encoder.setRenderPipelineState(compositePipeline)
        encoder.setFragmentBytes(&uniforms, length: MemoryLayout<FogUniforms>.stride, index: 0)
        encoder.setFragmentTexture(lag[current], index: 0)
        encoder.setFragmentTexture(depths[keyframe], index: 1)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
    }
}

struct SplatUniforms {
    var clip: simd_float4x4
    var pixelsPerMetre: Float
    var time: Float
    var pad: SIMD2<Float>
}

struct LagUniforms {
    var dt: Float
    var reduceMotion: Int32
    var reset: Int32
    var pad: Int32
}

struct FogUniforms {
    var viewSize: SIMD2<Float>
    var offset: SIMD2<Float>
    var scale: Float
    var time: Float
    var imageSize: SIMD2<Float>
    var motion: Float
    var pad0: Float
    var pad1: SIMD2<Float>
}
