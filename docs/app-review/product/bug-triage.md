# Suspected defects

Collected from every document's open questions and from [verification.md](verification.md),
deduplicated. Status checked at `t3/ios-mvf` `a39d0a5`; line references are at `0876e03` unless an
entry names another commit; paths are under `ios/`. **Seen** means observed in the Simulator; **code** means read from the
source and not run. Every blocker and major entry was re-read in the source by a second reader.

| ID | Severity | Where the homeowner meets it | Evidence | Decision |
| --- | --- | --- | --- | --- |
| B-03 | Blocker | Camera denied or camera failure (find-meter, code only), unreadable replay (onboarding, seen): no message, no way on | seen (replay, on onboarding); camera cases code only | fix |
| B-04 | Blocker | Meter close-up: "Can't get a clear shot" may never appear | code | fix |
| B-09 | Blocker | Feature list: "Add something" does nothing | code | fix |
| B-11 | Blocker | Meter tap can land on another wall's extended plane (ground height fixed at `beede15`) | code | fix |
| B-06 | Major | Walk: an end closer than 20 ft can't be marked (answer half fixed at `525ea40`) | code | fix |
| B-08 | Major | "Can't get there" is never sent to the server | code | fix (with S2) |
| B-10 | Major | Feature list: the scan resets after 20 s of relocalizing (result and upload fixed at `beede15`) | code | fix |
| B-12 | Major | A server request beyond a still-marked wall end can't finish (past-end case fixed at `beede15`) | code | fix |
| B-02 | Major | Upload and camera failures show raw error text | seen at `21a63e7` | fix |
| B-13 | Minor | With no spot, the 3D model's tint zones stop one battery width short (fixed with a spot at `beede15`) | code | fix |
| B-14 | Minor | Result rule lines hide whether a limit is a minimum or maximum | seen | fix |
| B-16 | Minor | Wording and VoiceOver: "Tap the ac unit"; "ft" and "in" read as letters | seen in part | fix |
| B-01 | Resolved | Every upload failed with 404 | seen fixed at `0876e03` | none |
| B-05 | Resolved | Camera-screen text missing at `21a63e7` | seen fixed | none |
| B-15 | Resolved | AR view did not say a sample result is a sample | seen fixed at `525ea40` | none |
| B-07 | Minor | Scan photos are never deleted from the phone | code | product call |

## Blockers

**B-03. Failures other than an unsupported phone never reach the screen.** Only the failure screen
shows a failure, and the app switches to it only for an unsupported phone
(`HouseScan/Runtime/ScanEngine.swift:91-93`). Two cases, with different evidence:

- **Unreadable replay (seen).** With `-replay /nonexistent` the app logged "replay unreadable:
  cannot read /nonexistent/session.json" and stayed on onboarding, with no message and no way on
  (report `sim/20260926-044618-5520229-5520229d-preview-bad-replay`).
- **Camera denied, failed session (code, not run).** The source sets the failure without changing
  screen (lines 105, 379, 381), so the homeowner stays on "Find your electric meter" with no
  camera and taps do nothing; "Open Settings" is unreachable. Read from source only; never run.

**B-04. The close-up's way out can fail to appear.** A failed try counts only when one problem
lasts 4 s with no good frame in between (`HouseScanKit/.../Capture/CloseUpGate.swift`, `evaluate`),
and the photo needs 0.6 s of good frames in a row. Frames that alternate between good and blurry
satisfy neither, so "Can't get a clear shot" never appears. S3's full-flow test cannot catch it:
its autopilot calls skip directly. Suggested: count a failed try per 4 s without a photo.

**B-09. "Add something" on the feature list does nothing.** The six chips start a mark
(`HouseScan/UI/Screens/MarkFeaturesScreen.swift:64`), but the list never shows marking: the circle,
prompt and Mark button exist only on the walk (`WallWalkScreen.swift:28-45`). The homeowner taps a
chip and nothing visible happens; "Looks complete" then uploads without it. Adding a missed
feature is what this screen is for.

**B-11. The meter tap can guess a depth.** A blocker under the review's I8 ([checklist](../ux/checklist.md)): anchoring never guesses a depth. At `beede15` the ground height comes from planes as
they arrive; the infinite-plane fallback remains (`LiveCapture.swift:76`). Before: The raycast falls back to `existingPlaneInfinite`
(`HouseScan/Runtime/LiveCapture.swift:76`): a tap at a meter whose wall is not detected yet can pin
it on another wall's plane extended past its edge. The code's own comment says a tap never guesses a
depth. The ground height also stays a guess (1.4 m below the phone) if no ground plane was seen at
the tap (`ScanEngine+Actions.swift:48-57`). `t3/ios-mvf` `ee1087e` no longer falls back to the infinite plane (read from source, not run).

## Major

