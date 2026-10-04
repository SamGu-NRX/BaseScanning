import ARKit
import CoreImage
import Foundation
import HouseScanKit
import ImageIO
import RealityKit
import Synchronization
import SwiftUI

/// What the AR delegate should prepare with each sampled frame.
enum LiveMode: Sendable {
    /// Pose and quality only.
    case idle
    /// Encode a JPEG about three times a second while the phone is held on the meter.
    case closeUp
    /// Encode a JPEG when the phone has moved or turned enough that the frame could be kept.
    case walk
}

enum LiveEvent: Sendable {
    case interrupted
    case interruptionEnded
    case cameraDenied
    case failed(String)
}

struct VerticalPlaneHit {
    let position: SIMD3<Float>
    let normal: SIMD3<Float>
    let transform: simd_float4x4
    /// Which kind of plane the ray hit; the export widens the meter's error for an estimated one.
    let source: MeterPlaneSource
}

/// The live ARKit source: RealityKit's ARView running world tracking with `.gravity` alignment and
/// horizontal and vertical plane detection. On an iPhone with LiDAR it also turns on per-frame
/// scene depth, which coverage uses to tell a wall from a bush in front of it and the packet
/// records with each photo and a few times a second between photos, and the scene mesh with
/// per-face classification, which the packet carries; without LiDAR it runs without either.
@MainActor
final class LiveCapture {
    let arView: ARView
    let delegate: LiveSessionDelegate

