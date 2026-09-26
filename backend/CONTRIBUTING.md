# Contributing

## Branches and pull requests

- `main` is protected by convention: nobody pushes to it directly after the initial import, and agents never merge.
- **Breadth work lands on branches named `breadth/<path-id>`, opened as draft pull requests.** Path ids match the folders: `a1-classical`, `a2-feedforward`, `a3-depth-fusion`, `a4-gaussian-splatting`, `a5-world-models`, `a6-iphone-quality`, `a7-iphone-degradation`, `a8-scale-without-lidar`, `a9-no-tape-scale`, `b1-detection`, `b2-equipment-reading`, `c1-rules-3d`, `c2-volumetric`, `c3-vlm`.
- Keep one writer per branch. Each breadth pull request ends with the standard one-page result: metrics, runtime, cost, failure modes, licence, and a go / no-go.
- Other work uses a short descriptive branch name and a draft pull request.
- Breadth agents that work in the Project store don't push themselves: `scripts/sync_breadth.sh <path-id>...` (or `--all`) copies `internal/breadth/<folder>/` and the path's result file (as `RESULTS.md`) onto `breadth/<path-id>` and pushes it. It skips photos, model outputs, weights, files over 5 MB, real meter numbers (`raw/meter/`, `labels/meter_numbers_hand.csv`), and Base SSR prompt text or a digest of it.

## Snapshot folders

These are copied from the team's Cursor Project store by `scripts/sync_from_store.sh`, and edits made here are overwritten on the next sync:

- `packet/` (from `internal/packet-generator/`)
- `eval/` (from `internal/eval-harness/`)
- `criteria/spec/` (from `internal/criteria/`)
- `data/scripts/` and `data/manifest.json` (from `internal/data/`)
- `docs/capture-packet-spec.md`, `docs/criteria-spec.md`, `docs/scene-and-verdict-format.md`

Change them in the store, then run `scripts/sync_from_store.sh`. It creates a `sync/store-<timestamp>` branch from `origin/main`, re-copies the folders, and leaves the changes uncommitted for review. Lint is relaxed for these folders until the store copies are cleaned up.

## What never gets committed

- Anything in `private/`, including the prompt material Base gave the team. The sync script puts it there locally.
- Images, videos, packets, point clouds and other large binaries. The sync skips files over 5 MB; `data/` holds scripts and manifests only.
- ML runs on Google Maps / Street View, Mapbox or Esri imagery, whose terms forbid it. Use StratMap or NAIP.

## Checks

CI runs `ruff check`, `ruff format --check` and `pytest` on every push to `main` and every pull request. Run them locally with `uv run ruff check . && uv run ruff format --check . && uv run pytest`.
