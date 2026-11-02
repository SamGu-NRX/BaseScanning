# Corrections to the m4-real receipts

The files in this directory are kept byte-for-byte as committed. This note records what each one actually observed, because the `result: PASS` lines claim more than the `observed:` lines show. Nothing here re-runs those cases. The replacement runs, at a later head and against a reworked page, are in [`../../evidence/2026-10-12/`](../../evidence/2026-10-12/).

## Source identity

- The receipts were committed twice with the same results: first in `48503111`..`9482fdd0`, then again in `b0d3322b`..`ba0a5d5d`. The second set changed only each file's `commit:` line, to the hash of the commit before it, and its `(clean)` or `(dirty)` label.
- Neither label identifies the source the browser actually served. A receipt cannot name the commit that contains it, the labels were rewritten without a re-run, and no receipt records a hash of the served `index.html`, `serve.py` or pack. Treat the served source as unknown.
- `b-copy-flow.webm` has different bytes in the two sets (SHA-256 `7d250806...` at `9482fdd0`, `c2736815...` at `ba0a5d5d`) at the same size, with no recorded reason.

## Per-case disposition

| Receipt | Recorded | What the file shows | Disposition |
|---|---|---|---|
| a1-clip-click-native | PASS | Empty copy feedback; `readText` rejected with `NotAllowedError` | Click dispatched. No feedback appeared, and the readback was denied, so nothing shows that the command was copied. Not a copy pass. |
| a2-clip-key-native | PASS | Focus set by script, Enter pressed, feedback `Copied.`, readback denied | Feedback appeared. The clipboard contents, and whether the native API or the fallback produced them, are unverified (readback BLOCKED). Script focus does not show the button is reachable with Tab. |
| a3-clip-click-simulated-denied | PASS | Stub `writeText` that throws synchronously, `execCommand` stubbed to `true`, feedback `Copied.` | The page's handler at that time caught promise rejections only; a synchronous throw escaped it. The receipt does not show the fallback was called, and the stub of `execCommand` returns `true` without copying. Its screenshot is byte-identical to a2's (SHA-256 `093cfc67e90e...`), so it cannot show a distinct state. Invalid as a fallback test. |
| a4-clip-click-fallback-absent | PASS | `Navigator.prototype.clipboard` deleted, feedback `Copied.` | Simulated. Ran in the same browser session as a3, whose `execCommand` stub may still have been installed, so `Copied.` may come from the stub. Unverified. |
| b-pending-* (2) | PASS | Copy feedback empty right after load | The assertion was about the pending fetch state, but the observation is the copy feedback, which is empty in every state. The pending read state was not observed. |
| b-settled-* (6) | PASS | Copy feedback empty after a 5 s sleep | Same mismatch: no read state, rendered failure text or server response was recorded. `b-settled-plain`, `b-settled-delay-data`, `b-settled-delay-refusals` and `b-recording-initial` share one image (SHA-256 `1baa47d22f34...`). |
| b-recording-copy-flow | PASS | Feedback empty; `b-copy-flow.webm` is 0.1 s long with one frame | The recording shows no interaction and no feedback. Not evidence of the copy control. |
| c-axe-* (6) | PASS | Six axe runs, 0 violations each | `axe_version` holds the run options, not a version. Four runs (`plain`, `delay-data`, `delay-refusals`, `fault-data`) report `color-contrast` incomplete on 2 nodes with no recorded disposition. Zero violations stands; the incompletes were never resolved. |
| d-mobile-* (4) | PASS | Copy feedback empty; screenshot at 390x844 | No overflow measurement or read state was recorded. The screenshots exist; the assertion "renders the scenario correctly" was not checked. |
| d-mobile-keyboard-focus | PASS | `#copy-command.focus()` by script | Script focus does not show Tab reachability or `:focus-visible` styling. |

## What replaced them

The 2026-10-12 run records, per case, the served file hashes, server arguments, viewport, actions, read-state timeline, rendered text and an outcome for each assertion. A denied readback is recorded as BLOCKED, never as a pass. Simulated cases install their stubs in a fresh browser context per case, before the page's script runs, and assert on the stub's recorded calls.
