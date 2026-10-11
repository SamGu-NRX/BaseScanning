# web

Browser app for zero-install capture experiments. The home page runs a placement
session: pick a `scene.json`, aim it at a placement server, run the attempt (with a
live status line and cancellation), and read the answer. The state layer lives in
`src/lib/session/store.ts` on top of the placement contract in `src/lib/contract/`;
the contract's test double (`contract-fake.ts`) is also the only server surface the
tests use. The optional bearer token stays in memory for the next submission only —
it is never persisted and never logged.

## Setup

Requires Node 24 or later (`.node-version`) and the pnpm version pinned in `package.json` `packageManager`. Corepack and pnpm's own version switching both pick up that pin.

```sh
pnpm install
```

## Commands

| Command          | What it does                                           |
| ---------------- | ------------------------------------------------------ |
| `pnpm dev`       | Vite dev server                                        |
| `pnpm build`     | `tsc -b`, then `vite build` into `dist/`               |
| `pnpm typecheck` | `tsc -b` (no emit)                                     |
| `pnpm lint`      | `biome ci .`: lint and format check, no writes         |
| `pnpm format`    | `biome format --write .`                               |
| `pnpm test`      | `vitest run`                                           |
| `pnpm check`     | lint, typecheck, test, build; stops at first failure   |

CI (`.github/workflows/web.yml`, job "Web checks") runs `pnpm install --frozen-lockfile` and then `pnpm run check`.

## Saved sessions: reopen, export, import

The app keeps one session record in browser-local storage (`localStorage`) and
publishes a snapshot there after every change. Closing the tab does not end the
session: the next page load offers to reopen it. Reopening re-intakes the saved
scene text through the same intake a fresh upload takes, recomputes the newest
bound result from the attempt history, and starts with **no bearer token** — the
token is held in memory for the app's lifetime only and is never written to
storage. An attempt recorded mid-flight ("submitting") can never settle, so
reopening marks it `cancelled` with a cancelled outcome; a new attempt can start
immediately. Export (button "Export session (redacted JSON)") and import write and read the same
redacted shape in a standalone file.

**What an export (and the saved record) contains**

- `format` and `version` (`basescanning-session-record` v1) and a `savedAt` time.
- `scene`: the file name and the file's **exact text** — re-intaking it reproduces
  the original upload bytes and their SHA-256.
- `session`: the attempt history — per attempt the status, the request receipt
  (URL, method, content type, byte length, `requestSha256`, started time), the
  response receipt where one exists (status, content type, body byte length,
  `bodySha256`, a capped `bodyExcerpt`, truncation flag, timing), the outcome
  (including any bound result), and the witnesses (refusals, mismatched answers,
  unreadable bodies, late arrivals) with their reasons.

**What it excludes**

- The **bearer token** — never stored, never exported, never logged; it lives in
  memory between selection and the next submission only.
- HTTP **headers**, including any `Authorization` header — receipts record the
  URL, sizes, hashes and timings, not the wire headers.
- The response **body beyond the capped excerpt** — long bodies are truncated by
  the client, and the excerpt is bounded in the record.
- Anything outside this app's session: no other tab's or app's storage, no
  browser state beyond the single record under one key.

Because the scene text travels in full, treat an export file as you treat the
original `scene.json`: it is a capture record, and the repository's rule about
synthetic data applies to it too.

## Conventions

- Lengths are stored in meters. Convert to feet and inches only for display, using `src/lib/units.ts`.
- TypeScript runs in strict mode with `noUncheckedIndexedAccess`.
- Tests live next to their source files as `*.test.ts`.
- Use synthetic data only. The repository is public, so never commit real home photos, addresses or meter numbers.
