# Backend status — wave 6 (W6)

This document accompanies `backend/tests/fixtures/w6/` and the gate test
`backend/tests/test_backend_w6_gate.py` on branch `obv/burst6-bs-status-evidence`
(draft PR stacked on PR #269, base `obv/burst6-bs-frontdoor-gate` at `b4401f27`).
It records what wave 6 proves, with the machine-readable receipts staged as
fixtures so the claims stay checkable. `backend/docs/STATUS.md` is untouched and
remains the authoritative burst-5 status; this file adds the wave-6 layer:
the placement stage behind Hunter's capture front door (PRs #268, #269) and the
acceptance receipts from the evaluator and gates work (PRs #261, #263, #264).

## What is staged here (receipt fixtures)

`backend/tests/fixtures/w6/` holds four JSON receipts. All contain no
credentials; capture/run ids and storage paths are run-scoped.

- `suite.json` — the eval-suite receipt (`hss-eval-suite/v0`, the contract PR
  #263 pinned), summary pass 3 / fail 21 / warn 0 / measured 3 over 27 rows,
  `git_sha 9b177de6` (PR #263's branch head). Its producer (`make evals`,
  PR #261) is not merged into this PR's base branch, so the receipt was staged
  by running that producer **unmodified** at its own head `9b177de6` in a
  throwaway worktree (`git worktree add`, run, copy, remove). No threshold or
  evaluator code changed to stage it; the run exits 1 by design (fail-closed).
- `gates.json` — the offline gates receipt (`hss-gates/v0`) from
  `make gates-offline` on this branch at tip `2083c91a` (recorded in the
  receipt's `git_sha`); summary pass 134 / fail 1 / known_fail 0 / warn 1 /
  skip 28 over 164 rows. The branch tip later moved to `b4401f27`, which only
  reformats a test file, so the receipt's code provenance is unaffected.
- `frontdoor-receipt.json` — one capture-front-door journey
  (`hss-w6-frontdoor/v0`), produced by driving PR #269's own test helpers once
  under the SD-73 dev settings those tests use (ingest hooks `auto`, stage
  stubs, memory state, volume storage): packet `mini-arkit`,
  capture `cap_01M509F1JWE71MCP006WX0QV9M`. The journey ends with exactly one
  terminal event (`verdict_ready`), capture status `manual_review`, the
  placement stage `done` with no error code, and a `200` from
  `GET /v1/captures/{id}/result`.
- `placement-result.json` — the placement stage's decision artifact from that
  journey (`decision: manual_review`, schema-valid against
  `server/schemas/result.schema.json`).

## Classification (what the gate test asserts)

`tests/test_backend_w6_gate.py` classifies every suite row from the receipt's
JSON — never from console or SUMMARY prose — and asserts the summary counts:

- **PASS 3** — the fixture scenes `c1/c2/c3` in the `fixtures` group; each
  carries an evidence path that exists on disk.
- **BLOCKED 5** — FAILs whose note names the unavailable asset, per the
  receipt contract PR #263 pinned (blocked fixtures carry the asset name and
  the produce hint): three `pipeline/*` rows failing with
  `occluders_missing` ("laser PLY absent; run make gt") and two
  `geometry/eth3d-facade/colmap-*` rows failing with `recon asset missing`
  ("laser asset laser.ply is not a readable file under
  runs/suite/latest/work/eth3d-facade/gt-laser — the recon PLYs are gitignored
  Modal-pipeline outputs (`uv run modal run scripts/modal_scene.py`)").
  The recon rows' `evidence_path` is honestly null: nothing was produced to
  point at, so the expected-asset location in the note is the row's evidence.
- **FAIL 16** — the evaluator's fail-closed cascade, kept FAIL so nothing
  hides: with the pipeline unable to run, the three `criteria/*` report rows,
  the composite `verdict_decisions` row, and the twelve `verdicts/*` rows have
  nothing to score ("the pipeline stage produced no criteria report" /
  "...no verdict for this profile"). These are not threshold failures, and
  they are not re-labeled.
- **MEASURED 3** — the three `public` profiles, recorded without a bar
  (`value: null`, note "no committed labels for this profile").

The offline gates receipt is asserted separately: 134 PASS / 1 FAIL / 1 WARN /
28 SKIP, the single FAIL being the expired SD-96 gate (below).

## What is proven (commands, this session)

- `make gates-offline` → exit 1; receipt `gates.json` above.
- Eval-suite producer run unmodified at `9b177de6` → exit 1 by design;
  receipt `suite.json` above.
- `uv run --frozen --extra api pytest tests/test_backend_w6_gate.py
  tests/test_capture_placement_frontdoor.py -q` → **12 passed**: the gate test
  (10) plus PR #269's two front-door tests. Among them, a fresh front-door
  journey driven in this run reproduces the staged `placement-result.json`
  byte-for-byte except the solver's `stats.elapsed_ms`, and the direct
  `POST /v1/placements` contract still echoes the submitted bytes' sha256.
- `make lint` → green (ruff check, ruff format over 470 files, lint-imports
  5 contracts kept).
- `make schemas` → green; `make hygiene` → green (3105 tracked files).
- Full backend suite `uv run --frozen --extra api pytest -q` → 1234 passed /
  17 skipped / 5 failed. The same 5 fail on the unmodified base branch
  (verified for bursts 4/6 and unchanged here): 1 SSR-hygiene test
  (`tests/test_ssr_overlap_check.py`) and 4 `server/eval/tests/test_gates.py`
  eval-gate tests, the SD-96 expiry among them. Nothing in this change
  introduces a failure; the diff adds fixtures, one test file, and this
  document.

## Failures, kept visible

1. **The expired SD-96 gate.** `gates.json`'s single FAIL is
   `positive/eth3d-facade/gt-laser-gravel/C/footprint3in`: "route 9.1834 ft
   exceeds 9.0". Its `known_failure` window (reason "conservative miss from
   near-wall self-occlusion, SD-96", owner WS-09) expired 2026-10-04, so the
   gate reports FAIL again and `make gates-offline`/`make ci` exit 1 for this
   reason alone. Extending the window is a WS-09 policy call — recorded here,
   not patched in code.
2. **Missing laser/recon PLYs.** The laser and recon PLYs are gitignored
   Modal-pipeline outputs and are absent on this machine, so 5 suite rows are
   BLOCKED with the produce hints (`make gt`; `uv run modal run
   scripts/modal_scene.py`), and the other 16 FAILs are the fail-closed
   cascade of those assets. The receipt and the gate test keep both visible;
   neither re-labels the cascade.
3. **No GPU.** The breadth tracks need the `objects`/`gpu` extras plus model
   weights, which the sandbox cannot install or download; every real stage
   runs behind the SD-73 stubs. Anything behind a stub is unverified here.
4. **No Swift toolchain.** The sandbox has no Xcode/Swift toolchain, so the
   iOS client cannot build. The `CaptureAPI` mirror in Python stands in; the
   iOS client's stale hand-rolled networking from `STATUS.md` is unaltered.
5. **`/v1/placements` contract unchanged.** The direct endpoint still serves
   the synchronous decision with `stats.input_sha256` equal to the committed
   scene fixture's sha256 (`07bda024c682be36…`), which equals the placement
   stage's solved `scene.json` bytes. The client mismatch from `STATUS.md`
   stands unaltered.

## Next decision

Real-data or GPU evaluation: fetch or produce the laser and recon PLYs
(`make gt` locally; `uv run modal run scripts/modal_scene.py` needs a Modal
token) or point the suite at real captures — that flips the 5 BLOCKED rows and
the 16 FAIL cascade into scored rows. Separately, WS-09 decides whether to
extend the SD-96 known-failure window. Both are policy calls, not code changes
on this branch.