    /// True when this phone gives per-frame LiDAR depth.
    static var supportsDepth: Bool { ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) }
    /// True when this phone builds a LiDAR mesh of the scene.
    static var supportsMesh: Bool { ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) }
    /// True when the mesh can carry ARKit's per-face classification (wall, floor, door, ...),
    /// which the packet's mesh records.
    static var supportsClassifiedMesh: Bool { ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) }
    /// True when this phone can hide virtual content behind a person standing in front of it.
    static var supportsPeopleOcclusion: Bool { ARWorldTrackingConfiguration.supportsFrameSemantics(.personSegmentationWithDepth) }

    /// The AR result in the camera view, while one is shown (`showResult`).
    private var result: AnchorEntity?
    /// An empty entity in the result at the point it has to put on screen (the battery's
    /// middle), for `resultIsDrawn`.
    private var resultFocus: Entity?
    /// The last `resultIsDrawn` findings logged, so they are logged when they change.
    private var lastDrawnReport: String?

    init(onFrame: @escaping @MainActor @Sendable (SourceFrame) -> Void, onEvent: @escaping @MainActor @Sendable (LiveEvent) -> Void) {
        arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        delegate = LiveSessionDelegate(onFrame: onFrame, onEvent: onEvent)
        arView.session.delegateQueue = DispatchQueue(label: "dev.housescanning.housescan.ar-delegate", qos: .userInitiated)
        arView.session.delegate = delegate
        arView.renderOptions.insert(.disableMotionBlur)
    }

    /// People occlusion is off: no caller asks for it. Turning it on while the AR result showed
    /// re-ran the session under the result's freshly added anchor entity, and build 4.1 drew no
    /// result on the phone it was tested on (#67). Whether and how to bring it back is #41.
    private static func configuration(occludingPeople: Bool = false) -> ARWorldTrackingConfiguration {
        let configuration = ARWorldTrackingConfiguration()
        // .gravity, not .gravityAndHeading: compass heading drifts near a house's metal and wiring.
        configuration.worldAlignment = .gravity
        configuration.planeDetection = [.horizontal, .vertical]
        if supportsClassifiedMesh {
            configuration.sceneReconstruction = .meshWithClassification
        } else if supportsMesh {
            configuration.sceneReconstruction = .mesh
        }
        if supportsDepth { configuration.frameSemantics.insert(.sceneDepth) }
        if occludingPeople, supportsPeopleOcclusion { configuration.frameSemantics.insert(.personSegmentationWithDepth) }
        return configuration
    }

    /// What the running session was configured with, for the packet's `session.device`.
    struct Settings: Sendable {
        var sceneDepth: Bool
        var mesh: Bool
        /// The mesh carries ARKit's per-face classification: `sceneReconstruction` included
        /// `.meshWithClassification`, not only `.mesh`.
        var meshClassification: Bool
        /// The camera's frame rate: the trajectory's nominal rate.
        var framesPerSecond: Int
    }

    var settings: Settings {
        let running = arView.session.configuration as? ARWorldTrackingConfiguration ?? Self.configuration()
        return Settings(
            sceneDepth: running.frameSemantics.contains(.sceneDepth),
            mesh: running.sceneReconstruction.contains(.mesh),
            meshClassification: running.sceneReconstruction.contains(.meshWithClassification),
            framesPerSecond: running.videoFormat.framesPerSecond
        )
    }

    /// Where every ARFrame's pose goes as it arrives (`CaptureRecorder.recordPose`).
    func setRecorder(_ recorder: CaptureRecorder?) {
        delegate.shared.withLock { $0.recorder = recorder }
    }

    func start() {
        RuntimeLog.engine.info("LiDAR: scene depth \(Self.supportsDepth ? "on" : "not available", privacy: .public), mesh \(Self.supportsMesh ? "on" : "not available", privacy: .public)")
        arView.session.run(Self.configuration())
    }

    func pause() {
        arView.session.pause()
    }

    /// Starts world tracking over with a fresh map, after relocalization failed. The old mesh goes
    /// with the old anchors.
    func restart() {
        arView.session.run(Self.configuration(), options: [.resetTracking, .removeExistingAnchors])
        delegate.shared.withLock { $0.meterAnchorID = nil }
    }

    /// Shows `model` in the camera view, hung on the meter's anchor so it follows ARKit's
    /// corrections to it. The model is in world axes with the meter at its origin, as they stood
    /// when the anchor was at `pose` (`MeterAnchorTracking.pose`, the pose the engine's wall
    /// agrees with). It is attached in that pose's frame, so a turn ARKit has made to the anchor
    /// since carries the model with it. Cancelling the anchor's current turn instead drew the
    /// wall as it stood before the turn. On a phone with LiDAR the scene mesh hides it where
    /// something stands in front of it. `focus` is the point the model has to put on screen, in
    /// the model's coordinates (`resultIsDrawn`).
    ///
    /// True means the model was handed to RealityKit, not that anything is drawn: build 4.1 took
    /// it for that and hid the screen's own drawing on a phone where nothing appeared (#67).
    /// False when the meter has no anchor in the session; nothing is shown then.
    func showResult(_ model: Entity, builtFor pose: simd_float4x4, focus: SIMD3<Float>) -> Bool {
        removeResult()
        guard let id = delegate.shared.withLock({ $0.meterAnchorID }),
              arView.session.currentFrame?.anchors.contains(where: { $0.identifier == id }) == true else {
            RuntimeLog.engine.info("AR result not shown: the meter's anchor is not in the session")
            return false
        }
        let holder = AnchorEntity(.anchor(identifier: id))
        model.orientation = simd_quatf(pose).inverse
        let marker = Entity()
        marker.position = focus
        model.addChild(marker)
        holder.addChild(model)
        arView.scene.addAnchor(holder)
        result = holder
        resultFocus = marker
        lastDrawnReport = nil
        setOcclusion(true)
        return true
    }

    /// Whether RealityKit can be drawing the shown result now: its anchor entity is anchored to
    /// the meter's ARKit anchor, it is enabled, and the result's focus point lands in front of
    /// the camera and inside the view. A check of the chain, not of pixels: RealityKit says
    /// nothing about what it drew. The findings are logged when they change. `held` is the chain
    /// without the view: the AR scene still has the result, whether or not it is in view
    /// (`ResultOverlayPolicy.update(drawn:held:time:)`). `anchored` leaves out whether it is
    /// enabled: the engine disables it while tracking is limited (`setResultVisible`).
    func resultIsDrawn() -> (drawn: Bool, held: Bool, anchored: Bool) {
        guard let result, let resultFocus else { return (false, false, false) }
        let id = delegate.shared.withLock { $0.meterAnchorID }
        let inSession = id.map { id in arView.session.currentFrame?.anchors.contains(where: { $0.identifier == id }) == true } ?? false
        let anchored = result.isAnchored
        let enabled = result.isEnabledInHierarchy
        let world = resultFocus.position(relativeTo: nil)
        let inCamera = arView.cameraTransform.matrix.inverse * SIMD4(world, 1)
        let point = inCamera.z < 0 ? arView.project(world) : nil
        let onScreen = point.map { arView.bounds.contains($0) } ?? false
        let report = "anchor in session \(inSession), anchored \(anchored), enabled \(enabled), on screen \(onScreen)"
        if report != lastDrawnReport {
            lastDrawnReport = report
            let at = point.map { String(format: "(%.0f, %.0f) in %.0f x %.0f", Double($0.x), Double($0.y), Double(arView.bounds.width), Double(arView.bounds.height)) } ?? "none"
            RuntimeLog.engine.info("AR result: \(report, privacy: .public), projected at \(at, privacy: .public)")
        }
        return (anchored && enabled && onScreen, anchored && enabled, anchored)
    }

    func hideResult() {
        removeResult()
        setOcclusion(false)
    }

    /// Hides the shown result without taking it out, while the anchor's pose can't be trusted.
    func setResultVisible(_ visible: Bool) {
        result?.isEnabled = visible
    }

    private func removeResult() {
        result?.removeFromParent()
        result = nil
        resultFocus = nil
    }

    /// The LiDAR mesh hides the result where something real stands in front of it. A RealityKit
    /// setting only: the session is not re-run (see `configuration`).
    private func setOcclusion(_ on: Bool) {
        guard Self.supportsMesh else { return }
        if on {
            arView.environment.sceneUnderstanding.options.insert(.occlusion)
        } else {
            arView.environment.sceneUnderstanding.options.remove(.occlusion)
        }
    }

    /// ARKit's mesh in world meters, with one `ARMeshClassification` raw value per triangle (0,
    /// none, when the session runs without classification).
    struct MeshSnapshot: Sendable {
        var mesh: TriangleMesh
        var classification: [UInt8]
    }

    /// The LiDAR mesh ARKit has built so far, in world meters, or nil when there is none (no
    /// LiDAR, or nothing reconstructed yet). Each anchor's vertices are read out of its Metal
    /// buffer, never written, and moved into world coordinates by the anchor's transform.
    func meshSnapshot() -> MeshSnapshot? {
        guard let anchors = arView.session.currentFrame?.anchors.compactMap({ $0 as? ARMeshAnchor }), !anchors.isEmpty else { return nil }
        var vertices: [SIMD3<Float>] = []
        var indices: [UInt32] = []
        var classification: [UInt8] = []
        for anchor in anchors {
            let source = anchor.geometry.vertices
            let faces = anchor.geometry.faces
            guard source.format == .float3, faces.indexCountPerPrimitive == 3, faces.bytesPerIndex == 4 || faces.bytesPerIndex == 2 else {
                RuntimeLog.engine.error("mesh anchor skipped: vertex format \(source.format.rawValue), \(faces.indexCountPerPrimitive) indices per face, \(faces.bytesPerIndex) bytes per index")
                continue
            }
            let first = UInt32(vertices.count)
            let points = UnsafeRawPointer(source.buffer.contents()).advanced(by: source.offset)
            for i in 0..<source.count {
                let p = points.advanced(by: i * source.stride)
                let local = SIMD4<Float>(
                    p.loadUnaligned(as: Float.self), p.loadUnaligned(fromByteOffset: 4, as: Float.self),
                    p.loadUnaligned(fromByteOffset: 8, as: Float.self), 1
                )
                let world = anchor.transform * local
                vertices.append(SIMD3(world.x, world.y, world.z))
            }
            let raw = UnsafeRawPointer(faces.buffer.contents())
            for i in 0..<(faces.count * 3) {
                let index = faces.bytesPerIndex == 4
                    ? raw.loadUnaligned(fromByteOffset: i * 4, as: UInt32.self)
                    : UInt32(raw.loadUnaligned(fromByteOffset: i * 2, as: UInt16.self))
                indices.append(first + index)
            }
            classification += Self.faceClasses(anchor.geometry)
        }
        return vertices.isEmpty ? nil : MeshSnapshot(mesh: TriangleMesh(vertices: vertices, indices: indices), classification: classification)
    }

    /// One class byte per face of `geometry`: its classification source when it has one in the
    /// layout ARKit documents (one uchar per face), else 0 (none) for every face.
    private static func faceClasses(_ geometry: ARMeshGeometry) -> [UInt8] {
        let faces = geometry.faces.count
        guard let source = geometry.classification, source.format == .uchar, source.count == faces, source.componentsPerVector == 1 else {
            return [UInt8](repeating: 0, count: faces)
        }
        let base = UnsafeRawPointer(source.buffer.contents()).advanced(by: source.offset)
        return (0..<faces).map { base.load(fromByteOffset: $0 * source.stride, as: UInt8.self) }
    }

    /// A plane anchor as ARKit reports it, copied off the session: world meters for the anchor,
    /// the anchor's own coordinates for everything else. `PacketPlane.init(anchorToMeter:...)`
    /// turns it into the packet's plane, whose pose sits at the extent's centre.
    struct PlaneSnapshot: Sendable {
        var id: String
        var vertical: Bool
        /// Nil for a class ARKit adds after iOS 26.
        var classification: PacketPlane.Classification?
        /// `ARPlaneAnchor.transform`: anchor to world.
        var anchorToWorld: simd_float4x4
        /// `ARPlaneAnchor.center`, anchor coordinates.
        var center: SIMD3<Float>
        /// `ARPlaneExtent.rotationOnYAxis`, radians.
        var rotationOnYAxis: Float
        /// `ARPlaneExtent.width` and `height`.
        var extent: SIMD2<Float>
        /// `ARPlaneGeometry.boundaryVertices`, anchor coordinates.
        var boundary: [SIMD3<Float>]
    }

    /// Every plane ARKit has detected, as it stands now.
    func planeSnapshot() -> [PlaneSnapshot] {
        guard let anchors = arView.session.currentFrame?.anchors.compactMap({ $0 as? ARPlaneAnchor }) else { return [] }
        return anchors.map { plane in
            PlaneSnapshot(
                id: plane.identifier.uuidString, vertical: plane.alignment == .vertical,
                classification: Self.name(plane.classification), anchorToWorld: plane.transform, center: plane.center,
                rotationOnYAxis: plane.planeExtent.rotationOnYAxis,
                extent: SIMD2(plane.planeExtent.width, plane.planeExtent.height), boundary: plane.geometry.boundaryVertices
            )
        }
    }

    private static func name(_ classification: ARPlaneAnchor.Classification) -> PacketPlane.Classification? {
        switch classification {
        case .none: PacketPlane.Classification.none
        case .wall: .wall
        case .floor: .floor
        case .ceiling: .ceiling
        case .table: .table
        case .seat: .seat
        case .window: .window
        case .door: .door
        @unknown default: nil
        }
    }

    func setMode(_ mode: LiveMode) {
        delegate.shared.withLock { $0.mode = mode }
    }

    /// Raycast from a view point to a vertical surface: a detected plane's extent first, then a
    /// vertical plane ARKit estimates from feature points at the tap. Never `.existingPlaneInfinite`,
    /// which extends a fence's or another wall's plane past its edges, so a tap beside it lands on
    /// a surface that isn't there. The hit says which kind it was.
    func raycastVerticalPlane(from point: CGPoint) -> VerticalPlaneHit? {
        let targets: [(ARRaycastQuery.Target, MeterPlaneSource)] = [(.existingPlaneGeometry, .detectedPlane), (.estimatedPlane, .estimatedPlane)]
        for (target, source) in targets {
            guard let result = arView.raycast(from: point, allowing: target, alignment: .vertical).first else { continue }
            // An estimated plane has no anchor; the result's own y axis is the surface normal.
            let transform = result.worldTransform
            let planeTransform = result.anchor?.transform ?? transform
            let normal = simd_normalize(SIMD3(planeTransform.columns.1.x, planeTransform.columns.1.y, planeTransform.columns.1.z))
            return VerticalPlaneHit(
                position: SIMD3(transform.columns.3.x, transform.columns.3.y, transform.columns.3.z),
                normal: normal, transform: transform, source: source
            )
        }
        return nil
    }

    /// Anchors the meter so ARKit keeps refining its position; returns the anchor's id.
    func addMeterAnchor(at transform: simd_float4x4) -> UUID {
        let anchor = ARAnchor(name: "meter", transform: transform)
        arView.session.add(anchor: anchor)
        delegate.shared.withLock { $0.meterAnchorID = anchor.identifier }
        return anchor.identifier
    }

    func removeAnchor(_ id: UUID) {
        if let anchor = arView.session.currentFrame?.anchors.first(where: { $0.identifier == id }) {
            arView.session.remove(anchor: anchor)
        }
        delegate.shared.withLock { $0.meterAnchorID = nil }
    }
}

