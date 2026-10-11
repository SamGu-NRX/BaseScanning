# Claim ledger

Every user-visible claim this app and its README make about the placement session,
mapped to the code that implements it and the test or committed evidence that proves
it. Claims the app deliberately does not make are at the end — they are part of the
contract too.

How to reproduce the evidence: `pnpm install && pnpm test` (unit) and
`pnpm test:e2e` (browser). Both run fully offline against the in-process contract
fake (`src/lib/contract/contract-fake.ts`) served by the loopback adapter
(`evidence/loopback-server.mjs`, bound to `127.0.0.1` only); no test or evidence run
touches a real network endpoint — `tests/helpers.ts` aborts every non-loopback
request and asserts the block list stayed empty.

## Claims the app makes

### 1. A result is bound to the upload's SHA-256

- **Said where:** README intro; the app shows "Bound to upload `<sha256>`" under the
  result and in the inspector (e.g. `28f11aae87d8…`).
- **Code:** the server returns the binding; the client displays it verbatim from the
  result (`src/components/OutcomePanel.tsx` "Bound to upload" + `result.stats.input_sha256`;
  same in `src/components/Inspector.tsx`), and the upload bytes are hashed server-side
  per the contract (`src/lib/contract/`).
- **Proves it:**
  - Unit: contract tests assert `input_sha256` is the SHA-256 of the scene text sent
    (`src/lib/contract/*.test.ts`).
  - Browser: `tests/session-journey.spec.ts` "picks a scene, submits…" computes the
    scene text's SHA-256 in the test and asserts that exact hash is visible in a
    `code` element; the export/import journey re-checks the same hash after a
    round-trip through a file.
  - Screenshot: `evidence/screenshots/02-bound-result.png`.

### 2. The bearer token is never persisted, exported, or logged

