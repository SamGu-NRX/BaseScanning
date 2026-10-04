import CoreGraphics
import Foundation
import HouseScanKit
import OSLog
import simd

/// Drives the intents through the whole flow on a replay, for UI tests and demos (`-autopilot`).
///
/// It calls the same `ScanActions` a homeowner's taps call. Taps are simulated at view points
/// computed by projecting chosen wall points through the shown replay frame, so the engine
/// computes the geometry exactly as it does for a finger. Each state is held for about a second
/// so screenshots show it.
@MainActor
final class Autopilot {
    private let engine: ScanEngine
    /// A portrait iPhone screen in points. Any size works: view points and the engine's mapping
    /// back to pixels use the same size.
    private let viewSize = CGSize(width: 393, height: 852)
    private var hold: Double { engine.options.autopilotHold }
    /// Set once a spot check was answered "Something's there" (`-autopilotSomethingThere`) or "I
    /// can't check this area" (`-autopilotCannotCheck`): the checks after it are answered "It's
    /// clear", so a server that keeps moving the spot can't keep the run going.
    private var withheldClear = false

    init(engine: ScanEngine) {
        self.engine = engine
    }

    func run() async {
        guard engine.options.replayFolder != nil else {
            log("needs -replay; the live camera can't be driven")
            return
        }
        guard await waitUntil(timeout: 60, { self.engine.replay != nil || self.engine.state.failure != nil }), let replay = engine.replay else {
            return fail("replay did not load")
        }
        async let prepared: Void = replay.prepareHeldBack(endsWherePhoneStood: engine.options.autopilotCantGetThere)
        await pause(hold)
        await engine.waitForGate(.onboarding)
        engine.finishOnboarding()
        await prepared
        if let window = replay.heldBack {
            log("holding back frames \(window.frames.lowerBound)..<\(window.frames.upperBound) for the gap loop; gap \(window.gap.band.rawValue) \(format(window.gap.span))")
        } else {
            log("no frames can be held back to make a closable gap on this replay; the gap step will be skipped")
        }
        await pause(hold)

        await engine.waitForGate(.findMeter)
        // Sending photos is the homeowner's answer, never the autopilot's: a UI test taps it.
        if engine.state.photoProcessing?.consent == .asking {
            guard await waitUntil(timeout: 120, { self.engine.state.photoProcessing?.consent != .asking }) else {
                return fail("nobody answered whether to send the scan's photos")
            }
        }
        engine.markMeter(at: nil, viewSize: viewSize)
        guard await waitFor(.meterCloseUp, timeout: 10) else { return fail("meter was not marked") }
        await takeCloseUp()
        guard await waitFor(.wallWalk, timeout: 150) else { return fail("walk did not start") }
        if engine.options.autopilotCantGetThere { await skipGroundByMeterBeforeItIsSeen(replay) }

        await pause(0.5)
        guard await waitUntil(timeout: 120, { !replay.isPlaying }) else { return fail("walk replay did not finish") }
        await pause(1.0)

        await markFeatures(replay)
        if engine.options.autopilotCantGetThere {
            await endWalkByCantGetThere(replay)
        } else {
            await markEnds(replay)
        }
        await tiltUp()
        await pause(1.0)
        await engine.waitForGate(.wallWalk)
        engine.finishWalk()
        guard await waitFor(.markFeatures, timeout: 5) else { return fail("could not finish the walk (ends marked: \(engine.bothEndsMarked))") }
        await pause(hold)

        await engine.waitForGate(.markFeatures)
        // Mulch, so the replay's scene carries a ground patch over the ground its walk saw.
        // Answered after the UI test has finished with the screen, so its audit reads a still tree.
        engine.answerGround(.type(.mulch))
        engine.confirmFeatures()
        await pause(0.3)
        if engine.state.phase == .gapRequest {
            if engine.state.gap?.reason == .overhead {
                await answerOverheadGap()
            } else if replay.heldBack == nil {
                await pause(hold)
                log("skipping the gap: no held-back frames to show it")
                await engine.waitForGate(.gapRequest)
                engine.skipGap()
            } else {
                // The held-back frames were chosen on geometry alone; on a LiDAR replay depth can
                // find part of the gap hidden in them, and then they don't settle it.
                await playGapFrames(replay)
            }
        }
        guard await waitUntil(timeout: 150, { [.uploading, .processing].contains(self.engine.state.phase) }) else {
            return fail("upload did not start")
        }
        if engine.state.phase == .processing { return await finishPhotoProcessing() }
        guard await driveToResult(replay) else { return }
        writeSceneForTest()
        await pause(hold)
        await engine.waitForGate(.result)
        engine.showAR()
        guard await waitFor(.resultAR, timeout: 5) else { return fail("AR result did not open") }
        await pause(hold)
        await engine.waitForGate(.resultAR)
        engine.closeAR()
        _ = await waitFor(.result, timeout: 5)
        log("done")
    }

