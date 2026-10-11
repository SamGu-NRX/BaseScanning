# Methods: what error bar a clearance between two features deserves

## Scope

This study asks one question: for a clearance rule between two features on one
wall, what error does the measured gap deserve, and which of five candidate
error bars is right? It does not change the shipped calculation, does not claim
any safety property for the service, and produces no measurement. Its
deliverables are (1) a provenance audit of every number the five bars rest on,
(2) a decision-accounting simulation calibrated to committed error data, and
(3) a comparator that pins the model of the shipped calculation to both solvers.

The load-bearing assumption of any conclusion here is that tracked position
error behaves like a random field along the wall. That is a modeling choice,
not an observation: no committed data measures a two-tap difference error
directly (the audit's difference scan shows this). The protocol in
[protocol-clearance-difference.md](protocol-clearance-difference.md) is the
measurement that would settle it.

## The five bars

All five decide the same way: `server/solver.py` `at_least` (line 168), a
three-way decider. PASS when `value - error > threshold`, FAIL when
`value + error < threshold`, UNSURE in between or when the bar covers both
lines. `models.py` mirrors it and `tests/test_models.py` asserts the mapping on
both sides of each line. The comparator additionally runs the real solvers.

For a gap of `c` ft between features tapped at walked distances `a` and `b`
(all bars in feet):

- **current** — what `t3/server` ships today: `base_a + base_b + drift_per_ft
  * (a + b)`. Each endpoint's bar is its own `base error + rate * walked
  distance`; the clearance check adds the two. This double-counts any error
  common to both taps.
- **rate_only** — the same without the per-tap bases: `rate * (a + b)`. Same
  double-count, smaller constant. Not a proposal; a decomposition of the
  current bar into base and walked parts.
- **rate_on_gap** — bases + `drift_per_ft * c`: the shipped coefficient and
  bases, but drift accumulated over the gap instead of over the walks. Needs
  no new data claim — it reinterprets the rules rate — which makes it the
  strongest bar available without new measurement.
- **scale_error** — bases + `scale_sd * c`, using each phone class's measured
  between-walk scale spread (MARViN 0.0147, ADVIO 0.03). The common-mode claim
  at its strongest. The committed data do not establish it: drift-anatomy A2
  dropped the one-reference version, and the audit's difference scan shows no
  direct measurement exists.
- **local_clearance** — a device measurement of the span: the conservative
  no-LiDAR sensor-budget row (1.34–4.37 in for a 6 ft span) as a fixed anchor.
  This is the bar a working measurement can actually earn.

The oracle — `|scale error| * c`, using the walk's true scale error — is not a
bar at all. It marks where the common-mode assumption would end if it were
true, so the sweep can show how far the field would have to bend for
scale_error to be right.

## Error-field calibration

`trajectories.py` simulates tracked position as a random field along the wall:

- a per-walk scale error (one draw per walk), and
- a residual error per foot, one common draw when the correlation length is
  infinite, splitting toward independent as the length shrinks.

The scale distributions are the measured ones: MODERN uses the MARViN trusted
walks (SD 1.47%, mean 0 — the audit recomputes both from the pinned arrays and
reports the actual mean offset, 0.2%); ADVIO/2018 uses the measured walk scales
(−17% to −5%) as its per-walk spread.

The residual spread `k` is set per class to the largest value implied by the
committed pooled p90 at its fit distances (ADVIO GPS-truth series 18.6/55.7/94.6
in at 3/10/20 ft; MARViN 8.6/13.4/18.5 in at 10/20/30 ft) after removing the
scale part. Choosing the largest keeps the field conservative: simulated p90s
meet or slightly exceed the committed ones, never fall below them. The
calibration table in results reports both sides.

What the calibration cannot fix: the pooled p90 mixes walks and scenes; the
simulated field imposes a Gaussian shape and a single correlation length; and
the same-walk regime's high correlation is an assumption the ADVIO data
explicitly fail to confirm (removing per-walk scale did not collapse their
pooled p90 — the audit pins that number). Read the same-walk columns as the
assumption's conditional, the cross-session columns as its failure mode, and
the protocol as the measurement that decides between them.

## Simulation design

Each cell of the sweep is one separation, one walked distance, one correlation
length, one regime, one phone class, 20,000 seeded draws of the error field.
For every draw the study computes the true gap, the tracked gap, the true
difference error `|tracked − true|`, each bar, and the three-way outcome. From
those draws:

- **wrongly clears** — PASS rate on separations just under the 3 ft rule
  (2.7, 2.9). The outcome that matters; any nonzero rate is a hazard.
- **UNSURE although met** — UNSURE rate at separations just over the rule
  (3.3). The cost of a too-wide bar: cases a human must tape.
- **coverage** — share of draws where `bar >= true difference error`. What the
  bar claims about itself.
- **false reject** — FAIL rate at met separations; tracked for completeness.

Rates at 20,000 draws have Clopper-Pearson 95% upper bounds near 0.02% when
zero is observed; `run.py` exposes the helper and the tables note when a zero
is a bound, not a proof of zero.

## Comparator

`comparator.py` proves the model of the shipped calculation, not the shipped
result. It imports each solver's own class (`experiment.utils.Solver` on main
at a fixed commit, `solver.Solver` on `t3/server`), extracts every clearance
check from both solvers' sampled scenes, replays each check through
`models.current_bar` (transforming units with the fixtures' `in_per_ft` where
the sampled check reports inches), and asserts agreement at 1e-9. It records
both commit ids so the pin is re-checkable. If the two refs ever disagree on a
bar, the comparator reports that before any conclusion leans on either.

## What would change the conclusions

- A measured two-tap difference error at any correlation length (protocol doc)
  replaces the swept correlation assumption entirely.
- A LiDAR-class device budget measured, not modeled, would move the
  local_clearance anchor down from its conservative no-LiDAR value.
- Evidence that per-walk scale on modern phones is common mode across a whole
  wall (MARViN's A2 tested one reference object and dropped; a full-wall
  reference is a different experiment) would let the two ends' scale errors
  cancel in the difference instead of adding, shrinking every absolute bar.
