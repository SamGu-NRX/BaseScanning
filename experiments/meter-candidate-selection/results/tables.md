# Meter candidate selection — results

Cases scored against gold, per reader arm. Causes of missed offers split into ranking (the serial's core was a candidate) and recognition (it never was).

## Outcome counts

| arm | cases | top-1 | top-3 | rank miss | filtered miss | recognition miss | correct rejection | false offer |
|---|---|---|---|---|---|---|---|---|
| observed | 12 | 6 | 7 | 0 | 1 | 1 | 3 | 0 |
| tesseract | 18 | 14 | 15 | 0 | 0 | 0 | 2 | 1 |

## Causes of missed offers (serial present, not offered top-1)

| arm | ranking cause | recognition cause |
|---|---|---|
| observed | 1 | 1 |
| tesseract | 0 | 0 |

## Arm status

- **meterocr**: not-run — Apple Vision through meterocr needs macOS; this Linux sandbox cannot run it, and a mock is not OCR evidence, so no substitute arm ran
- **observed**: ran, 12 cases — Hand-authored reader-shaped observation sets: observations, not OCR evidence
- **tesseract**: ran, 18 cases — Tesseract CLI (apt tesseract-ocr, eng) reading the frozen synthetic plates on Linux; real OCR output on synthetic input

## Per case

| id | arm | present | outcome | rank | candidates | offered |
|---|---|---|---|---|---|---|
| obs01 | observed | 1 | top1 | 1 | 3 | 1 |
| obs02 | observed | 1 | top1 | 1 | 3 | 1 |
| obs03 | observed | 1 | top1 | 1 | 3 | 1 |
| obs04 | observed | 1 | top1 | 1 | 3 | 1 |
| obs05 | observed | 1 | top1 | 1 | 2 | 1 |
| obs06 | observed | 1 | top3 | 2 | 2 | 2 |
| obs07 | observed | 1 | filtered_miss |  | 3 | 0 |
| obs08 | observed | 1 | top1 | 1 | 3 | 1 |
| obs09 | observed | 1 | recognition_miss |  | 3 | 1 |
| obs10 | observed | 0 | correct_rejection |  | 2 | 0 |
| obs11 | observed | 0 | correct_rejection |  | 0 | 0 |
| obs12 | observed | 0 | correct_rejection |  | 0 | 0 |
| gen01 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen02 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen03 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen04 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen05 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen06 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen07 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen08 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen09 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen10 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen11 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen12 | tesseract | 1 | top1 | 1 | 3 | 1 |
| gen13 | tesseract | 1 | top1 | 1 | 2 | 1 |
| gen14 | tesseract | 1 | top1 | 1 | 3 | 2 |
| gen15 | tesseract | 1 | top3 | 2 | 4 | 2 |
| gen16 | tesseract | 0 | correct_rejection |  | 2 | 0 |
| gen17 | tesseract | 0 | correct_rejection |  | 2 | 0 |
| gen18 | tesseract | 0 | false_offer |  | 1 | 1 |
