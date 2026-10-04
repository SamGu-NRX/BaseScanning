# The capture packet

The capture packet is how a scan leaves the phone. It is specified by the server team's capture
packet spec, `formatVersion` 0.4, together with its intake API. The spec is
`docs/capture-packet-spec.md` in the private repository `huntertcarver/house-scanning-server`,
and the intake API is its Appendix D. That spec is the one contract; this folder does not define
a format of its own.

This folder holds the app side's proposals for the next revision (0.5): what the app records
that 0.4 has no field for, and three arrival checks for the server's validator
([validator-checks.md](validator-checks.md)). Packet 1.1, which this folder used to define, is
retired as a contract proposal; that version is still readable at `t3/packet` commit `d5439cf`.

The app still writes a packet 1.1 for its own use: the `scan.zip` that "Share scan" offers,
for looking into a scan by hand. Nothing uploads it, the server's intake does not accept it,
and it is not an approved revision of 0.4. Fields it carries that 0.4 lacks, such as the end
provenance and guidance log below, are additive local diagnostics. Each is also listed here as
a proposal for 0.5, which the contract's owners may adopt, change or decline.

## Proposals for 0.5

Each item is something the app knows at capture time and the server would otherwise have to
guess. All eleven are unapproved proposals. None is part of the current contract, and the app must
not send them until the contract's owners adopt them in a version and with a compatibility rule
of their choosing. The study numbers cited motivate device tests; none is measured phone
performance.

