# Next-View Selection: A Finite Decision Problem

A closed, fully enumerable study of sequential sensor placement: which window
to look through next, and when to stop and guess, under a budget too tight to
see everything. Every number in this README is reproducible from the frozen
manifest with the commands at the bottom.

**Synthetic acquisition only.** Nothing here concerns field improvement or
electrical safety; the "openings" are cells in a 6-bay binary grid.

## The frozen problem (`manifest.json`)

- 6 bays in a row; **exactly 1 or 2 are open** (21 possible worlds, listed
  explicitly in the manifest; the loader re-derives them and refuses a mismatch).
- Three observation stations, each seeing a fixed 3-bay window:
  L = bays {0,1,2}, C = {1,2,3}, R = {3,4,5}.
- Movement is **adjacent-only** on the station line L–C–R.
- Costs: observe = 1, move = 1; **total budget = 4** per run.
- That budget is deliberately tight: a full L-and-R tour costs 2 moves plus 2
  observations, leaving nothing to act on what was seen.

Policies receive only their compatible-world belief, their station, and their
remaining budget. The exhaustive-search oracle (`study.oracle_resolvable`)
knows the true world; it exists **only** to classify impossible-view worlds for
analysis and is never visible to a policy.

## Policies

| name | rule |
|---|---|
| `uncertainty-reduction` | Greedy: the feasible action with the best expected posterior-entropy gain per unit cost, computed only from the compatible set; a re-observation with zero expected gain is skipped, otherwise fall back to the adjacent move that uncovers more unseen bays. |
| `nearest-unseen` | Observe the current station while it still covers unseen bays, then walk to the nearest station that does (ties break left). |
| `fixed-order` | Follow the manifest order L, C, R literally, informative or not. |
| `seeded-random` | Uniform choice among feasible actions each step; the RNG seed is pinned per world (`base_seed + world_index`). |

A run ends when the belief is a single world (**resolved** — the answer is then
supported, never guessed), the budget is exhausted, or no action remains. An
unresolved run still answers with the lexicographically smallest compatible
world, and the result is flagged `unsupported`.

## Hand-solved traces

The tests are hand-derived, not generated. Two worked examples, pinned in
`tests/test_policies.py`:

**World `SOSSOS` (openings at bays 1 and 4), uncertainty-reduction.**
`observe:C` sees (O,S,S) → 4 compatible worlds {OOSSSS, SOSSSS, SOSSOS, SOSSSO}.
Expected entropy gains, computed by hand from that set: `observe:R` = 1.5 bits
(one 2-world outcome, two singletons) vs `observe:L` = 0.75·log₂3 ≈ 0.81 bits.
The policy walks right; `observe:R` leaves exactly one world. Resolved at cost
3, no guess.

**World `SSSSSO` (single opening at bay 5), nearest-unseen.**
After `observe:C` the policy walks left (tie broken left), observes L, and ends
up covering bays 0–3 with its last unit spent walking back toward C. Bay 5 is
never seen; the run ends unresolved over 3 compatible worlds.

## Results (84 runs, byte-deterministic)

| policy | resolved /21 | rate | mean cost | mean cost (resolved) | unsupported |
|---|---|---|---|---|---|
| uncertainty-reduction | 11 | 0.524 | 3.190 | 2.455 | 10 |
| nearest-unseen | 6 | 0.286 | 3.429 | 2.0 | 15 |
| fixed-order | 6 | 0.286 | 3.714 | 2.0 | 15 |
| seeded-random | 5 | 0.238 | 3.667 | 2.6 | 16 |

Reading: the greedy policy wins on resolution rate and pays for it in cost —
it spends nearly the whole budget hunting the last bit. Fixed-order matches
nearest-unseen's rate while being blind to what it learns; its resolved runs
are the lucky worlds where the L window happened to contain the openings.

### Impossible-view worlds

The oracle proves that 7 of the 21 worlds — `OSSSOS, OSSSSO, SOSSSS, SSOSSS,
SSSOSS, SSSSOS, SSSSSO` — **cannot** be resolved by any policy within budget 4.

The mechanism: seeing through **both** side windows costs at least 5. From the
start at C, the cheapest tour of L and R is move:L, observe:L, move:C, move:R,
observe:R — five units, and observing C first only raises it. Budget 4 can
therefore never cover both side windows. Worked case `SSSSSO`: only R sees
bay 5, and after C+R the belief is {SSSSSO, OSSSSO}, which differs only at
bay 0 — visible from L alone. Resolving it requires all three windows;
impossible. Worked case `SOSSSS`: C=(O,S,S) leaves 4 worlds; C+L leaves 3;
C+R leaves 2 — every budget-feasible prefix stays stuck above 1. The oracle
confirms the same structure for the other five.

The oracle check ran on **all 84 committed runs**: every run a policy actually
resolved is oracle-resolvable — no policy was credited with an impossible view
(`tests/test_study.py::test_oracle_admits_every_policy_resolution`).

## Scope note

This study models geometry only — windows, adjacency, cost, budget. The full
capture-geometry work (edge onsets, ray geometry, full-wall coverage) is a
separately labelled upper bound and is **not** simulated or claimed here.

## Reproduce

```bash
uv sync --project experiments/next-view-selection
uv run --project experiments/next-view-selection \
    pytest experiments/next-view-selection/tests -q          # 24 tests
uv run --project experiments/next-view-selection \
    python experiments/next-view-selection/run.py \
    --manifest experiments/next-view-selection/manifest.json \
    --out experiments/next-view-selection/results             # study + figures
uv run --project experiments/next-view-selection \
    python experiments/next-view-selection/run.py \
    --manifest experiments/next-view-selection/manifest.json \
    --replay experiments/next-view-selection/results          # byte-agreement
```

Replay compares fresh regeneration against the committed CSVs and figures by
sha256; the committed outputs agree **FULL** (all six artifacts byte-identical).
