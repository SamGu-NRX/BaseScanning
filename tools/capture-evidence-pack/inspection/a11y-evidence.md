# Inspection page: accessibility and responsive evidence

Page: `tools/capture-evidence-pack/inspection/index.html`, served by
`tools/capture-evidence-pack/inspection/serve.py` on `http://127.0.0.1:8793/`
(localhost only, no external network calls; axe-core is vendored under
`inspection/vendor/`, see `vendor/NOTICE.txt`).

Verification date: 2026-10-10. Browser: Chrome (headless, via agent-browser),
real rendered page against the committed pack and receipts.

## Keyboard access — PASS

- First Tab lands on the **skip link** ("Skip to content"), which becomes
  visible at the top-left with a 3px focus outline: `screenshots/keyboard-focus.png`.
- Tab order (observed by stepping Tab and reading `document.activeElement`):
  skip link → "Copy command" button → scrollable `<pre>` regions
  (packed report text, refusal outputs, axe output; each has explicit
  `tabindex="0"`) → named scrollable table regions (`role="region"`) →
  wraps back to the top of the page.
- **No focus traps**: focus cycles through every stop and returns; there are no
  modal overlays and no `tabindex` values above 0 anywhere.
- The copy button announces its result through `role="status" aria-live="polite"`
  and degrades to an explicit failure message when the clipboard is unavailable.

## Mobile layout at 375 px — PASS

- `screenshots/mobile-375.png` (375x812): single-column reflow, the identity
  grid collapses to stacked terms, sections keep padding and readable line
  lengths.
- No horizontal page overflow: `document.documentElement.scrollWidth` was 360
  at 375 px viewport. Wide tables and long hashes scroll inside their own named
  regions instead of the page.

## Reduced motion — PASS

- With `prefers-reduced-motion: reduce` emulated (`agent-browser set media
  reduced-motion`), `matchMedia('(prefers-reduced-motion: reduce)').matches`
  reported `true` and the only transition on the page (the copy button's
  background-color transition) computed to `transition-duration: 0s`.
- The stylesheet gates that transition behind
  `@media (prefers-reduced-motion: no-preference)`; there are no other
  animations. The page renders identically in static captures with and without
  the emulation, so no separate screenshot is committed for this check.

## axe-core — 0 violations at both viewports

Run in the live page (loaded via `/?axe=1`) with axe-core 4.10.2, tags
`wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice`:

| Viewport | Violations | Passes | Incomplete |
|---|---|---|---|
| 1280x800 | 0 | 46 | 1 (color-contrast, 2 nodes) |
| 375x812 | 0 | 46 | 1 (color-contrast, 10 nodes) |

Full machine-readable results including the accepted list:
`screenshots/axe-results.json`. Rendered result visible on the page:
`screenshots/axe-results.png`.

### Accepted incomplete, with reasons

`color-contrast` — axe reports "background color could not be determined
because it's partially obscured by another element" for elements overlaid by a
horizontal scrollbar during static sampling. Every flagged element declares an
explicit foreground/background pair; computed ratios (WCAG formula):

| Elements | Colors | Ratio |
|---|---|---|
| report/refusal/axe `<pre>` text | `#e8f1ec` on `#0e1512` | 16.06:1 |
| body text | `--ink #16211c` on `--card #ffffff` | 16.56:1 |
| table header text | `--muted #52615a` on `--card #ffffff` | 6.53:1 |
| limits heading | `#7a4d0a` on `--warn-bg #fff7e8` | 6.82:1 |
| refusal heading | `#7c2424` on `--refusal-bg #fdf0f0` | 8.85:1 |

All are above the 4.5:1 AA threshold, so these incompletes are accepted as
sampling artifacts, not contrast failures.

### Violations found and fixed during verification

- `scrollable-region-focusable` (serious): scrollable `<pre>` blocks and, at
  mobile width, the table wrappers were not keyboard-focusable. Fixed with
  `tabindex="0"` on every scrollable `<pre>` and
  `tabindex="0" role="region" aria-label="…"` on the two table wrappers.
- An `aria-label` on the refusal `<pre>` elements produced an
  `aria-prohibited-attr` incomplete (labels are not well supported on `<pre>`
  without a role); removed — the adjacent "Recorded output of `<path>`"
  paragraph already names the content.

## Screenshots

| File | Shows |
|---|---|
| `screenshots/desktop.png` | Page at 1280x800: limitations panel, identity, schema versions |
| `screenshots/mobile-375.png` | Page at 375x812: single-column reflow, no overflow |
| `screenshots/keyboard-focus.png` | Skip link focused on first Tab, visible focus outline |
| `screenshots/refusals-desktop.png` | Recorded refusal states rendered from their committed receipts |
| `screenshots/axe-results.png` | On-page axe-core results region (`"violations": []`) |
