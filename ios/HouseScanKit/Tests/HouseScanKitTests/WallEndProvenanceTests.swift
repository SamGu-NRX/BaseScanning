import Foundation
import HouseScanKit
import simd
import Testing

// B-12: a wall end the homeowner marked and one the app inferred from the walk keep the same
// place and kind; only the packet's `t` and `attrs.inferred` tell them apart. The wall is z = 0
// facing +z with the meter 1.5 m up, synthetic values chosen for the tests.

@Suite struct WallEndProvenanceTests {
    static let wall = SceneWall(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0)

    static func end(_ stamp: WallEndStamp, kind: PacketMark.EndKind = .unexplored) throws -> PacketMark {
        let frame = try #require(MeterFrame(wall: wall))
        return .wallEnd(id: "wall_end_right", side: .right, endKind: kind, s: 2.5, wall: wall, frame: frame, stamp: stamp)
    }

    static func json(_ mark: PacketMark) throws -> [String: Any] {
        try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(mark)) as? [String: Any])
    }

    // MARK: Stamps

    @Test func aHomeownerMarkKeepsItsTimeAndMeetsTheRequest() {
        let stamp = WallEndStamp.marked(at: 12.5)
        #expect(stamp.source == .homeowner && stamp.markedAt == 12.5 && !stamp.isInferred)
        #expect(stamp.markEndOutcome == .met)
    }

    @Test func anInferredEndHasNoTimeAndNeverMeetsAMarkRequest() {
        let stamp = WallEndStamp.inferred
        #expect(stamp.source == .inferred && stamp.markedAt == nil && stamp.isInferred)
        #expect(stamp.markEndOutcome == .superseded)
    }

    /// A homeowner mark made before the packet's clock started has no time but is still the
    /// homeowner's: absence of a time is never read as either source.
    @Test func aMarkWithoutAClockIsStillTheHomeowners() {
        let stamp = WallEndStamp.marked(at: nil)
        #expect(stamp.source == .homeowner && stamp.markedAt == nil)
        #expect(stamp.markEndOutcome == .met)
        #expect(stamp != .inferred)
    }

    // MARK: Past-end requests

    @Test(arguments: [
        // A mark during the request stands, met or not: it may be nearer than the cleared end.
        (true, true, PastEndSettlement.keepMarked),
        (true, false, PastEndSettlement.keepMarked),
        // Met by views with nobody marking: the end moves on, inferred.
        (false, true, PastEndSettlement.moveOn),
        // "I can't get there": the cleared end comes back.
        (false, false, PastEndSettlement.restore),
    ])
    func pastEndSettlement(marked: Bool, met: Bool, expected: PastEndSettlement) {
        #expect(PastEndSettlement.when(endMarkedDuringRequest: marked, met: met) == expected)
    }

    // MARK: Packet encoding

    @Test func aMarkedEndCarriesItsTimeAndNoFlag() throws {
        let object = try Self.json(try Self.end(.marked(at: 12.5)))
        #expect(object["t"] as? Double == 12.5)
        #expect(object["attrs"] == nil)
    }

    @Test func anInferredEndCarriesTheFlagAndNoTime() throws {
        let object = try Self.json(try Self.end(.inferred))
        #expect(object["t"] == nil)
        #expect((object["attrs"] as? [String: Any])?["inferred"] as? Bool == true)
    }

    /// Provenance changes nothing about where the end is or what it is.
    @Test(arguments: [PacketMark.EndKind.unexplored, .limit])
    func provenanceKeepsPlaceAndKind(kind: PacketMark.EndKind) throws {
        let marked = try Self.end(.marked(at: 3), kind: kind)
        let inferred = try Self.end(.inferred, kind: kind)
        #expect(marked.points == inferred.points && marked.points == [SIMD3(2.5, 0, 0)])
        #expect(marked.side == inferred.side && marked.endKind == kind && inferred.endKind == kind)
        #expect(marked.kind == .wallEnd && inferred.kind == .wallEnd)
    }

    @Test func bothRoundTripThroughJSON() throws {
        for mark in [try Self.end(.marked(at: 7)), try Self.end(.inferred), try Self.end(.marked(at: nil))] {
            let decoded = try JSONDecoder().decode(PacketMark.self, from: JSONEncoder().encode(mark))
            #expect(decoded == mark)
        }
    }

    /// An older packet's wall end, without attrs, decodes as not inferred, its time kept.
    @Test func anEndWithoutAttrsIsNotInferred() throws {
        let json = #"{"id": "e", "kind": "wall_end", "points": [[2.5, 0, 0]], "t": 4, "side": "right", "end_kind": "limit"}"#
        let mark = try JSONDecoder().decode(PacketMark.self, from: Data(json.utf8))
        #expect(!mark.inferred && mark.t == 4)
    }

    /// The flag sits beside an opening's `operable` without disturbing it.
    @Test func operableStaysAlone() throws {
        let frame = try #require(MeterFrame(wall: Self.wall))
        let window = PacketMark.opening(.window, id: "w", span: 0 ... 1, bottom: 1, top: 2, operable: true, wall: Self.wall, frame: frame)
        let attrs = try #require(try Self.json(window)["attrs"] as? [String: Any])
        #expect(attrs.keys.sorted() == ["operable"])
    }

    /// The written synthetic packet carries an inferred left end and a marked right end, and its
    /// manifest validates (`syntheticPacketIsComplete`). Here: the schema reads the flag as a
    /// boolean, so a manifest with any other value fails at that mark.
    @Test func theSchemaRejectsANonBooleanFlag() throws {
        let folder = URL(fileURLWithPath: NSTemporaryDirectory()).appending(path: "hs-provenance-\(UUID())")
        let jpegs = URL(fileURLWithPath: NSTemporaryDirectory()).appending(path: "hs-provenance-jpegs-\(UUID())")
        try FileManager.default.createDirectory(at: jpegs, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(at: folder)
            try? FileManager.default.removeItem(at: jpegs)
        }
        _ = try SyntheticPacket().write(to: folder, jpegs: jpegs)
        let data = try Data(contentsOf: folder.appendingPathComponent("manifest.json"))
        let validator = try PacketSchema.validator()
        #expect(try validator.validate(data) == [])
        var manifest = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        var marks = try #require(manifest["marks"] as? [[String: Any]])
        #expect((marks[1]["attrs"] as? [String: Any])?["inferred"] as? Bool == true && marks[1]["t"] == nil)
        marks[1]["attrs"] = ["inferred": "yes"]
        manifest["marks"] = marks
        let errors = try validator.validate(JSONSerialization.data(withJSONObject: manifest))
        #expect(errors.contains { $0.contains("marks") && $0.contains("inferred") }, "\(errors)")
    }

    /// The writer's own check: an inferred end with a time, or the flag on another kind of mark,
    /// is refused before anything is written.
    @Test func theWriterRefusesAnInferredEndWithATime() throws {
        // Times after the synthetic capture's start, so only the rule under test can refuse.
        let t = SyntheticPacket.start + 3
        var mark = try Self.end(.inferred)
        mark.t = t
        let folder = URL(fileURLWithPath: NSTemporaryDirectory()).appending(path: "hs-provenance-\(UUID())")
        defer { try? FileManager.default.removeItem(at: folder) }
        var writer = try PacketWriter(folder: folder, session: try SyntheticPacket().session)
        #expect(throws: PacketError.invalidMark(id: "wall_end_right", reason: "an inferred wall end has no mark time: nobody marked it")) {
            try writer.setMarks([mark])
        }
        var meter = PacketMark.meter(id: "m")
        meter.inferred = true
        #expect(throws: PacketError.invalidMark(id: "m", reason: "only a wall end can be inferred")) { try writer.setMarks([meter]) }
        #expect(throws: Never.self) { try writer.setMarks([try Self.end(.inferred), try Self.end(.marked(at: t)).renamed("wall_end_left")]) }
    }
}

