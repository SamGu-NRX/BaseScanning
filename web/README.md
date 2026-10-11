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

## Conventions

- Lengths are stored in meters. Convert to feet and inches only for display, using `src/lib/units.ts`.
- TypeScript runs in strict mode with `noUncheckedIndexedAccess`.
- Tests live next to their source files as `*.test.ts`.
- Use synthetic data only. The repository is public, so never commit real home photos, addresses or meter numbers.
