import Foundation
import simd

/// House Scan's own checks of a 0.4 packet against the rules the capture API documents as
/// rejections: id and path patterns, the ARKit tier's required sections, listed files that exist
/// with their stated size and SHA-256, rigid poses, plausible intrinsics, still-to-keyframe links
/// and the tap replay. A packet that passes can still fail a server check this does not repeat.
public enum Packet04Check {
    /// The server's tap replay tolerance on origin and direction.
    public static let tapReplayTolerance = 1e-3

    public static func problems(_ p: Packet04.Packet, folder: URL?) -> [String] {
        var out: [String] = []
        if !Packet04.isStorageID(p.packetId) { out.append("packetId \(p.packetId) is not a StorageId") }
        if p.formatVersion != Packet04.formatVersion { out.append("formatVersion \(p.formatVersion)") }
        if p.keyframes.isEmpty { out.append("ARKit tier needs at least one keyframe") }
        if p.session.tier == .arkitLidar, !p.session.lidarAvailable { out.append("arkit_lidar needs lidarAvailable") }
        if p.session.worldAlignment != "gravity" { out.append("worldAlignment must be gravity") }
        if p.streams.imuRaw.rateHz < Packet04Streams.minimumIMUHz { out.append("imuRaw rateHz \(p.streams.imuRaw.rateHz) < 50") }
        if p.streams.arkitPoses.rateHz < Packet04Streams.minimumPoseHz { out.append("arkitPoses rateHz \(p.streams.arkitPoses.rateHz) < 10") }

        let epochs = Set(p.epochs.map(\.id))
        var listed: [String: Packet04.FileEntry] = [:]
        for entry in p.files {
            if !Packet04.isRelPath(entry.path) { out.append("file path \(entry.path) is not a RelPath") }
            if listed.updateValue(entry, forKey: entry.path) != nil { out.append("file \(entry.path) listed twice") }
        }
        func requireListed(_ path: String, _ role: Packet04.Role, _ place: String) {
            guard let entry = listed[path] else { return out.append("\(place) \(path) is not in files") }
            if entry.role != role { out.append("\(place) \(path) has role \(entry.role.rawValue), not \(role.rawValue)") }
        }

        var frames: [String: Packet04.Keyframe] = [:]
        var byTime: [Double: Packet04.Keyframe] = [:]
        for k in p.keyframes {
            if !Packet04.isSegmentID(k.id) { out.append("keyframe id \(k.id) is not a SegmentId") }
            if frames.updateValue(k, forKey: k.id) != nil { out.append("keyframe \(k.id) listed twice") }
            if !epochs.contains(k.epoch) { out.append("keyframe \(k.id) names unknown epoch \(k.epoch)") }
            requireListed(k.img, .keyframe, "keyframe \(k.id) image")
            if let depth = k.depth {
                requireListed(depth.file, .depth, "keyframe \(k.id) depth")
                if let confidence = depth.confidenceFile { requireListed(confidence, .confidence, "keyframe \(k.id) confidence") }
                if let entry = listed[depth.file], entry.bytes != depth.w * depth.h * 4 { out.append("keyframe \(k.id) depth size") }
            }
            guard let pose = matrix(k.pose) else { out.append("keyframe \(k.id) pose is not 16 finite numbers"); continue }
            if !PacketPose.isRigid(pose) { out.append("keyframe \(k.id) pose is not rigid") }
            if k.intrinsics.count != 4 {
                out.append("keyframe \(k.id) intrinsics")
            } else if let problem = PacketWriter.intrinsicsProblem(
                SIMD4(k.intrinsics.map(Float.init)), width: k.w, height: k.h) {
                out.append("keyframe \(k.id) intrinsics: \(problem)")
            }
            if let other = byTime[k.timestamp], other.epoch == k.epoch, other.pose != k.pose {
                out.append("keyframes \(other.id) and \(k.id) share timestamp \(k.timestamp) with different poses")
            }
            byTime[k.timestamp] = k
        }
        var stillIDs = Set<String>()
        for s in p.stills ?? [] {
            if !Packet04.isSegmentID(s.id) { out.append("still id \(s.id) is not a SegmentId") }
            stillIDs.insert(s.id)
            requireListed(s.img, .still, "still \(s.id) image")
            if let link = s.keyframe {
                guard let frame = frames[link] else { out.append("still \(s.id) names unknown keyframe \(link)"); continue }
                if frame.w != s.w || frame.h != s.h { out.append("still \(s.id) and keyframe \(link) sizes differ") }
            }
        }
        for tap in p.taps ?? [] {
            guard let frame = frames[tap.keyframe] else { out.append("tap \(tap.id) names unknown keyframe \(tap.keyframe)"); continue }
            if tap.pixel.count != 2 || !(0...Double(frame.w)).contains(tap.pixel[0]) || !(0...Double(frame.h)).contains(tap.pixel[1]) {
                out.append("tap \(tap.id) pixel is outside keyframe \(frame.id)")
                continue
            }
            if let epoch = tap.epoch, epoch != frame.epoch { out.append("tap \(tap.id) epoch differs from its keyframe's") }
            // Compared pairwise below, a missing or extra component, or a NaN, would go unseen.
            guard tap.rayOrigin.count == 3, tap.rayDirection.count == 3, (tap.rayOrigin + tap.rayDirection).allSatisfy(\.isFinite) else {
                out.append("tap \(tap.id) ray is not two finite 3-vectors")
                continue
            }
            let replay = ray(pose: frame.pose, intrinsics: frame.intrinsics, pixel: tap.pixel)
            let error = zip(replay.origin + replay.direction, tap.rayOrigin + tap.rayDirection).map { abs($0 - $1) }.max() ?? .infinity
            if !(error <= tapReplayTolerance) { out.append("tap \(tap.id) ray replays with error \(error)") }
        }
        for (name, close) in [("meterCloseUp", p.scaleReference.meterCloseUp), ("obliqueCloseUp", p.scaleReference.ext.obliqueCloseUp)] {
            if close.captured, !(close.still.map(stillIDs.contains) ?? false) { out.append("\(name) names a still that is not in the packet") }
        }
        requireListed(p.streams.imuRaw.file, .stream, "imuRaw")
        requireListed(p.streams.arkitPoses.file, .stream, "arkitPoses")

        if let folder {
            for entry in p.files {
                guard let data = try? Data(contentsOf: folder.appending(path: entry.path)) else { out.append("file \(entry.path) is missing"); continue }
                if data.count != entry.bytes { out.append("file \(entry.path) is \(data.count) bytes, listed \(entry.bytes)") }
                if PacketFiles.sha256(data) != entry.sha256 { out.append("file \(entry.path) SHA-256 differs from its listing") }
            }
        }
        return out
    }

