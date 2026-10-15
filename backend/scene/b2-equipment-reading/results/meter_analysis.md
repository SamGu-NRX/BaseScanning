## Label confirmation

Hand labels: 73. Confirmed by at least one non-Claude reader: 73.
Confirmed only by Vision's match against PR #16's key-holder label: 0 [].
Unconfirmed (Claude-only): 0 []

Confirmed by a non-Claude engine run here (excluding Vision): 73

## Top-1 / top-3, all labels vs non-Claude-confirmed subset

| Engine | n | top-1 all | top-3 all | top-1 confirmed | top-3 confirmed | top-1 held-out (even ids) |
|---|---|---|---|---|---|---|
| GPT-5.5 | 73 | 69/73 = 95% (87%–98%) | 71/73 = 97% (91%–99%) | 69/73 = 95% (87%–98%) | 71/73 = 97% (91%–99%) | 33/35 = 94% (81%–98%) |
| GPT-5.4-mini | 73 | 61/73 = 84% (73%–90%) | 70/73 = 96% (89%–99%) | 61/73 = 84% (73%–90%) | 70/73 = 96% (89%–99%) | 30/35 = 86% (71%–94%) |
| Grok 4.7 | 73 | 66/73 = 90% (82%–95%) | 72/73 = 99% (93%–100%) | 66/73 = 90% (82%–95%) | 72/73 = 99% (93%–100%) | 31/35 = 89% (74%–95%) |
| Grok 4.20 NR | 73 | 61/73 = 84% (73%–90%) | 72/73 = 99% (93%–100%) | 61/73 = 84% (73%–90%) | 72/73 = 99% (93%–100%) | 30/35 = 86% (71%–94%) |
| Opus 5.5 | 73 | 69/73 = 95% (87%–98%) | 73/73 = 100% (95%–100%) | 69/73 = 95% (87%–98%) | 73/73 = 100% (95%–100%) | 33/35 = 94% (81%–98%) |
| Haiku 4.5 | 73 | 57/73 = 78% (67%–86%) | 67/73 = 92% (83%–96%) | 57/73 = 78% (67%–86%) | 67/73 = 92% (83%–96%) | 27/35 = 77% (61%–88%) |
| docTR | 73 | 43/73 = 59% (47%–69%) | 58/73 = 79% (69%–87%) | 43/73 = 59% (47%–69%) | 58/73 = 79% (69%–87%) | 23/35 = 66% (49%–79%) |
| PaddleOCR | 73 | 50/73 = 68% (57%–78%) | 64/73 = 88% (78%–93%) | 50/73 = 68% (57%–78%) | 64/73 = 88% (78%–93%) | 26/35 = 74% (58%–86%) |
| Tesseract psm11 | 73 | 16/73 = 22% (14%–33%) | 20/73 = 27% (18%–39%) | 16/73 = 22% (14%–33%) | 20/73 = 27% (18%–39%) | 7/35 = 20% (10%–36%) |
| Tesseract psm3 | 73 | 18/73 = 25% (16%–36%) | 21/73 = 29% (20%–40%) | 18/73 = 25% (16%–36%) | 21/73 = 29% (20%–40%) | 8/35 = 23% (12%–39%) |

## Top-1 misses by category

| Engine | misses | other identifier | misread (edit <= 2) | prefix/suffix | no pick | other | ids |
|---|---|---|---|---|---|---|---|
| GPT-5.5 | 4 | 1 | 0 | 2 | 0 | 1 | m33:other m38:other_id m61:affix m66:affix |
| GPT-5.4-mini | 12 | 1 | 0 | 1 | 0 | 10 | m33:other m34:other m51:other m57:other m59:other m61:other m64:other m66:affix m69:other m70:other m77:other m82:other_id |
| Grok 4.7 | 7 | 1 | 0 | 2 | 0 | 4 | m30:other m38:other_id m57:other m61:affix m64:other m66:affix m77:other |
| Grok 4.20 NR | 12 | 2 | 0 | 1 | 0 | 9 | m25:other_id m30:other m34:other m43:other m61:other m64:other m66:affix m69:other m71:other m77:other m81:other m82:other_id |
| Opus 5.5 | 4 | 0 | 0 | 0 | 0 | 4 | m50:other m51:other m52:other m57:other |
| Haiku 4.5 | 16 | 1 | 1 | 4 | 0 | 10 | m05:misread m32:other m33:other m34:other m37:affix m40:other m57:other m61:affix m64:other m66:affix m68:affix m69:other m70:other m71:other m81:other m82:other_id |
| docTR | 30 | 3 | 3 | 2 | 0 | 22 | m01:misread m09:other m11:other m14:affix m23:other m25:other m27:other m29:other_id m30:other m31:affix m32:other_id m33:other m34:other m37:other m38:other m49:other m50:other m52:other m55:misread m56:other m57:other m61:other m64:other m65:other m70:other m71:other m74:misread m77:other m81:other m82:other_id |
| PaddleOCR | 23 | 4 | 0 | 0 | 0 | 19 | m04:other m05:other m11:other m23:other m25:other_id m26:other_id m30:other m31:other m32:other_id m33:other m34:other m38:other m43:other m47:other m51:other m57:other m61:other m64:other m65:other m70:other m77:other m81:other m82:other_id |
| Tesseract psm11 | 57 | 0 | 2 | 6 | 10 | 39 | m01:other m02:affix m03:other m04:other m05:other m06:other m07:other m08:none m11:none m13:other m14:other m15:affix m16:affix m17:other m18:other m19:other m21:none m22:other m23:none m25:other m26:misread m29:misread m30:affix m32:other m33:none m34:other m37:other m38:none m39:other m40:other m42:other m43:other m48:none m50:other m51:affix m53:other m54:other m55:none m56:other m57:other m58:other m59:other m62:other m63:affix m64:other m65:other m66:other m70:other m71:none m74:other m75:other m76:other m77:other m79:none m80:other m81:other m82:other |
| Tesseract psm3 | 55 | 0 | 0 | 4 | 19 | 32 | m01:other m02:none m03:other m04:none m05:other m06:affix m07:none m08:none m11:none m14:none m15:other m16:other m17:other m18:none m19:other m21:none m22:other m23:none m25:other m26:other m27:affix m29:none m32:other m33:none m34:other m37:affix m38:none m40:other m50:other m51:other m53:other m54:none m55:none m56:other m57:other m58:other m59:other m60:none m61:other m62:none m63:affix m64:other m65:other m66:other m70:other m71:none m73:other m74:other m75:other m76:none m77:none m78:other m80:other m81:other m82:other |