/// Shared between the main actor and the AR delegate queue.
struct LiveShared: Sendable {
    var mode: LiveMode = .idle
    var meterAnchorID: UUID?
    var recorder: CaptureRecorder?
}

/// Receives ARSession callbacks on a private serial queue. Each sampled frame is reduced to a
/// Sendable `SourceFrame` there (pose, intrinsics, a luma quality measure and, when it could be
/// kept, a JPEG of the unrotated sensor image) and then sent to the main actor. ARKit objects
/// never leave the delegate queue, except the camera image of the one frame being encoded.
///
/// JPEG encoding runs on its own queue, one frame at a time. Encoding on the delegate queue held
/// it for the length of an encode, so ARKit's next frames queued up, each holding a camera buffer
/// from ARKit's small pool. A keyframe candidate that arrives while an encode is running is sent
/// without a photo instead of waiting: it can't be kept, and the next sampled frame can be.
final class LiveSessionDelegate: NSObject, ARSessionDelegate, Sendable {
    let shared = Mutex(LiveShared())
    private let onFrame: @MainActor @Sendable (SourceFrame) -> Void
    private let onEvent: @MainActor @Sendable (LiveEvent) -> Void
    private let queueState = Mutex(QueueState())
    private let context = CIContext(options: [.cacheIntermediates: false])
    /// Serial, and every frame the main actor gets passes through it, pose-only ones too, then
    /// through the main queue (`deliver`), which is FIFO: frames reach the engine in capture
    /// order even when a sampled frame waited on its JPEG. A pose-only frame used to go straight
    /// to the main actor and overtake the sampled frame before it; the engine then saw time run
    /// back. The cost is that a pose may wait behind one encode.
    private let encodeQueue = DispatchQueue(label: "dev.housescanning.housescan.jpeg", qos: .userInitiated)

