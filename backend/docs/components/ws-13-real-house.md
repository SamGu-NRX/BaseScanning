# WS-13 real house and data

## Purpose

Give the scale layer a meter-cover table whose every number cites a public document, give WS-11 a way to fetch LaMAR without the A6 VM scripts, and give the crew a capture sheet for two real houses. The houses themselves wait for a capture day. This page covers pull request A (`ws-13/meter-models-and-registry`). Pull request B is the capture.

## Owner WS and brief

WS-13. Brief: `docs/build/ws-13-real-house-and-data.md`. Decision log: group B, including GB-17 (no generic prior; this WS owns the table, schema, and validator; WS-05 owns the loader). Layout log: store `internal/build/layout-decisions-ws-13.md`.

## Inputs and outputs

Inputs used now: the Itron, Aclara, Hubbell, Sensus, and Landis+Gyr documents listed in `data/meter-models/SOURCES.md`; the A6 fetch recipe (metadata URL, three `ios_*` session zips, COLMAP `LIN.zip`); A7 and A10 capture rules, folded into `scripts/real_house/field_sheet.md`.

Outputs in this pull request:

- `data/meter-models/meter_models.v1.json` and `meter_models.schema.json`
- `scripts/meter_models/validate_table.py`
- `scripts/lamar/fetch_meta.py`, `fetch_frames.py`, `fetch_lamar.sh`
- `data/fixtures/manifest.json` (LaMAR rows, hashes null; YouTube rows are `kind: packet_tar`, id is the house id, `committed: false`)
- `scripts/real_house/` converters, field sheet, consent template, example purpose CSV
- `docs/evidence-register.md`, `docs/data/real-house.md`, `docs/data/fold-ins/`

Not produced: real-house capture packets, tape scenes, a filled LaMAR download, or YouTube frame tars (those stay local). Draft labels are in `data/fixtures/labels/`. Packet JSON is not committed: exposure and pose numbers contain long digit runs. The tar sha256 is the registry record.

## How to run on a fixture

```bash
uv run python scripts/meter_models/validate_table.py data/meter-models/meter_models.v1.json
uv run python scripts/real_house/truth_to_scene.py --truth TRUTH.json --site real-001 --out scene.json
bash scripts/lamar/fetch_lamar.sh ~/lamar
```

The LaMAR command checks the INFO page for CC BY-SA 4.0, then downloads about 6.1 GB. It is not part of the unit tests. `build_laser.py` runs only when the data directory is `~/lamar` and `HSS_A6_ROOT` or `FIXTURE_SOURCE` points at the A6 helpers. Pass `~/lamar`. Any other directory skips the laser build.

There is no committed real-house fixture. `truth_to_scene.py` exits 4 if `truth.json` still has a meter number other than `REDACTED`.

## How to test

```bash
uv run pytest scripts/meter_models/tests scripts/real_house/tests scripts/lamar/tests -q
uv run ruff check scripts/meter_models scripts/lamar scripts/real_house
```

WS-00's contracts commit (`66ee387`) is on `main`: CI runs `ruff`, `pytest`, and `python -m server.contracts.export_schemas --check`. `make validate` and `scripts/check_repo_hygiene.py` are still the fixtures pull request, so they are not targets on this branch. Before push, the deny-list was run by hand: no tracked file over 5 MB, no secret-shaped tokens, no 8-digit run in `data/`, `docs/`, or `scripts/` outside lines that already say `sha256`, `bytes`, `uuid`, or `http`.

## Rebuild from scratch

1. Open each PDF in `SOURCES.md`, read the dimension that the drawing labels, convert inches with `× 0.0254` and half-up to 4 decimal places (centimetre labels that are already exact at 4 decimals stay as printed). Leave a field null when the sheet does not name that axis.
2. Run `validate_table.py`. It rejects `assumed` provenance, a cover outside 0.150–0.185 m, a ring that is not larger than the cover, and an S-base outside 0.1765 ± 0.002 m.
3. Point `fetch_lamar.sh` at `~/lamar`. Do not commit the download.

## Last gate table

Observed on this branch, 2026-09-27. `validate_table.py` printed `ok entries=20 usable_for_scale=5 table_version=1.2.0`. WS-00 contracts are merged from `origin/main`. Capture-day gates stay pending.

