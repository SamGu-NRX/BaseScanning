import simd
import Testing
@testable import LiveDotsCore

struct FieldRuleTests {
    let camera = SIMD3<Float>(0.2, 1, 2.5)

    @Test func `a corner between two planes is a crease edge and their interiors are flat`() {
        var field = VoxelField()
        for a in Int32(0)..<10 {
            for b in Int32(1)..<10 {
                field.observe(VoxelKey(a, b, 0), normal: SIMD3(0, 0, 1), gradient: 0, samples: 4, camera: camera, frame: 0)
                field.observe(VoxelKey(a, 0, b), normal: SIMD3(0, 1, 0), gradient: 0, samples: 4, camera: camera, frame: 0)
            }
            // The corner voxel holds samples of both planes, so its normal averages to 45 degrees.
            field.observe(VoxelKey(a, 0, 0), normal: simd_normalize(SIMD3(0, 1, 1)), gradient: 0, samples: 4, camera: camera, frame: 0)
        }
        #expect(field.edgeReason(for: VoxelKey(5, 0, 0)) == .crease)
        #expect(field.edgeReason(for: VoxelKey(5, 1, 0)) == .crease)
        #expect(field.edgeReason(for: VoxelKey(5, 5, 0)) == nil)
        #expect(field.edgeReason(for: VoxelKey(5, 0, 5)) == nil)
    }

    @Test func `neighbours under 35 degrees apart are not a crease`() {
        #expect(!VoxelField.normalsDisagree(SIMD3(0, 0, 1), SIMD3(0, sin(0.59), cos(0.59))))  // 33.8 degrees
        #expect(VoxelField.normalsDisagree(SIMD3(0, 0, 1), SIMD3(0, sin(0.63), cos(0.63))))   // 36.1 degrees
    }

    @Test func `seen-empty space along the surface makes a boundary, unseen space does not`() {
        var field = VoxelField()
        for x in Int32(0)..<10 {
            for y in Int32(0)..<10 {
                field.observe(VoxelKey(x, y, 0), normal: SIMD3(0, 0, 1), gradient: 0, samples: 4, camera: camera, frame: 0)
            }
        }
        for y in Int32(0)..<10 { field.markFree(VoxelKey(10, y, 0)) }
        // Empty space in front of the plane is not along the surface.
        field.markFree(VoxelKey(5, 5, 1))

        #expect(field.edgeReason(for: VoxelKey(9, 5, 0)) == .boundary)
        #expect(field.edgeReason(for: VoxelKey(0, 5, 0)) == nil, "x = -1 was never seen, so the scan's frontier is not an edge")
        #expect(field.edgeReason(for: VoxelKey(5, 5, 0)) == nil)
        // An occupied voxel can't be marked free.
        field.markFree(VoxelKey(4, 4, 0))
        #expect(!field.free.contains(VoxelKey(4, 4, 0)))
    }

    /// One keyframe square on to a flat wall at z = 0 from 2.5 m, with depth columns 39 and 40
    /// (the strip over voxel column x = 0) replaced by `stripDepth` at `stripConfidence`.
    func wallWithStrip(stripDepth: Float, stripConfidence: UInt8) -> VoxelField {
        let width = 80, height = 60
        var meters = [Float](repeating: 2.5, count: width * height)
        var confidence = [UInt8](repeating: 2, count: width * height)
        for j in 0..<height {
            for i in 39...40 {
                meters[j * width + i] = stripDepth
                confidence[j * width + i] = stripConfidence
            }
        }
        // 100 px focal at 2.5 m puts two depth pixels in each 5 cm voxel.
        let intrinsics = SIMD4<Float>(100, 100, 40, 30)
        let depth = DepthMap(meters: meters, confidence: confidence, width: width, height: height, intrinsics: intrinsics)
        let pose = simd_float4x4(columns: (SIMD4(1, 0, 0, 0), SIMD4(0, 1, 0, 0), SIMD4(0, 0, 1, 0), SIMD4(0, 0, 2.5, 1)))
        let keyframe = Keyframe(
            id: "k", imagePath: "k.jpg", width: width, height: height, intrinsics: intrinsics,
            cameraToWorld: pose, timestamp: 0, depth: nil)
        let image = RGBImage(width: width, height: height, rgba: [UInt8](repeating: 128, count: width * height * 4))
        var field = VoxelField()
        field.integrate(FrameInput(index: 0, keyframe: keyframe, depth: depth, gradient: GradientPyramid(image: image)))
        return field
    }

