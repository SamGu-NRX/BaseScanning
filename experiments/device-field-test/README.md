# Device field test

## Question

Does the House Scan TestFlight build produce a scan the server can place a battery on, from one walk
along a real wall with a real iPhone? And which behaviours that the Simulator can't check hold on a device?
Those are the tracking, the live meter tap, the close-up, haptics, the AR overlay and interruptions.

## Method

[RUNBOOK.md](RUNBOOK.md) lists every device-only claim from the product description on `t3/verification`,
in the order a person meets them, each with a test ID. The tester screen-records each run with the
microphone on and says the test IDs and haptics aloud. Before scanning, they tape the wall's features
relative to the meter. After the run:
- the exported scan is sent to the server again, which returns the same result the phone got;
- [`coverage_replay.py`](coverage_replay.py) replays its photos through the app's coverage rules.

[setup-plan.html](setup-plan.html) is the plan for the first site, a mock house wall with a meter, panel,
window, eave and an installed battery (rendered in [setup-plan.png](setup-plan.png)).

## Pass criteria

The first and last come from the runbook's goals before run 1. The 4 in bound is Measure Lab's, applied after the run.

- The uploaded scan covers the stretch of wall the tester walked, and the result names either a spot or a
  reason that refers to something on that wall.
- Measured features are within 4 in of the tape along the wall and in height, as in Measure Lab's criteria.
- Every device-only claim in the runbook is recorded as pass, fail or not reached.

## Result, run 1

