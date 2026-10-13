// The dot field: what the live capture view draws over the camera, and why. No-LiDAR mode is
// simulated in SimulatedFeatures.swift; this file is LiDAR mode.
//
// Each keyframe's depth samples with medium or high confidence and depth from 0.3 to 6 m are
// unprojected and fused into 5 cm voxels. A voxel keeps the keyframe it was first seen in, its
// hit count, its distinct view directions (a new one must differ from every stored one by more
// than 15 degrees), a normal from central differences on the depth map (set by the first
// keyframe, averaged after) and the image gradient its samples saw. Its dot sits at the centre
// plus a fixed jitter of up to 30% of the voxel, in the plane of its first normal, so the field
// never reads as a grid.
//
// A voxel becomes an edge, and stays one, when
//   (a) a face neighbour's normal differs by more than 35 degrees (the wall-to-ground corner,
//       the bin's corners);
//   (b) it has surface along one side and seen-empty space along another (the wall's top against
//       the sky, the bin's silhouette). Seen-empty means a depth ray of medium or high confidence
//       passed the neighbour with 10 cm to spare, so the edge of what has been scanned so far is
//       never an edge; or
//   (c) the colour gradient at its samples reaches 0.2 (painted outlines). The gradient is read
//       from a pyramid level whose pixel spans about one voxel, and ignored near a depth
//       silhouette, where the colour step is occlusion rather than paint. On the fixture
//       (`LiveDots --gradient-report`) 100% of voxels on painted outlines and 1% of plain-brick
//       voxels reach 0.2. A Sobel on the full-size JPEG gives 31% and 19%, because mortar joints
//       are as sharp as paint.
// The simulated no-LiDAR feature points use a lower gradient, 0.1, on purpose: ARKit's
// rawFeaturePoints land on any high-contrast texture, brick joints included, while an edge dot
// should mark only an outline.
// Every edge voxel gets a dot. Flat voxels get one per 2 x 2 x 2 group (the lowest hash, then
// kept), a quarter of the edge density on a wall. At most 6,000 dots draw in view; past that,
// flat dots drop in hash order.
//
// Opacity is 45% at one view and rises 15 points a view to 90% at four. An edge dot on a
// surface within 45 degrees of vertical stays at or below 60% until one view saw it within 30
// degrees of face-on.
//
// Drawing (the shader in Sources/LiveDots/Shaders.swift): flat dots are 2.5 pt and edge dots
// 3.5 pt at 2.5 m and beyond, growing linearly to 4 and 6 pt at 1 m and no larger closer in, for
// a view used 1.5 to 3 m from the wall. Every dot carries an additive halo: 4x size at 22% for
// edges, 2x at 8% for flat dots, 3x at 18% for the 4.5 pt simulated feature points. Cores are
// Hologram #E6ECF4 and halos #9CC8FF; dots more than 0.25 m in front of the wall and above the
// ground are on the occluder and draw violet #B49CFF, halo included.
// That is the hologram scheme; DotScheme.swift describes the constellation and ember looks.

import simd

public typealias VoxelKey = SIMD3<Int32>

/// What one 5 cm voxel has accumulated.
public struct Voxel: Sendable {
    public let firstSeenFrame: Int
    public internal(set) var hitCount: Int
    public internal(set) var views: ViewDirections
    /// Sum of per-keyframe unit normals: set by the first hit, averaged by later ones.
    public internal(set) var normalSum: SIMD3<Float>
    /// Fixed at the first hit, in the plane of the first normal.
    public let jitter: SIMD3<Float>
    /// Highest per-keyframe mean colour gradient of the samples that landed here.
    public internal(set) var gradient: Float
    /// Highest cosine between a view direction and the voxel's normal at that view. Kept apart
    /// from `views`, which drops a face-on view that falls within 15 degrees of an oblique one.
    public internal(set) var faceOnCosine: Float
    /// The keyframe the voxel first classified as an edge. Edges stay edges, so a dot never
    /// shrinks back or loses its glow.
    public internal(set) var edgeSinceFrame: Int?

    public var normal: SIMD3<Float>? {
        let length = simd_length(normalSum)
        return length > 1e-3 ? normalSum / length : nil
    }

    public var isEdge: Bool { edgeSinceFrame != nil }
}

public enum EdgeReason: Sendable, Equatable {
    /// A face neighbour's normal differs by more than 35 degrees.
    case crease
    /// Seen-empty space on one side along the surface, surface on another.
    case boundary
    /// The image gradient at the voxel crosses the threshold (painted outlines).
    case imageEdge
}

