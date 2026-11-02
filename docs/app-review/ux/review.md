# UX review of every app state

Each state of the HouseScan app judged against [checklist.md](checklist.md). Screenshots and
audit reports stay in `~/house-scanning-data/reports/` (replay frames are non-commercial data).

## Evidence

| Run | What it shows |
| --- | --- |
| `sim/20260926-071459-t3-ios-mvf-a39d0a50-replay` | Every state at `a39d0a5`, the current head, with the real server's result; the gap request appears and is answered "I can't get there" |
| `a11y/20260926-071248-t3-ios-mvf-a39d0a50-head-sample` | Apple's audit at `a39d0a5` on 21 screens: 10 issues, down from 32 at `525ea40` |
| `sim/20260926-051323-t3-ios-mvf-24af4342-replay` | Every state at `t3/ios-mvf` `24af434` with the real server's result |
| `sim/20260926-052535-t3-ios-mvf-525ea401-replay` | Every state at `525ea40` with the real server's result and the end question answered |
| `a11y/20260926-052756-t3-ios-mvf-525ea401-head-sample` | Apple's audit at `525ea40` on 21 screens, including the end question and the sample wording |
| `a11y/20260926-051535-t3-ios-mvf-24af4342-head-sample` | Apple's audit at `24af434` on 17 screens; the gap loop closes on the replay |
| `sim/20260926-050058-t3-ios-mvf-0876e03c-replay` | Every state at `0876e03` |
| `sim/20260926-043152-5520229-5520229d-preview-sample` | Every state of the flow, default text size, light appearance, sample result |
| `sim/20260926-044328-5520229-5520229d-preview-ax5-dark` | The same at the largest accessibility text size (AX5), dark appearance |
| `sim/20260926-044527-5520229-5520229d-preview-unsupported` | The unsupported-phone screen |
| `a11y/20260926-044005-5520229-5520229d-preview-sample` | Apple's accessibility audit on 17 distinct screens, with every label |
| Source at `0876e03` | Items that need the live camera |

`5520229` is `t3/ios-mvf` `90c01dd` merged with the UI lane `e5dde35`; `0876e03` is the same UI
merged by S3, plus the fixes listed under "Fixed since the preview". The Simulator cannot run
ARKit, so tracking loss, relocalization and the live meter tap are judged from source.

## Verdict: Block