Run on 2026-09-26 with TestFlight build 1.1 (workflow run
[36255760659](https://github.com/SamGu-NRX/house-scanning-master/actions/runs/36255760659), branch `beta` at
[`28cd898`](https://github.com/SamGu-NRX/house-scanning-master/commit/28cd8988187979800f74bde78ee1b1fc246d0ec3),
capture code `t3/ios-mvf` at
[`657ab28`](https://github.com/SamGu-NRX/house-scanning-master/tree/657ab283092f1a34cc2dff87d48a0871d284f9c4/ios)).
The 1 min 54 s screen recording was reviewed frame by frame, and its narration was transcribed on the
tester's laptop. It shows the same run as the scan: the share sheet reports `scan.zip` at 27.3 MB, and the
result text is the same.

**The first criterion fails.**
- **What the phone captured.** It kept 44 photos and a meter close-up. It walked about 19 ft right of the
  meter and back, 5 to 7½ ft from the wall.
- **What the scan says.** Both wall ends sit within 4 in of the meter: the exported wall baseline is
  0.66 ft long. The scan leaves out everything past its ends, so it tells the server that no wall and no
  ground were observed.
- **What the server answered.** `manual_review` with no spot, asking the homeowner to "keep walking past
  the left end of the scan (0 ft 4 in left of the meter)", and the same for the right.

### What the recording shows

"Can't get there" was tapped twice, and each tap set one end at the meter:

- **00:22, left.** The card said "Walk slowly to your left". The tester stood about a metre from the meter
  and hadn't walked or tilted down. The strip showed the wall row amber (seen once) either side of the
  meter and the ground row grey. The walk switched to "Walk slowly to your right".
- **00:28–00:44, tilt prompts.** "Tilt down to show the ground" asked for the strip 3 ft 3 in, 2 ft 9 in,
  4 ft 6 in, 7 ft and 8 ft 9 in right of the meter. None of them asked for the ground in front of the meter.
- **00:53, right.** The tester stood at the right corner, about 16 ft from the meter. Just before the tap,
  the strip's wall row was green (covered) for the first stretch from the meter and amber after it. The
  ground row was amber all the way from the meter, never green. Right after the tap the card said "That's
  the whole wall", and the strip went blank for the rest of the run.

After that, the tester marked the AC stand-in (listed at "About 16 ft right"), the window ("About 7 ft
right", answered "It stays shut") and the gas meter ("Around your meter"). The upload took 2.9 s. The result
was "An installer will take a look", with the two "Keep walking past the … end of the scan (0 ft 4 in …)"
requests and no checks. The AR view is offered only with a spot, so it wasn't reached.

The 20 ft "Is this the … end of the wall?" prompt never appeared.

### How the walk was lost

"Can't get there" during the walk sets that side's end at `GuidancePlanner.reach`
([`ScanEngine+Actions.swift`](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScan/Runtime/ScanEngine%2BActions.swift#L315-L319)).
`reach`
([`GuidancePlanner.swift`](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScanKit/Sources/HouseScanKit/Guidance/GuidancePlanner.swift#L101-L117))
counts 6-inch cells outward from the meter. It stops at the first cell whose wall and ground are not both
covered or explicitly skipped. The replay ignores skipped cells and depth, so it cannot establish the
app's reach in every state. A cell is covered when each of its three sample rows has been seen from two
positions: the wall at 0, 3.25 and 6.5 ft up, and the ground at 0, 2 and 4 ft out. So if the ground right in front of the
meter hasn't been seen twice, the tap puts the end at the meter.

After that, the coverage map ignores everything past the ends:
- `record` stops adding sightings outside them
  ([`CoverageMap.swift`](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScanKit/Sources/HouseScanKit/Coverage/CoverageMap.swift#L332));
- `coveredIntervals` and `groundDepthSpans` leave them out
  ([L979](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScanKit/Sources/HouseScanKit/Coverage/CoverageMap.swift#L979),
  [L527](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScanKit/Sources/HouseScanKit/Coverage/CoverageMap.swift#L527)).

The walk still kept photos and accepted marks up to 19 ft away, and nothing on screen said they would be
dropped. The export then clamps the baseline to ±0.1 m around the meter
([`ScanEngine+Export.swift`](https://github.com/SamGu-NRX/house-scanning-master/blob/ff95f1cb571e2f3ef56d9faae85880665ad0ca30/ios/HouseScan/Runtime/ScanEngine%2BExport.swift#L84-L85)),
which is the ±0.328 ft the server echoes. This code is unchanged between the build's `657ab28` and the
branch head `ff95f1c`.

The replay shows how long the scan was exposed to this. Replaying the 44 photos in order through the
simplified no-depth, no-skip coverage rules, with no ends marked, the covered stretch next to the meter
stays at 0 ft on both sides until photo 43:

```
photo   x (ft)  reach left  reach right
k00001     0.38       0.0 ft        0.0 ft
...
k00030    19.69       0.0 ft        0.0 ft
...
k00042     6.02       0.0 ft        0.0 ft
k00043     5.54       1.0 ft        5.5 ft
```

At the meter, the wall rows were seen from the start. The ground 2 ft and 4 ft out was seen only by photos
41–43, taken after both ends were set, while the window and gas meter were being marked. So during the
walk, any "Can't get there" put that side's end at the meter. That matches the recording: the ground row by
the meter was amber at the right-hand tap. The runbook told the tester to use it at the left corner, only
17 in from the meter, and at the right corner if the end prompt never came.

The replay also shows why the end prompt couldn't come:
- the ground 4 ft out was never seen twice past 5.5 ft right of the meter;
- the wall's top row, 6.5 ft up, was never seen twice past 10.5 ft.

The prompt appears only after 20 ft of unbroken coverage.

### Measurements

These are the app's marks against the tape, both taken from the meter's centre line. Positive means the
app has it farther right or higher.

| Feature | App − tape | Within 4 in |
|---|---|---|
| Window, right edge | −0.5 in | yes |
| Window, left edge | +5.4 in | no |
| Window, width | −5.9 in | no |
| Window, sill height | +1.9 in | yes |
| Gas meter stand-in, centre | 4.8 in nearer the meter | no |

The window's width error comes almost entirely from its left edge. The gas meter is a one-tap mark with a
nominal 1 ft width.

### Device-only claims

The tester read screen text aloud but didn't say test IDs or haptics, so haptic claims remain unverified.
These were visible:

| ID | Result | Evidence |
|---|---|---|
| O-1 | Not reached | The camera opened with no iOS prompt; permission was granted in an earlier run |
| F-2 | Pass | "Step a little closer to the wall" after an early tap |
| C-1 | Partially verified, haptic unverified | The ring filled in under a second; the meter-number question followed |
| W-1 | Pass | Left, right, "Tilt down…" and "Take a step back" all appeared; the fog cleared after about 3 s |
| W-2 | Pass | The photo counter flashes |
| W-5 | Fail, B-16 | "Tap the ac unit" |
| W-7 | Not reached | The end prompt never appeared; see above |
| W-12 | Fail | Reproduced without a double tap: two ordinary taps on "Can't get there" gave "That's the whole wall" |
| U-1 | Measured | 2.9 s from "Looks complete" to the result |
| U-2 | Fail, known | About 2.2 s on "Sending measurements, 99%"; "Check clearances" never became active |
| R-1 to R-5 | Not reached | No spot, so no checks and no AR view |

Not tried: W-4 (a refused mark), and runs 2 and 3.

Other things the recording showed:
- **Marks outside the ends still count.** The AC stand-in, 16 ft past the right end, is still listed and
  exported.
- **"Slow down" while standing still.** It appeared twice while the tester stood still to mark features.
- **No way to mark a battery.** The site's installed battery couldn't be marked, because the mark sheet
  has no battery kind.
- **A tilt-prompt distance went backwards**, from 3 ft 3 in to 2 ft 9 in.

## Result, run 2 (build 3.1)

Run on 2026-09-26 with TestFlight build 3.1 (workflow run
[36272848774](https://github.com/SamGu-NRX/house-scanning-master/actions/runs/36272848774), branch `beta` at
[`ad91620`](https://github.com/SamGu-NRX/house-scanning-master/commit/ad91620f41686d18f8699fbc1040d2a754719163),
capture code `t3/ios-mvf` at `e9e405d`, server at `e106acb`). The 4 min 28 s screen recording, run A only, was reviewed
at 1 fps and at 5 fps around each tap. Its narration was transcribed on the tester's laptop. The exported scan was
lost before it was copied off the phone, so:
- the packet wasn't validated;
- the coverage wasn't replayed;
- the scene wasn't sent to the server again.

The numbers below come from the screen and from the code. This repeats the runbook's run 1 (full flow) on the new
build; the runbook's runs 2 and 3 are still to do (#32).

**The first criterion fails again, but the run reached a spot for the first time.**
- **What the phone did.** It walked about 17 ft right of the meter to the end of the mock wall. The photo counter
  reached 136 on the walk and 145 after one server request.
- **What the scan said.** The wall ran from 7 ft 6 in left of the meter to 0 ft 6 in right. The window and the AC
  stand-in lay outside it.
- **What the server answered.** `manual_review`, "An installer will take a look / 1 ft 3 in left of your meter, 1 ft of
  cable", with 10 checks UNSURE.

### What the recording shows

"Can't get there" still sets the end at the meter:
- **00:53, left.** The ground cell just left of the meter was still amber (seen once). The left end landed at the
  meter.
- **02:38.** "Can't get there" on "Tilt up to show more wall / Around 16 ft 9 in right of your meter" hatched that
  stretch "Skipped". This is intended.
- **02:40, right.** At the end of the wall ("can't get there because I'm at this wall"), only one 6-inch cell next to
  the meter was covered. The right end landed at 0 ft 6 in. The strip went blank for the rest of the walk (#24).

The walk then kept asking for stretches past that end: "Tilt up to show more wall / Around 7 ft right of your meter"
and "Tilt down to show the ground / The strip along the wall, 5 ft 3 in right of your meter". None of them could fill.
After 57 s of "I don't know where it wants me to point", the tester tapped "Done with this wall" (#38).

The first upload took about 0.7 s and returned "2 more views to finish".
- **The first view.** "Show the ground around your meter / Keep walking past the left end of the scan (0 ft 4 in left of
  the meter)". Its bar stayed at 0%.
- **03:56.** "I can't get there" uploaded again, and the result came about 1.2 s later. The second view was never
  shown (#39).
- **The ends had moved.** The result now asked to "Keep walking past the left end of the scan (7 ft 6 in left of the
  meter)" and "…the right end of the scan (0 ft 6 in right of the meter)" (#35).

The spot's footprint runs from 2 ft 7 in left of the meter to the meter itself. Its check line read "Clear of the
meter's working space … Measured 1 ft 3 in. The rule is 0 in, and the measurement can be off by about 1 ft 6 in." That
1 ft 3 in is an **overlap** with the 2 ft 6 in by 3 ft working space; the app drops the minus sign (#40). The overlap is
UNSURE rather than FAIL only because the meter's error, 0.79 ft, plus the wall's, 0.71 ft, exceeds it (#44).

The AR view, "The spot an installer will check", stayed in one place relative to the meter for the 11 s the tester
walked around. But:
- it was drawn over a person standing at the wall (#41);
- it was drawn through the post at the wall's end;
- its ground tint ran past the end of the wall.

The tester called it "very not correct".

### How the walk was lost, and how the wall grew

The walk-end code is unchanged since run 1:
- "Can't get there" sets the end at `GuidancePlanner.reach`
  ([`ScanEngine+Actions.swift` L354–359](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScan/Runtime/ScanEngine%2BActions.swift#L354-L359)).
- `reach` stops at the first cell next to the meter that isn't covered
  ([`GuidancePlanner.swift` L102–118](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScanKit/Sources/HouseScanKit/Guidance/GuidancePlanner.swift#L102-L118)).

New in this run:
- **The past-end request cleared the left end.** `beginServerGap` clears the end it asks past
  ([`ScanEngine.swift` L1400–1411](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScan/Runtime/ScanEngine.swift#L1400-L1411)).
- **"I can't get there" didn't set it again.** `skipCurrentGap`
  ([L1290–1302](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScan/Runtime/ScanEngine.swift#L1290-L1302))
  marks the requested cells skipped on the phone and stops further requests. It never restores the end.
- **The export then ran to the farthest cell seen from one position.** With no left end, `exportSpan` uses
  `seenExtent`
  ([`ScanEngine+Export.swift` L128–133](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScan/Runtime/ScanEngine%2BExport.swift#L128-L133)).
  That was 15 cells, 7 ft 6 in.
- **Guidance ignores the ends.** It asks for stretches past an end because `laggingBand` and `hiddenNearCamera` read
  coverage around the camera without checking them
  ([`GuidancePlanner.swift` L143–166](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/ios/HouseScanKit/Sources/HouseScanKit/Guidance/GuidancePlanner.swift#L143-L166)).
  Cells past an end never count as covered and can't be skipped.

The server picked the spot because it ranks UNSURE candidates by the number of UNSURE checks, then cable length, then
position
([`solver.py` L1166–1167](https://github.com/SamGu-NRX/house-scanning-master/blob/ad91620f41686d18f8699fbc1040d2a754719163/server/solver.py#L1166-L1167)).
A 0 ft route that touches the meter wins ties (#44).

None of this changes in `b7f51ba` or `49a676e` on `t3/ios-mvf`.

### Measurements

No tape distances were read aloud, so there is no tape comparison. The app's positions, from the meter's centre line:

| Feature | App |
|---|---|
| AC stand-in | "About 16 ft right" (centre about 16.0 ft from the server's numbers) |
| Window | "About 7 ft right", near edge about 5.9 ft right |
| Spot | From 2 ft 7 in left to 0 ft |

### Device-only claims

The tester said the build and run, and "no haptic" once, but no test IDs. The other haptic claims remain unverified.

| ID | Result | Evidence |
|---|---|---|
| O-1 | Not reached | No camera prompt; permission was granted earlier. A new Motion & Fitness prompt appeared on "Find your electric meter" (#43) |
| F-2 | Unclear | Early taps were narrated, but no "Step a little closer to the wall" appeared |
| C-1 | Partially verified, haptic unverified | The ring filled in about a second; "Meter number saved" followed |
| W-1 | Pass, confusing | All instructions appeared. After the right end was set, "Around 7 ft right" requests couldn't be met |
| W-2 | Pass | The counter flashes; "there is no haptic" |
| W-5 | Fail, B-16 | "Tap the ac unit" (#25) |
| W-7 | Not reached | The wall ends before 20 ft |
| W-8 | Fail, B-06 | The wall ended at about 17 ft and only "Can't get there" was offered (#29) |
| G-2 | Pass, copy bug | "I can't get there" uploaded at once, but "2 more views to finish" showed only one (#39) |
| U-1 | Measured | 0.7 s for the first upload, 1.2 s after the gap request |
| U-2 | Fail, known | The final upload stayed on "Sending measurements, 99%"; "Check clearances" never ticked (#31) |
| R-1 | Partially verified, haptic unverified | First result with a spot: "An installer will take a look", 13 check lines |
| R-3 | Stayed put, wrong spot | Steady relative to the meter; drawn through the post at the wall's end |
| R-4 | Fail, known | The battery is drawn over a person (#41) |

Not tried: W-4, W-9 in the dark, M-2, M-3, R-2, R-5, and runs 2 and 3. The 3.1 fixes for tracking loss on the feature
list and during gap requests are not yet checked on a device.

Other things the recording showed:
- **Marks outside the ends still count.** The window and AC are listed and exported, and the server measures them on
  the wall's straight extension (#42).
- **"Slow down" during tilts and while standing still,** seven times. The gate fires above 60°/s of rotation as well
  as 1.5 m/s (#26).
- **No way to mark a battery** or other equipment on the wall (#27).
- **The tilt-prompt distance went backwards again,** from 5 ft 3 in to 4 ft 9 in, 4 ft 6 in and then 4 ft, never in
  front of the meter (#28).

## What this changes

- **For the capture app (lane A owner's call):** "Can't get there" shouldn't be able to place an end
  inside a stretch the tester has walked and photographed. Possible fixes:
  - place the end at the farthest point seen, or at the phone's position along the wall, instead of at
    the unbroken covered reach;
  - refuse the tap and say what is missing ("tilt down to show the ground by your meter");
  - at least warn when an end lands within a foot of the meter.

  Separately, the walk shouldn't keep photos and marks it will drop without saying so.
- **For guidance:** the tilt prompts asked for the ground from 2 ft 9 in right of the meter outward, never in
  front of the meter. That's the stretch `reach` needs first. Asking for it before "Walk slowly to your left"
  would unblock `reach` early. The strip could also show that an end will land at the meter before the tap.
- **For the next runs:** the runbook now says to fill the strip next to the meter before any "Can't get
  there". Testers should say test IDs and haptics aloud, and share the scan off the phone before closing the app: a
  new-store cleanup on relaunch keeps the two newest completed scans with valid bundles, but can delete
  older completed scans and unfinished scans without a bundle after pending bundle writes finish. Partial
  or unreadable bundles are left alone. Runs 2 and 3 are still to do.

Raw material stays out of git: the recording, the scan and the tape readings.
