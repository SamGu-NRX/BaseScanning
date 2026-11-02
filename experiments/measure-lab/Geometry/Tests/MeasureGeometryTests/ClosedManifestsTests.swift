import Testing
@testable import MeasureGeometry

/// A manifest stands in as the list of keyframe ids it names; the disk is a dictionary that a
/// test can make refuse writes.
struct ClosedManifestsTests {
    struct DiskFull: Error {}

    final class Disk {
        var files: [String: [String]] = [:]
        var failing = false

        func write(_ manifest: (session: String, keyframes: [String])) throws {
            if failing { throw DiskFull() }
            files[manifest.session] = manifest.keyframes
        }
    }

    typealias Manifests = ClosedManifests<String, (session: String, keyframes: [String])>

    var manifests = Manifests()
    let disk = Disk()

    @Test mutating func `a late keyframe is written and the session released once drained`() {
        manifests.close("S1", ("S1", ["k1"]), awaitingWrites: true, saved: true)
        manifests.update("S1") { $0.keyframes.append("k2") }
        manifests.markDrained("S1")

        let failures = manifests.flush(disk.write).failures

        #expect(failures.isEmpty)
        #expect(disk.files["S1"] == ["k1", "k2"])
        #expect(manifests.sessions.isEmpty)
    }

    @Test mutating func `a failed write keeps the drained manifest until a retry succeeds`() {
        manifests.close("S1", ("S1", ["k1"]), awaitingWrites: true, saved: true)
        manifests.update("S1") { $0.keyframes.append("k2") }
        manifests.markDrained("S1")
        disk.failing = true

        let failures = manifests.flush(disk.write).failures

        #expect(failures.map(\.session) == ["S1"])
        #expect(failures.first?.error is DiskFull)
        #expect(manifests.sessions == ["S1"])
        #expect(manifests.isUnsaved("S1"))
        #expect(manifests.value(for: "S1")?.keyframes == ["k1", "k2"])
        #expect(disk.files["S1"] == nil)

        disk.failing = false
        #expect(manifests.flush(disk.write).failures.isEmpty)
        #expect(disk.files["S1"] == ["k1", "k2"])
        #expect(manifests.sessions.isEmpty)
    }

    @Test mutating func `a failed write before the last keyframe keeps both keyframes`() {
        manifests.close("S1", ("S1", []), awaitingWrites: true, saved: true)
        manifests.update("S1") { $0.keyframes.append("k1") }
        disk.failing = true
        _ = manifests.flush(disk.write)

        disk.failing = false
        manifests.update("S1") { $0.keyframes.append("k2") }
        manifests.markDrained("S1")
        #expect(manifests.flush(disk.write).failures.isEmpty)

        #expect(disk.files["S1"] == ["k1", "k2"])
        #expect(manifests.sessions.isEmpty)
    }

    @Test mutating func `a saved session still awaiting writes stays after a flush`() {
        manifests.close("S1", ("S1", []), awaitingWrites: true, saved: true)
        manifests.update("S1") { $0.keyframes.append("k1") }

        #expect(manifests.flush(disk.write).failures.isEmpty)

        #expect(manifests.sessions == ["S1"])
        #expect(!manifests.isUnsaved("S1"))
    }

    @Test mutating func `a session whose closing save failed is kept and retried`() {
        manifests.close("S1", ("S1", ["k1"]), awaitingWrites: false, saved: false)
        #expect(manifests.sessions == ["S1"])

        #expect(manifests.flush(disk.write).failures.isEmpty)

        #expect(disk.files["S1"] == ["k1"])
        #expect(manifests.sessions.isEmpty)
    }

    @Test mutating func `a drained, saved session is not kept`() {
        manifests.close("S1", ("S1", ["k1"]), awaitingWrites: false, saved: true)
        #expect(manifests.sessions.isEmpty)
        #expect(!manifests.update("S1") { $0.keyframes.append("k2") })
    }
}
