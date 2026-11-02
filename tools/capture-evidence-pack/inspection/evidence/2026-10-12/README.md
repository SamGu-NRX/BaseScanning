# Browser evidence, 2026-10-12

Headless Chromium 156.0.8078.4 (Playwright 1.64.0) drove the real inspection server, started fresh for each case with the flags in that case's receipt. Each case ran in a new browser context. [`../../capture-browser-evidence.mjs`](../../capture-browser-evidence.mjs) is the harness; its SHA-256 at both runs was `f41dd584fa81...`.

| Run | Source | Files |
|---|---|---|
| `after/` | commit `64e381b1`, clean tree (no dirty paths recorded) | `receipts.json`, one PNG per screenshot, `recording-copy.webm` with an MP4 and GIF encoding of it |
| `before/example/` | `ba0a5d5` exported with `git archive` | `receipts.json`, PNGs |
| `before/refused-pack/` | the same export, with `inputs/bad-assertion.zip` copied over `evidence/example.zip`, because that server has no `--pack` flag | `receipts.json`, PNG |

Each `receipts.json` records the SHA-256 of the served `index.html`, `serve.py`, `read.py`, `example.zip` and `axe.min.js`. The before hashes equal the blobs at `ba0a5d5`; the after hashes equal the files at `64e381b1`. Every receipt lists the server arguments, viewport, browser actions, observed read-state timeline, rendered text and one outcome per assertion: PASS, FAIL, or BLOCKED when the browser refused the observation.

`inputs/bad-assertion.zip` (SHA-256 `e16a0663...`) is the example pack with `geometry_correctness.assertion` set to `"geometry verified correct"`, written with `zipfile`, not `pack.py`.

## Results

| Run | Case | Kind | Viewport | Outcome |
|---|---|---|---|---|
| after | `desktop-verified` | native | 1440x900 | 3 PASS |
| after | `mobile-verified` | native | 390x844 | 2 PASS |
| after | `desktop-refused-pack` | native | 1440x900 | 2 PASS |
| after | `fault-data` | native | 1440x900 | 3 PASS |
| after | `fault-refusals` | native | 1440x900 | 3 PASS |
| after | `fault-both` | native | 1440x900 | 3 PASS |
| after | `fault-data-malformed` | native | 1440x900 | 3 PASS |
| after | `fault-refusals-malformed` | native | 1440x900 | 3 PASS |
| after | `fault-data-incomplete` | native | 1440x900 | 3 PASS |
| after | `fault-refusals-incomplete` | native | 1440x900 | 3 PASS |
| after | `delay-data` | native | 1440x900 | 3 PASS |
| after | `delay-refusals` | native | 1440x900 | 3 PASS |
| after | `keyboard-desktop` | native | 1440x900 | 4 PASS |
| after | `keyboard-mobile` | native | 390x844 | 4 PASS |
| after | `native-copy-click` | native | 1440x900 | 3 PASS |
| after | `native-copy-enter` | native | 1440x900 | 4 PASS |
| after | `native-copy-space` | native | 1440x900 | 4 PASS |
| after | `native-copy-no-permission` | native | 1440x900 | 1 PASS, 1 BLOCKED (readback) |
| after | `sim-reject-exec-true` | simulated | 1440x900 | 5 PASS |
| after | `sim-reject-exec-false` | simulated | 1440x900 | 5 PASS |
| after | `sim-throw-exec-true` | simulated | 1440x900 | 5 PASS |
| after | `sim-absent-exec-true` | simulated | 1440x900 | 5 PASS |
| after | `sim-reject-exec-throws` | simulated | 1440x900 | 4 PASS |
| after | `sim-resolve` | simulated | 1440x900 | 4 PASS |
| after | `axe-desktop-verified` | native | 1440x900 | 4 PASS |
| after | `axe-mobile-verified` | native | 390x844 | 4 PASS |
| after | `axe-desktop-refused-pack` | native | 1440x900 | 4 PASS |
| after | `axe-fault-data` | native | 1440x900 | 4 PASS |
| after | `axe-fault-refusals` | native | 1440x900 | 4 PASS |
| after | `axe-data-malformed` | native | 1440x900 | 4 PASS |
| after | `reduced-motion` | native | 1440x900 | 1 PASS |
| after | `recording-copy` | native | 1280x800 | 2 PASS |
| before | `desktop-verified` | native | 1440x900 | 1 PASS, 2 FAIL |
| before | `mobile-verified` | native | 390x844 | 1 PASS, 1 FAIL |
| before | `sim-throw-exec-true` | simulated | 1440x900 | 5 FAIL |
| before | `desktop-refused-pack` | native | 1440x900 | 2 FAIL |