    /// The ray through `pixel` as the server replays it: origin the camera position, direction
    /// the unit vector of R · ((u − cx)/fx, −(v − cy)/fy, −1).
    public static func ray(pose: [Double], intrinsics k: [Double], pixel: [Double]) -> (origin: [Double], direction: [Double]) {
        guard pose.count == 16, k.count == 4, pixel.count == 2 else { return ([], []) }
        let d = SIMD3((pixel[0] - k[2]) / k[0], -(pixel[1] - k[3]) / k[1], -1)
        let c0 = SIMD3(pose[0], pose[1], pose[2]), c1 = SIMD3(pose[4], pose[5], pose[6]), c2 = SIMD3(pose[8], pose[9], pose[10])
        let world = simd_normalize(c0 * d.x + c1 * d.y + c2 * d.z)
        return ([pose[12], pose[13], pose[14]], [world.x, world.y, world.z])
    }

    static func matrix(_ values: [Double]) -> simd_float4x4? {
        guard values.count == 16, values.allSatisfy(\.isFinite) else { return nil }
        let f = values.map(Float.init)
        return simd_float4x4(SIMD4(f[0], f[1], f[2], f[3]), SIMD4(f[4], f[5], f[6], f[7]), SIMD4(f[8], f[9], f[10], f[11]), SIMD4(f[12], f[13], f[14], f[15]))
    }
}