    private struct QueueState {
        var frameCount = 0
        var lastEncode: (time: Double, camera: CameraFrame)?
        var encoding = false
    }

    /// The camera image handed to the encode queue. CVPixelBuffer is not Sendable; ARKit doesn't
    /// write to a delivered frame's image, and only the encode queue reads it. The meter tap's
    /// snapshot hands its one image off the main actor the same way.
    struct PixelBufferBox: @unchecked Sendable {
        let buffer: CVPixelBuffer
    }

    /// Every sixth frame (about 10 per second at 60 fps) is sampled for capture; more adds cost,
    /// not coverage. Every second frame carries the pose, so overlays drawn over the 60 fps camera
    /// view move at 30 fps instead of 10.
    private static let sampleEvery = 6
    private static let poseEvery = 2
    /// A walk JPEG is encoded once the phone moved 0.15 m or turned 5° since the last one: well
    /// under auto-capture's 0.5 m / 15° spacing, so no keepable frame lacks an image.
    private static let encodeMove: Float = 0.15
    private static let encodeTurn: Float = 5 * .pi / 180
    private static let encodeInterval = 0.3
    /// A walk JPEG is also encoded after this long without one, moved or not. The overhead
    /// question is only asked on a frame with a photo (the answer stores it), and a phone held
    /// still while tilted up would otherwise never get one. A guess: one encode a second while
    /// standing still costs little; not measured on a phone.
    private static let encodeStill = 1.0

