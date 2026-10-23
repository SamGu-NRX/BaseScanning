# Copy-button focus evidence, 2026-10-12

[`../../capture-copy-focus-evidence.mjs`](../../capture-copy-focus-evidence.mjs) ran in headless Chromium 156.0.8078.4 (Playwright 1.64.0) against the real inspection server, a fresh server and browser context per case, at 1440x900 and 390x844 with reduced motion on. In each case it pressed Tab until Copy command had focus, pressed Enter, recorded focus and feedback, then pressed Tab once more.

| Run | Source | Result |
|---|---|---|
| `before/` | `alpha` at `cde8450f`, exported with `git archive` | 48 PASS, 48 FAIL |
| `after/` | commit `a8eefc9c`, clean tree | 96 PASS |

`receipts.json` in each run records the SHA-256 of the served `index.html`, `serve.py`, `read.py` and example pack, and of the driver. After `a8eefc9c`, `index.html` is `aacff09c...` and the driver is `5c04a5b5...`.

## What failed before

- In the five simulated cases that reach the fallback (rejected, thrown or absent `writeText`, with `execCommand` returning true, false or throwing), focus ended on `<body>` after Enter, and the next Tab went to the skip link. After the fix, focus stays on Copy command with its 3 px ring, and the next Tab goes to the report text.
- With `writeText` rejecting 600 ms later and the user pressing Tab in between, focus ended on `<body>`. After the fix it stays on the report text the user moved to; the fallback does not pull it back to the button.
- Copy command was 41.17 px tall at 1440x900 and 39.63 px at 390x844. It is 44 px at both.

The native case (Chromium's own Clipboard API with a permission grant) passed its focus and Tab checks before and after: that path never moves focus. Its readback equalled the command both times. Resolved `writeText` behaved the same way. No case left a textarea behind or caused horizontal page overflow.

## Files

- `before/` and `after/` keep `reject-exec-false-*.png` and `slow-reject-user-moves-*.png`, the states that changed. The other cases' screenshot hashes are in `receipts.json`; their images were not kept.
- `after/keyboard-denial-1440.webm` (4.0 s, with MP4 and GIF encodings) records the `reject-exec-false` case at 1440x900: Tab to Copy command, Enter, the failure message, then Tab to the report text. The denial is simulated; the keyboard input is real.

## Not covered

The simulated cases prove which branch ran and where focus went, not that the operating system clipboard changed. No real phone, Safari, Firefox or screen reader was used.