/// The LiDAR-mode dot field: depth samples fused into 5 cm voxels, each voxel classified as edge
/// or flat, and the dots drawn from them. See the comment at the top of this file.
public struct VoxelField: Sendable {
    public let size: Float
    public private(set) var voxels: [VoxelKey: Voxel] = [:]
    /// Voxels a depth ray has passed through with room to spare. Only in-plane neighbours of
    /// occupied voxels are tested, which is all the boundary rule needs.
    public private(set) var free: Set<VoxelKey> = []
    /// One flat voxel per 2 x 2 x 2 group, chosen once by lowest hash and then kept, so a flat
    /// dot never hops to a sibling as the group fills in.
    private var representatives: [VoxelKey: VoxelKey] = [:]

    static let faceAxes: [VoxelKey] = [
        VoxelKey(1, 0, 0), VoxelKey(-1, 0, 0), VoxelKey(0, 1, 0),
        VoxelKey(0, -1, 0), VoxelKey(0, 0, 1), VoxelKey(0, 0, -1),
    ]

    public init(size: Float = Tuning.voxelSize) {
        self.size = size
    }

    /// Voxel centres sit on multiples of `size`, so the fixture's wall (z = 0) and ground (y = 0)
    /// run through voxel centres instead of splitting between two layers.
    public func key(for p: SIMD3<Float>) -> VoxelKey {
        VoxelKey((p / size).rounded(.toNearestOrAwayFromZero))
    }

    public func centre(of key: VoxelKey) -> SIMD3<Float> {
        SIMD3<Float>(key) * size
    }

    // MARK: Building blocks, public so the tests can build a field by hand

    /// Records one keyframe's samples in a voxel: `normal` is their mean (nil if none had one),
    /// `gradient` their mean image gradient, `camera` the camera position.
    public mutating func observe(
        _ key: VoxelKey, normal: SIMD3<Float>?, gradient: Float, samples: Int, camera: SIMD3<Float>, frame: Int
    ) {
        let centre = centre(of: key)
        var voxel = voxels[key] ?? Voxel(
            firstSeenFrame: frame, hitCount: 0, views: ViewDirections(), normalSum: .zero,
            jitter: Jitter.offset(for: key, normal: normal ?? SIMD3(0, 0, 1), cellSize: size),
            gradient: 0, faceOnCosine: -1, edgeSinceFrame: nil)
        voxel.hitCount += samples
        if let normal, simd_length(normal) > 1e-3 { voxel.normalSum += simd_normalize(normal) }
        voxel.gradient = max(voxel.gradient, gradient)
        voxel.views.insert(camera - centre)
        if let current = voxel.normal, simd_length(camera - centre) > 1e-6 {
            voxel.faceOnCosine = max(voxel.faceOnCosine, simd_dot(simd_normalize(camera - centre), current))
        }
        voxels[key] = voxel
        free.remove(key)
    }

    public mutating func markFree(_ key: VoxelKey) {
        if voxels[key] == nil { free.insert(key) }
    }

    public func edgeReason(for key: VoxelKey) -> EdgeReason? {
        guard let voxel = voxels[key] else { return nil }
        let normal = voxel.normal
        var surfaceNeighbour = false, emptyNeighbour = false
        for axis in Self.faceAxes {
            let along = normal.map { abs(simd_dot(SIMD3<Float>(axis), $0)) < Tuning.alongSurfaceCosine } ?? false
            if let other = voxels[key &+ axis] {
                if let normal, let otherNormal = other.normal, Self.normalsDisagree(normal, otherNormal) {
                    return .crease
                }
                if along { surfaceNeighbour = true }
            } else if along, free.contains(key &+ axis) {
                emptyNeighbour = true
            }
        }
        if surfaceNeighbour, emptyNeighbour { return .boundary }
        if voxel.gradient >= Tuning.gradientThreshold { return .imageEdge }
        return nil
    }

