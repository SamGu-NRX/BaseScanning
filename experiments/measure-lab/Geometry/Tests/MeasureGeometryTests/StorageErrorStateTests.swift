import Testing
@testable import MeasureGeometry

struct StorageErrorStateTests {
    @Test func `successful current manifest retry clears its failure`() {
        var errors = StorageErrorState<String>()
        errors.currentManifestFailed("Current write failed")
        errors.closedSessionSaved("S1")
        #expect(errors.message == "Current write failed")
        errors.currentManifestSaved()
        #expect(errors.message == nil)
    }

    @Test func `current manifest retry preserves unrelated keyframe loss`() {
        var errors = StorageErrorState<String>()
        errors.currentManifestFailed("Current write failed")
        errors.report("Keyframe lost")
        errors.currentManifestSaved()
        #expect(errors.message == "Keyframe lost")
        errors.currentManifestFailed("Another write failed")
        errors.currentManifestSaved()
        #expect(errors.message == "Keyframe lost")
    }

    @Test func `current manifest success preserves a closed manifest failure`() {
        var errors = StorageErrorState<String>()
        errors.report("Closed write failed", closedSession: "S1")
        errors.currentManifestSaved()
        #expect(errors.message == "Closed write failed")
    }

    @Test func `successful retry clears only its closed manifest error`() {
        var errors = StorageErrorState<String>()
        errors.report("S1 write failed", closedSession: "S1")
        errors.closedSessionSaved("S2")
        #expect(errors.message == "S1 write failed")
        errors.closedSessionSaved("S1")
        #expect(errors.message == nil)
    }

    @Test func `closed retry preserves a newer current session error`() {
        var errors = StorageErrorState<String>()
        errors.report("S1 write failed", closedSession: "S1")
        errors.report("Current session write failed")
        errors.closedSessionSaved("S1")
        #expect(errors.message == "Current session write failed")
    }

    @Test func `closed failure cannot hide an existing unrelated error`() {
        var errors = StorageErrorState<String>()
        errors.report("Keyframe lost")
        errors.report("S1 write failed", closedSession: "S1")
        errors.closedSessionSaved("S1")
        #expect(errors.message == "Keyframe lost")
    }

    @Test func `failed closed write followed by successful flush clears its error`() {
        struct DiskFull: Error {}
        var manifests = ClosedManifests<String, Int>()
        var errors = StorageErrorState<String>()
        manifests.close("S1", 1, awaitingWrites: false, saved: false)
        let failed = manifests.flush { _ in throw DiskFull() }
        #expect(failed.saved.isEmpty)
        for failure in failed.failures {
            errors.report("Write failed", closedSession: failure.session)
        }
        #expect(errors.message == "Write failed")
        let retried = manifests.flush { _ in }
        #expect(retried.saved == ["S1"])
        #expect(retried.failures.isEmpty)
        for session in retried.saved { errors.closedSessionSaved(session) }
        #expect(errors.message == nil)
        #expect(manifests.sessions.isEmpty)
    }
}
