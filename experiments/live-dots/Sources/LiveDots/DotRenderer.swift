import LiveDotsCore
import Metal
import MetalKit
import simd

/// Draws a keyframe: the camera image, the optional fog veil, then the dots as additive point
/// sprites from one vertex buffer. The buffer is rebuilt only when the keyframe or mode changes;
/// between keyframes the shader animates births and evidence from the playback time.
final class DotRenderer {
    enum SetupError: Error, CustomStringConvertible {
        case noMetalDevice
        case noCommandQueue
        case buffer(String)

        var description: String {
            switch self {
            case .noMetalDevice: "no Metal device on this Mac"
            case .noCommandQueue: "Metal would not make a command queue"
            case let .buffer(what): "Metal would not allocate the \(what) buffer"
            }
        }
    }

    static let pixelFormat = MTLPixelFormat.bgra8Unorm

    let device: MTLDevice
    let queue: MTLCommandQueue
    let data: ReplayData
    private let cameraPipeline: MTLRenderPipelineState
    private let veilPipeline: MTLRenderPipelineState
    private let dotPipeline: MTLRenderPipelineState
    private let linkPipeline: MTLRenderPipelineState
    private let cameraImages: [MTLTexture]
    private let fog: FogLayer
    private var splatBuffer: MTLBuffer?
    private var splatCount = 0

    private var spriteBuffer: MTLBuffer?
    private var spriteCount = 0
    private var veilBuffer: MTLBuffer?
    private var veilVertexCount = 0
    private var linkBuffer: MTLBuffer?
    private var linkVertexCount = 0
    private var cachedKey: (mode: CaptureMode, keyframe: Int, scheme: DotScheme)?
    private var fogContinuity = FogContinuity()
    /// Replaces the timeline's sprites, for the 6,000-dot benchmark.
    var spriteOverride: [DotSprite]? {
        didSet { cachedKey = nil }
    }

    init(data: ReplayData, device: MTLDevice? = MTLCreateSystemDefaultDevice()) throws {
        guard let device else { throw SetupError.noMetalDevice }
        guard let queue = device.makeCommandQueue() else { throw SetupError.noCommandQueue }
        self.device = device
        self.queue = queue
        self.data = data

        let library = try device.makeLibrary(source: Shaders.source, options: nil)
        func pipeline(_ vertex: String, _ fragment: String, blend: (MTLRenderPipelineColorAttachmentDescriptor) -> Void) throws -> MTLRenderPipelineState {
            let descriptor = MTLRenderPipelineDescriptor()
            descriptor.vertexFunction = library.makeFunction(name: vertex)
            descriptor.fragmentFunction = library.makeFunction(name: fragment)
            descriptor.colorAttachments[0].pixelFormat = Self.pixelFormat
            blend(descriptor.colorAttachments[0])
            return try device.makeRenderPipelineState(descriptor: descriptor)
        }
        cameraPipeline = try pipeline("cameraVertex", "cameraFragment") { _ in }
        veilPipeline = try pipeline("veilVertex", "veilFragment") { a in
            a.isBlendingEnabled = true
            a.sourceRGBBlendFactor = .sourceAlpha
            a.destinationRGBBlendFactor = .oneMinusSourceAlpha
            a.sourceAlphaBlendFactor = .zero
            a.destinationAlphaBlendFactor = .one
        }
        // Additive: the dots are light on the camera image, never paint over it.
        let additive: (MTLRenderPipelineColorAttachmentDescriptor) -> Void = { a in
            a.isBlendingEnabled = true
            a.sourceRGBBlendFactor = .one
            a.destinationRGBBlendFactor = .one
            a.sourceAlphaBlendFactor = .zero
            a.destinationAlphaBlendFactor = .one
        }
        dotPipeline = try pipeline("dotVertex", "dotFragment", blend: additive)
        linkPipeline = try pipeline("linkVertex", "linkFragment", blend: additive)

        fog = try FogLayer(device: device, library: library, replay: data.replay)

        let loader = MTKTextureLoader(device: device)
        cameraImages = try data.replay.keyframes.map { keyframe in
            try loader.newTexture(
                URL: data.replay.folder.appendingPathComponent(keyframe.imagePath),
                options: [.SRGB: false, .textureStorageMode: MTLStorageMode.private.rawValue])
        }
    }