    // MARK: Steps

    /// What the close-up came to, as far as the autopilot acts on it.
    private enum CloseUpOutcome {
        case choose(MeterNumberCandidate)
        /// The photo has to be retaken because no number could be read from it.
        case retake(CloseUpProblem)
        /// The flow left the close-up by itself.
        case left
    }

    private var closeUpOutcome: CloseUpOutcome? {
        let state = engine.state
        guard state.phase == .meterCloseUp else { return .left }
        if case .choose(let candidates) = state.meterNumber, let first = candidates.first { return .choose(first) }
        if case .aiming(_, let problem?) = state.closeUp, [.noNumber, .numberTooSmall, .photoNotSaved].contains(problem) { return .retake(problem) }
        return nil
    }

    /// Picks the first meter-number candidate after the hold, as a homeowner confirming it would.
    /// When the number can't be read (the synthetic fixture has none) or the close-up never
    /// fires, skips the close-up instead of retaking forever.
    private func takeCloseUp() async {
        // 4 s for the shutter on a 3x replay, plus time for the reader.
        _ = await waitUntil(timeout: 8) { self.closeUpOutcome != nil }
        switch closeUpOutcome {
        case .choose(let first):
            await pause(hold)
            engine.chooseMeterNumber(first)
            log("chose meter-number candidate \(first.id)")
        case .retake(let problem):
            await pause(hold)
            log("close-up needs a retake (\(String(describing: problem))); skipping it as a homeowner would")
            await engine.waitForGate(.meterCloseUp)
            engine.skipCloseUp()
        case .left:
            return
        case nil:
            log("close-up did not fire on this replay; skipping it as a homeowner would")
            await engine.waitForGate(.meterCloseUp)
            engine.skipCloseUp()
        }
    }

    /// A gas meter (one tap) and a window (two diagonal corners) on wall seen at least as high as
    /// the highest tap, 1.85 m. The synthetic fixture paints them at s = -1.2 m and s = 2.0...3.0
    /// m; on other replays the nearest such wall stands in for them.
    private func markFeatures(_ replay: ReplayPlayer) async {
        guard let map = engine.coverage else { return }
        let covered = map.wallSeenSpans().filter { $0.out >= 1.85 }.map(\.span)
        guard !covered.isEmpty else {
            log("no covered wall to mark features on")
            return
        }
        let gasS = nearestCovered(-1.2, in: covered)
        engine.beginMarking(.gasMeter)
        await tap(map.wall.world(s: gasS, height: 0.55), replay: replay) { point in
            self.engine.markFeaturePoint(at: point, viewSize: self.viewSize)
        }
        await pause(0.8)

        let windowS = nearestCovered(2.5, in: covered)
        engine.beginMarking(.window)
        // Near the corners of the fixture's window (0.9 to 2.0 m high), kept inside the band a
        // walking camera sees so a frame shows each tap.
        for corner in [map.wall.world(s: windowS - 0.45, height: 1.85), map.wall.world(s: windowS + 0.45, height: 0.95)] {
            let tapped = await tap(corner, replay: replay) { point in
                self.engine.markFeaturePoint(at: point, viewSize: self.viewSize)
            }
            if !tapped { log("no replay frame shows a window corner") }
            await pause(0.4)
        }
        if engine.state.marking != nil {
            log("window marking incomplete (\(String(describing: self.engine.state.marking?.refusal))); cancelling it")
            engine.cancelMarking()
        }
        if let window = engine.state.features.first(where: { $0.kind == .window }) {
            engine.setWindowOpens(window.id, opens: true)
        }
        log("marked \(engine.state.features.count) features")
    }

