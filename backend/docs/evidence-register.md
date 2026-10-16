# Evidence register

Numbers below are the landed breadth reports as folded in WS-13 brief §4-F. Status is `measured` when the report measured it, `simulated` when A7 says the sensor figure is assumed until a real phone capture, and `pending` when the report still lists a rescore. Changed on 2026-09-26 when this register was first written. Fold-in writeups: `docs/data/fold-ins/`. Issues were not opened.

| threshold or default | current value | owner WS | evidence (report §) | status | changed on |
|---|---|---|---|---|---|
| Detection cascade | CLIP ViT-B/32 union SigLIP2-base, top-2 full frames, no crops, no OWLv2 gate | WS-07 | B3 §1, §3.1 | measured | 2026-09-26 |
| VLM cost per house | about $0.30 (baseline $2.32); WS-07 cap $0.85 stays the ceiling | WS-12 | B3 | measured | 2026-09-26 |
| Meter auto-fill | 68/73 agree, 65/68 correct, Wilson low 87.8%; no auto-fill without a zxing match; pre-fill top-3 | WS-08 | B3 | measured | 2026-09-26 |
| Panel hazard path | mini at 2048 px, escalate hazard / brand / amps to GPT-5.5; 0 false hazard alarms | WS-08 | B3 | measured | 2026-09-26 |
| Motion blur drop | drop near 4 px; electro breaks at 5.6 px median, facade at 11 px; turn at most 7–10 °/s | WS-02, WS-14 | A7 §6 | simulated | 2026-09-26 |
| Daylight | about 1,000 lux on the wall; ISO above 200 is `high_iso`; electro fails near 100 lux | WS-02, WS-14 | A7 §6 | simulated | 2026-09-26 |
| Framing | wall base and at least 1 m of ground, phone level | WS-06, WS-14 | A7 §6 | simulated | 2026-09-26 |
| SfM fallback | registered fraction under about 90%, or SfM-vs-ARKit scale off by more than 5% | WS-04 | A7 | simulated | 2026-09-26 |
| Ground datum | narrow-FoV facade fit the ground to 0.45–0.75 m street furniture | WS-06 | A7 | simulated | 2026-09-26 |
| Photo pose engine | COLMAP incremental SfM; π³ and DA3 alone are no-go as pose engines | WS-04 | A10 | measured | 2026-09-26 |
| Photo spacing | 40 photos only when one step is 0.5–1 m; 40 frames about 1 m apart registered 2–7 of 40; 60–70 per about 35 m side | WS-02, WS-04, WS-14 | A10 §3.3, §7 | measured | 2026-09-26 |
| Photo-flow scale at 26 ft | about 3 / 7 in at true scale; ARKit-only +6–13 in; meter make/model +1.9 / 6.2 in | WS-05 | A10 | measured | 2026-09-26 |
| Photo depth | π³ / DA3 on COLMAP poses fill the meter wall (up to 99%) with p90 7–21 in; coverage only | WS-06 | A10 | measured | 2026-09-26 |
| pass_recall | SKIP until real-001 pass labels exist; 102 photo verdicts, 0 unsafe passes, no labelled pass | WS-11 | E2E §1, §7 | measured | 2026-09-26 |
| Decidability | laser 38% / 75% facade / electro; photo true scale 13% / 25% | WS-09, WS-11 | E2E | measured | 2026-09-26 |
| Mapper frame budget | 48 frames (`HSS_POSES_SFM_MAX_FRAMES`); 3 min p50 only near 40 frames | WS-03, WS-04 | Modal benchmark | measured | 2026-09-26 |
| pycolmap line | 4.2.0 with a matching COLMAP build unless 3.11.1 parity is shown | WS-04, WS-12 | Modal benchmark, SD-34 | measured | 2026-09-26 |
| GPU cost | geometry-only $0.05–0.15 per house; `scaledown_window=900` did not keep a container warm | WS-12 | Modal benchmark | measured | 2026-09-26 |
| LaMAR openings gate | `openings_p90_in_max` 4.6 in (SD-40); 4.2 in is superseded | WS-11 | A6 §7 | measured | 2026-09-26 |
| Usable meter covers | 2 rows (CENTRON poly 0.1598 m, glass 0.1631 m); brief asked for at least 3 | WS-05 | this table, `meter_models.v1.json` | measured | 2026-09-26 |
