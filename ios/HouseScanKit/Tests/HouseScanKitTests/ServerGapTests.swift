import Foundation
@testable import HouseScanKit
import Testing

/// Server missing-evidence items become capture requests (result.schema.json missing_evidence).
@Suite struct ServerGapTests {
    private func item(_ json: String) throws -> PlacementMissingEvidence {
        try JSONDecoder().decode(PlacementMissingEvidence.self, from: Data(json.utf8))
    }

    @Test func groundBandBecomesAGroundRequestInMeters() throws {
        // span_ft [3.0, 5.5] -> 0.9144...1.6764 m.
        let plan = try #require(GapPlanner().plan(for: item(#"{"kind":"band","band":"ground","span_ft":[3.0,5.5],"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(plan.band == .ground)
        #expect(abs(plan.span.lowerBound - 0.9144) < 1e-4)
        #expect(abs(plan.span.upperBound - 1.6764) < 1e-4)
        #expect(plan.reason == .server)
    }

    @Test func requestsWithAReachAskForItInMeters() throws {
        let planner = GapPlanner()
        // out_ft 6.0 = 1.8288 m, 8.5 = 2.5908 m, 7.0 = 2.1336 m.
        let ground = try #require(planner.plan(for: item(#"{"kind":"band","band":"ground","span_ft":[0,2.58],"out_ft":6.0,"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(ground.band == .ground)
        guard case .groundOut(let out) = ground.need else { Issue.record("\(ground.need)"); return }
        #expect(nearlyEqual(out, 1.8288))
        let facing = try #require(planner.plan(for: item(#"{"kind":"band","band":"facing","span_ft":[0,2.58],"out_ft":8.5,"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(facing.band == .ground)
        guard case .walkOut(let walk) = facing.need else { Issue.record("\(facing.need)"); return }
        #expect(nearlyEqual(walk, 2.5908))
        let overhead = try #require(planner.plan(for: item(#"{"kind":"band","band":"overhead","span_ft":[0,2.58],"out_ft":7.0,"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(overhead.band == .wall)
        guard case .overhead(let height?) = overhead.need else { Issue.record("\(overhead.need)"); return }
        #expect(nearlyEqual(height, 2.1336))
        #expect(planner.plan(for: try item(#"{"kind":"band","band":"wall","span_ft":[0,1],"message":"m"}"#), leftEnd: nil, rightEnd: nil)?.need == .cells)
    }

    /// Facing without out_ft means the view must reach whatever faces the wall and measure it,
    /// which walking can't show; overhead without one asks for any recorded tilt-up view.
    @Test func facingWithoutAReachIsNotACaptureRequest() throws {
        #expect(GapPlanner().plan(for: try item(#"{"kind":"band","band":"facing","span_ft":[0,1],"message":"m"}"#), leftEnd: nil, rightEnd: nil) == nil)
        #expect(GapPlanner().plan(for: try item(#"{"kind":"band","band":"overhead","span_ft":[0,1],"message":"m"}"#), leftEnd: nil, rightEnd: nil)?.need == .overhead(nil))
    }