    init(onFrame: @escaping @MainActor @Sendable (SourceFrame) -> Void, onEvent: @escaping @MainActor @Sendable (LiveEvent) -> Void) {
        self.onFrame = onFrame
        self.onEvent = onEvent
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let count = queueState.withLock { state -> Int in
            state.frameCount += 1
            return state.frameCount
        }
        let shared = shared.withLock { $0 }
        let tracking = Self.tracking(frame.camera.trackingState)
        // Every frame's pose goes to the packet's trajectory, before any sampling, with that frame's
        // own calibration at the same t.
        shared.recorder?.recordPose(
            t: frame.timestamp, tracking: TrackingCode(tracking), cameraToWorld: frame.camera.transform, intrinsics: frame.camera.intrinsics,
            imageWidth: Double(frame.camera.imageResolution.width), imageHeight: Double(frame.camera.imageResolution.height))
        let intrinsics = frame.camera.intrinsics
        let resolution = frame.camera.imageResolution
        let camera = CameraFrame(
            cameraToWorld: frame.camera.transform,
            intrinsics: SIMD4(intrinsics.columns.0.x, intrinsics.columns.1.y, intrinsics.columns.2.x, intrinsics.columns.2.y),
            imageSize: SIMD2(Float(resolution.width), Float(resolution.height))
        )
        if let recorder = shared.recorder, tracking == .normal {
            Self.recordDepthFrame(frame, camera: camera, into: recorder)
        }
        guard count % Self.poseEvery == 0 else { return }
        let meterAnchor = shared.meterAnchorID.flatMap { id in frame.anchors.first { $0.identifier == id }?.transform }
        guard count % Self.sampleEvery == 0 else {
            let pose = SourceFrame(
                id: "live-\(count)", timestamp: frame.timestamp, camera: camera, tracking: tracking,
                quality: nil, jpeg: .none, still: nil, meterAnchor: meterAnchor, meterAnchorID: shared.meterAnchorID, isPoseOnly: true
            )
            encodeQueue.async { [self, pose] in deliver(pose) }
            return
        }
        let quality = Self.quality(frame.capturedImage)
        let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
        let ground = planes.filter { $0.alignment == .horizontal }.map(Self.groundEvidence)
        let walls = planes.filter { $0.alignment == .vertical }.map(Self.wallEvidence)
        var snapshot = SourceFrame(
            id: "live-\(count)", timestamp: frame.timestamp, camera: camera, tracking: tracking,
            quality: quality, jpeg: .none, still: nil, meterAnchor: meterAnchor, meterAnchorID: shared.meterAnchorID,
            planes: PlaneSnapshot(ground: ground, walls: walls)
        )
        let image = tracking == .normal && shouldEncode(mode: shared.mode, time: frame.timestamp, camera: camera)
            ? PixelBufferBox(buffer: frame.capturedImage) : nil
        // Depth and camera settings go with the photo: a frame without one can't be kept. Copied
        // here, so the ARFrame is not held past this callback.
        if image != nil {
            snapshot.exposure = Self.exposure(frame)
            if let depth = frame.sceneDepth, let copied = Self.depthCopy(depth) {
                // The packet takes ARKit's depth only with its confidence (required from 1.1);
                // coverage uses the map either way.
                snapshot.sensorDepth = copied.confidence == nil ? nil : copied
                snapshot.depth = DepthImage(
                    meters: copied.meters, width: copied.width, height: copied.height, confidence: copied.confidence,
                    intrinsics: DepthImage.intrinsics(scaling: camera.intrinsics, from: camera.imageSize, toWidth: copied.width, height: copied.height)
                )
            }
        }
        encodeQueue.async { [self, snapshot] in
            var delivered = snapshot
            if let image {
                if let data = encode(image.buffer) { delivered.jpeg = .data(data) }
                queueState.withLock { $0.encoding = false }
            }
            deliver(delivered)
        }
    }