    /// Ends at the covered extremes (the held-back window's plan, or the walk's coverage).
    private func markEnds(_ replay: ReplayPlayer) async {
        guard let map = engine.coverage else { return }
        let intervals = map.wallSeenSpans().map(\.span) + map.coveredIntervals(.ground)
        guard let low = intervals.map(\.lowerBound).min(), let high = intervals.map(\.upperBound).max() else {
            log("nothing covered; marking ends 1 m either side of the meter")
            // These stand in for the homeowner's marks, as the taps below do.
            engine.setEnd(.left, at: -1, kind: .limit, source: .homeowner)
            engine.setEnd(.right, at: 1, kind: .limit, source: .homeowner)
            return
        }
        for (side, s) in [(WallSide.left, min(low, -0.2)), (.right, max(high, 0.2))] {
            let point = map.wall.world(s: s, height: 0.5)
            let tapped = await tap(point, replay: replay) { viewPoint in
                self.engine.markWallEnd(at: viewPoint, viewSize: self.viewSize)
            }
            let marked = side == .left ? engine.coverage?.leftEnd : engine.coverage?.rightEnd
            if !tapped || marked == nil {
                log("no replay frame shows the \(side.rawValue) end; setting it directly at s=\(s)")
                engine.setEnd(side, at: s, kind: .limit, source: .homeowner)
            }
            if let asked = engine.state.endQuestion {
                // Every end counts as a corner here: a replay has no fence or gate to report.
                await pause(hold)
                engine.answerWallEnd(turnsCorner: true)
                log("answered the \(asked.rawValue) end: turns a corner")
                if case .markNextWall = engine.state.guidance {
                    // A replay has no live wall round the corner to mark, so it doesn't follow
                    // the corner, as a homeowner who can't get round it wouldn't.
                    await pause(hold)
                    engine.cannotAccessArea()
                    log("can't get round the \(asked.rawValue) corner on a replay; the end stays unexplored")
                }
            }
            await pause(0.6)
        }
    }

    // MARK: Can't get there (-autopilotCantGetThere)

    /// The walk's first request is the ground in front of the meter. This says "Can't get there"
    /// to it as soon as it shows, before the replay has shown that ground from two places: on
    /// device run 1 nothing had seen it when the ends were set, and the walk has to move on.
    private func skipGroundByMeterBeforeItIsSeen(_ replay: ReplayPlayer) async {
        _ = await waitUntil(timeout: 20) { self.isAskingForGroundByMeter || !replay.isPlaying }
        guard isAskingForGroundByMeter, let map = engine.coverage else {
            log("the walk did not ask for the ground by the meter while the replay played")
            return
        }
        let window = map.indices(overlapping: -GuidancePlanner.aimHalfWidth...GuidancePlanner.aimHalfWidth)
        let covered = window.filter { map.level(.ground, $0) == .covered }.count
        engine.cannotAccessArea()
        log("can't get there on the ground by the meter, \(covered) of its \(window.count) cells covered; now \(ScanEngine.name(self.engine.state.guidance))")
    }

    private var isAskingForGroundByMeter: Bool {
        if case .aimAtGround(let s) = engine.state.guidance { return abs(s) < 0.01 }
        return false
    }

    /// Ends each side with "Can't get there" where the walk went farthest that way, as a
    /// homeowner who reached the end of what they can walk would, instead of marking the ends.
    /// The phone stands there again (that frame plays once more), and any request about the wall
    /// or ground in front of it gets "Can't get there" first, until the walk asks to walk on that
    /// way: that is when the tap ends the wall. The engine logs where each end went.
    private func endWalkByCantGetThere(_ replay: ReplayPlayer) async {
        guard let wall = engine.coverage?.wall else { return }
        let walkEnd = ScanEngine.tiltUpFrames(in: replay, map: engine.coverage ?? CoverageMap(wall: wall)).lowerBound
        let played = (0..<walkEnd).filter { !(replay.heldBack?.frames.contains($0) ?? false) }
        for side in [WallSide.left, .right] {
            let along = { (index: Int) in side.walk.sign * wall.wallPoint(replay.camera(at: index).position).s }
            guard let index = played.max(by: { along($0) < along($1) }) else { continue }
            replay.play(range: index..<(index + 1), speed: 3)
            _ = await waitUntil(timeout: 10) { !replay.isPlaying }
            await pause(0.3)
            for _ in 0..<8 where !isWalking(side) && asksAboutTheWallHere {
                engine.cannotAccessArea()
                await pause(0.3)
            }
            guard isWalking(side) else {
                log("the walk never asked to walk \(side.rawValue) at s=\(side.walk.sign * along(index)); leaving that end unmarked")
                continue
            }
            await pause(hold)
            engine.cannotAccessArea()
            if engine.state.endScanQuestion {
                // Soon after the other side's: the walk asks whether to end the scan (#82). This
                // homeowner walked both sides, so keeps walking and ends this side as before.
                log("asked to end the scan on the \(side.rawValue); keeping walking")
                await pause(hold)
                engine.answerEndScan(false)
                engine.cannotAccessArea()
            }
            let end = side == .left ? engine.coverage?.leftEnd : engine.coverage?.rightEnd
            log("can't get there on the \(side.rawValue) with the phone at s=\(side.walk.sign * along(index)): end at s=\(end ?? .nan)")
        }
    }

