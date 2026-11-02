# Provenance audit

Every number below was read from its committed source at run time.
Line locators assert the quoted text; JSON locators resolve exactly.
Citation review of the rules values is reused from PR #217
(obv/basescanning-024); historical measured values are retained here.

| quantity | source | locator | value | note |
|---|---|---|---|---|
| drift_per_ft rate | server/rules.yaml | yaml:41 | drift_per_ft: {value: 0.16, source: "S1 real-data evals (... | the rate under study, with its own source note: 2018 iPhone 6s, current phones untested |
| tap base | server/rules.yaml | yaml:29 | tap_ft: {value: 0.3, source: "Day-1 estimate for AR taps;... | per-tap allowance |
| vlm base | server/rules.yaml | yaml:30 | vlm_ft: {value: 1.5, source: "Day-1 estimate for photo de... | photo-detection allowance |
| wall base | server/rules.yaml | yaml:36 | wall_ft: {value: 0.3, source: "Walls come from AR taps: d... | tap wall allowance |
| evals tracking claim | experiments/evals/README.md | md:8 | - **Tracking:** a 2018 iPhone's position error, p90 18.6 ... | 2018 phone 2-3x the server allowance; MARViN p90 8.6/13.4/18.5 in at 10/20/30 ft |
| ADVIO p90 vs ARCore truth, 3 ft | experiments/evals/results/advio_drift.json | json:pooled.3.arcore.p90_in | 8.1700 | the numbers the drift_per_ft source note quotes (8.2 in rounded) |
| ADVIO p90 vs ARCore truth, 10 ft | experiments/evals/results/advio_drift.json | json:pooled.10.arcore.p90_in | 22.3000 |  |
| ADVIO p90 vs ARCore truth, 20 ft | experiments/evals/results/advio_drift.json | json:pooled.20.arcore.p90_in | 41.5500 |  |
| ADVIO p90 vs ARCore truth, 30 ft | experiments/evals/results/advio_drift.json | json:pooled.30.arcore.p90_in | 60.1500 |  |
| ADVIO position p90 vs GPS truth, 3 ft | experiments/evals/results/advio_drift.json | json:pooled.3.position_truth_gps.p90_in | 18.6300 | the harder calibration target the simulation uses |
| ADVIO position p90 vs GPS truth, 10 ft | experiments/evals/results/advio_drift.json | json:pooled.10.position_truth_gps.p90_in | 55.7000 |  |
| ADVIO position p90 vs GPS truth, 20 ft | experiments/evals/results/advio_drift.json | json:pooled.20.position_truth_gps.p90_in | 94.6100 |  |
| ADVIO position p90 vs GPS truth, 30 ft | experiments/evals/results/advio_drift.json | json:pooled.30.position_truth_gps.p90_in | 132.5800 |  |
| ADVIO position p90 after scale removal, 30 ft | experiments/evals/results/advio_drift.json | json:pooled.30.position_beyond_scale.p90_in | 144.5600 | removing per-walk scale does not collapse the p90; these data do not establish common mode |
| MARViN per-walk scales, bar+church | experiments/evals/results/modern_arkit.json | json:bar | [{'walk': 'seq1', 'images': 216, 'walked_m': 175.86654543... | arrays of per-walk scale; the simulation's SD is derived from these and checked below |
| MARViN atrium walks excluded from trusted set | experiments/evals/results/modern_arkit.json | json:atrium | [{'walk': 'seq1', 'images': 210, 'walked_m': 94.410164283... | per-walk scales down to 0.49; the trusted-set filter excludes scene disagreement |
| MARViN trusted-subset position p90, 10-30 ft | experiments/evals/results/modern_arkit.md | md:42 | \| 10 ft \| 3.3 / 18.7 \| 2.8 / 22.6 \| 3.1 / 8.6 \| 19.2 \| | lines 42-44, column 'As tracked, without those walks': p90 8.6/13.4/18.5 in; the calibration target |
| anatomy A1 loops within bound | experiments/drift-anatomy/results/drift_anatomy.json | json:a1.share_ok | 0.5357 | 30 of 56 loops: returning to the meter does not bound the error |
| anatomy A1 with walk scale removed | experiments/drift-anatomy/results/drift_anatomy.json | json:a1.walk_scale_removed_share_ok | 0.7500 | 75 of 100: scale removal helps and still fails the 90% criterion |
| anatomy A2 verdict | experiments/drift-anatomy/results/drift_anatomy.json | json:a2.marvin.verdict | drop | 'drop': one reference object near the meter is not enough |
| anatomy A2 MARViN walks | experiments/drift-anatomy/results/drift_anatomy.json | json:a2.marvin.walks | [{'walk': 'bar/seq1', 'windows_5m': 153, 'median_5m': 1.0... | per-walk robust SD of scale over 5 m windows, 1.1-2.1% |
| anatomy A3 along share at 20 ft | experiments/drift-anatomy/results/drift_anatomy.json | json:a3.marvin.20.along_share_of_total_p90 | 0.9230 | 0.92: the error runs along travel, the direction a same-wall difference shares |
| anatomy A3 ADVIO along share at 20 ft | experiments/drift-anatomy/results/drift_anatomy.json | json:a3.advio.20.along_share_of_total_p90 | 0.7642 | 0.76: on the 2018 phone the error runs every which way |
| anatomy A4 UWB range at sigma 10 cm, 20 ft | experiments/drift-anatomy/results/drift_anatomy.json | json:a4.marvin.20.after_p90_in_sigma_0.10 | 7.7125 | 7.7 in: a meter range helps and does not settle a 3 ft clearance |
| anatomy A4 UWB range at sigma 10 cm, 30 ft | experiments/drift-anatomy/results/drift_anatomy.json | json:a4.marvin.30.after_p90_in_sigma_0.10 | 8.5326 |  |
| budget: both ends in one photo, LiDAR | experiments/sensor-budget/results/budget.md | md:16 | \| One photo holds both ends, LiDAR \| 6 ft span from 3 m... | modeled device budget for a 6 ft span: best 0.54, p90 1.34 in |
| budget: both ends in one photo, no LiDAR | experiments/sensor-budget/results/budget.md | md:17 | \| One photo holds both ends, no LiDAR \| 6 ft span from ... | the anchor models.local_clearance_bar uses: 1.34-4.37 in for a 6 ft span |
| budget JSON precise values | experiments/sensor-budget/results/budget.json | json:rows | [{'name': 'Dual-camera disparity, depth per pixel', 'kind... | the table's underlying numbers |

## Source hashes

| source | path | sha256 | bytes |
|---|---|---|---|
| advio_drift | experiments/evals/results/advio_drift.json | ca06bf856186d907... | 11080 |
| modern_arkit | experiments/evals/results/modern_arkit.json | 9eec87acdca6123e... | 7918 |
| drift_anatomy | experiments/drift-anatomy/results/drift_anatomy.json | e86be1b4cb5d195e... | 41665 |
| budget_md | experiments/sensor-budget/results/budget.md | 28b863d7c25414cb... | 6158 |
| budget_json | experiments/sensor-budget/results/budget.json | e6c83062e3b50027... | 12279 |
| rules_yaml | server/rules.yaml | 539daff61e137654... | 6935 |
| evals_readme | experiments/evals/README.md | 04e1a62a0b02cff2... | 1264 |
| modern_arkit_md | experiments/evals/results/modern_arkit.md | f3898c86e65b4799... | 11718 |

## Measurement counts

| dataset | count |
|---|---|
| advio_walked_distance_levels | 5 |
| marvin_trusted_walks_bar_church | 25 |
| marvin_atrium_walks_excluded | 10 |
| drift_anatomy_a2_scale_windows | 25 |

## Limitations

- **advio_drift**: 2018 iPhone 6s ARKit 1.0 walks; GPS-rescaled truth carries its own error; pooled across walks, so it bounds absolute position, not two-tap differences.
- **modern_arkit**: COLMAP metric reference is not tape- or laser-verified; a potentially self-scaled reference is not independent truth, and the per-walk scale spreads inherit that doubt.
- **modern_arkit_md**: Same reference as modern_arkit; the trusted-subset p90 table is the simulation's calibration target.
- **drift_anatomy**: Within-walk scale variation and along-travel share; no row measures a two-tap difference (clearance) error directly.
- **budget_md**: Modeled device budget, not a measurement; anchors local_clearance.
- **budget_json**: Modeled device budget, not a measurement; anchors local_clearance.
- **rules_yaml**: 0.3/1.5 ft bases and the 0.16 ft/ft rate are day-1 estimates; the rate is calibrated to S1 real-data evals (PR #12) on a 2018 iPhone 6s only. Citation review reused from PR #217 (obv/basescanning-024).
- **evals_readme**: Summary prose; the numbers it quotes are pinned to their tables.

An assumed correlation is not measured calibration: the residual
correlation length in the sweep is a swept assumption, not a measured
value, and the manifest says so beside the hashes it freezes.

Scale check: 25 trusted MARViN walks, mean +0.9975, SD 0.0147; the simulation uses SD 0.0147 and mean 0. The mean offset is 0.2 percent, small against the SD; the simulation's zero mean is stated rather than hidden.

Difference scan: what the committed results say about two-tap differences:
16 result files scanned, 2270 matching leaf keys.
- experiments/drift-anatomy/results/drift_anatomy.json: 56 matches over 1 key names: ref_gap_m
- experiments/edge-geometry/results/edge_geometry.json: 16 matches over 1 key names: pairs
- experiments/evals/results/eth3d_recon.json: 672 matches over 1 key names: pairs
- experiments/evals/results/frames.json: 254 matches over 1 key names: pairs
- experiments/evals/results/pose_priors.json: 1248 matches over 1 key names: pairs
- experiments/plane-consistency/results/plane_consistency.json: 24 matches over 3 key names: no qualifying pair, pairs, pairs_refined
What these are: depth-prior and reconstruction keys named gap/between/etc. -- scene-geometry fields, not tracking-error measurements of a two-tap difference. The full match list is in audit.json.