    /// Hands a frame to the engine on the main queue. Called only from `encodeQueue`, in order;
    /// `DispatchQueue.main` runs blocks in the order they were queued, where separate `Task`s
    /// aimed at the main actor carry no such promise.
    private func deliver(_ frame: SourceFrame) {
        DispatchQueue.main.async { [onFrame] in
            MainActor.assumeIsolated { onFrame(frame) }
        }
    }

    /// A depth frame for the packet: LiDAR depth between photos, a few a second, from frames with
    /// normal tracking, so each sits on the trajectory with a pose worth fusing. The recorder's
    /// budget decides first, so a frame it would drop is never copied. The intrinsics are the
    /// camera's scaled to the depth map's grid, which covers the same view.
    private static func recordDepthFrame(_ frame: ARFrame, camera: CameraFrame, into recorder: CaptureRecorder) {
        guard let depth = frame.sceneDepth, recorder.wantsDepthFrame(at: frame.timestamp),
              let copied = depthCopy(depth), copied.confidence != nil else { return }
        recorder.recordDepthFrame(
            t: frame.timestamp, tracking: TrackingCode(.normal), cameraToWorld: frame.camera.transform,
            intrinsics: DepthImage.intrinsics(scaling: camera.intrinsics, from: camera.imageSize, toWidth: copied.width, height: copied.height),
            depth: copied
        )
    }

