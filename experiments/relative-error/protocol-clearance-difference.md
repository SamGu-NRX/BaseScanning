# Protocol: measure the error of a two-tap clearance difference

What is missing today is a direct measurement of one quantity: the error of a
gap measured by tapping two features on one wall, against the gap's true
length. Everything in this study that is currently an assumption (the residual
correlation length, the common-mode question, the oracle column) collapses into
a measured number once a few dozen such pairs exist.

This protocol extends the paired-mark and tape-reference practice already in
`experiments/evals/field/FIELD_SHEET.md`. Read that first for the established
marking and recording conventions; this document only adds what a difference
measurement needs beyond a single position measurement.

## Design

A **site** is one wall, 25–35 ft of clear run, with at least two feature pairs
whose true gap differs (e.g. a 1 ft and a 5 ft span), so the sweep over
separations is measured, not extrapolated.

A **pass** is one walk of the wall. On each pass:

1. Walk meter to far end and back, tapping the marked feature ends in the app
   exactly as a normal scan does (same camera height, same pace).
2. At each end, before tapping, hold the phone still and record a 3 s
   stationary heading sample. A direction-dependent error component would not
   cancel between the two ends of a feature, so the samples let the analysis
   separate any such effect from distance-dependent drift.
3. Record tape truth: feature end to feature end, steel tape, both directions,
   at the height the marks sit at.

The measured quantity per pass is `tracked gap − tape gap` for each pair.

Grid to cover, with the minimum viable cell count:

| factor | levels | passes |
|---|---|---|
| device | one LiDAR phone, one non-LiDAR phone | — |
| walked distance | 3 (10, 20, 30 ft to the pair) | — |
| pair separation | 2 per site (≈1 ft, ≈4–6 ft) | — |
| passes per cell | 5 | 3 walked × 2 pairs × 5 = 30 per device per site |
| sites | 2 | 120 passes total |

30 passes per device is the smallest grid where a p90 of the difference error
is readable at all; a within-pass repeat (tap each end twice) doubles the
pairs for one walk's extra effort and buys the repeatability split below.

## What each analysis gets from the grid

- **Difference error vs walked distance** — pooled per walked-distance level:
  is the error flat in distance (common mode) or growing (independent)?
  This single plot decides between the scale_error and current bars.
- **Difference error vs separation** — same, per separation level.
- **Repeatability vs walk-to-walk** — within-pass repeat pairs measure the
  common part; across-pass pairs measure the total. Their ratio is the
  correlation length, measured, not assumed.
- **Cross-session check** — repeat one cell on a second day; the anatomy
  study's cross-session finding predicts a jump. Two sessions confirm or
  break it on the difference quantity itself.

## Recording

One row per tap pair in the session sheet: site, device, pass, pair id,
walked distance, tape gap (both directions), tracked gap, plus the heading
samples. Keep the raw session files; the sheet is the derived record. Publish
results as `experiments/clearance-difference/results/` with the same
pooled-per-cell layout `advio_drift.json` uses, so the audit pattern in this
study can pin it without new machinery.

## Success criterion

The measurement settles the bar question when the p90 of
`tracked gap − tape gap` is bounded, at the largest walked distance and
smallest separation, to within an inch — enough to distinguish the
scale_error bar (0.65–0.69 ft at 3 ft, of which 0.6 ft is the tap bases) from
the no-LiDAR device anchor (4.37 in at 6 ft) and from the current bar (5.9 ft
at a 20 ft tap). If the p90 instead grows with walked distance, the answer is
the current bar's shape with measured coefficients; if it is flat and small,
the answer is the device anchor with a measured value.