    @Test func `a confident hole in the wall carves free space and makes boundary edges`() {
        let field = wallWithStrip(stripDepth: 0, stripConfidence: 2)
        #expect(field.voxels[VoxelKey(0, 0, 0)] == nil)
        #expect(field.free.contains(VoxelKey(0, 0, 0)))
        #expect(field.voxels[VoxelKey(1, 0, 0)]?.isEdge == true)
        #expect(field.voxels[VoxelKey(-1, 0, 0)]?.isEdge == true)
    }

    @Test(arguments: [Float(0), 9])
    func `low-confidence depth carves no free space`(stripDepth: Float) {
        let field = wallWithStrip(stripDepth: stripDepth, stripConfidence: 0)
        #expect(field.voxels[VoxelKey(0, 0, 0)] == nil, "fusion skips the strip")
        #expect(!field.free.contains(VoxelKey(0, 0, 0)))
        #expect(field.voxels.values.allSatisfy { !$0.isEdge }, "a flat wall with no confident hole has no edges")
    }

    @Test func `the gradient threshold makes an image edge`() {
        var field = VoxelField()
        for x in Int32(0)..<3 {
            field.observe(VoxelKey(x, 0, 0), normal: SIMD3(0, 0, 1), gradient: x == 1 ? Tuning.gradientThreshold : 0.19, samples: 4, camera: camera, frame: 0)
        }
        #expect(field.edgeReason(for: VoxelKey(1, 0, 0)) == .imageEdge)
        #expect(field.edgeReason(for: VoxelKey(0, 0, 0)) == nil)
    }

    @Test func `edges stay edges and flat groups keep one representative`() {
        var field = VoxelField()
        for x in Int32(0)..<4 {
            for y in Int32(0)..<4 {
                field.observe(VoxelKey(x, y, 0), normal: SIMD3(0, 0, 1), gradient: x == 0 ? 1 : 0, samples: 4, camera: camera, frame: 0)
            }
        }
        field.classify(frame: 0)
        let first = field.dots()
        #expect(first.count { $0.kind == .edge } == 4)
        // x 0...3 by y 0...3 falls into four 2 x 2 x 2 groups, each with flat members.
        #expect(first.count { $0.kind == .flat } == 4)
        // A later keyframe with no gradient can't demote an edge.
        field.observe(VoxelKey(0, 0, 0), normal: SIMD3(0, 0, 1), gradient: 0, samples: 4, camera: camera, frame: 1)
        field.classify(frame: 1)
        let second = field.dots()
        #expect(Set(first.map(\.id)) == Set(second.map(\.id)))
    }

    @Test(arguments: [(0, Float(0)), (1, 0.45), (2, 0.6), (3, 0.75), (4, 0.9), (7, 0.9)])
    func `evidence maps views to opacity`(views: Int, opacity: Float) {
        #expect(abs(Evidence.opacity(views: views) - opacity) < 1e-6)
    }

    /// An edge voxel seen from 40, -40 and 60 degrees off its normal (75% if uncapped), plus
    /// `extra`. The normal is the wall's, or the ground's with `ground`.
    func obliqueEdge(_ extra: Float?, ground: Bool = false) throws -> FieldDot {
        var field = VoxelField()
        let key = VoxelKey(0, 30, 0)
        let centre = field.centre(of: key)
        let normal: SIMD3<Float> = ground ? SIMD3(0, 1, 0) : SIMD3(0, 0, 1)
        let side = SIMD3<Float>(1, 0, 0)
        for degrees in [40, -40, 60] + (extra.map { [$0] } ?? []) {
            let camera = centre + 2.6 * (side * sin(degrees * .pi / 180) + normal * cos(degrees * .pi / 180))
            field.observe(key, normal: normal, gradient: 1, samples: 4, camera: camera, frame: 0)
        }
        field.classify(frame: 0)
        return try #require(field.dots().first)
    }