    private func isWalking(_ side: WallSide) -> Bool {
        switch engine.state.guidance {
        case .walk(let walking, _), .markEnd(let walking): walking == side
        default: false
        }
    }

    private var asksAboutTheWallHere: Bool {
        switch engine.state.guidance {
        case .aimAtGround, .aimAtWall, .seeBehind: true
        default: false
        }
    }

    /// With `-autopilotGate`, leaves the scene.json of the scan the result answers in the gate
    /// folder, for the UI test to check its ends and what it reports as seen, and the scene.json
    /// the last upload sent as `uploaded-scene.json`.
    private func writeSceneForTest() {
        guard let gate = engine.options.autopilotGate else { return }
        do {
            try engine.sceneJSON().write(to: gate.appending(path: "scene.json"))
            try engine.spotConfirm.lastScene?.write(to: gate.appending(path: "uploaded-scene.json"))
            // The last upload's packet marks and guidance log, from the inputs its packet is
            // written from (`ScanEngine.packetMarksAndGuidance`), for the UI test to check ends'
            // provenance on the real path.
            #if DEBUG
            if let packet = engine.packetMarksAndGuidance() {
                try packet.write(to: gate.appending(path: "packet-marks-guidance.json"))
            }
            #endif
            log("wrote scene.json to the gate folder")
        } catch {
            log("could not write scene.json to the gate folder: \(error)")
        }
    }

    /// The tilt-up step: plays the recording's closing tilt-up frames and answers "Open sky or
    /// nothing overhead", which is what the synthetic fixture shows above its wall. A recording
    /// without tilt-up frames can't answer honestly, so it gets "Can't get there" instead.
    private func tiltUp() async {
        var played = false
        let asked = await waitUntil(timeout: 5, { self.isTiltingUp })
        if !asked {
            // The step waits for the phone to be back near the meter (#64), and the ends were
            // tapped from frames far from it. The recording's closing tilt-up frames stand beside
            // the meter: play them, as a homeowner walking back would, and the step comes up.
            played = engine.playReplayTiltUp()
            guard played, await waitUntil(timeout: 20, { self.isTiltingUp || self.engine.state.overheadQuestion }) else {
                log("the walk did not ask to tilt up")
                return
            }
        }
        await pause(hold)
        guard played || engine.playReplayTiltUp() else {
            log("no tilt-up frames on this replay; skipping the step as a homeowner would")
            engine.cannotAccessArea()
            return
        }
        guard await waitUntil(timeout: 20, { self.engine.state.overheadQuestion }) else {
            log("no tilt-up frame was kept; skipping the step")
            if isTiltingUp { engine.cannotAccessArea() }
            return
        }
        await pause(hold)
        await answerOpenSky()
        log("answered the overhead question: open sky; \(overheadSummary)")
        // The rest of the tilt-up run (the synthetic replay's upper walk) plays out, so its
        // views are kept before the walk ends.
        guard let replay = engine.replay else { return }
        _ = await waitUntil(timeout: 30) { !replay.isPlaying }
    }