    /// Ground past the deepest sampled row (17 ft) can't be captured: no request is built, so the
    /// app sends it to review. 17 ft itself can be.
    @Test func groundDeeperThanTheMapSamplesIsBeyondCapture() throws {
        let planner = GapPlanner()
        let deepest = try item(#"{"kind":"band","band":"ground","span_ft":[0,2.58],"out_ft":17.0,"message":"m"}"#)
        #expect(!planner.isBeyondCapture(deepest))
        #expect(planner.plan(for: deepest, leftEnd: nil, rightEnd: nil) != nil)
        let deeper = try item(#"{"kind":"band","band":"ground","span_ft":[0,2.58],"out_ft":17.000001,"message":"m"}"#)
        #expect(planner.isBeyondCapture(deeper))
        #expect(planner.plan(for: deeper, leftEnd: nil, rightEnd: nil) == nil)
        // Only ground has a sampling limit.
        #expect(!planner.isBeyondCapture(try item(#"{"kind":"band","band":"facing","span_ft":[0,1],"out_ft":30,"message":"m"}"#)))
        #expect(planner.config.groundDepthReach == CoverageConfig().groundDepthReach)
    }

    @Test func outFtRoundTrips() throws {
        let decoded = try item(#"{"kind":"band","band":"ground","span_ft":[0,1],"out_ft":5.13,"message":"m"}"#)
        #expect(decoded.outFt == 5.13)
        #expect(try JSONDecoder().decode(PlacementMissingEvidence.self, from: JSONEncoder().encode(decoded)) == decoded)
    }

    @Test func groundOutIsMetOnlyWhenEveryCellIsSeenThatFar() {
        // GroundDepthTests' cameras: out 1.0 and 4.0 leave the depth at 2.1336 m (7 ft) over cells -4 to 5.
        var map = CoverageMap(wall: standardWall())
        for out: Float in [1.0, 4.0] {
            for s: Float in [0, 0.3] { map.observe(GroundDepthTests.downCamera(s: s, out: out), trackingNormal: true) }
        }
        let planner = GapPlanner()
        #expect(planner.isSatisfied(GapPlan(band: .ground, span: 0...0.6, reason: .server, need: .groundOut(7 * 0.3048), requestedOutFt: 7), map))
        #expect(!planner.isSatisfied(GapPlan(band: .ground, span: 0...0.6, reason: .server, need: .groundOut(7.1 * 0.3048), requestedOutFt: 7.1), map))
        // Cell 6 lies past the seen cells: 6 of 7 met would do for cells (80 %), not for a reach.
        let wider = GapPlan(band: .ground, span: 0...1.0668, reason: .server, need: .groundOut(1))
        #expect(planner.progress(of: wider, map) < 1)
        #expect(!planner.isSatisfied(wider, map))
    }

    /// The server asks for the smallest 6-decimal value strictly above its rule (solver.py
    /// `_above` on origin/t3/server): D + r for facing is 4.833333 ft, so it asks for 4.833334.
    /// The export reports 4.8333 ft for a clearance just under that, which does not settle the
    /// request; 4.8334 does. Rounding the request to the export's four decimals called both met.
    @Test func aReachJustUnderAStrictlyAboveRequestIsNotMet() throws {
        let plan = try #require(GapPlanner().plan(
            for: item(#"{"kind":"band","band":"facing","span_ft":[0,1],"out_ft":4.833334,"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(plan.requestedOutFt == 4.833334)
        // A walk that leaves 4.83332 ft clear at cell 1's far edge (0.3048 m): feet down, 4.8333.
        let under: Float = 4.83332 * 0.3048
        let over: Float = 4.83345 * 0.3048
        for (clear, met) in [(under, false), (over, true)] {
            var map = CoverageMap(wall: standardWall())
            FacingTests.walk(&map, out: clear + ServerErrorDefaults.wall(.tap, atS: 0.3048), from: -1, to: 1.5)
            let exported = try #require(map.walkedClearance(at: 1))
            #expect(SceneExport.feetDown(exported) == (met ? 4.8334 : 4.8333))
            let cell = GapPlan(band: .ground, span: 0.1524...0.3048, reason: .server, need: plan.need, requestedOutFt: plan.requestedOutFt)
            #expect(GapPlanner().isSatisfied(cell, map) == met)
        }
    }

    /// A reach request is met over its whole span as the export reports it. The export stops at
    /// a marked end, so a request reaching past it stays open on the server; counting only the
    /// cells inside the ends called it met.
    @Test func aReachRequestPastAMarkedEndIsNotMet() {
        let planner = GapPlanner()
        var ground = CoverageMap(wall: standardWall())
        for out: Float in [1.0, 4.0] {
            for s: Float in [0, 0.3] { ground.observe(GroundDepthTests.downCamera(s: s, out: out), trackingNormal: true) }
        }
        let deep = GapPlan(band: .ground, span: 0...0.6, reason: .server, need: .groundOut(7 * 0.3048), requestedOutFt: 7)
        #expect(planner.isSatisfied(deep, ground))
        ground.setEnd(.right, at: 0.5)
        #expect(!planner.isSatisfied(deep, ground))
        #expect(abs(planner.progress(of: deep, ground) - 0.5 / 0.6) < 1e-3)

        var walked = CoverageMap(wall: standardWall())
        FacingTests.walk(&walked, out: 1.6 + ServerErrorDefaults.wall(.tap, atS: 0.9144), from: -1, to: 1.5)
        let facing = GapPlan(band: .ground, span: 0...0.786, reason: .server, need: .walkOut(1.5))
        #expect(planner.isSatisfied(facing, walked))
        walked.setEnd(.right, at: 0.5)
        #expect(!planner.isSatisfied(facing, walked))
    }

    /// The request's span is read as the server sent it and the export as written. Ground seen up
    /// to an end at 0.5 m (1.64042 ft) is written to 1.6404 ft, rounded inward. The server reads
    /// a shortfall under its 0.01 ft tolerance as rounding, so a request to 1.64042 ft is met, but
    /// one 0.02 ft past what was seen is not. Past the (unexplored) end by that much, it has no
    /// capture request at all (`reachesPastEnd`), so it is planned here as if no end were marked.
    @Test func aRequestSpanIsReadInTheServersFeet() throws {
        var map = CoverageMap(wall: standardWall())
        for out: Float in [1.0, 4.0] {
            for s: Float in [0, 0.3] { map.observe(GroundDepthTests.downCamera(s: s, out: out), trackingNormal: true) }
        }
        map.setEnd(.right, at: 0.5)
        let rounding = try #require(GapPlanner().plan(
            for: item(#"{"kind":"band","band":"ground","span_ft":[0,1.64042],"out_ft":7.0,"message":"m"}"#), leftEnd: nil, rightEnd: map.rightEnd))
        #expect(GapPlanner().isSatisfied(rounding, map))
        let pastEnd = try item(#"{"kind":"band","band":"ground","span_ft":[0,1.66042],"out_ft":7.0,"message":"m"}"#)
        #expect(GapPlanner().plan(for: pastEnd, leftEnd: nil, rightEnd: map.rightEnd) == nil)
        let short = try #require(GapPlanner().plan(for: pastEnd, leftEnd: nil, rightEnd: nil))
        #expect(!GapPlanner().isSatisfied(short, map))
        let exact = try #require(GapPlanner().plan(
            for: item(#"{"kind":"band","band":"ground","span_ft":[0,1.6404],"out_ft":7.0,"message":"m"}"#), leftEnd: nil, rightEnd: map.rightEnd))
        #expect(GapPlanner().isSatisfied(exact, map))
    }

    @Test func walkOutIsMetByWalkingPastTheSpanFarEnoughOut() {
        var map = CoverageMap(wall: standardWall())
        FacingTests.walk(&map, out: 1.5, from: -1, to: 1.5)
        let planner = GapPlanner()
        // Needs 1.5 m clear: the walk at 1.5 m leaves 1.5 less the error.
        let gap = GapPlan(band: .ground, span: 0...0.786, reason: .server, need: .walkOut(1.5))
        #expect(!planner.isSatisfied(gap, map))
        // A second pass later on: poses join in the order they were captured, so it needs its own times.
        FacingTests.walk(&map, out: 1.5 + ServerErrorDefaults.wall(.tap, atS: 0.9144) + 0.01, from: -1, to: 1.5, startTime: 100)
        #expect(planner.isSatisfied(gap, map))
    }

    @Test func overheadIsMetByARecordedViewHighEnough() {
        var map = OverheadTests.walkedWall()
        let planner = GapPlanner()
        let gap = GapPlan(band: .wall, span: 0...0.786, reason: .server, need: .overhead(6.5 * 0.3048))
        #expect(!planner.isSatisfied(gap, map))
        // Seen but not confirmed clear is not evidence.
        #expect(!map.overheadReach(from: OverheadTests.tiltUp(pitch: 30)).isEmpty)
        #expect(!planner.isSatisfied(gap, map))
        map.recordOverhead(OverheadTests.tiltUp(pitch: 30), trackingNormal: true)
        #expect(planner.isSatisfied(gap, map))
        #expect(!planner.isSatisfied(GapPlan(band: .wall, span: 0...0.786, reason: .server, need: .overhead(5.1)), map))
    }

    /// wallCamera(s: c) sees a wall cell with lower edge L exactly when c is in [L - 0.7881,
    /// L + 0.9405], and cameras 0.3 m apart are far enough apart to cover it. Views at -1.5 ...
    /// 0 cover cells up to 3 (L = 0.4572; cell 4 at 0.6096 has only the view at 0).
    static func walkedWall(to last: Float) -> CoverageMap {
        var map = CoverageMap(wall: standardWall())
        for c in stride(from: Float(-1.5), through: last + 1e-3, by: 0.3) { map.observe(wallCamera(s: c), trackingNormal: true) }
        return map
    }

    @Test func aServerWallRequestNeedsItsWholeSpanAsExported() {
        let planner = GapPlanner()
        var map = Self.walkedWall(to: 0)
        #expect((0...3).allSatisfy { map.level(.wall, $0) == .covered } && map.level(.wall, 4) == .seen)
        // Cells 0 ... 4 lie over 0.05 ... 0.7: 4 of 5 covered, enough for the phone's own request.
        #expect(planner.isSatisfied(GapPlan(band: .wall, span: 0.05...0.7, reason: .wallNearMeter), map))
        // The export lists the wall up to 0.6096 m (2 ft) of the requested 0.1640 ... 2.2966 ft.
        let server = GapPlan(band: .wall, span: 0.05...0.7, reason: .server)
        #expect(abs(planner.progress(of: server, map) - (2 - 0.164) / (2.2966 - 0.164)) < 1e-3)
        #expect(!planner.isSatisfied(server, map))
        // A view at 0.3 covers cell 4 as well.
        map.observe(wallCamera(s: 0.3), trackingNormal: true)
        #expect(planner.progress(of: server, map) == 1)
        #expect(planner.isSatisfied(server, map))
    }

    /// The export stops at a marked end, so a request reaching past it stays open whatever is
    /// covered; counting only the cells inside the ends called it met.
    @Test func aServerRequestPastAMarkedEndIsNotMet() {
        let planner = GapPlanner()
        var map = Self.walkedWall(to: 0.3)
        map.setEnd(.right, at: 0.5)
        #expect(planner.isSatisfied(GapPlan(band: .wall, span: 0.05...0.7, reason: .wallNearMeter), map))
        #expect(!planner.isSatisfied(GapPlan(band: .wall, span: 0.05...0.7, reason: .server), map))
    }

    /// A request in feet that ends exactly at a marked end is met once covered, although its
    /// span went through Float meters on the way.
    @Test func aServerRequestEndingAtTheEndIsMetInFeet() throws {
        let planner = GapPlanner()
        var map = Self.walkedWall(to: 0.3)
        map.setEnd(.right, at: 2 * 0.3048)
        let plan = try #require(planner.plan(for: item(#"{"kind":"band","band":"wall","span_ft":[0.5,2.0],"message":"m"}"#), leftEnd: nil, rightEnd: map.rightEnd))
        #expect(planner.isSatisfied(plan, map))
    }

    /// A band request reaching past a marked end can't be met: the map records nothing past an
    /// end, except ground past a limit end, so its bar would stay at 0 % (issue #35). It has no
    /// request and goes to review. Reaching the end, within the server's 0.01 ft, is not past it.
    @Test func aBandRequestPastAMarkedEndIsNotACaptureRequest() throws {
        let planner = GapPlanner()
        // Ends at -1 m and 1 m (3.28084 ft).
        let wall = try item(#"{"kind":"band","band":"wall","span_ft":[2.0,4.0],"message":"m"}"#)
        #expect(planner.plan(for: wall, leftEnd: -1, rightEnd: 1) == nil)
        #expect(planner.plan(for: wall, leftEnd: -1, rightEnd: 1, limitEnds: [.right]) == nil)
        #expect(planner.plan(for: wall, leftEnd: -1, rightEnd: nil) != nil)
        let ground = try item(#"{"kind":"band","band":"ground","span_ft":[-4.0,-2.0],"out_ft":2.5,"message":"m"}"#)
        #expect(planner.plan(for: ground, leftEnd: -1, rightEnd: 1) == nil)
        #expect(planner.plan(for: ground, leftEnd: -1, rightEnd: 1, limitEnds: [.right]) == nil)
        #expect(planner.plan(for: ground, leftEnd: -1, rightEnd: 1, limitEnds: [.left]) != nil)
        // A walk past a limit end records nothing facing the wall there.
        let facing = try item(#"{"kind":"band","band":"facing","span_ft":[2.0,4.0],"out_ft":5.0,"message":"m"}"#)
        #expect(planner.plan(for: facing, leftEnd: -1, rightEnd: 1, limitEnds: [.right]) == nil)
        // The server rounds a span outward: 3.2809 ft ends at the right end.
        let toEnd = try item(#"{"kind":"band","band":"wall","span_ft":[0.5,3.2809],"message":"m"}"#)
        #expect(planner.plan(for: toEnd, leftEnd: -1, rightEnd: 1) != nil)
    }

    @Test func pastEndAsksForTheGroundBeyondThatEnd() throws {
        // Left end at s = -3 m: ask for -5...-3. Right end at 4 m: ask for 4...6.
        let left = try #require(GapPlanner().plan(for: item(#"{"kind":"past_end","side":"left","message":"m"}"#), leftEnd: -3, rightEnd: 4))
        #expect(left.band == .ground && left.span == -5 ... -3)
        let right = try #require(GapPlanner().plan(for: item(#"{"kind":"past_end","side":"right","message":"m"}"#), leftEnd: -3, rightEnd: 4))
        #expect(right.span == 4...6)
        // One owner of the 2 m: the item's request is the planner's past-end request from that end.
        #expect(left == GapPlanner().pastEndPlan(side: .left, end: -3))
        #expect(right == GapPlanner().pastEndPlan(side: .right, end: 4))
    }

    /// "It turns a corner" on a past_end request: the request can't follow the corner, so it is
    /// skipped, and the request planned from the homeowner's corner is recorded with it
    /// (`ScanEngine.skipCurrentGap(deferring:)`). The next answer's repeat of that side is not
    /// raised; a past_end from a different end on that side, or another request, still is. Ends
    /// at -3 and 4 m; the corner nearer or farther than the cleared end, on either side.
    @Test(arguments: [
        (WalkSide.left, Float(-2), Float(-1)), (.left, -4, -5),
        (.right, 3, 2), (.right, 5, 6),
    ])
    func aCornerIsNotAskedForAgainFromThatEnd(side: WalkSide, corner: Float, laterEnd: Float) throws {
        let planner = GapPlanner()
        let pastEnd = try item(#"{"kind":"past_end","side":"\#(side.rawValue)","message":"m"}"#)
        let ground = try item(#"{"kind":"band","band":"ground","span_ft":[3.0,5.5],"message":"m"}"#)
        func ends(_ s: Float) -> (left: Float, right: Float) { side == .left ? (s, 4) : (-3, s) }
        let cleared = ends(side == .left ? -3 : 4)
        let raised = try #require(planner.plan(for: pastEnd, leftEnd: cleared.left, rightEnd: cleared.right))
        let deferred = planner.pastEndPlan(side: side, end: corner)
        func next(at end: (left: Float, right: Float), skipped: [GapPlan]) -> [PlacementMissingEvidence] {
            planner.serverRequests(
                in: [pastEnd, ground], leftEnd: end.left, rightEnd: end.right, limitEnds: [],
                asked: [raised], skipped: skipped, limit: 5
            ).map(\.item)
        }
        // The skipped request alone misses the repeat: the corner moved the end.
        #expect(next(at: ends(corner), skipped: [raised]) == [pastEnd, ground])
        // With the corner's request recorded, the repeat is not raised; the ground request is.
        #expect(next(at: ends(corner), skipped: [raised, deferred]) == [ground])
        // The end moved on from the corner later: walking past it is a new view.
        #expect(next(at: ends(laterEnd), skipped: [raised, deferred]) == [pastEnd, ground])
    }

    /// Issue #39: the answer asks for two views and the homeowner can't get to the first. The
    /// next answer, which still lists it, moves on to the second instead of raising the first
    /// again or stopping; once both were raised, none is left and the result shows. The skipped
    /// past_end request gets its end back (`ScanEngine.settleClearedEnd`), so it comes back as
    /// the same request; a met one moves its end on, and the next asks for new ground.
    @Test func eachServerItemIsAskedForOnceAndARefusalMovesOnToTheNext() throws {
        let planner = GapPlanner()
        let pastEnd = try item(#"{"kind":"past_end","side":"left","message":"Walk past the left end."}"#)
        let ground = try item(#"{"kind":"band","band":"ground","span_ft":[3.0,5.5],"message":"Film the ground."}"#)
        let requests = planner.serverRequests(in: [pastEnd, ground], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5)
        #expect(requests.map { $0.item } == [pastEnd, ground])
        let first = try #require(requests.first)
        #expect(first.plan.span == -5 ... -3)

        // "I can't get there": raised and skipped, with the end it cleared put back.
        let reworded = try item(#"{"kind":"past_end","side":"left","message":"Keep walking past the left end."}"#)
        let second = try #require(planner.nextServerRequest(
            in: [reworded, ground], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [first.plan], skipped: [first.plan]))
        #expect(second.item == ground)

        #expect(planner.nextServerRequest(
            in: [reworded, ground], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [first.plan, second.plan], skipped: [first.plan])?.item == nil)
        // A right past-end is a different view.
        let right = try item(#"{"kind":"past_end","side":"right","message":"m"}"#)
        #expect(planner.nextServerRequest(
            in: [reworded, ground, right], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [first.plan, second.plan], skipped: [])?.item == right)
        // Met, the left end moved on to -5 m: the next past_end request is the 2 m after it.
        #expect(planner.nextServerRequest(
            in: [reworded], leftEnd: -5, rightEnd: 4, limitEnds: [], asked: [first.plan], skipped: [])?.plan.span == -7 ... -5)
    }

    /// The next answer works a refused view out again from the new scene, so it can come back with
    /// its span moved inside the old one. That asks for nothing new and isn't raised again;
    /// another stretch of the band is a new view. (#49: the reach that came back here at 4.9 ft
    /// after 4.833334 ft is now a new view, since progress requires it.)
    @Test func aRefusedViewWithSlightlyDifferentNumbersIsNotRaisedAgain() throws {
        let planner = GapPlanner()
        let refused = try item(#"{"kind":"band","band":"ground","span_ft":[2.4,7.9],"out_ft":4.833334,"message":"m"}"#)
        let plan = try #require(planner.plan(for: refused, leftEnd: -3, rightEnd: 4))
        let moved = try item(#"{"kind":"band","band":"ground","span_ft":[2.41,7.9],"out_ft":4.833334,"message":"m"}"#)
        #expect(planner.nextServerRequest(in: [moved], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [plan], skipped: [plan])?.item == nil)
        let elsewhere = try item(#"{"kind":"band","band":"ground","span_ft":[-7.9,-2.4],"out_ft":4.833334,"message":"m"}"#)
        #expect(planner.nextServerRequest(
            in: [moved, elsewhere], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [plan], skipped: [plan])?.item == elsewhere)
    }

    /// Requests raised without a tap keep the past-end guard (issue #35): ground past a marked
    /// unexplored end isn't raised, ground past a limit end is.
    @Test func theAutomaticRequestsKeepThePastEndGuard() throws {
        let planner = GapPlanner()
        let ground = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,4.0],"message":"m"}"#)
        #expect(planner.nextServerRequest(in: [ground], leftEnd: -1, rightEnd: 1, limitEnds: [], asked: [], skipped: [])?.item == nil)
        #expect(planner.nextServerRequest(in: [ground], leftEnd: -1, rightEnd: 1, limitEnds: [.right], asked: [], skipped: [])?.item != nil)
    }

    /// "N more views to finish" counts what the answer will raise: two items asking for the same
    /// view count once, and no more than the requests left in the pass.
    @Test func theViewsLeftCountEachViewOnceUpToTheLimit() throws {
        let planner = GapPlanner()
        let ground = try item(#"{"kind":"band","band":"ground","span_ft":[3.0,5.5],"message":"Film the ground."}"#)
        let sameView = try item(#"{"kind":"band","band":"ground","span_ft":[3.0,5.5],"message":"Another check wants it too."}"#)
        let pastEnd = try item(#"{"kind":"past_end","side":"left","message":"m"}"#)
        let all = [ground, sameView, pastEnd]
        #expect(planner.serverRequests(in: all, leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [ground, pastEnd])
        #expect(planner.serverRequests(in: all, leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 1).count == 1)
        #expect(planner.serverRequests(in: all, leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 0).isEmpty)
    }

    /// A request that asks for more than one already raised is a new view, even when it mostly
    /// overlaps: a longer stretch (2 to 9 ft after 2 to 8 ft, 6/7 of it already asked for) or
    /// ground farther out (5.10 ft after 4.83 ft, 0.08 m more). A span inside the earlier one, with
    /// the same reach, is the same view. This holds for earlier requests, skipped ones and items
    /// in the same answer.
    @Test func aRequestAskingForMoreIsANewView() throws {
        let planner = GapPlanner()
        let first = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.83,"message":"m"}"#)
        let longer = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,9.0],"out_ft":4.83,"message":"m"}"#)
        let farther = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":5.10,"message":"m"}"#)
        let jitter = try item(#"{"kind":"band","band":"ground","span_ft":[2.005,7.995],"out_ft":4.83,"message":"m"}"#)
        let asked = try #require(planner.plan(for: first, leftEnd: -3, rightEnd: 4))

        // After the first was raised, or skipped.
        for (raised, skipped) in [([asked], [GapPlan]()), ([asked], [asked])] {
            let next = planner.serverRequests(
                in: [first, jitter, longer, farther], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: raised, skipped: skipped, limit: 5)
            #expect(next.map { $0.item } == [longer, farther])
        }
        // Two items in one answer: each asks for more than the one before it.
        #expect(planner.serverRequests(
            in: [first, longer], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [first, longer])
        #expect(planner.serverRequests(
            in: [first, farther], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [first, farther])
        // Asking for less than an earlier item is no new view.
        #expect(planner.serverRequests(
            in: [longer, first, jitter], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [longer])
        #expect(planner.serverRequests(
            in: [farther, first], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [farther])
    }

    /// #49: a request whose span runs past an asked or skipped one (or the item before it in the
    /// same answer) by any amount is a new view: 0.009, 0.010, 0.011 or 0.09 ft past either edge,
    /// with the server's exact span_ft. Spans are compared exactly; rounding tolerance is for
    /// progress against real coverage, not for comparing two requests.
    @Test(arguments: ["[2.0,8.009]", "[2.0,8.01]", "[2.0,8.011]", "[2.0,8.09]", "[1.991,8.0]", "[1.99,8.0]", "[1.989,8.0]", "[1.91,8.0]"])
    func aRequestRunningPastAnEarlierOneIsANewView(spanFt: String) throws {
        let planner = GapPlanner()
        let old = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.833334,"message":"m"}"#)
        let oldPlan = try #require(planner.plan(for: old, leftEnd: -3, rightEnd: 4))
        let extended = try item(#"{"kind":"band","band":"ground","span_ft":\#(spanFt),"out_ft":4.833334,"message":"m"}"#)
        let plan = try #require(planner.plan(for: extended, leftEnd: -3, rightEnd: 4))
        let span = try #require(extended.spanFt)
        #expect(plan.requestedSpanFt == min(span.x, span.y)...max(span.x, span.y), "the request keeps the server's exact span_ft")
        #expect(!plan.asksForSameView(as: oldPlan), "\(spanFt)")
        let afterAsked = planner.serverRequests(in: [extended], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [oldPlan], skipped: [], limit: 5)
        let afterSkipped = planner.serverRequests(in: [extended], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [oldPlan], skipped: [oldPlan], limit: 5)
        let oneAnswer = planner.serverRequests(in: [old, extended], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5)
        #expect(afterAsked.map { $0.item } == [extended], "after asked for \(spanFt)")
        #expect(afterSkipped.map { $0.item } == [extended], "after skipped for \(spanFt)")
        #expect(oneAnswer.map { $0.item } == [old, extended], "one answer for \(spanFt)")
        // The limit and the homeowner's stop are unchanged: no room left raises nothing.
        #expect(planner.serverRequests(in: [extended], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [oldPlan], skipped: [], limit: 0).isEmpty)
    }

    /// Root's review of d837c4e4: the rounding allowance must not stack. Coverage seen to exactly
    /// [2, 8] ft meets [2, 8.0085] ft, since progress reads its 0.0085 ft tail as rounding. The
    /// next request, [2, 8.0175] ft, runs only 0.009 ft past that one, but 0.0175 ft past the
    /// coverage: progress is about 0.997, so it stays available.
    @Test func aRoundingAllowanceUsedOnceIsNotUsedAgain() throws {
        let planner = GapPlanner()
        let old = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0085],"out_ft":4.833334,"message":"m"}"#)
        let next = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0175],"out_ft":4.833334,"message":"m"}"#)
        let oldPlan = try #require(planner.plan(for: old, leftEnd: -3, rightEnd: 4))
        let nextPlan = try #require(planner.plan(for: next, leftEnd: -3, rightEnd: 4))
        let feetToMeters = 1 / SceneUnits.feetPerMeter
        let seen = Float(2.0 * feetToMeters)...Float(8.0 * feetToMeters)
        #expect(GapPlanner.fraction(of: oldPlan.requestedSpanInFeet, coveredBy: [seen]) == 1)
        let progress = GapPlanner.fraction(of: nextPlan.requestedSpanInFeet, coveredBy: [seen])
        #expect(abs(progress - (1 - 0.0175 / 6.0175)) < 1e-9, "progress \(progress)")
        #expect(!nextPlan.asksForSameView(as: oldPlan))
        for skipped in [[GapPlan](), [oldPlan]] {
            #expect(planner.serverRequests(in: [next], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [oldPlan], skipped: skipped, limit: 5).map { $0.item } == [next])
        }
    }

    /// Equal and contained requests stay the same view, after asked, after skipped and within one
    /// answer, including an edge moved inward by 0.009 ft.
    @Test func anEqualOrContainedRequestIsTheSameView() throws {
        let planner = GapPlanner()
        let old = try item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.833334,"message":"m"}"#)
        let oldPlan = try #require(planner.plan(for: old, leftEnd: -3, rightEnd: 4))
        for json in ["[2.0,8.0]", "[2.009,8.0]", "[2.0,7.991]", "[3.0,7.0]"] {
            let inside = try item(#"{"kind":"band","band":"ground","span_ft":\#(json),"out_ft":4.833334,"message":"m"}"#)
            let plan = try #require(planner.plan(for: inside, leftEnd: -3, rightEnd: 4))
            #expect(plan.asksForSameView(as: oldPlan), "\(json)")
            #expect(planner.serverRequests(in: [inside], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [oldPlan], skipped: [oldPlan], limit: 5).isEmpty, "\(json)")
            #expect(planner.serverRequests(in: [old, inside], leftEnd: -3, rightEnd: 4, limitEnds: [], asked: [], skipped: [], limit: 5).map { $0.item } == [old], "\(json)")
        }
    }

    /// #49: a reach is compared exactly, as progress compares it. Any reach above the old one is
    /// new evidence; an equal or lower one isn't. Requests built in meters (no requestedOutFt or
    /// requestedSpanFt) compare their meters converted, as progress reads them.
    @Test func aStricterReachIsANewViewAndAnEqualOneIsNot() throws {
        let planner = GapPlanner()
        let old = try #require(planner.plan(for: item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.833334,"message":"m"}"#), leftEnd: -3, rightEnd: 4))
        let stricter = try #require(planner.plan(for: item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.833335,"message":"m"}"#), leftEnd: -3, rightEnd: 4))
        let equal = try #require(planner.plan(for: item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.833334,"message":"m"}"#), leftEnd: -3, rightEnd: 4))
        let lower = try #require(planner.plan(for: item(#"{"kind":"band","band":"ground","span_ft":[2.0,8.0],"out_ft":4.8,"message":"m"}"#), leftEnd: -3, rightEnd: 4))
        #expect(!stricter.asksForSameView(as: old))
        #expect(equal.asksForSameView(as: old))
        #expect(lower.asksForSameView(as: old))
        let inMeters = GapPlan(band: .ground, span: 0.6...2.4, reason: .server, need: .groundOut(1.5))
        #expect(inMeters.requestedSpanInFeet == SceneExport.round4(Double(Float(0.6)) * SceneUnits.feetPerMeter)...SceneExport.round4(Double(Float(2.4)) * SceneUnits.feetPerMeter))
        #expect(inMeters.asksForSameView(as: inMeters))
        #expect(!GapPlan(band: .ground, span: 0.6...2.4, reason: .server, need: .groundOut(1.51)).asksForSameView(as: inMeters))
        #expect(!GapPlan(band: .ground, span: 0.6...2.4, reason: .server, need: .walkOut(1.5)).asksForSameView(as: inMeters), "another kind of need")
    }

    /// An overhead request without a height takes any tilt-up view, so it asks for no more than
    /// one with a height; one with a height asks for more than one without.
    @Test func anOverheadRequestWithAHeightAsksForMoreThanOneWithout() throws {
        let planner = GapPlanner()
        let anyView = try #require(planner.plan(for: item(#"{"kind":"band","band":"overhead","span_ft":[0,3],"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        let high = try #require(planner.plan(for: item(#"{"kind":"band","band":"overhead","span_ft":[0,3],"out_ft":7.0,"message":"m"}"#), leftEnd: nil, rightEnd: nil))
        #expect(anyView.asksForSameView(as: high))
        #expect(!high.asksForSameView(as: anyView))
        #expect(high.asksForSameView(as: high))
    }

    /// A met past_end request moves its end on past the ground it showed, and the next one asks
    /// for the 2 m after that. With the end left cleared, the next request asked for the ground
    /// at the meter (issue #35).
    @Test func aMetPastEndRequestMovesItsEndOn() throws {
        let planner = GapPlanner()
        let pastLeft = try item(#"{"kind":"past_end","side":"left","message":"m"}"#)
        let first = try #require(planner.plan(for: pastLeft, leftEnd: -3, rightEnd: 4))
        let moved = planner.endAfterPastEnd(first, side: .left, clearedAt: -3)
        #expect(moved == -5)
        #expect(planner.plan(for: pastLeft, leftEnd: moved, rightEnd: 4)?.span == -7 ... -5)
        #expect(planner.plan(for: pastLeft, leftEnd: nil, rightEnd: 4)?.span == -2 ... 0)
        let pastRight = try item(#"{"kind":"past_end","side":"right","message":"m"}"#)
        let right = try #require(planner.plan(for: pastRight, leftEnd: -3, rightEnd: 4))
        #expect(planner.endAfterPastEnd(right, side: .right, clearedAt: 4) == 6)
    }

    @Test func clearingAnEndLetsCoverageGrowPastIt() throws {
        let wall = try #require(WallFrame(meter: SIMD3(0, 1.5, 0), outward: SIMD3(0, 0, 1), groundY: 0))
        var map = CoverageMap(wall: wall)
        map.setEnd(.right, at: 1)
        #expect(map.visibleRange.upperBound == 1)
        map.clearEnd(.right)
        #expect(map.rightEnd == nil)
        // Nothing seen yet: the fog reaches 2.5 m either side of the meter again.
        #expect(map.visibleRange.upperBound == 2.5)
    }
}

/// #129: at the entry cap on a chain with corners the export joins the ground to one entry fewer
/// per corner (`SceneExport.observedBudget`), and the planner must read the ground the same way.
/// It read it at the full share, so a reach the export joined down still met the request locally.
@Suite struct CorneredCapSettlementTests {
    /// 125 touching ground spans (the full share) 0.05 m wide from s = -3, reaching 1 m and 1.5 m
    /// in turn, on `ChainExportTests.wall` (corners at -2 and 3).
    @Test func theGroundIsReadAsTheCorneredExportWritesIt() throws {
        let spans = (0..<SceneExport.bandBudget).map { i in
            let low = -3 + Float(i) * 0.05
            return ObservedSpan(span: low...(low + 0.05), out: i.isMultiple(of: 2) ? 1 : 1.5)
        }
        var input = ChainExportTests.input()
        input.coverage.ground = spans
        let data = try SceneExport.jsonData(input)
        #expect(try SceneSchemas.scene().validate(data) == [])
        let written = try ServerRequestSettlementTests.entries(data, band: "ground")
        let corners = input.wall.chain.segments.count - 1
        #expect(corners == 2)
        let planned = GapPlanner.exported(spans, "ground", corners: corners)
        #expect(planned.count == SceneExport.bandBudget - corners)
        let fullShare = ObservedSpan.coarsened(spans, toAtMost: SceneExport.bandBudget)

        let needed = SceneExport.feetDown(1.5)
        func planner(_ spans: [ObservedSpan], _ requested: ClosedRange<Double>) -> Bool {
            GapPlanner.fraction(of: requested, coveredBy: spans.filter { SceneExport.feetDown($0.out) >= needed }.map(\.span)) >= 1
        }
        // A request for 1.5 m over each 1.5 m cell, 1 mm inside it, away from the corners' cuts.
        var shrunk = 0
        for i in stride(from: 1, to: spans.count, by: 2) {
            let span = spans[i].span
            guard abs(span.lowerBound + 2) > 0.2, abs(span.upperBound - 3) > 0.2, abs(span.lowerBound - 3) > 0.2 else { continue }
            let requested = (Double(span.lowerBound) + 0.001) * SceneUnits.feetPerMeter...(Double(span.upperBound) - 0.001) * SceneUnits.feetPerMeter
            let settles = ServerRequestSettlementTests.seenTo(written, requested.lowerBound, requested.upperBound) >= needed
            #expect(planner(planned, requested) == settles, "cell at s = \(span.lowerBound)")
            if planner(fullShare, requested) && !settles { shrunk += 1 }
        }
        // The full share met requests the export no longer does.
        #expect(shrunk > 0)
    }
}