Blockers that fail: R9 (camera access denied leads nowhere, [B-03](../product/bug-triage.md#blockers)),
T5 (the close-up's way out can fail to appear, B-04) and, from source, I8 (at `a39d0a5` the meter
tap can land on another wall's plane extended past its edge, B-11). R1 is Not verified: the
checklist judges it on the final visible strip before the upload or finish state, and B-08, the
skipped stretch that is never sent to the server, does not by itself establish that this strip
ends with a silent gap. The feature list's "Add something" doing nothing
(B-09) blocks adding a missed feature. I9, the panel close-up's safety wording, is Not verified
because panel close-ups are not built; with blockers failing, the verdict stays Block.

## By state

| State | Result | Findings |
| --- | --- | --- |
| `onboarding` | Pass at default size: one message, one primary button, "About 2 min". At AX5 the pill reads "About 2 mi" and the body runs under the page dots. | UX-01 |
| `unsupported` | Pass: "This phone can't measure walls" with what to do instead. Camera denied never reaches this screen. | B-03 |
| `findMeter` | Fails I8 from source: with the meter's wall not yet detected, the tap can land on another wall's plane extended past its edge. Otherwise one instruction, the reticle, "This is my meter". At AX5 the button is pushed partly off screen. Refusal wording ("Step a little closer to the wall") also covers tracking refusals. | UX-01, B-11 |
| `meterCloseUp` | Pass for the meter close-up: one instruction, no shutter, a named fix ("Center the meter in the circle"). The problem pill failed contrast in the earlier runs and passes at `a39d0a5` (UX-03); the way out can fail to appear. I9 Not verified: panel close-ups are not built. T6 Not verified: the sources document fixes for blur and glare but none for a cut-off label ([meter-close-up.md](../product/screens/meter-close-up.md)), and no run covers one. | UX-03, B-04 |
| `wallWalk` | One instruction with its reply inside the card, as the checklist asks. The strip's states differ by colour alone, the counter fails contrast, and at AX5 the card covers most of the camera and pushes "Mark something" and the strip off screen. At `525ea40` a marked end is followed by "What's at the left end?", so its kind is truthful, but an end closer than 20 ft still can't be marked. From source ([wall-walk.md](../product/screens/wall-walk.md)): guidance toward unobserved ground is the world-anchored ring and dots, not the text I7 asks for, and the target ring pulses while idle, which V4 rules out. A2 Not verified: the strip's spoken summary can read "100 percent seen" while nothing is covered and the ground is unseen, and that description alone does not settle whether tracking status is announced. | UX-01, UX-02, UX-03, UX-05, UX-08, B-06, I7 (from source), V4 (from source) |
| `markFeatures` | Pass at default size: clear list, remove buttons labelled, window question with two full-width answers. At AX5 the list is hidden behind "Looks complete". "Add something" does nothing. The run marked one window; I4's pass holds only for that case (see "Fixed since the preview"). | UX-01, B-09 |
| `gapRequest` | Pass: one line with the reason and target, a progress bar, "I can't get there". The requested stretch reuses the amber that means "seen". | UX-05, UX-06, B-08 |
| `uploading` | Pass: progress in its own steps, apart from coverage (R7); with no server it says "Making a sample result" (`525ea40`). Earlier runs showed four text elements that do not scale; the audit at `a39d0a5` reports one fixed-size font, the walk's "Mark something" (UX-04). A failure shows raw error text. | UX-04, B-02 |
| `result` | Pass on honesty: "An installer will take a look", the rules-not-final note, the unseen side disclosed, the borderline window check with measurement, rule and error, and "An installer will check this". A maximum reads like a minimum; the first load leaves the upload screen up for over 1.2 s. H5 Not verified: the cited runs end in manual-review and sample results, never a failed result, so what a failed spot then offers is unseen. | B-14, UX-07 |
| `resultAR` | Pass: the spot and cable over the camera with one headline and "Done". The headline says "The spot an installer will check" unless the result is an approved pass, and "Example spot, not your result" for a sample (seen at `525ea40`). A7 Not verified: not run with Reduce Motion on, and the [product description](../product/screens/result.md) reads from source that the AR battery then rises over 0.2 s, where A7 asks for a crossfade or cut. | A7 (from source) |

Checks that passed across the flow: one primary instruction per camera screen (I1); plain words,
no jargon in any on-screen string (I3); no shutter where capture is automatic (I5); every control
has a spoken name (A1, from the audit's labels); no hit area below 44 pt (A6, audit).

## Findings

**UX-01. Major. Large text breaks the camera screens and the feature list (A4).** At AX5:
"This is my meter" is pushed partly off screen; the feature list shows its heading and "Looks
complete" but not the marked features or the window question; on the walk and a gap request the
instruction card covers most of the camera, and "Mark something", "I can't get there" and the strip
fall off screen; onboarding's "About 2 min" truncates to "About 2 mi". Seen on the preview; the
only layout change at `0876e03` stacks the window answers, which makes the list taller. The audit flags clipped text
on the close-up and walk cards. Suggested: cap the instruction card's type at a large size with
the rest scrollable, keep the primary button pinned above the home indicator, and let the list
scroll behind a pinned "Looks complete".

**UX-02. Major. Coverage is shown by colour alone (A9).** Covered cells are green and seen cells
amber; in grayscale both are the same light gray, and the strip has no legend or row labels. A
homeowner with red-green colour blindness cannot tell done from not done. Suggested: a pattern or
height difference per state, and "Wall" and "Ground" row labels.

**UX-03. Minor. Contrast over the camera (A5).** At `a39d0a5` the audit still fails contrast on
the photo counter during the walk; the close-up's problem pill and the walk's "Can't get there"
reply, which failed at `24af434` and `525ea40`, now pass. Failures on text mid-transition (the
result headline and the sample note as they fade in) are left out.

**UX-04. Minor. Text that does not scale or clips (A4).** At `a39d0a5` one element's font size
cannot change ("Mark something" on the walk), down from ten at `525ea40`; the strip's marks now
scale. Text may clip at large sizes on the close-up, the mark prompt ("Now tap its top-right
corner") and the end question ("What's at the right end?" and its explanation).

**UX-05. Minor. Two names for one action (V8).** The walk's reply is "Can't get there"; the gap
request's is "I can't get there".

**UX-06. Minor. One colour, two meanings.** The requested stretch is outlined in the amber that
marks "seen but not enough" cells on the same strip.

**UX-07. Minor. The result appears late the first time.** On the first visit the upload screen
("Checking your wall") was still showing 1.2 s after the app logged the result; after returning
from AR the result appeared at once. The 3D model's first load is the likely cause.

**UX-08. Minor, not seen at `a39d0a5`. Text VoiceOver cannot read (A2).** At `525ea40` the audit
flagged text on the walk that is drawn rather than exposed to accessibility; the audit at
`a39d0a5` does not report it.

## Fixed since the preview (`0876e03`)

Seen, with one window marked: the window question answers "It opens" and "It stays shut", stacked
(I4). That one-window run does not establish I4 in general: from source, every window row adds its
own question ([mark-features.md](../product/screens/mark-features.md)), so a scan with several
windows shows one question per row at once, which I4's one-question rule rules out. The result
says the electrical panel still needs an electrician's review (H7). From source: tracking coaching says
"Slow down" and "Aim at a corner or somewhere with more texture" (T1). While relocalizing, the
saved meter photo shows with "Point at the meter like this." (T7). Overlays hide while tracking is
not normal (T3).