    /// An overhead gap request: the engine plays the recording's tilt-up frames for it, and the
    /// question comes up only when one of them settles the request. The synthetic fixture shows
    /// open sky above its wall, so the answer is "Open sky or nothing overhead". When no tilt-up
    /// frame covers the requested span, "I can't get there", as a homeowner would.
    private func answerOverheadGap() async {
        guard await waitUntil(timeout: 20, { self.engine.state.overheadQuestion || self.engine.state.phase != .gapRequest }),
              engine.state.overheadQuestion else {
            log("no tilt-up frame covers the overhead request; skipping it")
            await engine.waitForGate(.gapRequest)
            engine.skipGap()
            return
        }
        await pause(hold)
        await answerOpenSky()
        log("answered the overhead request: open sky; \(overheadSummary)")
    }

    /// A photo-processing scan has no Legacy steps after the send: wait for its answer or its end,
    /// then hold the screen for the UI test.
    private func finishPhotoProcessing() async {
        guard await waitUntil(timeout: 240, { self.engine.state.photoProcessing?.isFinal == true }) else {
            return fail("photo processing never ended")
        }
        await pause(hold)
        await engine.waitForGate(.processing)
        log("done")
    }

    /// After an upload the engine raises the answer's capturable requests one at a time, uploading
    /// after each, before it shows the result, and checks the answer's spot with the homeowner.
    /// Drives each request and check, retries a failed upload once, and returns true once the
    /// result is up. Bounded by the engine's own bound on requests, and by one refused check.
    private func driveToResult(_ replay: ReplayPlayer) async -> Bool {
        var retried = false
        for _ in 0..<(ScanEngine.maxAutomaticGaps + 4) {
            let settled = await waitUntil(timeout: 240) {
                [.result, .gapRequest, .spotConfirm].contains(self.engine.state.phase) || self.uploadFailed
            }
            guard settled else {
                fail("no result")
                return false
            }
            switch engine.state.phase {
            case .result:
                return true
            case .gapRequest:
                await driveServerRequest(replay)
            case .spotConfirm:
                await answerSpotCheck()
            default:
                guard !retried else {
                    fail("no result after retry")
                    return false
                }
                retried = true
                log("upload failed; retrying once")
                engine.retryUpload()
            }
        }
        fail("still no result after \(ScanEngine.maxAutomaticGaps) requests")
        return false
    }

    /// "It's clear", or the first time "Something's there" with `-autopilotSomethingThere` and "I
    /// can't check this area" with `-autopilotCannotCheck`. After either, the stretch the answer
    /// withdrew goes to the gate folder as `spot-refusal.json` (the stretch's s in feet, as
    /// scene.json's spans), for the UI test to check the scene against.
    private func answerSpotCheck() async {
        guard let check = engine.state.spotCheck, check.answer == nil else { return }
        await pause(hold)
        await engine.waitForGate(.spotConfirm)
        let options = engine.options
        let first: SpotCheckAnswer = options.autopilotCannotCheck ? .cannotCheck : options.autopilotSomethingThere ? .somethingThere : .clear
        let answer: SpotCheckAnswer = withheldClear ? .clear : first
        engine.answerSpotCheck(answer)
        let said = switch answer {
        case .clear: "it's clear"
        case .somethingThere: "something's there"
        case .cannotCheck: "I can't check this area"
        }
        log("spot check \(check.id) over \(format(check.area)): \(said)")
        if answer != .clear {
            withheldClear = true
            writeRefusalForTest()
        }
        _ = await waitUntil(timeout: 20) { self.engine.state.phase != .spotConfirm }
    }

    /// The whole cells the refusal withdrew, in feet: what the scene must no longer claim.
    private func writeRefusalForTest() {
        guard let gate = engine.options.autopilotGate, let map = engine.coverage,
              let low = map.withdrawnCells.min(), let high = map.withdrawnCells.max() else { return }
        let feet = { (meters: Float) in Double(meters) * SceneUnits.feetPerMeter }
        let span = [feet(map.cellRange(low).lowerBound), feet(map.cellRange(high).upperBound)]
        do {
            try JSONSerialization.data(withJSONObject: ["withdrawn_span_ft": span]).write(to: gate.appending(path: "spot-refusal.json"))
        } catch {
            log("could not write spot-refusal.json to the gate folder: \(error)")
        }
    }

    private var uploadFailed: Bool {
        if case .failed = engine.state.upload { return engine.state.phase == .uploading }
        return false
    }