| Gate | Bar | Observed |
|---|---|---|
| 1. Table | exit 0; ≥ 20 entries; ≥ 3 `usable_for_scale`; no numeric `assumed` outside `generic-prior`; every datasheet number has `source.url` and `accessed` | **Met.** Exit 0. Entries 20. Usable 5: CENTRON poly 0.1598 m, glass 0.1631 m, OpenWay Riva letter G 0.1600 m, Gen5 Riva letter G 0.1600 m, A3 ALPHA figure 2-6 0.1620 m. No `assumed` values and no `generic-prior` row. Every stored number has `url` and `accessed` 2026-09-26 |
| 2. Three YouTube packets | validate; index lists 12 houses; `not_built` empty | **Index met.** Three local tars, each four variants, `kind: packet_tar`, `committed: false`. `yt-Y0DSQekoV_g` 92 keyframes, sha256 `95fcae5849c40bf008dceb7a680a3bd57c85c8bb79cd90b9b4b5160afd4dc85a`, 26368000 bytes. `yt-KSXy6LFwQU4` 176 keyframes, sha256 `8fabeb1639f937be058786d357f6e6b13cc6e90f5c293cc5116eac678a12d7d4`, 57221120 bytes. `yt-olz1uwI4vM8` 126 keyframes, 126 registered, sha256 `378b68fed0f5cc4d5cf1a4b59041575e8a526b01cac824bd4f7f78811cffd8ef`, 54662144 bytes. Store `internal/data/packets/index.json` lists 12 houses and `not_built` is empty. The tars are not copied into the store |
| 3. Labels | `52 files written and valid`; house-labels v0.6 | **Draft copied.** 28 repo files in `data/fixtures/labels/` and the same 28 under store `internal/criteria/labels-v0/` (60 files there: the previous 32 plus these). YouTube `site_decision` is `manual_review`. Real-house templates have `site_decision: unknown` and no `start_range` row. Every new file stays draft for Hunter's approval. `docs/house-labels.md` was not moved to v0.6 |
| 4. Fold-ins | store `internal/build/fold-ins/` for B3 and Modal; evidence register; one issue per owner | **Store copies written.** Register is `docs/evidence-register.md`. Writeups are `docs/data/fold-ins/` and store `internal/build/fold-ins/` (`B3-cheap-gate.md`, `modal-benchmark.md`, `A7-iphone-degradation.md`, `A10-sparse-photo-flow.md`, `E2E-criteria-on-recon.md`). Issues listed there and not opened |
| 5–13. Capture, E2E, LaMAR download, labels approval | two houses, truth, pass path, `fetch_lamar.sh` frame counts | **Pending.** Capture day is not scheduled. Houses were not simulated |

## Known limits

- Five usable covers: CENTRON poly and glass, OpenWay Riva letter G, Gen5 Riva letter G, and A3 ALPHA figure 2-6. The other 15 entries cite a public document and leave `cover_od_m` null, because that document does not name a cover or glass diameter. A line that says only "diameter" is not stored as a cover.
- Drawing letter C on the CENTRON sheet runs from the flange's front face to the cover front. Letter E is the overall depth. C is not stored as `rim_offset_m`. Unlabeled "DIA" and "H" callouts are not stored. The FOCUS "5.5 in" low-profile note is below the 0.150 m cover floor and is unlabeled, so it is not stored.
- I-210+ depth 5.25 in MAX is taken from brochure pages 7-8, both headed I-210+c. It stays on the I-210+ row.
- A wall baseline in `truth_to_scene.py` runs from the meter toward the right. A wall measured the other way needs a different baseline.
- `truth_to_scene.py` follows scene v0 from the contracts package: `frame.units` / `frame.convention`, geometry `kind` of `point` or `wall_rect`, and a required positive `height_m`. A wall without `height_m` exits 2. Nothing is defaulted.
- LaMAR `sha256` and `bytes` stay null until a fetch. `make validate` is not on `main` yet.
- Consent template and fold-ins live in the repo, not at the store paths in the brief.

## Stubs relied on

WS-00 contracts are on `main` (`server/contracts`, `make schemas`). `make validate`, `scripts/fetch_fixtures.py`, and `docs/components/_template.md` are still the fixtures pull request. The manifest uses `hss-fixtures/v0`. YouTube items are `kind: packet_tar` with the house id. LaMAR items stay `kind: lamar` under `.fixtures/lamar/`.

## Defaults chosen on this branch

The brief's D1–D19 stay in the layout log. Added while building:

- D20. Fold-ins, the registry note, and the consent template are in the repo (`docs/data/fold-ins/`, `docs/data/real-house.md`, `scripts/real_house/consent-template.md`).
- D21. No `generic-prior` row. The validator rejects every `assumed` value.
- D22. CENTRON letter C is not `rim_offset_m`. Unlabeled diameters are not stored.
- D23. The store packet index and label export were not edited.
- D24. Fold-in issues were not opened.
- D25. The pull request stayed draft until WS-00's contracts were on `main`. That commit is merged here.
- D26. Do not pad the table with assumed covers. A later pass recorded 20 confirmed entries and still 2 usable covers.
- D27. `fetch_lamar.sh` runs `build_laser.py` only for data in `~/lamar`.
- D28. Optional dusk session S1b is cancelled.
- D29. Photo count stays 60–70, dense on the meter side. It is not reduced to 40 around the house.

## Inconsistencies

- Brief §8 gate 1 asks for ≥ 20 entries and ≥ 3 usable covers. The table has 20 entries and 5 usable covers. OpenWay Riva and Gen5 store page 4 letter G. A3 ALPHA stores figure 2-6. kV2c, iConA, Stratus and REX2 still do not name a cover.
- Brief §3.3 allows one `generic-prior` of assumed covers and a datasheet rim offset of 0.0686 m. GB-17 and the measured drawing refuse both.
- Brief §4-C4 still offers an optional dusk repeat. A7 says dusk breaks registration. The field sheet cancels it.
- Brief §4-B and §4-F also name `docs/house-labels.md`, house records, and one GitHub issue per owner. Those paths were not written. The packet index, the draft labels, and the five fold-in docs were.
- Index inconsistency I12 is closed in the store index: 12 houses, `not_built` empty.
- Plan id `yt-KSXy4LFwQU4` is the manifest id `yt-KSXy6LFwQU4`.