    public static func normalsDisagree(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Bool {
        simd_dot(simd_normalize(a), simd_normalize(b)) < cos(Tuning.creaseDegrees * Float.pi / 180)
    }

    /// Marks new edges (sticky), then gives each group without one a flat representative.
    public mutating func classify(frame: Int) {
        let newEdges = voxels.keys.filter { voxels[$0]?.isEdge == false && edgeReason(for: $0) != nil }
        for key in newEdges { voxels[key]?.edgeSinceFrame = frame }

        var best: [VoxelKey: (key: VoxelKey, hash: UInt64)] = [:]
        for (key, voxel) in voxels where !voxel.isEdge {
            let group = key &>> 1
            guard representatives[group] == nil else { continue }
            let hash = StableHash.hash(key, seed: 0xF1A7)
            if let current = best[group], current.hash <= hash { continue }
            best[group] = (key, hash)
        }
        for (group, choice) in best { representatives[group] = choice.key }
    }

    // MARK: Keyframes

    public mutating func integrate(_ frame: FrameInput) {
        let depth = frame.depth
        let pose = frame.keyframe.cameraToWorld
        let camera = frame.keyframe.cameraPosition
        let imageScale = Float(frame.keyframe.width) / Float(depth.width)
        let focal = frame.keyframe.intrinsics.x

        let discontinuity = Self.discontinuityDistance(depth)

        struct Accumulator { var count = 0; var normals = 0; var normalSum = SIMD3<Float>.zero; var gradientSum: Float = 0 }
        var accumulators: [VoxelKey: Accumulator] = [:]
        for j in 0..<depth.height {
            for i in 0..<depth.width {
                let index = j * depth.width + i
                let d = depth.meters[index]
                guard depth.confidence[index] >= Tuning.minimumConfidence, Tuning.depthRange.contains(d) else { continue }
                let u = Float(i) + 0.5, v = Float(j) + 0.5
                let world = CameraMath.transform(pose, CameraMath.unproject(u: u, v: v, depth: d, intrinsics: depth.intrinsics))
                let level = GradientPyramid.level(depth: d, focal: focal)
                // Near a silhouette the colour step is one surface hiding another, not paint.
                // The boundary rule handles silhouettes from geometry; left in, this made wall
                // voxels beside the bin, and the whole foreshortened bin lid, into edges.
                let reach = 1.5 * Float(1 << level) / imageScale + 1
                let gradient = discontinuity[index] <= reach
                    ? 0 : frame.gradient.magnitude(u: u * imageScale, v: v * imageScale, level: level)
                var accumulator = accumulators[key(for: world)] ?? Accumulator()
                accumulator.count += 1
                accumulator.gradientSum += gradient
                if let normal = Self.cameraNormal(depth: depth, i: i, j: j) {
                    accumulator.normals += 1
                    accumulator.normalSum += CameraMath.rotate(pose, normal)
                }
                accumulators[key(for: world)] = accumulator
            }
        }
        for (key, a) in accumulators {
            observe(
                key, normal: a.normals > 0 ? a.normalSum / Float(a.normals) : nil,
                gradient: a.gradientSum / Float(a.count), samples: a.count, camera: camera, frame: frame.index)
        }
        carveFreeSpace(frame)
        classify(frame: frame.index)
    }

    /// Tests the empty in-plane neighbours of occupied voxels against this keyframe's depth: a
    /// neighbour is seen-empty when the ray through it hit nothing or hit something farther by
    /// `Tuning.freeSpaceMargin`. A neighbour outside the view or behind something stays unknown,
    /// which is why the edge of what has been scanned so far never counts as a boundary. So does
    /// one whose depth pixel is below `Tuning.minimumConfidence`: fusion ignores that pixel, and
    /// free space is never forgotten, so one bad zero or far reading would leave a lasting edge.
    mutating func carveFreeSpace(_ frame: FrameInput) {
        let depth = frame.depth
        let worldToCamera = frame.keyframe.cameraToWorld.inverse
        var found = Set<VoxelKey>()
        for (key, voxel) in voxels {
            guard let normal = voxel.normal else { continue }
            for axis in Self.faceAxes where abs(simd_dot(SIMD3<Float>(axis), normal)) < Tuning.alongSurfaceCosine {
                let neighbour = key &+ axis
                guard voxels[neighbour] == nil, !free.contains(neighbour) else { continue }
                let camera = CameraMath.transform(worldToCamera, centre(of: neighbour))
                guard let pixel = CameraMath.project(camera, intrinsics: depth.intrinsics),
                      pixel.depth >= Tuning.depthRange.lowerBound,
                      let measured = depth.depth(atU: pixel.u, v: pixel.v),
                      let confidence = depth.confidence(atU: pixel.u, v: pixel.v),
                      confidence >= Tuning.minimumConfidence
                else { continue }
                if measured == 0 || measured > pixel.depth + Tuning.freeSpaceMargin { found.insert(neighbour) }
            }
        }
        free.formUnion(found)
    }

    /// Per depth pixel, the distance in pixels (chamfer, 1 and 1.414 steps) to the nearest
    /// silhouette: a pixel whose 4-neighbour jumps by more than 10% of its depth or hit nothing.
    static func discontinuityDistance(_ depth: DepthMap) -> [Float] {
        let w = depth.width, h = depth.height, m = depth.meters
        var distance = [Float](repeating: .greatestFiniteMagnitude, count: w * h)
        for j in 0..<h {
            for i in 0..<w {
                let d = m[j * w + i]
                guard d > 0 else { continue }
                for (di, dj) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                    let x = i + di, y = j + dj
                    guard x >= 0, y >= 0, x < w, y < h else { continue }
                    let n = m[y * w + x]
                    if n == 0 || abs(n - d) > 0.1 * d { distance[j * w + i] = 0; break }
                }
            }
        }
        let diagonal = Float(2).squareRoot()
        func relax(_ index: Int, _ x: Int, _ y: Int, _ step: Float) {
            guard x >= 0, y >= 0, x < w, y < h else { return }
            distance[index] = min(distance[index], distance[y * w + x] + step)
        }
        for j in 0..<h {
            for i in 0..<w {
                let index = j * w + i
                relax(index, i - 1, j, 1); relax(index, i, j - 1, 1)
                relax(index, i - 1, j - 1, diagonal); relax(index, i + 1, j - 1, diagonal)
            }
        }
        for j in stride(from: h - 1, through: 0, by: -1) {
            for i in stride(from: w - 1, through: 0, by: -1) {
                let index = j * w + i
                relax(index, i + 1, j, 1); relax(index, i, j + 1, 1)
                relax(index, i + 1, j + 1, diagonal); relax(index, i - 1, j + 1, diagonal)
            }
        }
        return distance
    }

