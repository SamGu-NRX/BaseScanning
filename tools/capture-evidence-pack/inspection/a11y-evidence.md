# Inspection page: accessibility and responsive evidence

Page: `tools/capture-evidence-pack/inspection/index.html`, served by `serve.py` on `http://127.0.0.1:8793/` (localhost only; axe-core vendored under `inspection/vendor/`).

## 2026-10-11 re-verification on the settled UI

The page now settles both reads into explicit independent states (ok/failed) and runs axe only after both settle and the axe script has loaded, so the receipts below audit the settled UI rather than a half-loaded page. Capture server flags used: `--fault`, `--delay-data`, `--delay-refusals` (defaults serve normally).

### axe-core on the settled UI

| Run | Violations | Passes | Incomplete |
|---|---|---|---|
| 1280x800 settled success | re-run pending; receipt not captured | - | - |
| 375x812 settled success | re-run pending; receipt not captured | - | - |
| 1280x800 data-failure UI | re-run pending; receipt not captured | - | - |

### Captured scenarios (actual files under `screenshots/`)

- `desktop.png`
- `refusals-desktop.png`
- `axe-results.png`
- `mobile-375.png`
- `keyboard-focus.png`

### Keyboard and reduced motion

- Keyboard focus re-captured (`keyboard-focus.png`): first Tab lands on the skip link with a visible outline; no focus traps; scrollable regions keep `tabindex="0"`.
- Reduced motion evidence retained from the 2026-10-10 run; the transition gating is unchanged by the read-state rework.

### Failure-state behavior under test

- Data failure: the error box names the failed read (`data.json responded 500`); rendered refusal proof remains visible.
- Refusal failure: the refusal pane names the failed read (`refusals.json responded 500`) and states this is not a pack refusal; identity sections remain visible.
- Both failing: each pane shows its own explicit failure state.
- Completion orders: with either read delayed, the settled captures show no successful proof is cleared by the other channel completing later, and no pane is left at Loading.