1. **The meter anchor's orientation.** 0.4 records the meter tap's point, not the wall it sits
   on. Every placement distance is measured along that wall from the meter (C1's `s = 0`), and
   the app knows the wall at the tap: the vertical plane its raycast hit. Proposal: on the
   `meter` tap, a `meterAnchor` with `transform` (anchor to world in the tap's epoch: origin at
   the meter, +y up, +z the wall's outward normal, +x along the wall to the right seen from
   outside) and `normalSource`: `single_raycast` (one plane hit at the tap), `fitted` (fitted
   across the walk), `mesh` or `estimated`. The source matters: the signals lab (#59,
   `experiments/edge-geometry`) found a yaw error of 2.2° median and 6.7° p90 for a stand-in for
   the app's plane, a 0.3 m plane fitted to depth on ETH3D photos, not a device raycast. At that
   p90, 10 ft along the wall moves a point about 1 ft. A per-capture yaw uncertainty is worth
   adding once a device study measures it.
2. **The ground's height at the meter.** 0.4 notes that world y = 0 is the phone's height at
   session start, not the ground. The app measures the ground where it can: the horizontal plane
   below the meter. Proposal: `meterAnchor.groundY` (world meters) with `groundSource`
   (`plane`, `mesh`, `assumed`), so headroom and "ground under the battery" start from a
   measurement when there is one.
3. **What a marked end or feature is.** 0.4's tap labels name the point, not the answers the
   homeowner gave about it:
   - whether a wall end is a real end (something blocks it) or unexplored (it turns a corner,
     or no answer), which decides whether the server may reject for want of space;
   - whether the homeowner marked a wall end ("Wall ends here") or the app inferred it from the
     walk: where the walk reached after "Can't get there" or "The wall keeps going", or past the
     ground a `past_end` request showed. An inferred end has the same place and kind as a marked
     one, but nobody pointed at it, so it says where the scan stopped, not where the wall does;
   - whether a window opens, which the rules may use (whether a fixed window is exempt from the
     opening clearance is the rules' choice, not the app's);
   - which corner taps belong to the same door or window.

   Proposal: `taps[].attrs` with `endKind` (`limit`, `unexplored`), `operable` and `inferred`,
   and `taps[].group` for taps that outline one object.

   The app's local packet 1.1 (above) already writes the provenance: a wall end the homeowner
   marked has its mark time `t`; an inferred one has no `t` and `attrs.inferred: true`. A
   missing `t` alone never means the homeowner marked it.
4. **The guidance log.** Every request the homeowner was shown (walk, tilt to the ground, mark
   an end, close-up, a gap the phone or the server asked for), when, and its outcome: `met`,
   `skipped`, `cannot_reach`, `superseded` or `unresolved`. Today "I can't get there" never
   reaches the server, so the server can ask for the same unreachable view again. Proposal:
   `guidance[]` with `id`, `kind`, `origin` (`phone`, `server`), `message`, optional `band` and
   `span`, `tShown`, `tResolved` and `outcome`.

   For the wall's ends, as the app's local packet 1.1 records them: `mark_end` is `met` only
   when the homeowner marked that end. "The wall keeps going" and "Can't get there" close it as
   `cannot_reach`, because the end lay beyond where the homeowner got, and the end placed there
   is inferred. `gap_past_end` is `met` when views past the end settle it, or when the homeowner
   marks the end again and says the wall stops there (something blocks it, or it just ends); the
   wall end's provenance tells those apart. A corner the homeowner marks during the request is
   not `met`: the wall goes on round it, the request can't follow it there, and the server
   settles an unexplored end only with views past it. It closes as `cannot_reach`, and the
   corner stays where the homeowner marked it, unexplored. "I can't get there" is `cannot_reach`
   too, and the end the request had cleared comes back as it was.
5. **Depth between keyframes** (from S6's live map, draft PR #21). Keyframes are kept about every
   0.5 m, but the phone fuses depth at about 10 Hz to decide what was seen. With keyframe depth
   alone the server cannot rebuild that decision. Proposal: optional `depthFrames[]` with `t`,
   `epoch`, pose, intrinsics of the depth map's own grid, and the same depth and confidence files
   as a keyframe. At 256 × 192, one frame with confidence is about 246 KB, so a few hertz rather
   than ten.
6. **Estimated depth with its uncertainty** (S6). A phone without LiDAR can infer metric depth
   from the image, but used without its uncertainty it creates surfaces that are not there.
   Proposal: a depth `source` of `estimated`, which requires a `sigma` file (float32 meters, one
   standard deviation per depth pixel, the depth's layout).
7. **The frame of a plane's boundary.** 0.4 lists `planes[].boundary` without saying its frame.
   ARKit's `boundaryVertices` are relative to the anchor transform, not to `planeExtent`'s centre
   and `rotationOnYAxis`, and packet 1.1 found that mixing the two is an easy mistake. Proposal:
   carry the anchor-frame boundary in a new field whose frame is stated, rather than giving the
   existing `boundary` a meaning an existing reader might not share, and have the validator check
   it lies within `planeExtent` once the centre and rotation are applied.
8. **Photos that frame one span.** With learned depth on ETH3D photos, an along-wall edge came to
   0.9 in p90 when seen within 15° of face-on and 24 in beyond 45° (#59,
   `experiments/edge-geometry`), so a distance measured inside one face-on photo is likely far
   better than one chained across the walk. Proposal: a still or
   keyframe purpose `span` that names the marks the photo frames.
9. **Scale references in view.** One reference near the meter may not be enough: on MARViN's
   walks ARKit's scale varied 1.8% (robust SD) within a walk (#59, `experiments/drift-anatomy`),
   so a reference helps where it appears. Proposal: `references[]` with `kind` (door, brick course, and hand-held ones such
   as an ID card or a Letter sheet), nominal size, and its corners in each photo that shows it.
   Hand-held references ask the homeowner to hold something; #59 rates them worth a device test
   (`experiments/sensor-budget`), and whether the flow may ask is Sam's and Hunter's call.
10. **The meter anchor's pose over time.** ARKit re-estimates an anchor as tracking corrects, so
    the anchor pose at tap time goes stale. Proposal: an optional stream of the meter anchor's
    world pose at each ARKit update, so the server can see how far the meter frame moved. Each
    sample needs its timestamp, on the pose stream's clock, and its epoch: the epoch tells a
    world reset from a correction within one world, and the timestamp ties the sample to the
    keyframes and taps around it. There is no measurement of that movement yet.
11. **Distances from a second phone (low priority).** When a second phone with UWB is present,
    an optional stream of phone-to-phone distances. In #59's simulation on MARViN walks
    (`experiments/drift-anatomy`, question A4), a second phone left at the meter cut the p90 error
    at 20 ft from 10.4 in to 7.7 in at 10 cm ranging noise, and to 4.9 in at 5 cm. That result
    depends on the second phone staying still at a known place, so the proposal requires three
    things: where the second phone stands (at the meter, or a stated position) and that it did
    not move; a time for each range on the capture's clock, so it matches a pose; and the range's
    uncertainty. Ranges from a second phone that moves, or whose place is unknown, are not what
    the study simulated.

Already in 0.4, so not proposed: the kind and distance of each tap's hit, a keyframe for every
tap, and feature-point identifiers. Packet 1.1 also had a fixed sharpness score, distance walked
and a flag for consent to share location. They are not proposed here: distance walked can be
derived from the pose stream, and whether a sharpness score or a sharing-consent record belongs
in the contract is for its owners.