## Agreement rules for auto-fill (accept when all listed readers' top-1 cores agree)

Precision = right among accepted; coverage = accepted / 73. Bar: precision lower bound >= 95% for blind auto-fill; otherwise pre-select with tap-to-confirm.

| Rule | accepted (coverage) | precision | Wilson low |
|---|---|---|---|
| GPT-5.5 + Opus 5.5 | 65/73 = 89% | 65/65 = 100% (94%–100%) | 94.4% |
| GPT-5.5 + Grok 4.7 + Opus 5.5 | 62/73 = 85% | 62/62 = 100% (94%–100%) | 94.2% |
| Grok 4.20 NR + Opus 5.5 | 57/73 = 78% | 57/57 = 100% (94%–100%) | 93.7% |
| Grok 4.7 + Opus 5.5 | 64/73 = 88% | 63/64 = 98% (92%–100%) | 91.7% |
| GPT-5.5 + Grok 4.20 NR | 60/73 = 82% | 59/60 = 98% (91%–100%) | 91.1% |
| GPT-5.5 + Grok 4.7 + Grok 4.20 NR | 59/73 = 81% | 58/59 = 98% (91%–100%) | 91.0% |
| Opus 5.5 + Haiku 4.5 | 55/73 = 75% | 54/55 = 98% (90%–100%) | 90.4% |
| GPT-5.5 + PaddleOCR | 50/73 = 68% | 49/50 = 98% (90%–100%) | 89.5% |
| GPT-5.5 + GPT-5.4-mini | 62/73 = 85% | 60/62 = 97% (89%–99%) | 89.0% |
| GPT-5.4-mini + Opus 5.5 | 61/73 = 84% | 59/61 = 97% (89%–99%) | 88.8% |
| GPT-5.5 + Haiku 4.5 | 58/73 = 79% | 56/58 = 97% (88%–99%) | 88.3% |
| GPT-5.5 + docTR | 43/73 = 59% | 42/43 = 98% (88%–100%) | 87.9% |
| GPT-5.5 + Grok 4.7 | 68/73 = 93% | 65/68 = 96% (88%–98%) | 87.8% |
| Opus 5.5 | 73/73 = 100% | 69/73 = 95% (87%–98%) | 86.7% |
| GPT-5.5 | 73/73 = 100% | 69/73 = 95% (87%–98%) | 86.7% |
| GPT-5.4-mini + Grok 4.7 | 62/73 = 85% | 59/62 = 95% (87%–98%) | 86.7% |
| Grok 4.7 + Haiku 4.5 | 57/73 = 78% | 54/57 = 95% (86%–98%) | 85.6% |
| Grok 4.7 + Grok 4.20 NR | 63/73 = 86% | 59/63 = 94% (85%–98%) | 84.8% |
| GPT-5.5 + Tesseract psm3 | 18/73 = 25% | 18/18 = 100% (82%–100%) | 82.4% |
| GPT-5.4-mini + Grok 4.20 NR | 61/73 = 84% | 56/61 = 92% (82%–96%) | 82.2% |
| GPT-5.4-mini + Grok 4.20 NR | 61/73 = 84% | 56/61 = 92% (82%–96%) | 82.2% |
| GPT-5.4-mini + Haiku 4.5 | 59/73 = 81% | 54/59 = 92% (82%–96%) | 81.6% |
| Grok 4.7 | 73/73 = 100% | 66/73 = 90% (82%–95%) | 81.5% |
| GPT-5.5 + Tesseract psm11 | 15/73 = 21% | 15/15 = 100% (80%–100%) | 79.6% |
| Grok 4.20 NR + Haiku 4.5 | 59/73 = 81% | 53/59 = 90% (80%–95%) | 79.5% |
| Grok 4.20 NR | 73/73 = 100% | 61/73 = 84% (73%–90%) | 73.4% |
| GPT-5.4-mini | 73/73 = 100% | 61/73 = 84% (73%–90%) | 73.4% |
| Haiku 4.5 | 73/73 = 100% | 57/73 = 78% (67%–86%) | 67.3% |

## Barcode-confirmed pick (zxing payload core equals VLM top-1)

- GPT-5.5 top-1 confirmed by a decoded barcode: 10/73 accepted, precision 10/10 = 100% (72%–100%)
- Grok 4.7 top-1 confirmed by a decoded barcode: 10/73 accepted, precision 10/10 = 100% (72%–100%)
- Opus 5.5 top-1 confirmed by a decoded barcode: 11/73 accepted, precision 11/11 = 100% (74%–100%)
- Photos with any decoded barcode (zxing-cpp full + half size): 20/83

## Unconfirmed labels: who read what

