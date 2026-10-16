| Engine | photos | brand right (of 13 legible) | brand wrong | brand claimed, none legible | main amps right (of 3) | panel rating right (of 1) | wrong amp claims | hazard caught (of 4) | false hazard alarms |
|---|---|---|---|---|---|---|---|---|---|
| gpt-5.4-mini | 60 | 10/13 = 77% (50%–92%) | 1 | 19 | 3/3 | 1/1 | 1 | 4/4 | 0 |
| gpt-5.5 | 60 | 11/13 = 85% (58%–96%) | 0 | 14 | 3/3 | 1/1 | 0 | 4/4 | 1 |
| grok-4.20-0309-non-reasoning | 60 | 10/13 = 77% (50%–92%) | 1 | 21 | 2/3 | 0/1 | 3 | 4/4 | 3 |
| grok-4.7 | 60 | 10/13 = 77% (50%–92%) | 0 | 7 | 3/3 | 1/1 | 1 | 4/4 | 0 |
| doctr | 60 | 2/13 = 15% (4%–42%) | 0 | 1 | 1/3 | 1/1 | 2 | 1/4 | 0 |
| tesseract_psm11 | 60 | 1/13 = 8% (1%–33%) | 0 | 3 | 0/3 | 0/1 | 2 | 0/4 | 0 |

- gpt-5.4-mini: x0315:main=20 x0770:brand=ge
- gpt-5.5: x0149:hazard
- grok-4.20-0309-non-reasoning: x0141:brand=siemens x0141:rating=200 x0146:main=200? x0146:rating=200? x0147:rating=200? x0450:main=100? x0476:hazard x0638:hazard x0748:main=200? x0748:rating=200? x0770:rating=200 x0773:hazard x0778:main=200 x0787:rating=200?
- grok-4.7: x0205:rating=70
- doctr: x0146:main=41? x0146:rating=41? x0775:main=40 x0775:rating=40 x0781:main=26? x0781:rating=26?
- tesseract_psm11: x0457:main=29? x0457:rating=29? x0775:main=88 x0775:rating=88
