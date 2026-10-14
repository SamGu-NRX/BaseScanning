import CoreGraphics
import Foundation
import ImageIO
import LiveDotsCore
import SwiftUI
import UniformTypeIdentifiers

/// Renders the replay offscreen at 1170 x 2532 with the same renderer and chrome as the app.
enum Exporter {
    static let framesPerSecond: Float = 30

    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    /// Every frame from keyframe 1 until the last keyframe's births finish. `destination` is a
    /// folder for frame_00000.png onward, or "-" for raw BGRA frames on stdout (what
    /// make-video.sh pipes into ffmpeg, so no frame folder ever touches the disk).
    static func exportReplay(to destination: String, options: Command.RenderOptions) throws {
        let data = try ReplayData.loadNow(folder: FixtureLocator.folder(options.fixture))
        let renderer = try DotRenderer(data: data)
        let target = try OffscreenTarget(renderer: renderer)
        let seconds = Schedule.end(count: data.keyframeCount) + Tuning.birthDuration + 0.15
        let frameCount = Int((seconds * framesPerSecond).rounded(.up))
        let toStdout = destination == "-"
        let folder = URL(fileURLWithPath: destination, isDirectory: true)
        if !toStdout { try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true) }

        for frame in 0..<frameCount {
            let time = Float(frame) / framesPerSecond
            let request = playbackRequest(at: time, data: data, options: options)
            try target.render(request)
            try composite(chrome(for: request, data: data), onto: target)
            if toStdout {
                FileHandle.standardOutput.write(Data(target.bytes))
            } else {
                try writePNG(target, to: folder.appendingPathComponent(String(format: "frame_%05d.png", frame)))
            }
            if frame % 30 == 0 {
                FileHandle.standardError.write(Data("frame \(frame + 1) of \(frameCount)\n".utf8))
            }
        }
    }

    /// The frame at playback `time`, as the video draws it.
    private static func playbackRequest(at time: Float, data: ReplayData, options: Command.RenderOptions) -> FrameRequest {
        FrameRequest(
            mode: options.mode, keyframe: Schedule.keyframe(at: time, count: data.keyframeCount), time: time,
            reduceMotion: options.reduceMotion, fog: options.fog, scheme: options.scheme,
            ambientTime: time, frameInterval: 1 / framesPerSecond)
    }

    /// One keyframe as a PNG. Without `at`, the keyframe settled: every animation finished and
    /// the fog lag caught up. With `at`, the moment `at` seconds after the keyframe appears, before
    /// the next one replaces it, reached by playing the replay from the start at 30 fps, so the
    /// fog's lag is what the video shows.
    static func exportStill(keyframe number: Int, at offset: Float?, to path: String, options: Command.RenderOptions) throws {
        let data = try ReplayData.loadNow(folder: FixtureLocator.folder(options.fixture))
        guard number <= data.keyframeCount else {
            throw Failure(description: "--still \(number) is past the last keyframe, \(data.keyframeCount)")
        }
        // Stills are named and compared by keyframe, so an offset that plays into the next
        // keyframe would label one keyframe's image with another's number, and one past the end
        // of playback shows a moment the replay never reaches.
        let shown = Schedule.onScreen(number - 1, count: data.keyframeCount)
        if let offset, offset >= shown {
            let next = number < data.keyframeCount ? " or --still \(number + 1)" : ""
            throw Failure(description: String(
                format: "--at %g is past keyframe %d, which is on screen for %g s; use a smaller --at",
                offset, number, shown) + next)
        }
        let renderer = try DotRenderer(data: data)
        let target = try OffscreenTarget(renderer: renderer)
        let state = data.timeline(options.mode).states[number - 1]
        let request: FrameRequest
        if let offset {
            let end = Schedule.start(of: number - 1) + offset
            var frame = 0
            while Float(frame + 1) / framesPerSecond < end {
                try target.render(playbackRequest(at: Float(frame) / framesPerSecond, data: data, options: options), readBack: false)
                frame += 1
            }
            request = playbackRequest(at: end, data: data, options: options)
        } else {
            request = FrameRequest(
                mode: options.mode, keyframe: number - 1, time: 1e5,
                reduceMotion: options.reduceMotion, fog: options.fog, scheme: options.scheme)
        }
        try target.render(request)
        try composite(chrome(for: request, data: data), onto: target)
        try writePNG(target, to: URL(fileURLWithPath: path))
        FileHandle.standardError.write(Data(
            "\(path): keyframe \(number), \(options.mode.rawValue), \(options.scheme.rawValue), \(state.sprites.count) sprites drawn, \(state.fieldCount) dots in field (\(state.edgeCount) edges), coverage \(Int((state.coverage * 100).rounded()))%\n".utf8))
    }

    private static func composite(_ chrome: CGImage?, onto target: OffscreenTarget) throws {
        guard let chrome else { return }
        let context = try target.context()
        context.draw(chrome, in: CGRect(x: 0, y: 0, width: target.width, height: target.height))
    }

    private static func writePNG(_ target: OffscreenTarget, to url: URL) throws {
        guard let image = try target.context().makeImage(),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { throw Failure(description: "can't write \(url.path)") }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw Failure(description: "can't write \(url.path)") }
    }

    /// The SwiftUI chrome for one frame. Rendered every frame: the boxes fade and the hold ring
    /// fills between keyframes.
    private static func chrome(for request: FrameRequest, data: ReplayData) -> CGImage? {
        let renderer = ImageRenderer(content: PhoneChrome(
            state: data.timeline(request.mode).states[request.keyframe],
            keyframe: data.replay.keyframes[request.keyframe],
            boxes: data.boxes[request.keyframe], time: request.time)
            .frame(width: CGFloat(OffscreenTarget.pointSize.x), height: CGFloat(OffscreenTarget.pointSize.y)))
        renderer.scale = CGFloat(OffscreenTarget.scale)
        return renderer.cgImage
    }
}
