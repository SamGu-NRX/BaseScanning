# Fold-in: A7 iPhone degradation

Source: `internal/breadth/A7-iphone-degradation.md`, interim, landed 2026-09-26 23:20 UTC. Ten of 17 conditions on A1, nine on the feed-forward lanes. Re-open this file when the report is refreshed. Sensor numbers stay assumed until a real iPhone capture at noon and at dusk. real-001 is that capture, and it is not scheduled.

Verdict: conditional go for daylight and a steady walk. No-go for SfM poses at dusk or with walking blur. The field sheet cancels the optional dusk repeat.

## Old to new

| Item | Old | New |
|---|---|---|
| Blur drop | spec: drop above 1/60 s while moving | drop near 4 px of motion blur; electro breaks at 5.6 px median (about 10 °/s at 1/60 s); facade breaks at 11 px; walk at most 7–10 °/s |
| Light | unspecified | about 1,000 lux on the wall; ISO above 200 flags `high_iso`; electro fails near 100 lux; both scenes near 6 lux |
| Framing | unspecified | wall base plus at least 1 m of ground, phone level |
| Pose fallback | unspecified | registered fraction under about 90%, or SfM scale off ARKit by more than 5% |
| Matching | baseline | degraded frames inflate matching about 4–5×; filter before matching |
| Ground | fit the visible ground | narrow-FoV facade fitted 0.45–0.75 m street furniture; 20–36 in height errors inside ±0.08 m bounds |
| Harness clicks | GT perfect clicks | wrong for cropped phone frames (about 20 in on every path) |
| A1 stored model | published 0.84 / 2.90 in | current harness 1.73 / 4.11 in |

Feed-forward depth barely moves: electro median surface error 1.0–1.7 in, 77–95% complete.

## Still pending in the report

`blur_4`, `rs_30dps`, `rs_90dps`, `jpeg_q25`, `heic_q30`, `combo_dusk_slow`, `blur_32`; π³ electro; DA3 `hdr_sharp`. ARKit tracking degradation was not simulated.

## Owners

WS-02 (`motion_blur`, `high_iso`), WS-04 (fallback and match filter), WS-06 (ground-fit guard and framing), WS-11 (harness patch and re-baseline), WS-14 (daylight and slow-walk prompts). WS-03 relays only.

## Issues not opened

- `fold-in A7: motion_blur and high_iso flags` (WS-02)
- `fold-in A7: registration and scale fallback` (WS-04)
- `fold-in A7: ground-datum guard` (WS-06)
- `fold-in A7: harness patch and re-baseline` (WS-11)
- `fold-in A7: daylight and slow steady walk` (WS-14)
