# meter-candidate-selection

Does meter-closeup's hand-set candidate-selection rule pick a *unique* serial number
when a reader sees several digit strings — and when it fails, is the failure recognition
(the reader never saw the number) or ranking (it saw it and offered something else first)?

The experiment mirrors the `meter_eval.locate` / `match` interface from
`experiments/meter-closeup` — `normalize`/`core`, digit-run token merging, the
`8*barcode_confirmed + 3*keyword + 2*alone + 1*length_ok - 6*spec - 4*vertical - 6*zeros`
score — without importing or modifying `meter_eval`. Every weight was fixed before the
first run; nothing is calibrated on any outcome, and no threshold is tuned.

## Arms

| arm | kind | what ran |
|---|---|---|
| `tesseract` | real OCR | Tesseract 5.5.0 (eng) reading the frozen synthetic plates on Linux |
| `observed` | hand-authored | reader-shaped observation sets: observations, **not** OCR evidence |
| `meterocr` | — | **not run**: Apple Vision via meterocr needs macOS; a mock is not OCR evidence |

## Inputs (frozen, reproducible)

`inputs/` and `gold/labels.json` are committed artifacts. Regenerating them with
`uv run python -m meter_candidates.generate` (pure Pillow + random.Random seeds, no
network) reproduces every byte — `tests/test_inputs.py` checks exactly that.

- 18 generated plates, 640x400 PNG: 15 carry an 8-digit synthetic serial, 3 have none.
- 12 hand-authored observation sets: 9 with a serial visible (clean, two-keyword tie,
  garbled `0→O`, vertical print, barcode payload), 3 with no serial anywhere.
- Gold labels (`serial`, `present`) live only in `gold/labels.json`; the reader inputs
  and the extraction/ranking code never see them — scoring joins by case id afterwards.

## Run and replay

```sh
# each arm reads its frozen inputs; raw reader output + scored tables land in results/
uv run python run.py --manifest manifest.json

# recompute cases.csv / summary.json / tables.md from results/raw ALONE — no reader
# runs, no input files — and overwrite the presentation files in place
uv run python run.py --replay results
```

The commit must stay byte-identical under replay: same raw output in, same tables out,
whatever machine runs it. `tests/test_replay.py` checks that byte-for-byte.

## Checks

```sh
uv run ruff check src tests run.py
uv run pytest tests -q
```

## Reading the results

`results/tables.md` is the summary: per arm — cases, top-1, top-3, rank misses, and the
cause split (ranking cause = `rank_miss` + `filtered_miss`, recognition cause =
`recognition_miss`), plus rejects scored as `correct_rejection` or `false_offer`.
`results/cases.csv` is the per-case record; `results/raw/` holds each arm's untouched
reader output, which is the only thing replay needs.