    /// A request the engine raised from the server's answer. The engine plays the replay's frames
    /// for it; a replay shows only what it recorded, so when they don't settle the request the
    /// autopilot says "I can't get there", as a homeowner would. An overhead request is answered
    /// like the one before the upload.
    private func driveServerRequest(_ replay: ReplayPlayer) async {
        guard let request = engine.state.gap else { return }
        log("server request \(request.id) over \(format(request.span))")
        #if DEBUG
        if request.pastEndSide != nil, let gate = engine.options.autopilotGate, let packet = engine.packetMarksAndGuidance() {
            // The packet of the upload that raised the request, with the end it then cleared, for
            // the UI test to compare the end that comes back with.
            try? packet.write(to: gate.appending(path: "packet-first.json"))
        }
        #endif
        if request.reason == .overhead {
            await answerOverheadGap()
        } else if request.pastEndSide != nil, engine.options.autopilotMarkPastEnd {
            await markPastEnd(request, replay: replay)
        } else {
            await playGapFrames(replay)
        }
        // A settled request stays on screen for a moment before the next upload.
        _ = await waitUntil(timeout: 10) { self.engine.state.phase != .gapRequest || self.engine.state.gap?.id != request.id }
    }

    /// `-autopilotMarkPastEnd`: marks a past_end request's end again, 60% of the way from the meter
    /// to the end the request cleared, and answers "Something blocks it", so the wall comes out
    /// shorter and its end a limit; with `-autopilotPastEndCorner`, "It turns a corner", which
    /// leaves that end unexplored and the request cannot_reach. The mark is a tap where the replay shows that place on the
    /// wall, not the circle in the middle of the view: it goes through the same `EndAim` check
    /// from that point (`ScanEngine.markWallEnd`), but no camera was aimed. Where it marked goes
    /// to the gate folder as `past-end-mark.json` (side, s in meters), for the UI test.
    private func markPastEnd(_ request: GapRequest, replay: ReplayPlayer) async {
        guard let side = request.pastEndSide, let wall = engine.coverage?.wall else { return }
        let cleared = side == .left ? request.span.upperBound : request.span.lowerBound
        let s = side == .left ? min(cleared * 0.6, -0.5) : max(cleared * 0.6, 0.5)
        // The engine plays the request's frames first; showing another frame meanwhile would
        // race it. A replay that settled the request by itself leaves nothing to mark.
        _ = await waitUntil(timeout: 60) { !replay.isPlaying || self.engine.state.gap?.id != request.id }
        await pause(hold)
        guard engine.state.phase == .gapRequest, let gap = engine.state.gap, gap.id == request.id, !gap.isSatisfied else {
            fail("past_end request \(request.id) settled before its end could be marked again")
            return
        }
        let tapped = await tap(wall.world(s: s, height: 0.5), replay: replay) { viewPoint in
            self.engine.markWallEnd(at: viewPoint, viewSize: self.viewSize)
        }
        guard tapped, engine.state.endQuestion == side else {
            let why = engine.state.endMarkRefusal.map { String(describing: $0) } ?? (tapped ? "no question" : "no frame shows it")
            fail("could not mark the \(side.rawValue) end again at s=\(s): \(why)")
            return
        }
        let marked = side == .left ? engine.coverage?.leftEnd : engine.coverage?.rightEnd
        log("marked the \(side.rawValue) end again at s=\(marked ?? .nan) (cleared at s=\(cleared))")
        if let gate = engine.options.autopilotGate, let marked {
            let record: [String: Any] = ["side": side.rawValue, "s": Double(marked), "cleared_s": Double(cleared)]
            try? JSONSerialization.data(withJSONObject: record).write(to: gate.appending(path: "past-end-mark.json"))
        }
        await pause(hold)
        let corner = engine.options.autopilotPastEndCorner
        engine.answerWallEnd(turnsCorner: corner)
        log("answered the \(side.rawValue) end: \(corner ? "it turns a corner" : "something blocks it")")
    }

