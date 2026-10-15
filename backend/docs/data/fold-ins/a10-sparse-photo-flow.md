# Fold-in: A10 sparse photo flow

Source: `internal/breadth/A10-sparse-photo-flow.md`, conditional go, landed 2026-09-26 23:21 UTC. The field sheet keeps 60–70 JPEGs dense on the meter wall. It does not drop the walk to 40 photos around the house.

## Old to new

| Item | Old | New |
|---|---|---|
| Photo pose engine | π³ or DA3 as a pose source was open | COLMAP incremental SfM from images alone. DSLR facade 0.3–0.6 in median / 2.5–4.5 in p90 at 40 / 60 / 70 photos, 85–100% registered in one model. π³ alone is 0.1–0.4 m camera error with phantom walls. DA3 alone places 9–19 of 40 poses |
| Count vs spacing | a 40-photo shot list | 40 photos suffice only at one photo per 0.5–1 m. Forty frames about 1 m apart registered 2–7 of 40. The dimmest session needed 60–70 frames per about 35 m side. `photo_count` is a proxy; the gate is spacing |
| Close-ups | can be a separate still set | stay inside the guided session. Image-only blank-wall close-ups did not register |
| Depth | a measurement engine | π³ / DA3 on COLMAP poses fill the meter wall (up to 99%) with p90 7–21 in; coverage only |
| Criteria | expected to decide the site | C1 decided 15–38% of checks (laser ceiling 62–92%). Every verdict was manual review. Nothing was unsafe. Missing evidence is ground and adjacent walls |
| Scale at 26 ft | open | about 3 / 7 in at true scale, ARKit-only +6–13 in, meter make/model +1.9 / 6.2 in (calculated) |
| Harness | taps width rescale | wrong across different fields of view, same defect class as A7. Delivery-area reconstructions fail in the extractor (`meter clicks hit no wall`). Dense clouds can split the meter wall |

## Still pending in the report

Rescore of the pre-fix iPhone-quality rows, electro and delivery iPhone-quality sets, facade N70 DA3 alone, the no-tool scale rescoring, and the LCPB / Qm4b careless house sets.

## Owners

WS-02, WS-04, WS-05, WS-06, WS-09, WS-10, WS-11, WS-14.

## Issues not opened

- `fold-in A10: photo_count is a proxy for spacing` (WS-02)
- `fold-in A10: COLMAP is the photo-flow pose engine` (WS-04)
- `fold-in A10: scale at 26 ft` (WS-05)
- `fold-in A10: depth is coverage only` (WS-06)
- `fold-in A10: views_needed for ground and adjacent walls` (WS-09)
- `fold-in A10: reviewer priorities for missing evidence` (WS-10)
- `fold-in A10: taps rescale and extractor failures` (WS-11)
- `fold-in A10: shot list stays dense on the meter wall` (WS-14)