    /// Whether to encode this frame; claims the single encode slot when it says yes.
    private func shouldEncode(mode: LiveMode, time: Double, camera: CameraFrame) -> Bool {
        queueState.withLock { state in
            guard !state.encoding else { return false }
            let decision: Bool
            switch mode {
            case .idle:
                decision = false
            case .closeUp:
                decision = time - (state.lastEncode?.time ?? -.infinity) >= Self.encodeInterval
            case .walk:
                if let last = state.lastEncode {
                    let moved = simd_distance(camera.position, last.camera.position) >= Self.encodeMove
                        || camera.rotationAngle(to: last.camera) >= Self.encodeTurn
                    decision = (moved && time - last.time >= Self.encodeInterval) || time - last.time >= Self.encodeStill
                } else {
                    decision = true
                }
            }
            if decision {
                state.lastEncode = (time, camera)
                state.encoding = true
            }
            return decision
        }
    }

    /// JPEG of the sensor image as captured: landscape, unrotated, matching the intrinsics. The
    /// walk's keyframes and the meter tap's snapshot both use it.
    func encode(_ buffer: CVPixelBuffer) -> Data? {
        let image = CIImage(cvPixelBuffer: buffer)
        let space = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        let options = [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.8]
        return context.jpegRepresentation(of: image, colorSpace: space, options: options)
    }

    /// Sharpness and exposure from the luma plane, sampling every 8th pixel of every 8th row
    /// (240 x 180 for a 1920 x 1440 buffer).
    private static func quality(_ buffer: CVPixelBuffer) -> FrameQuality? {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard CVPixelBufferGetPlaneCount(buffer) >= 1, let base = CVPixelBufferGetBaseAddressOfPlane(buffer, 0) else { return nil }
        let width = CVPixelBufferGetWidthOfPlane(buffer, 0)
        let height = CVPixelBufferGetHeightOfPlane(buffer, 0)
        let bytesPerRow = CVPixelBufferGetBytesPerRowOfPlane(buffer, 0)
        guard width >= 8, height >= 8 else { return nil }
        let raw = UnsafeRawBufferPointer(start: base, count: bytesPerRow * height)
        return FrameQuality(LumaImage(sampling: raw, width: width, height: height, bytesPerRow: bytesPerRow, step: 8))
    }

    /// The camera settings of the frame: exposure time and offset from ARCamera, and ISO, focal
    /// length and f-number from the frame's EXIF (`kCGImagePropertyExif*` keys) when present.
    private static func exposure(_ frame: ARFrame) -> PhotoExposure {
        let exif = frame.exifData
        func number(_ key: CFString) -> Double? {
            (exif[key as String] as? NSNumber)?.doubleValue ?? (exif[key as String] as? [NSNumber])?.first?.doubleValue
        }
        let duration = frame.camera.exposureDuration
        return PhotoExposure(
            durationS: duration > 0 ? duration : nil,
            offsetEV: Double(frame.camera.exposureOffset),
            iso: number(kCGImagePropertyExifISOSpeedRatings).flatMap { $0 > 0 ? $0 : nil },
            focalLengthMM: number(kCGImagePropertyExifFocalLength).flatMap { $0 > 0 ? $0 : nil },
            fNumber: number(kCGImagePropertyExifFNumber).flatMap { $0 > 0 ? $0 : nil }
        )
    }

    /// The LiDAR depth map (Float32 meters along the camera's -z, 256 x 192 on current iPhones)
    /// with its confidence, copied row by row. The depth map covers the same view as the camera
    /// image at a lower resolution, so coverage's copy takes the camera's intrinsics scaled by the
    /// size ratio on each axis (`DepthImage.intrinsics(scaling:...)`). ARKit writes no value it
    /// could not measure as NaN; those become 0, "no measurement" in the packet.
    private static func depthCopy(_ data: ARDepthData) -> DepthPacket? {
        let map = data.depthMap
        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        guard CVPixelBufferGetPixelFormatType(map) == kCVPixelFormatType_DepthFloat32, let base = CVPixelBufferGetBaseAddress(map) else { return nil }
        let width = CVPixelBufferGetWidth(map)
        let height = CVPixelBufferGetHeight(map)
        let rowBytes = CVPixelBufferGetBytesPerRow(map)
        guard width > 0, height > 0 else { return nil }
        var meters = [Float](repeating: 0, count: width * height)
        for y in 0..<height {
            let row = UnsafeRawPointer(base).advanced(by: y * rowBytes)
            for x in 0..<width {
                let value = row.loadUnaligned(fromByteOffset: x * 4, as: Float32.self)
                meters[y * width + x] = value.isFinite && value > 0 ? value : 0
            }
        }
        var confidence: [UInt8]?
        if let levels = data.confidenceMap {
            CVPixelBufferLockBaseAddress(levels, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(levels, .readOnly) }
            if CVPixelBufferGetPixelFormatType(levels) == kCVPixelFormatType_OneComponent8,
               CVPixelBufferGetWidth(levels) == width, CVPixelBufferGetHeight(levels) == height,
               let levelBase = CVPixelBufferGetBaseAddress(levels) {
                let levelRowBytes = CVPixelBufferGetBytesPerRow(levels)
                var values = [UInt8](repeating: 0, count: width * height)
                for y in 0..<height {
                    let row = UnsafeRawPointer(levelBase).advanced(by: y * levelRowBytes)
                    for x in 0..<width { values[y * width + x] = row.load(fromByteOffset: x, as: UInt8.self) }
                }
                confidence = values
            }
        }
        return DepthPacket(meters: meters, width: width, height: height, confidence: confidence, source: .arkitSceneDepth)
    }

