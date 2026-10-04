import Foundation
import HouseScanKit
import Testing

@Suite struct CaptureFixtureLaunchTests {
    @Test(arguments: [true, false])
    func aReleaseBuildNeverUsesTheFixture(onReplay: Bool) {
        for synthetic in [true, false] {
            let launch = CaptureFixtureLaunch.resolve(debugBuild: false, onReplay: onReplay, answer: .candidate, syntheticCapture: synthetic)
            #expect(launch == .off("a release build has no capture fixture"))
        }
    }

    @Test func theLiveCameraNeverUsesTheFixture() {
        for synthetic in [true, false] {
            let launch = CaptureFixtureLaunch.resolve(debugBuild: true, onReplay: false, answer: .candidate, syntheticCapture: synthetic)
            #expect(launch == .off("the capture fixture runs only on a replay, never the live camera"))
        }
    }

    @Test func theSyntheticCaptureNeedsTheFixtureAndNothingIsOnByDefault() {
        #expect(CaptureFixtureLaunch.resolve(debugBuild: true, onReplay: true, answer: nil, syntheticCapture: true)
            == .off("the synthetic capture needs the capture fixture"))
        #expect(CaptureFixtureLaunch.resolve(debugBuild: true, onReplay: true, answer: nil, syntheticCapture: false)
            == .off("sending captures is off in this build"))
    }

    @Test func aDebugReplayUsesWhatItAskedFor() {
        #expect(CaptureFixtureLaunch.resolve(debugBuild: true, onReplay: true, answer: .hold, syntheticCapture: false) == .on(.hold, syntheticCapture: false))
        #expect(CaptureFixtureLaunch.resolve(debugBuild: true, onReplay: true, answer: .candidate, syntheticCapture: true)
            == .on(.candidate, syntheticCapture: true))
    }
}

@Suite(.serialized) @MainActor struct SyntheticCaptureTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "synthetic-capture-\(UUID().uuidString)")

    @Test func itIsTheRecordingOf192sSyntheticFixture() {
        let fixture = NativeCaptureFixture(folder: root)
        let capture = SyntheticCapture.standard
        #expect(capture.start == fixture.start && capture.end == fixture.end)
        let ours = capture.rows, theirs = fixture.rows
        #expect(ours.trajectory == theirs.trajectory)
        #expect(ours.intrinsics == theirs.intrinsics)
        #expect(ours.accelerometer == theirs.accelerometer)
        #expect(ours.gyroscope == theirs.gyroscope)
        #expect(capture.recording.start()?.uptime == capture.start)
        #expect(capture.recording.start()?.date == SyntheticCapture.startedAt)
    }

    @Test func twoRunsMakeTheSameRowsAndPhotos() throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let first = try SyntheticCapture().photos(in: root.appending(path: "a"))
        let second = try SyntheticCapture().photos(in: root.appending(path: "b"))
        #expect(first.count == 6)
        for (a, b) in zip(first, second) {
            #expect(a.t == b.t && a.purpose == b.purpose && a.cameraToWorld == b.cameraToWorld)
            #expect(try Data(contentsOf: a.jpeg) == Data(contentsOf: b.jpeg))
        }
        #expect(Set(try first.map { try Data(contentsOf: $0.jpeg) }).count == 6, "two photos share an image")
        #expect(SyntheticCapture().rows.accelerometer == SyntheticCapture.standard.rows.accelerometer)
    }

    func controller(_ http: FixtureCaptureHTTP) -> PhotoProcessingController {
        var policy = CaptureUploader.Policy()
        policy.eventsWait = 0
        policy.firstDelay = 0.01
        policy.maxDelay = 0.02
        let environment = CaptureSessionCoordinator.Environment(
            endpoint: FixtureCaptureHTTP.base, sends: true, http: http, capturesFolder: root.appending(path: "Captures"),
            sessionInfo: { packetID, video in SyntheticCapture.sessionInfo(packetID: packetID, video: video, appVersion: "test") },
            device: .init(model: "synthetic", systemVersion: "synthetic", appVersion: "test"), tier: .arkit, policy: policy)
        return PhotoProcessingController(setup: .ready(environment, standIn: true))
    }

    func until(_ condition: () -> Bool) async throws -> Bool {
        let deadline = ContinuousClock.now + .seconds(30)
        while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
        return condition()
    }

    /// The capture through the real coordinator and uploader: a packet the phone's checks pass,
    /// labelled synthetic, and the fixture's answer as the scan's typed stage.
    @Test func itsPacketPassesThePhonesChecksSaysSyntheticAndGetsAnAnswer() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        let capture = SyntheticCapture.standard
        controller.beginScan(ScanContext(scanID: "scan", profile: controller.profile, recordingSessionID: "rec"), recording: capture.recording)
        controller.answerConsent(true)
        for photo in try capture.photos(in: root.appending(path: "photos")) { controller.kept(photo) }
        controller.captureEnded(acceptedCloseUpAt: capture.acceptedCloseUpAt)
        try #require(try await until { controller.status?.isFinal == true }, "\(String(describing: controller.status))")
        #expect(controller.status?.stage == .answered(.candidate(.init(
            message: FixtureCaptureHTTP.Answer.candidate.message ?? "", previewMade: false, ar: .withheld(.analysisUnverified)))))
        let folder = try #require(controller.captureFolder)
        let packet = try JSONDecoder().decode(Packet04.Packet.self, from: Data(contentsOf: folder.appending(path: "packet.json")))
        #expect(packet.source.kind == .synthetic)
        #expect(Packet04Check.problems(packet, folder: folder).isEmpty)
        #expect(packet.stills?.map(\.purpose) == ["meter_close"])
        #expect(packet.scaleReference.meterCloseUp.still == "meter_close")
        controller.endScan(recording: capture.recording)
    }

    /// The same capture without its motion, as a replay is: the phone's checks still refuse it.
    @Test func withoutMotionThePacketIsStillRefusedOnThePhone() async throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let http = FixtureCaptureHTTP(answer: .candidate)
        let controller = controller(http)
        let capture = SyntheticCapture.standard
        let still = RecordingSource(start: { (capture.start, SyntheticCapture.startedAt) }, rows: {
            let rows = capture.rows
            return RecorderRows(trajectory: rows.trajectory, intrinsics: rows.intrinsics, accelerometer: [], gyroscope: [])
        })
        controller.beginScan(ScanContext(scanID: "scan", profile: controller.profile, recordingSessionID: "rec"), recording: still)
        controller.answerConsent(true)
        for photo in try capture.photos(in: root.appending(path: "photos")) { controller.kept(photo) }
        controller.captureEnded(acceptedCloseUpAt: capture.acceptedCloseUpAt)
        try #require(try await until { controller.status?.isFinal == true })
        #expect(controller.status?.stage == .ended(.notPrepared))
        #expect(!http.routes.contains("POST captures/finalize"))
        controller.endScan(recording: capture.recording)
    }
}