private extension PacketMark {
    func renamed(_ id: String) -> PacketMark {
        var mark = self
        mark.id = id
        return mark
    }
}

// MARK: Shorter bounds

/// An end the homeowner marks nearer than the end a past-end request cleared bounds the export:
/// nothing seen past it is reported, on either band, and the other side is untouched.
@Suite struct NearerEndBoundsTests {
    @Test func aNearerEndClipsWhatIsReportedAndLeavesTheOtherSide() throws {
        var map = CoverageMap(wall: standardWall())
        for (i, c) in stride(from: Float(-3), through: 3, by: 0.25).enumerated() {
            map.observe(wallCamera(s: c), trackingNormal: true, time: Double(i))
            map.observe(groundCamera(s: c), trackingNormal: true, time: Double(i) + 0.5)
        }
        map.setEnd(.left, at: -2.5)
        map.setEnd(.right, at: 2.5)
        let before = try #require(map.wallSeenSpans().map(\.span.upperBound).max())
        #expect(before > 2 && before <= 2.5 + 1e-4)
        #expect((map.coveredIntervals(.ground).map(\.upperBound).max() ?? 0) > 2)
        // The wall really stops at 1.5 m.
        map.setEnd(.right, at: 1.5)
        #expect(map.rightEnd == 1.5 && map.leftEnd == -2.5)
        for span in map.wallSeenSpans() + map.groundDepthSpans() {
            #expect(span.span.upperBound <= 1.5 + 1e-4, "\(span.span) runs past the nearer end")
        }
        #expect(map.coveredIntervals(.wall).allSatisfy { $0.upperBound <= 1.5 + 1e-4 })
        #expect(map.coveredIntervals(.ground).allSatisfy { $0.upperBound <= 1.5 + 1e-4 })
        // The left side is untouched.
        #expect((map.wallSeenSpans().map(\.span.lowerBound).min() ?? 0) < -2)
    }

    /// A nearer end keeps the walk's minimum (`WallFrame.minWallLength`), measured from the other
    /// end, or from the meter while that side has none, and never moves the other end.
    @Test func aNearerEndKeepsTheMinimumWallLength() {
        var map = CoverageMap(wall: standardWall())
        map.setEnd(.left, at: -1)
        let least = WallFrame.minWallLength
        #expect(map.endWouldLeaveTooLittle(.right, at: -1 + least - 0.01))
        #expect(!map.endWouldLeaveTooLittle(.right, at: -1 + least + 0.01))
        // No right end: measured from the meter.
        #expect(map.endWouldLeaveTooLittle(.left, at: -(least - 0.01)))
        #expect(!map.endWouldLeaveTooLittle(.left, at: -(least + 0.01)))
        map.setEnd(.right, at: 0.2)
        #expect(map.endWouldLeaveTooLittle(.left, at: -0.1), "a 0.3 m wall")
        #expect(!map.endWouldLeaveTooLittle(.left, at: 0.2 - least - 0.01))
        #expect(map.leftEnd == -1 && map.rightEnd == 0.2, "asking moved an end")
    }
}