    /// Encodes one frame into `pass`, whose target is `pixelSize` pixels at `pointScale` pixels
    /// per point.
    func encode(_ request: FrameRequest, into commandBuffer: MTLCommandBuffer, pass: MTLRenderPassDescriptor,
                pixelSize: SIMD2<Float>, pointScale: Float) throws {
        let state = data.timeline(request.mode).states[request.keyframe]
        let keyframe = data.replay.keyframes[request.keyframe]
        try prepareBuffers(request.mode, state, scheme: request.scheme)
        let projection = ScreenProjection(keyframe: keyframe, viewSize: pixelSize)

        var clip = projection.clipMatrix
        let resetFog = fogContinuity.consume(request)
        if request.fog == .on {
            fog.encodeMask(
                into: commandBuffer, splats: splatBuffer, splatCount: splatCount, clip: clip,
                pixelsPerMetre: keyframe.intrinsics.x * projection.scale * Float(FogLayer.maskWidth) / pixelSize.x,
                time: request.time, dt: request.frameInterval, reduceMotion: request.reduceMotion,
                reset: resetFog)
        }

        guard let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: pass) else { return }
        defer { encoder.endEncoding() }

        var camera = CameraUniforms(
            offset: projection.offset, scale: projection.scale, pad: 0,
            imageSize: SIMD2(Float(keyframe.width), Float(keyframe.height)))
        encoder.setRenderPipelineState(cameraPipeline)
        encoder.setFragmentBytes(&camera, length: MemoryLayout<CameraUniforms>.stride, index: 0)
        encoder.setFragmentTexture(cameraImages[request.keyframe], index: 0)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)

        if request.fog == .on {
            fog.encodeComposite(into: encoder, uniforms: FogUniforms(
                viewSize: pixelSize, offset: projection.offset, scale: projection.scale, time: request.ambientTime,
                imageSize: camera.imageSize, motion: request.reduceMotion ? 0 : 1, pad0: 0, pad1: .zero),
                keyframe: request.keyframe)
        }
        if request.fog == .veil, let veilBuffer, veilVertexCount > 0 {
            encoder.setRenderPipelineState(veilPipeline)
            encoder.setVertexBuffer(veilBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&clip, length: MemoryLayout<simd_float4x4>.stride, index: 1)
            encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: veilVertexCount)
        }

        var dots = DotUniforms(
            clip: clip, cameraAndTime: SIMD4(keyframe.cameraPosition, request.time),
            pointScale: pointScale, reduceMotion: request.reduceMotion ? 1 : 0,
            scheme: Float(DotScheme.allCases.firstIndex(of: request.scheme) ?? 0), pad: 0)
        if let linkBuffer, linkVertexCount > 0 {
            encoder.setRenderPipelineState(linkPipeline)
            encoder.setVertexBuffer(linkBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&dots, length: MemoryLayout<DotUniforms>.stride, index: 1)
            encoder.drawPrimitives(type: .line, vertexStart: 0, vertexCount: linkVertexCount)
        }
        if let spriteBuffer, spriteCount > 0 {
            encoder.setRenderPipelineState(dotPipeline)
            encoder.setVertexBuffer(spriteBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&dots, length: MemoryLayout<DotUniforms>.stride, index: 1)
            encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: spriteCount)
        }
    }

    private func prepareBuffers(_ mode: CaptureMode, _ state: KeyframeState, scheme: DotScheme) throws {
        if let cachedKey, cachedKey.mode == mode, cachedKey.keyframe == state.index, cachedKey.scheme == scheme { return }
        cachedKey = (mode, state.index, scheme)

        let all = spriteOverride ?? state.sprites
        // Every live dot clears fog, whatever the scheme draws.
        let splats = all.map(SpriteVertex.init)
        splatCount = splats.count
        splatBuffer = splats.isEmpty ? nil : try makeBuffer(splats, "splat")

        let sprites = all.filter { scheme.draws($0.kind) }
        let vertices = Self.vertices(for: sprites)
        spriteCount = vertices.count
        spriteBuffer = vertices.isEmpty ? nil : try makeBuffer(vertices, "dot")

        let links = scheme == .constellation ? Self.linkVertices(for: sprites) : []
        linkVertexCount = links.count
        linkBuffer = links.isEmpty ? nil : try makeBuffer(links, "link")

        var veil: [SIMD4<Float>] = []
        for cell in state.unseenCells {
            let (x0, y0, s) = (cell.minX, cell.minY, WallCell.size)
            let a = SIMD4<Float>(x0, y0, 0, 1), b = SIMD4<Float>(x0 + s, y0, 0, 1)
            let c = SIMD4<Float>(x0 + s, y0 + s, 0, 1), d = SIMD4<Float>(x0, y0 + s, 0, 1)
            veil += [a, b, c, a, c, d]
        }
        veilVertexCount = veil.count
        veilBuffer = veil.isEmpty ? nil : try makeBuffer(veil, "veil")
    }

    private func makeBuffer<T>(_ items: [T], _ what: String) throws -> MTLBuffer {
        let buffer = items.withUnsafeBytes { raw in
            raw.baseAddress.flatMap { device.makeBuffer(bytes: $0, length: raw.count, options: .storageModeShared) }
        }
        guard let buffer else { throw SetupError.buffer(what) }
        return buffer
    }

    /// Two vertices per dot: its halo (size and opacity set in the shader by kind), then the
    /// core. Halos come first so the sharp cores sit on top of them.
    static func vertices(for sprites: [DotSprite]) -> [SpriteVertex] {
        let cores = sprites.map(SpriteVertex.init)
        return cores.map(\.asHalo) + cores
    }
}