The two before failures in `desktop-verified` and `mobile-verified` only mean the old page has no `data-read-*` attributes to observe; its layout assertions passed. The other before failures are the defects this change fixes:

- `desktop-refused-pack`: the old page renders the refused pack's identity, hashes and schema versions as ordinary evidence, while its limits box states that `geometry_correctness.assertion` is `null`. The new page shows only the reader's `invalid-manifest` refusal.
- `sim-throw-exec-true`: a `writeText` that throws synchronously escaped the old handler. The page logged an uncaught error, never called the fallback and showed no feedback.

## What each kind of case shows

- **Native copy.** The context was granted `clipboard-read` and `clipboard-write` for the server's origin, and the clipboard was set to a sentinel first. Click, Enter and Space each produced `Copied.` through the Clipboard API, and `navigator.clipboard.readText()` returned the exact command. Without the grant (`native-copy-no-permission`) the feedback still appeared, but `readText` was refused with `NotAllowedError`, so the copied contents are unverified there and the receipt says BLOCKED.
- **Simulated copy.** Each case replaced `navigator.clipboard` and `document.execCommand` before the page's script ran, in its own context, and asserts on the stub's recorded calls: rejection, a synchronous throw, an absent API, `execCommand` returning `false` or throwing, and a resolving `writeText` that must not reach the fallback. These prove which branch ran, not that the operating system clipboard changed.
- **Faults and delays.** `--fault` serves a 500, a body that is not JSON, or JSON missing a record; the page shows a failed read for that channel and leaves the other channel's result intact. With one read delayed 2 s, the receipts show the other read settling first and the delayed one still pending, then both settling to their success states.
- **Keyboard.** Tab presses from page load, recorded stop by stop. The skip link is the first stop and the copy button is reached by Tab with `:focus-visible` and a 3 px solid outline, at both widths.
- **axe-core 4.10.2** (`results.testEngine.version`), run after both reads settled: 0 violations in all six states. At 390 px it reports `color-contrast` as incomplete on four nodes with reason `elmPartiallyObscured`: three cells of the horizontally scrolling source-refs table and the report `<pre>`. The harness measured them from computed styles: 6.53:1 for the table header text, 16.56:1 for the cells and 16.06:1 for the report, all above 4.5:1. No other state reported an incomplete.
- **Recording.** `recording-copy.webm` (4.0 s) scrolls to the reproduction command, tabs to the button, presses Enter and shows `Copied.`; the readback in its receipt equals the command.

## Not covered

No real phone, no Safari or Firefox, no screen reader, no 200 % zoom or forced-colors run, and no Lighthouse run. The browser was headless, so the clipboard is Chromium's, not the desktop's. Nothing here checks geometry; the page states that matching hashes identify bytes and say nothing about whether their geometry is right.

## Rerun

From the repository root, into a new empty directory:

```sh
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
node tools/capture-evidence-pack/inspection/capture-browser-evidence.mjs \
  --tree . --out /tmp/new-run --label after \
  --bad-pack tools/capture-evidence-pack/inspection/evidence/2026-10-12/inputs/bad-assertion.zip
```
