# Friction on the homeowner's path

Where a first-time homeowner can stall, redo something or have to decide, and what would most
raise the share of scans that finish on the first try. Written for the owner of `t3/ios-mvf`, at
`a39d0a5`. Evidence:

- the Simulator run `sim/20260926-071459-t3-ios-mvf-a39d0a50-replay` on the ADVIO replay;
- the server's answer to that run's upload, `e2e/20260926-071717-739fb6f7` (server `739fb6f`);
- the source at `a39d0a5`. `Kit/` below is `ios/HouseScanKit/Sources/HouseScanKit/`; other paths are under `ios/HouseScan/`.

## The run in numbers

The replay is 34.7 s of walking along a 39.7 m path, 79 frames, with tracking normal throughout;
the app turned it into 26 ft of wall (s −11 to +15 ft). With the autopilot holding each step 3 s,
launch to result took 44 s:

| Stage | Seconds |
| --- | --- |
| Onboarding | 4.6 |
| Find the meter | 3.2 |
| Meter close-up | 4.0, skipped after 2 failed tries (the replay has no meter) |
| Wall walk | 24.7 |
| Feature list | 3.2 |
| Gap request | 3.4, answered "I can't get there" |
| Upload | 0.7 |

Without failures, reaching the result takes 10 taps plus iOS's camera dialog: "Next", "Next",
"Allow camera", "This is my meter", "Wall ends here" and its end answer on each side, "Done with
this wall", "Looks complete". Five of them are decisions: where the meter is, where each end is,
and what is at each end. Seeing the spot in AR adds "See it on your wall" and "Done".

The first try did not finish. The result said "An installer will take a look … 8 checks depend on
areas the scan did not see". The server asked for four more views (ground, wall, facing, overhead)
and for walking past both ends.

## Where a homeowner can stall, in flow order

| Stage | Trigger | What they see | Way forward | Kind |
| --- | --- | --- | --- | --- |
| Find meter | Camera denied, camera failure, unreadable replay (`Runtime/ScanEngine.swift:119, 489, 491` set the failure without changing screen) | Camera denied or failed: "Find your electric meter" with no camera, taps do nothing (from source). Unreadable replay: the app stays on onboarding, no message and no way on (seen with `-replay /nonexistent`, B-03) | None; close the app | Stuck (B-03) |
| Find meter | Tap refused: tracking not normal, or no wall under the tap | "Step a little closer to the wall" for both causes | Tap again | Redo |
| Find meter to gap request | Relocalizing for over 20 s (`Runtime/ScanEngine.swift:466-471`) | Back to "Find your electric meter"; wall, marks and photos gone, no explanation | Start the scan again | Redo |
| Close-up | One problem held for 4 s is a failed try; a photo needs 0.6 s of good frames (`Kit/Capture/CloseUpGate.swift:13, 15`) | One instruction and a named fix, "Can't get a clear shot" from the second failed try | Skip; if frames alternate good and blurry, no try fails and the skip never appears | Redo, can stick (B-04) |
| Walk | A side's end is closer than 6.1 m (20 ft); "Wall ends here" is offered only after that much is covered (`Kit/Guidance/GuidancePlanner.swift:27, 119`) | "Walk slowly to your left" with no way to say the wall ends | "Can't get there", which records the end as unexplored | Decision, recorded wrong (B-06) |
| Walk | After "Wall ends here" | "What's at the left end?": "It turns a corner" / "Something blocks it" | Answer; a corner exports as unexplored (`Runtime/ScanEngine+Actions.swift:85`) | Decision, twice |
| Walk | Ground not seen | "Tilt down to show the ground" | Tilt; on the replay ground was seen under 4 ft of the 26 ft | Redo |
| Walk | Marking a door, window, drive or fence takes two taps; each can be refused | Warning haptic and a reason | Tap again or "Cancel" | Redo |
| Feature list | A chip under "Add something" (`UI/Screens/MarkFeaturesScreen.swift:64`) | Nothing visible; the marking circle exists only on the walk | None; "Looks complete" uploads without the feature | Stuck (B-09) |
| Feature list | A marked window | "Does this window open?" | Answer | Decision |
| Gap request | The phone's own check finds a gap after "Looks complete" | "Show the ground around your meter", a progress bar | Fill 80 %, or "I can't get there", which the upload does not carry (B-08) | Redo |
| Upload | Any failure | "That didn't go through" and raw error text (B-02) | "Try again" resends everything | Redo |
| Result | A check depends on an area not seen: every scan at `a39d0a5` (below) | "An installer will take a look", "Still needed" cards with "Capture it now" | Capture and upload again; ground, facing and overhead requests cannot be settled | Redo, cannot finish |
| Result | An end within reach is unexplored | "Keep walking past the left end of the scan (11 ft 0 in left of the meter)" | Walk past it; the server then asks again for ground there (S2 finding, routed) | Redo |

The autopilot presses buttons directly. It skips the close-up after 4 s and answers every end "It
turns a corner". It never taps "Add something", "Capture it now" or "Try again", and never
triggers a camera denial or the 20 s reset. Those rows come from the source and were not seen
running, apart from the unreadable replay, seen in a separate report (B-03).

## Three changes, ranked

**1. Report how far out the ground was seen.** Every upload says ground was seen out to exactly
1.2 m (3.9 ft). `Runtime/ScanEngine+Export.swift:62` exports the fixed `groundBandDepth` from
`Kit/Coverage/CoverageMap.swift:36`. The clearance checks need ground out to about 6 ft for gas
and AC, 8 ft for a drive and 13 ft for a pool: the battery's 1.83 ft depth, plus the radius, plus
its position error. So these four checks are unsure on every scan, and no "Show the ground"
request can be met however far the homeowner points. This alone guarantees a re-request.

The fix: record each stretch's out distance from where the camera actually saw the ground, and
have the walk's tilt instruction ask for the distance the rules need. The drive (5 ft) and pool
(10 ft) radii are placeholders in `server/rules.yaml`, so the rules owner can also shrink what
is asked; that is S2's call.

**2. Report the space above and in front of the wall, or stop requiring it.** The upload lists
only wall and ground coverage (`Kit/Scene/SceneExport.swift:284-288`), so the facing-gap and
headroom checks are unsure on every scan. The phone's gap planner drops overhead and facing
requests (`Kit/Guidance/GapPlanner.swift:118`), so "Capture it now" cannot settle them either.
The walk already counts the wall face up to the 6.5 ft headroom line
(`Kit/Coverage/CoverageMap.swift:34`), so the frames that saw it may also have seen the space in
front. The fix needs one decision shared with S2: which walk frames count as seeing overhead and
facing. The app then exports those bands.

**3. Let the walk follow a wall round its corners.** The app exports a single wall
(`Kit/Scene/SceneExport.swift:295`), and "It turns a corner" exports the end as unexplored. So
any corner within the 20 ft cable reach becomes a server request to keep walking past it: two of
the six requests on the replay. Separately, a wall ending closer than 20 ft can only be recorded
through "Can't get there" (row 5 above).

The fix: offer "Wall ends here" whenever the end is in view (`Kit/Guidance/GuidancePlanner.swift:119`,
`UI/Screens/WallWalkScreen.swift`). After "It turns a corner", continue the walk onto the next
wall, which C1's wall chain already supports.

The blockers B-03 and B-09 stop a homeowner outright, but they occur on uncommon paths. The three
changes above affect every scan.