    /// Camera-space unit normal at depth pixel (i, j) from central differences, falling back to
    /// one side where the other jumps by more than 10% of the depth (a silhouette), facing the
    /// camera. Nil when neither side is usable on either axis.
    static func cameraNormal(depth: DepthMap, i: Int, j: Int) -> SIMD3<Float>? {
        let d0 = depth.meters[j * depth.width + i]
        func point(_ i: Int, _ j: Int) -> SIMD3<Float>? {
            guard i >= 0, j >= 0, i < depth.width, j < depth.height else { return nil }
            let d = depth.meters[j * depth.width + i]
            guard d > 0, abs(d - d0) < 0.1 * d0 else { return nil }
            return CameraMath.unproject(u: Float(i) + 0.5, v: Float(j) + 0.5, depth: d, intrinsics: depth.intrinsics)
        }
        guard let centre = point(i, j) else { return nil }
        func tangent(_ plus: SIMD3<Float>?, _ minus: SIMD3<Float>?) -> SIMD3<Float>? {
            switch (plus, minus) {
            case let (p?, m?): p - m
            case let (p?, nil): p - centre
            case let (nil, m?): centre - m
            case (nil, nil): nil
            }
        }
        guard let tx = tangent(point(i + 1, j), point(i - 1, j)), let ty = tangent(point(i, j + 1), point(i, j - 1)) else { return nil }
        let n = simd_cross(tx, ty)
        let length = simd_length(n)
        guard length > 1e-9 else { return nil }
        let unit = n / length
        return simd_dot(unit, -centre) < 0 ? -unit : unit
    }

    // MARK: Dots

    /// Every edge voxel, plus each group's flat representative. The on-screen cap is applied
    /// per keyframe by the timeline, which knows what is in view.
    public func dots() -> [FieldDot] {
        var dots: [FieldDot] = []
        for (key, voxel) in voxels where voxel.isEdge {
            dots.append(dot(key, voxel, kind: .edge))
        }
        for (_, key) in representatives {
            guard let voxel = voxels[key], !voxel.isEdge else { continue }
            dots.append(dot(key, voxel, kind: .flat))
        }
        return dots
    }

    /// Wall cells holding at least one occupied voxel on the wall plane, for the fog comparison.
    public func seenWallCells() -> Set<WallCell> {
        var cells = Set<WallCell>()
        for key in voxels.keys where key.z == 0 {
            let c = centre(of: key)
            if let cell = WallCell.containing(x: c.x, y: c.y) { cells.insert(cell) }
        }
        return cells
    }

    private func dot(_ key: VoxelKey, _ voxel: Voxel, kind: DotKind) -> FieldDot {
        let centre = centre(of: key)
        let wallLike = voxel.normal.map { abs($0.y) < Tuning.cappedNormalMaxY } ?? false
        let faceOn = kind != .edge || !wallLike || voxel.faceOnCosine >= cos(Tuning.faceOnDegrees * Float.pi / 180)
        return FieldDot(
            id: Self.id(for: key), position: centre + voxel.jitter, kind: kind,
            views: voxel.views.count, onOccluder: FieldDot.isOnOccluder(centre), faceOn: faceOn,
            normal: voxel.normal ?? .zero)
    }

    /// Packs the key into 21 bits per axis (plus or minus 52 km at 5 cm), so ids never collide.
    static func id(for key: VoxelKey) -> UInt64 {
        let bias: Int64 = 1 << 20
        let x = UInt64(Int64(key.x) + bias) & 0x1F_FFFF
        let y = UInt64(Int64(key.y) + bias) & 0x1F_FFFF
        let z = UInt64(Int64(key.z) + bias) & 0x1F_FFFF
        return x | (y << 21) | (z << 42)
    }
}