**B-06. Wall ends.** Half fixed at `525ea40`: after "Wall ends here" the walk asks "What's at the
left end?", and only "something blocks it" records a real end; a corner or no answer stays
unexplored. Still open: "Wall ends here" is offered only while the walk asks for the end, which
happens at 6.1 m of coverage (`HouseScanKit/.../Guidance/GuidancePlanner.swift`, `preferredTask`),
so a wall that ends at 10 ft can only be recorded through "Can't get there". Suggested: offer
"Wall ends here" whenever the wall's end is in view.

**B-08. "Can't get there" is never sent.** The upload carries only covered stretches and each end's
kind (`HouseScan/Runtime/ScanEngine+Export.swift:48-58`, `HouseScanKit/.../Scene/SceneExport.swift`),
so a stretch the homeowner can't reach looks to the server like one nobody looked at. The gap
screen's hint promises "An installer will look at this part instead"; the server may ask for the
same view again. C1 has no field for it, so this needs S2 and S3 together.

**B-10. The scan resets after a lost world frame.** At `beede15` the reset spares the upload,
result and AR screens; it still fires on the feature list, which shows no coaching. Before: The 20 s relocalization reset runs on every screen while
the camera session runs (`ScanEngine.swift:189, 357-364`), including the feature list, the upload
and the result, where no coaching is shown. After a call, the homeowner can come back to "Find your
electric meter" with every photo, mark and the result gone. The reset also keeps a half-finished
mark and the first close-up (`ScanEngine.swift:386-410`, `KeyframeStore.swift:79`).

**B-12. Requests past a wall end.** At `beede15` marking the end again settles a past-end
request, but only the walk offers "Wall ends here"; the gap request screen shows neither it nor
the end question (`WallWalkScreen.swift:137-173`), so a homeowner can't use the fix. The
band-request case below is unchanged. Before: "Capture it now" on a past-end request first clears that end
(`ScanEngine+Actions.swift:206-209`) and nothing sets it again, so the next upload reports the side
as unexplored even after the homeowner looked past it, and the server can ask again. A server band
request whose stretch lies beyond an end that is still marked can never fill: the coverage map
records no cells past a marked end (`HouseScanKit/.../Coverage/CoverageMap.swift:113-118`), so its
bar stays at 0% and "I can't get there" is the only way out.

**B-02. Failures show raw error text.** "That didn't go through" is followed by the error's
description, for example `The server answered 404: {"error":{"code":"not_found",...}}` (seen at
`21a63e7`; same code at `5520229`: `UI/Copy/ScanCopy.swift:177`, `ScanEngine.swift:533, 560`). The
camera-failure text does the same (`ScanCopy.swift:240`) once B-03 is fixed.

## Minor

- **B-13.** At `beede15` each tint zone reaches one battery width past its last start, using the
  chosen spot's width (`ScanEngine+Export.swift:132-141`), so only a result with no spot is still
  affected: its 3D model tints the sweep's range of battery *start* positions as if it were where
  the battery stands, and each zone ends one battery width short. The AR view needs a spot, so
  only the autopilot opens it without one. Before: every result
  (`ScanEngine+Export.swift:124`).
- **B-14.** Seen on the sample result: "Measured 3 ft. The rule is 20 ft, and the measurement can be
  off by about 4 in." A maximum reads like a minimum; the server's `comparison` and
  `review_threshold_ft` are not used (`UI/Copy/ScanCopy.swift:210-218`).
- **B-16.** The AC unit prompt reads "Tap the ac unit", and VoiceOver hears "Mark ac unit" and
  "Remove ac unit" (`ScanCopy.swift:122`, `WallWalkScreen.swift:258`, `MarkFeaturesScreen.swift:121`;
  "Add ac unit" seen in the accessibility audit).
  Distances are spoken as "ft" and "in" in the 3D description and measurement lines, though
  `Distance.spoken` exists (`ResultScene3D.swift:296-321`, `ResultScreen.swift:237`).
- **B-07.** Each scan's photos stay in `Caches/Scans/<id>/` after upload, Start over or relaunch;
  `discardKeyframes` forgets the list but not the files (`KeyframeStore.swift:79`).
- **B-01 (resolved).** Until `0876e03` the app posted to `/v1/scenes`, which the server does not
  serve, and every upload failed with 404. At `0876e03` it posts to `/v1/placements`: seen `POST
  /v1/placements 200` and a result (report `sim/20260926-050058-t3-ios-mvf-0876e03c-replay`).
- **B-15 (resolved).** At `525ea40` the AR view on a sample result says "Example spot, not your
  result" and "No server checked this scan.", and the upload screen says "Making a sample result".
- **B-05 (resolved).** At `21a63e7` the camera screens' cards rendered without text; at `5520229` the
  same capture shows every word. The UI lane added the missing colour assets.

Smaller notes stay in each document's open questions: refusal wording on the meter tap, "Wall ends
here" aiming at a circle that is not drawn, the kind picker sliding under Reduce Motion, two taps on
"Can't get there" finishing an empty walk, and the result appearing more than 1.2 s after the app
logs it on first load.