    @Test func `an edge seen only obliquely stays at 60 percent until a face-on view`() throws {
        let oblique = try obliqueEdge(nil)
        #expect(oblique.kind == .edge && oblique.views == 3 && !oblique.faceOn)
        #expect(abs(oblique.opacity - 0.6) < 1e-6)

        let faced = try obliqueEdge(20)
        #expect(faced.views == 4 && faced.faceOn)
        #expect(abs(faced.opacity - 0.9) < 1e-6)

        // 28 degrees is within 15 of the 40-degree view, so it adds no view, yet it faces on.
        let near = try obliqueEdge(28)
        #expect(near.views == 3 && near.faceOn)
        #expect(abs(near.opacity - 0.75) < 1e-6)
    }

    @Test func `the oblique cap leaves edges on the ground alone`() throws {
        let ground = try obliqueEdge(nil, ground: true)
        #expect(ground.kind == .edge && ground.faceOn)
        #expect(abs(ground.opacity - 0.75) < 1e-6)
    }

    @Test func `flat dots ignore the oblique cap`() {
        #expect(abs(Evidence.opacity(views: 4, faceOn: false) - 0.6) < 1e-6)
        #expect(abs(FieldDot(id: 1, position: .zero, kind: .flat, views: 4, onOccluder: false).opacity - 0.9) < 1e-6)
    }

    @Test func `a view counts as new only past 15 degrees`() {
        func direction(_ degrees: Float) -> SIMD3<Float> {
            SIMD3(sin(degrees * .pi / 180), 0, cos(degrees * .pi / 180))
        }
        var views = ViewDirections()
        let added = [
            views.insert(direction(0) * 3),
            views.insert(direction(14)),
            views.insert(direction(16)),
            views.insert(direction(29)),  // 13 degrees from the 16-degree view
            views.insert(direction(-16)),
            views.insert(.zero),
        ]
        #expect(added == [true, false, true, false, true, false])
        #expect(views.count == 3)
    }

    @Test func `coverage counts 30 cm columns with a two-view wall dot`() {
        func dot(_ x: Float, views: Int, z: Float = 0, occluder: Bool = false) -> FieldDot {
            FieldDot(id: UInt64(bitPattern: Int64(x * 1000)), position: SIMD3(x, 1, z), kind: .flat, views: views, onOccluder: occluder)
        }
        let dots = [
            dot(-2.95, views: 2), dot(-2.9, views: 3),  // column 0, counted once
            dot(0.1, views: 1),                         // one view: not yet
            dot(2.99, views: 2),                        // column 19
            dot(3.5, views: 4),                         // outside the zone
            dot(1.0, views: 4, z: 0.5),                 // off the wall plane
            dot(1.5, views: 4, occluder: true),         // on the bin
        ]
        #expect(Coverage.columnCount == 20)
        #expect(abs(Coverage.fraction(of: dots) - 0.1) < 1e-6)
        #expect(Coverage.fraction(of: []) == 0)
    }

    @Test func `past the cap only flat dots are dropped`() {
        func sprite(_ id: UInt64, _ kind: DotKind) -> (sprite: DotSprite, kind: DotKind) {
            (DotSprite(id: id, position: .zero, kind: kind, onOccluder: false, birthTime: 0, fromOpacity: 0.45, toOpacity: 0.45,
                       opacityTime: 0, edgeSince: kind == .edge ? -.infinity : .infinity, deathTime: .infinity), kind)
        }
        let edges = (0..<UInt64(Tuning.dotCap - 10)).map { sprite($0, .edge) }
        let flats = (0..<UInt64(50)).map { sprite(1_000_000 + $0, .flat) }
        let kept = DotTimeline.Builder.capped(edges + flats)
        #expect(kept.count == Tuning.dotCap)
        #expect(kept.count { $0.kind == .edge } == edges.count)
        let expected = flats.map(\.sprite.id).sorted { StableHash.mix($0) < StableHash.mix($1) }.prefix(10)
        #expect(Set(kept.filter { $0.kind == .flat }.map(\.id)) == Set(expected))
    }
}