    /// Waits while the engine plays the replay's frames for the current request. A replay shows
    /// only what it recorded, so when they don't settle the request the autopilot says "I can't
    /// get there", as a homeowner would.
    private func playGapFrames(_ replay: ReplayPlayer) async {
        guard let request = engine.state.gap else { return }
        _ = await waitUntil(timeout: 60) { !replay.isPlaying || self.engine.state.gap?.id != request.id }
        // Time for the last frame's keyframe to be stored and counted.
        await pause(hold)
        if engine.state.phase == .gapRequest, let gap = engine.state.gap, gap.id == request.id, !gap.isSatisfied {
            log("the replay does not settle request \(request.id) (\(Int(gap.progress * 100))% of it seen); skipping it")
            await engine.waitForGate(.gapRequest)
            engine.skipGap()
        }
    }

    /// "Open sky or nothing overhead", then a wait for the view to be stored: it counts as an
    /// overhead view only once its photo is on disk.
    private func answerOpenSky() async {
        let before = engine.coverage?.overheadCameras.count ?? 0
        engine.answerOverhead(clear: true)
        _ = await waitUntil(timeout: 5) { (self.engine.coverage?.overheadCameras.count ?? 0) > before }
    }

    /// The overhead views kept and the stretches they show clear, as the export will send them.
    private var overheadSummary: String {
        guard let map = engine.coverage else { return "no wall" }
        let spans = map.overheadSpans().map { "\(format($0.span)) to \(String(format: "%.2f", $0.out)) m" }
        return "\(map.overheadCameras.count) overhead view(s) kept, clear over \(spans.isEmpty ? "nothing" : spans.joined(separator: ", "))"
    }

    private var isTiltingUp: Bool {
        if case .tiltUp = engine.state.guidance { return true }
        return false
    }

    // MARK: Helpers

    /// Shows the replay frame that best shows `point`, then taps where it appears on screen.
    @discardableResult
    private func tap(_ point: SIMD3<Float>, replay: ReplayPlayer, action: (CGPoint) -> Void) async -> Bool {
        guard let (index, viewPoint) = frameShowing(point, replay: replay) else { return false }
        replay.show(index: index)
        await replay.waitUntilShown(index)
        await pause(0.2)
        action(viewPoint)
        return true
    }

    private func frameShowing(_ point: SIMD3<Float>, replay: ReplayPlayer) -> (Int, CGPoint)? {
        let inset: CGFloat = 40
        var best: (Int, CGPoint, CGFloat)?
        for index in replay.frames.indices {
            let camera = replay.camera(at: index)
            let projection = CameraProjection(cameraToWorld: camera.cameraToWorld, intrinsics: camera.intrinsics, imageSize: camera.imageSize)
            guard let view = projection.viewPoint(for: point, in: viewSize),
                  view.x > inset, view.y > inset, view.x < viewSize.width - inset, view.y < viewSize.height - inset else { continue }
            let offCenter = hypot(view.x - viewSize.width / 2, view.y - viewSize.height / 2)
            if offCenter < best?.2 ?? .infinity { best = (index, view, offCenter) }
        }
        return best.map { ($0.0, $0.1) }
    }

    private func nearestCovered(_ s: Float, in intervals: [ClosedRange<Float>]) -> Float {
        if intervals.contains(where: { $0.contains(s) }) { return s }
        let candidates = intervals.map { interval -> Float in
            let inset = min(0.5, (interval.upperBound - interval.lowerBound) / 2)
            return min(max(s, interval.lowerBound + inset), interval.upperBound - inset)
        }
        return candidates.min { abs($0 - s) < abs($1 - s) } ?? s
    }

    private func waitFor(_ phase: ScanPhase, timeout: Double) async -> Bool {
        await waitUntil(timeout: timeout) { self.engine.state.phase == phase }
    }

    private func waitUntil(timeout: Double, _ condition: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now + .seconds(timeout)
        while ContinuousClock.now < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(50))
        }
        return condition()
    }

    private func pause(_ seconds: Double) async {
        try? await Task.sleep(for: .seconds(seconds))
    }

    private func log(_ message: String) {
        RuntimeLog.autopilot.info("AUTOPILOT \(message, privacy: .public)")
    }

    private func fail(_ message: String) {
        RuntimeLog.autopilot.error("AUTOPILOT stopped: \(message, privacy: .public) (phase \(self.engine.state.phase.rawValue, privacy: .public))")
    }

    private func format(_ range: ClosedRange<Float>) -> String {
        String(format: "%.2f...%.2f m", range.lowerBound, range.upperBound)
    }
}
