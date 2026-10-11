# House Scan: what the homeowner experiences

A description of the iOS capture app from the outside: what the homeowner sees on each screen,
what they can do there, and exactly what happens when they do it. It is written from the app's
code and tests, then checked against the app running in the Simulator. Anything that looks
wrong is collected in [bug-triage.md](bug-triage.md).

## Scope decisions

- **Surface.** The HouseScan app, one run of the homeowner flow from onboarding to the result,
  default settings. The ARKit camera cannot run in the Simulator, so behavior that needs the live
  camera is described from code and marked unverified.
- **Source.** `t3/ios-mvf`. Footers and the last Simulator check are at `a39d0a5`; line
  references are at `beede15`. Between the two, the only change a homeowner can reach is that the
  strip's marks scale with text size and stay inside it; the upload refusal and meter-number
  states added to the contract are not wired to anything yet.
- **How it is run.** `make sim-app REF=<commit>` from `verification/` (on `t3/verification`, #13): the real ADVIO replay
  `advio-20-0040-0075` (C3), the autopilot, and the server started from `t3/server`.
- **Out of scope.** The `-uiDemo` screens (a design preview, not the product), TestFlight
  signing, the server's placement logic (checked by `hsverify/e2e.py`).
- **Not yet built.** Panel close-ups and homeowner questions other than "does this window open".
  They are not described.

## Shape of every document

The unit of interaction is **one screen of the scan** (one `ScanPhase`). Its five phases, the
subsections of "The interaction, event by event", are:

1. **Arriving:** what the screen shows first and what the engine starts.
2. **Leaving at once:** the ways off the screen before anything is captured or marked.
3. **First capture:** the first photo kept, tap accepted or answer given.
4. **While capturing:** what updates live and what the homeowner can still do.
5. **Advancing:** what is committed and which screen comes next.

**Variants** (rows of every Modifiers table, columns "at arrival" and "while capturing"):
live camera or replay; autopilot on or off; a server URL or the bundled sample result; larger
text sizes; Reduce Motion.

**Interrupts** (rows of every Cancel and interrupt table, in this order):
the homeowner's way out on the screen ("I can't get there", "Can't get a clear shot", Cancel);
Start over; tracking limited (moving fast, too little detail, too dark); tracking lost or
relocalizing, including the 20 s reset; the app backgrounded or a call arriving; camera access
off or the camera session failing; the network lost or the upload failing; the app killed.

**Cross-cutting concerns** (paragraphs of "Interactions with other systems", in this order):
coverage and evidence; stored photos; upload and offline; accessibility (VoiceOver, Dynamic
Type, Reduce Motion); haptics and motion; verification hooks (`STATE=` logs, replay, autopilot).

Each document then has Summary, The simple case, The interaction (with one
`stateDiagram-v2`), Modifiers, Cancel and interrupt, Interactions with other systems, Edge cases,
and Open questions and verification, ending with the commit it was verified against.
[screens/meter-close-up.md](screens/meter-close-up.md) is the pilot every other document copies.

## Writing rules

- Describe the experience, not the code. Technical detail goes in `> Technical note:` quotes,
  only where the mechanism changes what the homeowner would expect.
- Use the words in [glossary.md](glossary.md). Numbers and definitions live in the foundation
  documents; other documents link to them.
- Every table cell is filled, "No effect." where that is the answer.
- Behavior that looks like a bug is said plainly under "Open questions" with the file and line.

## Structure and coverage

| Document | What it covers | Status |
| --- | --- | --- |
| [foundations/flow.md](foundations/flow.md) | The screens in order, what moves between them, the launch options, Start over, failures | drafted; checked in the Simulator |
| [foundations/coverage-and-guidance.md](foundations/coverage-and-guidance.md) | The coverage strip, when a photo is kept, the one instruction and what overrides it | drafted; checked in the Simulator |
| [screens/onboarding.md](screens/onboarding.md) | The intro pages and the failure screens (camera off, unsupported phone, unreadable replay) | drafted; checked in the Simulator |
| [screens/find-meter.md](screens/find-meter.md) | Finding the meter and tapping it to set the wall | drafted; checked in the Simulator |
| [screens/meter-close-up.md](screens/meter-close-up.md) | The self-timed meter photo (pilot) | drafted; checked in the Simulator |
| [screens/wall-walk.md](screens/wall-walk.md) | Walking the wall, marking its ends, "I can't get there" | drafted; checked in the Simulator |
| [screens/mark-features.md](screens/mark-features.md) | Marking gas meters, doors, windows, AC units, driveways and fences; the window question | drafted; checked in the Simulator |
| [screens/gap-request.md](screens/gap-request.md) | One targeted extra view, from the phone or the server | drafted; checked in the Simulator |
| [screens/uploading.md](screens/uploading.md) | Sending the scan and waiting for the result, offline and failure | drafted; checked in the Simulator |
| [screens/result.md](screens/result.md) | The result, its checks and the 3D and AR views | drafted; checked in the Simulator |
| [verification.md](verification.md) | Checks against the running app and their results | current pass at `a39d0a5` |
| [bug-triage.md](bug-triage.md) | Every suspected defect, deduplicated | 16 entries, 3 resolved (B-01, B-05, B-15) |