extension DotRenderer {
    /// Two vertices per link. Both carry the link's own timing: it is born with the later of its
    /// two dots (counting a dot as born when it became an edge), dies with the earlier, and takes
    /// the lower opacity.
    static func linkVertices(for sprites: [DotSprite]) -> [SpriteVertex] {
        EdgeLinks.links(among: sprites).flatMap { pair -> [SpriteVertex] in
            let a = sprites[Int(pair.x)], b = sprites[Int(pair.y)]
            let timing = DotSprite(
                id: 0, position: .zero, kind: .edge, onOccluder: a.onOccluder && b.onOccluder,
                birthTime: max(max(a.birthTime, a.edgeSince), max(b.birthTime, b.edgeSince)),
                fromOpacity: min(a.fromOpacity, b.fromOpacity), toOpacity: min(a.toOpacity, b.toOpacity),
                opacityTime: max(a.opacityTime, b.opacityTime), edgeSince: -.infinity,
                deathTime: min(a.deathTime, b.deathTime))
            var start = SpriteVertex(timing), end = start
            start.a = SIMD4(a.position, 0)
            end.a = SIMD4(b.position, 0)
            return [start, end]
        }
    }
}

/// Matches `Sprite` in the shader. Infinite times are clamped to plus or minus 1e6 s, because
/// Metal compiles with fast math, which assumes no infinities.
struct SpriteVertex {
    var a: SIMD4<Float>
    var b: SIMD4<Float>
    var c: SIMD4<Float>

    init(_ s: DotSprite) {
        func finite(_ t: Float) -> Float { min(max(t, -1e6), 1e6) }
        a = SIMD4(s.position, s.kind == .feature ? 1 : 0)
        b = SIMD4(finite(s.birthTime), s.fromOpacity, s.toOpacity, finite(s.opacityTime))
        c = SIMD4(finite(s.edgeSince), finite(s.deathTime), s.onOccluder ? 1 : 0, 0)
    }

    var asHalo: SpriteVertex {
        var halo = self
        halo.c.w = 1
        return halo
    }
}

struct CameraUniforms {
    var offset: SIMD2<Float>
    var scale: Float
    var pad: Float
    var imageSize: SIMD2<Float>
}

struct DotUniforms {
    var clip: simd_float4x4
    var cameraAndTime: SIMD4<Float>
    var pointScale: Float
    var reduceMotion: Float
    var scheme: Float
    var pad: Float
}
