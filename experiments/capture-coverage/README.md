# capture-coverage study (preregistered)

Does capture-to-scene keep unseen space unknown? This package answers it closed-loop: procedural
scenes with known geometry, simulated phone captures, the **real** recon worker (`recon/`) and
placement server (`server/`), and an independent analytic reference that judges every claim
against what the photons could actually have seen.

Commit `c813182` (branch `obv/products-capture-coverage-20261009`, off `origin/main`) is the
study base. Server-dependent results name their ref: `main` = working tree's `server/`,
`t3/server` = extraction of `1d1e7e1b`. The ETH3D electro external check (separate thread,
committed reference reproduced bit-identically on CPU) anchors the simulator's acceptance bars
to the real pipeline: median pair error 0.52 in, claimed 22.51 ft, false-observed 0.07 ft.

## Design

- `scenes.py` — the truth, in one world frame (metres, +y up, wall face z = 0, meter at x = 0):
  wall segments around openings, door/glass fills, pilasters, bins/bushes/rails, an L return
  wall, a bowed arc, ground patches with height functions, a parallel back fence.
- `sim.py` — walks (exact true poses + separately reported ARKit poses, so position drift and
  yaw drift can be injected honestly: photons true, odometry wrong), a software rasterizer for
  LiDAR depth (8 mm noise, edge + 0.4% dropout, 5 m cap, ARKit confidence planes), and bundle
  writing byte-compatible with `recon.capture.load` (column-major 4x4 poses in feet, float32
  metres depth, uint8 confidence, phone-attested marks/labels).
- `reference.py` — the oracle. Moller-trumbore ray casting against ALL scene triangles, the
  same 65°/0.9/1.68 frustum gate the worker uses, and per-band truth: wall band (18 rows ×
  24 columns × 3 sub-samples per cell), ground (12 heights × 21 depths), facing strip, overhead
  strip. A point counts as seen when a camera ray reaches it unblocked; a cell's existence is
  whether any sub-sample lies inside a solid.
