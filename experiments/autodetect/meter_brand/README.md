# Meter brand

The tracked brand scores predate token-boundary fixes in the reader and scorer. They need a new scoring run before use.

Can the phone read the meter's maker from the close-up it already reads the number from? It reuses #16's photos and #16's cached Vision output (`~/house-scanning-data/meter/ocr/clean.jsonl`, revision 3, `accurate`, no language correction), so its numbers compare directly with #16's.

`brands.py` lists 36 meter makers and matches each recognised line against them. `score.py` picks the listed maker on the tallest line that names one. No pass bar was set before the run.

**Labels.** A Claude Sonnet reader recorded the brand as printed on each photo and how it is printed. It agrees with the maker #16's reader named in its notes on 61 of the 71 photos with a brand in letters. The first pass had shifted rows m18 to m26 by four photos; the reader redid them before the scores below.

**Result** (`results/brand.md`, agreed photos): the maker is named correctly on 17 of 19 photos with plain letters and 22 of 42 with a logo. Vision doesn't read Tatung's round badge or GE's script monogram. The list rule named nothing on the 9 photos without a brand. Its one wrong pick is a plate printed with both Aclara and Landis & Gyr. Brand right and number in #16's top three together: 27 of 56.

**Changed after the first score.** Fuzzy matching now needs 6 letters, because GENUS matched a note reading "GE (US)"; accents fold to plain letters, because that change stopped `Itrón` matching; agreement now asks whether #16's note names the brand, so makers missing from the list can agree.

```sh
git show origin/t3/meter-closeup:experiments/meter-closeup/manifest.csv > /tmp/m16_manifest.csv
git show origin/t3/meter-closeup:experiments/meter-closeup/results/locate_per_image.csv > /tmp/m16_locate.csv
python3 score.py --manifest /tmp/m16_manifest.csv --numbers /tmp/m16_locate.csv \
    --rows results/brand_per_image.csv > results/brand.md
```

Most photos are Taiwanese and European meters. On the 16 US-style meters with an agreed brand, it names 11 correctly (`results/brand.md`), too few for a confident US figure.