    private static func tracking(_ state: ARCamera.TrackingState) -> TrackingQuality {
        switch state {
        case .notAvailable: .notAvailable
        case .normal: .normal
        case .limited(let reason):
            switch reason {
            case .initializing: .limited(.initializing)
            case .excessiveMotion: .limited(.excessiveMotion)
            case .insufficientFeatures: .limited(.insufficientFeatures)
            case .relocalizing: .limited(.relocalizing)
            @unknown default: .limited(.unknown)
            }
        }
    }

    /// A horizontal plane as the ground choice reads it (`GroundPlaneChoice`): its height, its
    /// class and its outline in plan, world meters.
    private static func groundEvidence(_ plane: ARPlaneAnchor) -> GroundPlaneEvidence {
        let center = plane.transform * SIMD4(plane.center, 1)
        let kind: GroundPlaneEvidence.Kind = switch plane.classification {
        case .floor: .floor
        case .table, .seat: .furniture
        case .none: .unclassified
        default: .other
        }
        let boundary = plane.geometry.boundaryVertices.map { vertex -> SIMD2<Float> in
            let world = plane.transform * SIMD4(vertex, 1)
            return SIMD2(world.x, world.z)
        }
        return GroundPlaneEvidence(y: center.y, kind: kind, boundary: boundary, id: plane.identifier.uuidString)
    }

    /// A vertical plane as the meter tap's checks read it (`MeterTap`): its class, a point on it,
    /// its normal (the anchor's y axis) and its outline, world meters. On a phone without plane
    /// classification every plane is `.none`, so every one is unclassified.
    private static func wallEvidence(_ plane: ARPlaneAnchor) -> WallPlaneEvidence {
        let center = plane.transform * SIMD4(plane.center, 1)
        let normal = plane.transform.columns.1
        let kind: WallPlaneEvidence.Kind = switch plane.classification {
        case .wall: .wall
        case .none: .unclassified
        default: .other
        }
        let boundary = plane.geometry.boundaryVertices.map { vertex -> SIMD3<Float> in
            let world = plane.transform * SIMD4(vertex, 1)
            return SIMD3(world.x, world.y, world.z)
        }
        return WallPlaneEvidence(
            id: plane.identifier.uuidString, kind: kind, center: SIMD3(center.x, center.y, center.z),
            normal: SIMD3(normal.x, normal.y, normal.z), boundary: boundary
        )
    }

    // MARK: Session events

    func sessionShouldAttemptRelocalization(_ session: ARSession) -> Bool {
        // Resume the walk in the same world frame after an interruption (checklist R4).
        true
    }

    func sessionWasInterrupted(_ session: ARSession) {
        Task { @MainActor [onEvent] in onEvent(.interrupted) }
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        Task { @MainActor [onEvent] in onEvent(.interruptionEnded) }
    }

    func session(_ session: ARSession, didFailWithError error: any Error) {
        let event: LiveEvent
        if let arError = error as? ARError, arError.code == .cameraUnauthorized {
            event = .cameraDenied
        } else {
            event = .failed(error.localizedDescription)
        }
        Task { @MainActor [onEvent] in onEvent(event) }
    }
}

/// The ARView as a SwiftUI view.
struct LiveCameraView: UIViewRepresentable {
    let arView: ARView

    func makeUIView(context: Context) -> ARView { arView }
    func updateUIView(_ view: ARView, context: Context) {}
}