- `judge.py` — audits the worker's output scene.json + geometry.json. Claims are mapped into
  the world through the **fitted** frame (the fit is part of the claim: a drifted fit claims
  drifted world cells, and that error is the worker's). Violations:
  - `FALSE_OBSERVED` — a claimed wall/ground span with >0.2 ft (wall rows) or >5% (ground
    depth samples) unseeable by ANY frame in the true walk.
  - `FALSE_CLEAR` — facing/overhead "clear to X" where a solid occupies sampled space inside X.
  - `CLEAR_UNSEEN` — clear-to-X where some sample inside X is occupied-free but no camera could
    see it (nothing supports the claim; grazing tolerance 2 cm).
  - `PHANTOM_OBSTACLE` — a facing measurement at depth X with nothing within ±0.15 m of X.
  - `OVERREACH` — existence claimed beyond the true face span (openings, beyond ends).
  - `EVIDENCE_DEFICIT` — a measurement resting on < 2 views (the worker's own two-position bar).
  - `UNSOUND_PASS` — a server pass whose deciding band is >50% unseeable at the spot span
    extended by the rule's own reach.
  - `MISSED` (informational, not a violation) — observable wall/ground left unclaimed.
- `solve.py` — runs the REAL server (`uv run --project server python`, driver prints result
  JSON) on the worker's output scene; extracts t3/server via `git archive` (no git state
  changes) for the second ref.
- `runner.py` — the scenario grid below; each carries its preregistered prediction. Results go
  to `/tmp/cc-study/<name>/` (out of the repo tree), one judge.json each.

## Preregistered grid (18 + 2 duplicate-solve runs)

Family A — reported-capture pathologies, true photons (noise ×5, 6° skewed path, cross-track
odometry drift 4 cm/m with jitter, every-3rd frames dropped, every-5th depthless, no confidence
planes): predictions all "no violations from these alone; claims shrink accordingly", EXCEPT
drift-cross-track where claims self-consistent with drifted poses must show FALSE_CLEAR/
FALSE_OBSERVED in true coordinates with a stable fingerprint (the self-consistency trap the
handoff notes flagged).

Family B — physical confounds: bush flush to wall and bin 0.2 m off wall (unseeable gap behind:
FALSE_OBSERVED if claimed through), parallel fence at 4 m (facing-clear must bound near 13 ft),
5 cm grazing rail (LiDAR should see it; honest output = a facing measurement), arc bow (line fit
rounds it; no hard violation expected), 6 cm pilaster (borderline, either output honest).

Family C — holes and ends: 2 m unfillable doorway (stop at jamb or OVERREACH), short walk
beyond which nothing is explored (claims stay inside the mark), fence-support short walk
(facing-clear-to-6ft must rest on evidence beyond the mark or be UNSURE).

Family D — missing inputs: no attested ground polygons; exactly one depth frame (single-view
free-space must not become claims: EVIDENCE_DEFICIT or UNSOUND_PASS expected).

Duplicate-solve runs: the noise-high output is solved on both server refs (main + t3) in every
scenario; decisions must agree where the rules agree, and any disagreement is reported per ref.

## Fixed choices made before any run (recorded to prevent post-hoc fitting)

- Oracle resolutions: wall cells 0.75 m × rows 0.0933 m × 3 sub-samples; ground 0.5 m out-step ×
  0.5 ft height-step... (ground: 12 heights × 21 out-samples per cell); facing 0.0762 m out ×
  0.15 m height × 3 across; overhead 0.05 m out × 0.075 m height × 3 across.
- Violation thresholds as listed above; grazing clearance 2 cm for free-space probes, 0.1 mm
  for samples on a face; the wall-band FALSE_OBSERVED bar is 0.2 ft of unseeable rows (~2 rows).
- The judge reads only the worker's output files; it never receives the input bundle's marks.
- Walks: step 0.55 m, standoff 2.4 m, pitch-look y = 1.1, 39 frames baseline.
- No dropout compensation in the judge: random pixel dropout biases the worker toward fewer
  claims (conservative for honesty testing) and is absorbed by the coverage threshold.
- The fitted ground height comes from geometry.json (`ground.height_ft`); absent the sidecar the
  judge substitutes the TRUE height at the fitted meter and records `ground_height_source`.

## Amendments (dated, made after first runs)

- **2026-10-10 — overhead height grid extended downward.** The frozen step (0.075 m) is unchanged,
  but the grid now starts at 0.05 m instead of 0.30 m. Found on the first end-to-end run: the
  baseline worker's overhead clearance claim (0.984 ft ≈ 0.30 m) mapped to an EMPTY height mask
  (`heights < 0.30` is vacuous at a grid starting exactly at 0.30), and the judge reported
  "0 views" rather than auditing. Any clearance claim must have samples beneath it.
- **2026-10-10 — ground strip unseeable under the frozen walk (recorded, not "fixed").** With the
  frozen pitch-look y = 1.1 (9.5° down) at 2.4 m standoff, the phone's vertical frame bottom sits
  ~37° below horizontal while the near ground strip lies at ≥ 38°: the walk physically cannot see
  ground at ANY out-distance. The oracle reports this correctly (all ground samples UNKNOWN); the
  recon worker sees the same null ground and claims none, so the ground band audits vacuously on
  family A/B scenarios. This is a consequence of the preregistered walk, left unchanged — any
  ground CLAIM made by a worker remains a FALSE_OBSERVED flag, which is the honest audit.
- **2026-10-10 — judge output carries an uncertainty summary.** Per run: wall/ground samples that
  EXIST but no frame could see (`wall_unknown_samples`, `ground_unknown_samples`), plus a note
  that unobserved regions are UNKNOWN, never counted toward complete observation.

- **2026-10-10 — judge band evaluation restructured to claimed spans.** Facing/overhead bands
  were built over the full probe range (all ~190 cells) though only claimed spans are audited;
  on a confound scene (~2k-triangle foliage) that cost over an hour per scenario in the oracle
  ray-caster. Bands are now built per claimed span ± one cell; oracle resolutions and audit
  semantics are unchanged, and the bush scenario runs end to end in ~5 minutes.
- **2026-10-10 — witness records the input bundle hash.** Replay witnesses now pin the bundle
  content, so a replay failure localizes to the worker leg vs the oracle leg.
- **2026-10-10 — one unreproduced worker output, preserved.** The first grid run's baseline
  recon output (runs/baseline/out, hash 14713defd915, claims starting at cell -5.5) differs
  from six later recon runs on a byte-identical bundle (all e701ae19e02b, claims at -6.0) —
  recon is deterministic under repeated runs and different thread counts in the current
  environment, and rerunning into a stale output directory overwrites cleanly, so the cause
  is unexplained. The result hash depends on the worker's claims (the judge probes a window
  around them), so worker nondeterminism breaks replay. The artifact is preserved; replays
  report mismatch rather than papering over it.

## Layout

    capture_coverage/  scenes.py sim.py reference.py judge.py solve.py runner.py
    tests/             reference truth tests (thin wall, occlusion, two-position, units)