- **Said where:** README ("stays in memory for the next submission only — never
  persisted and never logged"); the app's own notice under the token field ("A token
  is set. It stays in this page's memory only: never stored, never logged…").
- **Code:** `src/components/ServerFields.tsx` keeps the token in an uncontrolled
  input wired only to the store's in-memory field; `src/lib/session/persist.ts`
  serializes the session record from receipts and outcomes — the token is not part
  of the record shape, so no persistence or export path can include it.
- **Proves it:**
  - Unit: `src/lib/session/persist.test.ts` round-trips the record and asserts the
    token never appears; the reopen tests assert no token is restored.
  - Browser: the reopen journey fills a synthetic token, then reads
    `localStorage` and asserts the stored record contains neither the token nor the
    word "authorization" (`tests/session-journey.spec.ts` "reopens the persisted
    session…"); the export journey downloads the export file and makes the same two
    assertions on the raw file bytes.
  - The export test then imports that file into a brand-new browser context: the
    token notice is back to "Optional…", i.e. nothing was restored.

### 3. An export (and the saved record) contains exactly the documented redacted shape

- **Said where:** README section "Saved sessions: reopen, export, import" —
  `format`/`version`/`savedAt`, `scene` with the file's exact text, `session` with
  attempt history, receipts (URL, method, content types, byte lengths, request and
  body hashes, capped excerpt, truncation flag, timing), outcomes, witnesses; and
  the excludes list (token, HTTP headers, body beyond the capped excerpt, anything
  outside the one record).
- **Code:** `src/lib/session/persist.ts` (record shape and redaction), export button
  in `src/components/SessionPanel.tsx`.
- **Proves it:**
  - Unit: `src/lib/session/persist.test.ts` asserts the record and export shape,
    byte-length/hash fields, the capped excerpt, and absence of headers and token.
  - Browser: the export journey parses the downloaded JSON and asserts `format`,
    `version`, `savedAt`, `scene.fileName`, `scene.text` equal to the synthetic
    scene text, two attempts, and no token or "authorization" anywhere in the file
    (`tests/session-journey.spec.ts` "exports the redacted record…").
  - Screenshot: `evidence/screenshots/08-export-download.png`.

### 4. History is never displayed as the current answer

- **Said where:** the inspector's own copy — history entries say "Its result stays
  in history and is not displayed as the answer", mismatched results are "kept as a
  witness, never displayed", and stale attempts get ". Not the current answer.".
- **Code:** `src/components/Inspector.tsx` renders only the newest attempt's bound
  result as the answer (labelled "The result's own decision:", never bare
  "Decision:" — that heading is reserved for the current outcome panel); stale,
  mismatched, unreadable and late attempts are rendered as history and witnesses.
  The store's `display` state picks the current outcome; history entries cannot
  promote themselves (`src/lib/session/store.ts`).
- **Proves it:**
  - Unit: `src/lib/session/store.test.ts` (re-verification on restore, newest
    attempt selection) and `src/lib/session/persist.test.ts` (interrupted in-flight
    attempt reopens as cancelled, never as a settled answer).
  - Browser: the mismatch journey asserts the word "Decision:" appears **zero**
    times after a mismatched answer is recorded (`getByText("Decision:").toHaveCount(0)`)
    while the witness text "the request sent … the answer claims …" is visible; the
    inspector journey asserts the newest bound result is labelled current and the
    earlier attempt shows as history.
  - Screenshot: `evidence/screenshots/06-inspector-open.png`.

### 5. Result claims are quoted as the server's own verdict

- **Said where:** the outcome panel prints `Decision: <server's decision>`, the
  inspector labels its copy "The result's own decision:", and refusals show "The
  server said (<code>, HTTP <status>): <server message>".
- **Code:** `src/components/OutcomePanel.tsx` (decision, refusal quote),
  `src/components/Inspector.tsx` ("The result's own decision:"), the refusal
  detail with the server's envelope message and JSON pointer
  (`src/components/OutcomePanel.tsx`, fed by the contract's 422 envelope).
- **Proves it:**
  - Unit: contract tests pin the 422 envelope shape the UI quotes
    (`src/lib/contract/*.test.ts`).
  - Browser: the refusal journey asserts the server's message text
    ("invalid_scene, HTTP 422") is quoted on the page
    (`tests/session-journey.spec.ts` "sees the refusal…"), and the mismatch journey
    keeps "Decision:" off the page while a witness records what the server *claimed*.
  - Screenshots: `evidence/screenshots/04-refusal-422.png`, `05-mismatch-witness.png`.

### 6. A closed tab does not end the session — and reopening restores the server too

- **Said where:** README "Closing the tab does not end the session: the next page
  load offers to reopen it"; interrupted in-flight attempts reopen as `cancelled`.
- **Code:** `src/lib/session/persist.ts` (versioned browser-local record under one
  key, `basescanning-web.session.v1`), `src/lib/session/store.ts` (reopen re-intakes
  the saved scene text through the same intake as a fresh upload, re-verifies the
  newest bound result, marks a mid-flight attempt cancelled, and — since this
  branch — restores the placement server origin from the newest request receipt so
  the next attempt goes to the server the session actually talked to).
- **Proves it:**
  - Unit: `src/lib/session/persist.test.ts` — reopen with two attempts intact
    (history length 2), interrupted attempt becomes cancelled, corrupt record is
    reported with bytes kept, and the record's own server origin is adopted on
    reopen (while the request path never leaks into the base URL).
  - Browser: the reopen journey fills the token, submits twice (cancel + retry
    history), reloads the page, reopens, and asserts history, no token, and that a
    fresh submission lands on the loopback server (it would `ERR_CONNECTION_REFUSED`
    against any other origin); the export/import journey proves the same record
    restores standalone in a brand-new context.
  - Screenshot: `evidence/screenshots/07-reopened-session.png`.

### 7. The journey is operable without a mouse, has no reducible motion, and fits a phone

- **Said where:** this ledger and the PR description (the README makes no motion
  claim — see "not made").
- **Proves it:**
  - Keyboard: `tests/session-journey.spec.ts` "every control is operable from the
    keyboard alone" walks the full tab order (`server-base-url` → `server-token` →
    `scene-file` → Export → import → Run placement — the exact DOM order) and runs
    the whole submission with only Tab and Enter.
  - Reduced motion: the reduced-motion test scans every stylesheet rule for
    `animation` and `transition` properties; the app ships none (the single rule in
    `src/styles/app.css` is `overflow-wrap` for long hashes). The status line's
    "in flight → bound" progression is text, not animation.
  - Responsive: the 390×844 journey asserts no horizontal overflow at any point of
    the full flow; screenshot `evidence/screenshots/10-mobile-390x844.png`.
  - Accessibility: axe scans of empty, bound, refusal and inspector states in light
    and dark emulation recorded in `evidence/axe/*.json` — **zero violations of any
    impact level in all eight scans** (verified by `tests/axe-states.spec.ts`, which
    fails the suite on any violation).

## Claims the app deliberately does NOT make

### No safe-placement guarantee

The app reports the server's verdict and the evidence trail; it never claims a
placement is safe, correct or allowed. Words like "safe", "pass is correct", or
"verified" do not appear over results — the strongest statement the UI makes is
what the server said, bound to which bytes. The mismatch journey pins this: a
server answer that names different bytes is recorded as a witness, and the page
still shows no decision.

### Capture stays in the iOS app

The web client is a zero-install *experiment* surface: pick a scene file you
already have, aim it at a placement server, read the answer. There is no camera,
photo picker, or capture flow here — the README says so and the scene input only
accepts a `.json` file. Any claim about capturing homes belongs to the BaseScanning
iOS app, not to this client.

### No motion/animation claim in the README

The README does not advertise reduced-motion support because there is nothing to
reduce: the app ships no animations or transitions (verified by the stylesheet
scan above). If animation is ever added, this becomes a claim that needs its own
tests.

### No network privacy claim beyond redaction

The client claims receipts record sizes, hashes and timings — not headers, not
bodies beyond the capped excerpt. It does not claim the placement server is
trustworthy, that HTTPS is enforced for you, or that the loopback evidence setup
models real-network behavior beyond the contract.
